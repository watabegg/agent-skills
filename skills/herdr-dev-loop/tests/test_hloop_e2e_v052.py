from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
