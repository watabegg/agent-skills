from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional for skill consumers
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.reports import (  # noqa: E402
    OutcomeGate,
    OutcomeModelError,
    OutcomeReport,
    blocked_outcome,
    draft_outcome,
    final_outcome,
    render_outcome_markdown,
)
from hloop_lib.requirements import (  # noqa: E402
    CheckpointPolicyError,
    EvidenceRef,
    InputRecord,
    ProgressEvidenceError,
    ProgressSnapshot,
    Requirement,
    RequirementLedger,
    RequirementModelError,
    RequirementProgress,
    assert_checkpoint_inclusion_allowed,
    checkpoint_inclusion_allowed,
    transition_progress,
)


NOW = "2026-07-15T00:00:00+00:00"
HEAD_SHA = "a" * 40


def requirement(number: int, *, supersedes=()) -> Requirement:
    return Requirement(
        requirement_id=f"REQ-{number:03d}",
        source_inputs=("U0001",),
        acceptance=(f"requirement {number} is observable",),
        priority="P1",
        accepted_at=NOW,
        supersedes=tuple(supersedes),
    )


def verified_evidence(head_sha: str = HEAD_SHA) -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(
            kind="artifact",
            reference="results/T005/result.md",
            verified_by="hloop",
            head_sha=head_sha,
        ),
        EvidenceRef(
            kind="test",
            reference="python3 -m unittest test_requirements_v05.py",
            verified_by="hloop",
            head_sha=head_sha,
            result="passed",
        ),
    )


def verified_progress() -> RequirementProgress:
    return RequirementProgress(
        requirement_id="REQ-002",
        status="verified",
        task_ids=("T005",),
        evidence=verified_evidence(),
    )


def report_kwargs(progress: RequirementProgress, gates=()):
    return {
        "run_id": "run-001",
        "goal": "Implement requirement tracking",
        "generated_at": NOW,
        "requirement_progress": (progress,),
        "gates": tuple(gates),
        "integration_target_sha": "abc123",
        "current_branch_sha": "abc123",
        "next_user_actions": ("Review the outcome",),
    }


class InputRecordTests(unittest.TestCase):
    def test_capture_redacts_credentials_but_digests_original(self):
        original = (
            "Authorization: Basic basic-secret-value\n"
            "api_key=sk-abcdefghijklmnopqrstuvwxyz\n"
            "token=plain-token-value\n"
            "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
        )
        captured = InputRecord.capture(
            input_id="U0001",
            received_at=NOW,
            source="manager-pane",
            raw_input=original,
        )

        self.assertEqual(
            captured.prompt_digest, hashlib.sha256(original.encode()).hexdigest()
        )
        self.assertNotIn("basic-secret-value", captured.raw_input)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", captured.raw_input)
        self.assertNotIn("plain-token-value", captured.raw_input)
        self.assertNotIn("private-material", captured.raw_input)
        self.assertEqual(captured.artifact_class, "local-sensitive")
        self.assertFalse(captured.checkpoint_included)
        self.assertFalse(captured.product_commit_included)

    def test_capture_redacts_environment_uri_and_gitlab_credentials(self):
        original = (
            "AWS_SECRET_ACCESS_KEY=aws-secret-material\n"
            "DEPLOY_TOKEN=deployment-token-material\n"
            "WEBHOOK_SECRET=webhook-secret-material\n"
            "DATABASE_PASSWORD=database-env-password\n"
            "DATABASE_URL=postgres://reporter:database-password@db.example.test/app\n"
            "GITLAB_TOKEN=glpat-abcdefghijklmnopqrstuvwxyz\n"
            "standalone glpat-zyxwvutsrqponmlkjihgfedcba"
        )

        captured = InputRecord.capture(
            input_id="U0001",
            received_at=NOW,
            source="manager-pane",
            raw_input=original,
        )

        for secret in (
            "aws-secret-material",
            "deployment-token-material",
            "webhook-secret-material",
            "database-env-password",
            "database-password",
            "glpat-abcdefghijklmnopqrstuvwxyz",
            "glpat-zyxwvutsrqponmlkjihgfedcba",
        ):
            self.assertNotIn(secret, captured.raw_input)
        self.assertEqual(
            set(captured.redactions),
            {"credential-uri", "environment-credential", "gitlab-token"},
        )

    def test_capture_preserves_ordinary_text_and_noncredential_urls(self):
        ordinary = (
            "A token bucket controls throughput.\n"
            "PUBLIC_KEY=documentation-fingerprint\n"
            "Docs: https://example.test/guide and postgres://db.example.test/app"
        )

        captured = InputRecord.capture(
            input_id="U0001",
            received_at=NOW,
            source="manager-pane",
            raw_input=ordinary,
        )

        self.assertEqual(captured.raw_input, ordinary)
        self.assertEqual(captured.redactions, ())

    def test_direct_record_rejects_unredacted_or_checkpointed_input(self):
        with self.assertRaisesRegex(RequirementModelError, "unredacted"):
            InputRecord(
                input_id="U0001",
                received_at=NOW,
                source="manager-pane",
                prompt_digest="a" * 64,
                raw_input="password=secret-value",
            )
        with self.assertRaises(CheckpointPolicyError):
            InputRecord(
                input_id="U0001",
                received_at=NOW,
                source="manager-pane",
                prompt_digest="a" * 64,
                raw_input="[REDACTED]",
                checkpoint_included=True,
            )

    def test_raw_input_and_inbox_paths_are_never_checkpoint_eligible(self):
        raw_path = ".ai/herdr-dev-loop/loops/demo/inputs/U0001.md"
        inbox_path = ".ai/herdr-dev-loop/loops/demo/inbox/agent-reports/E0001.json"
        self.assertFalse(checkpoint_inclusion_allowed(raw_path))
        self.assertFalse(checkpoint_inclusion_allowed(inbox_path))
        self.assertTrue(
            checkpoint_inclusion_allowed(
                ".ai/herdr-dev-loop/loops/demo/requirements/REQUIREMENTS.md"
            )
        )
        with self.assertRaisesRegex(CheckpointPolicyError, "U0001"):
            assert_checkpoint_inclusion_allowed([raw_path])


class RequirementLedgerTests(unittest.TestCase):
    def test_supersede_preserves_old_requirement_and_links_both_directions(self):
        first = requirement(1)
        ledger = RequirementLedger().accept(first)
        replacement = requirement(2)

        updated = ledger.supersede(("REQ-001",), replacement)

        self.assertEqual(len(updated.requirements), 2)
        self.assertEqual(updated.get("REQ-001").status, "superseded")
        self.assertEqual(updated.get("REQ-001").superseded_by, "REQ-002")
        self.assertEqual(updated.get("REQ-002").supersedes, ("REQ-001",))
        self.assertEqual(ledger.get("REQ-001").status, "accepted")

    def test_duplicate_id_cannot_silently_rewrite_an_accepted_requirement(self):
        ledger = RequirementLedger().accept(requirement(1))
        with self.assertRaisesRegex(RequirementModelError, "silently rewrite"):
            ledger.accept(requirement(1))

    def test_supersede_requires_an_existing_active_requirement(self):
        with self.assertRaisesRegex(RequirementModelError, "unknown superseded"):
            RequirementLedger().accept(requirement(2, supersedes=("REQ-001",)))


class ProgressTests(unittest.TestCase):
    def test_explicit_transition_sequence_and_illegal_skip(self):
        initial = RequirementProgress("REQ-001")
        working = transition_progress(
            initial,
            "in_progress",
            task_ids=("T005",),
            remaining_work="Implement and test",
        )
        implemented = transition_progress(
            working,
            "implemented_unverified",
            remaining_work="Run validation",
        )
        self.assertEqual(implemented.status, "implemented_unverified")
        with self.assertRaisesRegex(RequirementModelError, "illegal"):
            transition_progress(initial, "verified")

    def test_verified_rejects_missing_or_agent_only_evidence(self):
        with self.assertRaisesRegex(ProgressEvidenceError, "requires"):
            RequirementProgress("REQ-001", status="verified")

        agent_assertion = EvidenceRef(
            kind="agent-report", reference="completion E0001", result="passed"
        )
        with self.assertRaisesRegex(ProgressEvidenceError, "requires"):
            RequirementProgress(
                "REQ-001", status="verified", evidence=(agent_assertion,)
            )

    def test_verified_requires_artifact_and_passing_validation_on_the_same_head(self):
        artifact = verified_evidence()[0]
        validation = verified_evidence()[1]

        with self.assertRaisesRegex(ProgressEvidenceError, "passing test/QA"):
            RequirementProgress(
                "REQ-001", status="verified", evidence=(artifact,)
            )
        with self.assertRaisesRegex(ProgressEvidenceError, "artifact.*head SHA"):
            RequirementProgress(
                "REQ-001",
                status="verified",
                evidence=(
                    EvidenceRef(
                        kind="artifact",
                        reference="results/T005/result.md",
                        verified_by="hloop",
                    ),
                    validation,
                ),
            )
        with self.assertRaisesRegex(ProgressEvidenceError, "same target head SHA"):
            RequirementProgress(
                "REQ-001",
                status="verified",
                evidence=(
                    artifact,
                    EvidenceRef(
                        kind="qa",
                        reference="local smoke check",
                        verified_by="manager",
                        head_sha="b" * 40,
                        result="confirmed",
                    ),
                ),
            )
        with self.assertRaisesRegex(ProgressEvidenceError, "requires a result"):
            EvidenceRef(
                kind="test",
                reference="python3 -m unittest test_requirements_v05.py",
                verified_by="hloop",
                head_sha=HEAD_SHA,
            )

    def test_incomplete_verified_record_cannot_reach_final_outcome(self):
        incomplete_record = {
            "requirement_id": "REQ-001",
            "status": "verified",
            "task_ids": ["T005"],
            "evidence": [verified_evidence()[0].to_record()],
            "remaining_work": "",
            "blockers": [],
        }

        with self.assertRaisesRegex(ProgressEvidenceError, "passing test/QA"):
            progress = RequirementProgress.from_record(incomplete_record)
            final_outcome(
                **report_kwargs(progress, gates=(OutcomeGateTests.passing_gate(),))
            )

    def test_hloop_verified_test_evidence_allows_verified_transition(self):
        working = RequirementProgress("REQ-001", status="in_progress")
        implemented = transition_progress(working, "implemented_unverified")
        verified = transition_progress(
            implemented,
            "verified",
            evidence=verified_evidence(),
            remaining_work="",
            blockers=(),
        )
        self.assertEqual(verified.status, "verified")
        self.assertTrue(verified.evidence[1].qualifies_for_verified)

    def test_snapshot_is_requirement_oriented(self):
        snapshot = ProgressSnapshot(
            progress_id="P0001",
            created_at=NOW,
            requirements=(
                verified_progress(),
                RequirementProgress(
                    "REQ-003",
                    status="blocked",
                    blockers=("D001",),
                    remaining_work="Wait for a user decision",
                ),
            ),
        )
        self.assertIn("2要件中1件を検証済み", snapshot.summary())
        self.assertEqual(snapshot.counts()["blocked"], 1)
        self.assertEqual(ProgressSnapshot.from_record(snapshot.to_record()), snapshot)


class OutcomeGateTests(unittest.TestCase):
    @staticmethod
    def passing_gate() -> OutcomeGate:
        return OutcomeGate(
            name="validation",
            status="passed",
            evidence_refs=("tests/test_requirements_v05.py",),
            target_sha="abc123",
            verified_by="hloop",
        )

    def test_draft_can_describe_pending_state_without_finalizing(self):
        progress = RequirementProgress(
            "REQ-001", status="in_progress", remaining_work="Run validation"
        )
        draft = draft_outcome(
            **report_kwargs(
                progress,
                gates=(OutcomeGate(name="validation", status="pending"),),
            )
        )
        self.assertEqual(draft.kind, "DRAFT")
        self.assertFalse(draft.finalized)

    def test_final_requires_terminal_requirements_and_verified_passing_gates(self):
        with self.assertRaisesRegex(OutcomeModelError, "non-passing gates"):
            final_outcome(
                **report_kwargs(
                    verified_progress(),
                    gates=(OutcomeGate(name="validation", status="pending"),),
                )
            )

        final = final_outcome(
            **report_kwargs(verified_progress(), gates=(self.passing_gate(),))
        )
        self.assertEqual(final.kind, "FINAL")
        self.assertTrue(final.finalized)
        self.assertEqual(OutcomeReport.from_record(final.to_record()), final)
        rendered = render_outcome_markdown(final)
        self.assertIn("# Final Outcome", rendered)
        self.assertIn("REQ-002", rendered)
        self.assertIn("## Validation and QA", rendered)

    def test_final_rejects_target_drift(self):
        kwargs = report_kwargs(verified_progress(), gates=(self.passing_gate(),))
        kwargs["current_branch_sha"] = "advanced-head"
        with self.assertRaisesRegex(OutcomeModelError, "does not match"):
            final_outcome(**kwargs)

        stale_gate = OutcomeGate(
            name="validation",
            status="passed",
            evidence_refs=("old test log",),
            target_sha="old-head",
            verified_by="hloop",
        )
        with self.assertRaisesRegex(OutcomeModelError, "different target SHA"):
            final_outcome(
                **report_kwargs(verified_progress(), gates=(stale_gate,))
            )

    def test_blocked_requires_external_goal_authorization_and_blocked_gate(self):
        progress = RequirementProgress(
            "REQ-001", status="blocked", blockers=("user response",)
        )
        gate = OutcomeGate(name="external-goal", status="blocked")
        kwargs = report_kwargs(progress, gates=(gate,))
        kwargs["blocking_reason"] = "The same external decision blocks all work"

        with self.assertRaisesRegex(OutcomeModelError, "authorization"):
            blocked_outcome(external_goal_blocked=False, **kwargs)

        blocked = blocked_outcome(external_goal_blocked=True, **kwargs)
        self.assertEqual(blocked.kind, "BLOCKED")
        self.assertTrue(blocked.finalized)


class SchemaContractTests(unittest.TestCase):
    def test_all_v05_schemas_are_valid_json_and_pin_terminal_constraints(self):
        schemas = {
            path.name: json.loads(path.read_text())
            for path in (
                SCHEMAS / "input.schema.json",
                SCHEMAS / "requirement.schema.json",
                SCHEMAS / "progress.schema.json",
                SCHEMAS / "outcome.schema.json",
            )
        }
        self.assertEqual(
            schemas["input.schema.json"]["properties"]["checkpoint_included"]["const"],
            False,
        )
        self.assertIn("verified", json.dumps(schemas["progress.schema.json"]))
        self.assertEqual(
            schemas["outcome.schema.json"]["properties"]["kind"]["enum"],
            ["DRAFT", "FINAL", "BLOCKED"],
        )

    @unittest.skipUnless(jsonschema is not None, "jsonschema is not installed")
    def test_progress_schema_rejects_incomplete_verified_evidence(self):
        schema = json.loads((SCHEMAS / "progress.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        valid = ProgressSnapshot(
            progress_id="P0001",
            created_at=NOW,
            requirements=(verified_progress(),),
        ).to_record()
        self.assertTrue(validator.is_valid(valid))

        artifact_only = copy.deepcopy(valid)
        artifact_only["requirements"][0]["evidence"] = [
            verified_evidence()[0].to_record()
        ]
        self.assertFalse(validator.is_valid(artifact_only))

        missing_head = copy.deepcopy(valid)
        missing_head["requirements"][0]["evidence"][0]["head_sha"] = ""
        self.assertFalse(validator.is_valid(missing_head))

        missing_result = copy.deepcopy(valid)
        missing_result["requirements"][0]["evidence"][1]["result"] = ""
        self.assertFalse(validator.is_valid(missing_result))


if __name__ == "__main__":
    unittest.main()
