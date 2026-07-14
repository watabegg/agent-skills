"""Pure DRAFT, FINAL, and BLOCKED outcome report gates for HLoop 0.5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    if isinstance(values, (str, bytes)):
        raise OutcomeModelError(f"{field_name} must be a sequence of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if len(set(normalized)) != len(normalized):
        raise OutcomeModelError(f"{field_name} must not contain duplicates")
    return normalized


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

    def _validate_blocked(self) -> None:
        if not self.external_goal_blocked:
            raise OutcomeModelError(
                "BLOCKED outcome requires explicit external-goal blocked authorization"
            )
        _required_text(self.blocking_reason, "blocking_reason")
        if not any(gate.required and gate.status == "blocked" for gate in self.gates):
            raise OutcomeModelError("BLOCKED outcome requires a blocked required gate")

    def to_record(self) -> dict[str, Any]:
        return {
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
    return "\n".join(lines) + "\n"
