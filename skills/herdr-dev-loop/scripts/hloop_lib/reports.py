"""Pure outcome gates and bounded-convergence projections for HLoop 0.5.2.

The report model deliberately keeps the v0.5.0/v0.5.1 outcome fields stable.
The bounded-convergence fields are optional projections: legacy records can be
read and written without knowing about them, while enabled runs can expose the
execution evidence needed by a final report and postmortem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Mapping, Sequence

from .requirements import EvidenceRef, RequirementProgress


OUTCOME_KINDS = frozenset({"DRAFT", "FINAL", "BLOCKED"})
PASSING_GATE_STATUSES = frozenset({"passed", "accepted-risk", "not-required"})
GATE_STATUSES = PASSING_GATE_STATUSES | frozenset({"pending", "blocked", "failed"})
TERMINAL_REQUIREMENT_STATUSES = frozenset({"verified", "deferred", "superseded"})


class OutcomeModelError(ValueError):
    """Raised when an outcome report attempts to bypass its terminal gate."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeModelError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise OutcomeModelError(f"{field_name} must be a string")
    return value.strip()


def _rfc3339(value: str, field_name: str) -> str:
    text = _required_text(value, field_name)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise OutcomeModelError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OutcomeModelError(f"{field_name} must include a timezone")
    return text


def _unique_texts(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise OutcomeModelError(f"{field_name} must be a sequence of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if len(set(normalized)) != len(normalized):
        raise OutcomeModelError(f"{field_name} must not contain duplicates")
    return normalized


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OutcomeModelError(f"{field_name} must be a non-negative integer")
    return value


def _nullable_nonnegative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field_name)


def _nonnegative_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutcomeModelError(f"{field_name} must be a non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise OutcomeModelError(f"{field_name} must be a non-negative finite number")
    return normalized


def _nullable_nonnegative_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _nonnegative_float(value, field_name)


def _count_map(value: Any, field_name: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise OutcomeModelError(f"{field_name} must be an object of counts")
    result: dict[str, int] = {}
    for key, count in value.items():
        normalized_key = _required_text(key, f"{field_name} key")
        result[normalized_key] = _nonnegative_int(count, f"{field_name}.{normalized_key}")
    return dict(sorted(result.items()))


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OutcomeModelError(f"{field_name} must be an object")
    return value


@dataclass(frozen=True, slots=True)
class OutcomeGate:
    """One externally observed finish/blocking gate."""

    name: str
    status: str
    evidence_refs: tuple[str, ...] = ()
    target_sha: str = ""
    verified_by: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        _required_text(self.name, "gate name")
        if self.status not in GATE_STATUSES:
            raise OutcomeModelError(f"unknown outcome gate status: {self.status}")
        object.__setattr__(
            self, "evidence_refs", _unique_texts(self.evidence_refs, "gate evidence_refs")
        )
        if self.verified_by and self.verified_by not in {"manager", "hloop"}:
            raise OutcomeModelError("gate verified_by must be manager or hloop")
        if not isinstance(self.target_sha, str):
            raise OutcomeModelError("gate target_sha must be a string")
        if not isinstance(self.required, bool):
            raise OutcomeModelError("gate required must be a boolean")

    @property
    def qualifies_for_final(self) -> bool:
        if not self.required:
            return True
        if self.status == "not-required":
            return True
        return (
            self.status in {"passed", "accepted-risk"}
            and bool(self.evidence_refs)
            and bool(self.target_sha)
            and self.verified_by in {"manager", "hloop"}
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
            "target_sha": self.target_sha,
            "verified_by": self.verified_by,
            "required": self.required,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "OutcomeGate":
        return cls(
            name=str(record.get("name") or ""),
            status=str(record.get("status") or ""),
            evidence_refs=tuple(record.get("evidence_refs") or ()),
            target_sha=str(record.get("target_sha") or ""),
            verified_by=str(record.get("verified_by") or ""),
            required=record.get("required", True),
        )


@dataclass(frozen=True, slots=True)
class ManagerInvocation:
    """The Manager backend identity captured for postmortem comparison."""

    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    recorded_at: str = ""
    unavailable_reason: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "provider",
            "model",
            "reasoning_effort",
            "recorded_at",
            "unavailable_reason",
        ):
            object.__setattr__(
                self, field_name, _optional_text(getattr(self, field_name), field_name)
            )
        if self.recorded_at:
            _rfc3339(self.recorded_at, "recorded_at")

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "recorded_at": self.recorded_at,
            "unavailable_reason": self.unavailable_reason,
        }
        return record

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "ManagerInvocation":
        record = _record(value, "manager_invocation")
        return cls(
            provider=record.get("provider", ""),
            model=record.get("model", ""),
            reasoning_effort=record.get("reasoning_effort", ""),
            recorded_at=record.get("recorded_at", ""),
            unavailable_reason=record.get("unavailable_reason", ""),
        )


@dataclass(frozen=True, slots=True)
class ExecutionMetrics:
    """Deterministic counts and timings projected into an outcome report.

    The first fields mirror the state contract in the bounded-convergence plan.
    The additional counters make the final report useful without requiring the
    renderer to inspect raw state.  All counters are optional at the state
    boundary and default to zero, which keeps legacy records unaffected.
    """

    planned_task_count: int = 0
    remediation_task_count: int = 0
    task_origin_counts: Mapping[str, int] = field(default_factory=dict)
    scope_revision_counts: Mapping[str, int] = field(default_factory=dict)
    review_fix_rounds: int = 0
    candidate_count: int = 0
    confirmed_count: int = 0
    finding_origin_counts: Mapping[str, int] = field(default_factory=dict)
    finding_contract_relation_counts: Mapping[str, int] = field(default_factory=dict)
    finding_decision_requirement_counts: Mapping[str, int] = field(default_factory=dict)
    finding_disposition_counts: Mapping[str, int] = field(default_factory=dict)
    review_completed_count: int = 0
    stale_review_count: int = 0
    aborted_review_count: int = 0
    timeout_review_count: int = 0
    gap_completed_count: int = 0
    stale_gap_count: int = 0
    aborted_gap_count: int = 0
    timeout_gap_count: int = 0
    worker_count: int = 0
    planned_task_completed: bool = False
    scope_expansion_started_at: str = ""
    scope_expansion_user_input_id: str = ""
    effective_parallelism: float | None = None
    phase_wall_time_seconds: float = 0.0
    validation_time_seconds: float = 0.0
    review_wait_time_seconds: float = 0.0
    longest_worker_seconds: float = 0.0

    def __post_init__(self) -> None:
        for field_name in (
            "planned_task_count",
            "remediation_task_count",
            "review_fix_rounds",
            "candidate_count",
            "confirmed_count",
            "review_completed_count",
            "stale_review_count",
            "aborted_review_count",
            "timeout_review_count",
            "gap_completed_count",
            "stale_gap_count",
            "aborted_gap_count",
            "timeout_gap_count",
            "worker_count",
        ):
            object.__setattr__(
                self, field_name, _nonnegative_int(getattr(self, field_name), field_name)
            )
        if not isinstance(self.planned_task_completed, bool):
            raise OutcomeModelError("planned_task_completed must be a boolean")
        for field_name in (
            "task_origin_counts",
            "scope_revision_counts",
            "finding_origin_counts",
            "finding_contract_relation_counts",
            "finding_decision_requirement_counts",
            "finding_disposition_counts",
        ):
            object.__setattr__(
                self, field_name, _count_map(getattr(self, field_name), field_name)
            )
        if self.scope_expansion_started_at:
            _rfc3339(self.scope_expansion_started_at, "scope_expansion_started_at")
        object.__setattr__(
            self,
            "scope_expansion_started_at",
            _optional_text(self.scope_expansion_started_at, "scope_expansion_started_at"),
        )
        object.__setattr__(
            self,
            "scope_expansion_user_input_id",
            _optional_text(
                self.scope_expansion_user_input_id, "scope_expansion_user_input_id"
            ),
        )
        object.__setattr__(
            self,
            "effective_parallelism",
            _nullable_nonnegative_float(
                self.effective_parallelism, "effective_parallelism"
            ),
        )
        for field_name in (
            "phase_wall_time_seconds",
            "validation_time_seconds",
            "review_wait_time_seconds",
            "longest_worker_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_float(getattr(self, field_name), field_name),
            )

    @property
    def review_attempt_count(self) -> int:
        return (
            self.review_completed_count
            + self.stale_review_count
            + self.aborted_review_count
            + self.timeout_review_count
        )

    @property
    def gap_attempt_count(self) -> int:
        return (
            self.gap_completed_count
            + self.stale_gap_count
            + self.aborted_gap_count
            + self.timeout_gap_count
        )

    def postmortem_warnings(self) -> tuple[str, ...]:
        """Return stable warning codes without changing execution state.

        These are intentionally advisory.  Dispatch freezes and round limits
        remain state-machine responsibilities; this method only projects
        evidence for a report or postmortem.
        """

        warnings: list[str] = []
        if self.remediation_task_count > 0 and (
            self.planned_task_completed
            or self.remediation_task_count > self.planned_task_count
        ):
            warnings.append(
                "remediation-task-growth: "
                f"{self.remediation_task_count} remediation tasks after "
                f"{self.planned_task_count} planned tasks"
            )

        review_attempts = self.review_attempt_count
        review_shortfalls = self.stale_review_count + self.aborted_review_count
        if review_attempts and review_shortfalls * 2 >= review_attempts:
            warnings.append(
                "review-shortfall-ratio-high: "
                f"{review_shortfalls}/{review_attempts} stale-or-aborted reviews"
            )

        if (
            self.worker_count >= 2
            and self.effective_parallelism is not None
            and self.effective_parallelism < 1.5
        ):
            warnings.append(
                "effective-parallelism-low: "
                f"{self.effective_parallelism:g} with {self.worker_count} workers"
            )
        return tuple(warnings)

    def to_record(self) -> dict[str, Any]:
        return {
            "planned_task_count": self.planned_task_count,
            "remediation_task_count": self.remediation_task_count,
            "task_origin_counts": dict(self.task_origin_counts),
            "scope_revision_counts": dict(self.scope_revision_counts),
            "review_fix_rounds": self.review_fix_rounds,
            "candidate_count": self.candidate_count,
            "confirmed_count": self.confirmed_count,
            "finding_origin_counts": dict(self.finding_origin_counts),
            "finding_contract_relation_counts": dict(
                self.finding_contract_relation_counts
            ),
            "finding_decision_requirement_counts": dict(
                self.finding_decision_requirement_counts
            ),
            "finding_disposition_counts": dict(self.finding_disposition_counts),
            "review_completed_count": self.review_completed_count,
            "stale_review_count": self.stale_review_count,
            "aborted_review_count": self.aborted_review_count,
            "timeout_review_count": self.timeout_review_count,
            "gap_completed_count": self.gap_completed_count,
            "stale_gap_count": self.stale_gap_count,
            "aborted_gap_count": self.aborted_gap_count,
            "timeout_gap_count": self.timeout_gap_count,
            "worker_count": self.worker_count,
            "planned_task_completed": self.planned_task_completed,
            "scope_expansion_started_at": self.scope_expansion_started_at,
            "scope_expansion_user_input_id": self.scope_expansion_user_input_id,
            "effective_parallelism": self.effective_parallelism,
            "phase_wall_time_seconds": self.phase_wall_time_seconds,
            "validation_time_seconds": self.validation_time_seconds,
            "review_wait_time_seconds": self.review_wait_time_seconds,
            "longest_worker_seconds": self.longest_worker_seconds,
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "ExecutionMetrics":
        record = _record(value, "execution_metrics")
        return cls(
            planned_task_count=record.get("planned_task_count", 0),
            remediation_task_count=record.get("remediation_task_count", 0),
            task_origin_counts=record.get("task_origin_counts", {}),
            scope_revision_counts=record.get(
                "scope_revision_counts", record.get("task_scope_revision_counts", {})
            ),
            review_fix_rounds=record.get("review_fix_rounds", 0),
            candidate_count=record.get("candidate_count", record.get("finding_candidate_count", 0)),
            confirmed_count=record.get("confirmed_count", record.get("finding_confirmed_count", 0)),
            finding_origin_counts=record.get("finding_origin_counts", {}),
            finding_contract_relation_counts=record.get(
                "finding_contract_relation_counts", {}
            ),
            finding_decision_requirement_counts=record.get(
                "finding_decision_requirement_counts", {}
            ),
            finding_disposition_counts=record.get("finding_disposition_counts", {}),
            review_completed_count=record.get("review_completed_count", 0),
            stale_review_count=record.get("stale_review_count", 0),
            aborted_review_count=record.get("aborted_review_count", 0),
            timeout_review_count=record.get("timeout_review_count", 0),
            gap_completed_count=record.get("gap_completed_count", 0),
            stale_gap_count=record.get("stale_gap_count", 0),
            aborted_gap_count=record.get("aborted_gap_count", 0),
            timeout_gap_count=record.get("timeout_gap_count", 0),
            worker_count=record.get("worker_count", 0),
            planned_task_completed=record.get("planned_task_completed", False),
            scope_expansion_started_at=record.get("scope_expansion_started_at", ""),
            scope_expansion_user_input_id=record.get(
                "scope_expansion_user_input_id", ""
            ),
            effective_parallelism=record.get("effective_parallelism"),
            phase_wall_time_seconds=record.get("phase_wall_time_seconds", 0.0),
            validation_time_seconds=record.get("validation_time_seconds", 0.0),
            review_wait_time_seconds=record.get("review_wait_time_seconds", 0.0),
            longest_worker_seconds=record.get("longest_worker_seconds", 0.0),
        )

    @classmethod
    def from_finding_dispositions(
        cls,
        values: Sequence[Any],
        **kwargs: Any,
    ) -> "ExecutionMetrics":
        """Build finding counters from disposition records or model objects."""

        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise OutcomeModelError("finding dispositions must be a sequence")
        origin_counts: dict[str, int] = {}
        relation_counts: dict[str, int] = {}
        decision_counts: dict[str, int] = {}
        disposition_counts: dict[str, int] = {}
        confirmed_count = 0
        for value in values:
            record = value.to_record() if hasattr(value, "to_record") else _record(
                value, "finding disposition"
            )
            for field_name, counts in (
                ("origin", origin_counts),
                ("contract_relation", relation_counts),
                ("decision_requirement", decision_counts),
                ("disposition", disposition_counts),
            ):
                key = _required_text(record.get(field_name), field_name)
                counts[key] = counts.get(key, 0) + 1
            if record.get("fact_status") == "confirmed":
                confirmed_count += 1
        return cls(
            candidate_count=len(values),
            confirmed_count=confirmed_count,
            finding_origin_counts=origin_counts,
            finding_contract_relation_counts=relation_counts,
            finding_decision_requirement_counts=decision_counts,
            finding_disposition_counts=disposition_counts,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class FollowUpProjection:
    """The namespaced follow-up count and references shown in a report."""

    count: int = 0
    references: tuple[str, ...] = ()
    issue_keys: tuple[str, ...] = ()
    issue_key_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "count", _nonnegative_int(self.count, "follow-ups count"))
        for field_name in ("references", "issue_keys", "issue_key_aliases"):
            object.__setattr__(
                self, field_name, _unique_texts(getattr(self, field_name), field_name)
            )

    @property
    def open_count(self) -> int:
        return self.count

    def to_record(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "references": list(self.references),
            "issue_keys": list(self.issue_keys),
            "issue_key_aliases": list(self.issue_key_aliases),
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "FollowUpProjection":
        record = _record(value, "follow_ups")
        issue_keys = record.get("issue_keys", ())
        if isinstance(issue_keys, Mapping):
            issue_keys = tuple(issue_keys)
        aliases = record.get("issue_key_aliases", ())
        if isinstance(aliases, Mapping):
            aliases = tuple(aliases)
        references = record.get("references", record.get("artifact_refs", ()))
        if isinstance(references, (str, bytes)):
            references = (references,)
        if isinstance(issue_keys, (str, bytes)):
            issue_keys = (issue_keys,)
        if isinstance(aliases, (str, bytes)):
            aliases = (aliases,)
        count = record.get(
            "count", record.get("open_count", len(issue_keys or references or ()))
        )
        return cls(
            count=count,
            references=tuple(references or ()),
            issue_keys=tuple(issue_keys or ()),
            issue_key_aliases=tuple(aliases or ()),
        )


@dataclass(frozen=True, slots=True)
class ReviewConvergenceProjection:
    """Review-round evidence used by the bounded convergence report."""

    status: str = "pending"
    target_sha: str = ""
    fix_round: int = 0
    max_fix_rounds: int = 2
    authorized_extra_rounds: int = 0
    verified_actionable_findings: int | None = None
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _required_text(self.status, "convergence status"))
        object.__setattr__(self, "target_sha", _optional_text(self.target_sha, "target_sha"))
        for field_name in ("fix_round", "max_fix_rounds", "authorized_extra_rounds"):
            object.__setattr__(
                self, field_name, _nonnegative_int(getattr(self, field_name), field_name)
            )
        if self.max_fix_rounds == 0:
            raise OutcomeModelError("max_fix_rounds must be positive")
        object.__setattr__(
            self,
            "verified_actionable_findings",
            _nullable_nonnegative_int(
                self.verified_actionable_findings, "verified_actionable_findings"
            ),
        )
        object.__setattr__(
            self, "artifact_refs", _unique_texts(self.artifact_refs, "artifact_refs")
        )

    @property
    def rounds(self) -> int:
        return self.fix_round

    @property
    def qualifies_for_final(self) -> bool:
        return self.status in {"converged", "passed"} and (
            self.verified_actionable_findings in {None, 0}
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target_sha": self.target_sha,
            "fix_round": self.fix_round,
            "max_fix_rounds": self.max_fix_rounds,
            "authorized_extra_rounds": self.authorized_extra_rounds,
            "verified_actionable_findings": self.verified_actionable_findings,
            "artifact_refs": list(self.artifact_refs),
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "ReviewConvergenceProjection":
        record = _record(value, "review_convergence")
        return cls(
            status=record.get("status", "pending"),
            target_sha=record.get("target_sha", ""),
            fix_round=record.get("fix_round", record.get("rounds", 0)),
            max_fix_rounds=record.get("max_fix_rounds", 2),
            authorized_extra_rounds=record.get("authorized_extra_rounds", 0),
            verified_actionable_findings=record.get("verified_actionable_findings"),
            artifact_refs=record.get("artifact_refs", ()),
        )


@dataclass(frozen=True, slots=True)
class ManualFinalReviewProjection:
    """Certification completeness evidence projected into the final report."""

    status: str = "pending"
    certification_id: str = ""
    target_sha: str = ""
    prepared_plan: str = ""
    prepared_plan_digest: str = ""
    manifest: str = ""
    report: str = ""
    manifest_complete: bool | None = None
    shortfall_count: int | None = None
    verified_actionable_findings: int | None = None
    lane_completed_count: int = 0
    lane_count: int = 0
    incomplete_attempt_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _required_text(self.status, "manual final status"))
        for field_name in (
            "certification_id",
            "target_sha",
            "prepared_plan",
            "prepared_plan_digest",
            "manifest",
            "report",
        ):
            object.__setattr__(
                self, field_name, _optional_text(getattr(self, field_name), field_name)
            )
        if self.manifest_complete is not None and not isinstance(
            self.manifest_complete, bool
        ):
            raise OutcomeModelError("manifest_complete must be a boolean or null")
        for field_name in (
            "shortfall_count",
            "verified_actionable_findings",
        ):
            object.__setattr__(
                self,
                field_name,
                _nullable_nonnegative_int(getattr(self, field_name), field_name),
            )
        for field_name in (
            "lane_completed_count",
            "lane_count",
            "incomplete_attempt_count",
        ):
            object.__setattr__(
                self, field_name, _nonnegative_int(getattr(self, field_name), field_name)
            )
        if self.lane_completed_count > self.lane_count and self.lane_count:
            raise OutcomeModelError("lane_completed_count cannot exceed lane_count")

    @property
    def complete(self) -> bool:
        if self.status == "not-required-for-legacy-run":
            return True
        return (
            self.status == "passed"
            and self.manifest_complete is True
            and self.shortfall_count in {None, 0}
            and self.verified_actionable_findings in {None, 0}
        )

    @property
    def qualifies_for_final(self) -> bool:
        return self.complete

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "certification_id": self.certification_id,
            "target_sha": self.target_sha,
            "prepared_plan": self.prepared_plan,
            "prepared_plan_digest": self.prepared_plan_digest,
            "manifest": self.manifest,
            "report": self.report,
            "manifest_complete": self.manifest_complete,
            "shortfall_count": self.shortfall_count,
            "verified_actionable_findings": self.verified_actionable_findings,
            "lane_completed_count": self.lane_completed_count,
            "lane_count": self.lane_count,
            "incomplete_attempt_count": self.incomplete_attempt_count,
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "ManualFinalReviewProjection":
        record = _record(value, "manual_final_review")
        return cls(
            status=record.get("status", "pending"),
            certification_id=record.get("certification_id", ""),
            target_sha=record.get("target_sha", ""),
            prepared_plan=record.get("prepared_plan", ""),
            prepared_plan_digest=record.get("prepared_plan_digest", ""),
            manifest=record.get("manifest", ""),
            report=record.get("report", ""),
            manifest_complete=record.get("manifest_complete", record.get("complete")),
            shortfall_count=record.get("shortfall_count"),
            verified_actionable_findings=record.get("verified_actionable_findings"),
            lane_completed_count=record.get("lane_completed_count", 0),
            lane_count=record.get("lane_count", 0),
            incomplete_attempt_count=record.get("incomplete_attempt_count", 0),
        )


# Short aliases make the projection types convenient for command-layer callers
# while keeping the persisted field names explicit.
ManagerInvocationProjection = ManagerInvocation
FollowUpSummary = FollowUpProjection
ConvergenceProjection = ReviewConvergenceProjection
ManualFinalProjection = ManualFinalReviewProjection


def compute_postmortem_warnings(
    metrics: ExecutionMetrics | Mapping[str, Any] | None,
    *,
    convergence: ReviewConvergenceProjection | Mapping[str, Any] | None = None,
    manual_final_review: ManualFinalReviewProjection
    | Mapping[str, Any]
    | None = None,
) -> tuple[str, ...]:
    """Project deterministic warnings without mutating state or stopping a run."""

    normalized_metrics = (
        metrics
        if isinstance(metrics, ExecutionMetrics)
        else ExecutionMetrics.from_record(metrics)
        if metrics is not None
        else None
    )
    warnings = list(normalized_metrics.postmortem_warnings() if normalized_metrics else ())
    normalized_convergence = (
        convergence
        if isinstance(convergence, ReviewConvergenceProjection)
        else ReviewConvergenceProjection.from_record(convergence)
        if convergence is not None
        else None
    )
    if normalized_convergence and normalized_convergence.status == "exhausted":
        warnings.append(
            "review-convergence-exhausted: "
            f"round {normalized_convergence.fix_round}/{normalized_convergence.max_fix_rounds}"
        )
    normalized_manual = (
        manual_final_review
        if isinstance(manual_final_review, ManualFinalReviewProjection)
        else ManualFinalReviewProjection.from_record(manual_final_review)
        if manual_final_review is not None
        else None
    )
    if normalized_manual and normalized_manual.status in {"incomplete", "failed"}:
        shortfalls = normalized_manual.shortfall_count
        suffix = f": {shortfalls} shortfalls" if shortfalls is not None else ""
        warnings.append(f"manual-final-{normalized_manual.status}{suffix}")
    return tuple(dict.fromkeys(warnings))


def _projection_from_state(
    state: Mapping[str, Any], key: str, model: Any
) -> Any | None:
    value = state.get(key)
    if value is None:
        return None
    if isinstance(value, model):
        return value
    return model.from_record(_record(value, key))


def report_projections_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build optional outcome fields from a format-3 state snapshot.

    Legacy state has none of these keys, so the returned mapping is empty and
    callers can pass it directly into an existing outcome constructor.
    """

    if not isinstance(state, Mapping):
        raise OutcomeModelError("state must be an object")
    manager_invocation = _projection_from_state(state, "manager_invocation", ManagerInvocation)
    execution_metrics = _projection_from_state(state, "execution_metrics", ExecutionMetrics)
    follow_ups = _projection_from_state(state, "follow_ups", FollowUpProjection)
    convergence = _projection_from_state(
        state, "review_convergence", ReviewConvergenceProjection
    )
    manual_final_review = _projection_from_state(
        state, "manual_final_review", ManualFinalReviewProjection
    )
    warnings = compute_postmortem_warnings(
        execution_metrics,
        convergence=convergence,
        manual_final_review=manual_final_review,
    )
    projections: dict[str, Any] = {}
    for key, value in (
        ("manager_invocation", manager_invocation),
        ("execution_metrics", execution_metrics),
        ("follow_ups", follow_ups),
        ("review_convergence", convergence),
        ("manual_final_review", manual_final_review),
    ):
        if value is not None:
            projections[key] = value
    if warnings:
        projections["postmortem_warnings"] = warnings
    return projections


# Explicit names are useful to the CLI wiring task and make the state-to-report
# boundary easy to test independently.
project_report_observability = report_projections_from_state


def manager_invocation_from_state(state: Mapping[str, Any]) -> ManagerInvocation | None:
    return _projection_from_state(state, "manager_invocation", ManagerInvocation)


def execution_metrics_from_state(state: Mapping[str, Any]) -> ExecutionMetrics | None:
    return _projection_from_state(state, "execution_metrics", ExecutionMetrics)


def follow_ups_from_state(state: Mapping[str, Any]) -> FollowUpProjection | None:
    return _projection_from_state(state, "follow_ups", FollowUpProjection)


def review_convergence_from_state(
    state: Mapping[str, Any],
) -> ReviewConvergenceProjection | None:
    return _projection_from_state(state, "review_convergence", ReviewConvergenceProjection)


def manual_final_review_from_state(
    state: Mapping[str, Any],
) -> ManualFinalReviewProjection | None:
    return _projection_from_state(state, "manual_final_review", ManualFinalReviewProjection)


def postmortem_warnings_from_state(state: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(report_projections_from_state(state).get("postmortem_warnings", ()))


@dataclass(frozen=True, slots=True)
class OutcomeReport:
    """A report whose terminal kind is inseparable from its proof gate."""

    kind: str
    run_id: str
    goal: str
    generated_at: str
    requirement_progress: tuple[RequirementProgress, ...]
    gates: tuple[OutcomeGate, ...]
    integration_target_sha: str
    current_branch_sha: str
    user_changes: tuple[str, ...] = ()
    validation_evidence: tuple[EvidenceRef, ...] = ()
    review_findings: tuple[str, ...] = ()
    review_fixes: tuple[str, ...] = ()
    accepted_risks: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    unresolved_items: tuple[str, ...] = ()
    cleanup_status: str = ""
    session_status: str = ""
    next_user_actions: tuple[str, ...] = ()
    blocking_reason: str = ""
    external_goal_blocked: bool = False
    finalized: bool = False
    manager_invocation: ManagerInvocation | None = None
    execution_metrics: ExecutionMetrics | None = None
    follow_ups: FollowUpProjection | None = None
    review_convergence: ReviewConvergenceProjection | None = None
    manual_final_review: ManualFinalReviewProjection | None = None
    postmortem_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in OUTCOME_KINDS:
            raise OutcomeModelError(f"unknown outcome kind: {self.kind}")
        if not isinstance(self.finalized, bool) or not isinstance(
            self.external_goal_blocked, bool
        ):
            raise OutcomeModelError(
                "finalized and external_goal_blocked must be booleans"
            )
        _required_text(self.run_id, "run_id")
        _required_text(self.goal, "goal")
        _rfc3339(self.generated_at, "generated_at")
        if any(not isinstance(item, RequirementProgress) for item in self.requirement_progress):
            raise OutcomeModelError(
                "requirement_progress must contain RequirementProgress values"
            )
        requirement_ids = [item.requirement_id for item in self.requirement_progress]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise OutcomeModelError("outcome requirement ids must be unique")
        if any(not isinstance(gate, OutcomeGate) for gate in self.gates):
            raise OutcomeModelError("gates must contain OutcomeGate values")
        gate_names = [gate.name for gate in self.gates]
        if len(set(gate_names)) != len(gate_names):
            raise OutcomeModelError("outcome gate names must be unique")
        if any(not isinstance(item, EvidenceRef) for item in self.validation_evidence):
            raise OutcomeModelError("validation_evidence must contain EvidenceRef values")
        for field_name in (
            "user_changes",
            "review_findings",
            "review_fixes",
            "accepted_risks",
            "decisions",
            "unresolved_items",
            "next_user_actions",
        ):
            object.__setattr__(
                self, field_name, _unique_texts(getattr(self, field_name), field_name)
            )
        for field_name, model in (
            ("manager_invocation", ManagerInvocation),
            ("execution_metrics", ExecutionMetrics),
            ("follow_ups", FollowUpProjection),
            ("review_convergence", ReviewConvergenceProjection),
            ("manual_final_review", ManualFinalReviewProjection),
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, Mapping):
                value = model.from_record(value)
            elif not isinstance(value, model):
                raise OutcomeModelError(f"{field_name} must be a {model.__name__}")
            object.__setattr__(self, field_name, value)
        warnings = _unique_texts(self.postmortem_warnings, "postmortem_warnings")
        if not warnings and any(
            value is not None
            for value in (
                self.execution_metrics,
                self.review_convergence,
                self.manual_final_review,
            )
        ):
            warnings = compute_postmortem_warnings(
                self.execution_metrics,
                convergence=self.review_convergence,
                manual_final_review=self.manual_final_review,
            )
        object.__setattr__(self, "postmortem_warnings", warnings)
        if self.kind == "DRAFT":
            if self.finalized:
                raise OutcomeModelError("DRAFT outcome must not be finalized")
            return
        if not self.finalized:
            raise OutcomeModelError(f"{self.kind} outcome must be finalized")
        if self.kind == "FINAL":
            self._validate_final()
        else:
            self._validate_blocked()

    def _validate_final(self) -> None:
        if self.external_goal_blocked or self.blocking_reason:
            raise OutcomeModelError("FINAL outcome cannot carry a blocking outcome")
        if not self.requirement_progress:
            raise OutcomeModelError("FINAL outcome requires requirement progress")
        incomplete = [
            item.requirement_id
            for item in self.requirement_progress
            if item.status not in TERMINAL_REQUIREMENT_STATUSES
        ]
        if incomplete:
            raise OutcomeModelError(
                "FINAL outcome has incomplete requirements: " + ", ".join(incomplete)
            )
        if not self.gates:
            raise OutcomeModelError("FINAL outcome requires finish gates")
        failed = [gate.name for gate in self.gates if not gate.qualifies_for_final]
        if failed:
            raise OutcomeModelError(
                "FINAL outcome has unverified or non-passing gates: " + ", ".join(failed)
            )
        target = _required_text(self.integration_target_sha, "integration_target_sha")
        current = _required_text(self.current_branch_sha, "current_branch_sha")
        if target != current:
            raise OutcomeModelError("FINAL outcome target SHA does not match current branch SHA")
        stale_gates = [
            gate.name
            for gate in self.gates
            if gate.required
            and gate.status != "not-required"
            and gate.target_sha != target
        ]
        if stale_gates:
            raise OutcomeModelError(
                "FINAL outcome has gates for a different target SHA: "
                + ", ".join(stale_gates)
            )
        if self.review_convergence is not None and not self.review_convergence.qualifies_for_final:
            raise OutcomeModelError(
                "FINAL outcome requires converged review with zero verified actionable findings"
            )
        if self.manual_final_review is not None and not self.manual_final_review.qualifies_for_final:
            raise OutcomeModelError(
                "FINAL outcome requires complete manual final review"
            )

    def _validate_blocked(self) -> None:
        if not self.external_goal_blocked:
            raise OutcomeModelError(
                "BLOCKED outcome requires explicit external-goal blocked authorization"
            )
        _required_text(self.blocking_reason, "blocking_reason")
        if not any(gate.required and gate.status == "blocked" for gate in self.gates):
            raise OutcomeModelError("BLOCKED outcome requires a blocked required gate")

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "kind": self.kind,
            "run_id": self.run_id,
            "goal": self.goal,
            "generated_at": self.generated_at,
            "requirements": [item.to_record() for item in self.requirement_progress],
            "gates": [gate.to_record() for gate in self.gates],
            "integration_target_sha": self.integration_target_sha,
            "current_branch_sha": self.current_branch_sha,
            "user_changes": list(self.user_changes),
            "validation_evidence": [item.to_record() for item in self.validation_evidence],
            "review": {
                "confirmed_findings": list(self.review_findings),
                "fixes": list(self.review_fixes),
                "accepted_risks": list(self.accepted_risks),
            },
            "decisions": list(self.decisions),
            "unresolved_items": list(self.unresolved_items),
            "cleanup_status": self.cleanup_status,
            "session_status": self.session_status,
            "next_user_actions": list(self.next_user_actions),
            "blocking_reason": self.blocking_reason,
            "external_goal_blocked": self.external_goal_blocked,
            "finalized": self.finalized,
        }
        optional_projections = (
            ("manager_invocation", self.manager_invocation),
            ("execution_metrics", self.execution_metrics),
            ("follow_ups", self.follow_ups),
            ("review_convergence", self.review_convergence),
            ("manual_final_review", self.manual_final_review),
        )
        for key, value in optional_projections:
            if value is not None:
                record[key] = value.to_record()
        if self.postmortem_warnings:
            record["postmortem_warnings"] = list(self.postmortem_warnings)
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "OutcomeReport":
        requirements = record.get("requirements") or ()
        gates = record.get("gates") or ()
        evidence = record.get("validation_evidence") or ()
        review = record.get("review") or {}
        if not isinstance(review, Mapping):
            raise OutcomeModelError("review must be an object")
        return cls(
            kind=str(record.get("kind") or ""),
            run_id=str(record.get("run_id") or ""),
            goal=str(record.get("goal") or ""),
            generated_at=str(record.get("generated_at") or ""),
            requirement_progress=tuple(
                item
                if isinstance(item, RequirementProgress)
                else RequirementProgress.from_record(item)
                for item in requirements
            ),
            gates=tuple(
                item if isinstance(item, OutcomeGate) else OutcomeGate.from_record(item)
                for item in gates
            ),
            integration_target_sha=str(record.get("integration_target_sha") or ""),
            current_branch_sha=str(record.get("current_branch_sha") or ""),
            user_changes=tuple(record.get("user_changes") or ()),
            validation_evidence=tuple(
                item if isinstance(item, EvidenceRef) else EvidenceRef.from_record(item)
                for item in evidence
            ),
            review_findings=tuple(review.get("confirmed_findings") or ()),
            review_fixes=tuple(review.get("fixes") or ()),
            accepted_risks=tuple(review.get("accepted_risks") or ()),
            decisions=tuple(record.get("decisions") or ()),
            unresolved_items=tuple(record.get("unresolved_items") or ()),
            cleanup_status=str(record.get("cleanup_status") or ""),
            session_status=str(record.get("session_status") or ""),
            next_user_actions=tuple(record.get("next_user_actions") or ()),
            blocking_reason=str(record.get("blocking_reason") or ""),
            external_goal_blocked=record.get("external_goal_blocked", False),
            finalized=record.get("finalized", False),
            manager_invocation=(
                record["manager_invocation"]
                if isinstance(record.get("manager_invocation"), ManagerInvocation)
                else ManagerInvocation.from_record(record["manager_invocation"])
                if record.get("manager_invocation") is not None
                else None
            ),
            execution_metrics=(
                record["execution_metrics"]
                if isinstance(record.get("execution_metrics"), ExecutionMetrics)
                else ExecutionMetrics.from_record(record["execution_metrics"])
                if record.get("execution_metrics") is not None
                else None
            ),
            follow_ups=(
                record["follow_ups"]
                if isinstance(record.get("follow_ups"), FollowUpProjection)
                else FollowUpProjection.from_record(record["follow_ups"])
                if record.get("follow_ups") is not None
                else None
            ),
            review_convergence=(
                record["review_convergence"]
                if isinstance(record.get("review_convergence"), ReviewConvergenceProjection)
                else ReviewConvergenceProjection.from_record(record["review_convergence"])
                if record.get("review_convergence") is not None
                else None
            ),
            manual_final_review=(
                record["manual_final_review"]
                if isinstance(record.get("manual_final_review"), ManualFinalReviewProjection)
                else ManualFinalReviewProjection.from_record(record["manual_final_review"])
                if record.get("manual_final_review") is not None
                else None
            ),
            postmortem_warnings=tuple(record.get("postmortem_warnings") or ()),
        )


def draft_outcome(**kwargs: Any) -> OutcomeReport:
    """Build a non-terminal snapshot without changing lifecycle phase."""

    return OutcomeReport(kind="DRAFT", finalized=False, **kwargs)


def final_outcome(**kwargs: Any) -> OutcomeReport:
    """Build FINAL only when requirements, SHAs, and finish gates permit it."""

    return OutcomeReport(kind="FINAL", finalized=True, **kwargs)


def blocked_outcome(*, external_goal_blocked: bool, **kwargs: Any) -> OutcomeReport:
    """Build BLOCKED only after the caller authorizes the external goal state."""

    return OutcomeReport(
        kind="BLOCKED",
        finalized=True,
        external_goal_blocked=external_goal_blocked,
        **kwargs,
    )


def render_outcome_markdown(report: OutcomeReport) -> str:
    """Render one validated outcome model as the canonical human report."""

    if not isinstance(report, OutcomeReport):
        raise OutcomeModelError("report must be an OutcomeReport")
    title = {
        "DRAFT": "Outcome Draft",
        "FINAL": "Final Outcome",
        "BLOCKED": "Blocked Outcome",
    }[report.kind]
    lines = [
        f"# {title}",
        "",
        f"- Run: `{report.run_id}`",
        f"- Goal: {report.goal}",
        f"- Generated: `{report.generated_at}`",
        f"- Integration target: `{report.integration_target_sha or '-'}`",
        f"- Current branch SHA: `{report.current_branch_sha or '-'}`",
        f"- Finalized: `{str(report.finalized).lower()}`",
        "",
        "## Requirement Outcomes",
        "",
    ]
    if report.requirement_progress:
        for item in report.requirement_progress:
            detail = item.remaining_work or "; ".join(item.blockers) or "完了"
            lines.append(f"- `{item.requirement_id}`: `{item.status}` — {detail}")
    else:
        lines.append("- No accepted requirements were recorded.")

    lines.extend(["", "## User-visible Changes", ""])
    lines.extend(f"- {item}" for item in report.user_changes)
    if not report.user_changes:
        lines.append("- No user-visible change summary was recorded.")

    lines.extend(["", "## Validation and QA", ""])
    for gate in report.gates:
        refs = ", ".join(gate.evidence_refs) or "no evidence recorded"
        lines.append(f"- `{gate.name}`: `{gate.status}` — {refs}")
    if not report.gates:
        lines.append("- No gates were recorded.")

    lines.extend(["", "## Review", ""])
    lines.append(
        "- Confirmed findings: "
        + ("; ".join(report.review_findings) or "none")
    )
    lines.append("- Fixes: " + ("; ".join(report.review_fixes) or "none"))
    lines.append(
        "- Accepted risks: " + ("; ".join(report.accepted_risks) or "none")
    )

    lines.extend(["", "## Decisions and Unresolved Items", ""])
    lines.append("- Decisions: " + ("; ".join(report.decisions) or "none"))
    lines.append(
        "- Unresolved: " + ("; ".join(report.unresolved_items) or "none")
    )
    if report.blocking_reason:
        lines.append(f"- Blocking reason: {report.blocking_reason}")

    lines.extend(
        [
            "",
            "## Cleanup and Sessions",
            "",
            f"- Cleanup: {report.cleanup_status or 'not recorded'}",
            f"- Sessions: {report.session_status or 'not recorded'}",
            "",
            "## Next User Actions",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.next_user_actions)
    if not report.next_user_actions:
        lines.append("- No user action is required.")

    has_bounded_projection = any(
        value is not None
        for value in (
            report.manager_invocation,
            report.execution_metrics,
            report.follow_ups,
            report.review_convergence,
            report.manual_final_review,
        )
    )
    if has_bounded_projection:
        lines.extend(["", "## Bounded Review Convergence", ""])
        if report.manager_invocation is not None:
            invocation = report.manager_invocation
            identity = "/".join(
                (
                    invocation.provider or "unknown-provider",
                    invocation.model or "unknown-model",
                    invocation.reasoning_effort or "unknown-effort",
                )
            )
            lines.append(f"- Manager invocation: {identity}")
            if invocation.recorded_at:
                lines.append(f"- Manager invocation recorded at: `{invocation.recorded_at}`")
            if invocation.unavailable_reason:
                lines.append(
                    f"- Manager invocation unavailable reason: {invocation.unavailable_reason}"
                )
        if report.execution_metrics is not None:
            metrics = report.execution_metrics
            lines.append(
                f"- Tasks: {metrics.planned_task_count} planned, "
                f"{metrics.remediation_task_count} remediation"
            )
            lines.append(
                f"- Findings: {metrics.candidate_count} candidates, "
                f"{metrics.confirmed_count} confirmed"
            )
            dispositions = ", ".join(
                f"{key}={count}"
                for key, count in metrics.finding_disposition_counts.items()
            ) or "none"
            lines.append(f"- Finding dispositions: {dispositions}")
            lines.append(f"- Review fix rounds: {metrics.review_fix_rounds}")
        if report.review_convergence is not None:
            convergence = report.review_convergence
            lines.append(
                f"- Review convergence: {convergence.status}, "
                f"round {convergence.fix_round}/{convergence.max_fix_rounds}, "
                f"user-authorized extra {convergence.authorized_extra_rounds}"
            )
            if convergence.verified_actionable_findings is not None:
                lines.append(
                    "- Verified actionable findings: "
                    f"{convergence.verified_actionable_findings}"
                )
        if report.follow_ups is not None:
            follow_ups = report.follow_ups
            lines.append(f"- Follow-ups: {follow_ups.count}")
            if follow_ups.references:
                lines.append("- Follow-up references: " + ", ".join(follow_ups.references))
        if report.manual_final_review is not None:
            manual = report.manual_final_review
            completeness = "complete" if manual.complete else "incomplete"
            shortfalls = manual.shortfall_count
            lines.append(
                f"- Manual review completeness: {completeness}, "
                f"shortfalls {shortfalls if shortfalls is not None else 'not recorded'}"
            )
            lines.append(f"- Manual final status: {manual.status}")
            if manual.lane_count:
                lines.append(
                    f"- Manual final lanes: {manual.lane_completed_count}/{manual.lane_count}"
                )

        lines.extend(["", "## Postmortem Warnings", ""])
        lines.extend(f"- {item}" for item in report.postmortem_warnings)
        if not report.postmortem_warnings:
            lines.append("- None")
    return "\n".join(lines) + "\n"
