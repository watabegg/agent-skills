from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
except ImportError:  # pragma: no cover - minimal skill installations
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.review import FindingCandidate, NormalizedFinding, normalize_findings  # noqa: E402
from hloop_lib.review_policy import (  # noqa: E402
    FindingDisposition,
    FollowUpIssueKey,
    FollowUpRelation,
    ReviewPolicyError,
    build_follow_up_issue_key,
    deduplicate_follow_up_keys,
    follow_up_issue_key,
    is_safety_critical_finding,
    issue_key_components,
    same_follow_up_issue,
    validate_disposition,
)


def disposition(**overrides) -> FindingDisposition:
    values = {
        "fact_status": "confirmed",
        "origin": "unrelated-pre-existing",
        "contract_relation": "outside_release",
        "decision_requirement": "none",
        "severity": "P2",
        "disposition": "defer_follow_up",
        "release_effect": "non_blocking",
    }
    values.update(overrides)
    return FindingDisposition(**values)


class FindingDispositionTests(unittest.TestCase):
    def test_independent_axes_round_trip_without_collapsing_origin_or_scope(self):
        original = disposition(
            fact_status="confirmed",
            origin="unrelated-pre-existing",
            contract_relation="outside_release",
            decision_requirement="spec",
            severity="P1",
            disposition="defer_follow_up",
            release_effect="non_blocking",
            requirement_refs=("REQ-004",),
            why_fix_now="",
        )

        self.assertEqual(FindingDisposition.from_record(original.to_record()), original)
        self.assertEqual(original.origin, "unrelated-pre-existing")
        self.assertEqual(original.contract_relation, "outside_release")
        self.assertEqual(original.decision_requirement, "spec")
        validate_disposition(original)

    def test_each_axis_rejects_unknown_values(self):
        axes = {
            "fact_status": "needs_spec",
            "origin": "scope-expanding",
            "contract_relation": "maybe",
            "decision_requirement": "manager",
            "severity": "P4",
            "disposition": "ignore",
            "release_effect": "later",
        }
        for field, value in axes.items():
            with self.subTest(field=field):
                with self.assertRaises(ReviewPolicyError):
                    disposition(**{field: value})

    def test_refuted_candidate_must_be_discarded(self):
        validate_disposition(
            disposition(
                fact_status="refuted",
                disposition="discard",
                release_effect="non_blocking",
            )
        )
        with self.assertRaisesRegex(ReviewPolicyError, "refuted"):
            disposition(
                fact_status="refuted",
                disposition="defer_follow_up",
                release_effect="non_blocking",
            )

    def test_in_scope_regressions_cannot_be_deferred(self):
        with self.assertRaisesRegex(ReviewPolicyError, "introduced or diff-expanded"):
            disposition(
                fact_status="confirmed",
                origin="introduced",
                contract_relation="in_scope",
                severity="P2",
                disposition="defer_follow_up",
                release_effect="non_blocking",
            )

    def test_in_scope_p1_requires_fix_or_explicit_blocking_action(self):
        with self.assertRaisesRegex(ReviewPolicyError, "P0/P1"):
            validate_disposition(
                disposition(
                    origin="diff-expanded-pre-existing",
                    contract_relation="in_scope",
                    severity="P1",
                    disposition="accepted_risk",
                    release_effect="non_blocking",
                ),
                accepted_risk_authorized=True,
            )
        validate_disposition(
            disposition(
                origin="diff-expanded-pre-existing",
                contract_relation="in_scope",
                severity="P1",
                disposition="fix_now",
                release_effect="blocking",
                requirement_refs=("REQ-002",),
            )
        )

    def test_insufficient_in_scope_evidence_requires_user_decision(self):
        candidate = disposition(
            fact_status="insufficient_evidence",
            origin="unknown",
            contract_relation="in_scope",
            decision_requirement="user",
            disposition="defer_follow_up",
            release_effect="non_blocking",
        )
        with self.assertRaisesRegex(ReviewPolicyError, "user_decision"):
            validate_disposition(candidate)

        validate_disposition(
            disposition(
                fact_status="insufficient_evidence",
                origin="unknown",
                contract_relation="in_scope",
                decision_requirement="user",
                disposition="user_decision",
                release_effect="blocking",
            )
        )

    def test_spec_decision_can_be_deferred_only_when_acceptance_is_preserved(self):
        candidate = disposition(
            fact_status="confirmed",
            origin="unrelated-pre-existing",
            contract_relation="in_scope",
            decision_requirement="spec",
            disposition="defer_follow_up",
            release_effect="non_blocking",
        )
        with self.assertRaises(ReviewPolicyError):
            validate_disposition(candidate)
        validate_disposition(
            candidate,
            acceptance_can_be_met_without_decision=True,
        )

    def test_security_or_data_loss_cannot_be_silently_deferred(self):
        candidate = disposition(
            fact_status="confirmed",
            origin="unrelated-pre-existing",
            contract_relation="outside_release",
            severity="P1",
            disposition="defer_follow_up",
            release_effect="non_blocking",
        )
        with self.assertRaisesRegex(ReviewPolicyError, "security or data-loss"):
            validate_disposition(candidate, safety_critical=True)

    def test_safety_critical_finding_detection_is_conservative(self):
        self.assertTrue(is_safety_critical_finding(severity="P0"))
        self.assertTrue(
            is_safety_critical_finding(
                severity="P1", product_impact="the path can cause data loss"
            )
        )
        self.assertTrue(
            is_safety_critical_finding(
                severity="P1", trigger="an authorization bypass is reachable"
            )
        )
        self.assertFalse(
            is_safety_critical_finding(
                severity="P1", product_impact="a slow but recoverable response"
            )
        )
        self.assertFalse(
            is_safety_critical_finding(
                severity="P2", product_impact="the path can cause data loss"
            )
        )


class RuntimeFindingAxisTests(unittest.TestCase):
    def candidate(self, **overrides) -> FindingCandidate:
        values = {
            "finding_id": "FND-001",
            "provider": "codex",
            "head_sha": "a" * 40,
            "discovering_agent": "codex-discovery-01",
            "severity": "P2",
            "confidence": 0.9,
            "title": "runtime finding",
            "file_path": "src/runtime.py",
            "line": 10,
            "symbol": "run",
            "trigger": "runtime trigger",
            "product_impact": "runtime impact",
            "origin": "unrelated-pre-existing",
            "proposed_fix": "runtime fix",
            "fact_status": "confirmed",
            "contract_relation": "outside_release",
            "decision_requirement": "spec",
            "disposition": "defer_follow_up",
            "release_effect": "non_blocking",
            "requirement_refs": ("REQ-001",),
            "why_fix_now": "",
        }
        values.update(overrides)
        return FindingCandidate(**values)

    def test_candidate_and_normalized_finding_round_trip_all_policy_axes(self):
        candidate = self.candidate()
        restored_candidate = FindingCandidate.from_record(
            json.loads(json.dumps(candidate.to_record()))
        )
        self.assertEqual(restored_candidate, candidate)
        normalized = normalize_findings((candidate,))[0]
        restored = NormalizedFinding.from_record(
            json.loads(json.dumps(normalized.to_record()))
        )
        self.assertEqual(restored, normalized)
        for record in (candidate.to_record(), normalized.to_record()):
            self.assertEqual(record["fact_status"], "confirmed")
            self.assertEqual(record["origin"], "unrelated-pre-existing")
            self.assertEqual(record["contract_relation"], "outside_release")
            self.assertEqual(record["decision_requirement"], "spec")
            self.assertEqual(record["disposition"], "defer_follow_up")
            self.assertEqual(record["release_effect"], "non_blocking")
        self.assertFalse(normalized.is_actionable)
        self.assertFalse(normalized.is_release_blocking)

    def test_normalized_axes_keep_confirmed_in_scope_blocking_actionable(self):
        normalized = normalize_findings(
            (
                self.candidate(
                    contract_relation="in_scope",
                    decision_requirement="none",
                    disposition="fix_now",
                    release_effect="blocking",
                ),
            )
        )[0]
        self.assertTrue(normalized.is_actionable)
        self.assertTrue(normalized.is_release_blocking)


class FollowUpIssueKeyTests(unittest.TestCase):
    def test_key_is_stable_across_evidence_only_changes(self):
        # These values intentionally vary but are not inputs to the key API.
        evidence_variants = (
            {"suggested_fix": "patch parser", "severity": "P1", "target_sha": "aaa", "line": 10},
            {"suggested_fix": "rewrite validation", "severity": "P3", "target_sha": "bbb", "line": 200},
        )
        keys = tuple(
            follow_up_issue_key(
                component="Review policy",
                trigger_class="candidate is re-reported",
                product_impact="same follow-up is created twice",
            )
            for _variant in evidence_variants
        )
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("fu:v1:sha256:"))
        self.assertEqual(issue_key_components(keys[0])["version"], 1)

    def test_key_normalizes_identity_and_root_cause_controls_provisional_state(self):
        first = build_follow_up_issue_key(
            component="  Review\\Policy ",
            trigger_class="Moved   symbol",
            product_impact="Data LOSS",
        )
        equivalent = build_follow_up_issue_key(
            component="review/policy",
            trigger_class="moved symbol",
            product_impact="data loss",
        )
        self.assertEqual(first.key, equivalent.key)
        self.assertTrue(first.provisional)

        resolved = build_follow_up_issue_key(
            component="review/policy",
            trigger_class="moved symbol",
            product_impact="data loss",
            root_cause="unsafe merge",
        )
        self.assertFalse(resolved.provisional)
        self.assertNotEqual(first.key, resolved.key)
        self.assertEqual(
            FollowUpIssueKey.from_record(resolved.to_record()),
            resolved,
        )

    def test_key_changes_only_when_semantic_identity_changes(self):
        base = build_follow_up_issue_key(
            component="review/policy",
            trigger_class="missing evidence",
            product_impact="unsafe deferral",
            root_cause="policy bypass",
        )
        self.assertNotEqual(
            base.key,
            build_follow_up_issue_key(
                component="review/policy",
                trigger_class="missing evidence",
                product_impact="unsafe deferral",
                root_cause="different cause",
            ).key,
        )
        self.assertNotEqual(
            base.key,
            build_follow_up_issue_key(
                component="review/triage",
                trigger_class="missing evidence",
                product_impact="unsafe deferral",
                root_cause="policy bypass",
            ).key,
        )

    def test_alias_duplicate_and_supersedes_relations_are_explicit(self):
        first = build_follow_up_issue_key(
            component="a", trigger_class="b", product_impact="c"
        )
        second = build_follow_up_issue_key(
            component="a", trigger_class="b", product_impact="d"
        )
        relation = FollowUpRelation(
            relation="duplicate_of",
            source_issue_key=second.key,
            target_issue_key=first.key,
        )
        self.assertEqual(FollowUpRelation.from_record(relation.to_record()), relation)
        self.assertTrue(same_follow_up_issue(first, first.key))
        self.assertEqual(deduplicate_follow_up_keys((first, first.key, second)), (first.key, second.key))
        with self.assertRaisesRegex(ReviewPolicyError, "cannot target itself"):
            FollowUpRelation(relation="alias", target_issue_key=first.key, source_issue_key=first.key)


@unittest.skipUnless(jsonschema is not None, "jsonschema is optional")
class SchemaTests(unittest.TestCase):
    def validator(self, name: str):
        schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        return jsonschema.Draft202012Validator(schema)

    def test_disposition_schema_matches_policy_record_and_rejects_regression_deferral(self):
        validator = self.validator("review-disposition.schema.json")
        valid = disposition(
            origin="introduced",
            contract_relation="in_scope",
            severity="P1",
            disposition="fix_now",
            release_effect="blocking",
        ).to_record()
        self.assertTrue(validator.is_valid(valid))

        invalid = dict(valid)
        invalid.update({"disposition": "defer_follow_up", "release_effect": "non_blocking"})
        self.assertFalse(validator.is_valid(invalid))

    def test_follow_up_schema_accepts_provisional_first_class_record(self):
        validator = self.validator("follow-up.schema.json")
        key = build_follow_up_issue_key(
            component="review/policy",
            trigger_class="ambiguous evidence",
            product_impact="follow-up duplication",
        )
        record = {
            "id": "F001",
            "title": "Duplicate follow-up candidate",
            "status": "open",
            **key.to_record(),
            "source_review_fingerprints": ["sha256:" + "a" * 64],
            "discovered_head": "abc123",
            "evidence": ["review/R001.md#FND-001"],
            "impact": "The same issue can be registered twice.",
            "affected_path": "skills/herdr-dev-loop/scripts/hloop_lib/review_policy.py",
            "fact_status": "confirmed",
            "severity": "P2",
            "origin": "unrelated-pre-existing",
            "contract_relation": "outside_release",
            "decision_requirement": "none",
            "release_effect": "non_blocking",
            "requirement_refs": [],
            "recommended_action": "defer_follow_up",
            "deferred_reason": "Outside the current release contract.",
            "reconsider_condition": "When follow-up storage is integrated.",
            "created_at": "2026-07-16T00:00:00+00:00",
            "updated_at": "2026-07-16T00:00:00+00:00",
        }
        self.assertTrue(validator.is_valid(record), validator.iter_errors(record))


if __name__ == "__main__":
    unittest.main()
