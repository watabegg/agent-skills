from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib import decisions  # noqa: E402


def pending_decision(
    *,
    decision_class: str = decisions.DECISION_ADVISORY,
    affected_task_ids: tuple[str, ...] = ("T001",),
) -> decisions.DecisionRecord:
    return decisions.DecisionRecord(
        decision_id="D001",
        decision_class=decision_class,
        status=decisions.DECISION_PENDING,
        question="Which storage contract should the public API use?",
        options=(
            decisions.DecisionOption(
                "existing-contract",
                "Keep the existing contract",
                ("No migration", "Keeps the current limitation"),
            ),
            decisions.DecisionOption(
                "new-contract",
                "Adopt the new contract",
                ("Supports the requirement", "Requires a migration"),
            ),
        ),
        recommendation=decisions.DecisionRecommendation(
            "existing-contract", "It has the lowest rollback cost."
        ),
        affected_task_ids=affected_task_ids,
        source_findings=("R001-F001",),
        created_from="reviews/R001/FINAL.md",
    )


class DecisionRecordTests(unittest.TestCase):
    def test_class_transition_changes_only_affected_task_behavior(self):
        tasks = {
            "T001": {"status": "queued", "depends_on": []},
            "T002": {"status": "queued", "depends_on": []},
        }
        advisory = pending_decision()
        advisory_plan = decisions.decision_aware_dispatch(tasks, (advisory,))
        self.assertEqual(advisory_plan.dispatchable_task_ids, ("T001", "T002"))
        self.assertFalse(advisory_plan.loop_blocked)

        deferred = decisions.reclassify_decision(
            advisory, decisions.DECISION_DEFERRED_USER
        )
        deferred_plan = decisions.decision_aware_dispatch(tasks, (deferred,))
        self.assertEqual(deferred_plan.decision_blocked_task_ids, ("T001",))
        self.assertEqual(deferred_plan.dispatchable_task_ids, ("T002",))
        self.assertFalse(deferred_plan.loop_blocked)

        blocking = decisions.reclassify_decision(
            deferred, decisions.DECISION_BLOCKING_USER
        )
        blocking_plan = decisions.decision_aware_dispatch(tasks, (blocking,))
        self.assertEqual(blocking_plan.decision_blocked_task_ids, ("T001",))
        self.assertEqual(blocking_plan.dispatchable_task_ids, ("T002",))
        self.assertFalse(blocking_plan.loop_blocked)

    def test_record_round_trip_preserves_options_tradeoffs_and_recommendation(self):
        record = pending_decision(
            decision_class=decisions.DECISION_DEFERRED_USER
        )
        restored = decisions.DecisionRecord.from_record(record.to_record())
        self.assertEqual(restored, record)
        self.assertEqual(
            restored.options[1].tradeoffs,
            ("Supports the requirement", "Requires a migration"),
        )
        self.assertEqual(restored.recommendation.option_id, "existing-contract")

    def test_user_decision_requires_an_explicit_task_scope(self):
        with self.assertRaisesRegex(
            decisions.DecisionValidationError, "at least one affected task"
        ):
            pending_decision(
                decision_class=decisions.DECISION_DEFERRED_USER,
                affected_task_ids=(),
            )

    def test_answer_remains_blocking_until_manager_resolution(self):
        record = pending_decision(
            decision_class=decisions.DECISION_DEFERRED_USER
        )
        answered = decisions.record_response(
            record,
            decisions.DecisionResponse(
                responded_by="user",
                responded_at="2026-07-15T01:00:00Z",
                selected_option="new-contract",
                free_text="Migrate now.",
            ),
        )
        self.assertEqual(answered.status, decisions.DECISION_ANSWERED)
        self.assertTrue(answered.blocks_affected_tasks)

        resolved = decisions.resolve_decision(
            answered,
            outcome=decisions.DECISION_ACCEPTED,
            rationale="The response explicitly selects the new contract.",
            resolved_by="manager",
            resolved_at="2026-07-15T01:01:00Z",
        )
        self.assertEqual(resolved.status, decisions.DECISION_ACCEPTED)
        self.assertEqual(resolved.resolution.selected_option, "new-contract")
        self.assertFalse(resolved.blocks_affected_tasks)

        tasks = {"T001": {"status": "queued", "depends_on": []}}
        self.assertEqual(
            decisions.decision_aware_dispatch(tasks, (resolved,)).dispatchable_task_ids,
            ("T001",),
        )

    def test_conflicting_second_response_and_resolution_are_rejected(self):
        record = pending_decision(
            decision_class=decisions.DECISION_BLOCKING_USER
        )
        response = decisions.DecisionResponse(
            responded_by="user",
            responded_at="2026-07-15T01:00:00Z",
            selected_option="existing-contract",
        )
        answered = decisions.record_response(record, response)
        self.assertEqual(decisions.record_response(answered, response), answered)
        with self.assertRaises(decisions.DecisionTransitionError):
            decisions.record_response(
                answered,
                decisions.DecisionResponse(
                    responded_by="user",
                    responded_at="2026-07-15T01:02:00Z",
                    selected_option="new-contract",
                ),
            )

        resolved = decisions.resolve_decision(
            answered,
            outcome=decisions.DECISION_ACCEPTED,
            rationale="Confirmed response.",
            resolved_by="manager",
            resolved_at="2026-07-15T01:03:00Z",
        )
        with self.assertRaises(decisions.DecisionTransitionError):
            decisions.resolve_decision(
                resolved,
                outcome=decisions.DECISION_REJECTED,
                rationale="Conflicting outcome.",
                resolved_by="manager",
                resolved_at="2026-07-15T01:04:00Z",
            )


class ScopedDependencyTests(unittest.TestCase):
    def test_explicit_and_record_scoped_dependencies_are_both_evaluated(self):
        record = pending_decision(
            decision_class=decisions.DECISION_DEFERRED_USER
        )
        tasks = {
            "T000": {"status": "running"},
            "T001": {"status": "queued", "depends_on": ["T000"]},
            "T002": {
                "status": "queued",
                "depends_on_decisions": ["D999"],
            },
        }
        first = decisions.evaluate_task_dependencies(
            "T001", tasks["T001"], tasks, (record,)
        )
        self.assertEqual(first.pending_task_ids, ("T000",))
        self.assertEqual(first.blocking_decision_ids, ("D001",))
        self.assertFalse(first.dispatchable)

        second = decisions.evaluate_task_dependencies(
            "T002", tasks["T002"], tasks, (record,)
        )
        self.assertEqual(second.unknown_decision_ids, ("D999",))
        self.assertFalse(second.dispatchable)

    def test_unanswered_decision_blocks_loop_only_after_safe_work_is_exhausted(self):
        record = pending_decision(
            decision_class=decisions.DECISION_BLOCKING_USER
        )
        all_affected = {"T001": {"status": "queued", "depends_on": []}}
        blocked = decisions.decision_aware_dispatch(all_affected, (record,))
        self.assertTrue(blocked.loop_blocked)
        self.assertEqual(blocked.blocking_decision_ids, ("D001",))

        active = {
            **all_affected,
            "T002": {"status": "running", "depends_on": []},
        }
        self.assertFalse(
            decisions.decision_aware_dispatch(active, (record,)).loop_blocked
        )
        self.assertFalse(
            decisions.decision_aware_dispatch(
                all_affected, (record,), safe_work_remaining=True
            ).loop_blocked
        )

    def test_decision_scope_propagates_through_unmerged_task_dependencies(self):
        record = pending_decision(
            decision_class=decisions.DECISION_DEFERRED_USER
        )
        tasks = {
            "T001": {"status": "queued", "depends_on": []},
            "T002": {"status": "queued", "depends_on": ["T001"]},
        }
        plan = decisions.decision_aware_dispatch(tasks, (record,))
        self.assertEqual(plan.decision_blocked_task_ids, ("T001", "T002"))
        self.assertTrue(plan.loop_blocked)

        tasks["T001"] = {"status": "merged", "depends_on": []}
        after_merge = decisions.decision_aware_dispatch(tasks, (record,))
        self.assertEqual(after_merge.decision_blocked_task_ids, ())
        self.assertEqual(after_merge.dispatchable_task_ids, ("T002",))
        self.assertFalse(after_merge.loop_blocked)

    def test_schema_captures_v05_decision_contract(self):
        schema_path = (
            Path(__file__).parents[1]
            / "references"
            / "schemas"
            / "decision.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertIn("class", schema["required"])
        self.assertEqual(
            set(schema["properties"]["class"]["enum"]),
            {
                decisions.DECISION_ADVISORY,
                decisions.DECISION_DEFERRED_USER,
                decisions.DECISION_BLOCKING_USER,
            },
        )
        self.assertEqual(
            schema["properties"]["options"]["items"]["required"],
            ["id", "label", "tradeoffs"],
        )
        self.assertIn("response", schema["properties"])
        self.assertIn("resolution", schema["properties"])


if __name__ == "__main__":
    unittest.main()
