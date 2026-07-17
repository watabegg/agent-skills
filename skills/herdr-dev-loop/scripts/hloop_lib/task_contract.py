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
LEGACY_TERMINAL_TASK_STATUSES = frozenset(
    {
        "merged",
        "done",
        "partial",
        "blocked",
        "failed",
        "abandoned",
        "failed_validation",
    }
)
TASK_KINDS = frozenset({"implementation", "fix", "research"})
RISK_CLASSES = frozenset({"mechanical", "normal", "high"})
REQUIRED_GATES = frozenset({"patch_review", "full_suite"})
RESULT_STATUSES = frozenset({"done", "partial", "blocked", "failed", "abandoned"})
VALIDATION_RESULTS = frozenset({"passed", "failed", "blocked"})

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
LEGACY_TASK_ACTIONS = frozenset(
    {
        "reclassify-to-revision-3",
        "legacy-complete-or-rebind",
        "accept-legacy-result-or-add-gates",
        "preserve-history",
    }
)


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
    migration_blocker: str = ""
    may_finish_legacy_attempt: bool = False
    may_accept_legacy_result: bool = False
    requires_fresh_ack_on_rebind: bool = False
    may_start_new_attempt: bool = False
    may_merge_reported_result: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "contract_schema_revision": LEGACY_CONTRACT_SCHEMA_REVISION,
            "action": self.action,
            "migration_blocker": self.migration_blocker,
            "may_finish_legacy_attempt": self.may_finish_legacy_attempt,
            "may_accept_legacy_result": self.may_accept_legacy_result,
            "requires_fresh_ack_on_rebind": self.requires_fresh_ack_on_rebind,
            "may_start_new_attempt": self.may_start_new_attempt,
            "may_merge_reported_result": self.may_merge_reported_result,
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
    if kind not in TASK_KINDS:
        issues.append(
            _issue("contract-field-unsupported", f"unsupported task kind: {kind!r}", "kind")
        )
    status = record.get("status")
    if status not in TASK_STATUSES:
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
            min_items=1 if kind in {"implementation", "fix"} else 0,
        )
    )
    issues.extend(
        _string_list_issues(record, "acceptance", required=True, min_items=1)
    )

    if revision == V053_CONTRACT_SCHEMA_REVISION:
        issues.extend(_unknown_field_issues(record, V053_TASK_TOP_LEVEL_FIELDS))
        issues.extend(
            _string_list_issues(
                record, "preserved_invariants", required=True, min_items=1
            )
        )
        issues.extend(
            _string_list_issues(record, "regression_checks", required=True, min_items=1)
        )
        risk_class = record.get("risk_class")
        if risk_class not in RISK_CLASSES:
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
    if status not in RESULT_STATUSES:
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
        if not results or any(result != "passed" for result in results):
            issues.append(
                _issue(
                    "merge-ready-validation-failed",
                    "merge_ready requires every validation result to pass",
                    "validation_results",
                )
            )

    if revision == V053_CONTRACT_SCHEMA_REVISION:
        issues.extend(_unknown_field_issues(record, V053_RESULT_TOP_LEVEL_FIELDS))
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


def migrate_legacy_task_contract(record: Mapping[str, Any]) -> LegacyTaskMigration:
    """Return a revision-2 copy and the required status-sensitive migration gate.

    The function is idempotent for an already-labeled revision-2 record and
    rejects revision 3 or unknown revisions.  It never upgrades a legacy task
    automatically, because doing so would retroactively apply new gates or
    bypass the fresh semantic ACK required for a running-task rebind.
    """

    if not isinstance(record, Mapping):
        raise ContractValidationError(
            (_issue("contract-not-object", "legacy task must be an object"),)
        )
    existing_revision = record.get("contract_schema_revision")
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
    if status not in TASK_STATUSES:
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

    if status == "queued":
        migrated["migration_blocker"] = LEGACY_QUEUED_BLOCKER
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action="reclassify-to-revision-3",
            migration_blocker=LEGACY_QUEUED_BLOCKER,
        )
    if status == "running":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action="legacy-complete-or-rebind",
            may_finish_legacy_attempt=True,
            requires_fresh_ack_on_rebind=True,
        )
    if status == "result_reported":
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action="accept-legacy-result-or-add-gates",
            may_accept_legacy_result=True,
        )
    if status in LEGACY_TERMINAL_TASK_STATUSES:
        return LegacyTaskMigration(
            record=migrated,
            status=status,
            action="preserve-history",
        )
    raise AssertionError(f"unhandled task status: {status}")


def migrate_legacy_result_contract(record: Mapping[str, Any]) -> dict[str, Any]:
    """Label an existing 0.5.2 result without inventing revision-3 evidence."""

    if not isinstance(record, Mapping):
        raise ContractValidationError(
            (_issue("contract-not-object", "legacy result must be an object"),)
        )
    existing_revision = record.get("contract_schema_revision")
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
