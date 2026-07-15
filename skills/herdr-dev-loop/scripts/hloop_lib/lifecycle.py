"""Pure lifecycle invariants for HLoop 0.5 state transitions.

This module deliberately performs no Git, filesystem, clock, or process I/O.
The CLI supplies observations and timestamps, then decides whether and how to
persist the returned records.  Stable issue codes let status, dashboard,
conductor, and mutating commands share the same conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence


class LifecycleInvariantError(ValueError):
    """Raised when a requested lifecycle transition violates an invariant."""

    def __init__(self, issues: Sequence["LifecycleIssue"]):
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


@dataclass(frozen=True, slots=True)
class LifecycleIssue:
    """A stable machine-readable lifecycle diagnostic."""

    code: str
    message: str
    subject: str = ""
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """A non-throwing validation result suitable for read surfaces."""

    issues: tuple[LifecycleIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def raise_for_errors(self) -> None:
        if self.issues:
            raise LifecycleInvariantError(self.issues)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    """Immutable identity captured when one role attempt starts."""

    run_id: str
    role_id: str
    attempt_id: str
    base_sha: str
    branch: str
    worktree: str
    task_contract_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "role_id",
            "attempt_id",
            "base_sha",
            "branch",
            "worktree",
            "task_contract_digest",
        ):
            _required_text(getattr(self, field_name), field_name)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "AttemptIdentity":
        """Build identity from new or legacy-compatible state field names."""

        base_sha = str(record.get("base_sha") or "")
        worker_base_sha = str(record.get("worker_base_sha") or "")
        if base_sha and worker_base_sha and base_sha != worker_base_sha:
            raise ValueError("base_sha and worker_base_sha disagree")
        return cls(
            run_id=_required_text(record.get("run_id"), "run_id"),
            role_id=_required_text(
                record.get("role_id") or record.get("agent_id") or record.get("task_id"),
                "role_id",
            ),
            attempt_id=_required_text(record.get("attempt_id"), "attempt_id"),
            base_sha=_required_text(worker_base_sha or base_sha, "base_sha"),
            branch=_required_text(record.get("branch"), "branch"),
            worktree=_required_text(record.get("worktree"), "worktree"),
            task_contract_digest=_required_text(
                record.get("task_contract_digest"), "task_contract_digest"
            ),
        )

    def to_record(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "role_id": self.role_id,
            "attempt_id": self.attempt_id,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "worktree": self.worktree,
            "task_contract_digest": self.task_contract_digest,
        }


def _attempt(value: AttemptIdentity | Mapping[str, Any]) -> AttemptIdentity:
    return value if isinstance(value, AttemptIdentity) else AttemptIdentity.from_record(value)


def validate_append_only_attempts(
    recorded: Sequence[AttemptIdentity | Mapping[str, Any]],
    proposed: Sequence[AttemptIdentity | Mapping[str, Any]],
) -> ValidationResult:
    """Require ``recorded`` to be an exact prefix of ``proposed``.

    Existing attempts may not be edited, reordered, or deleted.  New attempts
    may only be appended and their ``(run, role, attempt)`` key must be unique.
    """

    issues: list[LifecycleIssue] = []
    try:
        old = tuple(_attempt(item) for item in recorded)
        new = tuple(_attempt(item) for item in proposed)
    except (TypeError, ValueError) as exc:
        return ValidationResult(
            (LifecycleIssue("attempt-record-invalid", str(exc), "attempt-history"),)
        )
    if len(new) < len(old):
        issues.append(
            LifecycleIssue(
                "attempt-history-truncated",
                "append-only attempt history cannot delete recorded attempts",
                "attempt-history",
            )
        )
    for index, existing in enumerate(old[: len(new)]):
        if existing != new[index]:
            issues.append(
                LifecycleIssue(
                    "attempt-history-rewritten",
                    f"attempt identity at index {index} was changed or reordered",
                    existing.attempt_id,
                )
            )
    seen: set[tuple[str, str, str]] = set()
    for identity in new:
        key = (identity.run_id, identity.role_id, identity.attempt_id)
        if key in seen:
            issues.append(
                LifecycleIssue(
                    "attempt-id-duplicated",
                    f"attempt identity is duplicated: {identity.attempt_id}",
                    identity.attempt_id,
                )
            )
        seen.add(key)
    return ValidationResult(tuple(issues))


def validate_attempt_copies(
    manager_record: AttemptIdentity | Mapping[str, Any],
    role_record: AttemptIdentity | Mapping[str, Any],
) -> ValidationResult:
    """Detect Manager/role divergence without choosing either copy as winner."""

    try:
        manager = _attempt(manager_record)
        role = _attempt(role_record)
    except (TypeError, ValueError) as exc:
        return ValidationResult(
            (LifecycleIssue("attempt-record-invalid", str(exc), "active-attempt"),)
        )
    if manager == role:
        return ValidationResult()
    return ValidationResult(
        (
            LifecycleIssue(
                "attempt-state-diverged",
                "Manager and role worktree attempt identities differ; fresh requeue "
                "or a dedicated resume transaction is required",
                manager.attempt_id,
            ),
        )
    )


MERGE_ACTIVE = "active"
MERGE_CONTENT_CONFLICT = "content-conflict"
MERGE_ENVIRONMENT_FAILURE = "environment-failure"
MERGE_ABORTED = "aborted"
MERGE_COMPLETED = "completed"

_MERGE_TRANSITIONS = {
    MERGE_ACTIVE: {
        MERGE_ACTIVE,
        MERGE_CONTENT_CONFLICT,
        MERGE_ENVIRONMENT_FAILURE,
        MERGE_ABORTED,
        MERGE_COMPLETED,
    },
    MERGE_CONTENT_CONFLICT: {
        MERGE_CONTENT_CONFLICT,
        MERGE_ABORTED,
        MERGE_COMPLETED,
    },
    MERGE_ENVIRONMENT_FAILURE: {
        MERGE_ENVIRONMENT_FAILURE,
        MERGE_ABORTED,
    },
    MERGE_ABORTED: {MERGE_ABORTED, MERGE_ACTIVE},
    MERGE_COMPLETED: {MERGE_COMPLETED},
}

_MERGE_OPERATIONS = {
    MERGE_ACTIVE: ("watch", "abort"),
    MERGE_CONTENT_CONFLICT: ("watch", "continue", "abort", "retry"),
    MERGE_ENVIRONMENT_FAILURE: ("watch", "abort", "retry"),
    MERGE_ABORTED: ("watch", "retry"),
    MERGE_COMPLETED: ("watch",),
}


@dataclass(frozen=True, slots=True)
class MergeTransaction:
    """Merge identity plus append-only cherry-pick progress observations."""

    transaction_id: str
    task_id: str
    attempt_id: str
    branch: str
    pre_merge_head: str
    worker_head: str
    result_head: str
    index_state: str
    changed_paths: tuple[str, ...]
    status: str = MERGE_ACTIVE
    mode: str = "squash"
    source_commits: tuple[str, ...] = ()
    applied_commits: tuple[str, ...] = ()
    applied_head: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "transaction_id",
            "task_id",
            "attempt_id",
            "branch",
            "pre_merge_head",
            "worker_head",
            "result_head",
            "index_state",
        ):
            _required_text(getattr(self, field_name), field_name)
        if self.status not in _MERGE_TRANSITIONS:
            raise ValueError(f"unknown merge transaction status: {self.status}")
        if self.mode not in {"squash", "cherry-pick"}:
            raise ValueError(f"unknown merge transaction mode: {self.mode}")
        if any(not isinstance(path, str) or not path.strip() for path in self.changed_paths):
            raise ValueError("changed_paths must not contain empty paths")
        if len(set(self.changed_paths)) != len(self.changed_paths):
            raise ValueError("changed_paths must be unique")
        if any(not isinstance(commit, str) or not commit.strip() for commit in self.source_commits):
            raise ValueError("source_commits must not contain empty commits")
        if len(set(self.source_commits)) != len(self.source_commits):
            raise ValueError("source_commits must be unique")
        if any(
            not isinstance(commit, str) or not commit.strip()
            for commit in self.applied_commits
        ):
            raise ValueError("applied_commits must not contain empty commits")
        if self.mode == "squash" and (self.source_commits or self.applied_commits):
            raise ValueError("squash transactions cannot record cherry-pick commits")
        if self.mode == "cherry-pick" and not self.source_commits:
            raise ValueError("cherry-pick transactions require source_commits")
        if self.applied_commits != self.source_commits[: len(self.applied_commits)]:
            raise ValueError("applied_commits must be a prefix of source_commits")
        if self.status == MERGE_COMPLETED and self.mode == "cherry-pick":
            if self.applied_commits != self.source_commits:
                raise ValueError("completed cherry-pick must record the full applied prefix")
        object.__setattr__(self, "changed_paths", tuple(sorted(self.changed_paths)))
        object.__setattr__(self, "applied_head", self.applied_head or self.pre_merge_head)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "MergeTransaction":
        paths = record.get("changed_paths") or ()
        if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
            raise ValueError("changed_paths must be a sequence")
        source_commits = record.get("source_commits") or ()
        if isinstance(source_commits, (str, bytes)) or not isinstance(source_commits, Sequence):
            raise ValueError("source_commits must be a sequence")
        applied_commits = record.get("applied_commits") or ()
        if isinstance(applied_commits, (str, bytes)) or not isinstance(applied_commits, Sequence):
            raise ValueError("applied_commits must be a sequence")
        return cls(
            transaction_id=_required_text(record.get("transaction_id"), "transaction_id"),
            task_id=_required_text(record.get("task_id"), "task_id"),
            attempt_id=_required_text(record.get("attempt_id"), "attempt_id"),
            branch=_required_text(record.get("branch"), "branch"),
            pre_merge_head=_required_text(record.get("pre_merge_head"), "pre_merge_head"),
            worker_head=_required_text(record.get("worker_head"), "worker_head"),
            result_head=_required_text(record.get("result_head"), "result_head"),
            index_state=_required_text(
                record.get("index_state") or record.get("index_state_digest"), "index_state"
            ),
            changed_paths=tuple(sorted(str(path) for path in paths)),
            status=str(record.get("status") or MERGE_ACTIVE),
            mode=str(record.get("mode") or "squash"),
            source_commits=tuple(str(commit) for commit in source_commits),
            applied_commits=tuple(str(commit) for commit in applied_commits),
            applied_head=str(
                record.get("applied_head") or record.get("pre_merge_head") or ""
            ),
        )

    def immutable_identity(self) -> tuple[Any, ...]:
        return (
            self.transaction_id,
            self.task_id,
            self.attempt_id,
            self.branch,
            self.pre_merge_head,
            self.worker_head,
            self.result_head,
            self.index_state,
            self.changed_paths,
            self.mode,
            self.source_commits,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "branch": self.branch,
            "pre_merge_head": self.pre_merge_head,
            "worker_head": self.worker_head,
            "result_head": self.result_head,
            "index_state": self.index_state,
            "changed_paths": list(self.changed_paths),
            "status": self.status,
            "mode": self.mode,
            "source_commits": list(self.source_commits),
            "applied_commits": list(self.applied_commits),
            "applied_head": self.applied_head,
        }


def _transaction(value: MergeTransaction | Mapping[str, Any]) -> MergeTransaction:
    return value if isinstance(value, MergeTransaction) else MergeTransaction.from_record(value)


def allowed_merge_operations(status: str) -> tuple[str, ...]:
    """Return the Manager-visible operations permitted for a status."""

    try:
        return _MERGE_OPERATIONS[status]
    except KeyError as exc:
        raise ValueError(f"unknown merge transaction status: {status}") from exc


def validate_merge_transaction(
    previous: MergeTransaction | Mapping[str, Any] | None,
    proposed: MergeTransaction | Mapping[str, Any],
) -> ValidationResult:
    """Validate transaction identity and a single status transition.

    Identity or transition divergence is classified as
    ``manual-integration-trace`` so callers stop rather than inferring success
    from an out-of-band reset, commit, or Worker-branch merge.
    """

    try:
        candidate = _transaction(proposed)
        prior = _transaction(previous) if previous is not None else None
    except (TypeError, ValueError) as exc:
        return ValidationResult(
            (LifecycleIssue("merge-transaction-invalid", str(exc), "active-merge"),)
        )
    if prior is None:
        if candidate.status == MERGE_ACTIVE:
            return ValidationResult()
        return ValidationResult(
            (
                LifecycleIssue(
                    "merge-transaction-invalid-initial-status",
                    "a new merge transaction must start in active status",
                    candidate.transaction_id,
                ),
            )
        )
    issues: list[LifecycleIssue] = []
    if prior.immutable_identity() != candidate.immutable_identity():
        issues.append(
            LifecycleIssue(
                "manual-integration-trace",
                "merge transaction identity or recorded Git observations changed outside the transaction",
                prior.transaction_id,
                severity="P0",
            )
        )
    if candidate.applied_commits[: len(prior.applied_commits)] != prior.applied_commits:
        issues.append(
            LifecycleIssue(
                "manual-integration-trace",
                "merge transaction applied prefix was rewritten or truncated",
                prior.transaction_id,
                severity="P0",
            )
        )
    elif candidate.applied_commits == prior.applied_commits:
        if candidate.applied_head != prior.applied_head:
            issues.append(
                LifecycleIssue(
                    "manual-integration-trace",
                    "merge transaction HEAD changed without extending the applied prefix",
                    prior.transaction_id,
                    severity="P0",
                )
            )
    elif candidate.applied_head == prior.applied_head:
        issues.append(
            LifecycleIssue(
                "manual-integration-trace",
                "merge transaction applied prefix advanced without a new HEAD",
                prior.transaction_id,
                severity="P0",
            )
        )
    if candidate.status not in _MERGE_TRANSITIONS[prior.status]:
        issues.append(
            LifecycleIssue(
                "manual-integration-trace",
                f"illegal merge transition: {prior.status} -> {candidate.status}",
                prior.transaction_id,
                severity="P0",
            )
        )
    return ValidationResult(tuple(issues))


@dataclass(frozen=True, slots=True)
class DoneTargetDrift:
    """P0 diagnostic emitted when completed evidence no longer matches Git."""

    final_target_sha: str
    current_target_sha: str
    commit_count: int | None
    code: str = "done-target-drift"
    severity: str = "P0"

    @property
    def message(self) -> str:
        count = "unknown" if self.commit_count is None else str(self.commit_count)
        return (
            f"completed target drifted from {self.final_target_sha} to "
            f"{self.current_target_sha} ({count} advancing commits)"
        )


def diagnose_done_target_drift(
    *,
    phase: str,
    final_target_sha: str,
    current_target_sha: str,
    commit_count: int | None,
) -> DoneTargetDrift | LifecycleIssue | None:
    """Compare a done run's frozen target with the live integration target."""

    if phase != "done":
        return None
    if not final_target_sha or not current_target_sha:
        return LifecycleIssue(
            "done-target-missing",
            "a completed run must record both final and current target SHAs",
            "completion-target",
            severity="P0",
        )
    if final_target_sha == current_target_sha:
        return None
    if commit_count is not None and (
        isinstance(commit_count, bool)
        or not isinstance(commit_count, int)
        or commit_count < 0
    ):
        raise ValueError("commit_count must be a non-negative integer or None")
    return DoneTargetDrift(final_target_sha, current_target_sha, commit_count)


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """One completion gate observation bound to a target SHA."""

    name: str
    status: str
    head_sha: str
    passing_statuses: tuple[str, ...] = ("passed",)
    required: bool = True

    def __post_init__(self) -> None:
        _required_text(self.name, "name")
        if not self.passing_statuses:
            raise ValueError("passing_statuses must not be empty")


@dataclass(frozen=True, slots=True)
class ResumeRequirement:
    """A concrete condition that must be satisfied before resume."""

    code: str
    subject: str
    reason: str
    target_sha: str = ""
    observed_sha: str = ""


def compute_resume_requirements(
    *,
    current_target_sha: str,
    gates: Sequence[GateEvidence],
    active_blockers: Sequence[str] = (),
    dirty_paths: Sequence[str] = (),
    running_roles: Sequence[str] = (),
) -> tuple[ResumeRequirement, ...]:
    """Compute pause/resume blockers without reattaching stale evidence."""

    target = _required_text(current_target_sha, "current_target_sha")
    requirements: list[ResumeRequirement] = []
    for gate in gates:
        if not gate.required:
            continue
        if not gate.status or not gate.head_sha:
            requirements.append(
                ResumeRequirement(
                    "gate-missing",
                    gate.name,
                    f"{gate.name} evidence is missing",
                    target_sha=target,
                    observed_sha=gate.head_sha,
                )
            )
        elif gate.status not in gate.passing_statuses:
            requirements.append(
                ResumeRequirement(
                    "gate-not-passing",
                    gate.name,
                    f"{gate.name} status {gate.status!r} is not passing",
                    target_sha=target,
                    observed_sha=gate.head_sha,
                )
            )
        elif gate.head_sha != target:
            requirements.append(
                ResumeRequirement(
                    "gate-stale",
                    gate.name,
                    f"{gate.name} was recorded for a different target",
                    target_sha=target,
                    observed_sha=gate.head_sha,
                )
            )
    for blocker in dict.fromkeys(str(item) for item in active_blockers if str(item)):
        requirements.append(ResumeRequirement("active-blocker", blocker, blocker))
    for path in dict.fromkeys(str(item) for item in dirty_paths if str(item)):
        requirements.append(
            ResumeRequirement("dirty-state", path, f"dirty path must be resolved: {path}")
        )
    for role_id in dict.fromkeys(str(item) for item in running_roles if str(item)):
        requirements.append(
            ResumeRequirement(
                "running-role", role_id, f"running role must reach a safe state: {role_id}"
            )
        )
    return tuple(requirements)


@dataclass(frozen=True, slots=True)
class CleanupAuditRecord:
    """Auditable resolution of one role attempt's cleanup error."""

    run_id: str
    role_id: str
    attempt_id: str
    status: str
    reason: str
    manager_identity: str
    resolved_at: str
    error_fingerprint: str

    def __post_init__(self) -> None:
        if self.status not in {"cleaned", "accepted-risk"}:
            raise ValueError("cleanup resolution status must be cleaned or accepted-risk")
        for field_name in (
            "run_id",
            "role_id",
            "attempt_id",
            "reason",
            "manager_identity",
            "resolved_at",
            "error_fingerprint",
        ):
            _required_text(getattr(self, field_name), field_name)

    def to_record(self) -> dict[str, str]:
        return {
            "kind": "cleanup-resolution",
            "run_id": self.run_id,
            "role_id": self.role_id,
            "attempt_id": self.attempt_id,
            "status": self.status,
            "reason": self.reason,
            "manager_identity": self.manager_identity,
            "resolved_at": self.resolved_at,
            "error_fingerprint": self.error_fingerprint,
        }


def cleanup_resolution_record(
    *,
    run_id: str,
    role_id: str,
    attempt_id: str,
    status: str,
    reason: str,
    manager_identity: str,
    resolved_at: str,
    error_fingerprint: str,
) -> CleanupAuditRecord:
    """Create a deterministic ``cleaned`` or ``accepted-risk`` audit record."""

    return CleanupAuditRecord(
        run_id=_required_text(run_id, "run_id"),
        role_id=_required_text(role_id, "role_id"),
        attempt_id=_required_text(attempt_id, "attempt_id"),
        status=status,
        reason=_required_text(reason, "reason"),
        manager_identity=_required_text(manager_identity, "manager_identity"),
        resolved_at=_required_text(resolved_at, "resolved_at"),
        error_fingerprint=_required_text(error_fingerprint, "error_fingerprint"),
    )


@dataclass(frozen=True, slots=True)
class FinalGateConditions:
    """Batch stability conditions required before final gates are armed."""

    current_batch_closed: bool
    review_triage_complete: bool
    pending_fix_task_drafts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalGateArm:
    """One auditable arm generation; disarm preserves the original arm data."""

    generation: int
    status: str
    target_sha: str
    armed_at: str
    armed_by: str
    disarmed_at: str = ""
    disarmed_by: str = ""
    disarm_reason: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        if self.status not in {"armed", "disarmed"}:
            raise ValueError("final gate status must be armed or disarmed")
        for field_name in ("target_sha", "armed_at", "armed_by"):
            _required_text(getattr(self, field_name), field_name)
        if self.status == "disarmed":
            for field_name in ("disarmed_at", "disarmed_by", "disarm_reason"):
                _required_text(getattr(self, field_name), field_name)

    def to_record(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "status": self.status,
            "target_sha": self.target_sha,
            "armed_at": self.armed_at,
            "armed_by": self.armed_by,
            "disarmed_at": self.disarmed_at,
            "disarmed_by": self.disarmed_by,
            "disarm_reason": self.disarm_reason,
        }


def final_gate_arm_blockers(conditions: FinalGateConditions) -> ValidationResult:
    """Explain why the current batch is not stable enough to arm final gates."""

    issues: list[LifecycleIssue] = []
    if not conditions.current_batch_closed:
        issues.append(
            LifecycleIssue("final-gates-batch-open", "current batch must be closed", "batch")
        )
    if not conditions.review_triage_complete:
        issues.append(
            LifecycleIssue(
                "final-gates-review-untriaged",
                "review triage must be complete before final gates are armed",
                "review",
            )
        )
    for draft in conditions.pending_fix_task_drafts:
        issues.append(
            LifecycleIssue(
                "final-gates-fix-draft-pending",
                f"fix-task draft is still pending: {draft}",
                draft,
            )
        )
    return ValidationResult(tuple(issues))


def arm_final_gates(
    current: FinalGateArm | None,
    *,
    target_sha: str,
    armed_by: str,
    armed_at: str,
    conditions: FinalGateConditions,
) -> FinalGateArm:
    """Arm final gates after stability checks, idempotently for one target."""

    blockers = final_gate_arm_blockers(conditions)
    blockers.raise_for_errors()
    target = _required_text(target_sha, "target_sha")
    actor = _required_text(armed_by, "armed_by")
    timestamp = _required_text(armed_at, "armed_at")
    if current is not None and current.status == "armed":
        if current.target_sha == target:
            return current
        raise LifecycleInvariantError(
            (
                LifecycleIssue(
                    "final-gates-already-armed",
                    "final gates must be disarmed before arming a different target",
                    current.target_sha,
                ),
            )
        )
    generation = 1 if current is None else current.generation + 1
    return FinalGateArm(generation, "armed", target, timestamp, actor)


def disarm_final_gates(
    current: FinalGateArm,
    *,
    disarmed_by: str,
    disarmed_at: str,
    reason: str,
) -> FinalGateArm:
    """Disarm an arm generation while retaining a complete audit record."""

    if current.status == "disarmed":
        return current
    return replace(
        current,
        status="disarmed",
        disarmed_by=_required_text(disarmed_by, "disarmed_by"),
        disarmed_at=_required_text(disarmed_at, "disarmed_at"),
        disarm_reason=_required_text(reason, "reason"),
    )


def validate_final_gate_arm(
    current: FinalGateArm,
    *,
    current_target_sha: str,
    new_tasks_created: bool = False,
) -> ValidationResult:
    """Require disarm after target drift or creation of a new task."""

    if current.status != "armed":
        return ValidationResult()
    reasons: list[str] = []
    if current.target_sha != current_target_sha:
        reasons.append("the completion target changed")
    if new_tasks_created:
        reasons.append("a new task was created")
    if not reasons:
        return ValidationResult()
    return ValidationResult(
        (
            LifecycleIssue(
                "final-gates-disarm-required",
                "final gates must be disarmed because " + " and ".join(reasons),
                current.target_sha,
            ),
        )
    )
