from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional for minimal skill installs
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.task_contract import (  # noqa: E402
    LEGACY_CONTRACT_SCHEMA_REVISION,
    LEGACY_QUEUED_BLOCKER,
    LEGACY_RUNTIME_TASK_STATUSES,
    LEGACY_TASK_STATUS_ACTIONS,
    V053_CONTRACT_SCHEMA_REVISION,
    V053_RESULT_TOP_LEVEL_FIELDS,
    V053_TASK_TOP_LEVEL_FIELDS,
    ContractValidationError,
    contract_schema_revision_of,
    migrate_legacy_result_contract,
    migrate_legacy_task_contract,
    validate_result_contract,
    validate_task_contract,
)


def task_contract(*, revision: int, status: str = "queued") -> dict:
    record = {
        "id": "T001",
        "run_id": "run-v053",
        "skill_version": "0.5.3",
        "contract_schema_revision": revision,
        "kind": "implementation",
        "status": status,
        "created_from": "PLAN.md",
        "branch": "ai/v053/T001",
        "base_ref": "ai/v053/integration",
        "base_sha": "a" * 40,
        "priority": "P0",
        "depends_on": [],
        "write_allow": ["src/task.py"],
        "write_deny": [],
        "acceptance": ["contract is enforced"],
        "validation_minimum": "L1",
        "worker_protocol": "native",
        "worker_qa_profile": "repo-default",
        "worker_agent_provider": "codex",
        "worker_agent_model": "gpt-5.6-sol",
        "task_origin": "planned",
        "release_scope_revision": 1,
        "plan_item_refs": ["P001"],
        "requirement_refs": ["REQ-005"],
        "scope_refs": ["runtime-release"],
        "source_finding": "",
        "authorization_input_id": "",
        "why_fix_now": "",
        "operational_reason": "",
        "origin": "",
        "contract_relation": "",
        "decision_requirement": "",
        "release_effect": "",
        "remediation_round": 0,
        "fact_status": "",
        "disposition": "",
        "scope_expanding": False,
    }
    if revision == V053_CONTRACT_SCHEMA_REVISION:
        record.update(
            {
                "preserved_invariants": ["0.5.2 completion remains valid"],
                "regression_checks": ["run targeted migration tests"],
                "risk_class": "high",
                "required_gates": ["patch_review", "full_suite"],
                "worker_agent_effort": "xhigh",
                "investigation_goal": "identify every contract consumer",
                "implementation_ready_evidence": ["consumer map reviewed"],
                "exploration_budget_minutes": 30,
                "history_search_allowed": False,
            }
        )
    return record


def result_contract(*, revision: int) -> dict:
    record = {
        "task_id": "T001",
        "run_id": "run-v053",
        "skill_version": "0.5.3",
        "contract_schema_revision": revision,
        "attempt_id": "T001-A001",
        "status": "done",
        "merge_ready": True,
        "branch": "ai/v053/T001",
        "head_sha": "b" * 40,
        "base_sha": "a" * 40,
        "changed_files": ["src/task.py"],
        "validation_recorded": True,
        "validation_commands": ["python3 -m unittest tests.test_task"],
        "validation_results": ["passed"],
        "validation_summary": "targeted tests passed",
        "blocking_questions": [],
        "handoff": False,
    }
    if revision == V053_CONTRACT_SCHEMA_REVISION:
        record.update(
            {
                "invariant_evidence": ["legacy fixture passed"],
                "regression_evidence": ["targeted test passed"],
                "self_review_summary": "scope and error paths reviewed",
                "residual_risks": [],
                "unrun_checks": [],
            }
        )
    return record


class ContractPrimitiveTests(unittest.TestCase):
    def test_every_v052_runtime_task_status_has_an_explicit_migration_action(self):
        expected_actions = {
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

        self.assertEqual(LEGACY_TASK_STATUS_ACTIONS, expected_actions)
        self.assertEqual(LEGACY_RUNTIME_TASK_STATUSES, frozenset(expected_actions))
        for status, action in expected_actions.items():
            with self.subTest(status=status):
                legacy = task_contract(
                    revision=LEGACY_CONTRACT_SCHEMA_REVISION,
                    status=status,
                )
                legacy.pop("contract_schema_revision")

                migration = migrate_legacy_task_contract(legacy)

                self.assertEqual(migration.status, status)
                self.assertEqual(migration.record["status"], status)
                self.assertEqual(migration.action, action)

    def test_recoverable_legacy_statuses_keep_their_retry_boundary(self):
        failed_validation = task_contract(
            revision=LEGACY_CONTRACT_SCHEMA_REVISION,
            status="failed_validation",
        )
        failed_validation.pop("contract_schema_revision")
        retry = migrate_legacy_task_contract(failed_validation)

        self.assertEqual(retry.action, "retry-legacy-attempt")
        self.assertTrue(retry.may_start_new_attempt)
        self.assertFalse(retry.requires_requeue_before_start)

        for status in ("blocked_merge_conflict", "blocked_environment"):
            with self.subTest(status=status):
                legacy = task_contract(
                    revision=LEGACY_CONTRACT_SCHEMA_REVISION,
                    status=status,
                )
                legacy.pop("contract_schema_revision")
                recovery = migrate_legacy_task_contract(legacy)

                self.assertTrue(recovery.may_resume_legacy_merge)
                self.assertFalse(recovery.may_start_new_attempt)

        for status in (
            "aborted",
            "blocked_head_mismatch",
            "blocked_base_mismatch",
            "blocked_write_scope",
        ):
            with self.subTest(status=status):
                legacy = task_contract(
                    revision=LEGACY_CONTRACT_SCHEMA_REVISION,
                    status=status,
                )
                legacy.pop("contract_schema_revision")
                recovery = migrate_legacy_task_contract(legacy)

                self.assertTrue(recovery.requires_requeue_before_start)
                self.assertFalse(recovery.may_start_new_attempt)

    def test_revision_three_task_requires_quality_gate_fields(self):
        record = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        self.assertTrue(validate_task_contract(record).ok)

        for field in (
            "preserved_invariants",
            "regression_checks",
            "risk_class",
            "required_gates",
            "worker_agent_effort",
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(record)
                invalid.pop(field)
                validation = validate_task_contract(invalid)
                self.assertFalse(validation.ok)
                self.assertIn(field, {issue.field for issue in validation.issues})

    def test_patch_review_and_full_suite_are_independent_required_gates(self):
        record = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        self.assertEqual(record["required_gates"], ["patch_review", "full_suite"])
        self.assertTrue(validate_task_contract(record).ok)

        record["required_gates"] = ["patch_review", "patch_review"]
        codes = {issue.code for issue in validate_task_contract(record).issues}
        self.assertIn("contract-field-duplicated", codes)

    def test_revision_three_cannot_keep_legacy_migration_blocker(self):
        record = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        record["migration_blocker"] = LEGACY_QUEUED_BLOCKER
        validation = validate_task_contract(record)
        self.assertFalse(validation.ok)
        self.assertIn(
            "revision-3-legacy-blocker", {issue.code for issue in validation.issues}
        )

    def test_revision_three_rejects_unknown_top_level_properties(self):
        for name, record, validator in (
            (
                "task",
                task_contract(revision=V053_CONTRACT_SCHEMA_REVISION),
                validate_task_contract,
            ),
            (
                "result",
                result_contract(revision=V053_CONTRACT_SCHEMA_REVISION),
                validate_result_contract,
            ),
        ):
            with self.subTest(contract=name):
                record["unknown_top_level_property"] = True
                validation = validator(record)
                self.assertFalse(validation.ok)
                self.assertIn(
                    "contract-field-unknown",
                    {issue.code for issue in validation.issues},
                )
                self.assertIn(
                    "unknown_top_level_property",
                    {issue.field for issue in validation.issues},
                )

    def test_unknown_or_missing_revision_fails_closed(self):
        missing = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        missing.pop("contract_schema_revision")
        with self.assertRaises(ContractValidationError):
            contract_schema_revision_of(missing)

        future = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        future["contract_schema_revision"] = 4
        with self.assertRaises(ContractValidationError):
            contract_schema_revision_of(future)

    def test_queued_legacy_task_is_blocked_until_revision_three_reclassification(self):
        legacy = task_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        legacy.pop("contract_schema_revision")
        before = copy.deepcopy(legacy)

        migration = migrate_legacy_task_contract(legacy)

        self.assertEqual(legacy, before)
        self.assertEqual(
            migration.record["contract_schema_revision"],
            LEGACY_CONTRACT_SCHEMA_REVISION,
        )
        self.assertEqual(migration.migration_blocker, LEGACY_QUEUED_BLOCKER)
        self.assertEqual(migration.action, "reclassify-to-revision-3")
        self.assertFalse(migration.may_start_new_attempt)

    def test_running_legacy_task_can_finish_or_rebind_with_fresh_ack(self):
        legacy = task_contract(
            revision=LEGACY_CONTRACT_SCHEMA_REVISION, status="running"
        )
        legacy.pop("contract_schema_revision")

        migration = migrate_legacy_task_contract(legacy)

        self.assertEqual(migration.action, "legacy-complete-or-rebind")
        self.assertTrue(migration.may_finish_legacy_attempt)
        self.assertTrue(migration.requires_fresh_ack_on_rebind)
        self.assertFalse(migration.may_start_new_attempt)

    def test_reported_legacy_result_cannot_be_implicitly_merge_ready(self):
        legacy = task_contract(
            revision=LEGACY_CONTRACT_SCHEMA_REVISION, status="result_reported"
        )
        legacy.pop("contract_schema_revision")

        migration = migrate_legacy_task_contract(legacy)

        self.assertEqual(migration.action, "accept-legacy-result-or-add-gates")
        self.assertTrue(migration.may_accept_legacy_result)
        self.assertFalse(migration.may_finish_legacy_attempt)
        self.assertFalse(migration.may_merge_reported_result)

    def test_terminal_legacy_task_remains_historical_revision_two(self):
        legacy = task_contract(
            revision=LEGACY_CONTRACT_SCHEMA_REVISION, status="merged"
        )
        legacy.pop("contract_schema_revision")

        migration = migrate_legacy_task_contract(legacy)

        self.assertEqual(migration.action, "preserve-history")
        self.assertNotIn("migration_blocker", migration.record)

    def test_partial_legacy_blocker_on_running_task_is_rejected(self):
        legacy = task_contract(
            revision=LEGACY_CONTRACT_SCHEMA_REVISION, status="running"
        )
        legacy["migration_blocker"] = LEGACY_QUEUED_BLOCKER

        with self.assertRaises(ContractValidationError):
            migrate_legacy_task_contract(legacy)

    def test_legacy_result_label_does_not_invent_revision_three_evidence(self):
        legacy = result_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        legacy.pop("contract_schema_revision")
        before = copy.deepcopy(legacy)

        migrated = migrate_legacy_result_contract(legacy)

        self.assertEqual(legacy, before)
        self.assertEqual(
            migrated["contract_schema_revision"], LEGACY_CONTRACT_SCHEMA_REVISION
        )
        self.assertNotIn("invariant_evidence", migrated)

    def test_malformed_legacy_result_is_not_labeled_as_valid_evidence(self):
        legacy = result_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        legacy.pop("contract_schema_revision")
        legacy.pop("attempt_id")

        with self.assertRaises(ContractValidationError):
            migrate_legacy_result_contract(legacy)

    def test_revision_three_result_requires_completion_evidence(self):
        record = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        self.assertTrue(validate_result_contract(record).ok)

        for field in (
            "invariant_evidence",
            "regression_evidence",
            "self_review_summary",
            "residual_risks",
            "unrun_checks",
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(record)
                invalid.pop(field)
                validation = validate_result_contract(invalid)
                self.assertFalse(validation.ok)
                self.assertIn(field, {issue.field for issue in validation.issues})

    def test_blocked_revision_three_result_records_empty_evidence_and_unrun_checks(self):
        record = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        record.update(
            {
                "status": "blocked",
                "merge_ready": False,
                "validation_recorded": False,
                "validation_commands": [],
                "validation_results": [],
                "invariant_evidence": [],
                "regression_evidence": [],
                "unrun_checks": ["integration test blocked by migration decision"],
            }
        )

        self.assertTrue(validate_result_contract(record).ok)

    def test_merge_ready_still_requires_aligned_passing_validation(self):
        record = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        record["validation_results"] = ["failed"]
        validation = validate_result_contract(record)
        self.assertFalse(validation.ok)
        self.assertIn(
            "merge-ready-validation-failed",
            {issue.code for issue in validation.issues},
        )


@unittest.skipUnless(jsonschema is not None, "jsonschema is optional")
class ContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.task_schema = json.loads(
            (SCHEMAS / "task.schema.json").read_text(encoding="utf-8")
        )
        cls.result_schema = json.loads(
            (SCHEMAS / "result.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(cls.task_schema)
        jsonschema.Draft202012Validator.check_schema(cls.result_schema)
        cls.task_validator = jsonschema.Draft202012Validator(cls.task_schema)
        cls.result_validator = jsonschema.Draft202012Validator(cls.result_schema)

    def test_task_schema_accepts_explicit_legacy_revision_and_v053_revision(self):
        legacy = task_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        legacy["preserved_legacy_extension"] = {"evidence": True}
        self.task_validator.validate(legacy)
        self.task_validator.validate(
            task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        )

    def test_task_schema_rejects_missing_unknown_and_fail_open_revision_three(self):
        missing = task_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        missing.pop("contract_schema_revision")
        self.assertFalse(self.task_validator.is_valid(missing))

        unknown = task_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        unknown["contract_schema_revision"] = 4
        self.assertFalse(self.task_validator.is_valid(unknown))

        incomplete = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        incomplete.pop("preserved_invariants")
        self.assertFalse(self.task_validator.is_valid(incomplete))

        extra = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        extra["legacy_optional_escape"] = True
        self.assertFalse(self.task_validator.is_valid(extra))

    def test_task_schema_accepts_patch_review_and_full_suite_together(self):
        record = task_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        record["required_gates"] = ["patch_review", "full_suite"]
        self.task_validator.validate(record)

    def test_result_schema_preserves_legacy_and_requires_v053_evidence(self):
        legacy = result_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        legacy["legacy_evidence"] = "preserved"
        self.result_validator.validate(legacy)

        current = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        self.result_validator.validate(current)
        current.pop("self_review_summary")
        self.assertFalse(self.result_validator.is_valid(current))

        blocked = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        blocked.update(
            {
                "status": "blocked",
                "merge_ready": False,
                "validation_recorded": False,
                "validation_commands": [],
                "validation_results": [],
                "invariant_evidence": [],
                "regression_evidence": [],
                "unrun_checks": ["blocked migration check"],
            }
        )
        self.result_validator.validate(blocked)

    def test_result_schema_rejects_unknown_revision_and_v053_extension(self):
        future = result_contract(revision=LEGACY_CONTRACT_SCHEMA_REVISION)
        future["contract_schema_revision"] = 4
        self.assertFalse(self.result_validator.is_valid(future))

        current = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        current["unvalidated_completion_field"] = True
        self.assertFalse(self.result_validator.is_valid(current))

    def test_result_schema_retains_merge_ready_guard(self):
        current = result_contract(revision=V053_CONTRACT_SCHEMA_REVISION)
        current["validation_results"] = ["failed"]
        self.assertFalse(self.result_validator.is_valid(current))

    def test_revision_three_python_and_json_schema_property_parity(self):
        contracts = (
            (
                "task",
                task_contract,
                validate_task_contract,
                self.task_validator,
                self.task_schema,
                V053_TASK_TOP_LEVEL_FIELDS,
            ),
            (
                "result",
                result_contract,
                validate_result_contract,
                self.result_validator,
                self.result_schema,
                V053_RESULT_TOP_LEVEL_FIELDS,
            ),
        )
        for name, builder, python_validator, schema_validator, schema, fields in contracts:
            with self.subTest(contract=name, case="property-set"):
                self.assertEqual(
                    fields,
                    frozenset(schema["$defs"]["revision3"]["properties"]),
                )

            valid = builder(revision=V053_CONTRACT_SCHEMA_REVISION)
            invalid = copy.deepcopy(valid)
            invalid["unknown_top_level_property"] = True
            for case, record, expected in (
                ("valid", valid, True),
                ("unknown-property", invalid, False),
            ):
                with self.subTest(contract=name, case=case):
                    self.assertEqual(python_validator(record).ok, expected)
                    self.assertEqual(schema_validator.is_valid(record), expected)


if __name__ == "__main__":
    unittest.main()
