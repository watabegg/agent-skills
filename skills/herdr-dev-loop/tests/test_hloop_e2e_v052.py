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
SKILL_ROOT = RUNNER.parents[1]


AVAILABLE_RELEASE_DEPENDENCY = {
    "record_type": "herdr_dev_loop_release_dependencies",
    "schema_version": 1,
    "release": {
        "name": "herdr-dev-loop",
        "version": "0.5.3",
        "release_ready": True,
    },
    "required_release_evidence": [
        "hloop_codex_install_parity",
        "hloop_claude_install_parity",
        "companion_codex_install_parity",
        "companion_claude_install_parity",
        "codex_fresh_session_handshake",
        "claude_fresh_session_handshake",
    ],
    "dependencies": [
        {
            "name": "codex-review-multi-v2",
            "kind": "external_review_protocol",
            "required": True,
            "availability": "available",
            "blocking_reason": "",
            "minimum_compatible_version": "2.1.0",
            "distribution_identity": {
                "source": "https://example.invalid/codex-review-multi-v2.git",
                "immutable_id": "a" * 40,
                "version": "2.1.0",
                "digest_algorithm": "sha256-tree-v1",
                "content_digest": "sha256:" + "b" * 64,
            },
            "capability_manifest": {
                "relative_path": "capabilities/externally-planned-v1.json",
                "record_type": "external_review_protocol_adapter",
                "protocol": "codex-review-multi-v2",
                "required_capabilities": ["externally-planned-v1"],
            },
            "install_destinations": {
                "codex": "${CODEX_HOME:-$HOME/.codex}/skills/codex-review-multi-v2",
                "claude": "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/codex-review-multi-v2",
            },
        }
    ],
}

PROTOCOL_CAPABILITY = {
    "record_type": "external_review_protocol_adapter",
    "protocol": "codex-review-multi-v2",
    "source": "https://example.invalid/codex-review-multi-v2.git@" + "a" * 40,
    "version": "2.1.0",
    "content_digest": "sha256:" + "b" * 64,
    "capabilities": ["externally-planned-v1"],
}

# The historical behavioral fixtures still use a copied runtime, but the
# external adapter must now be the real validated sibling distribution.
AVAILABLE_RELEASE_DEPENDENCY = json.loads(
    (SKILL_ROOT / "release-dependencies.json").read_text(encoding="utf-8")
)
PROTOCOL_CAPABILITY = json.loads(
    (
        SKILL_ROOT.parent
        / "codex-review-multi-v2"
        / "capabilities"
        / "externally-planned-v1.json"
    ).read_text(encoding="utf-8")
)

SYNTHETIC_BOOTSTRAP = """\
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

runner = Path(sys.argv[1])
capability = sys.argv[2]
loader = importlib.machinery.SourceFileLoader("hloop_v052_synthetic_proxy", str(runner))
spec = importlib.util.spec_from_loader(loader.name, loader)
synthetic = importlib.util.module_from_spec(spec)
loader.exec_module(synthetic)

def prepare_final_review(fixture):
    synthetic._fixture_cli(
        fixture,
        "final-review",
        "prepare",
        "--protocol-capability",
        capability,
        "--json",
    )

original_write_final_manifest = synthetic._write_final_manifest

def write_final_manifest(fixture, **kwargs):
    manifest = original_write_final_manifest(fixture, **kwargs)
    execution = manifest.execution
    if execution is not None and manifest.review_manifest.review_id != execution.execution_id:
        review_manifest = synthetic.replace(
            manifest.review_manifest,
            review_id=execution.execution_id,
        )
        manifest = synthetic.replace(manifest, review_manifest=review_manifest)
        loop = synthetic.state_path(fixture["repo"], fixture["namespace"]).parent
        (loop / "reviews" / "final" / "MANIFEST.json").write_text(
            synthetic.json.dumps(manifest.to_record(), ensure_ascii=False, indent=2) + "\\n",
            encoding="utf-8",
        )
    return manifest

synthetic._prepare_final_review = prepare_final_review
synthetic._write_final_manifest = write_final_manifest
sys.argv = [str(runner), *sys.argv[3:]]
raise SystemExit(synthetic.main())
"""


class HLoopBoundedConvergenceE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release_fixture = tempfile.TemporaryDirectory(
            prefix="hloop-v052-release-pin-"
        )
        fixture_root = Path(cls.release_fixture.name)
        cls.skill_root = fixture_root / "herdr-dev-loop"
        shutil.copytree(
            SKILL_ROOT,
            cls.skill_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copytree(
            SKILL_ROOT.parent / "codex-review-multi-v2",
            fixture_root / "codex-review-multi-v2",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        cls.runner = cls.skill_root / "tests" / RUNNER.name
        (cls.skill_root / "release-dependencies.json").write_text(
            json.dumps(AVAILABLE_RELEASE_DEPENDENCY, indent=2) + "\n",
            encoding="utf-8",
        )
        cls.protocol_capability = (
            fixture_root
            / "codex-review-multi-v2"
            / "capabilities"
            / "externally-planned-v1.json"
        )
        cls.bootstrap = fixture_root / "run_synthetic_proxy.py"
        cls.bootstrap.write_text(SYNTHETIC_BOOTSTRAP, encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.release_fixture.cleanup()

    def run_scenario(self, name: str) -> dict:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                str(self.bootstrap),
                str(self.runner),
                str(self.protocol_capability),
                "--json",
                "--scenario",
                name,
            ],
            cwd=self.runner.parents[2],
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

    def test_manual_final_disposition_policy_fails_closed_before_state_mutation(self):
        evidence = self.run_scenario("manual-final-policy-fail-closed")
        self.assertTrue(evidence["rejected"])
        self.assertTrue(evidence["state_unchanged"])

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
            "hloop_t018_synthetic_e2e_runtime", str(self.runner)
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.assertIsNotNone(spec)
        synthetic = importlib.util.module_from_spec(spec)
        loader.exec_module(synthetic)

        root = Path(tempfile.mkdtemp(prefix="hloop-t018-final-outcome-"))
        original_review_manifest = synthetic._write_review_manifest
        raw_final_manifest = synthetic._write_final_manifest
        original_prepare_final_review = synthetic._prepare_final_review
        captured: dict[str, object] = {}
        try:
            def prepare_final_review(fixture):
                synthetic._fixture_cli(
                    fixture,
                    "final-review",
                    "prepare",
                    "--protocol-capability",
                    str(self.protocol_capability),
                    "--json",
                )

            synthetic._prepare_final_review = prepare_final_review

            def write_bound_final_manifest(fixture, **kwargs):
                manifest = raw_final_manifest(fixture, **kwargs)
                execution = manifest.execution
                if (
                    execution is not None
                    and manifest.review_manifest.review_id != execution.execution_id
                ):
                    review_manifest = synthetic.replace(
                        manifest.review_manifest,
                        review_id=execution.execution_id,
                    )
                    manifest = synthetic.replace(
                        manifest, review_manifest=review_manifest
                    )
                    loop = synthetic.state_path(
                        fixture["repo"], fixture["namespace"]
                    ).parent
                    (loop / "reviews" / "final" / "MANIFEST.json").write_text(
                        json.dumps(
                            manifest.to_record(), ensure_ascii=False, indent=2
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                return manifest

            original_final_manifest = write_bound_final_manifest
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
            synthetic._write_final_manifest = raw_final_manifest
            synthetic._prepare_final_review = original_prepare_final_review
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
