from __future__ import annotations

import argparse
import contextlib
from dataclasses import replace
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
from hloop_lib.certification import (
    CertificationPlan,
    FinalReviewManifest,
    FinalReviewProcessIdentity,
)
from hloop_lib.config import project_agent_identity
from hloop_lib.review import FindingCandidate


loader = importlib.machinery.SourceFileLoader("hloop_convergence_v052_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


FIXTURE_OBSERVED_FINAL_IDENTITIES = {
    "manual-final-coordinator": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "max",
    },
    "review-process": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
    },
}
FIXTURE_ATTESTED_FINAL_IDENTITIES = {
    "manual-final-coordinator": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "max",
    },
    "review-process": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
    },
}


def with_fixture_process_identities(
    plan: CertificationPlan, manifest: FinalReviewManifest
) -> FinalReviewManifest:
    identities = []
    for process in plan.process_plan:
        fixture_key = (
            "manual-final-coordinator"
            if process.process_id == "manual-final-coordinator"
            else "review-process"
        )
        identities.append(
            FinalReviewProcessIdentity(
                process_id=process.process_id,
                agent_identity=project_agent_identity(
                    {
                        "provider": process.provider,
                        "model": process.model,
                        "effort": process.effort,
                    },
                    observed=dict(FIXTURE_OBSERVED_FINAL_IDENTITIES[fixture_key]),
                    attested=dict(FIXTURE_ATTESTED_FINAL_IDENTITIES[fixture_key]),
                ).as_dict(),
            )
        )
    return replace(manifest, process_identities=tuple(identities))


class HLoopConvergenceV052Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.protocol_adapter = hloop_review.ExternalReviewProtocolAdapter(
            protocol="codex-review-multi-v2",
            source="https://example.invalid/codex-review-multi-v2.git@" + "a" * 40,
            version="2.1.0",
            content_digest="sha256:" + "b" * 64,
            capabilities=("externally-planned-v1",),
        )
        self.protocol_capability_path = self.root / "review-capability.json"
        self.protocol_capability_path.write_text(
            json.dumps(self.protocol_adapter.to_record()), encoding="utf-8"
        )
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
        code, out, err = self.run_cli(
            "input",
            "record",
            "--source",
            "manager-chat",
            "--text",
            "convergence fixture authorization",
        )
        self.assertEqual((code, err), (0, ""), out)
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
        args = tuple(args)
        if args[:2] == ("final-review", "prepare") and "--protocol-capability" not in args:
            args = (
                *args,
                "--protocol-capability",
                str(self.protocol_capability_path),
            )
        if args[:2] == ("task", "new") and "--preserved-invariant" not in args:
            args = (
                *args,
                "--preserved-invariant",
                "preserve fixture behavior",
                "--regression-check",
                "run fixture regression",
                "--risk-class",
                "normal",
                "--required-gate",
                "patch_review",
                "--required-gate",
                "full_suite",
            )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            mock.patch.object(
                hloop.hloop_release_dependency,
                "load_release_dependencies",
                return_value=self.protocol_adapter,
            ),
        ):
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

    def test_review_lane_count_resolves_swarm_topology_without_changing_holistic_modes(self):
        state = {
            "resolved_config": {
                "reviewer": {
                    "mode": "swarm",
                    "provider": "codex",
                    "lane_count": "auto",
                },
                "review": {"lane_count": 4},
            },
            "review_policy": {"cadence": "batch", "lane_count": 4},
        }
        for lane_count in (4, 6, 8):
            state["resolved_config"]["reviewer"]["lane_count"] = lane_count
            topology = hloop.resolved_reviewer_topology(state)
            self.assertEqual(topology["probe_count"], lane_count)
            plan = hloop.build_reviewer_group_plan(
                "swarm",
                "head-sha",
                {"provider": "codex", "model": "auto"},
                topology=topology,
            )
            self.assertEqual(len(plan.expected_lanes), lane_count)

        explicit = hloop.resolved_reviewer_topology(
            state, probe_count=4
        )
        self.assertEqual(explicit["probe_count"], 4)
        holistic = hloop.resolved_reviewer_topology(state, mode="single")
        self.assertIsNone(holistic["probe_count"])
        self.assertIsNone(holistic["probes_per_provider"])

        legacy = {
            "resolved_config": {
                "reviewer": {"mode": "swarm", "probe_count": 4}
            },
            "review_policy": {"cadence": "merge-count"},
        }
        legacy_topology = hloop.resolved_reviewer_topology(legacy)
        self.assertEqual(legacy_topology["probe_count"], 4)

        legacy_policy = {
            "resolved_config": {
                "reviewer": {"mode": "swarm"},
                "review": {"lane_count": 6},
            },
            "review_policy": {"cadence": "batch", "lane_count": "auto"},
        }
        self.assertEqual(
            hloop.resolved_reviewer_topology(legacy_policy)["probe_count"], 6
        )

        default_policy = {
            "resolved_config": {"reviewer": {"mode": "dual-swarm"}},
            "review_policy": {"cadence": "batch", "lane_count": 8},
        }
        self.assertEqual(
            hloop.resolved_reviewer_topology(default_policy)["probe_count"], 8
        )

    def test_canonical_config_lane_count_reaches_reviewer_startup_plan(self):
        lane_count = 7
        for index, mode in enumerate(("swarm", "dual-swarm"), start=1):
            with self.subTest(mode=mode):
                state = self.state()
                resolved = hloop.hloop_config.resolve_config(
                    hloop.hloop_config.V053_BUILT_IN_CONFIG_DEFAULTS,
                    task_override={
                        "reviewer": {"mode": mode, "lane_count": lane_count}
                    },
                    target_dir=self.repo,
                )
                state["resolved_config"] = resolved.as_dict()
                self.save_state(state)

                plans = []
                original_builder = hloop.build_reviewer_group_plan

                def capture_plan(*args, **kwargs):
                    plan = original_builder(*args, **kwargs)
                    plans.append(plan)
                    return plan

                with mock.patch.object(
                    hloop, "build_reviewer_group_plan", side_effect=capture_plan
                ):
                    code, out, err = self.run_cli(
                        "reviewer",
                        "start",
                        "--review-id",
                        f"R9{index:02d}",
                        "--dry-run",
                    )

                self.assertEqual((code, err), (0, ""), out)
                self.assertEqual(len(plans), 1)
                self.assertEqual(
                    [len(provider.lanes) for provider in plans[0].provider_plans],
                    [lane_count] * len(plans[0].providers),
                )

    def test_manual_final_process_plan_consumes_complete_component_configs(self):
        state = self.state()
        resolution = hloop.hloop_config.resolve_config(
            hloop.BUILT_IN_CONFIG_DEFAULTS,
            target_dir=self.repo,
            loop_snapshot=state["resolved_config"],
            start_override={
                "final_coordinator": {
                    "provider": "claude",
                    "model": "final-component-model",
                    "effort": "max",
                },
                "reviewer": {
                    "coordinator": {
                        "provider": "codex",
                        "model": "coordinator-component-model",
                        "effort": "high",
                    },
                    "lane": {
                        "provider": "codex",
                        "model": "lane-component-model",
                        "effort": "xhigh",
                    },
                    "verifier": {
                        "provider": "claude",
                        "model": "verifier-component-model",
                        "effort": "max",
                    },
                },
            },
        )
        state["resolved_config"] = resolution.as_dict()
        state["config_resolution_provenance"] = resolution.explain_provenance()
        target = state["integration_head_sha"]

        plan, group = hloop._final_certification_plan(
            self.repo,
            state,
            target,
            certification_id="C-component-config",
            base_sha=target,
            mode="swarm",
            probe_count=4,
        )

        self.assertEqual(group.provider_plans[0].model, "lane-component-model")
        by_kind = {}
        for process in plan.process_plan:
            by_kind.setdefault(process.process_kind, []).append(process)
            self.assertEqual(
                set(process.config_sources), {"provider", "model", "effort"}
            )
            self.assertEqual(
                set(process.config_provenance), {"provider", "model", "effort"}
            )
        final = next(
            item
            for item in by_kind["coordinator"]
            if item.process_id == "manual-final-coordinator"
        )
        provider_coordinator = next(
            item
            for item in by_kind["coordinator"]
            if item.process_id != "manual-final-coordinator"
        )
        self.assertEqual(
            (final.provider, final.model, final.effort),
            ("claude", "final-component-model", "max"),
        )
        self.assertEqual(
            (
                provider_coordinator.provider,
                provider_coordinator.model,
                provider_coordinator.effort,
            ),
            ("codex", "lane-component-model", "high"),
        )
        self.assertEqual(
            {
                (item.provider, item.model, item.effort)
                for item in by_kind["discovery"]
            },
            {("codex", "lane-component-model", "xhigh")},
        )
        self.assertEqual(
            {
                (item.provider, item.model, item.effort)
                for item in by_kind["verifier"]
            },
            {("codex", "lane-component-model", "max")},
        )
        self.assertEqual(
            provider_coordinator.config_provenance["model"][-1]["source"],
            "review-topology-override",
        )
        verifier = by_kind["verifier"][0]
        self.assertEqual(
            verifier.config_provenance["provider"][-1]["source"],
            "review-topology-override",
        )
        self.assertEqual(
            verifier.config_provenance["model"][-1]["source"],
            "review-topology-override",
        )

    def test_dual_swarm_process_identities_follow_each_provider_topology(self):
        state = self.state()
        target = state["integration_head_sha"]
        plan, group = hloop._final_certification_plan(
            self.repo,
            state,
            target,
            certification_id="C-dual-topology",
            base_sha=target,
            mode="dual-swarm",
            providers=("codex", "claude"),
            probe_count=4,
        )
        processes = {item.process_id: item for item in plan.process_plan}
        for provider_plan in group.provider_plans:
            coordinator = processes[
                f"provider-{provider_plan.provider}-coordinator"
            ]
            self.assertEqual(
                (coordinator.provider, coordinator.model),
                (provider_plan.provider, provider_plan.model),
            )
            for lane in provider_plan.lanes:
                process = processes[f"lane-{lane.provider}-{lane.lane_id}"]
                self.assertEqual(
                    (process.provider, process.model),
                    (provider_plan.provider, provider_plan.model),
                )
            for index, _label in enumerate(
                provider_plan.verifier_agents, start=1
            ):
                verifier = processes[
                    f"verifier-{provider_plan.provider}-{index}"
                ]
                self.assertEqual(
                    (verifier.provider, verifier.model),
                    (provider_plan.provider, provider_plan.model),
                )
        claude_coordinator = processes["provider-claude-coordinator"]
        self.assertEqual(
            claude_coordinator.config_sources["provider"],
            "review-topology-override",
        )

        tampered_processes = tuple(
            replace(item, provider="codex", agent_identity={})
            if item.process_id == "provider-claude-coordinator"
            else item
            for item in plan.process_plan
        )
        tampered = replace(plan, process_plan=tampered_processes)
        review_manifest = hloop_review.ReviewManifest(
            review_id="R901",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=hloop_review.plan_verification(group, ()),
            verifications=(),
        )
        evidence = FinalReviewManifest.from_review_manifest(
            tampered, review_manifest
        )
        result = hloop.hloop_certification.validate_final_review(
            tampered,
            evidence,
            current_target_sha=target,
            allow_legacy=True,
        )
        self.assertIn(
            "identity-mismatch:process-topology-identity:provider-claude-coordinator",
            result.issues,
        )

    def test_reused_reviewer_epoch_rejects_current_protocol_drift(self):
        fixtures = __import__(
            "skills.herdr-dev-loop.tests.test_review_epoch_v053",
            fromlist=["epoch_plan"],
        )
        reviewer = fixtures.reviewer_execution(
            protocol=hloop.hloop_certification.MANUAL_FINAL_PROTOCOL
        )
        epoch_plan = fixtures.epoch_plan(executions=(reviewer,))
        collection = fixtures.ReviewEpochCollection.create(epoch_plan)
        state = self.state()
        state["resolved_config"]["reviewer"]["protocol"] = "native"
        state["review_policy"]["manual_final_execution"] = (
            "reuse_epoch_reviewer"
        )
        state["resolved_config"]["review"]["manual_final_execution"] = (
            "reuse_epoch_reviewer"
        )
        store = {
            "active_epoch_id": epoch_plan.epoch_id,
            "records": {},
            "protocol_capabilities": {},
        }
        with (
            mock.patch.object(hloop, "review_epoch_store", return_value=store),
            mock.patch.object(
                hloop,
                "require_review_epoch_collection",
                return_value=collection,
            ),
            self.assertRaisesRegex(hloop.HLoopError, "protocol identity has drifted"),
        ):
            hloop._manual_final_execution_provenance(
                self.repo,
                state,
                epoch_plan.target_sha,
                argparse.Namespace(protocol_capability=None),
            )

    def _make_ready_state(self) -> None:
        state = self.state()
        target = subprocess.check_output(
            ["git", "rev-parse", "main"], cwd=self.repo, text=True
        ).strip()
        state["tasks"] = {"T001": {"status": "merged"}}
        state["integration_head_sha"] = target
        state["completion_target_sha"] = target
        state["batches"] = {}
        state["current_batch_id"] = ""
        self.save_state(state)
        code, out, err = self.run_cli("validate", "--level", "L3", "--no-cleanup")
        self.assertEqual((code, err), (0, ""), out)

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

    def write_policy_manifest(
        self,
        *,
        outside_release: bool,
        severity: str = "P2",
        product_impact: str = "the policy impact",
        disposition: str | None = None,
        release_effect: str | None = None,
        accepted_risk_decision_id: str = "",
    ) -> None:
        loop = self.state_path.parent
        plan = json.loads(
            (loop / "reviews" / "convergence" / "PLAN.json").read_text(encoding="utf-8")
        )
        group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
        candidate = FindingCandidate(
            finding_id="FND-001",
            provider="codex",
            head_sha=plan["target_sha"],
            discovering_agent=group.expected_lanes[0].agent_label,
            severity=severity,
            confidence=0.95,
            title="axis-derived finding",
            file_path="src/example.py",
            line=1,
            symbol="run",
            trigger="the policy trigger",
            product_impact=product_impact,
            origin="unrelated-pre-existing" if outside_release else "introduced",
            proposed_fix="repair the policy path",
            fact_status="confirmed",
            contract_relation="outside_release" if outside_release else "in_scope",
            decision_requirement="none",
            disposition=disposition or ("defer_follow_up" if outside_release else "fix_now"),
            release_effect=release_effect or ("non_blocking" if outside_release else "blocking"),
            accepted_risk_decision_id=accepted_risk_decision_id,
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
                severity=severity,
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

    def write_final_policy_manifest(
        self,
        *,
        disposition: str = "fix_now",
        release_effect: str = "blocking",
        severity: str = "P2",
        decision_requirement: str = "none",
        origin: str = "introduced",
        contract_relation: str = "in_scope",
        legacy: bool = False,
        accepted_risk_decision_id: str = "",
    ) -> None:
        self.prepare_convergence()
        self.complete_convergence_manifest()
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual((code, err), (0, ""), out)

        loop = self.state_path.parent
        plan = CertificationPlan.from_record(
            json.loads((loop / "reviews" / "final" / "PLAN.json").read_text(encoding="utf-8"))
        )
        group = hloop_review.ReviewGroupPlan.from_record(
            json.loads((loop / "reviews" / "final" / "MANIFEST.json").read_text(encoding="utf-8"))
        )
        finding_candidate = FindingCandidate(
            finding_id="FND-001",
            provider="codex",
            head_sha=plan.target_sha,
            discovering_agent=group.expected_lanes[0].agent_label,
            severity=severity,
            confidence=0.95,
            title="manual final policy finding",
            file_path="src/final.py",
            line=1,
            symbol="manual_final",
            trigger="the final policy path is exercised",
            product_impact="the final policy gate must classify this finding",
            origin=origin,
            proposed_fix="repair the final policy path",
            fact_status="confirmed",
            contract_relation=contract_relation,
            decision_requirement=decision_requirement,
            disposition=disposition,
            release_effect=release_effect,
            accepted_risk_decision_id=accepted_risk_decision_id,
        )
        finding = hloop_review.normalize_findings((finding_candidate,))[0]
        verification_plan = hloop_review.plan_verification(group, (finding,))
        verifications = tuple(
            hloop_review.VerificationRecord.from_assignment(
                assignment,
                fact_status="confirmed",
                ignore_status="may_defer" if legacy else "must_not_ignore",
                decision_status="none",
                progress_without_decision="yes",
                severity=severity,
                recommended_action="discard" if legacy else "fix_task",
            )
            for assignment in verification_plan.assignments
        )
        review_manifest = hloop_review.ReviewManifest(
            review_id=(plan.execution.execution_id if plan.execution else "R001"),
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
        evidence = with_fixture_process_identities(
            plan,
            FinalReviewManifest.from_review_manifest(
                plan,
                review_manifest,
                verified_actionable_findings=len(
                    review_manifest.verified_actionable_fingerprints
                ),
            ),
        )
        manifest_path = loop / "reviews" / "final" / "MANIFEST.json"
        if legacy:
            record = evidence.to_record()
            for finding_record in record["findings"]:
                for field_name in (
                    "fact_status",
                    "contract_relation",
                    "decision_requirement",
                    "disposition",
                    "release_effect",
                    "policy_axes_explicit",
                ):
                    finding_record.pop(field_name, None)
                for candidate_record in finding_record["candidates"]:
                    for field_name in (
                        "fact_status",
                        "contract_relation",
                        "decision_requirement",
                        "disposition",
                        "release_effect",
                        "policy_axes_explicit",
                    ):
                        candidate_record.pop(field_name, None)
            manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        else:
            manifest_path.write_text(
                json.dumps(evidence.to_record(), indent=2) + "\n", encoding="utf-8"
            )
        self.write_complete_final_report(plan, evidence)

    def add_follow_up_artifact(self, fingerprint: str) -> None:
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

    def record_accepted_risk_decision(
        self,
        fingerprint: str,
        target_sha: str,
        *,
        decision_id: str = "D001",
        status: str = "accepted",
        decision_target_sha: str | None = None,
        decision_finding_fingerprint: str | None = None,
        expires_at: str = "",
        reconsider_condition: str = "Reconsider when the release contract changes.",
    ) -> None:
        state = self.state()
        decision = {
            "id": decision_id,
            "class": "advisory",
            "status": status,
            "question": "Accept the concrete residual risk for this finding?",
            "options": [
                {
                    "id": "opt_1",
                    "label": "Accept the risk",
                    "tradeoffs": ["The risk remains documented for this target."],
                },
                {
                    "id": "opt_2",
                    "label": "Do not accept the risk",
                    "tradeoffs": ["The finding remains release-blocking."],
                },
            ],
            "recommendation": {
                "option_id": "opt_1",
                "rationale": "The approved release contract records this risk.",
            },
            "source_findings": [fingerprint],
            "accepted_risk_authorization": {
                "finding_fingerprint": decision_finding_fingerprint or fingerprint,
                "target_sha": decision_target_sha or target_sha,
                "authorized_by": "release-owner",
                "risk": "Compatibility behavior remains observable.",
                "reason": "The approved contract explicitly accepts this residual risk.",
                "expires_at": expires_at,
                "reconsider_condition": reconsider_condition,
            },
        }
        if status == "accepted":
            decision["resolution"] = {
                "outcome": "accepted",
                "rationale": "Accepted for the fixed target.",
                "resolved_by": "release-owner",
                "resolved_at": hloop.now_iso(),
                "selected_option": "opt_1",
            }
        state.setdefault("decisions", {})[decision_id] = decision
        self.save_state(state)

    def test_convergence_counts_use_axes_not_legacy_recommendation(self):
        self.prepare_convergence()
        self.write_policy_manifest(outside_release=True)
        fingerprint = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        manifest = hloop_review.ReviewManifest.from_record(
            json.loads(fingerprint.read_text(encoding="utf-8"))
        )
        self.add_follow_up_artifact(manifest.findings[0].fingerprint)
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "converged")
        self.assertEqual(payload["verified_actionable_findings"], 0)
        self.assertEqual(payload["release_blocking_findings"], 0)

    def test_convergence_accepts_only_finding_linked_decision_authorization(self):
        self.prepare_convergence()
        self.write_policy_manifest(
            outside_release=True,
            disposition="accepted_risk",
            release_effect="non_blocking",
        )
        manifest_path = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        manifest = hloop_review.ReviewManifest.from_record(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        finding = manifest.findings[0]
        target = self.state()["review_convergence"]["target_sha"]
        self.record_accepted_risk_decision(finding.fingerprint, target)

        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "converged")
        self.assertEqual(payload["verified_actionable_findings"], 0)
        self.assertEqual(payload["release_blocking_findings"], 0)
        authorization = self.state()["accepted_risk_authorizations"][finding.fingerprint]
        self.assertEqual(authorization["decision_id"], "D001")
        self.assertEqual(authorization["target_sha"], target)
        self.assertEqual(self.state()["defer_follow_up_fingerprints"], [])

    def test_convergence_rejects_unauthorized_authorization_atomically(self):
        cases = (
            {"status": "pending"},
            {"status": "accepted", "decision_target_sha": "wrong-target"},
            {"status": "rejected"},
            {
                "status": "accepted",
                "decision_finding_fingerprint": "sha256:" + "b" * 64,
            },
            {"status": "accepted", "expires_at": "2000-01-01T00:00:00Z"},
        )
        for case in cases:
            with self.subTest(case=case):
                self.prepare_convergence()
                self.write_policy_manifest(
                    outside_release=True,
                    disposition="accepted_risk",
                    release_effect="non_blocking",
                )
                manifest_path = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
                manifest = hloop_review.ReviewManifest.from_record(
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                )
                finding = manifest.findings[0]
                target = self.state()["review_convergence"]["target_sha"]
                self.record_accepted_risk_decision(
                    finding.fingerprint,
                    target,
                    **case,
                )
                before = self.state()
                code, out, err = self.run_cli(
                    "review", "convergence", "record", "--json"
                )
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("accepted-risk", err)
                self.assertEqual(before, self.state())
                manifest_path.unlink()

    def test_manual_final_and_final_report_project_authorized_risk_without_follow_up(self):
        self.write_final_policy_manifest(
            disposition="accepted_risk",
            release_effect="non_blocking",
        )
        loop = self.state_path.parent
        manifest_path = loop / "reviews" / "final" / "MANIFEST.json"
        manifest = FinalReviewManifest.from_record(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        finding = manifest.review_manifest.findings[0]
        self.record_accepted_risk_decision(finding.fingerprint, manifest.target_sha)

        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["verified_actionable_findings"], 0)
        self.assertEqual(payload["accepted_risks"][0]["decision_id"], "D001")
        state = self.state()
        self.assertEqual(
            state["manual_final_review"]["accepted_risk_authorizations"][finding.fingerprint][
                "decision_id"
            ],
            "D001",
        )
        self.assertEqual(state["defer_follow_up_fingerprints"], [])

        code, out, err = self.run_cli("report")
        self.assertEqual((code, err), (0, ""), out)
        report = (loop / "reports" / "DRAFT.md").read_text(encoding="utf-8")
        self.assertIn("D001", report)
        self.assertIn("Compatibility behavior remains observable", report)

        self.assertEqual(
            hloop._finish_accepted_risk_errors(
                self.repo, self.state(), manifest.target_sha
            ),
            [],
        )
        state = self.state()
        state.pop("accepted_risk_authorizations", None)
        state["manual_final_review"].pop("accepted_risk_authorizations", None)
        self.save_state(state)
        projection_errors = hloop._finish_accepted_risk_errors(
            self.repo, self.state(), manifest.target_sha
        )
        self.assertIn(
            "accepted-risk authorization projection does not match final-review findings",
            projection_errors,
        )
        state = self.state()
        state["decisions"]["D001"]["accepted_risk_authorization"][
            "target_sha"
        ] = "stale-target"
        self.save_state(state)
        errors = hloop._finish_accepted_risk_errors(self.repo, self.state(), manifest.target_sha)
        self.assertTrue(any("revalidation" in error for error in errors))

    def test_convergence_and_final_prepare_recheck_deferred_follow_up_artifacts(self):
        self.prepare_convergence()
        self.write_policy_manifest(outside_release=True)
        manifest_path = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        manifest = hloop_review.ReviewManifest.from_record(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        fingerprint = manifest.findings[0].fingerprint
        target = self.state()["review_convergence"]["target_sha"]

        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual(code, 2)
        self.assertIn("first-class follow-up artifacts", err)
        state = self.state()
        self.assertEqual(state["review_convergence"]["status"], "prepared")
        self.assertEqual(state["review_convergence"]["target_sha"], target)

        self.add_follow_up_artifact(fingerprint)
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        state = self.state()
        self.assertEqual(state["review_convergence"]["status"], "converged")
        self.assertEqual(state["review_convergence"]["target_sha"], target)
        self.assertIn(fingerprint, state["finding_inventory"]["fingerprints"])
        finding_record = state["finding_inventory"]["records"][fingerprint]
        self.assertEqual(finding_record["target_sha"], target)
        self.assertEqual(
            finding_record["scope_revision"],
            state["release_scope"]["scope_revision"],
        )
        self.assertEqual(
            set(finding_record["policy_axes"]),
            {
                "fact_status",
                "severity",
                "origin",
                "contract_relation",
                "decision_requirement",
                "disposition",
                "release_effect",
            },
        )
        self.assertIn("requirement_refs", finding_record)
        self.assertIn("scope_refs", finding_record)
        self.assertEqual(state["defer_follow_up_fingerprints"], [fingerprint])

        follow_up_path = self.state_path.parent / "follow-ups" / "F001.md"
        follow_up_path.unlink()
        state = self.state()
        state["follow_ups"]["artifact_refs"] = []
        self.save_state(state)
        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual(code, 2)
        self.assertIn("first-class follow-up artifacts", err)
        self.assertEqual(self.state()["review_convergence"]["target_sha"], target)

        self.add_follow_up_artifact(fingerprint)
        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.state()["manual_final_review"]["target_sha"], target)

    def test_independent_final_prepare_rejects_recorded_convergence_source_drift(self):
        self.prepare_convergence()
        self.complete_convergence_manifest()
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        state = self.state()
        self.assertEqual(state["review_convergence"]["status"], "converged")
        recorded_digest = state["review_convergence"]["recorded_manifest_digest"]

        manifest_path = (
            self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        )
        replacement = json.loads(manifest_path.read_text(encoding="utf-8"))
        replacement["review_id"] = "R009"
        manifest_path.write_text(
            json.dumps(replacement, indent=2) + "\n", encoding="utf-8"
        )
        self.assertNotEqual(
            hloop.hloop_certification.canonical_digest(replacement),
            recorded_digest,
        )

        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("recorded convergence manifest digest", err)
        self.assertNotEqual(self.state()["manual_final_review"]["status"], "prepared")

    def test_reused_reviewer_manifest_cannot_replace_source_findings(self):
        self.prepare_convergence()
        self.write_policy_manifest(outside_release=False)
        source_path = (
            self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        )
        source = hloop_review.ReviewManifest.from_record(
            json.loads(source_path.read_text(encoding="utf-8"))
        )
        self.assertTrue(source.findings)
        replacement = hloop_review.ReviewManifest(
            review_id=source.review_id,
            plan=source.plan,
            lane_results=tuple(lane.result() for lane in source.plan.expected_lanes),
            findings=(),
            verification_plan=hloop_review.plan_verification(source.plan, ()),
            verifications=(),
        )
        self.assertTrue(replacement.completeness.complete)
        execution = hloop.hloop_certification.ManualFinalExecutionProvenance(
            execution_policy="reuse_epoch_reviewer",
            execution_id=source.review_id,
            source_kind="review-epoch-reviewer",
            source_execution_id=source.review_id,
            source_artifact_ref="reviews/convergence/MANIFEST.json",
            source_artifact_digest=hloop._sha256_labelled(source_path.read_bytes()),
            target_sha=source.plan.head_sha,
            protocol_adapter=self.protocol_adapter,
        )
        with self.assertRaisesRegex(
            hloop.HLoopError, "must exactly match the validated source artifact"
        ):
            hloop._validate_manual_final_source_artifact(
                self.repo,
                execution,
                submitted_manifest=replacement,
            )

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
        inventory = ["README.md", "docs.md"]
        state["changed_file_validation"] = {
            "status": "passed",
            "target_sha": target,
            "paths": inventory,
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
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=inventory):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (2, ""), out)
        blocked = json.loads(out)
        self.assertIn(
            "changed file lacks validation evidence: docs.md", blocked["errors"]
        )

        state = self.state()
        state["changed_file_validation"]["mapping"]["docs.md"] = [
            "validation.log"
        ]
        state["changed_file_validation"]["mapping"]["unexpected.md"] = [
            "validation.log"
        ]
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=inventory):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (2, ""), out)
        blocked = json.loads(out)
        self.assertIn(
            "changed-file validation evidence has unexpected paths: unexpected.md",
            blocked["errors"],
        )

        state = self.state()
        state["changed_file_validation"]["mapping"].pop("unexpected.md")
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=inventory):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (0, ""), out)
        ready = json.loads(out)
        self.assertEqual(ready["checks"]["changed_file_validation"]["status"], "passed")
        self.assertEqual(ready["checks"]["special_verification"]["status"], "passed")

    def test_special_verification_record_cli_requires_current_evidence(self):
        target = self.state()["integration_head_sha"]
        inventory = [
            "migration/step.py",
            "schemas/release.schema.json",
            "docs/release.md",
            "security/auth.py",
        ]
        domains = {"migration", "schema", "public-docs", "security-boundary"}
        for domain in sorted(domains):
            code, out, err = self.run_cli(
                "verification",
                "record",
                "--domain",
                domain,
                "--target-sha",
                target,
                "--status",
                "passed",
                "--evidence-ref",
                f"{domain}.log",
                "--json",
            )
            self.assertEqual((code, err), (0, ""), out)
            self.assertEqual(json.loads(out)["target_sha"], target)

        state = self.state()
        self.assertEqual(set(state["special_verification_evidence"]), domains)
        state["changed_file_validation"] = {
            "status": "passed",
            "target_sha": target,
            "paths": inventory,
            "mapping": {path: ["validation.log"] for path in inventory},
        }
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=inventory):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (0, ""), out)

        before = self.state()["special_verification_evidence"]
        code, out, err = self.run_cli(
            "verification",
            "record",
            "--domain",
            "migration",
            "--target-sha",
            target,
            "--status",
            "passed",
        )
        self.assertEqual(code, 2)
        self.assertIn("requires at least one --evidence-ref", err)
        self.assertEqual(self.state()["special_verification_evidence"], before)

        code, out, err = self.run_cli(
            "verification",
            "record",
            "--domain",
            "migration",
            "--target-sha",
            target,
            "--status",
            "failed",
            "--evidence-ref",
            "failed.log",
        )
        self.assertEqual((code, err), (0, ""), out)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=inventory):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual(code, 2)
        blocked = json.loads(out)
        self.assertIn(
            "special verification evidence is not passed: migration",
            blocked["errors"],
        )

        (self.repo / "README.md").write_text("fixture changed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "advance integration head"],
            cwd=self.repo,
            check=True,
        )
        code, out, err = self.run_cli(
            "verification",
            "record",
            "--domain",
            "schema",
            "--target-sha",
            target,
            "--status",
            "passed",
            "--evidence-ref",
            "stale.log",
        )
        self.assertEqual(code, 2)
        self.assertIn("target SHA is stale", err)

    def test_readiness_rejects_legacy_special_verification_without_target_identity(self):
        target = self.state()["integration_head_sha"]
        state = self.state()
        state["changed_file_validation"] = {
            "status": "passed",
            "target_sha": target,
            "paths": ["migration/step.py"],
            "mapping": {"migration/step.py": ["validation.log"]},
        }
        state["special_verification_evidence"] = {
            "migration": ["legacy-migration.log"],
        }
        self.save_state(state)
        with mock.patch.object(
            hloop, "_changed_file_inventory", return_value=["migration/step.py"]
        ):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertIn(
            "special verification evidence identity is missing: migration",
            payload["errors"],
        )

    def test_validation_evidence_reuse_requires_exact_command_order(self):
        identity = {
            "target_sha": "target",
            "commands": ["lint", "test"],
            "dependency_identity": "dependencies",
        }
        record = {"validation_identity": identity}
        self.assertTrue(hloop._validation_identity_matches(record, identity))
        reordered = {
            **identity,
            "commands": ["test", "lint"],
        }
        self.assertFalse(hloop._validation_identity_matches(record, reordered))

    def test_manual_final_protocol_does_not_fallback_to_implemented_protocol(self):
        target = self.state()["integration_head_sha"]
        state = self.state()
        state["review_policy"]["manual_final_protocol"] = "native"
        state["review_convergence"] = {
            "status": "converged",
            "target_sha": target,
            "base_sha": target,
            "fix_round": 0,
            "artifact_refs": [],
        }
        self.save_state(state)
        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("unsupported manual-final protocol", err)

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
            review_id=(plan.execution.execution_id if plan.execution else "R001"),
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=hloop_review.plan_verification(group, ()),
            verifications=(),
        )
        manifest = with_fixture_process_identities(
            plan, FinalReviewManifest.from_review_manifest(plan, review)
        )
        manifest_path.write_text(json.dumps(manifest.to_record(), indent=2) + "\n", encoding="utf-8")
        self.write_complete_final_report(plan, manifest)
        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["status"], "passed")
        self.assertEqual(self.state()["manual_final_review"]["status"], "passed")

    def test_legacy_manual_final_rejects_asymmetric_execution_provenance_atomically(self):
        self.prepare_convergence()
        self.complete_convergence_manifest()
        code, out, err = self.run_cli("review", "convergence", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        code, out, err = self.run_cli("final-review", "prepare", "--json")
        self.assertEqual((code, err), (0, ""), out)

        loop = self.state_path.parent
        plan = CertificationPlan.from_record(
            json.loads(
                (loop / "reviews" / "final" / "PLAN.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        manifest_path = loop / "reviews" / "final" / "MANIFEST.json"
        group = hloop_review.ReviewGroupPlan.from_record(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        review = hloop_review.ReviewManifest(
            review_id=plan.execution.execution_id,
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=hloop_review.plan_verification(group, ()),
            verifications=(),
        )
        record = with_fixture_process_identities(
            plan, FinalReviewManifest.from_review_manifest(plan, review)
        ).to_record()
        record.pop("execution")
        manifest_path.write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        state = self.state()
        state["release_scope"] = {
            "status": "legacy-unlocked",
            "source_refs": [],
            "source_digests": {},
            "scope_revision": 0,
            "source_snapshot_revision": 0,
            "amendment_refs": [],
        }
        self.save_state(state)
        before = self.state()

        code, out, err = self.run_cli("final-review", "record", "--json")

        self.assertEqual((code, out), (2, ""))
        self.assertIn("missing plan-bound execution provenance", err)
        self.assertEqual(self.state(), before)

    def _assert_manual_final_policy_rejected(self, **kwargs):
        self.write_final_policy_manifest(**kwargs)
        before = self.state()
        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("disposition policy", err)
        self.assertEqual(before, self.state())

    def test_manual_final_rejects_in_scope_p1_deferred_finding_atomically(self):
        self._assert_manual_final_policy_rejected(
            severity="P1",
            disposition="defer_follow_up",
            release_effect="non_blocking",
        )

    def test_manual_final_rejects_discarded_in_scope_finding_atomically(self):
        self._assert_manual_final_policy_rejected(
            disposition="discard",
            release_effect="non_blocking",
        )

    def test_manual_final_rejects_unauthorized_accepted_risk_atomically(self):
        self._assert_manual_final_policy_rejected(
            disposition="accepted_risk",
            release_effect="non_blocking",
        )

    def test_manual_final_rejects_blocking_decision_bypass_atomically(self):
        self._assert_manual_final_policy_rejected(
            decision_requirement="user",
            disposition="defer_follow_up",
            release_effect="non_blocking",
        )

    def test_manual_final_recomputes_release_blocking_from_explicit_axes(self):
        self.write_final_policy_manifest(severity="P1")
        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual((code, err), (2, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["verified_actionable_findings"], 1)
        self.assertEqual(payload["release_blocking_findings"], 1)
        state = self.state()
        self.assertEqual(state["manual_final_review"]["release_blocking_findings"], 1)
        self.assertEqual(state["phase"], "manual_final_review_failed")

    def test_manual_final_rejects_legacy_manifest_in_fresh_scope_atomically(self):
        self._assert_manual_final_policy_rejected(
            disposition="defer_follow_up",
            release_effect="non_blocking",
            origin="unrelated-pre-existing",
            contract_relation="outside_release",
            legacy=True,
        )

    def test_manual_final_legacy_scope_uses_explicit_compatibility_fallback(self):
        self.write_final_policy_manifest(
            disposition="defer_follow_up",
            release_effect="non_blocking",
            origin="unrelated-pre-existing",
            contract_relation="outside_release",
            legacy=True,
        )
        state = self.state()
        state["release_scope"] = {
            "status": "legacy-unlocked",
            "source_refs": [],
            "source_digests": {},
            "scope_revision": 0,
            "source_snapshot_revision": 0,
            "amendment_refs": [],
        }
        self.save_state(state)
        code, out, err = self.run_cli("final-review", "record", "--json")
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["verified_actionable_findings"], 0)
        self.assertEqual(payload["release_blocking_findings"], 0)

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
        self.write_policy_manifest(outside_release=False)
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--fix-round", "0", "--json"
        )
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["status"], "pending")
        self.assertEqual(self.state()["review_convergence"]["fix_round"], 0)
        manifest_path = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        manifest_before_retry = manifest_path.read_bytes()
        self.prepare_convergence()
        self.assertEqual(manifest_path.read_bytes(), manifest_before_retry)
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--json"
        )
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["status"], "pending")

        code, out, err = self.run_cli(
            "input",
            "record",
            "--source",
            "manager-chat",
            "--text",
            "second convergence fixture authorization",
        )
        self.assertEqual((code, err), (0, ""), out)

        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "remediate",
            "--user-input-id",
            "U0001",
            "--json",
        )
        self.assertEqual((code, err), (0, ""), out)
        self.assertTrue(json.loads(out)["accepted"])

        self.prepare_convergence()
        self.write_policy_manifest(outside_release=False)
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--fix-round", "1", "--json"
        )
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["status"], "pending")

        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "remediate",
            "--user-input-id",
            "U0002",
            "--json",
        )
        self.assertEqual((code, err), (0, ""), out)
        self.assertTrue(json.loads(out)["accepted"])

        self.prepare_convergence()
        self.write_policy_manifest(outside_release=False)
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--fix-round", "2", "--json"
        )
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["status"], "exhausted")
        state = self.state()
        self.assertEqual(state["review_convergence"]["fix_round"], 2)
        self.assertEqual(state["phase"], "review_convergence_exhausted")
        self.assertEqual(state["dispatch_freeze"]["status"], "active")

    def test_convergence_record_rejects_round_injection_without_mutation(self):
        self.prepare_convergence()
        before = self.state()
        code, out, err = self.run_cli(
            "review",
            "convergence",
            "record",
            "--fix-round",
            "1",
            "--json",
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("canonical fix_round", err)
        self.assertEqual(before, self.state())

    def test_fresh_convergence_rejects_legacy_axes_before_state_mutation(self):
        self.prepare_convergence()
        self.write_policy_manifest(outside_release=False)
        manifest_path = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
        legacy_fields = {
            "fact_status",
            "contract_relation",
            "decision_requirement",
            "disposition",
            "release_effect",
            "policy_axes_explicit",
        }
        for finding in record["findings"]:
            for field_name in legacy_fields:
                finding.pop(field_name, None)
            for candidate in finding["candidates"]:
                for field_name in legacy_fields:
                    candidate.pop(field_name, None)
        manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

        before = self.state()
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--json"
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("explicit policy axes", err)
        self.assertEqual(before, self.state())

        legacy_state = self.state()
        legacy_state["release_scope"] = {
            "status": "legacy-unlocked",
            "source_refs": [],
            "source_digests": {},
            "scope_revision": 0,
            "source_snapshot_revision": 0,
            "amendment_refs": [],
        }
        self.save_state(legacy_state)
        code, out, err = self.run_cli(
            "review", "convergence", "record", "--json"
        )
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["verified_actionable_findings"], 1)

    def test_explicit_policy_combinations_are_rejected_before_state_mutation(self):
        cases = (
            {
                "outside_release": False,
                "disposition": "discard",
                "release_effect": "non_blocking",
            },
            {
                "outside_release": True,
                "disposition": "accepted_risk",
                "release_effect": "non_blocking",
            },
            {
                "outside_release": True,
                "severity": "P1",
                "product_impact": "the path can cause data loss",
                "disposition": "defer_follow_up",
                "release_effect": "non_blocking",
            },
            {
                "outside_release": True,
                "disposition": "user_decision",
                "release_effect": "blocking",
            },
        )
        for case in cases:
            with self.subTest(case=case):
                self.prepare_convergence()
                self.write_policy_manifest(**case)
                before = self.state()
                code, out, err = self.run_cli(
                    "review", "convergence", "record", "--json"
                )
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("disposition policy", err)
                self.assertEqual(before, self.state())
                manifest_path = self.state_path.parent / "reviews" / "convergence" / "MANIFEST.json"
                manifest_path.unlink()

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

    def test_reopen_pure_transition_rejects_uncaptured_ids_atomically(self):
        state = self.state()
        state["phase"] = "review_convergence_exhausted"
        state["review_convergence"].update(
            {"status": "exhausted", "fix_round": 2, "verified_actionable_findings": 1}
        )
        state["dispatch_freeze"].update({"status": "active", "reason": "exhausted"})
        state["inputs_index"] = {}
        before = json.loads(json.dumps(state))

        validation = hloop.hloop_certification.validate_reopen_transition(
            state,
            action="remediate",
            user_input_id="U0001",
            authorized_extra_rounds=1,
            authorization_input_id="U0002",
        )
        self.assertFalse(validation.accepted)
        self.assertIn("user-input-id-not-captured", validation.issues)
        self.assertIn("authorization-input-id-not-captured", validation.issues)

        result = hloop.hloop_certification.reopen_review(
            state,
            action="remediate",
            user_input_id="U0001",
            authorized_extra_rounds=1,
            authorization_input_id="U0002",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(state, before)
        self.assertEqual(result.state, before)

    def test_reopen_cli_rejects_uncaptured_user_and_extra_round_ids_atomically(self):
        state = self.state()
        state["phase"] = "review_convergence_exhausted"
        state["review_convergence"].update(
            {"status": "exhausted", "fix_round": 2, "verified_actionable_findings": 1}
        )
        state["dispatch_freeze"].update({"status": "active", "reason": "exhausted"})
        self.save_state(state)
        before = self.state_path.read_bytes()

        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "remediate",
            "--user-input-id",
            "U0009",
            "--json",
        )
        self.assertEqual((code, err), (2, ""), out)
        self.assertIn("user-input-id-not-captured", json.loads(out)["issues"])
        self.assertEqual(before, self.state_path.read_bytes())

        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "remediate",
            "--user-input-id",
            "U0001",
            "--authorized-extra-rounds",
            "1",
            "--authorization-input-id",
            "U0009",
            "--json",
        )
        self.assertEqual((code, err), (2, ""), out)
        self.assertIn(
            "authorization-input-id-not-captured", json.loads(out)["issues"]
        )
        self.assertEqual(before, self.state_path.read_bytes())

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
        self.assertEqual(state["review_convergence"]["status"], "pending")
        self.assertEqual(state["review_convergence"]["artifact_refs"], [])
        self.assertEqual(state["review_readiness"]["status"], "pending")
        self.assertEqual(state["dispatch_freeze"]["status"], "inactive")

    def test_scope_changing_reopen_cli_rejects_uncaptured_id_before_artifact(self):
        self._prepare_failed_final_review()
        loop = self.state_path.parent
        (loop / "PLAN.md").write_text(
            (loop / "PLAN.md").read_text(encoding="utf-8")
            + "\nScope correction for an uncaptured authorization.\n",
            encoding="utf-8",
        )
        before_state = self.state_path.read_bytes()

        code, out, err = self.run_cli(
            "review",
            "reopen",
            "--action",
            "scope-amend",
            "--user-input-id",
            "U0009",
            "--scope-reason",
            "uncaptured scope correction",
            "--scope-basis-ref",
            "REQ-005",
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("captured input inventory", err)
        self.assertEqual(before_state, self.state_path.read_bytes())
        self.assertEqual(
            list((loop / "release-scope" / "amendments").glob("A*.json")), []
        )

    def test_task_creation_after_non_scope_amendment_rejects_without_mutation(self):
        loop = self.state_path.parent
        for kind, basis_args in (
            ("editorial", ()),
            ("clarification", ("--basis-ref", "REQ-001")),
        ):
            with self.subTest(kind=kind):
                plan = loop / "PLAN.md"
                plan.write_text(
                    plan.read_text(encoding="utf-8")
                    + f"\n{kind} amendment fixture.\n",
                    encoding="utf-8",
                )
                code, out, err = self.run_cli(
                    "release-scope",
                    "amend",
                    "--kind",
                    kind,
                    "--reason",
                    f"record {kind} fixture",
                    *basis_args,
                )
                self.assertEqual((code, err), (0, ""), out)
                self.assertEqual(self.state()["release_scope"]["last_user_input_id"], "")

                before_state = self.state_path.read_bytes()
                task_path = loop / "tasks" / "T050.md"
                self.assertFalse(task_path.exists())
                code, out, err = self.run_cli(
                    "task",
                    "new",
                    "unauthorized scope expansion",
                    "--id",
                    "T050",
                    "--kind",
                    "implementation",
                    "--write-allow",
                    "src/example.py",
                    "--task-origin",
                    "user-amendment",
                    "--authorization-input-id",
                    "U0001",
                    "--scope-expanding",
                )
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("latest locked input", err)
                self.assertEqual(before_state, self.state_path.read_bytes())
                self.assertFalse(task_path.exists())

    def test_scope_change_task_records_specific_amendment_and_input(self):
        loop = self.state_path.parent
        plan = loop / "PLAN.md"
        plan.write_text(
            plan.read_text(encoding="utf-8")
            + "\nAuthorized scope-change fixture.\n",
            encoding="utf-8",
        )
        code, out, err = self.run_cli(
            "release-scope",
            "amend",
            "--kind",
            "scope-change",
            "--reason",
            "record authorized scope change",
            "--user-input-id",
            "U0001",
        )
        self.assertEqual((code, err), (0, ""), out)

        code, out, err = self.run_cli(
            "task",
            "new",
            "authorized scope expansion",
            "--id",
            "T050",
            "--kind",
            "implementation",
            "--write-allow",
            "src/example.py",
            "--task-origin",
            "user-amendment",
            "--authorization-input-id",
            "U0001",
            "--scope-expanding",
        )
        self.assertEqual((code, err), (0, ""), out)
        task_path = loop / "tasks" / "T050.md"
        task = hloop.read_frontmatter(task_path)
        self.assertEqual(task["release_scope_revision"], "2")
        self.assertEqual(task["authorization_input_id"], "U0001")
        self.assertEqual(task["scope_refs"], ["A001"])

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

    def test_batch_review_opens_for_closed_batch_with_future_queued_tasks(self):
        target = "head-sha"
        state = {
            "max_reviewers": 1,
            "review_policy": {"cadence": "batch", "lane_count": "auto"},
            "needs_review": False,
            "reviews": {},
            "unreviewed_merge_count": 1,
            "integration_head_sha": target,
            "completion_target_sha": target,
            "last_validation": {
                "head_sha": target,
                "results": [{"command": "true", "result": "passed"}],
            },
            "tasks": {
                "T001": {"status": "merged"},
                "T002": {"status": "queued"},
            },
            "batches": {
                "B001": {
                    "status": "closed",
                    "closed_at": "2026-07-16T00:00:00+00:00",
                    "task_ids": ["T001"],
                },
                "B002": {
                    "status": "active",
                    "task_ids": ["T002"],
                },
            },
            "current_batch_id": "",
        }
        self.assertTrue(hloop.should_open_review_gate(state))
        state["current_batch_id"] = "B002"
        self.assertFalse(hloop.should_open_review_gate(state))
        state["current_batch_id"] = ""
        state["last_validation"]["head_sha"] = "stale"
        self.assertFalse(hloop.should_open_review_gate(state))

    def test_batch_readiness_accepts_future_queued_tasks_after_closed_batch(self):
        state = self.state()
        target = state["integration_head_sha"]
        state["tasks"] = {
            "T001": {"status": "merged"},
            "T002": {"status": "queued"},
        }
        state["batches"] = {
            "B001": {
                "status": "closed",
                "closed_at": "2026-07-16T00:00:00+00:00",
                "task_ids": ["T001"],
            }
        }
        state["current_batch_id"] = ""
        self.save_state(state)
        with mock.patch.object(hloop, "_changed_file_inventory", return_value=[]):
            code, out, err = self.run_cli("review", "readiness", "--json")
        self.assertEqual((code, err), (0, ""), out)
        ready = json.loads(out)
        self.assertFalse(ready["checks"]["all_tasks_merged"])
        self.assertTrue(ready["checks"]["review_batch_tasks_merged"])


if __name__ == "__main__":
    unittest.main()
