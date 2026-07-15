from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError:  # pragma: no cover - exercised by minimal installations
    jsonschema = None
    Registry = None
    Resource = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
RUNTIME_SCHEMAS = Path(__file__).parents[1] / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.review import (  # noqa: E402
    FindingCandidate,
    ReviewManifest,
    ReviewModelError,
    VerificationRecord,
    normalize_findings,
    plan_review_group,
    plan_verification,
)


HEAD = "abc123"


def review_schema_validator(schema_path: Path):
    """Load a review schema with repository-relative references available."""

    registry = Registry()
    for path in (
        SCHEMAS / "review-manifest.schema.json",
        SCHEMAS / "review-finding.schema.json",
        SCHEMAS / "review-group-state.schema.json",
        RUNTIME_SCHEMAS / "review-group-state.schema.json",
    ):
        registry = registry.with_resource(
            path.resolve().as_uri(),
            Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))),
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=registry,
    )


def candidate(
    finding_id: str = "C-F001",
    *,
    provider: str = "codex",
    head_sha: str = HEAD,
    discovering_agent: str | None = None,
    severity: str = "P2",
    title: str = "Queue retry can lose the pending item",
    file_path: str = "src/queue.py",
    line: int = 42,
    symbol: str = "drain_queue",
    trigger: str = "Worker crashes after acknowledging the queue item",
    product_impact: str = "The pending item is never processed",
    proposed_fix: str = "Commit the acknowledgement with the state transition",
    requires_spec_decision: bool = False,
) -> FindingCandidate:
    return FindingCandidate(
        finding_id=finding_id,
        provider=provider,
        head_sha=head_sha,
        discovering_agent=discovering_agent or f"{provider}-discovery-01",
        severity=severity,
        confidence=0.9,
        title=title,
        file_path=file_path,
        line=line,
        symbol=symbol,
        trigger=trigger,
        product_impact=product_impact,
        origin="introduced",
        proposed_fix=proposed_fix,
        requires_spec_decision=requires_spec_decision,
    )


def confirmed_records(verification_plan):
    return tuple(
        VerificationRecord.from_assignment(
            assignment,
            fact_status="confirmed",
            ignore_status="must_not_ignore",
            decision_status="none",
            progress_without_decision="yes",
            severity="P1" if assignment.pass_number == 2 else "P2",
            recommended_action="fix_task",
        )
        for assignment in verification_plan.assignments
    )


def completed_lanes(group, findings=()):
    counts = Counter(
        (candidate.provider, candidate.discovering_agent)
        for finding in findings
        for candidate in finding.candidates
    )
    return tuple(
        lane.result(finding_count=counts[(lane.provider, lane.agent_label)])
        for lane in group.expected_lanes
    )


class ReviewGroupPlanningTests(unittest.TestCase):
    def test_modes_have_deterministic_provider_and_lane_topologies(self):
        single = plan_review_group("single", head_sha=HEAD)
        swarm = plan_review_group("swarm", head_sha=HEAD)
        dual = plan_review_group("dual", head_sha=HEAD)
        dual_swarm = plan_review_group("dual-swarm", head_sha=HEAD)

        self.assertEqual(single.providers, ("codex",))
        self.assertEqual([len(plan.lanes) for plan in single.provider_plans], [1])
        self.assertEqual([len(plan.lanes) for plan in swarm.provider_plans], [6])
        self.assertEqual(dual.providers, ("codex", "claude"))
        self.assertEqual([len(plan.lanes) for plan in dual.provider_plans], [1, 1])
        self.assertEqual(dual_swarm.providers, ("codex", "claude"))
        self.assertEqual(
            [len(plan.lanes) for plan in dual_swarm.provider_plans], [4, 4]
        )
        self.assertEqual(
            plan_review_group("swarm", head_sha=HEAD),
            plan_review_group("swarm", head_sha=HEAD),
        )

    def test_swarm_accepts_four_to_eight_lanes_and_rejects_outside_range(self):
        four = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        eight = plan_review_group("swarm", head_sha=HEAD, probe_count=8)
        self.assertEqual(len(four.expected_lanes), 4)
        self.assertEqual(len(eight.expected_lanes), 8)

        for count in (3, 9):
            with self.subTest(count=count), self.assertRaisesRegex(
                ReviewModelError, "4 to 8"
            ):
                plan_review_group("swarm", head_sha=HEAD, probe_count=count)

    def test_all_provider_plans_are_pinned_to_the_same_sha(self):
        plan = plan_review_group(
            "dual-swarm",
            head_sha=HEAD,
            providers=("claude", "codex"),
            probes_per_provider=5,
        )
        self.assertEqual(plan.providers, ("codex", "claude"))
        self.assertEqual({item.head_sha for item in plan.provider_plans}, {HEAD})


class FindingNormalizationTests(unittest.TestCase):
    def test_fingerprint_dedupes_provider_wording_not_title_or_line(self):
        codex = candidate()
        claude = candidate(
            "A-F009",
            provider="claude",
            title="Acknowledged work disappears after a crash",
            line=47,
        )

        normalized = normalize_findings((codex, claude))

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].classification, "consensus")
        self.assertTrue(normalized[0].cross_model_consensus)
        self.assertEqual(normalized[0].providers, ("codex", "claude"))
        self.assertEqual(len(normalized[0].candidates), 2)

    def test_semantic_fingerprint_changes_when_the_fix_changes(self):
        first = candidate()
        second = candidate(
            "C-F002", proposed_fix="Never acknowledge work until process shutdown"
        )
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(normalize_findings((first, second))), 2)

    def test_consensus_requires_matching_target_sha(self):
        same_sha = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        different_sha = normalize_findings(
            (
                candidate(),
                candidate("A-F001", provider="claude", head_sha="different-head"),
            )
        )

        self.assertEqual(same_sha[0].classification, "consensus")
        self.assertEqual(len(different_sha), 2)
        self.assertEqual(
            {finding.classification for finding in different_sha}, {"unique"}
        )


class VerificationPlanningTests(unittest.TestCase):
    def test_p0_and_p1_get_two_independent_passes_while_p2_gets_one(self):
        group = plan_review_group("single", head_sha=HEAD, verifier_pool_size=2)
        findings = normalize_findings(
            (
                candidate("C-F000", severity="P0"),
                candidate(
                    "C-F001",
                    severity="P1",
                    file_path="src/other.py",
                    symbol="write_state",
                ),
                candidate(
                    "C-F002",
                    severity="P2",
                    file_path="src/minor.py",
                    symbol="format_result",
                ),
            )
        )

        verification = plan_verification(group, findings)
        by_severity = {
            finding.severity: verification.assignments_for(finding.fingerprint)
            for finding in findings
        }
        self.assertEqual(len(by_severity["P0"]), 2)
        self.assertEqual(len(by_severity["P1"]), 2)
        self.assertEqual(len(by_severity["P2"]), 1)
        for severity in ("P0", "P1"):
            assignments = by_severity[severity]
            self.assertEqual({item.pass_number for item in assignments}, {1, 2})
            self.assertEqual(len({item.verifier_agent for item in assignments}), 2)

    def test_dual_critical_finding_requires_both_provider_verifiers(self):
        group = plan_review_group("dual", head_sha=HEAD)
        finding = normalize_findings((candidate(severity="P1"),))[0]

        assignments = plan_verification(group, (finding,)).assignments

        self.assertEqual({item.provider for item in assignments}, {"codex", "claude"})
        self.assertEqual({item.pass_number for item in assignments}, {1, 2})

    def test_discoverer_cannot_fill_a_verifier_pass(self):
        group = plan_review_group("single", head_sha=HEAD, verifier_pool_size=2)
        finding = normalize_findings(
            (
                candidate(
                    severity="P0", discovering_agent="codex-verifier-01"
                ),
            )
        )[0]

        verification = plan_verification(group, (finding,))

        self.assertEqual(len(verification.assignments), 1)
        self.assertEqual(verification.assignments[0].verifier_agent, "codex-verifier-02")
        self.assertEqual(
            verification.shortfalls[0].reason,
            "independent-verifier-unavailable",
        )

    def test_budget_exhaustion_marks_unverified_candidate_instead_of_dropping_it(self):
        group = plan_review_group(
            "single", head_sha=HEAD, max_verifications=1
        )
        findings = normalize_findings(
            (
                candidate("C-F001"),
                candidate(
                    "C-F002",
                    file_path="src/second.py",
                    symbol="second_path",
                ),
            )
        )

        verification = plan_verification(group, findings)

        self.assertEqual(len(findings), 2)
        self.assertEqual(len(verification.assignments), 1)
        self.assertTrue(verification.budget_exhausted)
        self.assertEqual(len(verification.shortfalls), 1)
        self.assertEqual(verification.shortfalls[0].reason, "budget-exhausted")
        self.assertIn(
            verification.shortfalls[0].fingerprint,
            {finding.fingerprint for finding in findings},
        )


class ManifestGateTests(unittest.TestCase):
    def test_complete_manifest_requires_all_lanes_and_verification_records(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        findings = normalize_findings((candidate(),))
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        self.assertTrue(manifest.completeness.complete)
        self.assertEqual(manifest.completeness.issues, ())
        self.assertTrue(manifest.to_record()["completeness"]["complete"])

    def test_missing_lane_fails_closed_and_names_the_lane(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes[:-1]),
            findings=(),
            verification_plan=plan_verification(group, ()),
            verifications=(),
        )

        result = manifest.completeness
        expected = group.expected_lanes[-1]
        self.assertFalse(result.complete)
        self.assertEqual(result.missing_lanes, (f"{expected.provider}:{expected.lane_id}",))
        self.assertIn(
            f"missing-lane:{expected.provider}:{expected.lane_id}", result.issues
        )

    def test_shortfall_and_missing_second_pass_keep_manifest_open(self):
        group = plan_review_group(
            "single", head_sha=HEAD, verifier_pool_size=1
        )
        findings = normalize_findings(
            (candidate(severity="P1", discovering_agent="codex-reviewer"),)
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        self.assertFalse(manifest.completeness.complete)
        self.assertTrue(
            any(
                issue.startswith("verification-shortfall:")
                for issue in manifest.completeness.issues
            )
        )
        self.assertEqual(
            manifest.completeness.incomplete_findings,
            (findings[0].fingerprint,),
        )
        self.assertEqual(manifest.confirmed_fingerprints, ())

    def test_manifest_rejects_lane_finding_count_drift(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        findings = normalize_findings((candidate(),))
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        self.assertFalse(manifest.completeness.complete)
        self.assertIn(
            "lane-finding-count-mismatch:codex:codex-L01",
            manifest.completeness.issues,
        )

    def test_manifest_round_trip_deserializes_actual_lane_and_verifier_data(self):
        group = plan_review_group("dual-swarm", head_sha=HEAD, probes_per_provider=4)
        findings = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        restored = ReviewManifest.from_record(
            json.loads(json.dumps(manifest.to_record()))
        )

        self.assertEqual(restored, manifest)
        self.assertTrue(restored.completeness.complete)
        self.assertEqual(restored.confirmed_fingerprints, (findings[0].fingerprint,))

    def test_manifest_deserialization_rejects_claimed_complete_missing_lane(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=plan_verification(group, ()),
            verifications=(),
        ).to_record()
        manifest["providers"][0]["lanes"].pop()

        with self.assertRaisesRegex(ReviewModelError, "completeness does not match"):
            ReviewManifest.from_record(manifest)

    def test_only_fully_confirmed_finding_is_triage_eligible(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        findings = normalize_findings((candidate(),))
        verification = plan_verification(group, findings)
        refuted = tuple(
            VerificationRecord.from_assignment(
                assignment,
                fact_status="refuted",
                ignore_status="no_action",
                decision_status="none",
                progress_without_decision="yes",
                severity="P2",
                recommended_action="discard",
            )
            for assignment in verification.assignments
        )
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=refuted,
        )

        self.assertTrue(manifest.completeness.complete)
        self.assertEqual(manifest.confirmed_fingerprints, ())


class ReviewSchemaTests(unittest.TestCase):
    @unittest.skipUnless(jsonschema is not None, "jsonschema is not installed")
    def test_runtime_records_validate_against_review_family_schemas(self):
        group = plan_review_group(
            "dual-swarm", head_sha=HEAD, probes_per_provider=4
        )
        findings = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )
        manifest_record = manifest.to_record()
        group_state_record = {
            "status": "reported",
            "gate_status": "reported",
            "mode": group.mode,
            "review_plan": group.to_record(),
            "review_providers": list(group.providers),
            "head_sha": group.head_sha,
            "review_path": ".ai/herdr-dev-loop/loops/example/reviews/R001/FINAL.md",
            "manifest_path": ".ai/herdr-dev-loop/loops/example/reviews/R001/MANIFEST.json",
            "provider_report_paths": {
                provider: (
                    ".ai/herdr-dev-loop/loops/example/reviews/"
                    f"R001/providers/{provider}.md"
                )
                for provider in group.providers
            },
            "provider_artifact_statuses": {
                provider: "reported" for provider in group.providers
            },
            "manifest_complete": manifest.completeness.complete,
            "manifest_issues": list(manifest.completeness.issues),
            "confirmed_finding_fingerprints": list(
                manifest.confirmed_fingerprints
            ),
        }

        records = (
            (
                SCHEMAS / "review-manifest.schema.json",
                manifest_record,
            ),
            (
                SCHEMAS / "review-finding.schema.json",
                findings[0].to_record(),
            ),
            (
                SCHEMAS / "review-group-state.schema.json",
                group_state_record,
            ),
        )
        for schema_path, record in records:
            with self.subTest(schema=schema_path.name):
                errors = sorted(
                    review_schema_validator(schema_path).iter_errors(record),
                    key=lambda error: list(error.absolute_path),
                )
                self.assertEqual(
                    errors,
                    [],
                    "\n".join(error.message for error in errors),
                )

    def test_review_schemas_capture_manifest_finding_and_verification_contracts(self):
        finding_schema = json.loads(
            (SCHEMAS / "review-finding.schema.json").read_text(encoding="utf-8")
        )
        manifest_schema = json.loads(
            (SCHEMAS / "review-manifest.schema.json").read_text(encoding="utf-8")
        )

        self.assertIn("fingerprint", finding_schema["required"])
        self.assertEqual(
            finding_schema["properties"]["classification"]["enum"],
            ["consensus", "unique"],
        )
        self.assertEqual(
            manifest_schema["properties"]["mode"]["enum"],
            ["single", "swarm", "dual", "dual-swarm"],
        )
        self.assertIn(
            "insufficient_evidence",
            manifest_schema["$defs"]["verificationRecord"]["properties"][
                "fact_status"
            ]["enum"],
        )
        self.assertIn("completeness", manifest_schema["required"])

        runtime_manifest_schema = json.loads(
            (RUNTIME_SCHEMAS / "review-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        group_state_schema = json.loads(
            (RUNTIME_SCHEMAS / "review-group-state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            runtime_manifest_schema["$ref"],
            "../references/schemas/review-manifest.schema.json",
        )
        self.assertIn("review_plan", group_state_schema["required"])
        self.assertIn("manifest_path", group_state_schema["properties"])


if __name__ == "__main__":
    unittest.main()
