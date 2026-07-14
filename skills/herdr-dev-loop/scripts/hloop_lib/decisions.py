"""Pure specification-decision and scoped-dispatch primitives for HLoop 0.5.

This module deliberately performs no filesystem, process, or clock I/O.  It
models the canonical decision record and answers two scheduler questions:

* which queued tasks are waiting for an unresolved user decision; and
* whether decision-independent work still exists.

An unanswered decision never becomes a global stop merely because its class is
``blocking-user``.  The scheduler first applies the decision's task scope and
only reports a loop-level block after every remaining queued task is affected
and no other work is active.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping, Sequence


DECISION_ADVISORY = "advisory"
DECISION_DEFERRED_USER = "deferred-user"
DECISION_BLOCKING_USER = "blocking-user"

DECISION_PENDING = "pending"
DECISION_ANSWERED = "answered"
DECISION_ACCEPTED = "accepted"
DECISION_REJECTED = "rejected"
DECISION_SUPERSEDED = "superseded"

DECISION_CLASSES = frozenset(
    {DECISION_ADVISORY, DECISION_DEFERRED_USER, DECISION_BLOCKING_USER}
)
DECISION_STATUSES = frozenset(
    {
        DECISION_PENDING,
        DECISION_ANSWERED,
        DECISION_ACCEPTED,
        DECISION_REJECTED,
        DECISION_SUPERSEDED,
    }
)
UNRESOLVED_DECISION_STATUSES = frozenset({DECISION_PENDING, DECISION_ANSWERED})
RESOLVED_DECISION_STATUSES = frozenset(
    {DECISION_ACCEPTED, DECISION_REJECTED, DECISION_SUPERSEDED}
)
USER_DECISION_CLASSES = frozenset(
    {DECISION_DEFERRED_USER, DECISION_BLOCKING_USER}
)

SATISFIED_TASK_STATUSES = frozenset({"merged", "done"})
ACTIVE_TASK_STATUSES = frozenset({"running", "result_reported"})

_DECISION_ID = re.compile(r"^D[0-9]{3}$")
_TASK_ID = re.compile(r"^T[0-9]{3}$")
_OPTION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class DecisionValidationError(ValueError):
    """Raised when a decision record is internally inconsistent."""


class DecisionTransitionError(ValueError):
    """Raised when response, resolution, or reclassification is not allowed."""


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DecisionValidationError(f"{field_name} must not be empty")
    return text


def _text_tuple(
    values: Sequence[Any] | None,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if values is None:
        items: tuple[str, ...] = ()
    elif isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise DecisionValidationError(f"{field_name} must be a sequence")
    else:
        items = tuple(_required_text(item, field_name) for item in values)
    if not allow_empty and not items:
        raise DecisionValidationError(f"{field_name} must not be empty")
    if len(set(items)) != len(items):
        raise DecisionValidationError(f"{field_name} must contain unique values")
    return items


@dataclass(frozen=True, slots=True)
class DecisionOption:
    """One selectable option and its user-visible tradeoffs."""

    option_id: str
    label: str
    tradeoffs: tuple[str, ...]

    def __post_init__(self) -> None:
        option_id = _required_text(self.option_id, "option_id")
        if not _OPTION_ID.fullmatch(option_id):
            raise DecisionValidationError(f"invalid option_id: {option_id}")
        object.__setattr__(self, "option_id", option_id)
        object.__setattr__(self, "label", _required_text(self.label, "label"))
        object.__setattr__(
            self,
            "tradeoffs",
            _text_tuple(self.tradeoffs, "tradeoffs", allow_empty=False),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DecisionOption":
        return cls(
            option_id=record.get("id", record.get("option_id", "")),
            label=record.get("label", ""),
            tradeoffs=record.get("tradeoffs"),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.option_id,
            "label": self.label,
            "tradeoffs": list(self.tradeoffs),
        }


@dataclass(frozen=True, slots=True)
class DecisionRecommendation:
    """Manager or Scout recommendation tied to a concrete option."""

    option_id: str
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "option_id", _required_text(self.option_id, "recommendation.option_id")
        )
        object.__setattr__(
            self, "rationale", _required_text(self.rationale, "recommendation.rationale")
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DecisionRecommendation":
        return cls(
            option_id=record.get("option_id", ""),
            rationale=record.get("rationale", ""),
        )

    def to_record(self) -> dict[str, str]:
        return {"option_id": self.option_id, "rationale": self.rationale}


@dataclass(frozen=True, slots=True)
class DecisionResponse:
    """Canonical user response; supports an option, free text, or both."""

    responded_by: str
    responded_at: str
    selected_option: str = ""
    free_text: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "responded_by", _required_text(self.responded_by, "responded_by")
        )
        object.__setattr__(
            self, "responded_at", _required_text(self.responded_at, "responded_at")
        )
        selected = str(self.selected_option or "").strip()
        free_text = str(self.free_text or "").strip()
        if not selected and not free_text:
            raise DecisionValidationError(
                "a response must include selected_option or free_text"
            )
        object.__setattr__(self, "selected_option", selected)
        object.__setattr__(self, "free_text", free_text)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DecisionResponse":
        return cls(
            responded_by=record.get("responded_by", ""),
            responded_at=record.get("responded_at", ""),
            selected_option=record.get("selected_option", ""),
            free_text=record.get("free_text", ""),
        )

    def to_record(self) -> dict[str, str]:
        record = {
            "responded_by": self.responded_by,
            "responded_at": self.responded_at,
        }
        if self.selected_option:
            record["selected_option"] = self.selected_option
        if self.free_text:
            record["free_text"] = self.free_text
        return record


@dataclass(frozen=True, slots=True)
class DecisionResolution:
    """Manager-confirmed terminal resolution of a decision."""

    outcome: str
    rationale: str
    resolved_by: str
    resolved_at: str
    selected_option: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in RESOLVED_DECISION_STATUSES:
            raise DecisionValidationError(f"invalid resolution outcome: {self.outcome}")
        object.__setattr__(
            self, "rationale", _required_text(self.rationale, "resolution.rationale")
        )
        object.__setattr__(
            self, "resolved_by", _required_text(self.resolved_by, "resolution.resolved_by")
        )
        object.__setattr__(
            self, "resolved_at", _required_text(self.resolved_at, "resolution.resolved_at")
        )
        selected = str(self.selected_option or "").strip()
        if self.outcome == DECISION_ACCEPTED and not selected:
            raise DecisionValidationError(
                "an accepted resolution must include selected_option"
            )
        object.__setattr__(self, "selected_option", selected)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DecisionResolution":
        return cls(
            outcome=record.get("outcome", ""),
            rationale=record.get("rationale", ""),
            resolved_by=record.get("resolved_by", ""),
            resolved_at=record.get("resolved_at", ""),
            selected_option=record.get("selected_option", ""),
        )

    def to_record(self) -> dict[str, str]:
        record = {
            "outcome": self.outcome,
            "rationale": self.rationale,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
        }
        if self.selected_option:
            record["selected_option"] = self.selected_option
        return record


def _option(value: DecisionOption | Mapping[str, Any]) -> DecisionOption:
    return value if isinstance(value, DecisionOption) else DecisionOption.from_record(value)


def _recommendation(
    value: DecisionRecommendation | Mapping[str, Any],
) -> DecisionRecommendation:
    return (
        value
        if isinstance(value, DecisionRecommendation)
        else DecisionRecommendation.from_record(value)
    )


def _response(value: DecisionResponse | Mapping[str, Any]) -> DecisionResponse:
    return value if isinstance(value, DecisionResponse) else DecisionResponse.from_record(value)


def _resolution(
    value: DecisionResolution | Mapping[str, Any],
) -> DecisionResolution:
    return (
        value
        if isinstance(value, DecisionResolution)
        else DecisionResolution.from_record(value)
    )


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Canonical decision, including scope, response, and Manager resolution."""

    decision_id: str
    decision_class: str
    status: str
    question: str
    options: tuple[DecisionOption, ...]
    recommendation: DecisionRecommendation
    affected_task_ids: tuple[str, ...] = ()
    source_findings: tuple[str, ...] = ()
    response: DecisionResponse | None = None
    resolution: DecisionResolution | None = None
    created_from: str = ""

    def __post_init__(self) -> None:
        decision_id = _required_text(self.decision_id, "decision_id")
        if not _DECISION_ID.fullmatch(decision_id):
            raise DecisionValidationError(f"invalid decision_id: {decision_id}")
        if self.decision_class not in DECISION_CLASSES:
            raise DecisionValidationError(
                f"invalid decision class: {self.decision_class}"
            )
        if self.status not in DECISION_STATUSES:
            raise DecisionValidationError(f"invalid decision status: {self.status}")

        options = tuple(_option(option) for option in self.options)
        if not 2 <= len(options) <= 3:
            raise DecisionValidationError("a decision must contain two or three options")
        option_ids = tuple(option.option_id for option in options)
        if len(set(option_ids)) != len(option_ids):
            raise DecisionValidationError("decision option ids must be unique")

        recommendation = _recommendation(self.recommendation)
        if recommendation.option_id not in option_ids:
            raise DecisionValidationError(
                "recommendation.option_id must reference a decision option"
            )

        affected = _text_tuple(self.affected_task_ids, "affected_task_ids")
        invalid_task_ids = [task_id for task_id in affected if not _TASK_ID.fullmatch(task_id)]
        if invalid_task_ids:
            raise DecisionValidationError(
                f"invalid affected task id: {invalid_task_ids[0]}"
            )
        if self.decision_class in USER_DECISION_CLASSES and not affected:
            raise DecisionValidationError(
                "user decisions must name at least one affected task"
            )

        response = _response(self.response) if self.response is not None else None
        if response and response.selected_option not in {"", *option_ids}:
            raise DecisionValidationError(
                "response.selected_option must reference a decision option"
            )
        resolution = (
            _resolution(self.resolution) if self.resolution is not None else None
        )
        if resolution and resolution.selected_option not in {"", *option_ids}:
            raise DecisionValidationError(
                "resolution.selected_option must reference a decision option"
            )

        if self.status == DECISION_PENDING and (response or resolution):
            raise DecisionValidationError(
                "a pending decision cannot contain response or resolution"
            )
        if self.status == DECISION_ANSWERED and (response is None or resolution):
            raise DecisionValidationError(
                "an answered decision requires response and cannot contain resolution"
            )
        if self.status in RESOLVED_DECISION_STATUSES:
            if resolution is None or resolution.outcome != self.status:
                raise DecisionValidationError(
                    "a resolved decision requires a matching resolution outcome"
                )
            if (
                self.status == DECISION_ACCEPTED
                and self.decision_class in USER_DECISION_CLASSES
                and response is None
            ):
                raise DecisionValidationError(
                    "an accepted user decision requires a recorded response"
                )

        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "question", _required_text(self.question, "question"))
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "recommendation", recommendation)
        object.__setattr__(self, "affected_task_ids", affected)
        object.__setattr__(
            self, "source_findings", _text_tuple(self.source_findings, "source_findings")
        )
        object.__setattr__(self, "response", response)
        object.__setattr__(self, "resolution", resolution)
        object.__setattr__(self, "created_from", str(self.created_from or "").strip())

    @property
    def unresolved(self) -> bool:
        return self.status in UNRESOLVED_DECISION_STATUSES

    @property
    def blocks_affected_tasks(self) -> bool:
        return self.unresolved and self.decision_class in USER_DECISION_CLASSES

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DecisionRecord":
        raw_response = record.get("response")
        raw_resolution = record.get("resolution")
        return cls(
            decision_id=record.get("id", record.get("decision_id", "")),
            decision_class=record.get("class", record.get("decision_class", "")),
            status=record.get("status", ""),
            question=record.get("question", ""),
            options=tuple(_option(option) for option in record.get("options", ())),
            recommendation=_recommendation(record.get("recommendation", {})),
            affected_task_ids=record.get("affected_task_ids", ()),
            source_findings=record.get("source_findings", ()),
            response=_response(raw_response) if raw_response is not None else None,
            resolution=(
                _resolution(raw_resolution) if raw_resolution is not None else None
            ),
            created_from=record.get("created_from", ""),
        )

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.decision_id,
            "class": self.decision_class,
            "status": self.status,
            "question": self.question,
            "options": [option.to_record() for option in self.options],
            "recommendation": self.recommendation.to_record(),
            "affected_task_ids": list(self.affected_task_ids),
            "source_findings": list(self.source_findings),
        }
        if self.response is not None:
            record["response"] = self.response.to_record()
        if self.resolution is not None:
            record["resolution"] = self.resolution.to_record()
        if self.created_from:
            record["created_from"] = self.created_from
        return record


def _decision(value: DecisionRecord | Mapping[str, Any]) -> DecisionRecord:
    return value if isinstance(value, DecisionRecord) else DecisionRecord.from_record(value)


def reclassify_decision(
    decision: DecisionRecord | Mapping[str, Any], decision_class: str
) -> DecisionRecord:
    """Change a pending decision class after its progress scope is re-evaluated."""

    record = _decision(decision)
    if decision_class not in DECISION_CLASSES:
        raise DecisionTransitionError(f"invalid decision class: {decision_class}")
    if record.decision_class == decision_class:
        return record
    if record.status != DECISION_PENDING:
        raise DecisionTransitionError("only a pending decision can be reclassified")
    try:
        return replace(record, decision_class=decision_class)
    except DecisionValidationError as exc:
        raise DecisionTransitionError(str(exc)) from exc


def record_response(
    decision: DecisionRecord | Mapping[str, Any],
    response: DecisionResponse | Mapping[str, Any],
) -> DecisionRecord:
    """Attach one canonical response without treating it as Manager resolution."""

    record = _decision(decision)
    candidate = _response(response)
    if record.status == DECISION_ANSWERED and record.response == candidate:
        return record
    if record.status != DECISION_PENDING:
        raise DecisionTransitionError("only a pending decision can receive a response")
    try:
        return replace(record, status=DECISION_ANSWERED, response=candidate)
    except DecisionValidationError as exc:
        raise DecisionTransitionError(str(exc)) from exc


def resolve_decision(
    decision: DecisionRecord | Mapping[str, Any],
    *,
    outcome: str,
    rationale: str,
    resolved_by: str,
    resolved_at: str,
    selected_option: str = "",
) -> DecisionRecord:
    """Record a terminal Manager resolution, idempotently for the same result."""

    record = _decision(decision)
    selected = str(selected_option or "").strip()
    if not selected and record.response is not None:
        selected = record.response.selected_option
    try:
        resolution = DecisionResolution(
            outcome=outcome,
            rationale=rationale,
            resolved_by=resolved_by,
            resolved_at=resolved_at,
            selected_option=selected,
        )
    except DecisionValidationError as exc:
        raise DecisionTransitionError(str(exc)) from exc
    if record.status in RESOLVED_DECISION_STATUSES:
        if record.resolution == resolution:
            return record
        raise DecisionTransitionError("a resolved decision is immutable")
    try:
        return replace(record, status=outcome, resolution=resolution)
    except DecisionValidationError as exc:
        raise DecisionTransitionError(str(exc)) from exc


def _decision_map(
    decisions: Mapping[str, DecisionRecord | Mapping[str, Any]]
    | Sequence[DecisionRecord | Mapping[str, Any]],
) -> dict[str, DecisionRecord]:
    if isinstance(decisions, Mapping):
        items = decisions.items()
    elif isinstance(decisions, Sequence) and not isinstance(decisions, (str, bytes)):
        items = (("", value) for value in decisions)
    else:
        raise DecisionValidationError("decisions must be a mapping or sequence")
    normalized: dict[str, DecisionRecord] = {}
    for key, value in items:
        record = _decision(value)
        if key and str(key) != record.decision_id:
            raise DecisionValidationError(
                f"decision mapping key {key!r} does not match {record.decision_id}"
            )
        if record.decision_id in normalized:
            raise DecisionValidationError(
                f"duplicate decision id: {record.decision_id}"
            )
        normalized[record.decision_id] = record
    return normalized


@dataclass(frozen=True, slots=True)
class TaskDependencyEvaluation:
    """Decision and ordinary task dependencies for one queued task."""

    task_id: str
    pending_task_ids: tuple[str, ...] = ()
    blocking_decision_ids: tuple[str, ...] = ()
    unknown_decision_ids: tuple[str, ...] = ()

    @property
    def dispatchable(self) -> bool:
        return not (
            self.pending_task_ids
            or self.blocking_decision_ids
            or self.unknown_decision_ids
        )

    @property
    def blocked_by_decision(self) -> bool:
        return bool(self.blocking_decision_ids)


def _dependency_ids(task: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    task_ids: list[str] = []
    decision_ids: list[str] = []
    for value in _text_tuple(task.get("depends_on", ()), "depends_on"):
        (decision_ids if _DECISION_ID.fullmatch(value) else task_ids).append(value)
    for field_name in ("decision_dependencies", "depends_on_decisions"):
        for value in _text_tuple(task.get(field_name, ()), field_name):
            if value not in decision_ids:
                decision_ids.append(value)
    return tuple(task_ids), tuple(decision_ids)


def _blocking_decisions_for_task(
    task_id: str,
    tasks: Mapping[str, Mapping[str, Any]],
    decision_records: Mapping[str, DecisionRecord],
    visiting: frozenset[str] = frozenset(),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return direct and transitive user-decision dependencies for a task."""

    if task_id in visiting:
        return (), ()
    task = tasks.get(task_id, {})
    task_dependencies, explicit_decisions = _dependency_ids(task)
    applicable_decisions = list(explicit_decisions)
    for decision in decision_records.values():
        if (
            task_id in decision.affected_task_ids
            and decision.decision_id not in applicable_decisions
        ):
            applicable_decisions.append(decision.decision_id)

    unknown = [
        decision_id
        for decision_id in applicable_decisions
        if decision_id not in decision_records
    ]
    blocking = [
        decision_id
        for decision_id in applicable_decisions
        if decision_id in decision_records
        and decision_records[decision_id].blocks_affected_tasks
    ]
    next_visiting = visiting | {task_id}
    for dependency in task_dependencies:
        if (
            str(tasks.get(dependency, {}).get("status") or "")
            in SATISFIED_TASK_STATUSES
        ):
            continue
        inherited_blocking, inherited_unknown = _blocking_decisions_for_task(
            dependency, tasks, decision_records, next_visiting
        )
        for decision_id in inherited_blocking:
            if decision_id not in blocking:
                blocking.append(decision_id)
        for decision_id in inherited_unknown:
            if decision_id not in unknown:
                unknown.append(decision_id)
    return tuple(blocking), tuple(unknown)


def evaluate_task_dependencies(
    task_id: str,
    task: Mapping[str, Any],
    tasks: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, DecisionRecord | Mapping[str, Any]]
    | Sequence[DecisionRecord | Mapping[str, Any]],
) -> TaskDependencyEvaluation:
    """Evaluate explicit and decision-record-derived dependencies for one task."""

    task_id = _required_text(task_id, "task_id")
    decision_records = _decision_map(decisions)
    task_dependencies, _ = _dependency_ids(task)
    pending_tasks = tuple(
        dependency
        for dependency in task_dependencies
        if str(tasks.get(dependency, {}).get("status") or "")
        not in SATISFIED_TASK_STATUSES
    )

    task_records = dict(tasks)
    task_records[task_id] = task
    blocking, unknown = _blocking_decisions_for_task(
        task_id, task_records, decision_records
    )
    return TaskDependencyEvaluation(
        task_id=task_id,
        pending_task_ids=pending_tasks,
        blocking_decision_ids=blocking,
        unknown_decision_ids=unknown,
    )


@dataclass(frozen=True, slots=True)
class SchedulerDecision:
    """Decision-aware view consumed by the Worker scheduler."""

    evaluations: tuple[TaskDependencyEvaluation, ...]
    loop_blocked: bool

    @property
    def dispatchable_task_ids(self) -> tuple[str, ...]:
        return tuple(item.task_id for item in self.evaluations if item.dispatchable)

    @property
    def decision_blocked_task_ids(self) -> tuple[str, ...]:
        return tuple(
            item.task_id for item in self.evaluations if item.blocked_by_decision
        )

    @property
    def blocking_decision_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                decision_id
                for item in self.evaluations
                for decision_id in item.blocking_decision_ids
            )
        )


def decision_aware_dispatch(
    tasks: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, DecisionRecord | Mapping[str, Any]]
    | Sequence[DecisionRecord | Mapping[str, Any]],
    *,
    safe_work_remaining: bool = False,
) -> SchedulerDecision:
    """Return runnable queued tasks and the correctly scoped loop block signal.

    The helper is intentionally narrower than the full scheduler: write-scope
    overlap, capacity, review/gap gates, and process startup remain caller
    responsibilities.  ``loop_blocked`` becomes true only when every queued
    task is waiting on a user decision and no running/reported or caller-known
    safe work remains.
    """

    decision_records = _decision_map(decisions)
    evaluations = tuple(
        evaluate_task_dependencies(task_id, task, tasks, decision_records)
        for task_id, task in tasks.items()
        if str(task.get("status") or "") == "queued"
    )
    active_work = any(
        str(task.get("status") or "") in ACTIVE_TASK_STATUSES
        for task in tasks.values()
    )
    loop_blocked = bool(evaluations) and all(
        item.blocked_by_decision for item in evaluations
    )
    loop_blocked = loop_blocked and not active_work and not safe_work_remaining
    return SchedulerDecision(evaluations=evaluations, loop_blocked=loop_blocked)
