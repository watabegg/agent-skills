from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # pragma: no cover - minimal installations skip schema tests
    jsonschema = None
    Registry = None
    Resource = None
    DRAFT202012 = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
PUBLIC_SCHEMAS = Path(__file__).parents[1] / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib import decisions as hloop_decisions  # noqa: E402
from hloop_lib import release_scope as hloop_release_scope  # noqa: E402
from hloop_lib.certification import (  # noqa: E402
    MANUAL_FINAL_PROTOCOL,
    CertificationPlan,
    FinalReviewLane,
    FinalReviewManifest,
    FinalReviewProcessIdentity,
    FinalReviewProcessPlan,
    ManualFinalExecutionProvenance,
    VerificationPolicy,
    canonical_digest,
    reopen_review,
    validate_final_review,
    validate_plan_digest,
    validate_reopen_transition,
)
from hloop_lib.config import project_agent_identity  # noqa: E402
from hloop_lib.review import (  # noqa: E402
    ExternalReviewProtocolAdapter,
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
ADAPTER = ExternalReviewProtocolAdapter(
    protocol=MANUAL_FINAL_PROTOCOL,
    source="https://example.invalid/codex-review-multi-v2.git@" + "c" * 40,
    version="2.1.0",
    content_digest="sha256:" + "d" * 64,
    capabilities=("externally-planned-v1",),
)


def process_config(provider: str, model: str, effort: str) -> dict:
    return {
        "provider": provider,
        "model": model,
        "effort": effort,
        "config_sources": {
            "provider": "fixture-config",
            "model": "fixture-config",
            "effort": "fixture-config",
        },
        "config_provenance": {
            "provider": [
                {"source": "fixture-config", "input_key": "provider", "value": provider}
            ],
            "model": [
                {"source": "fixture-config", "input_key": "model", "value": model}
            ],
            "effort": [
                {"source": "fixture-config", "input_key": "effort", "value": effort}
            ],
        },
    }


def review_plan(*, mode: str = "single", max_verifications: int = 64, verifier_pool_size: int = 2):
    return plan_review_group(
        mode,
        head_sha=HEAD,
        max_verifications=max_verifications,
        verifier_pool_size=verifier_pool_size,
    )


def certification_plan(group, *, target_sha: str = HEAD) -> CertificationPlan:
    processes = [
        FinalReviewProcessPlan(
            process_id="manual-final-coordinator",
            process_kind="coordinator",
            agent_label="manual-final-coordinator",
            **process_config("codex", "gpt-5.6-sol", "max"),
        )
    ]
    for provider_plan in group.provider_plans:
        if provider_plan.role == "coordinator":
            processes.append(
                FinalReviewProcessPlan(
                    process_id=f"coordinator-{provider_plan.provider}",
                    process_kind="coordinator",
                    agent_label=provider_plan.coordinator_label,
                    **process_config(
                        provider_plan.provider, provider_plan.model, "xhigh"
                    ),
                )
            )
        processes.extend(
            FinalReviewProcessPlan(
                process_id=f"lane-{lane.provider}-{lane.lane_id}",
                process_kind="discovery",
                agent_label=lane.agent_label,
                **process_config(lane.provider, provider_plan.model, "xhigh"),
            )
            for lane in provider_plan.lanes
        )
        processes.extend(
            FinalReviewProcessPlan(
                process_id=f"verifier-{provider_plan.provider}-{index}",
                process_kind="verifier",
                agent_label=label,
                **process_config(
                    provider_plan.provider, provider_plan.model, "xhigh"
                ),
            )
            for index, label in enumerate(provider_plan.verifier_agents, start=1)
        )
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
        execution_kind="manual-final",
        protocol_key="review.manual_final_protocol",
        protocol=MANUAL_FINAL_PROTOCOL,
        process_plan=tuple(processes),
        final_coordinator_config={
            "provider": "codex",
            "model": "gpt-5.6-sol",
            "effort": "max",
            "sources": {
                "provider": "config-defaults",
                "model": "config-defaults",
                "effort": "config-defaults",
            },
            "provenance": {
                "provider": [],
                "model": [],
                "effort": [],
            },
        },
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
        execution=ManualFinalExecutionProvenance(
            execution_policy="independent",
            execution_id="R001",
            source_kind="pre-final-review",
            source_execution_id="R000",
            source_artifact_ref="reviews/convergence/MANIFEST.json",
            source_artifact_digest="sha256:" + "e" * 64,
            target_sha=target_sha,
            protocol_adapter=ADAPTER,
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
    manifest = FinalReviewManifest.from_review_manifest(
        plan,
        review,
        verified_actionable_findings=verified_actionable_findings,
        patch_verdict=patch_verdict,
    )
    identities = tuple(
        FinalReviewProcessIdentity(
            process_id=process.process_id,
            agent_identity=project_agent_identity(
                {
                    "provider": process.provider,
                    "model": process.model,
                    "effort": process.effort,
                },
                observed={
                    "provider": process.provider,
                    "model": process.model,
                    "effort": process.effort,
                },
                attested={
                    "provider": process.provider,
                    "model": process.model,
                    "effort": process.effort,
                },
            ).as_dict(),
        )
        for process in plan.process_plan
    )
    return replace(manifest, process_identities=identities)


def candidate(
    *,
    severity: str = "P2",
    origin: str = "introduced",
    contract_relation: str = "in_scope",
    decision_requirement: str = "none",
    fact_status: str = "confirmed",
    disposition: str | None = None,
    release_effect: str | None = None,
    product_impact: str = "the item is never processed",
) -> FindingCandidate:
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
        product_impact=product_impact,
        origin=origin,
        proposed_fix="commit acknowledgement with the state transition",
        fact_status=fact_status,
        contract_relation=contract_relation,
        decision_requirement=decision_requirement,
        disposition=disposition or ("discard" if fact_status == "refuted" else "fix_now"),
        release_effect=release_effect
        or ("non_blocking" if fact_status == "refuted" else "blocking"),
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
            replace(
                plan,
                target_sha="another-target",
                execution=replace(plan.execution, target_sha="another-target"),
            ),
            replace(plan, scope_revision=2),
            replace(plan, source_snapshot_revision=2),
            replace(plan, source_digest="b" * 64),
            replace(plan, execution_kind="", protocol_key=""),
            replace(
                plan,
                process_plan=(
                    replace(plan.process_plan[0], agent_label="another-coordinator"),
                    *plan.process_plan[1:],
                ),
            ),
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
        self.assertEqual(restored.execution, plan.execution)

    def test_execution_policy_rejects_duplicate_or_synthetic_source_identity(self):
        base = certification_plan(review_plan()).execution
        assert base is not None
        with self.assertRaisesRegex(
            ValueError, "independent manual-final execution must differ"
        ):
            replace(base, source_execution_id=base.execution_id)
        with self.assertRaisesRegex(ValueError, "source_kind must be pre-final-review"):
            replace(base, source_kind="review-epoch-reviewer")

    def test_reuse_policy_requires_exact_epoch_execution_identity(self):
        base = certification_plan(review_plan()).execution
        assert base is not None
        reused = replace(
            base,
            execution_policy="reuse_epoch_reviewer",
            source_kind="review-epoch-reviewer",
            source_execution_id=base.execution_id,
        )
        self.assertEqual(
            reused.execution_id, reused.source_execution_id
        )
        with self.assertRaisesRegex(ValueError, "reuse the exact source execution"):
            replace(reused, source_execution_id="R002")


class FinalManifestCompletenessTests(unittest.TestCase):
    def test_complete_zero_finding_manifest_passes(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        result = validate_final_review(plan, manifest, current_target_sha=HEAD)
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.completeness.issues, ())

    def test_process_identity_evidence_fails_closed(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)

        missing = validate_final_review(
            plan,
            replace(
                manifest,
                process_identities=tuple(
                    item
                    for item in manifest.process_identities
                    if item.process_id != "manual-final-coordinator"
                ),
            ),
            current_target_sha=HEAD,
        )
        self.assertIn(
            "process-identity-missing:manual-final-coordinator",
            missing.issues,
        )

        first = next(
            item
            for item in manifest.process_identities
            if item.process_id == "manual-final-coordinator"
        )
        remaining = tuple(
            item
            for item in manifest.process_identities
            if item.process_id != "manual-final-coordinator"
        )
        mismatch_identity = project_agent_identity(
            {**first.agent_identity["requested"], "model": "other-model"},
            observed={**first.agent_identity["requested"], "model": "other-model"},
            attested={**first.agent_identity["requested"], "model": "other-model"},
        ).as_dict()
        mismatched = validate_final_review(
            plan,
            replace(
                manifest,
                process_identities=(
                    replace(first, agent_identity=mismatch_identity),
                    *remaining,
                ),
            ),
            current_target_sha=HEAD,
        )
        self.assertIn(
            "process-identity-requested-mismatch:manual-final-coordinator",
            mismatched.issues,
        )

        unattested_identity = project_agent_identity(
            first.agent_identity["requested"],
            observed={
                **first.agent_identity["requested"],
                "model": "other-model",
            },
        ).as_dict()
        unattested = validate_final_review(
            plan,
            replace(
                manifest,
                process_identities=(
                    replace(first, agent_identity=unattested_identity),
                    *remaining,
                ),
            ),
            current_target_sha=HEAD,
        )
        self.assertIn(
            "process-identity-attestation-invalid:manual-final-coordinator",
            unattested.issues,
        )

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

    def test_explicit_axes_recompute_actionable_and_blocking_counts(self):
        group = review_plan()
        plan = certification_plan(group)
        normalized = normalize_findings((candidate(),))
        verification = plan_verification(group, normalized)
        records = tuple(
            VerificationRecord.from_assignment(
                assignment,
                fact_status="confirmed",
                ignore_status="may_defer",
                decision_status="none",
                progress_without_decision="yes",
                severity="P2",
                # Deliberately contradict the explicit disposition axes.
                recommended_action="discard",
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
        self.assertEqual(manifest.recomputed_verified_actionable_count, 1)
        self.assertEqual(manifest.recomputed_verified_release_blocking_count, 1)
        result = validate_final_review(plan, manifest)
        self.assertFalse(result.passed)
        self.assertNotIn("verified-actionable-finding-count-mismatch", result.issues)

    def test_unsafe_explicit_dispositions_are_rejected_before_zero_count(self):
        cases = (
            candidate(
                severity="P1",
                disposition="defer_follow_up",
                release_effect="non_blocking",
            ),
            candidate(
                disposition="discard",
                release_effect="non_blocking",
            ),
            candidate(
                disposition="accepted_risk",
                release_effect="non_blocking",
            ),
            candidate(
                decision_requirement="user",
                disposition="defer_follow_up",
                release_effect="non_blocking",
            ),
        )
        for item in cases:
            with self.subTest(disposition=item.disposition):
                group = review_plan()
                plan = certification_plan(group)
                normalized = normalize_findings((item,))
                manifest = final_manifest(plan, group, findings=normalized)
                result = validate_final_review(plan, manifest)
                self.assertFalse(result.passed)
                self.assertEqual(result.status, "failed")
                self.assertTrue(any(issue.startswith("policy:") for issue in result.issues))

    def test_fresh_scope_rejects_legacy_manifest_but_migrated_scope_falls_back(self):
        group = review_plan()
        plan = certification_plan(group)
        normalized = normalize_findings(
            (
                candidate(
                    origin="unrelated-pre-existing",
                    contract_relation="outside_release",
                    disposition="defer_follow_up",
                    release_effect="non_blocking",
                ),
            )
        )
        verification = plan_verification(group, normalized)
        records = tuple(
            VerificationRecord.from_assignment(
                assignment,
                fact_status="confirmed",
                ignore_status="may_defer",
                decision_status="none",
                progress_without_decision="yes",
                severity="P2",
                recommended_action="discard",
            )
            for assignment in verification.assignments
        )
        manifest = final_manifest(
            plan,
            group,
            findings=normalized,
            verifications=records,
        )
        record = manifest.to_record()
        for finding in record["findings"]:
            for field_name in (
                "fact_status",
                "contract_relation",
                "decision_requirement",
                "disposition",
                "release_effect",
                "policy_axes_explicit",
            ):
                finding.pop(field_name, None)
            for candidate_record in finding["candidates"]:
                for field_name in (
                    "fact_status",
                    "contract_relation",
                    "decision_requirement",
                    "disposition",
                    "release_effect",
                    "policy_axes_explicit",
                ):
                    candidate_record.pop(field_name, None)
        legacy = FinalReviewManifest.from_record(record)
        fresh = validate_final_review(plan, legacy)
        self.assertFalse(fresh.passed)
        self.assertTrue(any("explicit policy axes" in issue for issue in fresh.issues))
        migrated = validate_final_review(plan, legacy, allow_legacy=True)
        self.assertTrue(migrated.passed)

    def test_legacy_execution_compatibility_requires_both_artifacts_to_omit_provenance(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        legacy_plan = replace(plan, execution=None)
        legacy_manifest = replace(
            manifest,
            prepared_plan_digest=legacy_plan.digest,
            execution=None,
        )

        self.assertTrue(
            validate_final_review(
                legacy_plan, legacy_manifest, allow_legacy=True
            ).passed
        )
        for prepared, evidence in (
            (plan, replace(manifest, execution=None)),
            (
                legacy_plan,
                replace(manifest, prepared_plan_digest=legacy_plan.digest),
            ),
        ):
            with self.subTest(
                plan_execution=prepared.execution is not None,
                manifest_execution=evidence.execution is not None,
            ):
                result = validate_final_review(
                    prepared, evidence, allow_legacy=True
                )
                self.assertFalse(result.passed)
                self.assertIn(
                    "identity-mismatch:execution-provenance", result.issues
                )

    def test_identity_and_target_drift_are_rejected(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        drifted_plan = replace(
            plan,
            target_sha="new-target",
            execution=replace(plan.execution, target_sha="new-target"),
        )
        result = validate_final_review(drifted_plan, manifest)
        self.assertFalse(result.passed)
        self.assertIn("identity-mismatch:target-sha", result.issues)
        current_drift = validate_final_review(plan, manifest, current_target_sha="new-target")
        self.assertIn("target-sha-drift", current_drift.issues)

        assert manifest.execution is not None
        provenance_drift = replace(
            manifest,
            execution=replace(
                manifest.execution,
                source_artifact_digest="sha256:" + "f" * 64,
            ),
        )
        provenance_result = validate_final_review(plan, provenance_drift)
        self.assertIn(
            "identity-mismatch:execution-provenance", provenance_result.issues
        )

    def test_scope_identity_change_rejects_the_old_manifest(self):
        group = review_plan()
        plan = certification_plan(group)
        manifest = final_manifest(plan, group)
        amended_plan = replace(
            plan,
            scope_revision=2,
            source_snapshot_revision=2,
            source_digest="b" * 64,
        )

        result = validate_final_review(amended_plan, manifest)

        self.assertFalse(result.passed)
        self.assertIn("identity-mismatch:prepared-plan-digest", result.issues)
        self.assertIn("identity-mismatch:scope-revision", result.issues)
        self.assertIn(
            "identity-mismatch:source-snapshot-revision", result.issues
        )
        self.assertIn("identity-mismatch:source-digest", result.issues)

    def test_explicit_verification_consensus_must_match_normalized_fact(self):
        group = review_plan()
        plan = certification_plan(group)
        for normalized_fact, verifier_fact in (
            ("confirmed", "refuted"),
            ("refuted", "confirmed"),
        ):
            with self.subTest(normalized_fact=normalized_fact, verifier_fact=verifier_fact):
                normalized = normalize_findings(
                    (candidate(fact_status=normalized_fact),)
                )
                verification = plan_verification(group, normalized)
                verifier_records = tuple(
                    VerificationRecord.from_assignment(
                        assignment,
                        fact_status=verifier_fact,
                        ignore_status=(
                            "must_not_ignore"
                            if verifier_fact == "confirmed"
                            else "no_action"
                        ),
                        decision_status="none",
                        progress_without_decision="yes",
                        severity="P2",
                        recommended_action=(
                            "fix_task" if verifier_fact == "confirmed" else "discard"
                        ),
                    )
                    for assignment in verification.assignments
                )
                manifest = final_manifest(
                    plan,
                    group,
                    findings=normalized,
                    verifications=verifier_records,
                )

                self.assertFalse(manifest.completeness.complete)
                self.assertIn(
                    f"verification-consensus-mismatch:{normalized[0].fingerprint}",
                    manifest.completeness.issues,
                )
                result = validate_final_review(plan, manifest)
                self.assertFalse(result.passed)

    def test_finish_time_accepted_risk_revalidation_checks_identity_and_expiry(self):
        fingerprint = "sha256:" + "a" * 64
        target = "target-sha"
        decision = {
            "id": "D001",
            "class": "advisory",
            "status": "accepted",
            "question": "Accept the fixed-target residual risk?",
            "options": [
                {
                    "id": "accept",
                    "label": "Accept",
                    "tradeoffs": ["The residual risk remains documented."],
                },
                {
                    "id": "fix",
                    "label": "Fix",
                    "tradeoffs": ["The release remains blocked until fixed."],
                },
            ],
            "recommendation": {
                "option_id": "accept",
                "rationale": "The approved contract accepts this risk.",
            },
            "resolution": {
                "outcome": "accepted",
                "rationale": "Accepted for the fixed target.",
                "resolved_by": "release-owner",
                "resolved_at": "2026-07-16T00:00:00Z",
                "selected_option": "accept",
            },
            "accepted_risk_authorization": {
                "finding_fingerprint": fingerprint,
                "target_sha": target,
                "authorized_by": "release-owner",
                "risk": "A residual compatibility behavior remains.",
                "reason": "The approved contract accepts this risk.",
                "expires_at": "2026-07-20T00:00:00Z",
            },
        }
        finding = {
            "fingerprint": fingerprint,
            "head_sha": target,
            "disposition": "accepted_risk",
            "accepted_risk_decision_id": "D001",
        }

        resolved = hloop_decisions.resolve_accepted_risk_authorizations(
            {"D001": decision},
            (finding,),
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(resolved[fingerprint].decision_id, "D001")

        for field, value, message in (
            ("finding_fingerprint", "sha256:" + "b" * 64, "different finding"),
            ("target_sha", "other-target", "different SHA"),
            ("expires_at", "2026-07-16T00:00:00Z", "expired"),
        ):
            with self.subTest(field=field):
                invalid = json.loads(json.dumps(decision))
                invalid["accepted_risk_authorization"][field] = value
                with self.assertRaisesRegex(
                    hloop_decisions.DecisionAuthorizationError, message
                ):
                    hloop_decisions.resolve_accepted_risk_authorizations(
                        {"D001": invalid},
                        (finding,),
                        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
                    )


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
            "source_digests": {"PLAN.md": SOURCE_DIGEST},
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
        state["review_convergence"].update(
            {
                "status": "converged",
                "review_plan": {"head_sha": "old-target"},
                "recorded_round": 1,
                "recorded_manifest_digest": "sha256:" + "c" * 64,
                "recorded_status": "converged",
                "accepted_risk_authorizations": {"old": {"decision_id": "D001"}},
            }
        )
        state["accepted_risk_authorizations"] = {
            "old": {"decision_id": "D001"}
        }
        state["manual_final_review"]["accepted_risk_authorizations"] = {
            "old": {"decision_id": "D001"}
        }
        scope = hloop_release_scope.ReleaseScope.from_record(state["release_scope"])
        amendment = hloop_release_scope.create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            reason="authorized scope change",
            new_source_digests={"PLAN.md": "b" * 64},
            basis_refs=("REQ-005",),
            user_input_id="U0001",
            created_at="2026-07-16T00:00:00+00:00",
        ).to_record()
        amendment["reopen_user_input_id"] = "U0001"
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
        convergence = result.state["review_convergence"]
        self.assertEqual(convergence["status"], "pending")
        self.assertEqual(convergence["artifact_refs"], [])
        self.assertNotIn("review_plan", convergence)
        self.assertNotIn("recorded_manifest_digest", convergence)
        self.assertNotIn("recorded_round", convergence)
        self.assertNotIn("accepted_risk_authorizations", convergence)
        self.assertNotIn("accepted_risk_authorizations", result.state)
        self.assertNotIn(
            "accepted_risk_authorizations", result.state["manual_final_review"]
        )

    def test_non_scope_reopen_amendments_do_not_record_outer_user_input(self):
        for kind, basis_refs in (("editorial", ()), ("clarification", ("REQ-005",))):
            with self.subTest(kind=kind):
                state = reopen_state(phase="manual_final_review_failed", finding_count=1)
                scope = hloop_release_scope.ReleaseScope.from_record(
                    state["release_scope"]
                )
                amendment = hloop_release_scope.create_amendment(
                    scope,
                    amendment_id="A001",
                    kind=kind,
                    reason=f"{kind} reopen amendment",
                    new_source_digests={"PLAN.md": "b" * 64},
                    basis_refs=basis_refs,
                    created_at="2026-07-16T00:00:00+00:00",
                ).to_record()
                amendment["reopen_user_input_id"] = "U0001"
                result = reopen_review(
                    state,
                    action="scope-amend",
                    user_input_id="U0001",
                    scope_amendment=amendment,
                )
                self.assertTrue(result.accepted)
                self.assertEqual(
                    result.state["release_scope"]["last_user_input_id"], ""
                )

    def test_failed_reopen_returns_original_frozen_state_without_mutation(self):
        state = reopen_state(phase="manual_final_review_failed", finding_count=1)
        before = json.loads(json.dumps(state))
        scope = hloop_release_scope.ReleaseScope.from_record(state["release_scope"])
        amendment = hloop_release_scope.create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            reason="authorized scope change",
            new_source_digests={"PLAN.md": "b" * 64},
            basis_refs=("REQ-005",),
            user_input_id="U0001",
            created_at="2026-07-16T00:00:00+00:00",
        ).to_record()
        amendment["reopen_user_input_id"] = "U9999"
        amendment["new_scope_revision"] = 3
        result = reopen_review(
            state,
            action="scope-amend",
            user_input_id="U0001",
            scope_amendment=amendment,
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
            SCHEMAS / "final-review-plan.schema.json",
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

    def _offline_validator(self, path: Path):
        def retrieve(uri: str):
            if not uri.startswith("file://"):
                raise AssertionError(
                    f"offline final-review validator attempted network retrieval: {uri}"
                )
            from urllib.parse import unquote, urlparse

            local_path = Path(unquote(urlparse(uri).path))
            return Resource.from_contents(
                json.loads(local_path.read_text(encoding="utf-8")),
                default_specification=DRAFT202012,
            )

        registry = Registry(retrieve=retrieve)
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

    def test_public_wrappers_match_canonical_execution_semantics_offline(self):
        group = review_plan()
        plan = certification_plan(group)
        records = {
            "final-review-plan.schema.json": plan.to_record(),
            "final-review-manifest.schema.json": final_manifest(
                plan, group
            ).to_record(),
        }
        for filename, record in records.items():
            with self.subTest(filename=filename):
                wrapper = json.loads(
                    (PUBLIC_SCHEMAS / filename).read_text(encoding="utf-8")
                )
                self.assertNotIn("$id", wrapper)
                self.assertEqual(
                    wrapper["$ref"], f"../references/schemas/{filename}"
                )
                canonical = self._validator(SCHEMAS / filename)
                public = self._offline_validator(PUBLIC_SCHEMAS / filename)
                self.assertEqual(list(canonical.iter_errors(record)), [])
                self.assertEqual(list(public.iter_errors(record)), [])

                invalid = json.loads(json.dumps(record))
                invalid["execution"]["execution_policy"] = "synthetic"
                self.assertTrue(list(canonical.iter_errors(invalid)))
                self.assertTrue(list(public.iter_errors(invalid)))

                missing_execution = json.loads(json.dumps(record))
                del missing_execution["execution"]
                self.assertTrue(list(canonical.iter_errors(missing_execution)))
                self.assertTrue(list(public.iter_errors(missing_execution)))

                leading_zero_version = json.loads(json.dumps(record))
                leading_zero_version["execution"]["protocol_adapter"]["version"] = (
                    "02.1.0"
                )
                self.assertTrue(list(canonical.iter_errors(leading_zero_version)))
                self.assertTrue(list(public.iter_errors(leading_zero_version)))


if __name__ == "__main__":
    unittest.main()
