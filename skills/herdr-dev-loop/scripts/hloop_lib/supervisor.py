"""Foreground broker/Manager sleep supervision primitives for HLoop 0.5.

The supervisor intentionally stays in the foreground.  It owns one private Unix
socket under a non-blocking file lock, registers a broker owner epoch, drains the
durable report spool, installs a Manager wake lease under the broker transaction,
and waits for either a report signal, a Herdr blocking-wait fallback, or timeout.
Every exit path invalidates its lease and removes only socket/metadata paths that
still belong to the acquired owner.
"""

from __future__ import annotations

import fcntl
import os
import select
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import broker


WAKE_SIGNAL = b"HLOOP_WAKE\n"
WAKE_REPORT_TYPES = frozenset({"ack", "attention", "completion"})
FALLBACK_STATUSES = frozenset({"idle", "blocked", "done", "unknown"})
DEFAULT_WAKE_BURST_WINDOW_SECONDS = 0.2
DEFAULT_WAKE_BURST_POLL_SECONDS = 0.02


class SupervisorError(RuntimeError):
    """Base error for ownership or foreground wait failures."""


class DuplicateOwnerError(SupervisorError):
    """Raised when another process owns the run supervisor."""


class UnsafeSocketError(SupervisorError):
    """Raised rather than unlinking a non-socket or replaced socket path."""


@dataclass(frozen=True)
class FallbackWatch:
    pane_id: str
    status: str

    def __post_init__(self) -> None:
        _single_line(self.pane_id, "pane_id")
        if self.status not in FALLBACK_STATUSES:
            raise SupervisorError(
                "fallback status must be one of: "
                + ", ".join(sorted(FALLBACK_STATUSES))
            )


@dataclass(frozen=True)
class FallbackSignal:
    pane_id: str
    status: str
    returncode: int


@dataclass(frozen=True)
class ManagerSleepResult:
    reason: str
    lease_generation: int | None
    event_ids: tuple[str, ...] = ()
    fallback: FallbackSignal | None = None
    drained_reports: int = 0


@dataclass
class _FallbackProcess:
    watch: FallbackWatch
    process: Any


class HerdrFallbackWaiter:
    """Run Herdr's blocking agent-status waits without pane-output polling."""

    def __init__(
        self,
        watches: Iterable[FallbackWatch],
        *,
        timeout_ms: int,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
            raise SupervisorError("fallback timeout_ms must be an integer")
        if timeout_ms < 1:
            raise SupervisorError("fallback timeout_ms must be positive")
        self._processes: list[_FallbackProcess] = []
        try:
            for watch in watches:
                argv = herdr_wait_argv(watch, timeout_ms=timeout_ms)
                process = popen_factory(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._processes.append(_FallbackProcess(watch, process))
        except BaseException:
            self.close()
            raise

    def poll(self) -> FallbackSignal | None:
        for item in self._processes:
            returncode = item.process.poll()
            if returncode == 0:
                return FallbackSignal(
                    pane_id=item.watch.pane_id,
                    status=item.watch.status,
                    returncode=returncode,
                )
        return None

    def close(self) -> None:
        for item in self._processes:
            if item.process.poll() is not None:
                continue
            item.process.terminate()
            try:
                item.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                item.process.kill()
                item.process.wait(timeout=0.5)


def herdr_wait_argv(watch: FallbackWatch, *, timeout_ms: int) -> tuple[str, ...]:
    return (
        "herdr",
        "wait",
        "agent-status",
        watch.pane_id,
        "--status",
        watch.status,
        "--timeout",
        str(timeout_ms),
    )


class ManagerSleepSupervisor:
    """Own one run socket and perform a bounded foreground Manager sleep."""

    def __init__(
        self,
        store: broker.BrokerStore,
        *,
        namespace: str,
        run_id: str,
        runtime_version: str,
        manager_session_id: str,
        pane_id: str,
        socket_path: Path | None = None,
        owner_metadata_path: Path | None = None,
        owner_lock_path: Path | None = None,
        spool_directory: Path | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.namespace = _single_line(namespace, "namespace")
        self.run_id = _single_line(run_id, "run_id")
        self.runtime_version = _single_line(runtime_version, "runtime_version")
        self.manager_session_id = _single_line(
            manager_session_id, "manager_session_id"
        )
        self.pane_id = _single_line(pane_id, "pane_id")
        self.socket_path = _absolute_path(
            socket_path
            or broker.derive_runtime_socket_path(self.namespace, self.run_id),
            "socket_path",
        )
        self.owner_metadata_path = _absolute_path(
            owner_metadata_path or (self.store.root / "owner.json"),
            "owner_metadata_path",
        )
        self.owner_lock_path = _absolute_path(
            owner_lock_path or (self.store.root / "supervisor.lock"),
            "owner_lock_path",
        )
        self.spool_directory = _absolute_path(
            spool_directory or (self.store.root / "spool"),
            "spool_directory",
        )
        self._popen_factory = popen_factory
        self._monotonic = monotonic
        self._lock_file: Any | None = None
        self._listener: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._owner_metadata: dict[str, Any] | None = None

    @property
    def acquired(self) -> bool:
        return self._listener is not None

    def acquire(self) -> dict[str, Any]:
        if self.acquired:
            raise SupervisorError("supervisor is already acquired")
        _private_directory(self.owner_lock_path.parent)
        descriptor = os.open(
            self.owner_lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600
        )
        lock_file = os.fdopen(descriptor, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise DuplicateOwnerError(
                f"run supervisor is already owned: {self.run_id}"
            ) from exc
        self._lock_file = lock_file

        try:
            self._prepare_socket_path()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(self.socket_path))
                os.chmod(self.socket_path, 0o600)
                listener.listen(16)
                listener.setblocking(False)
            except BaseException:
                listener.close()
                raise
            socket_stat = os.lstat(self.socket_path)
            self._listener = listener
            self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)

            with self.store.transaction() as transaction:
                metadata = self.store.register_owner(
                    transaction,
                    namespace=self.namespace,
                    run_id=self.run_id,
                    runtime_version=self.runtime_version,
                    socket_path=self.socket_path,
                )
            broker.write_owner_metadata(self.owner_metadata_path, metadata)
            self._owner_metadata = metadata
            return dict(metadata)
        except BaseException:
            try:
                self.release()
            except SupervisorError:
                pass
            raise

    def release(self) -> None:
        errors: list[BaseException] = []
        listener, self._listener = self._listener, None
        try:
            if listener is not None:
                listener.close()
            self._unlink_owned_socket()
            self._unlink_owned_metadata()
        except BaseException as exc:
            errors.append(exc)
        finally:
            self._socket_identity = None
            self._owner_metadata = None
            lock_file, self._lock_file = self._lock_file, None
            if lock_file is not None:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    try:
                        lock_file.close()
                    except BaseException as exc:
                        errors.append(exc)
        if errors:
            raise SupervisorError(f"supervisor cleanup failed: {errors[0]}") from errors[0]

    def __enter__(self) -> "ManagerSleepSupervisor":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()

    def sleep(
        self,
        *,
        timeout_seconds: float,
        fallback_watches: Iterable[FallbackWatch] = (),
        seen_event_ids: Iterable[str] = (),
        poll_interval_seconds: float = 0.05,
        wake_burst_window_seconds: float = DEFAULT_WAKE_BURST_WINDOW_SECONDS,
        wake_burst_poll_seconds: float = DEFAULT_WAKE_BURST_POLL_SECONDS,
    ) -> ManagerSleepResult:
        """Register a lease and block until report, fallback, or timeout.

        The lease and first inbox recheck occur in one broker transaction.  This
        closes the report-arrives-before-sleep lost-wake window.  Report socket
        bytes are only a wake signal; durable spool/inbox records remain the
        source of truth and no role output is interpreted as a Manager prompt.

        Once any wake-eligible (``ack``/``attention``/``completion``) report is
        seen, the return is held open for a bounded ``wake_burst_window_seconds``
        so a cross-role burst that lands moments later is delivered as one
        batch instead of triggering a separate Manager wake per event. Every
        event that arrives is still preserved in the durable inbox either way;
        this window only changes how many are handed back together.
        """

        if timeout_seconds <= 0:
            raise SupervisorError("timeout_seconds must be positive")
        if poll_interval_seconds <= 0:
            raise SupervisorError("poll_interval_seconds must be positive")
        if wake_burst_window_seconds < 0:
            raise SupervisorError("wake_burst_window_seconds must not be negative")
        if wake_burst_poll_seconds <= 0:
            raise SupervisorError("wake_burst_poll_seconds must be positive")
        seen = {_single_line(item, "seen_event_id") for item in seen_event_ids}
        watches = tuple(fallback_watches)
        acquired_for_call = not self.acquired
        if acquired_for_call:
            self.acquire()

        lease: dict[str, Any] | None = None
        lease_cancelled = False
        waiter: HerdrFallbackWaiter | None = None
        drained_total = 0
        started = self._monotonic()
        deadline = started + timeout_seconds
        try:
            created_at, expires_at = _lease_times(timeout_seconds)
            with self.store.transaction() as transaction:
                drained = self.store.replay_spool(transaction, self.spool_directory)
                drained_total += len(drained)
                lease = self.store.register_wake_lease(
                    transaction,
                    run_id=self.run_id,
                    manager_session_id=self.manager_session_id,
                    pane_id=self.pane_id,
                    expires_at=expires_at,
                    created_at=created_at,
                )
                unread = _unseen_inbox(
                    self.store.unconsumed_inbox(transaction, run_id=self.run_id),
                    seen,
                    self.run_id,
                )
            if unread:
                unread, burst_drained = self._settle_wake_burst(
                    seen,
                    unread,
                    deadline=deadline,
                    burst_window_seconds=wake_burst_window_seconds,
                    burst_poll_seconds=wake_burst_poll_seconds,
                )
                drained_total += burst_drained
                with self.store.transaction() as transaction:
                    self.store.enqueue_unacknowledged_for_lease(
                        transaction,
                        run_id=self.run_id,
                        lease_generation=lease["generation"],
                        manager_session_id=self.manager_session_id,
                        pane_id=self.pane_id,
                        report_types=WAKE_REPORT_TYPES,
                        created_at=created_at,
                    )
                    self._cancel_lease_in_transaction(transaction, created_at)
                lease_cancelled = True
                return ManagerSleepResult(
                    reason="report",
                    lease_generation=lease["generation"],
                    event_ids=tuple(item["event_id"] for item in unread),
                    drained_reports=drained_total,
                )

            remaining_ms = max(1, int((deadline - self._monotonic()) * 1000))
            waiter = HerdrFallbackWaiter(
                watches,
                timeout_ms=remaining_ms,
                popen_factory=self._popen_factory,
            )
            while True:
                unread, drained = self._drain_and_unread(seen)
                drained_total += drained
                if unread:
                    unread, burst_drained = self._settle_wake_burst(
                        seen,
                        unread,
                        deadline=deadline,
                        burst_window_seconds=wake_burst_window_seconds,
                        burst_poll_seconds=wake_burst_poll_seconds,
                    )
                    drained_total += burst_drained
                    return ManagerSleepResult(
                        reason="report",
                        lease_generation=lease["generation"],
                        event_ids=tuple(item["event_id"] for item in unread),
                        drained_reports=drained_total,
                    )

                fallback = waiter.poll()
                if fallback is not None:
                    return ManagerSleepResult(
                        reason="herdr-fallback",
                        lease_generation=lease["generation"],
                        fallback=fallback,
                        drained_reports=drained_total,
                    )

                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return ManagerSleepResult(
                        reason="timeout",
                        lease_generation=lease["generation"],
                        drained_reports=drained_total,
                    )
                listener = self._listener
                if listener is None:
                    raise SupervisorError("supervisor socket was released while sleeping")
                readable, _, _ = select.select(
                    [listener], [], [], min(poll_interval_seconds, remaining)
                )
                if readable:
                    self._drain_socket_signals()
        finally:
            try:
                if waiter is not None:
                    waiter.close()
            finally:
                try:
                    if lease is not None and not lease_cancelled:
                        self._cancel_lease_if_current(lease["generation"])
                finally:
                    if acquired_for_call:
                        self.release()

    def _drain_and_unread(
        self, seen: set[str]
    ) -> tuple[list[dict[str, Any]], int]:
        with self.store.transaction() as transaction:
            drained = self.store.replay_spool(transaction, self.spool_directory)
            unread = _unseen_inbox(
                self.store.unconsumed_inbox(transaction, run_id=self.run_id),
                seen,
                self.run_id,
            )
            return unread, len(drained)

    def _settle_wake_burst(
        self,
        seen: set[str],
        initial_unread: list[dict[str, Any]],
        *,
        deadline: float,
        burst_window_seconds: float,
        burst_poll_seconds: float,
    ) -> tuple[list[dict[str, Any]], int]:
        """Hold a detected wake open briefly to batch a cross-role burst.

        Every unconsumed wake-eligible event is already durable before this
        runs; this only widens what one ``ManagerSleepResult`` reports back so
        near-simultaneous reports from other roles do not each force their own
        Manager wake.
        """

        if burst_window_seconds <= 0:
            return initial_unread, 0
        burst_deadline = min(self._monotonic() + burst_window_seconds, deadline)
        unread = initial_unread
        drained_total = 0
        while self._monotonic() < burst_deadline:
            remaining = burst_deadline - self._monotonic()
            time.sleep(min(burst_poll_seconds, max(remaining, 0.0)))
            unread, drained = self._drain_and_unread(seen)
            drained_total += drained
        return unread, drained_total

    def _cancel_lease_in_transaction(
        self, transaction: broker.BrokerTransaction, timestamp: str
    ) -> None:
        self.store.register_wake_lease(
            transaction,
            run_id=self.run_id,
            manager_session_id=self.manager_session_id,
            pane_id=self.pane_id,
            expires_at=timestamp,
            created_at=timestamp,
        )

    def _cancel_lease_if_current(self, generation: int) -> None:
        timestamp = _utc_now()
        with self.store.transaction() as transaction:
            row = transaction.connection.execute(
                """
                SELECT generation, manager_session_id, pane_id
                FROM wake_leases WHERE run_id = ?
                ORDER BY generation DESC LIMIT 1
                """,
                (self.run_id,),
            ).fetchone()
            if (
                row is not None
                and row["generation"] == generation
                and row["manager_session_id"] == self.manager_session_id
                and row["pane_id"] == self.pane_id
            ):
                self._cancel_lease_in_transaction(transaction, timestamp)

    def _prepare_socket_path(self) -> None:
        _private_directory(self.socket_path.parent)
        if not os.path.lexists(self.socket_path):
            return
        current = os.lstat(self.socket_path)
        if not stat.S_ISSOCK(current.st_mode):
            raise UnsafeSocketError(
                f"refusing to replace non-socket path: {self.socket_path}"
            )
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            probe.connect(str(self.socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        except OSError as exc:
            raise UnsafeSocketError(
                f"cannot verify existing Unix socket {self.socket_path}: {exc}"
            ) from exc
        else:
            raise DuplicateOwnerError(
                f"Unix socket already has a live owner: {self.socket_path}"
            )
        finally:
            probe.close()
        latest = os.lstat(self.socket_path)
        if (latest.st_dev, latest.st_ino) != (current.st_dev, current.st_ino):
            raise UnsafeSocketError(
                f"Unix socket changed during stale-owner check: {self.socket_path}"
            )
        os.unlink(self.socket_path)

    def _drain_socket_signals(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while True:
            try:
                connection, _ = listener.accept()
            except BlockingIOError:
                return
            with connection:
                connection.settimeout(0.1)
                try:
                    connection.recv(4096)
                except (OSError, TimeoutError):
                    pass

    def _unlink_owned_socket(self) -> None:
        identity = self._socket_identity
        if identity is None or not os.path.lexists(self.socket_path):
            return
        current = os.lstat(self.socket_path)
        if (
            not stat.S_ISSOCK(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            return
        os.unlink(self.socket_path)

    def _unlink_owned_metadata(self) -> None:
        expected = self._owner_metadata
        if expected is None or not os.path.lexists(self.owner_metadata_path):
            return
        current = os.lstat(self.owner_metadata_path)
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            return
        try:
            actual = broker.read_owner_metadata(self.owner_metadata_path)
        except broker.BrokerStorageError:
            return
        if actual == expected:
            os.unlink(self.owner_metadata_path)


def signal_supervisor(socket_path: Path) -> None:
    """Send a fixed wake byte sequence; durable event content travels elsewhere."""

    path = Path(socket_path)
    if not path.is_absolute():
        raise SupervisorError("socket_path must be absolute")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(str(path))
        client.sendall(WAKE_SIGNAL)
    finally:
        client.close()


def _unseen_inbox(
    inbox: list[dict[str, Any]], seen: set[str], run_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in inbox
        if item.get("run_id") == run_id
        and item.get("event_id") not in seen
        and item.get("report_type") in WAKE_REPORT_TYPES
    ]


def _private_directory(path: Path) -> None:
    """Create/repair one private directory without following its final link."""

    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise UnsafeSocketError(
            f"cannot create private directory {path}: {exc}"
        ) from exc

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UnsafeSocketError(
            f"private directory must be a real directory, not a symlink: {path}: {exc}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeSocketError(f"private path is not a directory: {path}")
        expected_uid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
        if metadata.st_uid != expected_uid:
            raise UnsafeSocketError(
                f"private directory is not owned by the current user: {path}"
            )
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            try:
                os.fchmod(descriptor, 0o700)
            except OSError as exc:
                raise UnsafeSocketError(
                    f"cannot secure private directory {path}: {exc}"
                ) from exc
            metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise UnsafeSocketError(
                f"private directory must have mode 0700: {path}"
            )

        # Compare the opened directory with the pathname after securing it.
        # This detects replacement even on platforms without O_NOFOLLOW.
        try:
            path_metadata = os.lstat(path)
        except OSError as exc:
            raise UnsafeSocketError(
                f"cannot verify private directory {path}: {exc}"
            ) from exc
        if (
            stat.S_ISLNK(path_metadata.st_mode)
            or not stat.S_ISDIR(path_metadata.st_mode)
            or (path_metadata.st_dev, path_metadata.st_ino)
            != (metadata.st_dev, metadata.st_ino)
        ):
            raise UnsafeSocketError(
                f"private directory changed or is a symlink: {path}"
            )
    finally:
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_times(seconds: float) -> tuple[str, str]:
    created = datetime.now(timezone.utc)
    return created.isoformat(), (created + timedelta(seconds=seconds)).isoformat()


def _single_line(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise SupervisorError(f"{field} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise SupervisorError(f"{field} must be a single-line string")
    return value


def _absolute_path(value: Path, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SupervisorError(f"{field} must be absolute")
    return path
