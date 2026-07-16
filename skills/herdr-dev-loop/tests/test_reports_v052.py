from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError:  # pragma: no cover - optional for skill consumers
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.requirements import EvidenceRef, RequirementProgress  # noqa: E402
from hloop_lib.reports import (  # noqa: E402
    ExecutionMetrics,
    FollowUpProjection,
    ManualFinalReviewProjection,
    ManagerInvocation,
    OutcomeGate,
    OutcomeModelError,
    ReviewConvergenceProjection,
    OutcomeReport,
    draft_outcome,
    final_outcome,
    report_projections_from_state,
    render_outcome_markdown,
)


NOW = "2026-07-16T00:00:00+00:00"
HEAD_SHA = "a" * 40


def verified_progress() -> RequirementProgress:
    return RequirementProgress(
        "REQ-001",
        status="verified",
        task_ids=("T005",),
        evidence=(
            EvidenceRef(
                kind="artifact",
                reference="results/T005/result.md",
                verified_by="hloop",
                head_sha=HEAD_SHA,
            ),
            EvidenceRef(
                kind="test",
                reference="python3 -m unittest test_reports_v052.py",
                verified_by="hloop",
                head_sha=HEAD_SHA,
                result="passed",
            ),
        ),
    )


def passing_gate() -> OutcomeGate:
    return OutcomeGate(
        name="validation",
        status="passed",
        evidence_refs=("test_reports_v052.py",),
        target_sha=HEAD_SHA,
        verified_by="hloop",
    )


def projection_set() -> dict[str, object]:
    return {
        "manager_invocation": ManagerInvocation(
            provider="codex",
            model="gpt-5.6-luna",
            reasoning_effort="max",
            recorded_at=NOW,
        ),
        "execution_metrics": ExecutionMetrics(
            planned_task_count=2,
            remediation_task_count=1,
            task_origin_counts={"planned": 2, "finding": 1},
            scope_revision_counts={"1": 3},
            review_fix_rounds=2,
            candidate_count=3,
            confirmed_count=2,
            finding_disposition_counts={"fix_now": 1, "defer_follow_up": 1},
            review_completed_count=1,
            stale_review_count=1,
            worker_count=2,
            planned_task_completed=True,
            effective_parallelism=1.0,
        ),
        "follow_ups": FollowUpProjection(
            count=1,
            references=("follow-ups/F001.md",),
            issue_keys=("fu:v1:sha256:" + "b" * 64,),
        ),
        "review_convergence": ReviewConvergenceProjection(
            status="converged",
            target_sha=HEAD_SHA,
            fix_round=2,
            max_fix_rounds=2,
            authorized_extra_rounds=0,
            verified_actionable_findings=0,
            artifact_refs=("reviews/R001.md",),
        ),
        "manual_final_review": ManualFinalReviewProjection(
            status="passed",
            certification_id="C001",
            target_sha=HEAD_SHA,
            prepared_plan="reviews/final/PLAN.json",
            prepared_plan_digest="sha256:" + "c" * 64,
            manifest="reviews/final/MANIFEST.json",
            report="reviews/final/FINAL.md",
            manifest_complete=True,
            shortfall_count=0,
            verified_actionable_findings=0,
            lane_completed_count=4,
            lane_count=4,
        ),
    }


class ProjectionTests(unittest.TestCase):
    def test_finding_disposition_projection_counts_each_axis(self):
        metrics = ExecutionMetrics.from_finding_dispositions(
            (
                {
                    "fact_status": "confirmed",
                    "origin": "introduced",
                    "contract_relation": "in_scope",
                    "decision_requirement": "none",
                    "disposition": "fix_now",
                },
                {
                    "fact_status": "confirmed",
                    "origin": "unrelated-pre-existing",
                    "contract_relation": "outside_release",
                    "decision_requirement": "spec",
                    "disposition": "defer_follow_up",
                },
            )
        )

        self.assertEqual(metrics.candidate_count, 2)
        self.assertEqual(metrics.confirmed_count, 2)
        self.assertEqual(metrics.finding_disposition_counts["fix_now"], 1)
        self.assertEqual(metrics.finding_contract_relation_counts["outside_release"], 1)
        self.assertEqual(metrics.finding_decision_requirement_counts["spec"], 1)

    def test_legacy_outcome_record_has_no_v052_fields(self):
        report = draft_outcome(
            run_id="run-001",
            goal="legacy report",
            generated_at=NOW,
            requirement_progress=(RequirementProgress("REQ-001"),),
            gates=(OutcomeGate(name="validation", status="pending"),),
            integration_target_sha="",
            current_branch_sha="",
        )

        record = report.to_record()
        self.assertNotIn("manager_invocation", record)
        self.assertNotIn("execution_metrics", record)
        self.assertNotIn("postmortem_warnings", record)
        self.assertEqual(OutcomeReport.from_record(record), report)

    def test_state_projection_is_round_trippable_and_warns_without_mutating_state(self):
        state = {
            "manager_invocation": projection_set()["manager_invocation"].to_record(),
            "execution_metrics": projection_set()["execution_metrics"].to_record(),
            "follow_ups": projection_set()["follow_ups"].to_record(),
            "review_convergence": projection_set()["review_convergence"].to_record(),
            "manual_final_review": ManualFinalReviewProjection(
                status="incomplete", manifest_complete=False, shortfall_count=1
            ).to_record(),
        }
        before = json.loads(json.dumps(state))
        projections = report_projections_from_state(state)

        self.assertEqual(state, before)
        self.assertEqual(projections["follow_ups"].count, 1)
        warnings = tuple(projections["postmortem_warnings"])
        self.assertTrue(any(item.startswith("remediation-task-growth:") for item in warnings))
        self.assertTrue(any(item.startswith("review-shortfall-ratio-high:") for item in warnings))
        self.assertTrue(any(item.startswith("effective-parallelism-low:") for item in warnings))
        self.assertIn("manual-final-incomplete: 1 shortfalls", warnings)

    def test_outcome_projection_round_trip_and_markdown_include_metrics(self):
        report = draft_outcome(
            run_id="run-001",
            goal="bounded convergence",
            generated_at=NOW,
            requirement_progress=(RequirementProgress("REQ-001"),),
            gates=(OutcomeGate(name="validation", status="pending"),),
            integration_target_sha="",
            current_branch_sha="",
            **projection_set(),
        )

        restored = OutcomeReport.from_record(report.to_record())
        self.assertEqual(restored, report)
        rendered = render_outcome_markdown(report)
        self.assertIn("Manager invocation: codex/gpt-5.6-luna/max", rendered)
        self.assertIn("Tasks: 2 planned, 1 remediation", rendered)
        self.assertIn("Task origins: finding=1, planned=2", rendered)
        self.assertIn("Review attempts: 1 completed, 1 stale, 0 aborted, 0 timeout", rendered)
        self.assertIn("Follow-ups: 1", rendered)
        self.assertIn("Manual review completeness: complete, shortfalls 0", rendered)
        self.assertIn("effective-parallelism-low: 1 with 2 workers", rendered)

    def test_final_outcome_requires_complete_manual_final_when_projected(self):
        incomplete = projection_set()
        incomplete["manual_final_review"] = ManualFinalReviewProjection(
            status="incomplete", manifest_complete=False, shortfall_count=1
        )
        with self.assertRaisesRegex(OutcomeModelError, "complete manual final review"):
            final_outcome(
                run_id="run-001",
                goal="bounded convergence",
                generated_at=NOW,
                requirement_progress=(verified_progress(),),
                gates=(passing_gate(),),
                integration_target_sha=HEAD_SHA,
                current_branch_sha=HEAD_SHA,
                **incomplete,
            )

    def test_final_outcome_accepts_follow_up_without_invalidating_complete_review(self):
        projections = projection_set()
        report = final_outcome(
            run_id="run-001",
            goal="bounded convergence",
            generated_at=NOW,
            requirement_progress=(verified_progress(),),
            gates=(passing_gate(),),
            integration_target_sha=HEAD_SHA,
            current_branch_sha=HEAD_SHA,
            **projections,
        )
        self.assertEqual(report.follow_ups.count, 1)
        self.assertTrue(report.manual_final_review.complete)


@unittest.skipUnless(jsonschema is not None, "jsonschema is not installed")
class OutcomeSchemaTests(unittest.TestCase):
    def test_schema_accepts_legacy_and_projected_records(self):
        outcome_path = SCHEMAS / "outcome.schema.json"
        progress_path = SCHEMAS / "progress.schema.json"
        schema = json.loads(outcome_path.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema_path in (progress_path, outcome_path):
            registry = registry.with_resource(
                schema_path.resolve().as_uri(),
                Resource.from_contents(json.loads(schema_path.read_text())),
            )
        validator = jsonschema.Draft202012Validator(
            {"$schema": schema["$schema"], "$ref": outcome_path.resolve().as_uri()},
            registry=registry,
        )
        self.assertIn("execution_metrics", schema["properties"])
        self.assertIn("manual_final_review", schema["properties"])

        legacy = draft_outcome(
            run_id="run-001",
            goal="legacy report",
            generated_at=NOW,
            requirement_progress=(RequirementProgress("REQ-001"),),
            gates=(OutcomeGate(name="validation", status="pending"),),
            integration_target_sha="",
            current_branch_sha="",
        ).to_record()
        projected = draft_outcome(
            run_id="run-001",
            goal="bounded convergence",
            generated_at=NOW,
            requirement_progress=(RequirementProgress("REQ-001"),),
            gates=(OutcomeGate(name="validation", status="pending"),),
            integration_target_sha="",
            current_branch_sha="",
            **projection_set(),
        ).to_record()
        self.assertEqual(list(validator.iter_errors(legacy)), [])
        self.assertEqual(list(validator.iter_errors(projected)), [])


if __name__ == "__main__":
    unittest.main()
