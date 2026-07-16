from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
from hloop_lib import review as hloop_review
from hloop_lib.certification import CertificationPlan, FinalReviewManifest
from hloop_lib.review import FindingCandidate


loader = importlib.machinery.SourceFileLoader("hloop_convergence_v052_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class HLoopConvergenceV052Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo, check=True)
        self.namespace = "convergence-fixture"
        self.run_cli(
            "init",
            "--goal",
            "convergence fixture",
            "--integration",
            "main",
            "--max-gap-auditors",
            "0",
            "--validation",
            "true",
        )
        code, out, err = self.run_cli("release-scope", "lock")
        self.assertEqual((code, err), (0, ""), out)
        self.state_path = (
            self.repo
            / ".ai"
            / "herdr-dev-loop"
            / "loops"
            / self.namespace
            / "STATE.json"
        )
        self._make_ready_state()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = hloop.main(
                ["--repo", str(self.repo), "--namespace", self.namespace, *args]
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def test_new_loop_initializes_manager_invocation_and_execution_metrics(self):
        state = self.state()
        self.assertIn("manager_invocation", state)
        self.assertIn("execution_metrics", state)
        invocation = state["manager_invocation"]
        if not any(invocation.get(field) for field in ("provider", "model", "reasoning_effort")):
            self.assertTrue(invocation["unavailable_reason"])
        self.assertIsNone(state["execution_metrics"]["effective_parallelism"])
        self.assertEqual(state["execution_metrics"]["planned_task_count"], 0)

    def _make_ready_state(self) -> None:
        state = self.state()
        target = subprocess.check_output(
            ["git", "rev-parse", "main"], cwd=self.repo, text=True
        ).strip()
        state["tasks"] = {"T001": {"status": "merged"}}
        state["last_validation"] = {
            "head_sha": target,
            "results": [{"command": "true", "result": "passed"}],
        }
        state["integration_head_sha"] = target
        state["completion_target_sha"] = target
        state["batches"] = {}
        state["current_batch_id"] = ""
        self.save_state(state)

    def prepare_convergence(self) -> None:
        code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (0, ""), out)
        code, out, err = self.run_cli("review", "convergence", "prepare", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertFalse(json.loads(out)["automatic_reviewer_started"])

    def complete_convergence_manifest(self) -> None:
        loop = self.state_path.parent
        plan = json.loads((loop / "reviews" / "convergence" / "PLAN.json").read_text(encoding="utf-8"))
        group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
        manifest = hloop_review.ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=hloop_review.plan_verification(group, ()),
            verifications=(),
        )
        (loop / "reviews" / "convergence" / "MANIFEST.json").write_text(
            json.dumps(manifest.to_record(), indent=2) + "\n", encoding="utf-8"
        )

    def write_complete_final_report(
        self, plan: CertificationPlan, evidence: FinalReviewManifest
    ) -> None:
        loop = self.state_path.parent
        inventory = hloop._changed_file_inventory(self.repo, plan.base_sha, plan.target_sha)
        report = {
            "protocol": plan.protocol,
            "certification_id": plan.certification_id,
            "prepared_plan_digest": plan.digest,
            "base_sha": plan.base_sha,
            "target_sha": plan.target_sha,
            "scope_revision": plan.scope_revision,
            "source_snapshot_revision": plan.source_snapshot_revision,
            "source_digest": plan.source_digest,
            "lane_count": len(plan.lane_plan),
            "lane_names": [
                f"{lane.provider}:{lane.lane_id}" for lane in plan.lane_plan
            ],
            "lane_outcomes": [
                f"{result.provider}:{result.lane_id}:{result.status}"
                for result in evidence.review_manifest.lane_results
            ],
            "coordinator_session_id": "fixture-coordinator",
            "diff_inventory": inventory or ["(no changed files)"],
            "verification_records": [
                record.fingerprint
                for record in evidence.review_manifest.verifications
            ],
            "verification_shortfall": len(
                evidence.review_manifest.verification_plan.shortfalls
            ),
            "incomplete_findings": list(evidence.completeness.incomplete_findings),
            "manifest_complete": evidence.manifest_complete,
            "verified_actionable_findings": evidence.recomputed_verified_actionable_count,
            "findings": [
                finding.fingerprint
                for finding in evidence.review_manifest.findings
            ],
            "residual_risks": [],
            "follow_up_refs": [],
            "patch_verdict": evidence.patch_verdict,
            "completed_at": hloop.now_iso(),
        }
        (loop / "reviews" / "final" / "FINAL.md").write_text(
            hloop.frontmatter(report) + "\n# Fixture Manual Final Review\n",
            encoding="utf-8",
        )

    def write_policy_manifest(self, *, outside_release: bool) -> None:
        loop = self.state_path.parent
        plan = json.loads(
            (loop / "reviews" / "convergence" / "PLAN.json").read_text(encoding="utf-8")
        )
        group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
        candidate = FindingCandidate(
            finding_id="FND-001",
            provider="codex",
            head_sha=plan["target_sha"],
            discovering_agent="codex-reviewer",
            severity="P2",
            confidence=0.95,
            title="axis-derived finding",
            file_path="src/example.py",
            line=1,
            symbol="run",
            trigger="the policy trigger",
            product_impact="the policy impact",
            origin="unrelated-pre-existing" if outside_release else "introduced",
            proposed_fix="repair the policy path",
            fact_status="confirmed",
            contract_relation="outside_release" if outside_release else "in_scope",
            decision_requirement="none",
            disposition="defer_follow_up" if outside_release else "fix_now",
            release_effect="non_blocking" if outside_release else "blocking",
        )
        finding = hloop_review.normalize_findings((candidate,))[0]
        verification_plan = hloop_review.plan_verification(group, (finding,))
        verifications = tuple(
            hloop_review.VerificationRecord.from_assignment(
                assignment,
                fact_status="confirmed",
                ignore_status="must_not_ignore",
                decision_status="none",
                progress_without_decision="yes",
                severity="P2",
                # Deliberately disagree with the old recommendation.  New
                # runtime records must use the disposition axes instead.
                recommended_action="fix_task",
            )
            for assignment in verification_plan.assignments
        )
        manifest = hloop_review.ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(
                lane.result(
                    finding_count=sum(
                        1
                        for item in finding.candidates
                        if item.discovering_agent == lane.agent_label
                    )
                )
                for lane in group.expected_lanes
            ),
            findings=(finding,),
            verification_plan=verification_plan,
            verifications=verifications,
        )
        (loop / "reviews" / "convergence" / "MANIFEST.json").write_text(
            json.dumps(manifest.to_record(), indent=2) + "\n", encoding="utf-8"
        )

    def test_convergence_counts_use_axes_not_legacy_recommendation(self):
        self.prepare_convergence()
        self.write_policy_manifest(outside_release=True)
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "converged")
        self.assertEqual(payload["verified_actionable_findings"], 0)
        self.assertEqual(payload["release_blocking_findings"], 0)

    def test_readiness_fails_closed_without_changed_file_and_special_verification(self):
        target = self.state()["integration_head_sha"]
        state = self.state()
        state.pop("changed_file_validation", None)
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=["README.md"]):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (2, ""), out)
        blocked = json.loads(out)
        self.assertIn("changed-file validation evidence mapping is missing", blocked["errors"])
        self.assertIn("special verification evidence is missing: public-docs", blocked["errors"])

        state = self.state()
        state["changed_file_validation"] = {
            "status": "passed",
            "target_sha": target,
            "paths": ["README.md"],
            "mapping": {"README.md": ["validation.log"]},
        }
        state["special_verification_evidence"] = {
            "public-docs": {
                "status": "passed",
                "target_sha": target,
                "references": ["docs-review.log"],
            }
        }
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=["README.md"]):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (0, ""), out)
        ready = json.loads(out)
        self.assertEqual(ready["checks"]["changed_file_validation"]["status"], "passed")
        self.assertEqual(ready["checks"]["special_verification"]["status"], "passed")

    def test_readiness_requires_first_class_follow_up_for_deferred_candidate(self):
        fingerprint = "sha256:" + "d" * 64
        state = self.state()
        state["defer_follow_up_fingerprints"] = [fingerprint]
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=[]):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (2, ""), out)
        blocked = json.loads(out)
        self.assertTrue(
            any("first-class follow-up artifacts" in item for item in blocked["errors"])
        )

        loop = self.state_path.parent
        follow_up_path = loop / "follow-ups" / "F001.md"
        follow_up_path.parent.mkdir(parents=True, exist_ok=True)
        follow_up_path.write_text(
            hloop.frontmatter(
                {
                    "id": "F001",
                    "source_review_fingerprints": [fingerprint],
                    "status": "deferred",
                }
            )
            + "\n# Follow-up\n",
            encoding="utf-8",
        )
        state = self.state()
        state["follow_ups"]["artifact_refs"] = [
            str(follow_up_path.relative_to(self.repo))
        ]
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=[]):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(
            json.loads(out)["checks"]["follow_up_completeness"]["status"],
            "passed",
        )

    def test_convergence_retains_confirmed_in_scope_blocking_finding(self):
        self.prepare_convergence()
        self.write_policy_manifest(outside_release=False)
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--fix-round", "0", "--json"
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["verified_actionable_findings"], 1)
        self.assertEqual(payload["release_blocking_findings"], 1)

    def test_incomplete_zero_finding_final_review_does_not_pass(self):
        self.prepare_convergence()
        self.complete_convergence_manifest()
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["status"], "converged")
        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual((code, err), (0, ""), out)

        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        result = json.loads(out)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any(item.startswith("report-") for item in result["issues"]))
        state = self.state()
        self.assertEqual(state["manual_final_review"]["status"], "incomplete")
        self.assertEqual(state["phase"], "manual_final_review_incomplete")
        self.assertEqual(state["dispatch_freeze"]["status"], "active")

    def test_complete_zero_finding_final_review_passes(self):
        self.prepare_convergence()
        self.complete_convergence_manifest()
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.run_cli("final-review", "prepare", "--json")
        loop = self.state_path.parent
        manifest_path = loop / "reviews" / "final" / "MANIFEST.json"
        plan = CertificationPlan.from_record(
            json.loads((loop / "reviews" / "final" / "PLAN.json").read_text(encoding="utf-8"))
        )
        current_record = json.loads(manifest_path.read_text(encoding="utf-8"))
        group = hloop_review.ReviewGroupPlan.from_record(current_record)
        review = hloop_review.ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=hloop_review.plan_verification(group, ()),
            verifications=(),
        )
        manifest = FinalReviewManifest.from_review_manifest(plan, review)
        manifest_path.write_text(json.dumps(manifest.to_record(), indent=2) + "\n", encoding="utf-8")
        self.write_complete_final_report(plan, manifest)
        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["status"], "passed")
        self.assertEqual(self.state()["manual_final_review"]["status"], "passed")

    def test_convergence_record_rejects_stale_target_sha(self):
        self.prepare_convergence()
        (self.repo / "README.md").write_text("advanced\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "advance target"], cwd=self.repo, check=True)
        before = self.state()
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual(code, 2)
        self.assertIn("stale", err)
        self.assertEqual(before, self.state())

    def test_convergence_record_exhausts_at_two_fix_rounds(self):
        self.prepare_convergence()
        loop = self.state_path.parent
        plan = json.loads((loop / "reviews" / "convergence" / "PLAN.json").read_text(encoding="utf-8"))
        group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
        candidate = FindingCandidate(
            finding_id="F001",
            provider="codex",
            head_sha=plan["target_sha"],
            discovering_agent="codex-reviewer",
            severity="P1",
            confidence=0.95,
            title="A confirmed regression",
            file_path="src/example.py",
            line=1,
            symbol="run",
            trigger="the regression trigger",
            product_impact="the release path fails",
            origin="introduced",
            proposed_fix="repair the release path",
        )
        finding = hloop_review.normalize_findings((candidate,))[0]
        verification_plan = hloop_review.plan_verification(group, (finding,))
        verifications = tuple(
            hloop_review.VerificationRecord.from_assignment(
                assignment,
                fact_status="confirmed",
                ignore_status="must_not_ignore",
                decision_status="none",
                progress_without_decision="yes",
                severity="P1",
                recommended_action="fix_task",
            )
            for assignment in verification_plan.assignments
        )
        manifest = hloop_review.ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(
                lane.result(
                    finding_count=sum(
                        1
                        for item in (finding.candidates if finding else ())
                        if item.discovering_agent == lane.agent_label
                    )
                )
                for lane in group.expected_lanes
            ),
            findings=(finding,),
            verification_plan=verification_plan,
            verifications=verifications,
        )
        (loop / "reviews" / "convergence" / "MANIFEST.json").write_text(
            json.dumps(manifest.to_record(), indent=2) + "\n", encoding="utf-8"
        )
        code, out, err = self.run_cli(
            "review",
            "convergence",
            "record",
            "--fix-round",
            "2",
            "--json",
        )
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["status"], "exhausted")
        state = self.state()
        self.assertEqual(state["phase"], "review_convergence_exhausted")
        self.assertEqual(state["dispatch_freeze"]["status"], "active")

    def test_reopen_rejects_exhausted_round_without_authorization_atomically(self):
        state = self.state()
        state["phase"] = "review_convergence_exhausted"
        state["review_convergence"].update(
            {"status": "exhausted", "fix_round": 2, "verified_actionable_findings": 1}
        )
        state["dispatch_freeze"].update({"status": "active", "reason": "exhausted"})
        self.save_state(state)
        before = self.state()
        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "remediate",
            "--user-input-id",
            "U0001",
            "--json",
        )
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertIn("authorized-extra-rounds-required", json.loads(out)["issues"])
        self.assertEqual(before, self.state())

    def _prepare_failed_final_review(self) -> None:
        state = self.state()
        state["phase"] = "manual_final_review_failed"
        state["review_convergence"].update(
            {
                "status": "converged",
                "fix_round": 0,
                "verified_actionable_findings": 1,
                "artifact_refs": ["reviews/convergence/MANIFEST.json"],
            }
        )
        state["manual_final_review"].update(
            {
                "status": "failed",
                "verified_actionable_findings": 1,
                "manifest_complete": True,
            }
        )
        state["dispatch_freeze"].update(
            {"status": "active", "reason": "manual-final-review-failed"}
        )
        self.save_state(state)

    def test_scope_changing_reopen_reads_source_and_records_immutable_amendment(self):
        self._prepare_failed_final_review()
        loop = self.state_path.parent
        (loop / "PLAN.md").write_text(
            (loop / "PLAN.md").read_text(encoding="utf-8")
            + "\nScope correction for the approved reopen.\n",
            encoding="utf-8",
        )

        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "scope-amend",
            "--user-input-id",
            "U0001",
            "--scope-reason",
            "approved scope correction",
            "--scope-basis-ref",
            "REQ-005",
            "--json",
        )
        self.assertEqual((code, err), (0, ""), out)
        result = json.loads(out)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["phase"], "review_readiness")

        artifact_path = loop / "release-scope" / "amendments" / "A001.json"
        self.assertEqual(Path(result["artifact"]), artifact_path)
        amendment = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(amendment["amendment_id"], "A001")
        self.assertEqual(amendment["kind"], "scope-change")
        self.assertEqual(amendment["previous_scope_revision"], 1)
        self.assertEqual(amendment["new_scope_revision"], 2)
        self.assertEqual(amendment["previous_source_snapshot_revision"], 1)
        self.assertEqual(amendment["new_source_snapshot_revision"], 2)
        self.assertEqual(amendment["reason"], "approved scope correction")
        self.assertEqual(amendment["basis_refs"], ["REQ-005"])
        self.assertEqual(amendment["user_input_id"], "U0001")
        state = self.state()
        self.assertEqual(state["release_scope"]["amendment_refs"], ["A001"])
        self.assertEqual(state["release_scope"]["scope_revision"], 2)
        self.assertEqual(state["release_scope"]["source_snapshot_revision"], 2)
        self.assertEqual(state["release_scope"]["last_user_input_id"], "U0001")
        self.assertEqual(state["manual_final_review"]["status"], "pending")
        self.assertEqual(state["dispatch_freeze"]["status"], "inactive")

    def test_scope_changing_reopen_rolls_back_artifact_when_state_save_fails(self):
        self._prepare_failed_final_review()
        loop = self.state_path.parent
        (loop / "PLAN.md").write_text(
            (loop / "PLAN.md").read_text(encoding="utf-8")
            + "\nScope correction for rollback.\n",
            encoding="utf-8",
        )
        before_state = self.state_path.read_bytes()
        parser = hloop.build_parser()
        args = parser.parse_args(
            [
                "--repo",
                str(self.repo),
                "--namespace",
                self.namespace,
                "review",
                "reopen",
                "--action",
                "scope-amend",
                "--user-input-id",
                "U0001",
                "--scope-reason",
                "simulated persistence failure",
                "--scope-basis-ref",
                "REQ-005",
            ]
        )

        def fail_save(repo: Path, state: dict) -> None:
            raise OSError("simulated state write failure")

        with mock.patch.object(hloop, "save_state", side_effect=fail_save):
            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_review_reopen(args)

        self.assertEqual(self.state_path.read_bytes(), before_state)
        self.assertEqual(
            list((loop / "release-scope" / "amendments").glob("A*.json")), []
        )

    def test_legacy_migration_keeps_merge_count_cadence(self):
        state = {
            "review_policy": {"cadence": "merge-count"},
            "manual_final_review": {"status": "not-required-for-legacy-run"},
            "review_after_merges": 2,
            "unreviewed_merge_count": 1,
            "tasks": {"T001": {"status": "merged"}},
            "reviews": {},
            "final_gate": None,
        }
        self.assertFalse(hloop.should_open_review_gate(state))
        state["unreviewed_merge_count"] = 2
        self.assertTrue(hloop.should_open_review_gate(state))


if __name__ == "__main__":
    unittest.main()
