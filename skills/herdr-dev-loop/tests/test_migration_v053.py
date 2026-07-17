from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.migration import (  # noqa: E402
    FORMAT_3_REVISION_3_MIGRATION,
    FORMAT_THREE_MIGRATION_STEPS,
    V052_STATE_SCHEMA_VERSION,
    V053_STATE_SCHEMA_VERSION,
    MigrationDecisionRequired,
    MigrationError,
    artifact_digests_from_bytes,
    decide_migration_recovery,
    migrate_schema,
    plan_format_three_revision_three,
    plan_migration_transaction,
    recover_legacy_remediation_history,
    sha256_digest,
)


def legacy_task(status: str, **updates) -> dict:
    record = {
        "id": updates.pop("id", "T001"),
        "status": status,
        "kind": "implementation",
        "task_origin": "planned",
        "remediation_round": 0,
        "source_finding": "",
    }
    record.update(updates)
    return record


def legacy_state(*, tasks: dict | None = None) -> dict:
    return {
        "state_format_version": 3,
        "schema_revision": 2,
        "run_id": "run-v052",
        "tasks": tasks or {},
        "batches": {},
        "reviews": {},
        "gaps": {},
        "review_convergence": {
            "status": "not-started",
            "fix_round": 0,
            "authorized_extra_rounds": 0,
            "extra_round_authorization_refs": [],
        },
        "manual_final_review": {"status": "not-started"},
        "execution_metrics": {"review_fix_rounds": 0},
    }


def transaction_plan():
    return plan_migration_transaction(
        migration_generation=7,
        source=V052_STATE_SCHEMA_VERSION,
        target=V053_STATE_SCHEMA_VERSION,
        artifacts={
            ".ai/herdr-dev-loop/loops/demo/STATE.json": (
                b'{"schema_revision":2}\n',
                b'{"schema_revision":3}\n',
            ),
            ".ai/herdr-dev-loop/loops/demo/tasks/T001.md": (
                b"contract_schema_revision: absent\n",
                b"contract_schema_revision: 2\n",
            ),
        },
    )


def source_digests(plan) -> dict[str, str]:
    return {artifact.path: artifact.archive_digest for artifact in plan.artifacts}


def output_digests(plan) -> dict[str, str]:
    return {artifact.path: artifact.output_digest for artifact in plan.artifacts}


class StateMigrationV053Tests(unittest.TestCase):
    def test_status_sensitive_legacy_task_projection_is_side_effect_free(self):
        state = legacy_state(
            tasks={
                "T001": legacy_task("queued", id="T001"),
                "T002": legacy_task("running", id="T002"),
                "T003": legacy_task("result_reported", id="T003"),
                "T004": legacy_task("merged", id="T004"),
            }
        )
        before = copy.deepcopy(state)

        plan = plan_format_three_revision_three(state)

        self.assertEqual(state, before)
        self.assertTrue(plan.applicable)
        for task_id in state["tasks"]:
            self.assertEqual(
                plan.state["tasks"][task_id]["contract_schema_revision"], 2
            )
        self.assertEqual(
            plan.state["tasks"]["T001"]["migration_blocker"],
            "risk-classification-required",
        )
        self.assertEqual(
            plan.task_migrations["T002"].action, "legacy-complete-or-rebind"
        )
        self.assertTrue(
            plan.task_migrations["T002"].requires_fresh_ack_on_rebind
        )
        self.assertEqual(
            plan.task_migrations["T003"].action,
            "accept-legacy-result-or-add-gates",
        )
        self.assertFalse(plan.task_migrations["T003"].may_merge_reported_result)
        self.assertEqual(plan.task_migrations["T004"].action, "preserve-history")
        self.assertEqual(plan.state["first_v053_mutation_at"], "")
        self.assertEqual(plan.state["first_v053_mutation_command"], "")
        self.assertEqual(
            plan.state["contract_schema_compatibility"],
            {
                "state_schema_revision": 3,
                "legacy_contract_schema_revision": 2,
                "current_contract_schema_revision": 3,
            },
        )

    def test_revision_three_migration_runs_through_existing_engine(self):
        state = legacy_state(tasks={"T001": legacy_task("queued")})

        migrated = migrate_schema(
            state,
            target=V053_STATE_SCHEMA_VERSION,
            steps=(FORMAT_3_REVISION_3_MIGRATION,),
        )

        self.assertEqual(migrated.applied_steps, ("format-3-revision-3",))
        self.assertEqual(migrated.state["state_format_version"], 3)
        self.assertEqual(migrated.state["schema_revision"], 3)
        self.assertEqual(
            migrated.state["tasks"]["T001"]["contract_schema_revision"], 2
        )

    def test_revision_one_migrates_contiguously_through_052_and_053(self):
        state = {
            "state_format_version": 3,
            "schema_revision": 1,
            "run_id": "run-v051",
            "review_after_merges": 3,
            "tasks": {"T001": legacy_task("merged")},
        }
        before = copy.deepcopy(state)

        migrated = migrate_schema(
            state,
            target=V053_STATE_SCHEMA_VERSION,
            steps=FORMAT_THREE_MIGRATION_STEPS,
        )

        self.assertEqual(state, before)
        self.assertEqual(
            migrated.applied_steps,
            ("format-3-revision-2", "format-3-revision-3"),
        )
        self.assertEqual(migrated.state["schema_revision"], 3)
        self.assertEqual(
            migrated.state["tasks"]["T001"]["contract_schema_revision"], 2
        )
        self.assertEqual(
            migrated.state["manual_final_review"]["status"],
            "not-required-for-legacy-run",
        )

    def test_remediation_history_recovers_one_batch_from_source_and_task_evidence(self):
        state = legacy_state(
            tasks={
                "T009": legacy_task(
                    "merged",
                    id="T009",
                    task_origin="finding",
                    remediation_round=1,
                ),
                "T010": legacy_task(
                    "merged",
                    id="T010",
                    task_origin="finding",
                    remediation_round=1,
                ),
            }
        )
        state["reviews"] = {
            "R001": {
                "created_fix_tasks": ["T009", "T010"],
                "head_sha": "a" * 40,
            }
        }
        state["review_convergence"].update(
            {
                "fix_round": 1,
                "extra_round_authorization_refs": ["U0007", "U0007"],
            }
        )
        state["execution_metrics"]["review_fix_rounds"] = 1

        recovery = recover_legacy_remediation_history(state)

        self.assertEqual(recovery.status, "recovered")
        self.assertEqual(recovery.consumed_rounds, 1)
        self.assertEqual(len(recovery.batches), 1)
        self.assertEqual(recovery.batches[0].task_ids, ("T009", "T010"))
        self.assertEqual(
            recovery.extra_round_authorization_refs,
            ("U0007",),
        )

    def test_ambiguous_orphan_remediation_tasks_require_a_decision(self):
        state = legacy_state(
            tasks={
                "T009": legacy_task(
                    "merged",
                    id="T009",
                    task_origin="finding",
                    remediation_round=1,
                ),
                "T010": legacy_task(
                    "merged",
                    id="T010",
                    task_origin="finding",
                    remediation_round=1,
                ),
            }
        )

        plan = plan_format_three_revision_three(state)

        self.assertFalse(plan.applicable)
        self.assertTrue(plan.remediation.decision_required)
        self.assertEqual(plan.remediation.decision_candidates, (1, 2))
        self.assertIn(
            "cannot be grouped uniquely", " ".join(plan.blocking_reasons)
        )
        with self.assertRaises(MigrationDecisionRequired):
            migrate_schema(
                state,
                target=V053_STATE_SCHEMA_VERSION,
                steps=(FORMAT_3_REVISION_3_MIGRATION,),
            )

    def test_source_spanning_two_explicit_batches_fails_closed(self):
        state = legacy_state(
            tasks={
                "T009": legacy_task(
                    "merged",
                    id="T009",
                    task_origin="finding",
                    remediation_round=1,
                    batch_id="B009",
                ),
                "T010": legacy_task(
                    "merged",
                    id="T010",
                    task_origin="finding",
                    remediation_round=1,
                    batch_id="B010",
                ),
            }
        )
        state["reviews"] = {
            "R001": {"created_fix_tasks": ["T009", "T010"]}
        }

        recovery = recover_legacy_remediation_history(state)

        self.assertTrue(recovery.decision_required)
        self.assertIn("multiple legacy batches", " ".join(recovery.issues))

    def test_disjoint_sources_may_not_be_guessed_as_one_or_two_batches(self):
        state = legacy_state(
            tasks={
                "T009": legacy_task(
                    "merged",
                    id="T009",
                    task_origin="finding",
                    remediation_round=1,
                ),
                "T010": legacy_task(
                    "merged",
                    id="T010",
                    task_origin="finding",
                    remediation_round=1,
                ),
            }
        )
        target_sha = "a" * 40
        state["reviews"] = {
            "R001": {"created_fix_tasks": ["T009"], "head_sha": target_sha},
            "R002": {"created_fix_tasks": ["T010"], "head_sha": target_sha},
        }

        recovery = recover_legacy_remediation_history(state)

        self.assertTrue(recovery.decision_required)
        self.assertEqual(recovery.decision_candidates, (1, 2))
        self.assertIn("without explicit batch", " ".join(recovery.issues))

    def test_legacy_counter_disagreement_is_not_resolved_to_the_smaller_value(self):
        state = legacy_state()
        state["review_convergence"]["fix_round"] = 2
        state["execution_metrics"]["review_fix_rounds"] = 1

        recovery = recover_legacy_remediation_history(state)

        self.assertTrue(recovery.decision_required)
        self.assertIsNone(recovery.consumed_rounds)
        self.assertEqual(recovery.decision_candidates, (1, 2))

    def test_positive_legacy_counters_without_provenance_require_a_decision(self):
        for fix_round, metric_rounds in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(
                fix_round=fix_round,
                metric_rounds=metric_rounds,
            ):
                state = legacy_state()
                state["review_convergence"]["fix_round"] = fix_round
                state["execution_metrics"]["review_fix_rounds"] = metric_rounds

                recovery = recover_legacy_remediation_history(state)

                self.assertEqual(recovery.status, "migration_decision_required")
                self.assertTrue(recovery.decision_required)
                self.assertIsNone(recovery.consumed_rounds)
                self.assertEqual(recovery.batches, ())
                self.assertEqual(recovery.decision_candidates, (1,))
                self.assertIn(
                    "no reconstructible remediation provenance",
                    " ".join(recovery.issues),
                )

    def test_partial_forward_mutation_marker_in_revision_two_is_rejected(self):
        state = legacy_state()
        state["first_v053_mutation_at"] = "2026-07-17T00:00:00Z"

        with self.assertRaisesRegex(MigrationError, "partial first-v0.5.3"):
            plan_format_three_revision_three(state)

    def test_task_key_and_embedded_identity_mismatch_fails_closed(self):
        state = legacy_state(
            tasks={"T001": legacy_task("queued", id="T999")}
        )

        with self.assertRaisesRegex(MigrationError, "disagrees with embedded id"):
            plan_format_three_revision_three(state)


class MigrationTransactionPlanningTests(unittest.TestCase):
    def test_plan_records_deterministic_prepared_and_committed_identity(self):
        first = transaction_plan()
        second = plan_migration_transaction(
            migration_generation=7,
            source=V052_STATE_SCHEMA_VERSION,
            target=V053_STATE_SCHEMA_VERSION,
            artifacts={
                ".ai/herdr-dev-loop/loops/demo/tasks/T001.md": (
                    b"contract_schema_revision: absent\n",
                    b"contract_schema_revision: 2\n",
                ),
                ".ai/herdr-dev-loop/loops/demo/STATE.json": (
                    b'{"schema_revision":2}\n',
                    b'{"schema_revision":3}\n',
                ),
            },
        )

        self.assertEqual(first, second)
        self.assertEqual(first.prepared_marker["status"], "prepared")
        self.assertEqual(first.committed_marker["status"], "committed")
        self.assertEqual(
            first.committed_marker["prepared_marker_digest"],
            first.prepared_marker_digest,
        )
        self.assertEqual(first.committed_marker["first_v053_mutation_at"], "")
        self.assertRegex(first.archive_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(first.output_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            [artifact.path for artifact in first.artifacts],
            sorted(artifact.path for artifact in first.artifacts),
        )

    def test_plan_rejects_escaping_artifact_paths(self):
        with self.assertRaisesRegex(MigrationError, "repository-relative"):
            plan_migration_transaction(
                migration_generation=1,
                source=V052_STATE_SCHEMA_VERSION,
                target=V053_STATE_SCHEMA_VERSION,
                artifacts={"../STATE.json": (b"old", b"new")},
            )

    def test_digest_helper_is_stable(self):
        self.assertEqual(sha256_digest(b"state"), sha256_digest(b"state"))
        self.assertNotEqual(sha256_digest(b"state"), sha256_digest(b"output"))
        self.assertEqual(
            artifact_digests_from_bytes({"STATE.json": b"state"}),
            {"STATE.json": sha256_digest(b"state")},
        )

    def test_no_marker_requires_untouched_source_then_prepare(self):
        plan = transaction_plan()

        decision = decide_migration_recovery(
            plan,
            marker=None,
            current_artifact_digests=source_digests(plan),
        )
        self.assertEqual(decision.action, "prepare")

        changed = source_digests(plan)
        first_path = next(iter(changed))
        changed[first_path] = output_digests(plan)[first_path]
        decision = decide_migration_recovery(
            plan,
            marker=None,
            current_artifact_digests=changed,
        )
        self.assertTrue(decision.blocked)

    def test_prepared_marker_resumes_partial_apply_or_commits_complete_output(self):
        plan = transaction_plan()
        partial = source_digests(plan)
        first_path = next(iter(partial))
        partial[first_path] = output_digests(plan)[first_path]

        resumed = decide_migration_recovery(
            plan,
            marker=plan.prepared_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=partial,
        )
        self.assertEqual(resumed.action, "resume-apply")
        self.assertTrue(resumed.recovery_rollback_eligible)

        committed = decide_migration_recovery(
            plan,
            marker=plan.prepared_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=output_digests(plan),
        )
        self.assertEqual(committed.action, "write-committed-marker")

    def test_prepared_marker_can_begin_deterministic_recovery_rollback(self):
        plan = transaction_plan()
        decision = decide_migration_recovery(
            plan,
            marker=plan.prepared_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=output_digests(plan),
            requested_action="rollback",
        )
        self.assertEqual(decision.action, "begin-rollback")
        self.assertTrue(decision.recovery_rollback_eligible)

    def test_archive_mismatch_and_unknown_partial_bytes_fail_closed(self):
        plan = transaction_plan()
        mismatch = decide_migration_recovery(
            plan,
            marker=plan.prepared_marker,
            archive_digest=sha256_digest(b"wrong archive"),
            current_artifact_digests=source_digests(plan),
        )
        self.assertTrue(mismatch.blocked)
        self.assertIn("archive digest", " ".join(mismatch.issues))

        unknown = source_digests(plan)
        unknown[next(iter(unknown))] = sha256_digest(b"unknown partial bytes")
        mismatch = decide_migration_recovery(
            plan,
            marker=plan.prepared_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=unknown,
        )
        self.assertTrue(mismatch.blocked)
        self.assertIn("neither archived source", " ".join(mismatch.issues))

    def test_committed_marker_enables_rollback_and_first_mutation_once(self):
        plan = transaction_plan()
        observed = output_digests(plan)

        complete = decide_migration_recovery(
            plan,
            marker=plan.committed_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=observed,
        )
        self.assertEqual(complete.action, "complete")
        self.assertTrue(complete.rollback_eligible)
        self.assertTrue(complete.first_v053_mutation_eligible)

        mutation = decide_migration_recovery(
            plan,
            marker=plan.committed_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=observed,
            requested_action="first-mutation",
            requested_command="worker start T001",
        )
        self.assertEqual(mutation.action, "record-first-v053-mutation")

    def test_committed_marker_requires_complete_mutation_boundary(self):
        plan = transaction_plan()

        for missing_field in (
            "first_v053_mutation_at",
            "first_v053_mutation_command",
        ):
            with self.subTest(missing_field=missing_field):
                marker = plan.committed_marker
                del marker[missing_field]

                rollback = decide_migration_recovery(
                    plan,
                    marker=marker,
                    archive_digest=plan.archive_digest,
                    current_artifact_digests=output_digests(plan),
                    requested_action="rollback",
                )

                self.assertTrue(rollback.blocked)
                self.assertFalse(rollback.rollback_eligible)
                self.assertIn(missing_field, " ".join(rollback.issues))

    def test_first_mutation_marker_permanently_disables_rollback(self):
        plan = transaction_plan()
        marker = plan.committed_marker
        marker["first_v053_mutation_at"] = "2026-07-17T07:00:00Z"
        marker["first_v053_mutation_command"] = "worker start T001"

        rollback = decide_migration_recovery(
            plan,
            marker=marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=output_digests(plan),
            requested_action="rollback",
        )

        self.assertTrue(rollback.blocked)
        self.assertIn("forbidden", " ".join(rollback.issues))

    def test_first_mutation_observation_conflict_fails_closed(self):
        plan = transaction_plan()
        marker = plan.committed_marker
        marker["first_v053_mutation_at"] = "2026-07-17T07:00:00Z"
        marker["first_v053_mutation_command"] = "worker start T001"

        decision = decide_migration_recovery(
            plan,
            marker=marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=output_digests(plan),
            first_v053_mutation_at="2026-07-17T07:00:01Z",
            first_v053_mutation_command="worker start T001",
        )

        self.assertTrue(decision.blocked)
        self.assertIn("observations disagree", " ".join(decision.issues))

    def test_prepared_marker_cannot_claim_a_first_v053_mutation(self):
        plan = transaction_plan()
        marker = plan.prepared_marker
        marker["first_v053_mutation_at"] = "2026-07-17T07:00:00Z"
        marker["first_v053_mutation_command"] = "worker start T001"

        decision = decide_migration_recovery(
            plan,
            marker=marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=source_digests(plan),
        )

        self.assertTrue(decision.blocked)
        self.assertIn("only valid after migration commit", " ".join(decision.issues))

    def test_committed_mixed_tree_and_marker_identity_drift_fail_closed(self):
        plan = transaction_plan()
        partial = source_digests(plan)
        first_path = next(iter(partial))
        partial[first_path] = output_digests(plan)[first_path]
        decision = decide_migration_recovery(
            plan,
            marker=plan.committed_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=partial,
        )
        self.assertTrue(decision.blocked)
        self.assertIn("committed marker", " ".join(decision.issues))

        tampered = plan.prepared_marker
        tampered["output_digest"] = sha256_digest(b"different plan")
        decision = decide_migration_recovery(
            plan,
            marker=tampered,
            archive_digest=plan.archive_digest,
            current_artifact_digests=source_digests(plan),
        )
        self.assertTrue(decision.blocked)
        self.assertIn("does not match the plan", " ".join(decision.issues))

    def test_rollback_marker_resumes_until_every_artifact_is_archived(self):
        plan = transaction_plan()
        partial = output_digests(plan)
        first_path = next(iter(partial))
        partial[first_path] = source_digests(plan)[first_path]

        resumed = decide_migration_recovery(
            plan,
            marker=plan.rollback_prepared_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=partial,
        )
        self.assertEqual(resumed.action, "resume-rollback")

        finished = decide_migration_recovery(
            plan,
            marker=plan.rollback_prepared_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=source_digests(plan),
        )
        self.assertEqual(finished.action, "write-rolled-back-marker")

        complete = decide_migration_recovery(
            plan,
            marker=plan.rolled_back_marker,
            archive_digest=plan.archive_digest,
            current_artifact_digests=source_digests(plan),
        )
        self.assertEqual(complete.action, "complete")


if __name__ == "__main__":
    unittest.main()
