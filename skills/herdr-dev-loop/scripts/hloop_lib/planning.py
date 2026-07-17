"""Pure repository-planning contracts for HLoop 0.5.3.

The command-line runtime owns filesystem discovery, revisions, Scout
processes, and dispatch transactions.  This module owns the portable contract
between those operations: immutable planning identity, canonical artifact
digests, strict record shapes, coverage completeness, and task dependency
consistency.

All helpers are side-effect free.  A caller must pass the current identity
when checking dispatch readiness; accepting an artifact merely because its
own digest is internally consistent would allow stale PLAN, Requirement,
release-scope, or source evidence to be reused.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import fnmatch
import hashlib
import json
import re
from typing import Any


PLANNING_SCHEMA_REVISION = 1
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"

IMPACT_RECORD_TYPE = "repository_impact_map"
TASK_GRAPH_RECORD_TYPE = "task_risk_graph"
COVERAGE_RECORD_TYPE = "acceptance_coverage_ledger"
PLAN_GAP_RECORD_TYPE = "plan_gap"
PLANNING_RECORD_TYPES = frozenset(
    {
        IMPACT_RECORD_TYPE,
        TASK_GRAPH_RECORD_TYPE,
        COVERAGE_RECORD_TYPE,
        PLAN_GAP_RECORD_TYPE,
    }
)

RISK_CLASSES = frozenset({"mechanical", "normal", "high"})
REQUIRED_GATES = frozenset({"patch_review", "full_suite"})
TASK_OWNERS = frozenset({"worker", "manager"})
FINAL_EVIDENCE_OWNERS = frozenset({"manager", "reviewer", "gap"})
IMPACT_KINDS = frozenset(
    {
        "module",
        "entry_point",
        "public_api",
        "schema",
        "cli",
        "persistence",
        "generated_artifact",
        "migration",
        "compatibility",
        "operations",
        "docs",
        "fixture",
    }
)
PLAN_GAP_CATEGORIES = frozenset(
    {
        "unassigned_requirement",
        "unassigned_plan_item",
        "unassigned_caller",
        "unassigned_consumer",
        "unassigned_migration",
        "unassigned_docs",
        "unassigned_fixture",
        "unassigned_failure_path",
        "unassigned_test",
        "missing_final_evidence",
        "dependency_conflict",
        "shared_invariant_conflict",
        "write_scope_conflict",
        "scope_external",
    }
)

_DIGEST_RE = re.compile(DIGEST_PATTERN)
_TASK_ID_RE = re.compile(r"^T[0-9]{3,}$")

_IDENTITY_FIELDS = frozenset(
    {
        "planning_schema_revision",
        "run_id",
        "plan_revision",
        "requirements_revision",
        "release_scope_revision",
        "release_scope_digest",
        "source_revision",
        "source_digest",
    }
)
_COMMON_FIELDS = _IDENTITY_FIELDS | {"record_type", "artifact_digest"}
_TOP_LEVEL_FIELDS = {
    IMPACT_RECORD_TYPE: _COMMON_FIELDS
    | {"surfaces", "preserved_invariants", "non_goals", "shared_contracts"},
    TASK_GRAPH_RECORD_TYPE: _COMMON_FIELDS | {"tasks"},
    COVERAGE_RECORD_TYPE: _COMMON_FIELDS
    | {"impact_map_digest", "task_graph_digest", "entries"},
    PLAN_GAP_RECORD_TYPE: _COMMON_FIELDS
    | {
        "impact_map_digest",
        "task_graph_digest",
        "coverage_digest",
        "checker",
        "verdict",
        "findings",
    },
}

_SURFACE_FIELDS = frozenset(
    {
        "surface_id",
        "kind",
        "paths",
        "symbols",
        "entry_points",
        "callers",
        "consumers",
        "success_paths",
        "failure_paths",
        "test_refs",
        "fixture_refs",
        "validation_commands",
        "final_evidence_refs",
        "requirement_refs",
        "plan_item_refs",
        "scope_refs",
    }
)
_SHARED_CONTRACT_FIELDS = frozenset(
    {"contract_ref", "surface_refs", "invariant_refs"}
)
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "changes",
        "change_refs",
        "preserved_invariants",
        "write_allow",
        "non_goals",
        "acceptance",
        "regression_checks",
        "depends_on",
        "shared_surface_refs",
        "risk_class",
        "required_gates",
        "requirement_refs",
        "plan_item_refs",
        "scope_refs",
        "owner",
    }
)
_COVERAGE_ENTRY_FIELDS = frozenset(
    {
        "coverage_id",
        "acceptance_ref",
        "requirement_refs",
        "plan_item_refs",
        "task_refs",
        "surface_refs",
        "caller_refs",
        "consumer_refs",
        "failure_path_refs",
        "test_refs",
        "final_evidence_refs",
        "final_evidence_owner",
        "review_lane_refs",
    }
)
_CHECKER_FIELDS = frozenset(
    {"role_id", "role_kind", "mode", "provider", "model", "effort"}
)
_FINDING_FIELDS = frozenset(
    {"finding_id", "category", "refs", "message"}
)


class PlanningContractError(ValueError):
    """Raised when planning evidence is malformed or not dispatch-ready."""

    def __init__(self, issues: Sequence["PlanningIssue"] | str):
        if isinstance(issues, str):
            normalized = (PlanningIssue("invalid-contract", issues),)
        else:
            normalized = tuple(issues)
        self.issues = normalized
        super().__init__("; ".join(issue.message for issue in normalized))


@dataclass(frozen=True, slots=True)
class PlanningIssue:
    """One stable, machine-readable planning diagnostic."""

    code: str
    message: str
    field: str = ""
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanningValidation:
    """Non-throwing validation result used by planning and dispatch surfaces."""

    issues: tuple[PlanningIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def stale(self) -> bool:
        return any(
            issue.code in {"identity-mismatch", "reference-digest-mismatch"}
            for issue in self.issues
        )

    def require_ok(self) -> None:
        if self.issues:
            raise PlanningContractError(self.issues)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PlanningContractError(f"{field_name} must be a positive integer")
    return value


def _digest(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _DIGEST_RE.fullmatch(text):
        raise PlanningContractError(
            f"{field_name} must be a lowercase labelled SHA-256 digest"
        )
    return text


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanningContractError(f"{field_name} must be an object")
    return value


def _sequence(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PlanningContractError(f"{field_name} must be an array")
    return tuple(value)


def _text_tuple(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    values = tuple(
        _required_text(item, field_name) for item in _sequence(value, field_name)
    )
    if not allow_empty and not values:
        raise PlanningContractError(f"{field_name} must not be empty")
    if len(set(values)) != len(values):
        raise PlanningContractError(f"{field_name} must not contain duplicates")
    return values


def canonical_json(value: Any) -> str:
    """Serialize identity input deterministically and reject non-JSON values."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise PlanningContractError(
            f"value is not canonically JSON serializable: {exc}"
        ) from exc


def canonical_digest(value: Any) -> str:
    """Return a stable labelled digest for canonical JSON data."""

    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanningIdentity:
    """Identity shared by every artifact in one planning revision."""

    run_id: str
    plan_revision: int
    requirements_revision: int
    release_scope_revision: int
    release_scope_digest: str
    source_revision: int
    source_digest: str
    planning_schema_revision: int = PLANNING_SCHEMA_REVISION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        for field_name in (
            "plan_revision",
            "requirements_revision",
            "release_scope_revision",
            "source_revision",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), field_name),
            )
        if self.planning_schema_revision != PLANNING_SCHEMA_REVISION:
            raise PlanningContractError(
                "unsupported planning_schema_revision: "
                f"{self.planning_schema_revision!r}"
            )
        object.__setattr__(
            self,
            "release_scope_digest",
            _digest(self.release_scope_digest, "release_scope_digest"),
        )
        object.__setattr__(
            self, "source_digest", _digest(self.source_digest, "source_digest")
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "PlanningIdentity":
        return cls(
            planning_schema_revision=record.get("planning_schema_revision"),
            run_id=record.get("run_id"),
            plan_revision=record.get("plan_revision"),
            requirements_revision=record.get("requirements_revision"),
            release_scope_revision=record.get("release_scope_revision"),
            release_scope_digest=record.get("release_scope_digest"),
            source_revision=record.get("source_revision"),
            source_digest=record.get("source_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_schema_revision": self.planning_schema_revision,
            "run_id": self.run_id,
            "plan_revision": self.plan_revision,
            "requirements_revision": self.requirements_revision,
            "release_scope_revision": self.release_scope_revision,
            "release_scope_digest": self.release_scope_digest,
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
        }

    def stale_fields(self, record: Mapping[str, Any]) -> tuple[str, ...]:
        expected = self.to_dict()
        return tuple(
            field for field, value in expected.items() if record.get(field) != value
        )


def _coerce_identity(value: PlanningIdentity | Mapping[str, Any]) -> PlanningIdentity:
    if isinstance(value, PlanningIdentity):
        return value
    return PlanningIdentity.from_record(_mapping(value, "current_identity"))


def artifact_digest(record: Mapping[str, Any]) -> str:
    """Compute an artifact digest without trusting its stored digest."""

    payload = dict(_mapping(record, "artifact"))
    payload.pop("artifact_digest", None)
    return canonical_digest(payload)


def seal_planning_artifact(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a strict artifact copy carrying its canonical digest."""

    sealed = dict(_mapping(record, "artifact"))
    sealed.pop("artifact_digest", None)
    sealed["artifact_digest"] = artifact_digest(sealed)
    validation = validate_planning_artifact(sealed)
    validation.require_ok()
    return sealed


def _shape_issues(
    record: Mapping[str, Any], expected: frozenset[str], field_name: str
) -> list[PlanningIssue]:
    issues: list[PlanningIssue] = []
    missing = sorted(expected - set(record))
    unknown = sorted(set(record) - expected)
    if missing:
        issues.append(
            PlanningIssue(
                "missing-field",
                f"{field_name} is missing required fields: {', '.join(missing)}",
                field_name,
                tuple(missing),
            )
        )
    if unknown:
        issues.append(
            PlanningIssue(
                "unknown-field",
                f"{field_name} has unknown fields: {', '.join(unknown)}",
                field_name,
                tuple(unknown),
            )
        )
    return issues


def _issue_from_error(exc: PlanningContractError, field: str) -> PlanningIssue:
    return PlanningIssue("invalid-field", str(exc), field)


def _validate_object_array(
    record: Mapping[str, Any],
    field_name: str,
    expected_fields: frozenset[str],
    *,
    allow_empty: bool,
) -> tuple[tuple[Mapping[str, Any], ...], list[PlanningIssue]]:
    issues: list[PlanningIssue] = []
    try:
        raw_items = _sequence(record.get(field_name), field_name)
    except PlanningContractError as exc:
        return (), [_issue_from_error(exc, field_name)]
    if not allow_empty and not raw_items:
        issues.append(
            PlanningIssue(
                "invalid-field", f"{field_name} must not be empty", field_name
            )
        )
    items: list[Mapping[str, Any]] = []
    for index, raw in enumerate(raw_items):
        item_field = f"{field_name}[{index}]"
        try:
            item = _mapping(raw, item_field)
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, item_field))
            continue
        issues.extend(_shape_issues(item, expected_fields, item_field))
        items.append(item)
    return tuple(items), issues


def _validate_text_list_field(
    record: Mapping[str, Any],
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[tuple[str, ...], list[PlanningIssue]]:
    try:
        return (
            _text_tuple(record.get(field_name), field_name, allow_empty=allow_empty),
            [],
        )
    except PlanningContractError as exc:
        return (), [_issue_from_error(exc, field_name)]


def _validate_impact_map(record: Mapping[str, Any]) -> list[PlanningIssue]:
    issues: list[PlanningIssue] = []
    surfaces, item_issues = _validate_object_array(
        record, "surfaces", _SURFACE_FIELDS, allow_empty=False
    )
    issues.extend(item_issues)
    seen_surface_ids: set[str] = set()
    for index, surface in enumerate(surfaces):
        prefix = f"surfaces[{index}]"
        try:
            surface_id = _required_text(
                surface.get("surface_id"), f"{prefix}.surface_id"
            )
            if surface_id in seen_surface_ids:
                issues.append(
                    PlanningIssue(
                        "duplicate-reference",
                        f"duplicate surface_id: {surface_id}",
                        f"{prefix}.surface_id",
                        (surface_id,),
                    )
                )
            seen_surface_ids.add(surface_id)
            if surface.get("kind") not in IMPACT_KINDS:
                raise PlanningContractError(
                    f"{prefix}.kind must be one of: {', '.join(sorted(IMPACT_KINDS))}"
                )
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, prefix))
        for field_name in (
            "paths",
            "symbols",
            "entry_points",
            "callers",
            "consumers",
            "success_paths",
            "failure_paths",
            "test_refs",
            "fixture_refs",
            "validation_commands",
            "final_evidence_refs",
            "requirement_refs",
            "plan_item_refs",
            "scope_refs",
        ):
            _, list_issues = _validate_text_list_field(
                surface,
                field_name,
                allow_empty=field_name
                not in {"paths", "requirement_refs", "plan_item_refs", "scope_refs"},
            )
            issues.extend(
                PlanningIssue(issue.code, issue.message, f"{prefix}.{field_name}")
                for issue in list_issues
            )

    preserved_invariants: tuple[str, ...] = ()
    for field_name in ("preserved_invariants", "non_goals"):
        values, list_issues = _validate_text_list_field(
            record, field_name, allow_empty=field_name == "non_goals"
        )
        issues.extend(list_issues)
        if field_name == "preserved_invariants":
            preserved_invariants = values

    contracts, contract_issues = _validate_object_array(
        record, "shared_contracts", _SHARED_CONTRACT_FIELDS, allow_empty=True
    )
    issues.extend(contract_issues)
    seen_contracts: set[str] = set()
    for index, contract in enumerate(contracts):
        prefix = f"shared_contracts[{index}]"
        try:
            contract_ref = _required_text(
                contract.get("contract_ref"), f"{prefix}.contract_ref"
            )
            if contract_ref in seen_contracts:
                issues.append(
                    PlanningIssue(
                        "duplicate-reference",
                        f"duplicate contract_ref: {contract_ref}",
                        f"{prefix}.contract_ref",
                        (contract_ref,),
                    )
                )
            seen_contracts.add(contract_ref)
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, prefix))
        for field_name in ("surface_refs", "invariant_refs"):
            values, list_issues = _validate_text_list_field(
                contract, field_name, allow_empty=False
            )
            issues.extend(list_issues)
            if field_name == "surface_refs":
                unknown = sorted(set(values) - seen_surface_ids)
                if unknown:
                    issues.append(
                        PlanningIssue(
                            "unknown-reference",
                            "shared contract references unknown surfaces: "
                            + ", ".join(unknown),
                            f"{prefix}.surface_refs",
                            tuple(unknown),
                        )
                    )
            elif field_name == "invariant_refs":
                unknown = sorted(set(values) - set(preserved_invariants))
                if unknown:
                    issues.append(
                        PlanningIssue(
                            "unknown-reference",
                            "shared contract references unknown invariants: "
                            + ", ".join(unknown),
                            f"{prefix}.invariant_refs",
                            tuple(unknown),
                        )
                    )
    return issues


def _task_records(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = record.get("tasks", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _surface_records(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = record.get("surfaces", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _entry_records(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = record.get("entries", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _texts(record: Mapping[str, Any], field: str) -> tuple[str, ...]:
    raw = record.get(field, ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item.strip())


def _dependency_reachability(
    dependencies: Mapping[str, tuple[str, ...]], task_id: str
) -> frozenset[str]:
    reached: set[str] = set()
    pending = list(dependencies.get(task_id, ()))
    while pending:
        dependency = pending.pop()
        if dependency in reached:
            continue
        reached.add(dependency)
        pending.extend(dependencies.get(dependency, ()))
    return frozenset(reached)


def _has_glob_magic(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _normalize_write_pattern(pattern: str) -> str:
    normalized = pattern[2:] if pattern.startswith("./") else pattern
    return normalized.rstrip("/")


def _direct_write_overlap_paths(
    left_patterns: Iterable[str], right_patterns: Iterable[str]
) -> frozenset[str]:
    """Return overlaps decidable from exact and glob/exact write scopes."""

    overlaps: set[str] = set()
    for raw_left in left_patterns:
        left = _normalize_write_pattern(raw_left)
        if not left:
            continue
        left_is_glob = _has_glob_magic(left)
        for raw_right in right_patterns:
            right = _normalize_write_pattern(raw_right)
            if not right:
                continue
            right_is_glob = _has_glob_magic(right)
            if left == right:
                overlaps.add(left)
            elif not left_is_glob and not right_is_glob:
                if left.startswith(right + "/"):
                    overlaps.add(left)
                elif right.startswith(left + "/"):
                    overlaps.add(right)
            elif not left_is_glob and fnmatch.fnmatchcase(left, right):
                overlaps.add(left)
            elif not right_is_glob and fnmatch.fnmatchcase(right, left):
                overlaps.add(right)
    return frozenset(overlaps)


def _known_write_overlap_paths(
    left_patterns: Iterable[str],
    right_patterns: Iterable[str],
    known_paths: Iterable[str],
) -> frozenset[str]:
    """Return additional overlap witnesses supplied by concrete impact paths."""

    left = tuple(_normalize_write_pattern(pattern) for pattern in left_patterns)
    right = tuple(_normalize_write_pattern(pattern) for pattern in right_patterns)
    if _direct_write_overlap_paths(left, right):
        return frozenset()
    return frozenset(
        path
        for raw_path in known_paths
        if (path := _normalize_write_pattern(raw_path))
        and not _has_glob_magic(path)
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in left)
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in right)
    )


def _validate_task_graph(record: Mapping[str, Any]) -> list[PlanningIssue]:
    issues: list[PlanningIssue] = []
    tasks, item_issues = _validate_object_array(
        record, "tasks", _TASK_FIELDS, allow_empty=False
    )
    issues.extend(item_issues)
    by_id: dict[str, Mapping[str, Any]] = {}
    dependencies: dict[str, tuple[str, ...]] = {}
    for index, task in enumerate(tasks):
        prefix = f"tasks[{index}]"
        try:
            task_id = _required_text(task.get("task_id"), f"{prefix}.task_id")
            if not _TASK_ID_RE.fullmatch(task_id):
                raise PlanningContractError(
                    f"{prefix}.task_id must match ^T[0-9]{{3,}}$"
                )
            if task_id in by_id:
                issues.append(
                    PlanningIssue(
                        "duplicate-reference",
                        f"duplicate task_id: {task_id}",
                        f"{prefix}.task_id",
                        (task_id,),
                    )
                )
            by_id[task_id] = task
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, prefix))
            continue

        for field_name in (
            "changes",
            "change_refs",
            "preserved_invariants",
            "write_allow",
            "non_goals",
            "acceptance",
            "regression_checks",
            "depends_on",
            "shared_surface_refs",
            "required_gates",
            "requirement_refs",
            "plan_item_refs",
            "scope_refs",
        ):
            _, list_issues = _validate_text_list_field(
                task,
                field_name,
                allow_empty=field_name
                in {"non_goals", "depends_on", "shared_surface_refs", "required_gates"},
            )
            issues.extend(
                PlanningIssue(issue.code, issue.message, f"{prefix}.{field_name}")
                for issue in list_issues
            )
        if task.get("risk_class") not in RISK_CLASSES:
            issues.append(
                PlanningIssue(
                    "invalid-field",
                    f"{prefix}.risk_class must be mechanical, normal, or high",
                    f"{prefix}.risk_class",
                )
            )
        gates = set(_texts(task, "required_gates"))
        unknown_gates = sorted(gates - REQUIRED_GATES)
        if unknown_gates:
            issues.append(
                PlanningIssue(
                    "invalid-field",
                    "unsupported required gates: " + ", ".join(unknown_gates),
                    f"{prefix}.required_gates",
                    tuple(unknown_gates),
                )
            )
        if task.get("owner") not in TASK_OWNERS:
            issues.append(
                PlanningIssue(
                    "invalid-field",
                    f"{prefix}.owner must be worker or manager",
                    f"{prefix}.owner",
                )
            )
        changes = set(_texts(task, "changes"))
        non_goals = set(_texts(task, "non_goals"))
        contradictory = sorted(changes & non_goals)
        if contradictory:
            issues.append(
                PlanningIssue(
                    "change-is-non-goal",
                    f"{task_id} changes items declared as non-goals: "
                    + ", ".join(contradictory),
                    f"{prefix}.non_goals",
                    tuple(contradictory),
                )
            )
        dependencies[task_id] = _texts(task, "depends_on")

    known_ids = set(by_id)
    for task_id, task_dependencies in dependencies.items():
        if task_id in task_dependencies:
            issues.append(
                PlanningIssue(
                    "self-dependency",
                    f"{task_id} cannot depend on itself",
                    "depends_on",
                    (task_id,),
                )
            )
        unknown = sorted(set(task_dependencies) - known_ids)
        if unknown:
            issues.append(
                PlanningIssue(
                    "unknown-dependency",
                    f"{task_id} depends on unknown tasks: {', '.join(unknown)}",
                    "depends_on",
                    tuple(unknown),
                )
            )

    for task_id in sorted(known_ids):
        reached = _dependency_reachability(dependencies, task_id)
        if task_id in reached:
            cycle_refs = tuple(sorted({task_id} | set(reached)))
            issues.append(
                PlanningIssue(
                    "dependency-cycle",
                    f"task dependency cycle reaches {task_id}",
                    "depends_on",
                    cycle_refs,
                )
            )

    ordered_ids = sorted(known_ids)
    reachability = {
        task_id: _dependency_reachability(dependencies, task_id)
        for task_id in ordered_ids
    }
    for index, left_id in enumerate(ordered_ids):
        left = by_id[left_id]
        for right_id in ordered_ids[index + 1 :]:
            right = by_id[right_id]
            shared_changes = set(_texts(left, "change_refs")) & set(
                _texts(right, "change_refs")
            )
            shared_writes = _direct_write_overlap_paths(
                _texts(left, "write_allow"), _texts(right, "write_allow")
            )
            shared_declared = set(_texts(left, "shared_surface_refs")) & set(
                _texts(right, "shared_surface_refs")
            )
            conflicts = sorted(shared_changes | shared_writes | shared_declared)
            if not conflicts:
                continue
            ordered = (
                right_id in reachability[left_id]
                or left_id in reachability[right_id]
            )
            if not ordered:
                issues.append(
                    PlanningIssue(
                        "unordered-shared-change",
                        f"{left_id} and {right_id} share mutable planning surfaces "
                        "without a dependency order: "
                        + ", ".join(conflicts),
                        "tasks",
                        (left_id, right_id, *conflicts),
                    )
                )
    return issues


def _validate_coverage_shape(record: Mapping[str, Any]) -> list[PlanningIssue]:
    issues: list[PlanningIssue] = []
    for field_name in ("impact_map_digest", "task_graph_digest"):
        try:
            _digest(record.get(field_name), field_name)
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, field_name))

    entries, item_issues = _validate_object_array(
        record, "entries", _COVERAGE_ENTRY_FIELDS, allow_empty=False
    )
    issues.extend(item_issues)
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        for field_name in ("coverage_id", "acceptance_ref"):
            try:
                value = _required_text(entry.get(field_name), f"{prefix}.{field_name}")
                if field_name == "coverage_id":
                    if value in seen_ids:
                        issues.append(
                            PlanningIssue(
                                "duplicate-reference",
                                f"duplicate coverage_id: {value}",
                                f"{prefix}.coverage_id",
                                (value,),
                            )
                        )
                    seen_ids.add(value)
            except PlanningContractError as exc:
                issues.append(_issue_from_error(exc, f"{prefix}.{field_name}"))
        for field_name in (
            "requirement_refs",
            "plan_item_refs",
            "task_refs",
            "surface_refs",
            "caller_refs",
            "consumer_refs",
            "failure_path_refs",
            "test_refs",
            "final_evidence_refs",
            "review_lane_refs",
        ):
            _, list_issues = _validate_text_list_field(
                entry,
                field_name,
                allow_empty=field_name
                in {
                    "requirement_refs",
                    "plan_item_refs",
                    "caller_refs",
                    "consumer_refs",
                },
            )
            issues.extend(
                PlanningIssue(issue.code, issue.message, f"{prefix}.{field_name}")
                for issue in list_issues
            )
        if not _texts(entry, "requirement_refs") and not _texts(
            entry, "plan_item_refs"
        ):
            issues.append(
                PlanningIssue(
                    "invalid-field",
                    f"{prefix} must reference a requirement or PLAN item",
                    prefix,
                )
            )
        if entry.get("final_evidence_owner") not in FINAL_EVIDENCE_OWNERS:
            issues.append(
                PlanningIssue(
                    "invalid-field",
                    f"{prefix}.final_evidence_owner must be manager, reviewer, or gap",
                    f"{prefix}.final_evidence_owner",
                )
            )
    return issues


def _validate_plan_gap(record: Mapping[str, Any]) -> list[PlanningIssue]:
    issues: list[PlanningIssue] = []
    for field_name in (
        "impact_map_digest",
        "task_graph_digest",
        "coverage_digest",
    ):
        try:
            _digest(record.get(field_name), field_name)
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, field_name))

    try:
        checker = _mapping(record.get("checker"), "checker")
        issues.extend(_shape_issues(checker, _CHECKER_FIELDS, "checker"))
        expected = {
            "role_kind": "specification_scout",
            "mode": "coverage",
            "provider": "codex",
            "model": "gpt-5.6-sol",
            "effort": "xhigh",
        }
        try:
            _required_text(checker.get("role_id"), "checker.role_id")
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, "checker.role_id"))
        for field_name, value in expected.items():
            if checker.get(field_name) != value:
                issues.append(
                    PlanningIssue(
                        "invalid-checker-identity",
                        f"checker.{field_name} must be {value!r}",
                        f"checker.{field_name}",
                    )
                )
    except PlanningContractError as exc:
        issues.append(_issue_from_error(exc, "checker"))

    findings, finding_issues = _validate_object_array(
        record, "findings", _FINDING_FIELDS, allow_empty=True
    )
    issues.extend(finding_issues)
    seen_findings: set[str] = set()
    for index, finding in enumerate(findings):
        prefix = f"findings[{index}]"
        try:
            finding_id = _required_text(
                finding.get("finding_id"), f"{prefix}.finding_id"
            )
            if finding_id in seen_findings:
                issues.append(
                    PlanningIssue(
                        "duplicate-reference",
                        f"duplicate finding_id: {finding_id}",
                        f"{prefix}.finding_id",
                        (finding_id,),
                    )
                )
            seen_findings.add(finding_id)
            _required_text(finding.get("message"), f"{prefix}.message")
        except PlanningContractError as exc:
            issues.append(_issue_from_error(exc, prefix))
        category = finding.get("category")
        if category not in PLAN_GAP_CATEGORIES:
            issues.append(
                PlanningIssue(
                    "invalid-field",
                    f"unsupported Plan Gap category: {category!r}",
                    f"{prefix}.category",
                )
            )
        _, list_issues = _validate_text_list_field(
            finding, "refs", allow_empty=False
        )
        issues.extend(list_issues)

    verdict = record.get("verdict")
    if verdict not in {"clean", "gaps_found"}:
        issues.append(
            PlanningIssue(
                "invalid-field",
                "verdict must be clean or gaps_found",
                "verdict",
            )
        )
    elif verdict == "clean" and findings:
        issues.append(
            PlanningIssue(
                "verdict-finding-conflict",
                "a clean Plan Gap artifact cannot contain findings",
                "verdict",
            )
        )
    elif verdict == "gaps_found" and not findings:
        issues.append(
            PlanningIssue(
                "verdict-finding-conflict",
                "a gaps_found Plan Gap artifact must contain findings",
                "verdict",
            )
        )
    return issues


def validate_planning_artifact(
    record: Mapping[str, Any], *, expected_record_type: str | None = None
) -> PlanningValidation:
    """Validate one strict planning artifact, including its stored digest."""

    if not isinstance(record, Mapping):
        return PlanningValidation(
            (PlanningIssue("invalid-artifact", "planning artifact must be an object"),)
        )
    record_type = record.get("record_type")
    if record_type not in PLANNING_RECORD_TYPES:
        return PlanningValidation(
            (
                PlanningIssue(
                    "unsupported-record-type",
                    f"unsupported planning record_type: {record_type!r}",
                    "record_type",
                ),
            )
        )
    issues = _shape_issues(record, _TOP_LEVEL_FIELDS[record_type], "artifact")
    if expected_record_type is not None and record_type != expected_record_type:
        issues.append(
            PlanningIssue(
                "unexpected-record-type",
                f"expected {expected_record_type!r}, found {record_type!r}",
                "record_type",
            )
        )
    try:
        PlanningIdentity.from_record(record)
    except PlanningContractError as exc:
        issues.append(_issue_from_error(exc, "planning_identity"))

    stored_digest = record.get("artifact_digest")
    try:
        _digest(stored_digest, "artifact_digest")
        expected_digest = artifact_digest(record)
        if stored_digest != expected_digest:
            issues.append(
                PlanningIssue(
                    "artifact-digest-mismatch",
                    "artifact_digest does not match canonical artifact content",
                    "artifact_digest",
                )
            )
    except PlanningContractError as exc:
        issues.append(_issue_from_error(exc, "artifact_digest"))

    if record_type == IMPACT_RECORD_TYPE:
        issues.extend(_validate_impact_map(record))
    elif record_type == TASK_GRAPH_RECORD_TYPE:
        issues.extend(_validate_task_graph(record))
    elif record_type == COVERAGE_RECORD_TYPE:
        issues.extend(_validate_coverage_shape(record))
    elif record_type == PLAN_GAP_RECORD_TYPE:
        issues.extend(_validate_plan_gap(record))
    return PlanningValidation(tuple(issues))


def planning_staleness(
    records: Iterable[Mapping[str, Any]],
    current_identity: PlanningIdentity | Mapping[str, Any],
) -> PlanningValidation:
    """Report every artifact whose source or revision identity is stale."""

    try:
        current = _coerce_identity(current_identity)
    except PlanningContractError as exc:
        return PlanningValidation(
            (
                PlanningIssue(
                    "invalid-current-identity",
                    str(exc),
                    "current_identity",
                ),
            )
        )
    issues: list[PlanningIssue] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            issues.append(
                PlanningIssue(
                    "invalid-artifact",
                    f"planning artifact {index} must be an object",
                )
            )
            continue
        stale_fields = current.stale_fields(record)
        if stale_fields:
            record_type = str(record.get("record_type", f"artifact-{index}"))
            issues.append(
                PlanningIssue(
                    "identity-mismatch",
                    f"{record_type} is stale for current planning identity: "
                    + ", ".join(stale_fields),
                    "planning_identity",
                    stale_fields,
                )
            )
    return PlanningValidation(tuple(issues))


def is_planning_stale(
    records: Iterable[Mapping[str, Any]],
    current_identity: PlanningIdentity | Mapping[str, Any],
) -> bool:
    return not planning_staleness(records, current_identity).ok


def _unassigned_issue(
    code: str, label: str, values: set[str], covered: set[str]
) -> PlanningIssue | None:
    missing = tuple(sorted(values - covered))
    if not missing:
        return None
    return PlanningIssue(
        code,
        f"unassigned {label}: {', '.join(missing)}",
        "entries",
        missing,
    )


def validate_coverage(
    impact_map: Mapping[str, Any],
    task_graph: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    required_requirement_refs: Iterable[str] = (),
    required_plan_item_refs: Iterable[str] = (),
    allowed_scope_refs: Iterable[str] | None = None,
) -> PlanningValidation:
    """Validate requirement-to-task-to-evidence coverage fail-closed."""

    if not all(
        isinstance(record, Mapping) for record in (impact_map, task_graph, coverage)
    ):
        return PlanningValidation(
            (
                PlanningIssue(
                    "invalid-artifact",
                    "impact map, task graph, and coverage must all be objects",
                ),
            )
        )
    issues: list[PlanningIssue] = []
    surfaces = _surface_records(impact_map)
    tasks = _task_records(task_graph)
    entries = _entry_records(coverage)
    surface_by_id = {
        str(surface.get("surface_id")): surface
        for surface in surfaces
        if isinstance(surface.get("surface_id"), str)
    }
    task_by_id = {
        str(task.get("task_id")): task
        for task in tasks
        if isinstance(task.get("task_id"), str)
    }

    known_paths = {
        path
        for surface in surfaces
        for path in _texts(surface, "paths")
        if not _has_glob_magic(path)
    }
    dependencies = {
        task_id: _texts(task, "depends_on")
        for task_id, task in task_by_id.items()
    }
    reachability = {
        task_id: _dependency_reachability(dependencies, task_id)
        for task_id in task_by_id
    }
    ordered_task_ids = sorted(task_by_id)
    for index, left_id in enumerate(ordered_task_ids):
        left = task_by_id[left_id]
        for right_id in ordered_task_ids[index + 1 :]:
            right = task_by_id[right_id]
            overlaps = sorted(
                _known_write_overlap_paths(
                    _texts(left, "write_allow"),
                    _texts(right, "write_allow"),
                    known_paths,
                )
            )
            if not overlaps:
                continue
            ordered = (
                right_id in reachability[left_id]
                or left_id in reachability[right_id]
            )
            if not ordered:
                issues.append(
                    PlanningIssue(
                        "unordered-shared-change",
                        f"{left_id} and {right_id} share mutable planning surfaces "
                        "without a dependency order: "
                        + ", ".join(overlaps),
                        "tasks",
                        (left_id, right_id, *overlaps),
                    )
                )

    expected = {
        "requirement": set(required_requirement_refs),
        "plan_item": set(required_plan_item_refs),
        "surface": set(surface_by_id),
        "caller": set(),
        "consumer": set(),
        "failure_path": set(),
        "test": set(),
        "final_evidence": set(),
    }
    for surface in surfaces:
        expected["requirement"].update(_texts(surface, "requirement_refs"))
        expected["plan_item"].update(_texts(surface, "plan_item_refs"))
        expected["caller"].update(_texts(surface, "callers"))
        expected["consumer"].update(_texts(surface, "consumers"))
        expected["failure_path"].update(_texts(surface, "failure_paths"))
        expected["test"].update(_texts(surface, "test_refs"))
        expected["final_evidence"].update(_texts(surface, "final_evidence_refs"))
    for task in tasks:
        expected["requirement"].update(_texts(task, "requirement_refs"))
        expected["plan_item"].update(_texts(task, "plan_item_refs"))

    covered = {
        "requirement": set(),
        "plan_item": set(),
        "surface": set(),
        "caller": set(),
        "consumer": set(),
        "failure_path": set(),
        "test": set(),
        "final_evidence": set(),
    }
    entry_field = {
        "requirement": "requirement_refs",
        "plan_item": "plan_item_refs",
        "surface": "surface_refs",
        "caller": "caller_refs",
        "consumer": "consumer_refs",
        "failure_path": "failure_path_refs",
        "test": "test_refs",
        "final_evidence": "final_evidence_refs",
    }
    for entry in entries:
        for category, field_name in entry_field.items():
            covered[category].update(_texts(entry, field_name))

    issue_specs = (
        ("unassigned-requirement", "requirements", "requirement"),
        ("unassigned-plan-item", "PLAN items", "plan_item"),
        ("unassigned-surface", "impact surfaces", "surface"),
        ("unassigned-caller", "callers", "caller"),
        ("unassigned-consumer", "consumers", "consumer"),
        ("unassigned-failure-path", "failure paths", "failure_path"),
        ("unassigned-test", "tests", "test"),
        ("missing-final-evidence", "final evidence", "final_evidence"),
    )
    for code, label, category in issue_specs:
        issue = _unassigned_issue(
            code, label, expected[category], covered[category]
        )
        if issue is not None:
            issues.append(issue)

    known_sets = {
        "requirement": expected["requirement"],
        "plan_item": expected["plan_item"],
        "surface": set(surface_by_id),
        "caller": expected["caller"],
        "consumer": expected["consumer"],
        "failure_path": expected["failure_path"],
        "test": expected["test"],
        "final_evidence": expected["final_evidence"],
    }
    for category, known in known_sets.items():
        unknown = tuple(sorted(covered[category] - known))
        if unknown:
            issues.append(
                PlanningIssue(
                    "unknown-reference",
                    f"coverage claims unknown {category} refs: {', '.join(unknown)}",
                    entry_field[category],
                    unknown,
                )
            )

    known_task_ids = set(task_by_id)
    for index, entry in enumerate(entries):
        task_refs = set(_texts(entry, "task_refs"))
        unknown_tasks = tuple(sorted(task_refs - known_task_ids))
        if unknown_tasks:
            issues.append(
                PlanningIssue(
                    "unknown-reference",
                    "coverage references unknown tasks: "
                    + ", ".join(unknown_tasks),
                    f"entries[{index}].task_refs",
                    unknown_tasks,
                )
            )
        for category, field_name, issue_code in (
            ("requirement", "requirement_refs", "unassigned-requirement"),
            ("PLAN item", "plan_item_refs", "unassigned-plan-item"),
        ):
            for ref in _texts(entry, field_name):
                assigned = [
                    task_id
                    for task_id in task_refs & known_task_ids
                    if ref in _texts(task_by_id[task_id], field_name)
                ]
                if not assigned:
                    issues.append(
                        PlanningIssue(
                            issue_code,
                            f"{ref} has no task that declares its "
                            f"{category} assignment",
                            f"entries[{index}].task_refs",
                            (ref,),
                        )
                    )
        for surface_ref in _texts(entry, "surface_refs"):
            assigned = [
                task_id
                for task_id in task_refs & known_task_ids
                if surface_ref in _texts(task_by_id[task_id], "change_refs")
            ]
            if not assigned:
                issues.append(
                    PlanningIssue(
                        "unassigned-surface",
                        f"{surface_ref} has no assigned task that changes it",
                        f"entries[{index}].task_refs",
                        (surface_ref,),
                    )
                )

    known_surface_ids = set(surface_by_id)
    known_invariants = set(_texts(impact_map, "preserved_invariants"))
    for task_id, task in task_by_id.items():
        change_refs = set(_texts(task, "change_refs"))
        shared_surface_refs = set(_texts(task, "shared_surface_refs"))
        unknown_surfaces = tuple(
            sorted((change_refs | shared_surface_refs) - known_surface_ids)
        )
        if unknown_surfaces:
            issues.append(
                PlanningIssue(
                    "unknown-reference",
                    f"{task_id} references unknown surfaces: "
                    + ", ".join(unknown_surfaces),
                    "change_refs",
                    unknown_surfaces,
                )
            )
        unknown_invariants = tuple(
            sorted(set(_texts(task, "preserved_invariants")) - known_invariants)
        )
        if unknown_invariants:
            issues.append(
                PlanningIssue(
                    "unknown-reference",
                    f"{task_id} preserves unknown invariants: "
                    + ", ".join(unknown_invariants),
                    "preserved_invariants",
                    unknown_invariants,
                )
            )
        write_allow = _texts(task, "write_allow")
        for surface_id in sorted(change_refs & known_surface_ids):
            surface_paths = _texts(surface_by_id[surface_id], "paths")
            uncovered_paths = tuple(
                path
                for path in surface_paths
                if not any(
                    fnmatch.fnmatchcase(path, pattern) for pattern in write_allow
                )
            )
            if uncovered_paths:
                issues.append(
                    PlanningIssue(
                        "write-scope-missing",
                        f"{task_id} write_allow does not cover {surface_id}: "
                        + ", ".join(uncovered_paths),
                        "write_allow",
                        (task_id, surface_id, *uncovered_paths),
                    )
                )

    allowed = (
        set(allowed_scope_refs)
        if allowed_scope_refs is not None
        else {
            scope_ref
            for surface in surfaces
            for scope_ref in _texts(surface, "scope_refs")
        }
    )
    for task_id, task in task_by_id.items():
        external = tuple(sorted(set(_texts(task, "scope_refs")) - allowed))
        if external:
            issues.append(
                PlanningIssue(
                    "scope-external",
                    f"{task_id} includes scope-external refs: {', '.join(external)}",
                    "scope_refs",
                    (task_id, *external),
                )
            )
    return PlanningValidation(tuple(issues))


def validate_planning_bundle(
    impact_map: Mapping[str, Any],
    task_graph: Mapping[str, Any],
    coverage: Mapping[str, Any],
    plan_gap: Mapping[str, Any],
    current_identity: PlanningIdentity | Mapping[str, Any],
    *,
    required_requirement_refs: Iterable[str] = (),
    required_plan_item_refs: Iterable[str] = (),
    allowed_scope_refs: Iterable[str] | None = None,
) -> PlanningValidation:
    """Validate the complete pre-dispatch planning evidence bundle."""

    records = (impact_map, task_graph, coverage, plan_gap)
    expected_types = (
        IMPACT_RECORD_TYPE,
        TASK_GRAPH_RECORD_TYPE,
        COVERAGE_RECORD_TYPE,
        PLAN_GAP_RECORD_TYPE,
    )
    issues: list[PlanningIssue] = []
    for record, record_type in zip(records, expected_types, strict=True):
        issues.extend(
            validate_planning_artifact(
                record, expected_record_type=record_type
            ).issues
        )
    issues.extend(planning_staleness(records, current_identity).issues)
    if not all(isinstance(record, Mapping) for record in records):
        return PlanningValidation(tuple(issues))

    references = (
        (
            coverage,
            "impact_map_digest",
            impact_map.get("artifact_digest"),
        ),
        (
            coverage,
            "task_graph_digest",
            task_graph.get("artifact_digest"),
        ),
        (
            plan_gap,
            "impact_map_digest",
            impact_map.get("artifact_digest"),
        ),
        (
            plan_gap,
            "task_graph_digest",
            task_graph.get("artifact_digest"),
        ),
        (plan_gap, "coverage_digest", coverage.get("artifact_digest")),
    )
    for owner, field_name, expected_digest in references:
        if owner.get(field_name) != expected_digest:
            issues.append(
                PlanningIssue(
                    "reference-digest-mismatch",
                    f"{owner.get('record_type', 'artifact')}.{field_name} "
                    "does not identify the current dependency artifact",
                    field_name,
                )
            )

    issues.extend(
        validate_coverage(
            impact_map,
            task_graph,
            coverage,
            required_requirement_refs=required_requirement_refs,
            required_plan_item_refs=required_plan_item_refs,
            allowed_scope_refs=allowed_scope_refs,
        ).issues
    )
    if plan_gap.get("verdict") != "clean" or plan_gap.get("findings"):
        issues.append(
            PlanningIssue(
                "plan-gap-not-clean",
                "dispatch requires a clean Plan Gap artifact with no findings",
                "verdict",
            )
        )
    return PlanningValidation(tuple(issues))


def validate_dispatch_readiness(*args: Any, **kwargs: Any) -> PlanningValidation:
    """Named dispatch-gate alias used by the CLI integration layer."""

    return validate_planning_bundle(*args, **kwargs)


def assert_dispatch_ready(*args: Any, **kwargs: Any) -> None:
    """Raise with all diagnostics unless the planning bundle is dispatch-ready."""

    validate_dispatch_readiness(*args, **kwargs).require_ok()
