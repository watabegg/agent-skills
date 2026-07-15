from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError:  # pragma: no cover - minimal installations skip schema tests
    jsonschema = None
    Registry = None
    Resource = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.certification import (  # noqa: E402
    MANUAL_FINAL_PROTOCOL,
    CertificationPlan,
    FinalReviewLane,
    FinalReviewManifest,
    VerificationPolicy,
    canonical_digest,
    reopen_review,
    validate_final_review,
    validate_plan_digest,
    validate_reopen_transition,
)
from hloop_lib.review import (  # noqa: E402
    FindingCandidate,
    ReviewManifest,
    VerificationRecord,
    normalize_findings,
    plan_review_group,
    plan_verification,
)


HEAD = "target-sha"
BASE = "base-sha"
SOURCE_DIGEST = "a" * 64


def review_plan(*, mode: str = "single", max_verifications: int = 64, verifier_pool_size: int = 2):
    return plan_review_group(
        mode,
        head_sha=HEAD,
        max_verifications=max_verifications,
        verifier_pool_size=verifier_pool_size,
    )


def certification_plan(group, *, target_sha: str = HEAD) -> CertificationPlan:
    return CertificationPlan(
        certification_id="C001",
        base_sha=BASE,
        target_sha=target_sha,
        base_ref="master",
        target_ref="feat/integration",
        scope_source=("MISSION.md", "PLAN.md"),
        scope_revision=1,
        source_snapshot_revision=1,
        source_digest=SOURCE_DIGEST,
        protocol=MANUAL_FINAL_PROTOCOL,
        lane_plan=tuple(
            FinalReviewLane(
                provider=lane.provider,
                lane_id=lane.lane_id,
                purpose=lane.purpose,
                agent_label=lane.agent_label,
            )
            for lane in group.expected_lanes
        ),
        verification_policy=VerificationPolicy(
            max_parallel_verifiers=group.budget.max_parallel_verifiers,
            max_verifications=group.budget.max_verifications,
            time_limit_seconds=group.budget.time_limit_seconds,
            provider_limits=group.budget.provider_limits,
        ),
    )


def final_manifest(
    plan: CertificationPlan,
    group,
    *,
    findings=(),
    lane_results=None,
    verifications=(),
    verified_actionable_findings: int = 0,
    patch_verdict: str = "passed",
) -> FinalReviewManifest:
    findings = tuple(findings)
    counts = Counter(
        (candidate.provider, candidate.discovering_agent)
        for finding in findings
        for candidate in finding.candidates
    )
    if lane_results is None:
        lane_results = tuple(
            lane.result(finding_count=counts[(lane.provider, lane.agent_label)])
            for lane in group.expected_lanes
        )
    review = ReviewManifest(
        review_id="R001",
        plan=group,
        lane_results=tuple(lane_results),
        findings=findings,
        verification_plan=plan_verification(group, findings),
        verifications=tuple(verifications),
    )
    return FinalReviewManifest.from_review_manifest(
        plan,
        review,
        verified_actionable_findings=verified_actionable_findings,
        patch_verdict=patch_verdict,
    )


def candidate(*, severity: str = "P2") -> FindingCandidate:
    return FindingCandidate(
        finding_id="C-F001",
        provider="codex",
        head_sha=HEAD,
        discovering_agent="codex-reviewer",
        severity=severity,
        confidence=0.9,
        title="A queue item can be lost",
        file_path="src/queue.py",
        line=42,
        symbol="drain_queue",
        trigger="the worker crashes after acknowledgement",
        product_impact="the item is never processed",
        origin="introduced",
        proposed_fix="commit acknowledgement with the state transition",
    )


class PlanIdentityTests(unittest.TestCase):
    def test_digest_is_canonical_and_plan_is_immutable(self):
        group = review_plan()
        plan = certification_plan(group)
        self.assertEqual(plan.digest, plan.plan_digest)
        self.assertTrue(plan.digest.startswith("sha256:"))
        self.assertEqual(plan, CertificationPlan.from_record(plan.to_record()))
        self.assertTrue(validate_plan_digest(plan, plan.digest))
        self.assertFalse(validate_plan_digest(plan, canonical_digest({"other": True})))
        with self.assertRaises(AttributeError):
            plan.target_sha = "changed"  # type: ignore[misc]

    def test_digest_pins_every_identity_component(self):
        group = review_plan()
        plan = certification_plan(group)
        mutations = (
            replace(plan, base_sha="another-base"),
            replace(plan, target_sha="another-target"),
            replace(plan, scope_revision=2),
            replace(plan, source_snapshot_revision=2),
            replace(plan, source_digest="b" * 64),
            replace(plan, lane_plan=(replace(plan.lane_plan[0], purpose="different"),)),
            replace(
                plan,
                verification_policy=replace(plan.verification_policy, max_verifications=1),
            ),
        )
        self.assertEqual(len({candidate.digest for candidate in mutations}), len(mutations))

    def test_round_trip_manifest_has_explicit_identity(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        restored = FinalReviewManifest.from_record(manifest.to_record())
        self.assertEqual(restored, manifest)
        self.assertEqual(restored.to_record()["target_sha"], HEAD)
        self.assertEqual(restored.to_record()["prepared_plan_digest"], plan.digest)


class FinalManifestCompletenessTests(unittest.TestCase):
    def test_complete_zero_finding_manifest_passes(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        result = validate_final_review(plan, manifest, current_target_sha=HEAD)
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.completeness.issues, ())

    def test_missing_failed_and_timeout_lanes_are_incomplete(self):
        group = review_plan()
        plan = certification_plan(group)
        expected = group.expected_lanes
        cases = {
            "missing": tuple(lane.result() for lane in expected[:-1]),
            "failed": tuple(
                lane.result(status="failed") if index == 0 else lane.result()
                for index, lane in enumerate(expected)
            ),
            "timeout": tuple(
                lane.result(status="timeout") if index == 0 else lane.result()
                for index, lane in enumerate(expected)
            ),
        }
        for name, lanes in cases.items():
            with self.subTest(name=name):
                manifest = final_manifest(plan, group, lane_results=lanes)
                result = validate_final_review(plan, manifest)
                self.assertFalse(result.passed)
                self.assertEqual(result.status, "incomplete")
                self.assertTrue(result.completeness.issues)

    def test_verification_shortfall_and_incomplete_finding_block_zero_count(self):
        group = review_plan(max_verifications=1, verifier_pool_size=1)
        plan = certification_plan(group)
        normalized = normalize_findings((candidate(severity="P1"),))
        manifest = final_manifest(plan, group, findings=normalized)
        result = validate_final_review(plan, manifest)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "incomplete")
        self.assertTrue(
            any("verification-shortfall" in issue for issue in result.issues)
        )
        self.assertTrue(result.completeness.incomplete_findings)

    def test_confirmed_actionable_finding_fails_even_with_complete_lanes(self):
        group = review_plan()
        plan = certification_plan(group)
        normalized = normalize_findings((candidate(),))
        verification = plan_verification(group, normalized)
        records = tuple(
            VerificationRecord.from_assignment(
                assignment,
                fact_status="confirmed",
                ignore_status="must_not_ignore",
                decision_status="none",
                progress_without_decision="yes",
                severity="P2",
                recommended_action="fix_task",
            )
            for assignment in verification.assignments
        )
        manifest = final_manifest(
            plan,
            group,
            findings=normalized,
            verifications=records,
            verified_actionable_findings=1,
        )
        result = validate_final_review(plan, manifest)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "failed")
        self.assertIn("verified-actionable-findings:1", result.issues)

    def test_identity_and_target_drift_are_rejected(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        drifted_plan = replace(plan, target_sha="new-target")
        result = validate_final_review(drifted_plan, manifest)
        self.assertFalse(result.passed)
        self.assertIn("identity-mismatch:target-sha", result.issues)
        current_drift = validate_final_review(plan, manifest, current_target_sha="new-target")
        self.assertIn("target-sha-drift", current_drift.issues)


def reopen_state(*, phase: str, finding_count: int = 1, fix_round: int = 0):
    status = {
        "manual_final_review_failed": "failed",
        "manual_final_review_incomplete": "incomplete",
    }.get(phase, "failed")
    return {
        "phase": phase,
        "dispatch_freeze": {
            "status": "active",
            "reason": "manual-final",
            "allowed_running_role_ids": [],
        },
        "review_policy": {"max_fix_rounds": 2},
        "review_convergence": {
            "status": "exhausted" if phase == "review_convergence_exhausted" else "pending",
            "fix_round": fix_round,
            "authorized_extra_rounds": 0,
            "extra_round_authorization_refs": [],
            "verified_actionable_findings": finding_count,
            "artifact_refs": ["review.json"],
        },
        "manual_final_review": {
            "status": status,
            "certification_id": "C001",
            "prepared_plan": "reviews/final/PLAN.json",
            "prepared_plan_digest": "sha256:" + "a" * 64,
            "manifest": "reviews/final/MANIFEST.json",
            "report": "reviews/final/FINAL.md",
            "manifest_complete": phase == "manual_final_review_failed",
            "shortfall_count": 0,
            "verified_actionable_findings": finding_count,
            "attempt_history": [],
        },
        "release_scope": {
            "status": "locked",
            "source_refs": ["PLAN.md"],
            "source_digests": {"scope": SOURCE_DIGEST},
            "scope_revision": 1,
            "source_snapshot_revision": 1,
            "amendment_refs": [],
        },
    }


class ReopenTransitionTests(unittest.TestCase):
    def test_exhausted_remediate_requires_and_records_extra_round_authorization(self):
        state = reopen_state(phase="review_convergence_exhausted", fix_round=2)
        result = reopen_review(
            state,
            action="remediate",
            user_input_id="U0001",
            authorized_extra_rounds=1,
            authorization_input_id="U0002",
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.state["phase"], "review_convergence")
        self.assertEqual(result.state["dispatch_freeze"]["status"], "inactive")
        self.assertEqual(result.state["review_convergence"]["fix_round"], 3)
        self.assertEqual(result.state["review_convergence"]["authorized_extra_rounds"], 0)
        self.assertEqual(
            result.state["review_convergence"]["extra_round_authorization_refs"],
            ["U0002"],
        )

    def test_exhausted_remediate_without_extra_authorization_is_rejected(self):
        state = reopen_state(phase="review_convergence_exhausted", fix_round=2)
        validation = validate_reopen_transition(
            state, action="remediate", user_input_id="U0001"
        )
        self.assertFalse(validation.accepted)
        self.assertIn("authorized-extra-rounds-required", validation.issues)

    def test_incomplete_review_can_only_retry_certification(self):
        state = reopen_state(
            phase="manual_final_review_incomplete", finding_count=0, fix_round=1
        )
        result = reopen_review(
            state, action="retry-certification", user_input_id="U0001"
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.state["phase"], "awaiting_manual_final_review")
        self.assertEqual(result.state["dispatch_freeze"]["status"], "active")
        self.assertEqual(result.state["manual_final_review"]["status"], "pending")

        invalid = validate_reopen_transition(
            state, action="remediate", user_input_id="U0001"
        )
        self.assertFalse(invalid.accepted)
        self.assertIn(
            "remediate-requires-confirmed-in-scope-finding", invalid.issues
        )

    def test_scope_amend_updates_scope_and_returns_to_readiness(self):
        state = reopen_state(phase="manual_final_review_failed", finding_count=1)
        amendment = {
            "kind": "scope-change",
            "input_id": "U0001",
            "scope_revision": 2,
            "source_snapshot_revision": 2,
            "source_digest": "b" * 64,
            "source_refs": ["MISSION.md", "PLAN.md"],
        }
        result = reopen_review(
            state,
            action="scope-amend",
            user_input_id="U0001",
            scope_amendment=amendment,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.state["phase"], "review_readiness")
        self.assertEqual(result.state["release_scope"]["scope_revision"], 2)
        self.assertEqual(result.state["release_scope"]["source_snapshot_revision"], 2)
        self.assertEqual(result.state["release_scope"]["last_user_input_id"], "U0001")

    def test_failed_reopen_returns_original_frozen_state_without_mutation(self):
        state = reopen_state(phase="manual_final_review_failed", finding_count=1)
        before = json.loads(json.dumps(state))
        result = reopen_review(
            state,
            action="scope-amend",
            user_input_id="U0001",
            scope_amendment={"kind": "scope-change", "input_id": "U9999"},
        )
        self.assertFalse(result.accepted)
        self.assertEqual(state, before)
        self.assertEqual(result.state, before)
        self.assertIn("scope-amendment-input-mismatch", result.issues)
        self.assertIn("scope-amendment-revision-invalid", result.issues)

    def test_abort_keeps_freeze_active(self):
        state = reopen_state(phase="manual_final_review_failed", finding_count=1)
        result = reopen_review(state, action="abort", user_input_id="U0001")
        self.assertTrue(result.accepted)
        self.assertEqual(result.state["phase"], "paused")
        self.assertEqual(result.state["dispatch_freeze"]["status"], "active")


@unittest.skipUnless(jsonschema is not None, "jsonschema is required for schema tests")
class FinalReviewSchemaTests(unittest.TestCase):
    def _validator(self, path: Path):
        registry = Registry()
        for schema_path in (
            SCHEMAS / "review-manifest.schema.json",
            SCHEMAS / "review-finding.schema.json",
            path,
        ):
            registry = registry.with_resource(
                schema_path.resolve().as_uri(),
                Resource.from_contents(
                    json.loads(schema_path.read_text(encoding="utf-8"))
                ),
            )
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        return jsonschema.Draft202012Validator(
            {"$schema": schema["$schema"], "$ref": path.resolve().as_uri()},
            registry=registry,
        )

    def test_plan_schema_accepts_canonical_plan(self):
        group = review_plan()
        plan = certification_plan(group)
        errors = list(
            self._validator(SCHEMAS / "final-review-plan.schema.json").iter_errors(
                plan.to_record()
            )
        )
        self.assertEqual(errors, [])

    def test_manifest_schema_accepts_complete_zero_finding_manifest(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        errors = list(
            self._validator(SCHEMAS / "final-review-manifest.schema.json").iter_errors(
                manifest.to_record()
            )
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
