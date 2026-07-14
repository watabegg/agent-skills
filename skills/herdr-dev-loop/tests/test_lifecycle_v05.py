import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.lifecycle import (  # noqa: E402
    MERGE_ABORTED,
    MERGE_ACTIVE,
    MERGE_COMPLETED,
    MERGE_CONTENT_CONFLICT,
    MERGE_ENVIRONMENT_FAILURE,
    AttemptIdentity,
    FinalGateConditions,
    GateEvidence,
    LifecycleInvariantError,
    MergeTransaction,
    allowed_merge_operations,
    arm_final_gates,
    cleanup_resolution_record,
    compute_resume_requirements,
    diagnose_done_target_drift,
    disarm_final_gates,
    validate_append_only_attempts,
    validate_attempt_copies,
    validate_final_gate_arm,
    validate_merge_transaction,
)
from hloop_lib.migration import (  # noqa: E402
    FutureSchemaError,
    MigrationError,
    MigrationStep,
    MissingMigrationError,
    SchemaVersion,
    migrate_schema,
    plan_schema_migration,
)


class MigrationPrimitiveTests(unittest.TestCase):
    def setUp(self):
        def enter_format_three(state):
            state["format_three"] = True
            return state

        def add_runtime_invariants(state):
            state["runtime_invariants"] = []
            return state

        self.steps = (
            MigrationStep(
                SchemaVersion(2, 0),
                SchemaVersion(3, 0),
                "format-2-to-3",
                enter_format_three,
            ),
            MigrationStep(
                SchemaVersion(3, 0),
                SchemaVersion(3, 1),
                "format-3-revision-1",
                add_runtime_invariants,
            ),
        )

    def test_migration_applies_every_revision_without_mutating_input(self):
        original = {"state_format_version": 2, "run_id": "run-1", "nested": {"x": 1}}
        before = copy.deepcopy(original)

        result = migrate_schema(
            original,
            target=SchemaVersion(3, 1),
            steps=self.steps,
        )

        self.assertEqual(original, before)
        self.assertEqual(result.state["state_format_version"], 3)
        self.assertEqual(result.state["schema_revision"], 1)
        self.assertTrue(result.state["format_three"])
        self.assertEqual(result.state["runtime_invariants"], [])
        self.assertEqual(
            result.applied_steps,
            ("format-2-to-3", "format-3-revision-1"),
        )

    def test_dry_run_plan_is_contiguous_and_side_effect_free(self):
        state = {"state_format_version": 3, "schema_revision": 0}
        plan = plan_schema_migration(
            state,
            target=SchemaVersion(3, 1),
            steps=self.steps,
        )
        self.assertEqual(plan.step_names, ("format-3-revision-1",))
        self.assertEqual(state, {"state_format_version": 3, "schema_revision": 0})

    def test_future_revision_is_rejected(self):
        with self.assertRaises(FutureSchemaError):
            plan_schema_migration(
                {"state_format_version": 3, "schema_revision": 2},
                target=SchemaVersion(3, 1),
                steps=self.steps,
            )

    def test_missing_intermediate_revision_is_rejected(self):
        with self.assertRaises(MissingMigrationError):
            plan_schema_migration(
                {"state_format_version": 2},
                target=SchemaVersion(3, 1),
                steps=self.steps[1:],
            )

    def test_transform_cannot_forge_a_later_revision(self):
        def forge_revision(state):
            state["state_format_version"] = 3
            state["schema_revision"] = 9
            return state

        step = MigrationStep(
            SchemaVersion(3, 0),
            SchemaVersion(3, 1),
            "forging-step",
            forge_revision,
        )
        with self.assertRaises(MigrationError):
            migrate_schema(
                {"state_format_version": 3, "schema_revision": 0},
                target=SchemaVersion(3, 1),
                steps=(step,),
            )


class AttemptIdentityTests(unittest.TestCase):
    @staticmethod
    def attempt(number, base="base-one"):
        return AttemptIdentity(
            run_id="run-1",
            role_id="T001",
            attempt_id=f"T001-A{number:03d}",
            base_sha=base,
            branch=f"ai/task-a{number:03d}",
            worktree=f"/worktrees/T001-a{number:03d}",
            task_contract_digest=f"digest-{number}",
        )

    def test_attempt_history_allows_only_append(self):
        first = self.attempt(1)
        second = self.attempt(2, "base-two")
        self.assertTrue(validate_append_only_attempts((first,), (first, second)).ok)

        rewritten = replace(first, base_sha="rewritten-base")
        result = validate_append_only_attempts((first,), (rewritten, second))
        self.assertFalse(result.ok)
        self.assertIn("attempt-history-rewritten", {issue.code for issue in result.issues})

    def test_attempt_history_rejects_truncation_and_duplicates(self):
        first = self.attempt(1)
        second = self.attempt(2, "base-two")
        truncated = validate_append_only_attempts((first, second), (first,))
        duplicated = validate_append_only_attempts((first,), (first, first))
        self.assertEqual(truncated.issues[0].code, "attempt-history-truncated")
        self.assertEqual(duplicated.issues[0].code, "attempt-id-duplicated")

    def test_manager_and_role_copy_divergence_is_not_auto_resolved(self):
        manager = self.attempt(1)
        role = replace(manager, task_contract_digest="changed")
        result = validate_attempt_copies(manager, role)
        self.assertEqual(result.issues[0].code, "attempt-state-diverged")

    def test_legacy_worker_base_alias_must_agree(self):
        record = self.attempt(1).to_record()
        record["worker_base_sha"] = "different"
        result = validate_append_only_attempts((), (record,))
        self.assertEqual(result.issues[0].code, "attempt-record-invalid")


class MergeTransactionTests(unittest.TestCase):
    @staticmethod
    def transaction(status=MERGE_ACTIVE):
        return MergeTransaction(
            transaction_id="M-T001-A001",
            task_id="T001",
            attempt_id="T001-A001",
            branch="ai/task",
            pre_merge_head="integration-head",
            worker_head="worker-head",
            result_head="worker-head",
            index_state="clean-index-digest",
            changed_paths=("src/example.py",),
            status=status,
        )

    def test_conflict_continue_and_abort_retry_transitions_are_valid(self):
        active = self.transaction()
        conflict = replace(active, status=MERGE_CONTENT_CONFLICT)
        completed = replace(conflict, status=MERGE_COMPLETED)
        aborted = replace(conflict, status=MERGE_ABORTED)
        retried = replace(aborted, status=MERGE_ACTIVE)
        self.assertTrue(validate_merge_transaction(active, conflict).ok)
        self.assertTrue(validate_merge_transaction(conflict, completed).ok)
        self.assertTrue(validate_merge_transaction(conflict, aborted).ok)
        self.assertTrue(validate_merge_transaction(aborted, retried).ok)

    def test_environment_failure_must_abort_before_retry(self):
        failed = self.transaction(MERGE_ENVIRONMENT_FAILURE)
        retry_without_abort = replace(failed, status=MERGE_ACTIVE)
        result = validate_merge_transaction(failed, retry_without_abort)
        self.assertEqual(result.issues[0].code, "manual-integration-trace")

    def test_transaction_identity_change_is_manual_trace(self):
        active = self.transaction()
        changed = replace(active, worker_head="unexpected-worker-head")
        result = validate_merge_transaction(active, changed)
        self.assertEqual(result.issues[0].code, "manual-integration-trace")
        self.assertEqual(result.issues[0].severity, "P0")

    def test_changed_path_order_is_canonical(self):
        transaction = replace(
            self.transaction(),
            changed_paths=("src/z.py", "src/a.py"),
        )
        restored = MergeTransaction.from_record(transaction.to_record())
        self.assertEqual(transaction.changed_paths, ("src/a.py", "src/z.py"))
        self.assertEqual(restored, transaction)

    def test_conflict_operations_are_explicitly_bounded(self):
        self.assertEqual(
            allowed_merge_operations(MERGE_CONTENT_CONFLICT),
            ("watch", "continue", "abort", "retry"),
        )


class CompletionLifecycleTests(unittest.TestCase):
    def test_done_target_drift_includes_both_shas_and_commit_count(self):
        diagnostic = diagnose_done_target_drift(
            phase="done",
            final_target_sha="old-head",
            current_target_sha="new-head",
            commit_count=3,
        )
        self.assertEqual(diagnostic.code, "done-target-drift")
        self.assertEqual(diagnostic.severity, "P0")
        self.assertIn("old-head", diagnostic.message)
        self.assertIn("new-head", diagnostic.message)
        self.assertIn("3 advancing commits", diagnostic.message)

    def test_non_done_or_unchanged_target_has_no_drift(self):
        self.assertIsNone(
            diagnose_done_target_drift(
                phase="paused",
                final_target_sha="old",
                current_target_sha="new",
                commit_count=1,
            )
        )
        self.assertIsNone(
            diagnose_done_target_drift(
                phase="done",
                final_target_sha="same",
                current_target_sha="same",
                commit_count=0,
            )
        )

    def test_resume_requirements_do_not_rebind_stale_gate_evidence(self):
        requirements = compute_resume_requirements(
            current_target_sha="head-new",
            gates=(
                GateEvidence("validation", "passed", "head-old"),
                GateEvidence("review", "reported", "head-new", ("closed",)),
                GateEvidence("gap", "", "", ("closed",)),
                GateEvidence("manager-qa", "not-required", "head-new", ("not-required",)),
            ),
            active_blockers=("decision D001",),
            dirty_paths=("src/dirty.py",),
            running_roles=("T009",),
        )
        codes = [item.code for item in requirements]
        self.assertEqual(
            codes,
            [
                "gate-stale",
                "gate-not-passing",
                "gate-missing",
                "active-blocker",
                "dirty-state",
                "running-role",
            ],
        )
        self.assertEqual(requirements[0].observed_sha, "head-old")
        self.assertEqual(requirements[0].target_sha, "head-new")

    def test_cleanup_accepted_risk_record_carries_audit_identity(self):
        record = cleanup_resolution_record(
            run_id="run-1",
            role_id="R001",
            attempt_id="R001-A002",
            status="accepted-risk",
            reason="provider archive is unsupported",
            manager_identity="manager-pane-1",
            resolved_at="2026-07-15T00:00:00Z",
            error_fingerprint="sha256:error",
        ).to_record()
        self.assertEqual(record["status"], "accepted-risk")
        self.assertEqual(record["run_id"], "run-1")
        self.assertEqual(record["role_id"], "R001")
        self.assertEqual(record["attempt_id"], "R001-A002")
        self.assertEqual(record["manager_identity"], "manager-pane-1")

    def test_cleanup_resolution_requires_reason_and_manager(self):
        with self.assertRaises(ValueError):
            cleanup_resolution_record(
                run_id="run-1",
                role_id="R001",
                attempt_id="R001-A001",
                status="accepted-risk",
                reason="",
                manager_identity="manager",
                resolved_at="2026-07-15T00:00:00Z",
                error_fingerprint="error",
            )

    def test_final_gate_arm_is_stable_and_disarms_on_new_task(self):
        conditions = FinalGateConditions(True, True, ())
        armed = arm_final_gates(
            None,
            target_sha="head-one",
            armed_by="manager",
            armed_at="2026-07-15T00:00:00Z",
            conditions=conditions,
        )
        idempotent = arm_final_gates(
            armed,
            target_sha="head-one",
            armed_by="other-manager",
            armed_at="later",
            conditions=conditions,
        )
        self.assertEqual(idempotent, armed)
        requirement = validate_final_gate_arm(
            armed,
            current_target_sha="head-one",
            new_tasks_created=True,
        )
        self.assertEqual(requirement.issues[0].code, "final-gates-disarm-required")

        disarmed = disarm_final_gates(
            armed,
            disarmed_by="manager",
            disarmed_at="2026-07-15T00:05:00Z",
            reason="fix task T002 was created",
        )
        self.assertEqual(disarmed.status, "disarmed")
        self.assertEqual(disarmed.target_sha, armed.target_sha)
        self.assertEqual(
            disarm_final_gates(
                disarmed,
                disarmed_by="x",
                disarmed_at="x",
                reason="x",
            ),
            disarmed,
        )

    def test_final_gate_arm_rejects_unstable_batch(self):
        with self.assertRaises(LifecycleInvariantError) as raised:
            arm_final_gates(
                None,
                target_sha="head",
                armed_by="manager",
                armed_at="now",
                conditions=FinalGateConditions(False, False, ("R001.fix-task-draft.md",)),
            )
        self.assertEqual(
            {issue.code for issue in raised.exception.issues},
            {
                "final-gates-batch-open",
                "final-gates-review-untriaged",
                "final-gates-fix-draft-pending",
            },
        )


if __name__ == "__main__":
    unittest.main()
