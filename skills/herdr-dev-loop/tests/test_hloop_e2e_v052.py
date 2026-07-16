from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).with_name("run_synthetic_e2e.py")


class HLoopBoundedConvergenceE2ETests(unittest.TestCase):
    def run_scenario(self, name: str) -> dict:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--json", "--scenario", name],
            cwd=RUNNER.parents[2],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{name} failed\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["scenario_count"], 1)
        self.assertEqual(payload["scenarios"][0]["name"], name)
        self.assertEqual(payload["scenarios"][0]["status"], "passed")
        return payload["scenarios"][0]["evidence"]

    def test_remediation_convergence_and_batch_proxies(self):
        evidence = self.run_scenario("remediation-convergence")
        self.assertEqual(evidence["parallel_workers"], 3)
        self.assertFalse(evidence["review_per_merge"])
        self.assertTrue(evidence["validation_scales_by_batch"])
        self.assertTrue(evidence["remediation_findings_coalesced"])
        self.assertEqual(evidence["final_status"], "passed")

    def test_batch_review_cadence_pins_each_closed_batch_head(self):
        evidence = self.run_scenario("batch-review-cadence")
        self.assertEqual(evidence["review_events"], 2)
        self.assertTrue(evidence["targets_advanced"])
        self.assertNotEqual(
            evidence["first_review_target_sha"], evidence["second_review_target_sha"]
        )
        self.assertTrue(evidence["future_queued_tasks_did_not_block"])
        self.assertTrue(evidence["open_batch_kept_closed"])

    def test_batch_performance_validation_reuse_and_conflict_avoidance(self):
        evidence = self.run_scenario("batch-performance-validation-reuse")
        self.assertTrue(evidence["low_parallelism_warning"])
        self.assertTrue(evidence["replan_required"])
        self.assertTrue(evidence["scheduler_avoided_overlap"])
        self.assertTrue(evidence["direct_start_blocked"])
        self.assertTrue(evidence["validation_reused"])
        self.assertTrue(evidence["validation_invalidated_by_command_set"])
        self.assertTrue(evidence["validation_invalidated_by_resolved_config"])

    def test_scope_expansion_creates_follow_up_without_gate_invalidation(self):
        evidence = self.run_scenario("scope-expansion-follow-up")
        self.assertEqual(evidence["follow_up_id"], "F001")
        self.assertFalse(evidence["gate_invalidated"])
        self.assertEqual(evidence["created_tasks"], 0)
        self.assertEqual(evidence["fixture_task_count"], 1)

    def test_convergence_exhaustion_has_no_automatic_third_round(self):
        evidence = self.run_scenario("two-round-exhaustion")
        self.assertEqual(evidence["recorded_fix_round"], 2)
        self.assertFalse(evidence["automatic_third_round"])
        self.assertTrue(evidence["dispatch_frozen"])
        self.assertEqual(evidence["canonical_fix_round_after_authorized_reopen"], 3)

    def test_user_stop_freezes_new_dispatch_but_allows_safe_harvest(self):
        evidence = self.run_scenario("user-stop-freeze")
        self.assertEqual(evidence["freeze_status"], "active")
        self.assertEqual(evidence["allowed_running_role_ids"], ["T001"])
        self.assertTrue(evidence["safe_harvest_while_review_waits"])
        self.assertEqual(evidence["new_task_events"], 0)
        self.assertEqual(evidence["new_reviewer_events"], 0)
        self.assertEqual(evidence["new_gap_events"], 0)

    def test_incomplete_manual_final_can_retry_on_same_sha(self):
        evidence = self.run_scenario("manual-final-retry-same-sha")
        self.assertEqual(evidence["initial_status"], "incomplete")
        self.assertEqual(evidence["retry_status"], "passed")
        self.assertTrue(evidence["same_target_sha"])
        self.assertIn("incomplete", evidence["attempt_history_statuses"])
        self.assertIn("passed", evidence["attempt_history_statuses"])

    def test_user_authorized_reopen_after_final_finding_converges(self):
        evidence = self.run_scenario("manual-final-authorized-reopen")
        self.assertEqual(evidence["initial_status"], "failed")
        self.assertTrue(evidence["reopened_with_user_input"])
        self.assertEqual(evidence["remediation_round"], 1)
        self.assertEqual(evidence["final_status"], "passed")
        self.assertIn("failed", evidence["attempt_history_statuses"])
        self.assertIn("passed", evidence["attempt_history_statuses"])

    def test_finish_projects_state_metrics_into_final_outcome(self):
        evidence = self.run_scenario("final-gate-and-finish")
        self.assertTrue(evidence["metrics_projected"])
        self.assertEqual(evidence["phase"], "done")

    def test_finish_projects_manual_risks_and_all_finding_axis_maps(self):
        loader = importlib.machinery.SourceFileLoader(
            "hloop_t018_synthetic_e2e_runtime", str(RUNNER)
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.assertIsNotNone(spec)
        synthetic = importlib.util.module_from_spec(spec)
        loader.exec_module(synthetic)

        root = Path(tempfile.mkdtemp(prefix="hloop-t018-final-outcome-"))
        original_review_manifest = synthetic._write_review_manifest
        original_final_manifest = synthetic._write_final_manifest
        captured: dict[str, object] = {}
        try:
            repo = synthetic.make_repo(root)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root / "home"),
                    "HLOOP_CONFIG_HOME": str(root / "config-home"),
                    "XDG_CONFIG_HOME": str(root / "xdg"),
                    "HERDR_ENV": "1",
                }
            )
            context = {
                "root": root,
                "repo": repo,
                "env": env,
                "namespace": "synthetic-e2e",
                "runtime_version": (synthetic.SKILL_ROOT / "VERSION")
                .read_text(encoding="utf-8")
                .strip(),
            }

            def write_review_manifest(fixture, plan, **kwargs):
                group = synthetic.hloop_review.ReviewGroupPlan.from_record(
                    plan["review_plan"]
                )
                lane = group.expected_lanes[0]
                introduced = synthetic.replace(
                    synthetic._candidate_for_lane(
                        plan["target_sha"],
                        lane,
                        finding_id="RISK-INTRODUCED",
                        severity="P2",
                        title="synthetic refuted introduced finding",
                        origin="introduced",
                        contract_relation="in_scope",
                        disposition="discard",
                        release_effect="non_blocking",
                    ),
                    fact_status="refuted",
                    decision_requirement="none",
                )
                unrelated = synthetic.replace(
                    synthetic._candidate_for_lane(
                        plan["target_sha"],
                        lane,
                        finding_id="RISK-EXTERNAL",
                        severity="P2",
                        title="synthetic refuted external finding",
                        origin="unrelated-pre-existing",
                        contract_relation="outside_release",
                        disposition="discard",
                        release_effect="non_blocking",
                    ),
                    fact_status="refuted",
                    decision_requirement="spec",
                )
                return original_review_manifest(
                    fixture,
                    plan,
                    candidates=(introduced, unrelated),
                    **kwargs,
                )

            def write_final_manifest(fixture, **kwargs):
                result = original_final_manifest(fixture, **kwargs)
                report_path = (
                    synthetic.state_path(fixture["repo"], fixture["namespace"])
                    .parent
                    / "reviews"
                    / "final"
                    / "FINAL.md"
                )
                report = synthetic.hloop.read_frontmatter(report_path)
                report["residual_risks"] = [
                    "insufficient_evidence: provider verification was unavailable",
                    "external_dependency: external provider was not exercised",
                ]
                report["follow_up_refs"] = ["follow-ups/F001.md"]
                report_path.write_text(
                    synthetic.hloop.frontmatter(report)
                    + "\n# Synthetic Manual Final Review\n",
                    encoding="utf-8",
                )
                captured["fixture"] = fixture
                return result

            synthetic._write_review_manifest = write_review_manifest
            synthetic._write_final_manifest = write_final_manifest
            evidence = synthetic.scenario_finish(context)

            fixture = captured["fixture"]
            state_path = synthetic.state_path(fixture["repo"], fixture["namespace"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            report_path = state_path.parent / "reports" / "FINAL.md"
            report = report_path.read_text(encoding="utf-8")
            manual = state["manual_final_review"]

            self.assertTrue(evidence["metrics_projected"])
            self.assertEqual(state["phase"], "done")
            self.assertEqual(manual["target_sha"], state["final_target_sha"])
            self.assertEqual(
                manual["residual_risks"],
                [
                    "insufficient_evidence: provider verification was unavailable",
                    "external_dependency: external provider was not exercised",
                ],
            )
            self.assertEqual(manual["follow_up_refs"], ["follow-ups/F001.md"])
            accepted_line = next(
                line for line in report.splitlines() if line.startswith("- Accepted risks:")
            )
            self.assertIn("synthetic residual compatibility risk", accepted_line)
            self.assertNotIn("insufficient_evidence", accepted_line)
            self.assertIn(
                "- Residual risks: insufficient_evidence: provider verification was unavailable; "
                "external_dependency: external provider was not exercised",
                report,
            )
            self.assertIn("Finding origin counts: introduced=1, unrelated-pre-existing=1", report)
            self.assertIn("Finding contract relation counts: in_scope=1, outside_release=1", report)
            self.assertIn("Finding decision requirement counts: none=1, spec=1", report)
            self.assertIn("Finding disposition counts: discard=2", report)
        finally:
            synthetic._write_review_manifest = original_review_manifest
            synthetic._write_final_manifest = original_final_manifest
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
