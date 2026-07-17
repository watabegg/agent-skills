from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional for minimal skill installs
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.worker_candidate import (  # noqa: E402
    CANDIDATE_SEAL_FIELDS,
    DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK,
    IMPLEMENTATION_CANDIDATE_FIELDS,
    PATCH_REVIEW_FIELDS,
    CandidateSeal,
    ImplementationCandidate,
    PatchReview,
    WorkerCandidateError,
    canonical_digest,
    evaluate_patch_review_rounds,
    patch_review_staleness,
    record_patch_review,
    require_fresh_patch_review,
    seal_candidate,
    validate_candidate_seal,
    validate_final_result_authenticity,
)


def digest(label: str) -> str:
    return canonical_digest({"label": label})


def candidate(
    *,
    mode: str = "commit",
    revision: int = 1,
    tree: str | None = None,
    contract_digest: str | None = None,
    ack_event_id: str = "ack-event-001",
) -> ImplementationCandidate:
    return ImplementationCandidate(
        run_id="run-v053",
        skill_version="0.5.3",
        task_id="T006",
        attempt_id="T006-A001",
        task_contract_digest=contract_digest or digest("task-contract-v1"),
        semantic_ack_event_id=ack_event_id,
        base_sha="a" * 40,
        candidate_revision=revision,
        completion_mode=mode,
        candidate_tree_sha=tree or ("b" * 40),
        candidate_artifact_ref=(
            f"implementation-candidates/T006/T006-A001/{revision}.json"
        ),
        changed_files=("src/worker.py", "tests/test_worker.py"),
        validation_commands=("python3 -m unittest tests.test_worker",),
        validation_results=("passed",),
        validation_summary="targeted Worker tests passed",
        invariant_evidence=("semantic ACK identity remains unchanged",),
        regression_evidence=("legacy handoff test passed",),
        self_review_summary="diff, error paths, and write scope reviewed",
        residual_risks=(),
        unrun_checks=("Manager integration validation remains pending",),
    )


def sealed(
    current: ImplementationCandidate,
    *,
    commit: str | None = None,
    artifact_digest: str | None = None,
) -> CandidateSeal:
    return seal_candidate(
        current,
        candidate_sha=commit or ("c" * 40),
        candidate_artifact_digest=(
            artifact_digest or current.canonical_artifact_digest
        ),
        observed_tree_sha=current.candidate_tree_sha,
        active_attempt_id=current.attempt_id,
        active_task_contract_digest=current.task_contract_digest,
        approved_ack_event_id=current.semantic_ack_event_id,
    )


def review(
    current: CandidateSeal,
    *,
    attempt: str = "PR-T006-A001",
    round_number: int = 1,
    verdict: str = "passed",
    unresolved: tuple[str, ...] = (),
    follow_ups: tuple[str, ...] = (),
) -> PatchReview:
    return record_patch_review(
        current,
        review_attempt_id=attempt,
        review_round=round_number,
        reviewer_provider="codex",
        reviewer_model="gpt-5.6-sol",
        reviewer_effort="xhigh",
        verdict=verdict,
        unresolved_finding_fingerprints=unresolved,
        follow_up_finding_fingerprints=follow_ups,
    )


def final_result(current: ImplementationCandidate, current_seal: CandidateSeal) -> dict:
    return {
        "task_id": current.task_id,
        "run_id": current.run_id,
        "skill_version": current.skill_version,
        "contract_schema_revision": 3,
        "attempt_id": current.attempt_id,
        "status": "done",
        "merge_ready": True,
        "branch": "ai/v053/T006",
        "head_sha": "f" * 40,
        "base_sha": current.base_sha,
        "changed_files": list(current.changed_files),
        "validation_recorded": True,
        "validation_commands": [
            *current.validation_commands,
            "python3 -m unittest skills.herdr-dev-loop.tests.test_worker_candidate_v053",
        ],
        "validation_results": ["passed", "passed"],
        "validation_summary": "candidate and final gate validation passed",
        "blocking_questions": [],
        "handoff": current.completion_mode == "handoff",
        "invariant_evidence": list(current.invariant_evidence),
        "regression_evidence": list(current.regression_evidence),
        "self_review_summary": current.self_review_summary,
        "residual_risks": list(current.residual_risks),
        "unrun_checks": list(current.unrun_checks),
    }


class ImplementationCandidateTests(unittest.TestCase):
    def test_worker_submit_is_nonterminal_and_binds_required_evidence(self):
        current = candidate()
        record = current.to_record()

        self.assertEqual(record["lifecycle_status"], "implementation_complete")
        self.assertEqual(record["candidate_tree_sha"], "b" * 40)
        self.assertFalse(record["merge_ready"])
        self.assertFalse(record["terminal_result_emitted"])
        self.assertFalse(record["completion_sentinel_emitted"])
        self.assertTrue(record["invariant_evidence"])
        self.assertTrue(record["regression_evidence"])
        self.assertTrue(record["self_review_summary"])
        self.assertIn("residual_risks", record)
        self.assertIn("unrun_checks", record)
        self.assertEqual(ImplementationCandidate.from_record(record), current)

    def test_submit_rejects_missing_evidence_failed_validation_and_unknown_fields(self):
        with self.assertRaisesRegex(WorkerCandidateError, "invariant_evidence"):
            replace(candidate(), invariant_evidence=())
        with self.assertRaisesRegex(WorkerCandidateError, "every recorded validation"):
            replace(candidate(), validation_results=("failed",))

        record = candidate().to_record()
        record["terminal_result_path"] = "results/T006/result.md"
        with self.assertRaisesRegex(WorkerCandidateError, "unknown fields"):
            ImplementationCandidate.from_record(record)

    def test_multiple_passing_validation_results_remain_aligned(self):
        current = replace(
            candidate(),
            validation_commands=("python3 -m unittest tests.one", "python3 -m unittest tests.two"),
            validation_results=("passed", "passed"),
        )
        self.assertEqual(current.validation_results, ("passed", "passed"))

    def test_candidate_artifact_path_and_attempt_must_match_identity(self):
        with self.assertRaisesRegex(WorkerCandidateError, "artifact_ref"):
            replace(candidate(), candidate_artifact_ref="implementation-candidates/T006/x.json")
        with self.assertRaisesRegex(WorkerCandidateError, "belong to task"):
            replace(candidate(), attempt_id="T007-A001")


class CandidateSealTests(unittest.TestCase):
    def test_commit_and_handoff_seals_preserve_attempt_ack_and_nonterminal_state(self):
        for mode in ("commit", "handoff"):
            with self.subTest(mode=mode):
                current = candidate(mode=mode)
                current_seal = sealed(current)
                record = current_seal.to_record()

                self.assertEqual(current_seal.attempt_id, current.attempt_id)
                self.assertEqual(
                    current_seal.semantic_ack_event_id,
                    current.semantic_ack_event_id,
                )
                self.assertEqual(current_seal.task_contract_digest, current.task_contract_digest)
                self.assertEqual(record["completion_mode"], mode)
                self.assertEqual(record["candidate_sha"], "c" * 40)
                self.assertFalse(record["merge_ready"])
                self.assertFalse(record["manager_validation_recorded"])
                self.assertEqual(CandidateSeal.from_record(record), current_seal)
                validate_candidate_seal(
                    current,
                    current_seal,
                    candidate_artifact_digest=current.canonical_artifact_digest,
                )

    def test_seal_rejects_stale_attempt_contract_ack_tree_and_artifact(self):
        current = candidate()
        common = {
            "candidate_sha": "c" * 40,
            "candidate_artifact_digest": current.canonical_artifact_digest,
            "observed_tree_sha": current.candidate_tree_sha,
            "active_attempt_id": current.attempt_id,
            "active_task_contract_digest": current.task_contract_digest,
            "approved_ack_event_id": current.semantic_ack_event_id,
        }
        cases = (
            ("active_attempt_id", "T006-A002", "active attempt"),
            ("active_task_contract_digest", digest("changed"), "contract is stale"),
            ("approved_ack_event_id", "different-ack", "approved ACK"),
            ("observed_tree_sha", "d" * 40, "tree does not match"),
        )
        for field_name, value, message in cases:
            with self.subTest(field=field_name):
                arguments = dict(common)
                arguments[field_name] = value
                with self.assertRaisesRegex(WorkerCandidateError, message):
                    seal_candidate(current, **arguments)

        current_seal = sealed(current)
        with self.assertRaisesRegex(WorkerCandidateError, "artifact digest"):
            validate_candidate_seal(
                current,
                current_seal,
                candidate_artifact_digest=digest("different-artifact"),
            )


class PatchReviewIdentityTests(unittest.TestCase):
    def test_review_is_bound_to_every_candidate_and_contract_identity_axis(self):
        first = candidate()
        first_seal = sealed(first)
        recorded = review(first_seal)

        self.assertEqual(recorded.task_id, first.task_id)
        self.assertEqual(recorded.attempt_id, first.attempt_id)
        self.assertEqual(recorded.task_contract_digest, first.task_contract_digest)
        self.assertEqual(recorded.base_sha, first.base_sha)
        self.assertEqual(recorded.candidate_sha, first_seal.candidate_sha)
        self.assertEqual(
            recorded.candidate_artifact_digest,
            first_seal.candidate_artifact_digest,
        )
        self.assertEqual(PatchReview.from_record(recorded.to_record()), recorded)
        self.assertEqual(
            require_fresh_patch_review(
                recorded,
                first_seal,
                current_task_contract_digest=first.task_contract_digest,
            ),
            recorded,
        )

    def test_new_candidate_or_contract_stales_prior_review(self):
        first = candidate()
        first_seal = sealed(first)
        old_review = review(first_seal)
        second = candidate(revision=2, tree="d" * 40)
        second_seal = sealed(second, commit="e" * 40)

        candidate_reasons = patch_review_staleness(
            old_review,
            second_seal,
            current_task_contract_digest=second.task_contract_digest,
        )
        self.assertIn("candidate_revision-changed", candidate_reasons)
        self.assertIn("candidate_sha-changed", candidate_reasons)
        self.assertIn("candidate_artifact_digest-changed", candidate_reasons)

        contract_reasons = patch_review_staleness(
            old_review,
            first_seal,
            current_task_contract_digest=digest("task-contract-v2"),
        )
        self.assertIn("active-task-contract-changed", contract_reasons)
        with self.assertRaisesRegex(WorkerCandidateError, "stale"):
            require_fresh_patch_review(
                old_review,
                first_seal,
                current_task_contract_digest=digest("task-contract-v2"),
            )

    def test_verdict_consistency_and_no_automatic_scope_expansion(self):
        current_seal = sealed(candidate())
        finding = digest("outside-task-finding")
        passed = review(current_seal, follow_ups=(finding,))
        self.assertEqual(passed.to_record()["automatic_task_ids"], [])

        with self.assertRaisesRegex(WorkerCandidateError, "cannot retain unresolved"):
            review(current_seal, verdict="passed", unresolved=(digest("bug"),))
        with self.assertRaisesRegex(WorkerCandidateError, "requires unresolved"):
            review(current_seal, verdict="fix_required")

        record = passed.to_record()
        record["automatic_task_ids"] = ["T099"]
        with self.assertRaisesRegex(WorkerCandidateError, "cannot automatically"):
            PatchReview.from_record(record)

        with self.assertRaisesRegex(WorkerCandidateError, "artifact filename"):
            review(current_seal, attempt="../../outside")


class PatchReviewRoundTests(unittest.TestCase):
    def test_fix_required_reuses_same_attempt_then_pass_allows_finalize(self):
        first = candidate()
        first_seal = sealed(first)
        finding = digest("task-local-regression")
        first_review = review(
            first_seal,
            verdict="fix_required",
            unresolved=(finding,),
        )
        first_decision = evaluate_patch_review_rounds(
            first_seal,
            [first_review],
            current_task_contract_digest=first.task_contract_digest,
        )
        self.assertEqual(first_decision.action, "patch_fix_running")
        self.assertEqual(first_decision.attempt_id, first.attempt_id)
        self.assertEqual(first_decision.semantic_ack_event_id, first.semantic_ack_event_id)

        second = candidate(revision=2, tree="d" * 40)
        second_seal = sealed(second, commit="e" * 40)
        passed = review(
            second_seal,
            attempt="PR-T006-A002",
            round_number=2,
        )
        final_decision = evaluate_patch_review_rounds(
            second_seal,
            [first_review, passed],
            current_task_contract_digest=second.task_contract_digest,
        )
        self.assertEqual(final_decision.action, "finalize_allowed")
        self.assertEqual(final_decision.rounds_used, 2)
        self.assertIn(first_review.review_attempt_id, final_decision.stale_review_attempt_ids)

    def test_round_limit_stops_with_unresolved_findings_and_last_sha(self):
        finding_one = digest("regression-one")
        finding_two = digest("regression-two")
        first = candidate()
        first_seal = sealed(first)
        first_review = review(
            first_seal,
            verdict="fix_required",
            unresolved=(finding_one,),
        )
        second = candidate(revision=2, tree="d" * 40)
        second_seal = sealed(second, commit="e" * 40)
        second_review = review(
            second_seal,
            attempt="PR-T006-A002",
            round_number=2,
            verdict="fix_required",
            unresolved=(finding_two,),
        )

        decision = evaluate_patch_review_rounds(
            second_seal,
            [first_review, second_review],
            current_task_contract_digest=second.task_contract_digest,
            max_rounds=DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK,
        )

        self.assertEqual(decision.action, "user_decision_required")
        self.assertTrue(decision.requires_user_decision)
        self.assertEqual(decision.unresolved_finding_fingerprints, (finding_two,))
        self.assertEqual(decision.last_candidate_sha, second_seal.candidate_sha)
        self.assertEqual(decision.automatic_task_ids, ())

    def test_idempotent_review_record_does_not_double_count_or_hide_conflict(self):
        current = candidate()
        current_seal = sealed(current)
        finding = digest("regression")
        recorded = review(
            current_seal,
            verdict="fix_required",
            unresolved=(finding,),
        )
        decision = evaluate_patch_review_rounds(
            current_seal,
            [recorded, recorded],
            current_task_contract_digest=current.task_contract_digest,
        )
        self.assertEqual(decision.rounds_used, 1)
        self.assertEqual(decision.action, "patch_fix_running")

        conflicting = replace(recorded, follow_up_finding_fingerprints=(digest("extra"),))
        with self.assertRaisesRegex(WorkerCandidateError, "reused with different"):
            evaluate_patch_review_rounds(
                current_seal,
                [recorded, conflicting],
                current_task_contract_digest=current.task_contract_digest,
            )

    def test_contract_change_requires_candidate_resubmission(self):
        current = candidate()
        current_seal = sealed(current)
        passed = review(current_seal)
        decision = evaluate_patch_review_rounds(
            current_seal,
            [passed],
            current_task_contract_digest=digest("task-contract-v2"),
        )
        self.assertEqual(decision.action, "candidate_resubmission_required")
        self.assertIn(passed.review_attempt_id, decision.stale_review_attempt_ids)


class FinalResultAuthenticityTests(unittest.TestCase):
    def test_fresh_passed_review_and_projected_evidence_authorize_result(self):
        current = candidate()
        current_seal = sealed(current)
        passed = review(current_seal)

        validate_final_result_authenticity(
            current,
            current_seal,
            passed,
            final_result(current, current_seal),
            candidate_artifact_digest=current.canonical_artifact_digest,
            current_task_contract_digest=current.task_contract_digest,
            result_parent_candidate_sha=current_seal.candidate_sha,
        )

    def test_changed_evidence_sha_or_review_fails_final_authenticity(self):
        current = candidate()
        current_seal = sealed(current)
        passed = review(current_seal)
        result = final_result(current, current_seal)
        result["invariant_evidence"] = ["different evidence"]
        with self.assertRaisesRegex(WorkerCandidateError, "invariant_evidence"):
            validate_final_result_authenticity(
                current,
                current_seal,
                passed,
                result,
                candidate_artifact_digest=current.canonical_artifact_digest,
                current_task_contract_digest=current.task_contract_digest,
                result_parent_candidate_sha=current_seal.candidate_sha,
            )

        result = final_result(current, current_seal)
        with self.assertRaisesRegex(WorkerCandidateError, "reviewed candidate SHA"):
            validate_final_result_authenticity(
                current,
                current_seal,
                passed,
                result,
                candidate_artifact_digest=current.canonical_artifact_digest,
                current_task_contract_digest=current.task_contract_digest,
                result_parent_candidate_sha="e" * 40,
            )

        fix_required = review(
            current_seal,
            verdict="fix_required",
            unresolved=(digest("unresolved"),),
        )
        with self.assertRaisesRegex(WorkerCandidateError, "unresolved"):
            validate_final_result_authenticity(
                current,
                current_seal,
                fix_required,
                final_result(current, current_seal),
                candidate_artifact_digest=current.canonical_artifact_digest,
                current_task_contract_digest=current.task_contract_digest,
                result_parent_candidate_sha=current_seal.candidate_sha,
            )


@unittest.skipUnless(jsonschema is not None, "jsonschema is optional")
class WorkerCandidateSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate_schema = json.loads(
            (SCHEMAS / "implementation-candidate.schema.json").read_text(
                encoding="utf-8"
            )
        )
        cls.review_schema = json.loads(
            (SCHEMAS / "patch-review.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(cls.candidate_schema)
        jsonschema.Draft202012Validator.check_schema(cls.review_schema)
        cls.candidate_validator = jsonschema.Draft202012Validator(cls.candidate_schema)
        cls.review_validator = jsonschema.Draft202012Validator(cls.review_schema)

    def test_python_and_schema_candidate_property_sets_match(self):
        definitions = self.candidate_schema["$defs"]
        self.assertEqual(
            IMPLEMENTATION_CANDIDATE_FIELDS,
            frozenset(definitions["implementationCandidate"]["properties"]),
        )
        self.assertEqual(
            CANDIDATE_SEAL_FIELDS,
            frozenset(definitions["candidateSeal"]["properties"]),
        )
        self.assertEqual(PATCH_REVIEW_FIELDS, frozenset(self.review_schema["properties"]))

    def test_candidate_and_both_seal_modes_are_schema_valid(self):
        for mode in ("commit", "handoff"):
            with self.subTest(mode=mode):
                current = candidate(mode=mode)
                self.candidate_validator.validate(current.to_record())
                self.candidate_validator.validate(sealed(current).to_record())

    def test_schema_rejects_terminal_or_unvalidated_candidate_and_extra_field(self):
        for field_name, value in (
            ("merge_ready", True),
            ("terminal_result_emitted", True),
            ("validation_results", ["failed"]),
        ):
            with self.subTest(field=field_name):
                record = candidate().to_record()
                record[field_name] = value
                self.assertFalse(self.candidate_validator.is_valid(record))
        record = candidate().to_record()
        record["terminal_result_path"] = "results/T006/result.md"
        self.assertFalse(self.candidate_validator.is_valid(record))

    def test_patch_review_schema_binds_identity_and_verdict_shape(self):
        current = candidate()
        current_seal = sealed(current)
        passed = review(current_seal)
        self.review_validator.validate(passed.to_record())

        fix_required = review(
            current_seal,
            verdict="fix_required",
            unresolved=(digest("regression"),),
        )
        self.review_validator.validate(fix_required.to_record())

        invalid = fix_required.to_record()
        invalid["candidate_artifact_digest"] = "not-a-digest"
        self.assertFalse(self.review_validator.is_valid(invalid))
        invalid = fix_required.to_record()
        invalid["automatic_task_ids"] = ["T099"]
        self.assertFalse(self.review_validator.is_valid(invalid))
        invalid = fix_required.to_record()
        invalid["unresolved_finding_fingerprints"] = []
        self.assertFalse(self.review_validator.is_valid(invalid))


if __name__ == "__main__":
    unittest.main()
