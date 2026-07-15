"""Durable storage primitives for the HLoop report broker.

Daemon lifecycle and CLI wiring intentionally live elsewhere.  This module
provides a caller-held transaction which combines the immutable event log,
Manager inbox projection, wake outbox, leases, and broker owner epochs.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .events import (
    ReportValidationError,
    assign_broker_sequence,
    canonical_json,
    new_event_id,
    normalize_invocation_id,
    parse_rfc3339,
    payload_digest,
    prepare_client_event,
    utc_now,
    validate_client_event,
    validate_report,
)


BROKER_SCHEMA_VERSION = 1
UNIX_SOCKET_PATH_MAX_BYTES = 103


class BrokerStorageError(RuntimeError):
    """Base error for broker persistence failures."""


class BrokerUnavailableError(BrokerStorageError):
    """A retryable broker/SQLite availability failure."""


class BrokerIntegrityError(BrokerStorageError):
    """A permanent broker storage invariant or schema failure."""


class IdempotencyConflict(BrokerStorageError):
    """An existing event id was reused for a different payload digest."""


class LeaseGenerationMismatch(BrokerStorageError):
    """A wake references a stale or otherwise different Manager lease."""


class ReportAuthenticationError(BrokerStorageError):
    """A live or spooled report does not match an active role identity."""


def _classify_sqlite_error(
    error: sqlite3.DatabaseError, *, operation: str
) -> BrokerStorageError:
    """Separate retryable SQLite availability from permanent semantics."""

    if isinstance(
        error,
        (
            sqlite3.IntegrityError,
            sqlite3.DataError,
            sqlite3.ProgrammingError,
            sqlite3.NotSupportedError,
        ),
    ):
        return BrokerIntegrityError(f"{operation} failed integrity checks: {error}")
    return BrokerUnavailableError(f"{operation} is unavailable: {error}")


@dataclass(frozen=True)
class StoredEvent:
    event: dict[str, Any]
    inserted: bool


@dataclass(frozen=True)
class StoredWake:
    wake: dict[str, Any]
    inserted: bool


@dataclass(frozen=True)
class WakeConsumption:
    accepted: bool
    reason: str


@dataclass(frozen=True)
class InboxAcknowledgement:
    accepted: bool
    reason: str


@dataclass
class BrokerTransaction:
    """An active transaction token passed to every storage mutation."""

    store: "BrokerStore"
    connection: sqlite3.Connection
    active: bool = True
    _after_commit: list[Callable[[], None]] = field(default_factory=list)

    def require_active(self, store: "BrokerStore") -> None:
        if not self.active or self.store is not store:
            raise BrokerStorageError("operation requires this store's active transaction")

    def after_commit(self, callback: Callable[[], None]) -> None:
        self.require_active(self.store)
        self._after_commit.append(callback)


def _schema_sql() -> str:
    append_only_tables = (
        "events",
        "inbox",
        "wake_leases",
        "wake_outbox",
        "wake_consumptions",
        "inbox_acknowledgements",
        "broker_owners",
        "active_roles",
    )
    triggers = []
    for table in append_only_tables:
        triggers.extend(
            [
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
                """,
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
                """,
            ]
        )
    return (
        """
        CREATE TABLE IF NOT EXISTS broker_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            payload_digest TEXT NOT NULL,
            event_json TEXT NOT NULL,
            run_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            report_type TEXT NOT NULL,
            received_at TEXT NOT NULL,
            UNIQUE(event_id, sequence),
            UNIQUE(event_id, run_id)
        );

        CREATE TABLE IF NOT EXISTS inbox (
            inbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            sequence INTEGER NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            report_type TEXT NOT NULL,
            projected_at TEXT NOT NULL,
            FOREIGN KEY(event_id, sequence) REFERENCES events(event_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS wake_leases (
            lease_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            manager_session_id TEXT NOT NULL,
            pane_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, generation)
        );

        CREATE TABLE IF NOT EXISTS wake_outbox (
            wake_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            lease_generation INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_id, lease_generation),
            UNIQUE(event_id, run_id, lease_generation),
            FOREIGN KEY(event_id, run_id) REFERENCES events(event_id, run_id),
            FOREIGN KEY(run_id, lease_generation)
                REFERENCES wake_leases(run_id, generation)
        );

        CREATE TABLE IF NOT EXISTS wake_consumptions (
            consumption_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            lease_generation INTEGER NOT NULL,
            consumed_at TEXT NOT NULL,
            UNIQUE(event_id, lease_generation),
            FOREIGN KEY(event_id, run_id, lease_generation)
                REFERENCES wake_outbox(event_id, run_id, lease_generation)
        );

        CREATE TABLE IF NOT EXISTS inbox_acknowledgements (
            acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            acknowledged_at TEXT NOT NULL,
            FOREIGN KEY(event_id, run_id) REFERENCES events(event_id, run_id)
        );

        CREATE TABLE IF NOT EXISTS broker_owners (
            owner_id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            run_id TEXT NOT NULL,
            owner_epoch INTEGER NOT NULL,
            pid INTEGER NOT NULL,
            runtime_version TEXT NOT NULL,
            socket_path TEXT NOT NULL,
            started_at TEXT NOT NULL,
            UNIQUE(namespace, run_id, owner_epoch)
        );

        CREATE TABLE IF NOT EXISTS active_roles (
            registration_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            task_contract_digest TEXT NOT NULL,
            token_digest TEXT NOT NULL,
            active INTEGER NOT NULL,
            registered_at TEXT NOT NULL
        );
        """
        + "\n".join(triggers)
    )


class BrokerStore:
    """SQLite-backed append-only broker records guarded by the HLoop lock."""

    def __init__(
        self,
        root: Path,
        *,
        lock_path: Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.root = Path(root).resolve()
        self.database_path = self.root / "broker.sqlite3"
        self.lock_path = Path(lock_path).resolve() if lock_path else self.root / "hloop.lock"
        self.timeout_seconds = timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            raise _classify_sqlite_error(exc, operation="initialize broker") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except BaseException:
            try:
                connection.close()
            except BaseException:
                # Initialization already failed. Preserve that original
                # exception instead of replacing it with a cleanup failure.
                pass
            raise

    def _initialize(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                connection = self._connect()
                try:
                    metadata_exists = connection.execute(
                        """
                        SELECT 1 FROM sqlite_master
                        WHERE type = 'table' AND name = 'broker_meta'
                        """
                    ).fetchone()
                    row = None
                    if metadata_exists is not None:
                        row = connection.execute(
                            "SELECT value FROM broker_meta WHERE key = 'schema_version'"
                        ).fetchone()
                        if row is not None and row["value"] != str(
                            BROKER_SCHEMA_VERSION
                        ):
                            raise BrokerStorageError(
                                f"unsupported broker schema version: {row['value']}"
                            )
                    connection.executescript(_schema_sql())
                    if row is None:
                        connection.execute(
                            "INSERT INTO broker_meta(key, value) "
                            "VALUES('schema_version', ?)",
                            (str(BROKER_SCHEMA_VERSION),),
                        )
                finally:
                    connection.close()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        try:
            self.database_path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def transaction(self) -> Iterator[BrokerTransaction]:
        """Hold the shared external lock and one SQLite write transaction.

        Storage methods never commit independently.  The caller must retain the
        yielded token while applying all projections for one report.
        """

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        callbacks: list[Callable[[], None]] = []
        committed = False
        try:
            with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                connection = self._connect()
                transaction = BrokerTransaction(self, connection)
                primary_error: BaseException | None = None
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    yield transaction
                    transaction.require_active(self)
                    connection.commit()
                    committed = True
                    callbacks = list(transaction._after_commit)
                except BaseException as exc:
                    primary_error = exc
                    try:
                        connection.rollback()
                    except sqlite3.DatabaseError:
                        # Preserve the primary semantic/storage exception. A
                        # failed rollback cannot make a permanent conflict
                        # eligible for fallback spooling.
                        pass
                    raise
                finally:
                    transaction.active = False
                    try:
                        connection.close()
                    except sqlite3.DatabaseError:
                        if primary_error is None:
                            raise
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except sqlite3.DatabaseError as exc:
            raise _classify_sqlite_error(exc, operation="broker transaction") from exc
        finally:
            if committed:
                for callback in callbacks:
                    callback()

    def accept_report(
        self,
        transaction: BrokerTransaction,
        client_event: Mapping[str, Any],
        *,
        received_at: str | None = None,
    ) -> StoredEvent:
        """Idempotently append an event and its inbox projection."""

        transaction.require_active(self)
        event = validate_client_event(client_event)
        row = transaction.connection.execute(
            "SELECT payload_digest, event_json FROM events WHERE event_id = ?",
            (event["event_id"],),
        ).fetchone()
        if row is not None:
            if row["payload_digest"] != event["payload_digest"]:
                raise IdempotencyConflict(
                    f"event_id {event['event_id']} already has a different digest"
                )
            return StoredEvent(json.loads(row["event_json"]), False)

        next_row = transaction.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events"
        ).fetchone()
        sequence = int(next_row["next_sequence"])
        stored = assign_broker_sequence(event, sequence)
        timestamp = received_at or utc_now()
        parse_rfc3339(timestamp, field="received_at")
        transaction.connection.execute(
            """
            INSERT INTO events(
                sequence, event_id, payload_digest, event_json, run_id,
                role_id, attempt_id, report_type, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence,
                stored["event_id"],
                stored["payload_digest"],
                canonical_json(stored),
                stored["run_id"],
                stored["role_id"],
                stored["attempt_id"],
                stored["type"],
                timestamp,
            ),
        )
        transaction.connection.execute(
            """
            INSERT INTO inbox(
                event_id, sequence, run_id, role_id, report_type, projected_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                stored["event_id"],
                sequence,
                stored["run_id"],
                stored["role_id"],
                stored["type"],
                timestamp,
            ),
        )
        return StoredEvent(stored, True)

    def get_event(
        self, transaction: BrokerTransaction, *, event_id: str
    ) -> dict[str, Any] | None:
        transaction.require_active(self)
        row = transaction.connection.execute(
            "SELECT event_json FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return json.loads(row["event_json"]) if row is not None else None

    def events(self, transaction: BrokerTransaction) -> list[dict[str, Any]]:
        transaction.require_active(self)
        rows = transaction.connection.execute(
            "SELECT event_json FROM events ORDER BY sequence"
        ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

    def latest_role_event(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        role_id: str,
        attempt_id: str,
        report_type: str,
    ) -> dict[str, Any] | None:
        """Return the newest authenticated event for one active attempt."""

        transaction.require_active(self)
        row = transaction.connection.execute(
            """
            SELECT event_json FROM events
            WHERE run_id = ? AND role_id = ? AND attempt_id = ?
              AND report_type = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (run_id, role_id, attempt_id, report_type),
        ).fetchone()
        return json.loads(row["event_json"]) if row is not None else None

    def inbox(self, transaction: BrokerTransaction) -> list[dict[str, Any]]:
        transaction.require_active(self)
        rows = transaction.connection.execute(
            """
            SELECT inbox.event_id, inbox.sequence, inbox.run_id, inbox.role_id,
                   inbox.report_type, inbox.projected_at
            FROM inbox ORDER BY inbox.sequence
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def unconsumed_inbox(
        self, transaction: BrokerTransaction, *, run_id: str
    ) -> list[dict[str, Any]]:
        """Return inbox rows for a run without an explicit Manager ACK.

        This checks the raw append-only inbox rather than ``wake_outbox``, so a
        report that arrived while no lease was active (or under a lease
        generation that had already lapsed) still surfaces here. Combined with
        registering a wake lease in the same transaction, this closes the
        window where a report could otherwise be lost between an inbox check
        and a Manager going to sleep.

        Unread ``milestone`` rows that share the same role, attempt, stage, and
        summary as a later unread milestone are collapsed to that later row in
        this projection only; the underlying append-only ``events`` and
        ``inbox`` tables are untouched, and ``attention`` rows are never
        collapsed even when their content repeats an earlier report.
        """

        transaction.require_active(self)
        run_id = _nonempty_line(run_id, "run_id")
        rows = transaction.connection.execute(
            """
            SELECT inbox.event_id, inbox.sequence, inbox.run_id, inbox.role_id,
                   inbox.report_type, inbox.projected_at, events.event_json
            FROM inbox
            JOIN events ON events.event_id = inbox.event_id
            WHERE inbox.run_id = ?
              AND inbox.event_id NOT IN (
                  SELECT event_id FROM inbox_acknowledgements
              )
            ORDER BY inbox.sequence
            """,
            (run_id,),
        ).fetchall()
        return _coalesce_unread_milestones([dict(row) for row in rows])

    def enqueue_unacknowledged_for_lease(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        lease_generation: int,
        manager_session_id: str,
        pane_id: str,
        report_types: frozenset[str] | None = None,
        created_at: str | None = None,
    ) -> list[StoredWake]:
        """Project every unacknowledged report onto the current lease.

        Old wake rows remain append-only and unconsumed.  A fresh generation
        receives a new outbox row, so an expired/crashed Manager lease cannot
        silently discard a report.
        """

        transaction.require_active(self)
        stored: list[StoredWake] = []
        for row in self.unconsumed_inbox(transaction, run_id=run_id):
            if report_types is not None and row["report_type"] not in report_types:
                continue
            stored.append(
                self.enqueue_wake(
                    transaction,
                    event_id=row["event_id"],
                    lease_generation=lease_generation,
                    manager_session_id=manager_session_id,
                    pane_id=pane_id,
                    created_at=created_at,
                )
            )
        return stored

    def register_wake_lease(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        manager_session_id: str,
        pane_id: str,
        expires_at: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Append the next lease generation for a run."""

        transaction.require_active(self)
        run_id = _nonempty_line(run_id, "run_id")
        manager_session_id = _nonempty_line(manager_session_id, "manager_session_id")
        pane_id = _nonempty_line(pane_id, "pane_id")
        parse_rfc3339(expires_at, field="expires_at")
        timestamp = created_at or utc_now()
        parse_rfc3339(timestamp, field="created_at")
        row = transaction.connection.execute(
            """
            SELECT COALESCE(MAX(generation), 0) + 1 AS generation
            FROM wake_leases WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        generation = int(row["generation"])
        transaction.connection.execute(
            """
            INSERT INTO wake_leases(
                run_id, generation, manager_session_id, pane_id,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                generation,
                manager_session_id,
                pane_id,
                expires_at,
                timestamp,
            ),
        )
        return {
            "run_id": run_id,
            "generation": generation,
            "manager_session_id": manager_session_id,
            "pane_id": pane_id,
            "expires_at": expires_at,
            "created_at": timestamp,
        }

    def lease_generation_matches(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        generation: int,
        manager_session_id: str | None = None,
        pane_id: str | None = None,
        at: str | None = None,
    ) -> bool:
        """Check the complete identity of the current, unexpired wake lease."""

        transaction.require_active(self)
        if isinstance(generation, bool) or not isinstance(generation, int):
            return False
        row = transaction.connection.execute(
            """
            SELECT run_id, generation, manager_session_id, pane_id, expires_at
            FROM wake_leases WHERE run_id = ?
            ORDER BY generation DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None or row["generation"] != generation:
            return False
        if manager_session_id is not None and row["manager_session_id"] != manager_session_id:
            return False
        if pane_id is not None and row["pane_id"] != pane_id:
            return False
        check_time = parse_rfc3339(at or utc_now(), field="at")
        expires = parse_rfc3339(row["expires_at"], field="expires_at")
        return expires > check_time

    def enqueue_wake(
        self,
        transaction: BrokerTransaction,
        *,
        event_id: str,
        lease_generation: int,
        manager_session_id: str | None = None,
        pane_id: str | None = None,
        created_at: str | None = None,
    ) -> StoredWake:
        """Append a fixed-content wake for the current lease generation."""

        transaction.require_active(self)
        event_row = transaction.connection.execute(
            "SELECT event_json FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if event_row is None:
            raise BrokerStorageError(f"event is not stored: {event_id}")
        event = json.loads(event_row["event_json"])
        if not self.lease_generation_matches(
            transaction,
            run_id=event["run_id"],
            generation=lease_generation,
            manager_session_id=manager_session_id,
            pane_id=pane_id,
            at=created_at,
        ):
            raise LeaseGenerationMismatch(
                f"wake lease generation does not match run {event['run_id']}"
            )

        existing = transaction.connection.execute(
            """
            SELECT wake_id, event_id, run_id, lease_generation, message, created_at
            FROM wake_outbox WHERE event_id = ? AND lease_generation = ?
            """,
            (event_id, lease_generation),
        ).fetchone()
        if existing is not None:
            return StoredWake(dict(existing), False)

        timestamp = created_at or utc_now()
        parse_rfc3339(timestamp, field="created_at")
        message = fixed_wake_message(event, lease_generation)
        cursor = transaction.connection.execute(
            """
            INSERT INTO wake_outbox(
                event_id, run_id, lease_generation, message, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event["run_id"],
                lease_generation,
                message,
                timestamp,
            ),
        )
        wake = {
            "wake_id": int(cursor.lastrowid),
            "event_id": event_id,
            "run_id": event["run_id"],
            "lease_generation": lease_generation,
            "message": message,
            "created_at": timestamp,
        }
        return StoredWake(wake, True)

    def pending_wakes(
        self, transaction: BrokerTransaction, *, at: str | None = None
    ) -> list[dict[str, Any]]:
        """Return only wakes deliverable under the current lease generation.

        Stale wakes are intentionally left unprocessed.  Registering a later
        lease re-enqueues the still-unacknowledged inbox event for that fresh
        generation.
        """

        transaction.require_active(self)
        timestamp = at or utc_now()
        parse_rfc3339(timestamp, field="at")
        rows = transaction.connection.execute(
            """
            SELECT w.wake_id, w.event_id, w.run_id, w.lease_generation,
                   w.message, w.created_at
            FROM wake_outbox AS w
            LEFT JOIN wake_consumptions AS c
              ON c.event_id = w.event_id
             AND c.lease_generation = w.lease_generation
            LEFT JOIN inbox_acknowledgements AS a
              ON a.event_id = w.event_id
            WHERE c.consumption_id IS NULL
              AND a.acknowledgement_id IS NULL
            ORDER BY w.wake_id
            """
        ).fetchall()
        pending: list[dict[str, Any]] = []
        for row in rows:
            wake = dict(row)
            if self.lease_generation_matches(
                transaction,
                run_id=wake["run_id"],
                generation=wake["lease_generation"],
                at=timestamp,
            ):
                pending.append(wake)
        return pending

    def acknowledge_inbox(
        self,
        transaction: BrokerTransaction,
        *,
        event_id: str,
        run_id: str,
        acknowledged_at: str | None = None,
    ) -> InboxAcknowledgement:
        """Explicitly acknowledge one displayed inbox event exactly once.

        A displayed milestone can represent several earlier unread milestones
        collapsed by :meth:`unconsumed_inbox`.  Acknowledging that displayed
        event also acknowledges the matching milestones up to its sequence so
        the hidden duplicates do not resurface one by one.  Later reports and
        every non-milestone remain independent inbox events.
        """

        transaction.require_active(self)
        event_id = _nonempty_line(event_id, "event_id")
        run_id = _nonempty_line(run_id, "run_id")
        row = transaction.connection.execute(
            """
            SELECT inbox.run_id, inbox.sequence, inbox.role_id,
                   inbox.report_type, events.attempt_id, events.event_json
            FROM inbox
            JOIN events ON events.event_id = inbox.event_id
            WHERE inbox.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None or row["run_id"] != run_id:
            return InboxAcknowledgement(False, "missing")
        existing = transaction.connection.execute(
            "SELECT 1 FROM inbox_acknowledgements WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing is not None:
            return InboxAcknowledgement(False, "duplicate")
        timestamp = acknowledged_at or utc_now()
        parse_rfc3339(timestamp, field="acknowledged_at")
        acknowledged_event_ids = [event_id]
        if row["report_type"] == "milestone":
            displayed_key = _milestone_coalescing_key(row)
            candidates = transaction.connection.execute(
                """
                SELECT inbox.event_id, inbox.role_id, events.event_json
                FROM inbox
                JOIN events ON events.event_id = inbox.event_id
                LEFT JOIN inbox_acknowledgements AS acknowledgements
                  ON acknowledgements.event_id = inbox.event_id
                WHERE inbox.run_id = ?
                  AND inbox.role_id = ?
                  AND inbox.report_type = 'milestone'
                  AND inbox.sequence <= ?
                  AND events.attempt_id = ?
                  AND acknowledgements.acknowledgement_id IS NULL
                ORDER BY inbox.sequence
                """,
                (run_id, row["role_id"], row["sequence"], row["attempt_id"]),
            ).fetchall()
            acknowledged_event_ids = [
                candidate["event_id"]
                for candidate in candidates
                if _milestone_coalescing_key(candidate) == displayed_key
            ]
        transaction.connection.executemany(
            """
            INSERT INTO inbox_acknowledgements(event_id, run_id, acknowledged_at)
            VALUES (?, ?, ?)
            """,
            [
                (acknowledged_event_id, run_id, timestamp)
                for acknowledged_event_id in acknowledged_event_ids
            ],
        )
        return InboxAcknowledgement(True, "acknowledged")

    def consume_wake(
        self,
        transaction: BrokerTransaction,
        *,
        event_id: str,
        lease_generation: int,
        consumed_at: str | None = None,
    ) -> WakeConsumption:
        """Consume a wake once; duplicate deliveries are harmless."""

        transaction.require_active(self)
        row = transaction.connection.execute(
            """
            SELECT run_id FROM wake_outbox
            WHERE event_id = ? AND lease_generation = ?
            """,
            (event_id, lease_generation),
        ).fetchone()
        if row is None:
            return WakeConsumption(False, "missing")
        timestamp = consumed_at or utc_now()
        parse_rfc3339(timestamp, field="consumed_at")
        if not self.lease_generation_matches(
            transaction,
            run_id=row["run_id"],
            generation=lease_generation,
            at=timestamp,
        ):
            return WakeConsumption(False, "stale-lease")
        existing = transaction.connection.execute(
            """
            SELECT 1 FROM wake_consumptions
            WHERE event_id = ? AND lease_generation = ?
            """,
            (event_id, lease_generation),
        ).fetchone()
        if existing is not None:
            return WakeConsumption(False, "duplicate")
        self._record_wake_terminal(
            transaction,
            event_id=event_id,
            run_id=row["run_id"],
            lease_generation=lease_generation,
            terminal_at=timestamp,
        )
        return WakeConsumption(True, "consumed")

    def _record_wake_terminal(
        self,
        transaction: BrokerTransaction,
        *,
        event_id: str,
        run_id: str,
        lease_generation: int,
        terminal_at: str,
    ) -> None:
        """Append the shared terminal record for consumed or stale wakes."""

        transaction.require_active(self)
        transaction.connection.execute(
            """
            INSERT OR IGNORE INTO wake_consumptions(
                event_id, run_id, lease_generation, consumed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (event_id, run_id, lease_generation, terminal_at),
        )

    def replay_spool(
        self, transaction: BrokerTransaction, spool_directory: Path
    ) -> list[StoredEvent]:
        """Replay a spool batch and remove files only after a successful commit.

        A poison entry (invalid content, stale-run, revoked-attempt, a
        digest/token mismatch, or a per-entry storage rejection) is
        quarantined with audit metadata instead of aborting the whole batch,
        so later valid entries still replay in the same recovery pass.
        """

        transaction.require_active(self)
        directory = Path(spool_directory)
        valid_entries, poison_entries = _scan_spool_directory(directory)
        replayed: list[StoredEvent] = []
        for path, reason, detail, event_id in poison_entries:
            transaction.after_commit(
                lambda path=path, reason=reason, detail=detail, event_id=event_id: _quarantine_spool_entry(
                    path, directory, reason=reason, detail=detail, event_id=event_id
                )
            )
        for _, path, event, authentication in valid_entries:
            auth_failure = self._spool_authentication_failure_reason(
                transaction, event=event, authentication=authentication
            )
            if auth_failure is not None:
                detail = (
                    "spooled report does not match an active "
                    "run/role/attempt/digest/token identity: "
                    f"{event['role_id']}/{event['attempt_id']}"
                )
                transaction.after_commit(
                    lambda path=path, reason=auth_failure, detail=detail, event_id=event[
                        "event_id"
                    ]: _quarantine_spool_entry(
                        path, directory, reason=reason, detail=detail, event_id=event_id
                    )
                )
                continue
            try:
                stored = self.accept_report(transaction, event)
            except BrokerStorageError as exc:
                reason = (
                    "idempotency-conflict"
                    if isinstance(exc, IdempotencyConflict)
                    else "storage-error"
                )
                transaction.after_commit(
                    lambda path=path, reason=reason, detail=str(exc), event_id=event[
                        "event_id"
                    ]: _quarantine_spool_entry(
                        path, directory, reason=reason, detail=detail, event_id=event_id
                    )
                )
                continue
            replayed.append(stored)
            expected_digest = event["payload_digest"]
            transaction.after_commit(
                lambda path=path, digest=expected_digest: _remove_spool_if_unchanged(
                    path, digest
                )
            )
        return replayed

    def _spool_authentication_failure_reason(
        self,
        transaction: BrokerTransaction,
        *,
        event: Mapping[str, Any],
        authentication: Mapping[str, str] | None,
    ) -> str | None:
        """Classify why a spooled report cannot be authenticated, or None if it can."""

        if authentication is None:
            return "invalid"
        current = self._latest_active_role(
            transaction, run_id=event["run_id"], role_id=event["role_id"]
        )
        if current is None:
            return "stale-run"
        if not current["active"]:
            return "revoked-attempt"
        if current["attempt_id"] != event["attempt_id"]:
            return "revoked-attempt"
        if current["task_contract_digest"] != event["task_contract_digest"].lower():
            return "digest-mismatch"
        token_digest = hashlib.sha256(
            str(authentication.get("token") or "").encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(current["token_digest"], token_digest):
            return "token-mismatch"
        return None

    def register_owner(
        self,
        transaction: BrokerTransaction,
        *,
        namespace: str,
        run_id: str,
        runtime_version: str,
        socket_path: Path,
        pid: int | None = None,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        """Append a broker owner epoch and return its status metadata."""

        transaction.require_active(self)
        namespace = _nonempty_line(namespace, "namespace")
        run_id = _nonempty_line(run_id, "run_id")
        runtime_version = _nonempty_line(runtime_version, "runtime_version")
        socket_text = str(Path(socket_path))
        if not Path(socket_text).is_absolute():
            raise BrokerStorageError("socket_path must be absolute")
        owner_pid = os.getpid() if pid is None else pid
        if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid < 1:
            raise BrokerStorageError("pid must be a positive integer")
        timestamp = started_at or utc_now()
        parse_rfc3339(timestamp, field="started_at")
        row = transaction.connection.execute(
            """
            SELECT COALESCE(MAX(owner_epoch), 0) + 1 AS owner_epoch
            FROM broker_owners WHERE namespace = ? AND run_id = ?
            """,
            (namespace, run_id),
        ).fetchone()
        owner_epoch = int(row["owner_epoch"])
        metadata = {
            "namespace": namespace,
            "run_id": run_id,
            "runtime_version": runtime_version,
            "owner_epoch": owner_epoch,
            "pid": owner_pid,
            "socket_path": socket_text,
            "started_at": timestamp,
        }
        transaction.connection.execute(
            """
            INSERT INTO broker_owners(
                namespace, run_id, owner_epoch, pid, runtime_version,
                socket_path, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                namespace,
                run_id,
                owner_epoch,
                owner_pid,
                runtime_version,
                socket_text,
                timestamp,
            ),
        )
        return metadata

    def current_owner(
        self, transaction: BrokerTransaction, *, namespace: str, run_id: str
    ) -> dict[str, Any] | None:
        transaction.require_active(self)
        row = transaction.connection.execute(
            """
            SELECT namespace, run_id, runtime_version, owner_epoch, pid,
                   socket_path, started_at
            FROM broker_owners
            WHERE namespace = ? AND run_id = ?
            ORDER BY owner_epoch DESC LIMIT 1
            """,
            (namespace, run_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def current_lease(
        self, transaction: BrokerTransaction, *, run_id: str
    ) -> dict[str, Any] | None:
        """Return the most recently registered wake lease for a run, if any."""

        transaction.require_active(self)
        row = transaction.connection.execute(
            """
            SELECT run_id, generation, manager_session_id, pane_id,
                   expires_at, created_at
            FROM wake_leases WHERE run_id = ?
            ORDER BY generation DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def register_active_role(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        role_id: str,
        attempt_id: str,
        task_contract_digest: str,
        token: str,
        registered_at: str | None = None,
    ) -> dict[str, Any]:
        """Append the authoritative identity a role must present on every report."""

        transaction.require_active(self)
        run_id = _nonempty_line(run_id, "run_id")
        role_id = _nonempty_line(role_id, "role_id")
        attempt_id = _nonempty_line(attempt_id, "attempt_id")
        task_contract_digest = _nonempty_line(
            task_contract_digest, "task_contract_digest"
        ).lower()
        token = _nonempty_line(token, "token")
        timestamp = registered_at or utc_now()
        parse_rfc3339(timestamp, field="registered_at")
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        transaction.connection.execute(
            """
            INSERT INTO active_roles(
                run_id, role_id, attempt_id, task_contract_digest,
                token_digest, active, registered_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (run_id, role_id, attempt_id, task_contract_digest, token_digest, timestamp),
        )
        return {
            "run_id": run_id,
            "role_id": role_id,
            "attempt_id": attempt_id,
            "task_contract_digest": task_contract_digest,
            "active": True,
            "registered_at": timestamp,
        }

    def revoke_active_role(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        role_id: str,
        revoked_at: str | None = None,
    ) -> bool:
        """Append a tombstone row so a superseded attempt cannot submit reports."""

        transaction.require_active(self)
        run_id = _nonempty_line(run_id, "run_id")
        role_id = _nonempty_line(role_id, "role_id")
        current = self._latest_active_role(transaction, run_id=run_id, role_id=role_id)
        if current is None or not current["active"]:
            return False
        timestamp = revoked_at or utc_now()
        parse_rfc3339(timestamp, field="revoked_at")
        transaction.connection.execute(
            """
            INSERT INTO active_roles(
                run_id, role_id, attempt_id, task_contract_digest,
                token_digest, active, registered_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (
                run_id,
                role_id,
                current["attempt_id"],
                current["task_contract_digest"],
                current["token_digest"],
                timestamp,
            ),
        )
        return True

    def authenticate_role_report(
        self,
        transaction: BrokerTransaction,
        *,
        run_id: str,
        role_id: str,
        attempt_id: str,
        task_contract_digest: str,
        token: str,
    ) -> bool:
        """Return whether a submitted report matches the registered active identity."""

        transaction.require_active(self)
        current = self._latest_active_role(transaction, run_id=run_id, role_id=role_id)
        if current is None or not current["active"]:
            return False
        if current["attempt_id"] != attempt_id:
            return False
        if current["task_contract_digest"] != task_contract_digest.lower():
            return False
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(current["token_digest"], token_digest)

    def has_active_role_registration(
        self, transaction: BrokerTransaction, *, run_id: str, role_id: str
    ) -> bool:
        transaction.require_active(self)
        return self._latest_active_role(transaction, run_id=run_id, role_id=role_id) is not None

    def _latest_active_role(
        self, transaction: BrokerTransaction, *, run_id: str, role_id: str
    ) -> dict[str, Any] | None:
        row = transaction.connection.execute(
            """
            SELECT run_id, role_id, attempt_id, task_contract_digest,
                   token_digest, active, registered_at
            FROM active_roles WHERE run_id = ? AND role_id = ?
            ORDER BY registration_id DESC LIMIT 1
            """,
            (run_id, role_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["active"] = bool(result["active"])
        return result


def _coalesce_unread_milestones(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse unread ``milestone`` duplicates in the returned projection.

    Only the last unread milestone for each ``(role_id, attempt_id, stage,
    summary)`` key survives; earlier duplicates are dropped from the returned
    list but were never mutated or deleted in storage. Every non-milestone
    row (including every ``attention``) is always kept.
    """

    keep_index: dict[tuple[str, str, str, str], int] = {}
    for index, row in enumerate(rows):
        if row["report_type"] != "milestone":
            continue
        keep_index[_milestone_coalescing_key(row)] = index
    kept = set(keep_index.values())
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if row["report_type"] == "milestone" and index not in kept:
            continue
        result.append({key: value for key, value in row.items() if key != "event_json"})
    return result


def _milestone_coalescing_key(
    row: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    """Return the identity shared by milestone projection and acknowledgement."""

    event = json.loads(row["event_json"])
    return (row["role_id"], event["attempt_id"], event["stage"], event["summary"])


def role_outbox_event_id(
    outbox_path: Path,
    *,
    payload_digest: str,
    requested_event_id: str | None = None,
    max_entries: int = 64,
) -> str:
    """Resolve and atomically persist one role's event ID before it is sent.

    The event ID must be durable before the caller ever reaches the broker (or
    the fallback spool): if the client is interrupted after the broker commits
    but before it observes success, or the broker call itself never lands, a
    retry that rebuilds an identical report body reuses the same event ID
    recorded here instead of minting a fresh one. The broker's own per-event
    idempotency then guarantees the retry produces no duplicate event, inbox
    projection, or wake.
    """

    directory = outbox_path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    lock_fd = os.open(directory / f".{outbox_path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            # `_atomic_write_json` canonicalizes with `sort_keys=True`, which
            # would silently reorder a top-level `{digest: event_id}` mapping
            # alphabetically on every write. An explicit oldest-first list of
            # pairs is the only way to actually trim by recency below.
            entries: list[list[str]] = []
            if outbox_path.exists():
                try:
                    raw = json.loads(outbox_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = {}
                if isinstance(raw, Mapping) and isinstance(raw.get("entries"), list):
                    entries = [
                        [str(pair[0]), str(pair[1])]
                        for pair in raw["entries"]
                        if isinstance(pair, list)
                        and len(pair) == 2
                        and isinstance(pair[0], str)
                        and isinstance(pair[1], str)
                    ]
            existing = next(
                (event_id for digest, event_id in entries if digest == payload_digest),
                None,
            )
            event_id = requested_event_id or existing or new_event_id()
            entries = [pair for pair in entries if pair[0] != payload_digest]
            entries.append([payload_digest, event_id])
            if len(entries) > max_entries:
                entries = entries[len(entries) - max_entries :]
            _atomic_write_json(outbox_path, {"entries": entries})
            return event_id
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def role_outbox_client_event(
    outbox_path: Path,
    *,
    report: Mapping[str, Any],
    requested_event_id: str | None = None,
    invocation_id: str | None = None,
    max_entries: int = 64,
) -> dict[str, Any]:
    """Persist and reuse a client event for one logical report invocation.

    ``created_at`` is transport metadata, not part of the caller's semantic
    report identity. A caller-stable ``invocation_id`` selects the first exact
    retained envelope regardless of pending/confirmed delivery state. Without
    either explicit ID, legacy implicit retry continues to reuse only a pending
    semantic match. Retention is bounded by ``max_entries``.
    """

    if requested_event_id is not None and invocation_id is not None:
        raise ReportValidationError(
            "event_id and invocation_id are mutually exclusive"
        )
    normalized_invocation_id = (
        normalize_invocation_id(invocation_id)
        if invocation_id is not None
        else None
    )
    normalized = validate_report(report)
    semantic_payload = dict(normalized)
    semantic_payload.pop("created_at", None)
    semantic_digest = hashlib.sha256(
        canonical_json(semantic_payload).encode("utf-8")
    ).hexdigest()
    current_payload_digest = payload_digest(normalized)

    directory = outbox_path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    lock_fd = os.open(
        directory / f".{outbox_path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            raw_entries: list[Any] = []
            if outbox_path.exists():
                try:
                    raw = json.loads(outbox_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise BrokerIntegrityError(
                        f"cannot read role report outbox {outbox_path}: {exc}"
                    ) from exc
                if not isinstance(raw, Mapping) or not isinstance(
                    raw.get("entries"), list
                ):
                    raise BrokerIntegrityError(
                        f"role report outbox has an invalid shape: {outbox_path}"
                    )
                raw_entries = list(raw["entries"])

            retained: list[Any] = []
            existing_event: dict[str, Any] | None = None
            requested_event: tuple[str, dict[str, Any]] | None = None
            invocation_event: tuple[str, dict[str, Any]] | None = None
            legacy_event_id: str | None = None
            for entry in raw_entries:
                if isinstance(entry, Mapping):
                    if set(entry) not in (
                        {"semantic_digest", "event"},
                        {"semantic_digest", "event", "status"},
                        {
                            "semantic_digest",
                            "event",
                            "status",
                            "invocation_id",
                        },
                    ):
                        raise BrokerIntegrityError(
                            f"role report outbox entry has an invalid shape: {outbox_path}"
                        )
                    event = validate_client_event(entry["event"])
                    recorded_semantic_digest = str(entry["semantic_digest"])
                    status = str(entry.get("status", "pending"))
                    recorded_invocation_id = (
                        normalize_invocation_id(entry["invocation_id"])
                        if "invocation_id" in entry
                        else None
                    )
                    if status not in {"pending", "confirmed"}:
                        raise BrokerIntegrityError(
                            f"role report outbox entry has an invalid status: {outbox_path}"
                        )
                    event_semantic_payload = {
                        key: value
                        for key, value in event.items()
                        if key not in {"created_at", "event_id", "payload_digest"}
                    }
                    actual_semantic_digest = hashlib.sha256(
                        canonical_json(event_semantic_payload).encode("utf-8")
                    ).hexdigest()
                    if recorded_semantic_digest != actual_semantic_digest:
                        raise BrokerIntegrityError(
                            f"role report outbox semantic digest mismatch: {outbox_path}"
                        )
                    retained_entry = {
                        "semantic_digest": recorded_semantic_digest,
                        "event": event,
                        "status": status,
                    }
                    if recorded_invocation_id is not None:
                        retained_entry["invocation_id"] = recorded_invocation_id
                    retained.append(retained_entry)
                    if (
                        recorded_semantic_digest == semantic_digest
                        and status == "pending"
                    ):
                        existing_event = event
                    if event["event_id"] == requested_event_id:
                        requested_event = (recorded_semantic_digest, event)
                    if (
                        normalized_invocation_id is not None
                        and recorded_invocation_id == normalized_invocation_id
                    ):
                        if recorded_semantic_digest != semantic_digest:
                            raise IdempotencyConflict(
                                f"invocation id {normalized_invocation_id} is retained "
                                "for different semantic content"
                            )
                        if invocation_event is None:
                            invocation_event = (recorded_semantic_digest, event)
                    continue

                # Read the 0.5.0 pre-fix digest/event-id pair format so an
                # already persisted, same-second send still upgrades without
                # allocating a duplicate ID.
                if (
                    isinstance(entry, list)
                    and len(entry) == 2
                    and isinstance(entry[0], str)
                    and isinstance(entry[1], str)
                ):
                    retained.append([entry[0], entry[1]])
                    if entry[0] == current_payload_digest:
                        legacy_event_id = entry[1]
                    continue
                raise BrokerIntegrityError(
                    f"role report outbox entry has an invalid shape: {outbox_path}"
                )

            if requested_event is not None:
                recorded_semantic_digest, event = requested_event
                if recorded_semantic_digest != semantic_digest:
                    raise IdempotencyConflict(
                        f"requested event id {requested_event_id} is retained for "
                        "different semantic content"
                    )
                return event

            if invocation_event is not None:
                return invocation_event[1]

            if (
                requested_event_id is None
                and normalized_invocation_id is None
                and existing_event is not None
            ):
                return existing_event

            event_id = requested_event_id or legacy_event_id or new_event_id()
            client_event = prepare_client_event(normalized, event_id=event_id)
            retained = [
                entry
                for entry in retained
                if not (
                    isinstance(entry, list)
                    and len(entry) == 2
                    and entry[0] == current_payload_digest
                )
            ]
            retained_entry = {
                "semantic_digest": semantic_digest,
                "event": client_event,
                "status": "pending",
            }
            if normalized_invocation_id is not None:
                retained_entry["invocation_id"] = normalized_invocation_id
            retained.append(retained_entry)
            if len(retained) > max_entries:
                retained = retained[len(retained) - max_entries :]
            _atomic_write_json(outbox_path, {"entries": retained})
            return client_event
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def confirm_role_outbox_delivery(
    outbox_path: Path,
    *,
    event_id: str,
    payload_digest_value: str,
) -> bool:
    """Mark one exact pending outbox envelope as durably delivered.

    Matching both event ID and payload digest prevents a late confirmation
    from changing a newer identical report's pending envelope. Missing entries
    are an idempotent no-op, which is required when concurrent callers both
    retried the same pending event.
    """

    directory = outbox_path.parent
    lock_fd = os.open(
        directory / f".{outbox_path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                raw = json.loads(outbox_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BrokerIntegrityError(
                    f"cannot read role report outbox {outbox_path}: {exc}"
                ) from exc
            if not isinstance(raw, Mapping) or not isinstance(
                raw.get("entries"), list
            ):
                raise BrokerIntegrityError(
                    f"role report outbox has an invalid shape: {outbox_path}"
                )

            changed = False
            entries: list[Any] = []
            for entry in raw["entries"]:
                if isinstance(entry, Mapping):
                    if set(entry) not in (
                        {"semantic_digest", "event"},
                        {"semantic_digest", "event", "status"},
                        {
                            "semantic_digest",
                            "event",
                            "status",
                            "invocation_id",
                        },
                    ):
                        raise BrokerIntegrityError(
                            f"role report outbox entry has an invalid shape: {outbox_path}"
                        )
                    event = validate_client_event(entry["event"])
                    status = str(entry.get("status", "pending"))
                    recorded_invocation_id = (
                        normalize_invocation_id(entry["invocation_id"])
                        if "invocation_id" in entry
                        else None
                    )
                    if status not in {"pending", "confirmed"}:
                        raise BrokerIntegrityError(
                            f"role report outbox entry has an invalid status: {outbox_path}"
                        )
                    event_semantic_payload = {
                        key: value
                        for key, value in event.items()
                        if key not in {"created_at", "event_id", "payload_digest"}
                    }
                    actual_semantic_digest = hashlib.sha256(
                        canonical_json(event_semantic_payload).encode("utf-8")
                    ).hexdigest()
                    recorded_semantic_digest = str(entry["semantic_digest"])
                    if recorded_semantic_digest != actual_semantic_digest:
                        raise BrokerIntegrityError(
                            f"role report outbox semantic digest mismatch: {outbox_path}"
                        )
                    if event["event_id"] == event_id:
                        if event["payload_digest"] != payload_digest_value:
                            raise BrokerIntegrityError(
                                "role report outbox delivery confirmation digest mismatch: "
                                f"{outbox_path}"
                            )
                        if status == "pending":
                            status = "confirmed"
                            changed = True
                    retained_entry = {
                        "semantic_digest": recorded_semantic_digest,
                        "event": event,
                        "status": status,
                    }
                    if recorded_invocation_id is not None:
                        retained_entry["invocation_id"] = recorded_invocation_id
                    entries.append(retained_entry)
                    continue
                if (
                    isinstance(entry, list)
                    and len(entry) == 2
                    and isinstance(entry[0], str)
                    and isinstance(entry[1], str)
                ):
                    entries.append(entry)
                    continue
                raise BrokerIntegrityError(
                    f"role report outbox entry has an invalid shape: {outbox_path}"
                )

            if changed:
                _atomic_write_json(outbox_path, {"entries": entries})
            return changed
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def fixed_wake_message(event: Mapping[str, Any], lease_generation: int) -> str:
    """Build the non-injectable message delivered to a Manager pane."""

    return "\n".join(
        [
            "HLoop report ready",
            f"run_id: {event['run_id']}",
            f"event_id: {event['event_id']}",
            f"lease_generation: {lease_generation}",
            f"role_id: {event['role_id']}",
            f"type: {event['type']}",
            f"command: hloop inbox show {event['event_id']}",
        ]
    )


def derive_runtime_socket_path(
    namespace: str,
    run_id: str,
    *,
    runtime_directory: Path | None = None,
) -> Path:
    """Derive a short, stable Unix socket path without repository path data."""

    namespace = _nonempty_line(namespace, "namespace")
    run_id = _nonempty_line(run_id, "run_id")
    if runtime_directory is None:
        configured = os.environ.get("HLOOP_RUNTIME_DIR") or os.environ.get(
            "XDG_RUNTIME_DIR"
        )
        runtime_directory = Path(configured) if configured else Path(tempfile.gettempdir())
    runtime_directory = Path(runtime_directory).expanduser()
    if not runtime_directory.is_absolute():
        raise BrokerStorageError("runtime_directory must be absolute")
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    digest = hashlib.sha256(f"{namespace}\0{run_id}".encode("utf-8")).hexdigest()[:24]
    path = runtime_directory / f"herdr-dev-loop-{uid}" / f"{digest}.sock"
    length = len(os.fsencode(path))
    if length > UNIX_SOCKET_PATH_MAX_BYTES:
        raise BrokerStorageError(
            f"derived Unix socket path is {length} bytes; maximum is "
            f"{UNIX_SOCKET_PATH_MAX_BYTES}"
        )
    return path


def spool_client_event(
    spool_directory: Path,
    client_event: Mapping[str, Any],
    *,
    authentication: Mapping[str, Any] | None = None,
) -> Path:
    """Atomically persist a report and its local-only authentication envelope."""

    event = validate_client_event(client_event)
    auth = _validate_spool_authentication(authentication, event=event)
    directory = Path(spool_directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    target = directory / f"{event['event_id']}.json"
    lock_fd = os.open(directory / ".enqueue.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if os.path.lexists(target):
            _, existing, existing_auth = _read_spool_entry(target)
            if (
                existing["payload_digest"] != event["payload_digest"]
                or existing_auth != auth
            ):
                raise IdempotencyConflict(
                    f"spooled event_id {event['event_id']} has different content or authentication"
                )
            return target
        sequence = _next_spool_sequence(directory)
        try:
            _atomic_create_json(
                target,
                {
                    "spool_sequence": sequence,
                    "event": event,
                    "authentication": auth,
                },
            )
        except FileExistsError:
            _, existing, existing_auth = _read_spool_entry(target)
            if (
                existing["payload_digest"] != event["payload_digest"]
                or existing_auth != auth
            ):
                raise IdempotencyConflict(
                    f"spooled event_id {event['event_id']} has different content or authentication"
                )
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return target


def _next_spool_sequence(directory: Path) -> int:
    highest = 0
    for path in directory.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            raise BrokerStorageError(f"unsafe spool entry: {path}")
        sequence, event, _ = _read_spool_entry(path)
        if path.stem != event["event_id"]:
            raise BrokerStorageError(
                f"spool filename does not match event_id: {path.name}"
            )
        if sequence is not None:
            highest = max(highest, sequence)
    return highest + 1


def _read_spool_entry(
    path: Path,
) -> tuple[int | None, dict[str, Any], dict[str, str] | None]:
    if path.is_symlink() or not path.is_file():
        raise BrokerStorageError(f"unsafe spool entry: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, Mapping) and set(value) in (
            {"spool_sequence", "event"},
            {"spool_sequence", "event", "authentication"},
        ):
            sequence = value["spool_sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise BrokerStorageError(
                    f"invalid spool entry {path}: spool_sequence must be positive"
                )
            event = validate_client_event(value["event"])
            auth = _validate_spool_authentication(
                value.get("authentication"), event=event, allow_missing=True
            )
            return sequence, event, auth
        return None, validate_client_event(value), None
    except (OSError, json.JSONDecodeError, ReportValidationError, BrokerStorageError) as exc:
        raise BrokerStorageError(f"invalid spool entry {path}: {exc}") from exc


def _read_spool_file(path: Path) -> dict[str, Any]:
    _, event, _ = _read_spool_entry(path)
    return event


def _validate_spool_authentication(
    authentication: Mapping[str, Any] | None,
    *,
    event: Mapping[str, Any],
    allow_missing: bool = False,
) -> dict[str, str] | None:
    if authentication is None:
        if allow_missing:
            return None
        raise ReportAuthenticationError("spooled report authentication is required")
    fields = {
        "run_id",
        "role_id",
        "attempt_id",
        "task_contract_digest",
        "token",
    }
    if not isinstance(authentication, Mapping) or set(authentication) != fields:
        raise ReportAuthenticationError(
            "spooled report authentication has an invalid field set"
        )
    normalized = {key: _nonempty_line(authentication[key], key) for key in fields}
    for key in ("run_id", "role_id", "attempt_id", "task_contract_digest"):
        if normalized[key] != event[key]:
            raise ReportAuthenticationError(
                f"spooled authentication {key} does not match the event"
            )
    return normalized


_SPOOL_QUARANTINE_DIRNAME = "quarantine"


def _scan_spool_directory(
    spool_directory: Path,
) -> tuple[
    list[tuple[int | None, Path, dict[str, Any], dict[str, str] | None]],
    list[tuple[Path, str, str, str | None]],
]:
    """Split a spool directory into valid entries and poison entries.

    Poison entries never abort the scan: each unsafe path, unparseable file,
    filename/event_id mismatch, or duplicate spool_sequence is recorded as
    ``(path, reason, detail, event_id)`` instead of raising, so a single
    corrupted file cannot block replay of the rest of the batch. Valid
    entries are returned in durable enqueue order (by spool_sequence, then
    filename for legacy unsequenced entries).
    """

    directory = Path(spool_directory)
    valid: list[tuple[int | None, Path, dict[str, Any], dict[str, str] | None]] = []
    poison: list[tuple[Path, str, str, str | None]] = []
    if not directory.exists():
        return valid, poison
    seen_sequences: dict[int, Path] = {}
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            poison.append((path, "invalid", f"unsafe spool entry: {path}", None))
            continue
        try:
            sequence, event, authentication = _read_spool_entry(path)
        except (BrokerStorageError, ReportAuthenticationError) as exc:
            poison.append((path, "invalid", str(exc), _best_effort_event_id(path)))
            continue
        if path.stem != event["event_id"]:
            poison.append(
                (
                    path,
                    "invalid",
                    f"spool filename does not match event_id: {path.name}",
                    event["event_id"],
                )
            )
            continue
        if sequence is not None and sequence in seen_sequences:
            poison.append(
                (
                    path,
                    "invalid",
                    "duplicate spool_sequence "
                    f"{sequence}: {seen_sequences[sequence].name}, {path.name}",
                    event["event_id"],
                )
            )
            continue
        if sequence is not None:
            seen_sequences[sequence] = path
        valid.append((sequence, path, event, authentication))
    valid.sort(
        key=lambda item: (
            item[0] is not None,
            item[0] if item[0] is not None else 0,
            item[1].name,
        )
    )
    return valid, poison


def _best_effort_event_id(path: Path) -> str | None:
    """Try to recover an event_id from an otherwise-unparseable spool file."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, Mapping):
        candidate = raw.get("event", raw)
        if isinstance(candidate, Mapping):
            event_id = candidate.get("event_id")
            if isinstance(event_id, str) and event_id:
                return event_id
    return None


def _quarantine_spool_entry(
    path: Path,
    spool_directory: Path,
    *,
    reason: str,
    detail: str,
    event_id: str | None,
) -> None:
    """Atomically move a poison spool entry aside with audit metadata."""

    if not path.exists():
        return
    quarantine_dir = Path(spool_directory) / _SPOOL_QUARANTINE_DIRNAME
    quarantine_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        quarantine_dir.chmod(0o700)
    except OSError:
        pass
    target = quarantine_dir / path.name
    suffix = 0
    while target.exists():
        suffix += 1
        target = quarantine_dir / f"{path.stem}.{suffix}{path.suffix}"
    os.replace(path, target)
    _atomic_write_json(
        target.with_name(target.name + ".audit.json"),
        {
            "quarantined_at": utc_now(),
            "reason": reason,
            "detail": detail,
            "original_filename": path.name,
            "event_id": event_id or "",
        },
    )


def spool_quarantine_count(spool_directory: Path) -> int:
    """Return the number of poison entries currently quarantined."""

    quarantine_dir = Path(spool_directory) / _SPOOL_QUARANTINE_DIRNAME
    if not quarantine_dir.exists():
        return 0
    return sum(1 for path in quarantine_dir.glob("*.json") if not path.name.endswith(".audit.json"))


def spool_has_entries(spool_directory: Path) -> bool:
    """Return whether the spool directory holds any pending entry.

    This is a lightweight existence check (no parsing) so a poison-only
    spool still reports pending work and triggers a quarantining replay.
    """

    directory = Path(spool_directory)
    if not directory.exists():
        return False
    return any(directory.glob("*.json"))


def iter_spooled_events(
    spool_directory: Path,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield valid spool entries in durable enqueue order.

    Poison entries are silently skipped; use ``replay_spool`` to quarantine
    them durably.
    """

    valid_entries, _ = _scan_spool_directory(spool_directory)
    for _, path, event, _authentication in valid_entries:
        yield path, event


def write_owner_metadata(path: Path, metadata: Mapping[str, Any]) -> None:
    """Atomically publish the current owner status file with private mode."""

    _atomic_write_json(Path(path), validate_owner_metadata(metadata))


def read_owner_metadata(path: Path) -> dict[str, Any]:
    """Read and validate an owner status file."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrokerStorageError(f"cannot read broker owner metadata: {exc}") from exc
    return validate_owner_metadata(value)


def validate_owner_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "namespace",
        "run_id",
        "runtime_version",
        "owner_epoch",
        "pid",
        "socket_path",
        "started_at",
    }
    if not isinstance(metadata, Mapping) or set(metadata) != fields:
        raise BrokerStorageError("broker owner metadata has an invalid field set")
    owner_epoch = metadata["owner_epoch"]
    pid = metadata["pid"]
    if isinstance(owner_epoch, bool) or not isinstance(owner_epoch, int) or owner_epoch < 1:
        raise BrokerStorageError("owner_epoch must be a positive integer")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        raise BrokerStorageError("pid must be a positive integer")
    socket_path = Path(str(metadata["socket_path"]))
    if not socket_path.is_absolute():
        raise BrokerStorageError("socket_path must be absolute")
    started_at = _nonempty_line(metadata["started_at"], "started_at")
    parse_rfc3339(started_at, field="started_at")
    return {
        "namespace": _nonempty_line(metadata["namespace"], "namespace"),
        "run_id": _nonempty_line(metadata["run_id"], "run_id"),
        "runtime_version": _nonempty_line(
            metadata["runtime_version"], "runtime_version"
        ),
        "owner_epoch": owner_epoch,
        "pid": pid,
        "socket_path": str(socket_path),
        "started_at": started_at,
    }


def _nonempty_line(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise BrokerStorageError(f"{field_name} must be a non-empty single-line string")
    if "\0" in value:
        raise BrokerStorageError(f"{field_name} contains a null byte")
    return value


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    payload = canonical_json(dict(value)) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_create_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create an immutable spool entry without replacing a concurrent writer."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    payload = canonical_json(dict(value)) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _remove_spool_if_unchanged(path: Path, expected_digest: str) -> None:
    if not path.exists():
        return
    event = _read_spool_file(path)
    if event["payload_digest"] != expected_digest:
        raise IdempotencyConflict(f"spool entry changed during replay: {path}")
    path.unlink()
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
