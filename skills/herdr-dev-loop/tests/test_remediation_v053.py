from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # pragma: no cover - minimal installs fail explicitly
    jsonschema = None
    Registry = None
    Resource = None
    DRAFT202012 = None


SKILL_ROOT = Path(__file__).parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
REFERENCE_SCHEMA = SKILL_ROOT / "references" / "schemas" / "remediation-ledger.schema.json"
PUBLIC_SCHEMA = SKILL_ROOT / "schemas" / "remediation-ledger.schema.json"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.release_scope import ReleaseScope  # noqa: E402
from hloop_lib.remediation import (  # noqa: E402
    AcceptedRiskAuthorization,
    CandidateClassificationConflict,
    CandidateObservation,
    ExtraRoundAuthorization,
    RemediationApprovalConflict,
    RemediationLedger,
    RemediationLedgerError,
    RemediationRoundLimitExceeded,
    TaskMaterializationObservation,
    approve_remediation_batch,
    canonical_digest,
    complete_remediation_batch,
    create_remediation_batch,
    deterministic_task_ids,
    mark_ready_to_triage,
    reconcile_materialization,
    register_candidate,
)
from hloop_lib.review_policy import FindingDisposition  # noqa: E402


NOW = "2026-07-17T00:00:00+00:00"
TARGET_SHA = "target-remediation-v053"
EXTRA_INPUT_DIGEST = canonical_digest({"input": "extra remediation round"})


def captured_input(input_id: str) -> dict[str, dict[str, str]]:
    return {
        input_id: {
            "source": "manager-chat",
            "prompt_digest": EXTRA_INPUT_DIGEST.removeprefix("sha256:"),
        }
    }


def locked_scope() -> ReleaseScope:
    return ReleaseScope.lock(
        source_contents={
            "MISSION.md": "Implement remediation safety.\n",
            "PLAN.md": "P002: crash-safe remediation ledger.\n",
        },
        locked_at=NOW,
        plan_item_refs=("P002",),
        requirement_refs=("REQ-003",),
        release_scope_refs=("runtime-release",),
        accepted_requirement_refs=("REQ-003",),
    )


def fingerprint(label: str) -> str:
    return canonical_digest({"finding": label})


def disposition(
    observation_id: str,
    semantic_fingerprint: str,
    *,
    severity: str = "P1",
    origin: str = "introduced",
    contract_relation: str = "in_scope",
    fact_status: str = "confirmed",
    disposition_value: str = "fix_now",
    release_effect: str = "blocking",
    accepted_risk_decision_id: str = "",
) -> FindingDisposition:
    return FindingDisposition(
        fact_status=fact_status,
        origin=origin,
        contract_relation=contract_relation,
        decision_requirement="none",
        severity=severity,
        disposition=disposition_value,
        release_effect=release_effect,
        finding_id=observation_id,
        source_artifact="reviews/E001/MANIFEST.json",
        source_candidate_id=observation_id,
        fingerprint=semantic_fingerprint,
        target_sha=TARGET_SHA,
        requirement_refs=("REQ-003",),
        why_fix_now="The confirmed regression blocks REQ-003.",
        accepted_risk_decision_id=accepted_risk_decision_id,
    )


def candidate(
    observation_id: str,
    *,
    source_kind: str,
    source_execution_id: str,
    semantic_fingerprint: str | None = None,
    severity: str = "P1",
    origin: str = "introduced",
    contract_relation: str = "in_scope",
    fact_status: str = "confirmed",
    disposition_value: str = "fix_now",
    release_effect: str = "blocking",
    accepted_risk_decision_id: str = "",
) -> CandidateObservation:
    semantic_fingerprint = semantic_fingerprint or fingerprint(observation_id)
    return CandidateObservation(
        observation_id=observation_id,
        source_kind=source_kind,
        source_ref=f"{source_kind}/{source_execution_id}/MANIFEST.json",
        source_execution_id=source_execution_id,
        source_candidate_id=observation_id,
        fingerprint=semantic_fingerprint,
        target_sha=TARGET_SHA,
        classification=disposition(
            observation_id,
            semantic_fingerprint,
            severity=severity,
            origin=origin,
            contract_relation=contract_relation,
            fact_status=fact_status,
            disposition_value=disposition_value,
            release_effect=release_effect,
            accepted_risk_decision_id=accepted_risk_decision_id,
        ),
        requirement_refs=("REQ-003",),
        scope_refs=("runtime-release",),
        why_fix_now="The confirmed regression blocks REQ-003.",
    )


def remediation_task(
    task_id: str,
    source_finding: str,
    remediation_round: int,
    *,
    severity: str = "P1",
    contract_relation: str = "in_scope",
    fact_status: str = "confirmed",
    disposition_value: str = "fix_now",
    release_effect: str = "blocking",
) -> dict:
    return {
        "id": task_id,
        "task_origin": "finding",
        "release_scope_revision": 1,
        "plan_item_refs": [],
        "requirement_refs": ["REQ-003"],
        "scope_refs": ["runtime-release"],
        "source_finding": source_finding,
        "authorization_input_id": "",
        "why_fix_now": "The confirmed regression blocks REQ-003.",
        "operational_reason": "",
        "origin": "introduced",
        "contract_relation": contract_relation,
        "release_effect": release_effect,
        "remediation_round": remediation_round,
        "fact_status": fact_status,
        "disposition": disposition_value,
        "severity": severity,
        "decision_requirement": "none",
        "scope_expanding": False,
        "write_allow": [f"src/{task_id}.py"],
        "acceptance": [f"{task_id} fixes its canonical candidate"],
    }


def candidate_batch(
    *,
    ledger: RemediationLedger | None = None,
    batch_id: str = "RB001",
    semantic_fingerprint: str | None = None,
) -> RemediationLedger:
    ledger = ledger or RemediationLedger()
    semantic_fingerprint = semantic_fingerprint or fingerprint(batch_id)
    ledger = create_remediation_batch(
        ledger,
        batch_id=batch_id,
        epoch_id=f"E-{batch_id}",
        target_sha=TARGET_SHA,
        required_execution_ids=("R001", "G001"),
    )
    ledger = register_candidate(
        ledger,
        batch_id,
        candidate(
            f"{batch_id}:review:F001",
            source_kind="reviewer",
            source_execution_id="R001",
            semantic_fingerprint=semantic_fingerprint,
        ),
    )
    ledger = register_candidate(
        ledger,
        batch_id,
        candidate(
            f"{batch_id}:gap:F001",
            source_kind="gap",
            source_execution_id="G001",
            semantic_fingerprint=semantic_fingerprint,
        ),
    )
    return mark_ready_to_triage(
        ledger,
        batch_id,
        terminal_execution_ids=("G001", "R001"),
    )


def approve(
    ledger: RemediationLedger,
    batch_id: str,
    *,
    task_number: int,
    remediation_round: int,
    extra_round_authorization_ref: str = "",
    extra_round_authorization: ExtraRoundAuthorization | None = None,
    captured_input_ids: tuple[str, ...] = (),
    captured_inputs_override: dict[str, dict[str, str]] | None = None,
) -> RemediationLedger:
    batch = ledger.batch(batch_id)
    task_id = f"T{task_number:03d}"
    return approve_remediation_batch(
        ledger,
        batch_id,
        approval_ref=f"manager-approval:{batch_id}",
        scope_digest=locked_scope().scope_digest,
        scope_revision=1,
        task_contracts=(
            remediation_task(
                task_id,
                batch.canonical_candidates[0].observation_ids[0],
                remediation_round,
            ),
        ),
        first_task_number=task_number,
        release_scope=locked_scope(),
        extra_round_authorization_ref=extra_round_authorization_ref,
        extra_round_authorization=extra_round_authorization,
        captured_input_ids=captured_input_ids,
        captured_inputs=(
            captured_inputs_override
            if captured_inputs_override is not None
            else {
                input_id: captured_input(input_id)[input_id]
                for input_id in captured_input_ids
            }
        ),
    )


def complete_observation(ledger: RemediationLedger, batch_id: str):
    plan = ledger.batch(batch_id).materialization_plan[0]
    contract = plan.to_record()["task_contract"]
    return TaskMaterializationObservation(
        task_id=plan.task_id,
        state_task_contract=contract,
        artifact_task_contract=contract,
        artifact_digest=canonical_digest(
            {"artifact_ref": plan.artifact_ref, "contract": contract}
        ),
        source_refs=plan.source_refs,
    )


def offline_validator(schema_path: Path):
    if (
        jsonschema is None
        or Registry is None
        or Resource is None
        or DRAFT202012 is None
    ):
        raise AssertionError("remediation schema tests require jsonschema")

    def retrieve(uri: str):
        if not uri.startswith("file://"):
            raise AssertionError(f"schema validation attempted network access: {uri}")
        from urllib.parse import unquote, urlparse

        path = Path(unquote(urlparse(uri).path))
        return Resource.from_contents(
            json.loads(path.read_text(encoding="utf-8")),
            default_specification=DRAFT202012,
        )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=Registry(retrieve=retrieve),
    )


class CandidateRegistrationTests(unittest.TestCase):
    def test_manual_final_source_alias_serializes_to_one_canonical_kind(self):
        observed = candidate(
            "manual:F001",
            source_kind="manual_final",
            source_execution_id="M001",
        )
        self.assertEqual(observed.source_kind, "manual-final")
        self.assertEqual(observed.to_record()["source_kind"], "manual-final")

    def test_multiple_sources_coalesce_and_exact_retry_is_idempotent(self):
        semantic = fingerprint("same-finding")
        ledger = RemediationLedger()
        ledger = create_remediation_batch(
            ledger,
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001", "G001"),
        )
        review = candidate(
            "review:F001",
            source_kind="reviewer",
            source_execution_id="R001",
            semantic_fingerprint=semantic,
        )
        ledger = register_candidate(ledger, "RB001", review)
        self.assertIs(register_candidate(ledger, "RB001", review), ledger)
        ledger = register_candidate(
            ledger,
            "RB001",
            candidate(
                "gap:F009",
                source_kind="gap",
                source_execution_id="G001",
                semantic_fingerprint=semantic,
            ),
        )
        ready = mark_ready_to_triage(
            ledger,
            "RB001",
            terminal_execution_ids=("R001", "G001"),
        )

        batch = ready.batch("RB001")
        self.assertEqual(batch.status, "ready_to_triage")
        self.assertEqual(len(batch.canonical_candidates), 1)
        self.assertEqual(
            batch.canonical_candidates[0].observation_ids,
            ("gap:F009", "review:F001"),
        )
        self.assertRegex(batch.candidate_set_digest, r"^sha256:[0-9a-f]{64}$")

    def test_same_observation_id_with_changed_content_fails_closed(self):
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001",),
        )
        original = candidate(
            "review:F001", source_kind="reviewer", source_execution_id="R001"
        )
        ledger = register_candidate(ledger, "RB001", original)
        with self.assertRaisesRegex(RemediationLedgerError, "changed on retry"):
            register_candidate(
                ledger,
                "RB001",
                candidate(
                    "review:F001",
                    source_kind="reviewer",
                    source_execution_id="R001",
                    severity="P2",
                ),
            )

        changed_identity = replace(original, observation_id="review:F999")
        with self.assertRaisesRegex(RemediationLedgerError, "different observation_id"):
            register_candidate(ledger, "RB001", changed_identity)

    def test_incomplete_required_execution_cannot_reach_triage(self):
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001", "G001"),
        )
        ledger = register_candidate(
            ledger,
            "RB001",
            candidate(
                "review:F001", source_kind="reviewer", source_execution_id="R001"
            ),
        )
        with self.assertRaisesRegex(RemediationLedgerError, "missing terminal"):
            mark_ready_to_triage(
                ledger, "RB001", terminal_execution_ids=("R001",)
            )

    def test_policy_axis_conflict_requires_explicit_canonicalization(self):
        semantic = fingerprint("classification-conflict")
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001", "G001"),
        )
        ledger = register_candidate(
            ledger,
            "RB001",
            candidate(
                "review:F001",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=semantic,
                severity="P1",
            ),
        )
        ledger = register_candidate(
            ledger,
            "RB001",
            candidate(
                "gap:F001",
                source_kind="gap",
                source_execution_id="G001",
                semantic_fingerprint=semantic,
                severity="P2",
            ),
        )
        self.assertEqual(ledger.batch("RB001").status, "classification_conflict")
        with self.assertRaises(CandidateClassificationConflict):
            mark_ready_to_triage(
                ledger,
                "RB001",
                terminal_execution_ids=("R001", "G001"),
            )
        canonical = disposition("manager:F001", semantic, severity="P1")
        ready = mark_ready_to_triage(
            ledger,
            "RB001",
            terminal_execution_ids=("R001", "G001"),
            canonical_classifications={semantic: canonical},
            canonicalization_ref="manager-triage:E001:classification-1",
        )
        self.assertEqual(ready.batch("RB001").status, "ready_to_triage")
        self.assertEqual(
            ready.batch("RB001").canonicalization_ref,
            "manager-triage:E001:classification-1",
        )

    def test_single_source_classification_override_requires_manager_reference(self):
        semantic = fingerprint("single-source-override")
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001",),
        )
        ledger = register_candidate(
            ledger,
            "RB001",
            candidate(
                "review:F001",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=semantic,
                severity="P2",
            ),
        )
        override = disposition("manager:F001", semantic, severity="P1")
        with self.assertRaisesRegex(RemediationLedgerError, "canonicalization_ref"):
            mark_ready_to_triage(
                ledger,
                "RB001",
                terminal_execution_ids=("R001",),
                canonical_classifications={semantic: override},
            )


class ApprovalAndRoundTests(unittest.TestCase):
    def test_mixed_batch_terminalizes_non_actionable_candidates_and_plans_only_fix_now(self):
        fix_fingerprint = fingerprint("mixed-fix")
        discard_fingerprint = fingerprint("mixed-discard")
        risk_fingerprint = fingerprint("mixed-risk")
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001",),
        )
        observations = (
            candidate(
                "review:fix",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=fix_fingerprint,
            ),
            candidate(
                "review:discard",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=discard_fingerprint,
                severity="P2",
                origin="unrelated-pre-existing",
                contract_relation="outside_release",
                fact_status="refuted",
                disposition_value="discard",
                release_effect="non_blocking",
            ),
            candidate(
                "review:risk",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=risk_fingerprint,
                severity="P2",
                origin="unrelated-pre-existing",
                contract_relation="outside_release",
                disposition_value="accepted_risk",
                release_effect="non_blocking",
                accepted_risk_decision_id="D-RISK-001",
            ),
        )
        for observation in observations:
            ledger = register_candidate(ledger, "RB001", observation)

        with self.assertRaisesRegex(
            RemediationLedgerError, "accepted-risk authorizations"
        ):
            mark_ready_to_triage(
                ledger,
                "RB001",
                terminal_execution_ids=("R001",),
            )

        authorization = AcceptedRiskAuthorization(
            decision_id="D-RISK-001",
            fingerprint=risk_fingerprint,
            target_sha=TARGET_SHA,
        )
        ready = mark_ready_to_triage(
            ledger,
            "RB001",
            terminal_execution_ids=("R001",),
            accepted_risk_authorizations=(authorization,),
        )
        approved = approve_remediation_batch(
            ready,
            "RB001",
            approval_ref="manager-approval:RB001",
            scope_digest=locked_scope().scope_digest,
            scope_revision=1,
            task_contracts=(
                remediation_task("T020", "review:fix", 1),
            ),
            first_task_number=20,
            release_scope=locked_scope(),
        )

        batch = approved.batch("RB001")
        self.assertEqual(len(batch.canonical_candidates), 3)
        self.assertEqual(
            batch.accepted_risk_authorizations, (authorization,)
        )
        self.assertEqual(
            batch.materialization_plan[0].candidate_fingerprints,
            (fix_fingerprint,),
        )
        self.assertEqual(approved.consumed_rounds, 1)

    def test_accepted_risk_authorization_is_bound_to_fingerprint_and_target(self):
        semantic = fingerprint("risk-binding")
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id="E001",
            target_sha=TARGET_SHA,
            required_execution_ids=("R001",),
        )
        ledger = register_candidate(
            ledger,
            "RB001",
            candidate(
                "review:risk",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=semantic,
                severity="P2",
                origin="unrelated-pre-existing",
                contract_relation="outside_release",
                disposition_value="accepted_risk",
                release_effect="non_blocking",
                accepted_risk_decision_id="D-RISK-002",
            ),
        )
        for bad in (
            AcceptedRiskAuthorization(
                decision_id="D-OTHER",
                fingerprint=semantic,
                target_sha=TARGET_SHA,
            ),
            AcceptedRiskAuthorization(
                decision_id="D-RISK-002",
                fingerprint=fingerprint("other"),
                target_sha=TARGET_SHA,
            ),
            AcceptedRiskAuthorization(
                decision_id="D-RISK-002",
                fingerprint=semantic,
                target_sha="other-target",
            ),
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    RemediationLedgerError, "accepted-risk authorizations"
                ):
                    mark_ready_to_triage(
                        ledger,
                        "RB001",
                        terminal_execution_ids=("R001",),
                        accepted_risk_authorizations=(bad,),
                    )

    def test_approval_consumes_one_round_and_stores_deterministic_write_ahead_plan(self):
        ledger = candidate_batch()
        approved = approve(
            ledger, "RB001", task_number=20, remediation_round=1
        )
        batch = approved.batch("RB001")

        self.assertEqual(approved.consumed_rounds, 1)
        self.assertEqual(batch.status, "materializing")
        self.assertTrue(batch.round_consumed)
        self.assertEqual(batch.remediation_round, 1)
        self.assertEqual([item.task_id for item in batch.materialization_plan], ["T020"])
        self.assertRegex(batch.approval_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(
            batch.materialization_plan[0].task_contract_digest,
            r"^sha256:[0-9a-f]{64}$",
        )

        replay = approve(
            approved, "RB001", task_number=20, remediation_round=1
        )
        self.assertIs(replay, approved)
        self.assertEqual(replay.consumed_rounds, 1)

    def test_approved_retry_with_different_contract_fails_closed(self):
        approved = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        )
        batch = approved.batch("RB001")
        changed = remediation_task(
            "T020", batch.canonical_candidates[0].observation_ids[0], 1
        )
        changed["acceptance"] = ["changed after approval"]
        with self.assertRaises(RemediationApprovalConflict):
            approve_remediation_batch(
                approved,
                "RB001",
                approval_ref="manager-approval:RB001",
                scope_digest=locked_scope().scope_digest,
                scope_revision=1,
                task_contracts=(changed,),
                first_task_number=20,
                release_scope=locked_scope(),
            )

    def test_task_ids_and_grouping_are_canonical(self):
        a = fingerprint("a")
        b = fingerprint("b")
        self.assertEqual(
            deterministic_task_ids(((b,), (a,)), first_task_number=7),
            (("T007", (a,)), ("T008", (b,))),
        )
        with self.assertRaisesRegex(RemediationLedgerError, "more than one"):
            deterministic_task_ids(((a, b), (a,)), first_task_number=7)

    def test_release_scope_and_canonical_axes_cannot_be_bypassed(self):
        ledger = candidate_batch()
        batch = ledger.batch("RB001")
        source = batch.canonical_candidates[0].observation_ids[0]
        outside = remediation_task(
            "T020", source, 1, contract_relation="outside_release"
        )
        with self.assertRaisesRegex(
            RemediationLedgerError, "release-scope authorization|canonical classification"
        ):
            approve_remediation_batch(
                ledger,
                "RB001",
                approval_ref="manager-approval:RB001",
                scope_digest=locked_scope().scope_digest,
                scope_revision=1,
                task_contracts=(outside,),
                first_task_number=20,
                release_scope=locked_scope(),
            )

        stale_scope = replace(locked_scope(), scope_revision=2)
        valid = remediation_task("T020", source, 1)
        with self.assertRaisesRegex(RemediationLedgerError, "scope_revision is stale"):
            approve_remediation_batch(
                ledger,
                "RB001",
                approval_ref="manager-approval:RB001",
                scope_digest=stale_scope.scope_digest,
                scope_revision=1,
                task_contracts=(valid,),
                first_task_number=20,
                release_scope=stale_scope,
            )

    def test_round_limit_needs_unique_extra_authorization_and_never_double_consumes(self):
        ledger = candidate_batch(ledger=RemediationLedger(max_fix_rounds=1))
        ledger = approve(ledger, "RB001", task_number=20, remediation_round=1)
        ledger = candidate_batch(
            ledger=ledger,
            batch_id="RB002",
            semantic_fingerprint=fingerprint("round-two"),
        )
        with self.assertRaises(RemediationRoundLimitExceeded):
            approve(ledger, "RB002", task_number=21, remediation_round=2)
        authorization = ExtraRoundAuthorization(
            input_id="U0007",
            source="manager-chat",
            content_digest=EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB002",),
        )
        ledger = approve(
            ledger,
            "RB002",
            task_number=21,
            remediation_round=2,
            extra_round_authorization_ref="U0007:RB002",
            extra_round_authorization=authorization,
            captured_input_ids=("U0007",),
        )
        self.assertEqual(ledger.consumed_rounds, 2)
        self.assertEqual(
            ledger.consumed_extra_round_authorization_refs, ("U0007:RB002",)
        )
        replay = approve_remediation_batch(
            ledger,
            "RB002",
            approval_ref="manager-approval:RB002",
            scope_digest=locked_scope().scope_digest,
            scope_revision=1,
            task_contracts=(
                remediation_task(
                    "T021",
                    ledger.batch("RB002").canonical_candidates[0].observation_ids[0],
                    2,
                ),
            ),
            first_task_number=21,
            release_scope=locked_scope(),
            extra_round_authorization_ref="U0007:RB002",
            extra_round_authorization=authorization,
            captured_input_ids=("U0007",),
            captured_inputs=captured_input("U0007"),
        )
        self.assertIs(replay, ledger)
        self.assertEqual(replay.consumed_rounds, 2)

    def test_policy_default_is_two_and_larger_limits_are_rejected(self):
        self.assertEqual(RemediationLedger().max_fix_rounds, 2)
        with self.assertRaisesRegex(RemediationLedgerError, "must not exceed 2"):
            RemediationLedger(max_fix_rounds=3)

    def test_zero_automatic_rounds_requires_exact_captured_authorization(self):
        ledger = candidate_batch(ledger=RemediationLedger(max_fix_rounds=0))
        with self.assertRaises(RemediationRoundLimitExceeded):
            approve(ledger, "RB001", task_number=20, remediation_round=1)

        authorization = ExtraRoundAuthorization(
            input_id="U0008",
            source="manager-chat",
            content_digest=EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB001",),
        )
        approved = approve(
            ledger,
            "RB001",
            task_number=20,
            remediation_round=1,
            extra_round_authorization_ref="U0008:RB001",
            extra_round_authorization=authorization,
            captured_input_ids=("U0008",),
        )

        self.assertEqual(approved.max_fix_rounds, 0)
        self.assertEqual(approved.consumed_rounds, 1)
        self.assertEqual(
            approved.consumed_extra_round_authorization_refs,
            ("U0008:RB001",),
        )
        self.assertEqual(
            RemediationLedger.from_record(approved.to_record()), approved
        )

    def test_extra_round_authorization_fails_closed_for_unsafe_records(self):
        ledger = candidate_batch(ledger=RemediationLedger(max_fix_rounds=0))
        exact = ExtraRoundAuthorization(
            input_id="U0009",
            source="manager-chat",
            content_digest=EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB001",),
        )
        cases = (
            (
                "arbitrary-ref",
                {
                    "extra_round_authorization_ref": "arbitrary",
                    "extra_round_authorization": exact,
                    "captured_input_ids": ("U0009",),
                },
            ),
            (
                "uncaptured-input",
                {
                    "extra_round_authorization_ref": "U0009:RB001",
                    "extra_round_authorization": exact,
                    "captured_input_ids": (),
                },
            ),
            (
                "other-batch",
                {
                    "extra_round_authorization_ref": "U0009:RB001",
                    "extra_round_authorization": ExtraRoundAuthorization(
                        input_id="U0009",
                        source="manager-chat",
                        content_digest=EXTRA_INPUT_DIGEST,
                        authorized_extra_rounds=1,
                        remediation_batch_ids=("RB002",),
                    ),
                    "captured_input_ids": ("U0009",),
                },
            ),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                with self.assertRaises(RemediationLedgerError):
                    approve(
                        ledger,
                        "RB001",
                        task_number=20,
                        remediation_round=1,
                        **kwargs,
                    )

        with self.assertRaisesRegex(
            RemediationLedgerError, "exactly match remediation_batch_ids"
        ):
            ExtraRoundAuthorization(
                input_id="U0009",
                source="manager-chat",
                content_digest=EXTRA_INPUT_DIGEST,
                authorized_extra_rounds=1,
                remediation_batch_ids=("RB001", "RB002"),
            )

        for label, source, content_digest in (
            ("non-user-origin", "reviewer", EXTRA_INPUT_DIGEST),
            ("unlabelled-content", "manager-chat", "0" * 64),
        ):
            with self.subTest(label=label):
                with self.assertRaises(RemediationLedgerError):
                    ExtraRoundAuthorization(
                        input_id="U0009",
                        source=source,
                        content_digest=content_digest,
                        authorized_extra_rounds=1,
                        remediation_batch_ids=("RB001",),
                    )

        with self.assertRaisesRegex(RemediationLedgerError, "input_id"):
            ExtraRoundAuthorization(
                input_id="manager-latest-message",
                source="manager-chat",
                content_digest=EXTRA_INPUT_DIGEST,
                authorized_extra_rounds=1,
                remediation_batch_ids=("RB001",),
            )

        with self.assertRaisesRegex(RemediationLedgerError, "provenance"):
            approve(
                ledger,
                "RB001",
                task_number=20,
                remediation_round=1,
                extra_round_authorization_ref="U0009:RB001",
                extra_round_authorization=exact,
                captured_input_ids=("U0009",),
                captured_inputs_override={
                    "U0009": {
                        "source": "manager-chat",
                        "prompt_digest": "1" * 64,
                    }
                },
            )

    def test_captured_input_authorization_cannot_be_redefined_for_reuse(self):
        first_authorization = ExtraRoundAuthorization(
            input_id="U0012",
            source="manager-chat",
            content_digest=EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB001",),
        )
        ledger = approve(
            candidate_batch(ledger=RemediationLedger(max_fix_rounds=0)),
            "RB001",
            task_number=20,
            remediation_round=1,
            extra_round_authorization_ref="U0012:RB001",
            extra_round_authorization=first_authorization,
            captured_input_ids=("U0012",),
        )
        ledger = candidate_batch(
            ledger=ledger,
            batch_id="RB002",
            semantic_fingerprint=fingerprint("reused-input"),
        )
        redefined = ExtraRoundAuthorization(
            input_id="U0012",
            source="manager-chat",
            content_digest=EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB002",),
        )

        with self.assertRaisesRegex(RemediationLedgerError, "changed across batches"):
            approve(
                ledger,
                "RB002",
                task_number=21,
                remediation_round=2,
                extra_round_authorization_ref="U0012:RB002",
                extra_round_authorization=redefined,
                captured_input_ids=("U0012",),
            )


class CrashReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.approved = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        )

    def test_missing_materialization_is_repairable_without_consuming_round_again(self):
        result = reconcile_materialization(self.approved, "RB001", ())
        self.assertEqual(result.status, "repair_required")
        self.assertEqual(result.ledger.consumed_rounds, 1)
        self.assertEqual(
            {item.action for item in result.repair_actions},
            {
                "write_state_projection",
                "write_task_artifact",
                "link_source_created_fix_task",
            },
        )

    def test_partial_crash_reconcile_reports_only_missing_durable_steps(self):
        plan = self.approved.batch("RB001").materialization_plan[0]
        contract = plan.to_record()["task_contract"]
        result = reconcile_materialization(
            self.approved,
            "RB001",
            (
                TaskMaterializationObservation(
                    task_id="T020",
                    state_task_contract=contract,
                ),
            ),
        )
        self.assertEqual(result.status, "repair_required")
        self.assertNotIn(
            "write_state_projection",
            {item.action for item in result.repair_actions},
        )
        self.assertIn(
            "write_task_artifact",
            {item.action for item in result.repair_actions},
        )

    def test_conflicting_state_or_artifact_stops_without_repair_guess(self):
        plan = self.approved.batch("RB001").materialization_plan[0]
        contract = plan.to_record()["task_contract"]
        conflicting = dict(contract)
        conflicting["acceptance"] = ["tampered after write-ahead"]
        result = reconcile_materialization(
            self.approved,
            "RB001",
            (
                TaskMaterializationObservation(
                    task_id="T020",
                    state_task_contract=conflicting,
                    artifact_task_contract=contract,
                    artifact_digest=canonical_digest({"artifact": "T020"}),
                    source_refs=plan.source_refs,
                ),
            ),
        )
        self.assertEqual(result.status, "remediation_reconcile_required")
        self.assertEqual(result.repair_actions, ())
        self.assertIs(result.ledger, self.approved)
        self.assertIn("STATE projection conflicts", result.issues[0])

    def test_complete_reconcile_dispatches_once_and_replay_is_idempotent(self):
        observed = complete_observation(self.approved, "RB001")
        result = reconcile_materialization(
            self.approved, "RB001", (observed,)
        )
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.ledger.batch("RB001").status, "dispatched")
        self.assertEqual(result.ledger.consumed_rounds, 1)
        replay = reconcile_materialization(
            result.ledger, "RB001", (observed,)
        )
        self.assertEqual(replay.status, "dispatched")
        self.assertEqual(replay.ledger, result.ledger)
        self.assertEqual(replay.ledger.consumed_rounds, 1)

        lost = reconcile_materialization(result.ledger, "RB001", ())
        self.assertEqual(lost.status, "remediation_reconcile_required")
        self.assertEqual(lost.repair_actions, ())
        self.assertIn("dispatched task", lost.issues[0])

    def test_no_change_and_aborted_outcomes_keep_the_round_consumed(self):
        observed = complete_observation(self.approved, "RB001")
        dispatched = reconcile_materialization(
            self.approved, "RB001", (observed,)
        ).ledger
        completed = complete_remediation_batch(
            dispatched, "RB001", outcome="no_change"
        )
        self.assertEqual(completed.batch("RB001").status, "completed")
        self.assertEqual(completed.consumed_rounds, 1)
        self.assertIs(
            complete_remediation_batch(completed, "RB001", outcome="no_change"),
            completed,
        )

        second = candidate_batch(
            ledger=completed,
            batch_id="RB002",
            semantic_fingerprint=fingerprint("abort-round"),
        )
        second = approve(second, "RB002", task_number=21, remediation_round=2)
        second = reconcile_materialization(
            second, "RB002", (complete_observation(second, "RB002"),)
        ).ledger
        aborted = complete_remediation_batch(second, "RB002", outcome="aborted")
        self.assertEqual(aborted.batch("RB002").status, "aborted")
        self.assertEqual(aborted.consumed_rounds, 2)


class PersistenceAndSchemaTests(unittest.TestCase):
    def test_round_trip_preserves_immutable_ledger_identity(self):
        approved = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        )
        restored = RemediationLedger.from_record(
            json.loads(json.dumps(approved.to_record()))
        )
        self.assertEqual(restored, approved)
        self.assertEqual(
            canonical_digest(
                restored.batch("RB001").materialization_plan[0].task_contract
            ),
            restored.batch("RB001").materialization_plan[0].task_contract_digest,
        )
        with self.assertRaises(FrozenInstanceError):
            restored.consumed_rounds = 99  # type: ignore[misc]
        with self.assertRaises(TypeError):
            contract = restored.batch("RB001").materialization_plan[0].task_contract
            contract["id"] = "T999"  # type: ignore[index]

    def test_ambiguous_persisted_rounds_fail_closed(self):
        approved = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        ).to_record()
        approved["consumed_rounds"] = 2
        with self.assertRaisesRegex(RemediationLedgerError, "contiguous"):
            RemediationLedger.from_record(approved)

    def test_restore_recomputes_canonical_approval_payload(self):
        approved = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        ).to_record()

        changed_plan = json.loads(json.dumps(approved))
        planned = changed_plan["batches"][0]["materialization_plan"][0]
        planned["task_contract"]["acceptance"] = ["expanded after approval"]
        planned["task_contract_digest"] = canonical_digest(
            planned["task_contract"]
        )

        changed_scope = json.loads(json.dumps(approved))
        changed_scope["batches"][0]["scope_digest"] = canonical_digest(
            {"scope": "changed"}
        )

        changed_round = json.loads(json.dumps(approved))
        changed_round["batches"][0]["remediation_round"] = 2

        changed_candidates = json.loads(json.dumps(approved))
        canonical = changed_candidates["batches"][0]["canonical_candidates"]
        canonical[0]["why_fix_now"] = "changed candidate evidence"
        changed_candidates["batches"][0]["candidate_set_digest"] = canonical_digest(
            canonical
        )

        changed_fingerprints = json.loads(json.dumps(approved))
        changed_fingerprints["batches"][0]["materialization_plan"][0][
            "candidate_fingerprints"
        ] = [fingerprint("unapproved-candidate")]

        for label, record, error in (
            ("plan", changed_plan, "approval_digest"),
            ("scope", changed_scope, "approval_digest"),
            ("round", changed_round, "remediation_round|approval_digest"),
            ("candidates", changed_candidates, "approval_digest"),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    RemediationLedgerError, error
                ):
                    RemediationLedger.from_record(record)

        with self.assertRaisesRegex(
            RemediationLedgerError, "exactly cover"
        ):
            RemediationLedger.from_record(changed_fingerprints)

    def test_restore_binds_extra_authorization_and_artifact_paths(self):
        ledger = candidate_batch(ledger=RemediationLedger(max_fix_rounds=0))
        authorization = ExtraRoundAuthorization(
            input_id="U0010",
            source="manager-chat",
            content_digest=EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB001",),
        )
        approved = approve(
            ledger,
            "RB001",
            task_number=20,
            remediation_round=1,
            extra_round_authorization_ref="U0010:RB001",
            extra_round_authorization=authorization,
            captured_input_ids=("U0010",),
        ).to_record()

        changed_authorization = json.loads(json.dumps(approved))
        batch = changed_authorization["batches"][0]
        batch["extra_round_authorization"]["input_id"] = "U0011"
        batch["extra_round_authorization_ref"] = "U0011:RB001"
        changed_authorization["consumed_extra_round_authorization_refs"] = [
            "U0011:RB001"
        ]
        with self.assertRaisesRegex(RemediationLedgerError, "approval_digest"):
            RemediationLedger.from_record(changed_authorization)

        changed_path = json.loads(json.dumps(approved))
        changed_path["batches"][0]["materialization_plan"][0][
            "artifact_ref"
        ] = "tasks/T999.md"
        with self.assertRaisesRegex(RemediationLedgerError, "canonical"):
            RemediationLedger.from_record(changed_path)

    def test_restore_rejects_missing_or_noncanonical_write_ahead_identity(self):
        valid = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        ).to_record()
        cases = (
            ("null-digest", "task_contract_digest", None),
            ("empty-digest", "task_contract_digest", ""),
            ("unlabelled-digest", "task_contract_digest", "0" * 64),
            ("null-artifact", "artifact_ref", None),
            ("empty-artifact", "artifact_ref", ""),
            ("noncanonical-artifact", "artifact_ref", "./tasks/T020.md"),
        )
        for label, field, value in cases:
            record = json.loads(json.dumps(valid))
            record["batches"][0]["materialization_plan"][0][field] = value
            with self.subTest(label=label):
                with self.assertRaises(RemediationLedgerError):
                    RemediationLedger.from_record(record)
                for schema_path in (REFERENCE_SCHEMA, PUBLIC_SCHEMA):
                    self.assertTrue(
                        list(offline_validator(schema_path).iter_errors(record))
                    )

    def test_reconcile_revalidates_approval_digest(self):
        approved = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        )
        object.__setattr__(
            approved.batch("RB001"),
            "approval_digest",
            canonical_digest({"forged": True}),
        )

        with self.assertRaisesRegex(RemediationLedgerError, "approval_digest"):
            reconcile_materialization(approved, "RB001", ())

    def test_reference_and_public_schemas_accept_contract_and_reject_unsafe_shapes(self):
        valid = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        ).to_record()
        for schema_path in (REFERENCE_SCHEMA, PUBLIC_SCHEMA):
            validator = offline_validator(schema_path)
            with self.subTest(schema=schema_path.name, case="valid"):
                self.assertEqual(list(validator.iter_errors(valid)), [])
            invalid_limit = json.loads(json.dumps(valid))
            invalid_limit["max_fix_rounds"] = 3
            with self.subTest(schema=schema_path.name, case="limit"):
                self.assertTrue(list(validator.iter_errors(invalid_limit)))
            unknown = json.loads(json.dumps(valid))
            unknown["unexpected"] = True
            with self.subTest(schema=schema_path.name, case="unknown"):
                self.assertTrue(list(validator.iter_errors(unknown)))
            ambiguous = json.loads(json.dumps(valid))
            ambiguous["batches"][0]["status"] = "authorized"
            with self.subTest(schema=schema_path.name, case="authorized-gap"):
                self.assertTrue(list(validator.iter_errors(ambiguous)))
            zero_rounds = RemediationLedger(max_fix_rounds=0).to_record()
            with self.subTest(schema=schema_path.name, case="zero-rounds"):
                self.assertEqual(list(validator.iter_errors(zero_rounds)), [])

    def test_python_parser_rejects_unknown_fields_and_digest_mismatch(self):
        valid = approve(
            candidate_batch(), "RB001", task_number=20, remediation_round=1
        ).to_record()
        unknown = json.loads(json.dumps(valid))
        unknown["unexpected"] = True
        with self.assertRaisesRegex(RemediationLedgerError, "unknown fields"):
            RemediationLedger.from_record(unknown)
        nested_unknown = json.loads(json.dumps(valid))
        nested_unknown["batches"][0]["observations"][0]["classification"][
            "unexpected_axis"
        ] = "unsafe"
        with self.assertRaisesRegex(RemediationLedgerError, "classification.*unknown"):
            RemediationLedger.from_record(nested_unknown)
        mismatch = json.loads(json.dumps(valid))
        mismatch["batches"][0]["candidate_set_digest"] = canonical_digest(
            {"wrong": True}
        )
        with self.assertRaisesRegex(RemediationLedgerError, "candidate_set_digest"):
            RemediationLedger.from_record(mismatch)


if __name__ == "__main__":
    unittest.main()
