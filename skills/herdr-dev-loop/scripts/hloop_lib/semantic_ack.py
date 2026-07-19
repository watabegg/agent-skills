"""Fail-closed state inspection for the blocking semantic ACK exchange.

The transport-specific work (report submission and application events) stays
in the CLI.  This module owns the exact identity checks and bounded decision
wait so they can be exercised without a Herdr pane or a broker fixture.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ExchangeIdentity:
    run_id: str
    role_id: str
    attempt_id: str
    task_contract_digest: str
    message_id: str
    ack_event_id: str
    ack_sequence: int

    def __post_init__(self) -> None:
        for field in ("run_id", "role_id", "attempt_id", "message_id", "ack_event_id"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} must not be empty")
        if not _DIGEST_RE.fullmatch(self.task_contract_digest):
            raise ValueError("task_contract_digest must be a 64-character SHA-256 digest")
        if isinstance(self.ack_sequence, bool) or self.ack_sequence <= 0:
            raise ValueError("ack_sequence must be a positive broker sequence")


@dataclass(frozen=True)
class ExchangeApproval:
    decision: dict[str, Any]
    availability: dict[str, Any]
    completion_mode: str
    completion_mode_probe: dict[str, Any]


@dataclass(frozen=True)
class ApplicationIdentity:
    event_id: str
    event_sequence: int
    payload_digest: str

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if isinstance(self.event_sequence, bool) or self.event_sequence <= 0:
            raise ValueError("event_sequence must be a positive broker sequence")
        if not _DIGEST_RE.fullmatch(self.payload_digest):
            raise ValueError("payload_digest must be a 64-character SHA-256 digest")


class ExchangeFailure(RuntimeError):
    """A structured terminal or fail-closed exchange outcome."""

    def __init__(
        self,
        code: str,
        status: str,
        reason: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.status = status
        self.reason = reason
        self.retryable = retryable

    def as_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "reason": self.reason,
            "material_work_authorized": False,
            "retryable": self.retryable,
        }


def _fail(code: str, reason: str, *, status: str = "blocked") -> None:
    raise ExchangeFailure(code, status, reason)


def inspect_exchange_snapshot(
    *,
    observed_run_id: str,
    observed_role_id: str,
    active_attempt_id: str,
    active_contract_digest: str,
    agent_state: Mapping[str, Any],
    identity: ExchangeIdentity,
) -> ExchangeApproval | None:
    """Return an exact approval, ``None`` while pending, or fail closed."""

    observed = {
        "run_id": observed_run_id,
        "role_id": observed_role_id,
        "attempt_id": active_attempt_id,
        "task_contract_digest": active_contract_digest,
    }
    expected = {
        "run_id": identity.run_id,
        "role_id": identity.role_id,
        "attempt_id": identity.attempt_id,
        "task_contract_digest": identity.task_contract_digest,
    }
    for field, expected_value in expected.items():
        if observed[field] != expected_value:
            _fail(
                f"{field}-mismatch",
                f"semantic ACK exchange {field} mismatch: "
                f"expected={expected_value} observed={observed[field]}",
            )

    barrier = agent_state.get("semantic_ack_barrier")
    if not isinstance(barrier, Mapping):
        _fail("missing-barrier", "semantic ACK exchange has no active barrier")
    if str(barrier.get("message_id") or "") != identity.message_id:
        _fail(
            "superseded",
            "semantic ACK exchange barrier message was superseded",
            status="superseded",
        )
    if str(barrier.get("digest") or "") != identity.task_contract_digest:
        _fail(
            "barrier-digest-mismatch",
            "semantic ACK exchange barrier digest does not match the submitted ACK",
        )

    decision = barrier.get("semantic_decision")
    if not isinstance(decision, Mapping):
        _fail("missing-decision", "semantic ACK exchange decision state is missing")
    decision_status = str(decision.get("status") or "")
    if decision_status == "awaiting_ack":
        return None
    if decision_status == "rejected":
        if identity.ack_sequence > int(
            barrier.get("required_reack_after_sequence") or 0
        ):
            return None
        raise ExchangeFailure(
            "rejected",
            "rejected",
            str(decision.get("reason") or "Manager rejected the semantic ACK"),
        )
    if decision_status == "timed_out":
        if identity.ack_sequence > int(
            barrier.get("required_reack_after_sequence") or 0
        ):
            return None
        raise ExchangeFailure(
            "manager-timeout",
            "timed_out",
            str(decision.get("reason") or "Manager timed out the semantic ACK"),
            retryable=True,
        )
    if decision_status == "superseded":
        raise ExchangeFailure(
            "superseded",
            "superseded",
            "semantic ACK exchange decision was superseded",
        )
    if decision_status != "approved":
        _fail(
            "invalid-decision-status",
            f"semantic ACK exchange decision status is invalid: {decision_status or 'missing'}",
        )
    if str(decision.get("ack_event_id") or "") != identity.ack_event_id:
        _fail(
            "ack-event-mismatch",
            "semantic ACK exchange decision does not bind the submitted ACK event",
        )

    availability = barrier.get("approval_availability")
    if not isinstance(availability, Mapping):
        _fail(
            "missing-availability",
            "semantic ACK exchange approval availability is missing",
        )
    if str(availability.get("status") or "") != "available":
        _fail(
            "approval-unavailable",
            "semantic ACK exchange approval is not available",
        )
    availability_identity = {
        "message_id": str(availability.get("message_id") or ""),
        "task_contract_digest": str(
            availability.get("task_contract_digest") or ""
        ),
        "ack_event_id": str(availability.get("ack_event_id") or ""),
    }
    expected_availability = {
        "message_id": identity.message_id,
        "task_contract_digest": identity.task_contract_digest,
        "ack_event_id": identity.ack_event_id,
    }
    if availability_identity != expected_availability:
        _fail(
            "availability-identity-mismatch",
            "semantic ACK exchange approval availability identity is inconsistent",
        )

    completion_mode_probe = agent_state.get("completion_mode_probe") or {}
    if not isinstance(completion_mode_probe, Mapping):
        _fail(
            "completion-probe-invalid",
            "semantic ACK exchange completion-mode probe is invalid",
        )
    return ExchangeApproval(
        decision=dict(decision),
        availability=dict(availability),
        completion_mode=str(agent_state.get("completion_mode") or ""),
        completion_mode_probe=dict(completion_mode_probe),
    )


def wait_for_approval(
    load_snapshot: Callable[[], tuple[str, str, str, str, Mapping[str, Any]]],
    identity: ExchangeIdentity,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ExchangeApproval:
    """Boundedly poll durable state without holding a Manager repository lock."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    deadline = monotonic() + timeout_seconds
    while True:
        run_id, role_id, attempt_id, digest, agent_state = load_snapshot()
        approval = inspect_exchange_snapshot(
            observed_run_id=run_id,
            observed_role_id=role_id,
            active_attempt_id=attempt_id,
            active_contract_digest=digest,
            agent_state=agent_state,
            identity=identity,
        )
        if approval is not None:
            return approval
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ExchangeFailure(
                "wait-timeout",
                "timed_out",
                "semantic ACK exchange timed out waiting for a Manager decision",
                retryable=True,
            )
        sleep(min(poll_interval_seconds, remaining))


def inspect_application_snapshot(
    *,
    observed_run_id: str,
    observed_role_id: str,
    active_attempt_id: str,
    active_contract_digest: str,
    agent_state: Mapping[str, Any],
    identity: ExchangeIdentity,
    application_identity: ApplicationIdentity,
) -> dict[str, Any] | None:
    """Return the exact Manager-applied binding, or ``None`` while pending."""

    inspect_exchange_snapshot(
        observed_run_id=observed_run_id,
        observed_role_id=observed_role_id,
        active_attempt_id=active_attempt_id,
        active_contract_digest=active_contract_digest,
        agent_state=agent_state,
        identity=identity,
    )
    barrier = agent_state.get("semantic_ack_barrier")
    application = (
        barrier.get("approval_application")
        if isinstance(barrier, Mapping)
        else None
    )
    if not isinstance(application, Mapping):
        _fail(
            "missing-application",
            "semantic ACK exchange approval application state is missing",
        )
    status = str(application.get("status") or "")
    if status in {
        "pending",
        "delivered",
        "acknowledged",
        "undelivered",
        "unknown",
    }:
        return None
    if status == "superseded":
        raise ExchangeFailure(
            "superseded",
            "superseded",
            "semantic ACK exchange approval application was superseded",
        )
    if status != "applied":
        _fail(
            "invalid-application-status",
            f"semantic ACK exchange application status is invalid: {status or 'missing'}",
        )
    observed = {
        "ack_event_id": str(application.get("ack_event_id") or ""),
        "application_event_id": str(application.get("application_event_id") or ""),
        "application_event_digest": str(
            application.get("application_event_digest") or ""
        ),
        "application_attempt_id": str(
            application.get("application_attempt_id") or ""
        ),
        "application_task_contract_digest": str(
            application.get("application_task_contract_digest") or ""
        ),
    }
    expected = {
        "ack_event_id": identity.ack_event_id,
        "application_event_id": application_identity.event_id,
        "application_event_digest": application_identity.payload_digest,
        "application_attempt_id": identity.attempt_id,
        "application_task_contract_digest": identity.task_contract_digest,
    }
    if observed != expected:
        _fail(
            "application-identity-mismatch",
            "semantic ACK exchange Manager-applied application identity is inconsistent",
        )
    return dict(application)


def wait_for_application(
    load_snapshot: Callable[[], tuple[str, str, str, str, Mapping[str, Any]]],
    identity: ExchangeIdentity,
    application_identity: ApplicationIdentity,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Boundedly wait for the Manager-owned exact application binding."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    deadline = monotonic() + timeout_seconds
    while True:
        run_id, role_id, attempt_id, digest, agent_state = load_snapshot()
        application = inspect_application_snapshot(
            observed_run_id=run_id,
            observed_role_id=role_id,
            active_attempt_id=attempt_id,
            active_contract_digest=digest,
            agent_state=agent_state,
            identity=identity,
            application_identity=application_identity,
        )
        if application is not None:
            return application
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ExchangeFailure(
                "application-wait-timeout",
                "timed_out",
                "semantic ACK exchange timed out waiting for Manager application",
                retryable=True,
            )
        sleep(min(poll_interval_seconds, remaining))
