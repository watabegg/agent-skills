"""Durable storage primitives for the HLoop report broker.

Daemon lifecycle and CLI wiring intentionally live elsewhere.  This module
provides a caller-held transaction which combines the immutable event log,
Manager inbox projection, wake outbox, leases, and broker owner epochs.
"""

from __future__ import annotations

import fcntl
import hashlib
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
    parse_rfc3339,
    utc_now,
    validate_client_event,
)


BROKER_SCHEMA_VERSION = 1
UNIX_SOCKET_PATH_MAX_BYTES = 103


class BrokerStorageError(RuntimeError):
    """Base error for broker persistence failures."""


class IdempotencyConflict(BrokerStorageError):
    """An existing event id was reused for a different payload digest."""


class LeaseGenerationMismatch(BrokerStorageError):
    """A wake references a stale or otherwise different Manager lease."""


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
        "broker_owners",
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
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

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
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    yield transaction
                    transaction.require_active(self)
                    connection.commit()
                    committed = True
                    callbacks = list(transaction._after_commit)
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    transaction.active = False
                    connection.close()
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
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

    def events(self, transaction: BrokerTransaction) -> list[dict[str, Any]]:
        transaction.require_active(self)
        rows = transaction.connection.execute(
            "SELECT event_json FROM events ORDER BY sequence"
        ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

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
        """Return deliverable wakes and terminalize stale lease generations."""

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
            WHERE c.consumption_id IS NULL
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
                continue
            self._record_wake_terminal(
                transaction,
                event_id=wake["event_id"],
                run_id=wake["run_id"],
                lease_generation=wake["lease_generation"],
                terminal_at=timestamp,
            )
        return pending

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
            self._record_wake_terminal(
                transaction,
                event_id=event_id,
                run_id=row["run_id"],
                lease_generation=lease_generation,
                terminal_at=timestamp,
            )
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
        """Replay a spool batch and remove files only after a successful commit."""

        transaction.require_active(self)
        replayed: list[StoredEvent] = []
        for path, event in iter_spooled_events(spool_directory):
            stored = self.accept_report(transaction, event)
            replayed.append(stored)
            expected_digest = event["payload_digest"]
            transaction.after_commit(
                lambda path=path, digest=expected_digest: _remove_spool_if_unchanged(
                    path, digest
                )
            )
        return replayed

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


def spool_client_event(spool_directory: Path, client_event: Mapping[str, Any]) -> Path:
    """Atomically persist a report when the broker socket is unavailable."""

    event = validate_client_event(client_event)
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
            existing = _read_spool_file(target)
            if existing["payload_digest"] != event["payload_digest"]:
                raise IdempotencyConflict(
                    f"spooled event_id {event['event_id']} has a different digest"
                )
            return target
        sequence = _next_spool_sequence(directory)
        try:
            _atomic_create_json(
                target,
                {"spool_sequence": sequence, "event": event},
            )
        except FileExistsError:
            existing = _read_spool_file(target)
            if existing["payload_digest"] != event["payload_digest"]:
                raise IdempotencyConflict(
                    f"spooled event_id {event['event_id']} has a different digest"
                )
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return target


def _next_spool_sequence(directory: Path) -> int:
    highest = 0
    for path in directory.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            raise BrokerStorageError(f"unsafe spool entry: {path}")
        sequence, event = _read_spool_entry(path)
        if path.stem != event["event_id"]:
            raise BrokerStorageError(
                f"spool filename does not match event_id: {path.name}"
            )
        if sequence is not None:
            highest = max(highest, sequence)
    return highest + 1


def _read_spool_entry(path: Path) -> tuple[int | None, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise BrokerStorageError(f"unsafe spool entry: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, Mapping) and set(value) == {"spool_sequence", "event"}:
            sequence = value["spool_sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise BrokerStorageError(
                    f"invalid spool entry {path}: spool_sequence must be positive"
                )
            return sequence, validate_client_event(value["event"])
        return None, validate_client_event(value)
    except (OSError, json.JSONDecodeError, ReportValidationError) as exc:
        raise BrokerStorageError(f"invalid spool entry {path}: {exc}") from exc


def _read_spool_file(path: Path) -> dict[str, Any]:
    _, event = _read_spool_entry(path)
    return event


def iter_spooled_events(
    spool_directory: Path,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield validated spool entries in durable enqueue order."""

    directory = Path(spool_directory)
    if not directory.exists():
        return
    entries: list[tuple[int | None, Path, dict[str, Any]]] = []
    seen_sequences: dict[int, Path] = {}
    for path in directory.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            raise BrokerStorageError(f"unsafe spool entry: {path}")
        sequence, event = _read_spool_entry(path)
        if path.stem != event["event_id"]:
            raise BrokerStorageError(
                f"spool filename does not match event_id: {path.name}"
            )
        if sequence is not None and sequence in seen_sequences:
            raise BrokerStorageError(
                "duplicate spool_sequence "
                f"{sequence}: {seen_sequences[sequence].name}, {path.name}"
            )
        if sequence is not None:
            seen_sequences[sequence] = path
        entries.append((sequence, path, event))
    entries.sort(
        key=lambda item: (
            item[0] is not None,
            item[0] if item[0] is not None else 0,
            item[1].name,
        )
    )
    for _, path, event in entries:
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
