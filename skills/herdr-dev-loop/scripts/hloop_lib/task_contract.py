"""Pure versioned task and Worker-result contract primitives for HLoop 0.5.3.

Artifact contract revisions are deliberately independent from the enclosing
STATE schema revision.  Revision 2 denotes evidence produced under the 0.5.2
contract; revision 3 denotes the 0.5.3 contract with explicit risk, invariant,
regression, gate, and Worker self-review evidence.

This module performs no filesystem or process I/O.  Migration and runtime
callers can therefore classify legacy tasks, validate records, and build
updated copies before starting an atomic state/artifact transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


LEGACY_CONTRACT_SCHEMA_REVISION = 2
V053_CONTRACT_SCHEMA_REVISION = 3
SUPPORTED_CONTRACT_SCHEMA_REVISIONS = frozenset(
    {LEGACY_CONTRACT_SCHEMA_REVISION, V053_CONTRACT_SCHEMA_REVISION}
)

TASK_STATUSES = frozenset(
    {
        "queued",
        "running",
        "result_reported",
        "merged",
        "done",
        "partial",
        "blocked",
        "failed",
        "abandoned",
        "failed_validation",
    }
)
# Exhaustive 0.5.2 runtime and compatibility task outcomes: task
# creation/start/harvest, merge recovery and preflight blockers, generic
# abort/requeue, historical task-artifact outcomes, plus failed_validation,
# which worker start explicitly treats as retryable. Keep this separate from
# TASK_STATUSES, which is the task-artifact schema.
LEGACY_TASK_STATUS_ACTIONS = {
    "queued": "reclassify-to-revision-3",
    "running": "legacy-complete-or-rebind",
    "result_reported": "accept-legacy-result-or-add-gates",
    "merged": "preserve-history",
    "done": "preserve-history",
    "partial": "preserve-history",
    "blocked": "preserve-history",
    "failed": "preserve-history",
    "abandoned": "preserve-history",
    "aborted": "requeue-after-manager-recovery",
    "failed_validation": "retry-legacy-attempt",
    "blocked_merge_conflict": "resume-or-abort-legacy-merge",
    "blocked_environment": "resume-or-abort-legacy-merge",
    "blocked_head_mismatch": "requeue-after-manager-recovery",
    "blocked_base_mismatch": "requeue-after-manager-recovery",
    "blocked_write_scope": "requeue-after-manager-recovery",
}
LEGACY_RUNTIME_TASK_STATUSES = frozenset(LEGACY_TASK_STATUS_ACTIONS)
TASK_KINDS = frozenset({"implementation", "fix", "research"})
RISK_CLASSES = frozenset({"mechanical", "normal", "high"})
REQUIRED_GATES = frozenset({"patch_review", "full_suite"})
RESULT_STATUSES = frozenset({"done", "partial", "blocked", "failed", "abandoned"})
VALIDATION_RESULTS = frozenset({"passed", "failed", "blocked"})
PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
WORKER_PROTOCOLS = frozenset({"native", "codex-impl"})
AGENT_PROVIDERS = frozenset({"codex", "claude"})
WORKER_QA_PROFILES = frozenset(
    {"repo-default", "local", "staging", "preview", "none", "custom"}
)
TASK_ORIGINS = frozenset(
    {"planned", "finding", "user-amendment", "operational", "legacy-unclassified"}
)
FINDING_ORIGINS = frozenset(
    {
        "",
        "introduced",
        "diff-expanded-pre-existing",
        "unrelated-pre-existing",
        "unknown",
    }
)
CONTRACT_RELATIONS = frozenset({"", "in_scope", "outside_release", "ambiguous"})
DECISION_REQUIREMENTS = frozenset({"", "none", "spec", "user"})
RELEASE_EFFECTS = frozenset({"", "blocking", "non_blocking"})
FACT_STATUSES = frozenset({"", "confirmed", "refuted", "insufficient_evidence"})
DISPOSITIONS = frozenset(
    {
        "",
        "fix_now",
        "defer_follow_up",
        "disable_feature",
        "mark_experimental",
        "user_decision",
        "accepted_risk",
        "discard",
    }
)

V053_TASK_TOP_LEVEL_FIELDS = frozenset(
    {
        "id",
        "run_id",
        "skill_version",
        "contract_schema_revision",
        "kind",
        "status",
        "created_from",
        "branch",
        "base_ref",
        "base_sha",
        "priority",
        "severity",
        "batch_id",
        "depends_on",
        "write_allow",
        "write_deny",
        "acceptance",
        "validation_minimum",
        "worker_protocol",
        "worker_agent_provider",
        "worker_agent_model",
        "worker_agent_effort",
        "worker_qa_profile",
        "qa_profile",
        "preserved_invariants",
        "regression_checks",
        "risk_class",
        "required_gates",
        "investigation_goal",
        "implementation_ready_evidence",
        "exploration_budget_minutes",
        "history_search_allowed",
        "task_origin",
        "release_scope_revision",
        "plan_item_refs",
        "requirement_refs",
        "scope_refs",
        "source_finding",
        "authorization_input_id",
        "why_fix_now",
        "operational_reason",
        "origin",
        "contract_relation",
        "decision_requirement",
        "release_effect",
        "remediation_round",
        "fact_status",
        "disposition",
        "scope_expanding",
    }
)
V053_RESULT_TOP_LEVEL_FIELDS = frozenset(
    {
        "task_id",
        "run_id",
        "skill_version",
        "contract_schema_revision",
        "attempt_id",
        "status",
        "merge_ready",
        "branch",
        "head_sha",
        "base_sha",
        "changed_files",
        "validation_recorded",
        "validation_commands",
        "validation_results",
        "validation_summary",
        "blocking_questions",
        "handoff",
        "invariant_evidence",
        "regression_evidence",
        "self_review_summary",
        "residual_risks",
        "unrun_checks",
    }
)

LEGACY_QUEUED_BLOCKER = "risk-classification-required"
LEGACY_TASK_ACTIONS = frozenset(LEGACY_TASK_STATUS_ACTIONS.values())


class ContractValidationError(ValueError):
    """Raised when an artifact does not satisfy its declared contract revision."""

    def __init__(self, issues: Sequence["ContractIssue"]):
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


@dataclass(frozen=True, slots=True)
class ContractIssue:
    """A stable, machine-readable contract diagnostic."""

    code: str
    message: str
    field: str = ""


@dataclass(frozen=True, slots=True)
class ContractValidation:
    """Non-throwing validation result shared by read and mutation surfaces."""

    revision: int | None
    issues: tuple[ContractIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def raise_for_errors(self) -> None:
        if self.issues:
            raise ContractValidationError(self.issues)


@dataclass(frozen=True, slots=True)
class LegacyTaskMigration:
    """Status-sensitive projection for one 0.5.2 task.

    ``record`` is a deep-copied revision-2 record.  The booleans describe the
    gate that a later CLI integration must enforce; they do not themselves
    authorize a state transition.
    """

    record: Mapping[str, Any]
    status: str
    action: str
    contract_schema_revision: int = LEGACY_CONTRACT_SCHEMA_REVISION
    migration_blocker: str = ""
    may_finish_legacy_attempt: bool = False
    may_accept_legacy_result: bool = False
    requires_fresh_ack_on_rebind: bool = False
    may_start_new_attempt: bool = False
    may_merge_reported_result: bool = False
    may_resume_legacy_merge: bool = False
    requires_requeue_before_start: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "contract_schema_revision": self.contract_schema_revision,
            "action": self.action,
            "migration_blocker": self.migration_blocker,
            "may_finish_legacy_attempt": self.may_finish_legacy_attempt,
            "may_accept_legacy_result": self.may_accept_legacy_result,
            "requires_fresh_ack_on_rebind": self.requires_fresh_ack_on_rebind,
            "may_start_new_attempt": self.may_start_new_attempt,
            "may_merge_reported_result": self.may_merge_reported_result,
            "may_resume_legacy_merge": self.may_resume_legacy_merge,
            "requires_requeue_before_start": self.requires_requeue_before_start,
        }


def _issue(code: str, message: str, field: str = "") -> ContractIssue:
    return ContractIssue(code=code, message=message, field=field)


def _unknown_field_issues(
    record: Mapping[str, Any], allowed_fields: frozenset[str]
) -> list[ContractIssue]:
    return [
        _issue(
            "contract-field-unknown",
            f"unknown revision-3 top-level property: {field!r}",
            field if isinstance(field, str) else repr(field),
        )
        for field in record
        if field not in allowed_fields
    ]


def contract_schema_revision_of(record: Mapping[str, Any]) -> int:
    """Return the explicit artifact revision, rejecting missing/unknown values."""

    if not isinstance(record, Mapping):
        raise ContractValidationError(
            (_issue("contract-not-object", "contract must be an object"),)
        )
    value = record.get("contract_schema_revision")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(
            (
                _issue(
                    "contract-revision-invalid",
                    "contract_schema_revision must be an integer discriminator",
                    "contract_schema_revision",
                ),
            )
        )
    if value not in SUPPORTED_CONTRACT_SCHEMA_REVISIONS:
        raise ContractValidationError(
            (
                _issue(
                    "contract-revision-unsupported",
                    f"unsupported contract_schema_revision: {value}",
                    "contract_schema_revision",
                ),
            )
        )
    return value


def _required_text(record: Mapping[str, Any], field: str) -> ContractIssue | None:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        return _issue(
            "contract-field-invalid",
            f"{field} must be a non-empty string",
            field,
        )
    return None


def _string_list_issues(
    record: Mapping[str, Any],
    field: str,
    *,
    required: bool,
    min_items: int = 0,
    allowed: frozenset[str] | None = None,
    unique: bool = False,
) -> list[ContractIssue]:
    if field not in record:
        if required:
            return [
                _issue(
                    "contract-field-missing",
                    f"{field} is required",
                    field,
                )
            ]
        return []
    value = record.get(field)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return [
            _issue(
                "contract-field-invalid",
                f"{field} must be an array of strings",
                field,
            )
        ]
    items = list(value)
    issues: list[ContractIssue] = []
    if len(items) < min_items:
        issues.append(
            _issue(
                "contract-field-empty",
                f"{field} must contain at least {min_items} item(s)",
                field,
            )
        )
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            issues.append(
                _issue(
                    "contract-field-invalid",
                    f"{field} items must be non-empty strings",
                    field,
                )
            )
            continue
        normalized.append(item)
        if allowed is not None and item not in allowed:
            issues.append(
                _issue(
                    "contract-field-unsupported",
                    f"unsupported {field} value: {item!r}",
                    field,
                )
            )
    if unique and len(set(normalized)) != len(normalized):
        issues.append(
            _issue(
                "contract-field-duplicated",
                f"{field} must not contain duplicates",
                field,
            )
        )
    return issues


def _schema_string_issues(
    record: Mapping[str, Any],
    field: str,
    *,
    min_length: int = 0,
    pattern: str | None = None,
    allowed: frozenset[str] | None = None,
) -> list[ContractIssue]:
    """Mirror one canonical JSON Schema string property without I/O."""

    if field not in record:
        return []
    value = record.get(field)
    if not isinstance(value, str):
        return [
            _issue(
                "contract-field-invalid",
                f"{field} must be a string",
                field,
            )
        ]
    issues: list[ContractIssue] = []
    if len(value) < min_length:
        issues.append(
            _issue(
                "contract-field-invalid",
                f"{field} must contain at least {min_length} character(s)",
                field,
            )
        )
    if pattern is not None and re.fullmatch(pattern, value) is None:
        issues.append(
            _issue(
                "contract-field-invalid",
                f"{field} does not match the canonical pattern",
                field,
            )
        )
    if allowed is not None and value not in allowed:
        issues.append(
            _issue(
                "contract-field-unsupported",
                f"unsupported {field} value: {value!r}",
                field,
            )
        )
    return issues


def _schema_integer_issues(
    record: Mapping[str, Any], field: str, *, minimum: int
) -> list[ContractIssue]:
    if field not in record:
        return []
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return [
            _issue(
                "contract-field-invalid",
                f"{field} must be an integer >= {minimum}",
                field,
            )
        ]
    return []


def _schema_boolean_issues(
    record: Mapping[str, Any], field: str
) -> list[ContractIssue]:
    if field in record and not isinstance(record.get(field), bool):
        return [
            _issue(
                "contract-field-invalid",
                f"{field} must be a boolean",
                field,
            )
        ]
    return []


def _schema_string_array_issues(
    record: Mapping[str, Any],
    field: str,
    *,
    min_items: int = 0,
    item_min_length: int = 0,
    item_pattern: str | None = None,
    allowed: frozenset[str] | None = None,
    unique: bool = False,
) -> list[ContractIssue]:
    """Mirror an array-of-strings property from the canonical schema."""

    if field not in record:
        return []
    value = record.get(field)
    if not isinstance(value, list):
        return [
            _issue(
                "contract-field-invalid",
                f"{field} must be an array of strings",
                field,
            )
        ]
    issues: list[ContractIssue] = []
    if len(value) < min_items:
        issues.append(
            _issue(
                "contract-field-empty",
                f"{field} must contain at least {min_items} item(s)",
                field,
            )
        )
    all_strings = True
    for item in value:
        if not isinstance(item, str):
            all_strings = False
            issues.append(
                _issue(
                    "contract-field-invalid",
                    f"{field} items must be strings",
                    field,
                )
            )
            continue
        if len(item) < item_min_length:
            issues.append(
                _issue(
                    "contract-field-invalid",
                    f"{field} items must contain at least {item_min_length} character(s)",
                    field,
                )
            )
        if item_pattern is not None and re.fullmatch(item_pattern, item) is None:
            issues.append(
                _issue(
                    "contract-field-invalid",
                    f"{field} item does not match the canonical pattern",
                    field,
                )
            )
        if allowed is not None and item not in allowed:
            issues.append(
                _issue(
                    "contract-field-unsupported",
                    f"unsupported {field} value: {item!r}",
                    field,
                )
            )
    if unique and all_strings and len(set(value)) != len(value):
        issues.append(
            _issue(
                "contract-field-duplicated",
                f"{field} must not contain duplicates",
                field,
            )
        )
    return issues


def _validation_minimum_schema_issues(
    record: Mapping[str, Any],
) -> list[ContractIssue]:
    field = "validation_minimum"
    if field not in record:
        return []
    value = record.get(field)
    if isinstance(value, str) and len(value) >= 1:
        return []
    if (
        isinstance(value, list)
        and len(value) >= 1
        and all(isinstance(item, str) and len(item) >= 1 for item in value)
    ):
        return []
    return [
        _issue(
            "contract-field-invalid",
            "validation_minimum must be a non-empty string or non-empty array of non-empty strings",
            field,
        )
    ]


def _revision_three_task_schema_issues(
    record: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate every value constraint in task.schema.json revision 3."""

    issues: list[ContractIssue] = []
    string_specs = (
        ("id", 1, r"T[0-9]{3}", None),
        ("run_id", 1, None, None),
        ("skill_version", 1, None, None),
        ("kind", 0, None, TASK_KINDS),
        ("status", 0, None, TASK_STATUSES),
        ("created_from", 0, None, None),
        ("branch", 1, None, None),
        ("base_ref", 1, None, None),
        ("base_sha", 1, None, None),
        ("priority", 0, None, PRIORITIES),
        ("severity", 0, None, PRIORITIES),
        ("batch_id", 0, r"B[0-9]{3}", None),
        ("worker_protocol", 0, None, WORKER_PROTOCOLS),
        ("worker_agent_provider", 0, None, AGENT_PROVIDERS),
        ("worker_agent_model", 1, None, None),
        ("worker_agent_effort", 1, None, None),
        ("worker_qa_profile", 0, None, WORKER_QA_PROFILES),
        ("qa_profile", 0, None, WORKER_QA_PROFILES),
        ("risk_class", 0, None, RISK_CLASSES),
        ("investigation_goal", 1, None, None),
        ("task_origin", 0, None, TASK_ORIGINS),
        ("source_finding", 0, None, None),
        ("authorization_input_id", 0, r"(?:|U[0-9]{4})", None),
        ("why_fix_now", 0, None, None),
        ("operational_reason", 0, None, None),
        ("origin", 0, None, FINDING_ORIGINS),
        ("contract_relation", 0, None, CONTRACT_RELATIONS),
        ("decision_requirement", 0, None, DECISION_REQUIREMENTS),
        ("release_effect", 0, None, RELEASE_EFFECTS),
        ("fact_status", 0, None, FACT_STATUSES),
        ("disposition", 0, None, DISPOSITIONS),
    )
    for field, min_length, pattern, allowed in string_specs:
        issues.extend(
            _schema_string_issues(
                record,
                field,
                min_length=min_length,
                pattern=pattern,
                allowed=allowed,
            )
        )

    array_specs = (
        ("depends_on", 0, 0, r"T[0-9]{3}", None, True),
        ("write_allow", 0, 1, None, None, False),
        ("write_deny", 0, 1, None, None, False),
        ("acceptance", 1, 1, None, None, False),
        ("preserved_invariants", 1, 1, None, None, False),
        ("regression_checks", 1, 1, None, None, False),
        ("required_gates", 0, 0, None, REQUIRED_GATES, True),
        ("implementation_ready_evidence", 0, 1, None, None, False),
        ("plan_item_refs", 0, 0, None, None, True),
        ("requirement_refs", 0, 0, None, None, True),
        ("scope_refs", 0, 0, None, None, True),
    )
    for field, min_items, item_min_length, pattern, allowed, unique in array_specs:
        issues.extend(
            _schema_string_array_issues(
                record,
                field,
                min_items=min_items,
                item_min_length=item_min_length,
                item_pattern=pattern,
                allowed=allowed,
                unique=unique,
            )
        )

    issues.extend(_validation_minimum_schema_issues(record))
    for field, minimum in (
        ("exploration_budget_minutes", 1),
        ("release_scope_revision", 0),
        ("remediation_round", 0),
    ):
        issues.extend(_schema_integer_issues(record, field, minimum=minimum))
    for field in ("history_search_allowed", "scope_expanding"):
        issues.extend(_schema_boolean_issues(record, field))
    return issues


def _revision_three_result_schema_issues(
    record: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate every value constraint in result.schema.json revision 3."""

    issues: list[ContractIssue] = []
    for field, min_length, pattern in (
        ("task_id", 1, r"T[0-9]{3}"),
        ("run_id", 1, None),
        ("skill_version", 1, None),
        ("attempt_id", 1, r"T[0-9]{3}-A[0-9]{3}"),
        ("branch", 1, None),
        ("head_sha", 1, None),
        ("base_sha", 1, None),
        ("validation_summary", 0, None),
        ("self_review_summary", 1, None),
    ):
        issues.extend(
            _schema_string_issues(
                record, field, min_length=min_length, pattern=pattern
            )
        )
    issues.extend(_schema_string_issues(record, "status", allowed=RESULT_STATUSES))
    for field in ("merge_ready", "validation_recorded", "handoff"):
        issues.extend(_schema_boolean_issues(record, field))
    for field, min_items, item_min_length, allowed in (
        ("changed_files", 0, 0, None),
        ("validation_commands", 0, 1, None),
        ("validation_results", 0, 0, VALIDATION_RESULTS),
        ("blocking_questions", 0, 0, None),
        ("invariant_evidence", 1 if record.get("status") == "done" else 0, 1, None),
        ("regression_evidence", 1 if record.get("status") == "done" else 0, 1, None),
        ("residual_risks", 0, 1, None),
        ("unrun_checks", 0, 1, None),
    ):
        issues.extend(
            _schema_string_array_issues(
                record,
                field,
                min_items=min_items,
                item_min_length=item_min_length,
                allowed=allowed,
            )
        )
    return issues


def _revision_issues(record: Mapping[str, Any]) -> tuple[int | None, list[ContractIssue]]:
    try:
        return contract_schema_revision_of(record), []
    except ContractValidationError as exc:
        return None, list(exc.issues)


def validate_task_contract(record: Mapping[str, Any]) -> ContractValidation:
    """Validate common task fields and revision-specific 0.5.3 gates."""

    if not isinstance(record, Mapping):
        return ContractValidation(
            None, (_issue("contract-not-object", "task contract must be an object"),)
        )
    revision, issues = _revision_issues(record)
    for field in ("id", "run_id", "skill_version", "branch", "base_ref", "base_sha"):
        problem = _required_text(record, field)
        if problem:
            issues.append(problem)

    kind = record.get("kind")
    if not isinstance(kind, str) or kind not in TASK_KINDS:
        issues.append(
            _issue("contract-field-unsupported", f"unsupported task kind: {kind!r}", "kind")
        )
    status = record.get("status")
    if not isinstance(status, str) or status not in TASK_STATUSES:
        issues.append(
            _issue(
                "contract-field-unsupported",
                f"unsupported task status: {status!r}",
                "status",
            )
        )
    issues.extend(
        _string_list_issues(
            record,
            "write_allow",
            required=True,
            min_items=1 if kind == "implementation" or kind == "fix" else 0,
        )
    )
    issues.extend(
        _string_list_issues(record, "acceptance", required=True, min_items=1)
    )

    if revision == V053_CONTRACT_SCHEMA_REVISION:
        issues.extend(_unknown_field_issues(record, V053_TASK_TOP_LEVEL_FIELDS))
        issues.extend(_revision_three_task_schema_issues(record))
        issues.extend(
            _string_list_issues(
                record, "preserved_invariants", required=True, min_items=1
            )
        )
        issues.extend(
            _string_list_issues(record, "regression_checks", required=True, min_items=1)
        )
        risk_class = record.get("risk_class")
        if not isinstance(risk_class, str) or risk_class not in RISK_CLASSES:
            issues.append(
                _issue(
                    "contract-field-unsupported",
                    f"unsupported risk_class: {risk_class!r}",
                    "risk_class",
                )
            )
        issues.extend(
            _string_list_issues(
                record,
                "required_gates",
                required=True,
                allowed=REQUIRED_GATES,
                unique=True,
            )
        )
        problem = _required_text(record, "worker_agent_effort")
        if problem:
            issues.append(problem)
        migration_blocker = record.get("migration_blocker")
        if migration_blocker is not None and migration_blocker != "":
            issues.append(
                _issue(
                    "revision-3-legacy-blocker",
                    "revision 3 task contracts must clear migration_blocker",
                    "migration_blocker",
                )
            )
        for field in ("investigation_goal",):
            if field in record:
                problem = _required_text(record, field)
                if problem:
                    issues.append(problem)
        issues.extend(
            _string_list_issues(
                record, "implementation_ready_evidence", required=False
            )
        )
        if "exploration_budget_minutes" in record:
            budget = record.get("exploration_budget_minutes")
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
                issues.append(
                    _issue(
                        "contract-field-invalid",
                        "exploration_budget_minutes must be a positive integer",
                        "exploration_budget_minutes",
                    )
                )
        if "history_search_allowed" in record and not isinstance(
            record.get("history_search_allowed"), bool
        ):
            issues.append(
                _issue(
                    "contract-field-invalid",
                    "history_search_allowed must be a boolean",
                    "history_search_allowed",
                )
            )

    return ContractValidation(revision=revision, issues=tuple(issues))


def validate_task_state_projection(record: Mapping[str, Any]) -> ContractValidation:
    """Strictly validate the revision-3 contract fields copied into STATE.

    Runtime task state deliberately contains lifecycle, pane, candidate, and
    cleanup fields that are not part of the immutable task artifact.  A mixed
    schema-3.2 namespace can therefore validate the embedded revision-3
    discriminator and every required contract projection without pretending
    the runtime record itself is a task artifact or accepting unknown future
    contract revisions.
    """

    if not isinstance(record, Mapping):
        return ContractValidation(
            None, (_issue("contract-not-object", "task state must be an object"),)
        )
    revision, issues = _revision_issues(record)
    if revision != V053_CONTRACT_SCHEMA_REVISION:
        if revision is not None:
            issues.append(
                _issue(
                    "state-task-revision-invalid",
                    "task state projection must declare contract_schema_revision 3",
                    "contract_schema_revision",
                )
            )
        return ContractValidation(revision=revision, issues=tuple(issues))

    # Validate every contract field that survives in the runtime projection.
    # Runtime-only lifecycle fields remain outside the artifact schema and are
    # intentionally ignored here.
    artifact_fields = {key: value for key, value in record.items() if key != "status"}
    issues.extend(_revision_three_task_schema_issues(artifact_fields))
    status = record.get("status")
    if not isinstance(status, str) or status not in LEGACY_RUNTIME_TASK_STATUSES:
        issues.append(
            _issue(
                "contract-field-unsupported",
                f"unsupported task state status: {status!r}",
                "status",
            )
        )
    task_contract_digest = record.get("task_contract_digest")
    if not isinstance(task_contract_digest, str) or re.fullmatch(
        r"[0-9a-f]{64}", task_contract_digest
    ) is None:
        issues.append(
            _issue(
                "contract-field-invalid",
                "task_contract_digest must be a canonical raw SHA-256 digest",
                "task_contract_digest",
            )
        )
    issues.extend(
        _string_list_issues(
            record, "preserved_invariants", required=True, min_items=1
        )
    )
    issues.extend(
        _string_list_issues(record, "regression_checks", required=True, min_items=1)
    )
    risk_class = record.get("risk_class")
    if not isinstance(risk_class, str) or risk_class not in RISK_CLASSES:
        issues.append(
            _issue(
                "contract-field-unsupported",
                f"unsupported risk_class: {risk_class!r}",
                "risk_class",
            )
        )
    issues.extend(
        _string_list_issues(
            record,
            "required_gates",
            required=True,
            allowed=REQUIRED_GATES,
            unique=True,
        )
    )
    problem = _required_text(record, "worker_agent_effort")
    if problem:
        issues.append(problem)
    if record.get("migration_blocker") not in {None, ""}:
        issues.append(
            _issue(
                "revision-3-legacy-blocker",
                "revision 3 task state must clear migration_blocker",
                "migration_blocker",
            )
        )
    return ContractValidation(revision=revision, issues=tuple(issues))


def validate_result_contract(record: Mapping[str, Any]) -> ContractValidation:
    """Validate common result fields and revision-3 completion evidence."""

    if not isinstance(record, Mapping):
        return ContractValidation(
            None, (_issue("contract-not-object", "result contract must be an object"),)
        )
    revision, issues = _revision_issues(record)
    for field in (
        "task_id",
        "run_id",
        "skill_version",
        "attempt_id",
        "branch",
        "head_sha",
        "base_sha",
    ):
        problem = _required_text(record, field)
        if problem:
            issues.append(problem)
    status = record.get("status")
    if not isinstance(status, str) or status not in RESULT_STATUSES:
        issues.append(
            _issue(
                "contract-field-unsupported",
                f"unsupported result status: {status!r}",
                "status",
            )
        )
    if not isinstance(record.get("merge_ready"), bool):
        issues.append(
            _issue(
                "contract-field-invalid",
                "merge_ready must be a boolean",
                "merge_ready",
            )
        )
    if not isinstance(record.get("validation_recorded"), bool):
        issues.append(
            _issue(
                "contract-field-invalid",
                "validation_recorded must be a boolean",
                "validation_recorded",
            )
        )
    issues.extend(
        _string_list_issues(record, "changed_files", required=True)
    )
    issues.extend(
        _string_list_issues(record, "blocking_questions", required=True)
    )
    issues.extend(
        _string_list_issues(record, "validation_commands", required=False)
    )
    issues.extend(
        _string_list_issues(
            record,
            "validation_results",
            required=False,
            allowed=VALIDATION_RESULTS,
        )
    )
    commands = record.get("validation_commands", [])
    results = record.get("validation_results", [])
    if (
        isinstance(commands, Sequence)
        and not isinstance(commands, (str, bytes))
        and isinstance(results, Sequence)
        and not isinstance(results, (str, bytes))
        and len(commands) != len(results)
    ):
        issues.append(
            _issue(
                "validation-evidence-misaligned",
                "validation_commands and validation_results must have equal lengths",
                "validation_results",
            )
        )
    commands_present = (
        isinstance(commands, Sequence)
        and not isinstance(commands, (str, bytes))
        and bool(commands)
    )
    results_present = (
        isinstance(results, Sequence)
        and not isinstance(results, (str, bytes))
        and bool(results)
    )
    if record.get("validation_recorded") is True and not (
        commands_present and results_present
    ):
        issues.append(
            _issue(
                "validation-evidence-missing",
                "validation_recorded requires commands and results",
                "validation_recorded",
            )
        )
    if record.get("validation_recorded") is False and (
        commands_present or results_present
    ):
        issues.append(
            _issue(
                "validation-evidence-unrecorded",
                "validation evidence must be empty when validation_recorded is false",
                "validation_recorded",
            )
        )
    if record.get("merge_ready") is True:
        if status != "done":
            issues.append(
                _issue(
                    "merge-ready-status-invalid",
                    "merge_ready requires status done",
                    "status",
                )
            )
        if record.get("validation_recorded") is not True or not commands:
            issues.append(
                _issue(
                    "merge-ready-validation-missing",
                    "merge_ready requires recorded validation commands",
                    "validation_recorded",
                )
            )
        if not results_present or any(result != "passed" for result in results):
            issues.append(
                _issue(
                    "merge-ready-validation-failed",
                    "merge_ready requires every validation result to pass",
                    "validation_results",
                )
            )

    if revision == V053_CONTRACT_SCHEMA_REVISION:
        issues.extend(_unknown_field_issues(record, V053_RESULT_TOP_LEVEL_FIELDS))
        issues.extend(_revision_three_result_schema_issues(record))
        completion_evidence_items = 1 if status == "done" else 0
        issues.extend(
            _string_list_issues(
                record,
                "invariant_evidence",
                required=True,
                min_items=completion_evidence_items,
            )
        )
        issues.extend(
            _string_list_issues(
                record,
                "regression_evidence",
                required=True,
                min_items=completion_evidence_items,
            )
        )
        problem = _required_text(record, "self_review_summary")
        if problem:
            issues.append(problem)
        issues.extend(
            _string_list_issues(record, "residual_risks", required=True)
        )
        issues.extend(_string_list_issues(record, "unrun_checks", required=True))

    return ContractValidation(revision=revision, issues=tuple(issues))


def migrate_legacy_task_contract(
    record: Mapping[str, Any],
    *,
    record_type: str | None = None,
) -> LegacyTaskMigration:
    """Return a revision-2 copy and the required status-sensitive migration gate.

    The function is idempotent for an already-labeled revision-2 record and
    validates revision 3 according to explicit caller provenance.  It never
    upgrades a legacy task automatically, because doing so would retroactively
    apply new gates or bypass the fresh semantic ACK required for a
    running-task rebind.
    """

    if not isinstance(record, Mapping):
        raise ContractValidationError(
            (_issue("contract-not-object", "legacy task must be an object"),)
        )
    existing_revision = record.get("contract_schema_revision")
    if existing_revision == V053_CONTRACT_SCHEMA_REVISION:
        if record_type not in {"artifact", "state"}:
            raise ContractValidationError(
                (
                    _issue(
                        "task-record-type-required",
                        "revision 3 task migration requires explicit artifact or state provenance",
                        "record_type",
                    ),
                )
            )
        validation_record = deepcopy(dict(record))
        if record_type == "artifact":
            for field in ("release_scope_revision", "remediation_round"):
                value = validation_record.get(field)
                if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
                    validation_record[field] = int(value)
        validation = (
            validate_task_contract(validation_record)
            if record_type == "artifact"
            else validate_task_state_projection(validation_record)
        )
        validation.raise_for_errors()
        return LegacyTaskMigration(
            record=deepcopy(dict(record)),
            status=str(record.get("status") or ""),
            action="preserve-revision-3",
            contract_schema_revision=V053_CONTRACT_SCHEMA_REVISION,
        )
    if existing_revision is not None and existing_revision != LEGACY_CONTRACT_SCHEMA_REVISION:
        raise ContractValidationError(
            (
                _issue(
                    "legacy-contract-revision-conflict",
                    "0.5.2 task migration accepts only an unlabeled or revision-2 record",
                    "contract_schema_revision",
                ),
            )
        )
    status = record.get("status")
    action = (
        LEGACY_TASK_STATUS_ACTIONS.get(status) if isinstance(status, str) else None
    )
    if action is None:
        raise ContractValidationError(
            (
                _issue(
                    "legacy-task-status-unsupported",
                    f"unsupported legacy task status: {status!r}",
                    "status",
                ),
            )
        )
    existing_blocker = record.get("migration_blocker")
    if status == "queued" and existing_blocker not in {
        None,
        "",
        LEGACY_QUEUED_BLOCKER,
    }:
        raise ContractValidationError(
            (
                _issue(
                    "legacy-migration-blocker-conflict",
                    f"unsupported queued-task migration blocker: {existing_blocker!r}",
                    "migration_blocker",
                ),
            )
        )
    if status != "queued" and existing_blocker not in {None, ""}:
        raise ContractValidationError(
            (
                _issue(
                    "legacy-migration-blocker-conflict",
                    "only a queued legacy task may carry migration_blocker",
                    "migration_blocker",
                ),
            )
        )
    migrated = deepcopy(dict(record))
    migrated["contract_schema_revision"] = LEGACY_CONTRACT_SCHEMA_REVISION

    if action == "reclassify-to-revision-3":
        migrated["migration_blocker"] = LEGACY_QUEUED_BLOCKER
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
            migration_blocker=LEGACY_QUEUED_BLOCKER,
        )
    if action == "legacy-complete-or-rebind":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
            may_finish_legacy_attempt=True,
            requires_fresh_ack_on_rebind=True,
        )
    if action == "accept-legacy-result-or-add-gates":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
            may_accept_legacy_result=True,
        )
    if action == "preserve-history":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
        )
    if action == "retry-legacy-attempt":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
            may_start_new_attempt=True,
        )
    if action == "resume-or-abort-legacy-merge":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
            may_resume_legacy_merge=True,
        )
    if action == "requeue-after-manager-recovery":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action=action,
            requires_requeue_before_start=True,
        )
    raise AssertionError(f"unhandled legacy task migration action: {action}")


def migrate_legacy_result_contract(record: Mapping[str, Any]) -> dict[str, Any]:
    """Label an existing 0.5.2 result without inventing revision-3 evidence."""

    if not isinstance(record, Mapping):
        raise ContractValidationError(
            (_issue("contract-not-object", "legacy result must be an object"),)
        )
    existing_revision = record.get("contract_schema_revision")
    if existing_revision == V053_CONTRACT_SCHEMA_REVISION:
        migrated = deepcopy(dict(record))
        validate_result_contract(migrated).raise_for_errors()
        return migrated
    if existing_revision is not None and existing_revision != LEGACY_CONTRACT_SCHEMA_REVISION:
        raise ContractValidationError(
            (
                _issue(
                    "legacy-contract-revision-conflict",
                    "0.5.2 result migration accepts only an unlabeled or revision-2 record",
                    "contract_schema_revision",
                ),
            )
        )
    migrated = deepcopy(dict(record))
    migrated["contract_schema_revision"] = LEGACY_CONTRACT_SCHEMA_REVISION
    validation = validate_result_contract(migrated)
    validation.raise_for_errors()
    return migrated
