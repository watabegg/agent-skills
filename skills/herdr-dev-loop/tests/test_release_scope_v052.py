from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional for skill consumers
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.release_scope import (  # noqa: E402
    AmendmentValidationError,
    ProvenanceMutationError,
    ReleaseScope,
    ScopeDriftError,
    ScopeValidationError,
    TaskAuthorizationError,
    TaskProvenance,
    aggregate_source_digest,
    authorize_task_creation,
    compute_source_digests,
    create_amendment,
    digest_text,
    validate_provenance_update,
    validate_task_provenance,
)


NOW = "2026-07-16T00:00:00+00:00"
SOURCES = {
    "MISSION.md": "goal\n",
    "PLAN.md": "P001: bounded core\n",
}


def locked_scope(**overrides):
    values = {
        "source_contents": SOURCES,
        "locked_at": NOW,
        "plan_item_refs": ("P001",),
        "requirement_refs": ("REQ-001",),
        "release_scope_refs": ("release-scope",),
        "accepted_requirement_refs": ("REQ-001",),
    }
    values.update(overrides)
    return ReleaseScope.lock(**values)


def planned_task(**overrides):
    values = {
        "task_origin": "planned",
        "release_scope_revision": 1,
        "plan_item_refs": ["P001"],
        "requirement_refs": [],
        "scope_refs": [],
        "source_finding": "",
        "authorization_input_id": "",
        "why_fix_now": "",
        "operational_reason": "",
        "origin": "",
        "contract_relation": "",
        "release_effect": "",
        "remediation_round": 0,
        "fact_status": "",
        "disposition": "",
    }
    values.update(overrides)
    return values


def finding_task(**overrides):
    values = {
        "task_origin": "finding",
        "release_scope_revision": 1,
        "plan_item_refs": [],
        "requirement_refs": ["REQ-001"],
        "scope_refs": [],
        "source_finding": "FND-001",
        "authorization_input_id": "",
        "why_fix_now": "The confirmed regression violates the release contract.",
        "operational_reason": "",
        "origin": "introduced",
        "contract_relation": "in_scope",
        "release_effect": "blocking",
        "remediation_round": 1,
        "fact_status": "confirmed",
        "disposition": "fix_now",
        "severity": "P1",
        "decision_requirement": "none",
    }
    values.update(overrides)
    return values


class SourceDigestTests(unittest.TestCase):
    def test_source_digest_is_canonical_and_snapshot_digest_is_order_independent(self):
        self.assertRegex(digest_text("hello\n"), r"^sha256:[0-9a-f]{64}$")
        first = compute_source_digests(SOURCES)
        second = compute_source_digests({"PLAN.md": SOURCES["PLAN.md"], "MISSION.md": SOURCES["MISSION.md"]})
        self.assertEqual(first, second)
        self.assertEqual(aggregate_source_digest(first), aggregate_source_digest(second))

    def test_invalid_source_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source content"):
            compute_source_digests({"PLAN.md": b"not text"})


class ReleaseScopeTests(unittest.TestCase):
    def test_lock_requires_every_locked_reference_to_have_a_digest(self):
        digests = compute_source_digests(SOURCES)
        with self.assertRaisesRegex(ScopeValidationError, "missing locked references"):
            ReleaseScope.lock(
                source_refs=("MISSION.md", "PLAN.md"),
                source_digests={"MISSION.md": digests["MISSION.md"]},
            )

    def test_lock_is_immutable_and_detects_source_drift(self):
        scope = locked_scope()
        with self.assertRaises(TypeError):
            scope.source_digests["PLAN.md"] = digest_text("changed")  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            scope.scope_revision = 2  # type: ignore[misc]
        with self.assertRaises(ScopeDriftError):
            scope.assert_source_snapshot({**SOURCES, "PLAN.md": "changed\n"})

    def test_editorial_amendment_changes_only_source_snapshot_revision(self):
        scope = locked_scope()
        amendment = create_amendment(
            scope,
            amendment_id="A001",
            kind="editorial",
            new_source_contents={"MISSION.md": SOURCES["MISSION.md"], "PLAN.md": "P001: bounded core.\n"},
            reason="Fix punctuation without changing the contract.",
        )
        updated = scope.apply_amendment(amendment)
        self.assertEqual(updated.scope_revision, 1)
        self.assertEqual(updated.source_snapshot_revision, 2)
        self.assertEqual(updated.amendment_refs, ("A001",))
        self.assertEqual(scope.source_snapshot_revision, 1)
        self.assertEqual(amendment.previous_scope_digest, scope.scope_digest)

    def test_clarification_requires_basis_reference(self):
        scope = locked_scope()
        with self.assertRaisesRegex(AmendmentValidationError, "basis_refs"):
            create_amendment(
                scope,
                amendment_id="A001",
                kind="clarification",
                new_source_contents={"MISSION.md": "clarified\n", "PLAN.md": SOURCES["PLAN.md"]},
                reason="Make an implication explicit.",
            )

    def test_scope_change_requires_user_input_and_records_new_scope_revision(self):
        scope = locked_scope()
        with self.assertRaisesRegex(AmendmentValidationError, "user_input_id"):
            create_amendment(
                scope,
                amendment_id="A001",
                kind="scope-change",
                new_source_contents={"MISSION.md": "expanded\n", "PLAN.md": SOURCES["PLAN.md"]},
                reason="Add an explicitly authorized platform.",
            )
        amendment = create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            new_source_contents={
                "MISSION.md": "expanded\n",
                "PLAN.md": SOURCES["PLAN.md"],
                "SCOPE.md": "newly locked contract section\n",
            },
            reason="Add an explicitly authorized platform.",
            user_input_id="U0002",
        )
        updated = scope.apply_amendment(amendment)
        self.assertEqual(updated.scope_revision, 2)
        self.assertEqual(updated.source_snapshot_revision, 2)
        self.assertEqual(updated.last_user_input_id, "U0002")
        self.assertEqual(updated.consumed_user_input_ids, ("U0002",))
        self.assertEqual(updated.source_refs, ("MISSION.md", "PLAN.md", "SCOPE.md"))
        second = create_amendment(
            updated,
            amendment_id="A002",
            kind="scope-change",
            new_source_contents={
                "MISSION.md": "expanded again\n",
                "PLAN.md": SOURCES["PLAN.md"],
                "SCOPE.md": "newly locked contract section\n",
            },
            reason="Record a separately authorized scope change.",
            user_input_id="U0003",
        )
        self.assertEqual(updated.apply_amendment(second).scope_revision, 3)

    def test_scope_change_rejects_input_missing_from_captured_inventory(self):
        scope = locked_scope()
        with self.assertRaisesRegex(AmendmentValidationError, "captured input inventory"):
            create_amendment(
                scope,
                amendment_id="A001",
                kind="scope-change",
                new_source_contents={
                    "MISSION.md": "expanded\n",
                    "PLAN.md": SOURCES["PLAN.md"],
                },
                reason="Reject an authorization that was never captured.",
                user_input_id="U0002",
                captured_input_ids={"U0001": {"source": "manager-chat"}},
            )

    def test_scope_change_cannot_reuse_input_after_editorial_or_clarification(self):
        scope = locked_scope()
        scope_change = create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            new_source_contents={
                "MISSION.md": "expanded\n",
                "PLAN.md": SOURCES["PLAN.md"],
            },
            reason="User-authorized scope addition.",
            user_input_id="U0002",
        )
        scope_after_change = scope.apply_amendment(scope_change)

        for kind, contents, basis_refs in (
            (
                "editorial",
                {"MISSION.md": "expanded,\n", "PLAN.md": SOURCES["PLAN.md"]},
                (),
            ),
            (
                "clarification",
                {"MISSION.md": "expanded\n", "PLAN.md": "P001: bounded core clarified\n"},
                ("REQ-001",),
            ),
        ):
            with self.subTest(kind=kind):
                non_scope_amendment = create_amendment(
                    scope_after_change,
                    amendment_id="A002",
                    kind=kind,
                    new_source_contents=contents,
                    reason=f"{kind} source update.",
                    basis_refs=basis_refs,
                )
                amended_scope = scope_after_change.apply_amendment(non_scope_amendment)
                self.assertEqual(amended_scope.last_user_input_id, "U0002")

                reused = create_amendment(
                    amended_scope,
                    amendment_id="A003",
                    kind="scope-change",
                    new_source_contents={
                        "MISSION.md": "reused authorization\n",
                        "PLAN.md": SOURCES["PLAN.md"],
                    },
                    reason="Reject reuse of the consumed authorization input.",
                    user_input_id="U0002",
                )
                with self.assertRaisesRegex(
                    AmendmentValidationError, "already authorized an earlier amendment"
                ):
                    amended_scope.apply_amendment(reused)

    def test_scope_change_history_rejects_old_input_after_a_different_change_and_round_trips(self):
        scope = locked_scope()
        first = scope.apply_amendment(
            create_amendment(
                scope,
                amendment_id="A001",
                kind="scope-change",
                new_source_contents={
                    "MISSION.md": "expanded once\n",
                    "PLAN.md": SOURCES["PLAN.md"],
                },
                reason="First authorized scope change.",
                user_input_id="U0001",
            )
        )
        second = first.apply_amendment(
            create_amendment(
                first,
                amendment_id="A002",
                kind="scope-change",
                new_source_contents={
                    "MISSION.md": "expanded twice\n",
                    "PLAN.md": SOURCES["PLAN.md"],
                },
                reason="Second authorized scope change.",
                user_input_id="U0002",
            )
        )
        self.assertEqual(second.last_user_input_id, "U0002")
        self.assertEqual(second.consumed_user_input_ids, ("U0001", "U0002"))

        editorial = second.apply_amendment(
            create_amendment(
                second,
                amendment_id="A003",
                kind="editorial",
                new_source_contents={
                    "MISSION.md": "expanded twice,\n",
                    "PLAN.md": SOURCES["PLAN.md"],
                },
                reason="Editorial source correction.",
            )
        )
        self.assertEqual(editorial.consumed_user_input_ids, ("U0001", "U0002"))

        reused = create_amendment(
            editorial,
            amendment_id="A004",
            kind="scope-change",
            new_source_contents={
                "MISSION.md": "reused first authorization\n",
                "PLAN.md": SOURCES["PLAN.md"],
            },
            reason="Reject reuse of the first consumed authorization.",
            user_input_id="U0001",
        )
        with self.assertRaisesRegex(
            AmendmentValidationError, "already authorized an earlier amendment"
        ):
            editorial.apply_amendment(reused)

        restored = ReleaseScope.from_record(editorial.to_record())
        self.assertEqual(restored.consumed_user_input_ids, ("U0001", "U0002"))
        self.assertEqual(restored.to_record(), editorial.to_record())

        legacy_record = editorial.to_record()
        legacy_record.pop("consumed_user_input_ids")
        legacy = ReleaseScope.from_record(legacy_record)
        self.assertEqual(legacy.consumed_user_input_ids, ("U0002",))

    def test_legacy_scope_record_with_only_last_input_is_normalized_for_reuse_checks(self):
        scope = locked_scope()
        legacy_record = scope.to_record()
        legacy_record["last_user_input_id"] = "U0007"
        legacy = ReleaseScope.from_record(legacy_record)
        self.assertEqual(legacy.consumed_user_input_ids, ("U0007",))
        self.assertEqual(legacy.to_record()["consumed_user_input_ids"], ["U0007"])

    def test_amendment_cannot_start_from_another_lock_or_be_reapplied(self):
        scope = locked_scope()
        amendment = create_amendment(
            scope,
            amendment_id="A001",
            kind="editorial",
            new_source_contents={"MISSION.md": "new\n", "PLAN.md": SOURCES["PLAN.md"]},
            reason="Editorial correction.",
        )
        updated = scope.apply_amendment(amendment)
        with self.assertRaisesRegex(AmendmentValidationError, "already been applied"):
            updated.apply_amendment(amendment)
        with self.assertRaisesRegex(AmendmentValidationError, "different source snapshot"):
            scope.apply_amendment(
                create_amendment(
                    locked_scope(
                        source_contents={
                            "MISSION.md": "branch-specific\n",
                            "PLAN.md": SOURCES["PLAN.md"],
                        }
                    ),
                    amendment_id="A002",
                    kind="editorial",
                    new_source_contents={"MISSION.md": "other\n", "PLAN.md": SOURCES["PLAN.md"]},
                    reason="Different branch.",
                )
            )


class TaskAuthorizationTests(unittest.TestCase):
    def test_planned_task_requires_locked_plan_or_requirement_reference(self):
        scope = locked_scope()
        self.assertEqual(
            authorize_task_creation(planned_task(), scope),
            TaskProvenance.from_record(planned_task()),
        )
        with self.assertRaisesRegex(TaskAuthorizationError, "plan_item_refs or requirement_refs"):
            authorize_task_creation(planned_task(plan_item_refs=[]), scope)
        with self.assertRaisesRegex(TaskAuthorizationError, "missing locked PLAN reference"):
            authorize_task_creation(planned_task(plan_item_refs=["P999"]), scope)

    def test_created_from_plan_text_does_not_authorize_direct_task_bypass(self):
        scope = locked_scope()
        direct = {
            "created_from": "PLAN.md",
            "task_origin": "planned",
            "release_scope_revision": 1,
            "plan_item_refs": [],
        }
        with self.assertRaisesRegex(TaskAuthorizationError, "plan_item_refs or requirement_refs"):
            authorize_task_creation(direct, scope)

    def test_finding_task_requires_confirmed_in_scope_fix_now_provenance(self):
        scope = locked_scope()
        self.assertEqual(
            authorize_task_creation(
                finding_task(), scope, locked_finding_ids=("FND-001",)
            ).task_origin,
            "finding",
        )
        with self.assertRaisesRegex(TaskAuthorizationError, "finding inventory is empty"):
            authorize_task_creation(finding_task(), scope)
        for mutation, message in (
            ({"contract_relation": "outside_release"}, "in_scope"),
            ({"fact_status": "insufficient_evidence"}, "fact_status confirmed"),
            ({"disposition": "defer_follow_up"}, "disposition fix_now"),
            ({"requirement_refs": []}, "requirement_refs or an explicit release scope reference"),
        ):
            with self.subTest(mutation=mutation):
                candidate = finding_task(**mutation)
                with self.assertRaisesRegex(TaskAuthorizationError, message):
                    authorize_task_creation(candidate, scope, locked_finding_ids=("FND-001",))

    def test_authoritative_finding_record_rejects_stale_or_mutated_task_axes(self):
        scope = locked_scope()
        fingerprint = "sha256:" + "f" * 64
        record = {
            "fingerprint": fingerprint,
            "target_sha": "target-current",
            "scope_revision": scope.scope_revision,
            "confirmed": True,
            "policy_axes_explicit": True,
            "policy_axes": {
                "fact_status": "confirmed",
                "severity": "P1",
                "origin": "introduced",
                "contract_relation": "in_scope",
                "decision_requirement": "none",
                "disposition": "fix_now",
                "release_effect": "blocking",
            },
            "requirement_refs": ["REQ-001"],
            "scope_refs": [],
        }
        task = finding_task(source_finding=fingerprint)
        self.assertEqual(
            authorize_task_creation(
                task,
                scope,
                finding_records={fingerprint: record},
                current_target_sha="target-current",
            ).source_finding,
            fingerprint,
        )

        for mutation in (
            {"severity": "P2"},
            {"contract_relation": "outside_release"},
            {"disposition": "defer_follow_up"},
            {"fact_status": "refuted"},
            {"decision_requirement": "user"},
            {"requirement_refs": ["REQ-999"]},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(TaskAuthorizationError):
                    authorize_task_creation(
                        {**task, **mutation},
                        scope,
                        finding_records={fingerprint: record},
                        current_target_sha="target-current",
                    )

        for inventory_mutation in (
            {"confirmed": False},
            {"target_sha": "stale-target"},
        ):
            with self.subTest(inventory_mutation=inventory_mutation):
                with self.assertRaises(TaskAuthorizationError):
                    authorize_task_creation(
                        task,
                        scope,
                        finding_records={fingerprint: {**record, **inventory_mutation}},
                        current_target_sha="target-current",
                    )

    def test_finding_task_can_repair_confirmed_requirement_or_safety_invariant_violation(self):
        scope = locked_scope()
        requirement_violation = finding_task(origin="unrelated-pre-existing")
        self.assertEqual(
            authorize_task_creation(
                requirement_violation, scope, locked_finding_ids=("FND-001",)
            ).task_origin,
            "finding",
        )
        safety_violation = finding_task(
            origin="unknown",
            requirement_refs=[],
            scope_refs=["release-scope"],
        )
        self.assertEqual(
            authorize_task_creation(
                safety_violation, scope, locked_finding_ids=("FND-001",)
            ).task_origin,
            "finding",
        )
        with self.assertRaisesRegex(TaskAuthorizationError, "only in_scope"):
            authorize_task_creation(
                finding_task(
                    origin="unrelated-pre-existing", contract_relation="outside_release"
                ),
                scope,
                locked_finding_ids=("FND-001",),
            )

    def test_lock_rejects_references_outside_source_and_accepted_inventory(self):
        with self.assertRaisesRegex(ScopeValidationError, "missing locked PLAN reference"):
            ReleaseScope.lock(
                source_contents=SOURCES,
                locked_at=NOW,
                plan_item_refs=("P999",),
                accepted_requirement_refs=("REQ-001",),
            )
        with self.assertRaisesRegex(ScopeValidationError, "accepted requirement"):
            ReleaseScope.lock(
                source_contents=SOURCES,
                locked_at=NOW,
                plan_item_refs=("P001",),
                requirement_refs=("REQ-999",),
                accepted_requirement_refs=("REQ-001",),
            )

    def test_user_amendment_task_requires_matching_recorded_authorization(self):
        scope = locked_scope()
        amendment = create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            new_source_contents={"MISSION.md": "expanded\n", "PLAN.md": SOURCES["PLAN.md"]},
            reason="User-authorized scope addition.",
            user_input_id="U0002",
        )
        amended_scope = scope.apply_amendment(amendment)
        task = planned_task(
            task_origin="user-amendment",
            release_scope_revision=2,
            plan_item_refs=[],
            authorization_input_id="U0002",
            scope_refs=["A001"],
        )
        self.assertEqual(authorize_task_creation(task, amended_scope).task_origin, "user-amendment")
        with self.assertRaisesRegex(
            TaskAuthorizationError, "latest scope-change amendment"
        ):
            authorize_task_creation(
                {**task, "scope_refs": ["release-scope"]}, amended_scope
            )
        with self.assertRaisesRegex(TaskAuthorizationError, "latest locked input"):
            authorize_task_creation(
                {**task, "authorization_input_id": "U0003"}, amended_scope
            )

    def test_user_amendment_task_rejects_input_missing_from_captured_inventory(self):
        scope = locked_scope()
        amendment = create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            new_source_contents={"MISSION.md": "expanded\n", "PLAN.md": SOURCES["PLAN.md"]},
            reason="User-authorized scope addition.",
            user_input_id="U0002",
            captured_input_ids={"U0002": {"source": "manager-chat"}},
        )
        amended_scope = scope.apply_amendment(amendment)
        task = planned_task(
            task_origin="user-amendment",
            release_scope_revision=2,
            plan_item_refs=[],
            authorization_input_id="U0002",
            scope_refs=["A001"],
        )
        self.assertEqual(
            authorize_task_creation(
                task,
                amended_scope,
                captured_input_ids={"U0002": {"source": "manager-chat"}},
            ).authorization_input_id,
            "U0002",
        )
        with self.assertRaisesRegex(TaskAuthorizationError, "captured input inventory"):
            authorize_task_creation(
                task,
                amended_scope,
                captured_input_ids={"U0001": {"source": "manager-chat"}},
            )

    def test_editorial_and_clarification_amendments_preserve_scope_authorization(self):
        scope = locked_scope()
        scope_change = create_amendment(
            scope,
            amendment_id="A001",
            kind="scope-change",
            new_source_contents={
                "MISSION.md": "expanded\n",
                "PLAN.md": SOURCES["PLAN.md"],
            },
            reason="User-authorized scope addition.",
            user_input_id="U0002",
        )
        scope_after_change = scope.apply_amendment(scope_change)
        task = planned_task(
            task_origin="user-amendment",
            release_scope_revision=2,
            plan_item_refs=[],
            authorization_input_id="U0002",
            scope_refs=["A001"],
        )

        for kind, contents, basis_refs in (
            (
                "editorial",
                {"MISSION.md": "expanded,\n", "PLAN.md": SOURCES["PLAN.md"]},
                (),
            ),
            (
                "clarification",
                {"MISSION.md": "expanded\n", "PLAN.md": "P001: bounded core clarified\n"},
                ("REQ-001",),
            ),
        ):
            with self.subTest(kind=kind):
                amendment = create_amendment(
                    scope_after_change,
                    amendment_id="A002",
                    kind=kind,
                    new_source_contents=contents,
                    reason=f"{kind} source update.",
                    basis_refs=basis_refs,
                )
                amended_scope = scope_after_change.apply_amendment(amendment)
                self.assertEqual(amended_scope.last_user_input_id, "U0002")
                with self.assertRaisesRegex(
                    TaskAuthorizationError, "latest scope-change amendment"
                ):
                    authorize_task_creation(task, amended_scope)

    def test_operational_task_cannot_change_product_or_release_artifact(self):
        scope = locked_scope()
        operational = planned_task(
            task_origin="operational",
            plan_item_refs=[],
            operational_reason="Collect validation evidence.",
            write_allow=["reports/FINAL.md"],
        )
        self.assertEqual(authorize_task_creation(operational, scope).task_origin, "operational")
        canonical_evidence = ".ai/herdr-dev-loop/loops/release-scope/validation/T001.log"
        self.assertEqual(
            authorize_task_creation(
                {**operational, "write_allow": [canonical_evidence]},
                scope,
            ).task_origin,
            "operational",
        )
        with self.assertRaisesRegex(TaskAuthorizationError, "product or release artifacts"):
            authorize_task_creation(
                {**operational, "write_allow": ["src/feature.py"]}, scope
            )

        for field_name in ("changed_files", "product_paths", "release_artifact_paths"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    TaskAuthorizationError, "product or release artifacts"
                ):
                    authorize_task_creation(
                        {**operational, field_name: ["unclassified-output.bin"]},
                        scope,
                    )

        with self.assertRaisesRegex(
            TaskAuthorizationError, "product or release artifacts"
        ):
            authorize_task_creation(
                {**operational, "changed_files": ["../reports/escape.bin"]},
                scope,
            )

    def test_task_provenance_scope_expanding_round_trips_and_legacy_defaults_false(self):
        record = finding_task(scope_expanding=True)
        provenance = TaskProvenance.from_record(record)
        self.assertTrue(provenance.scope_expanding)
        self.assertTrue(TaskProvenance.from_record(provenance.to_record()).scope_expanding)

        legacy_record = dict(record)
        legacy_record.pop("scope_expanding")
        legacy = TaskProvenance.from_record(legacy_record)
        self.assertFalse(legacy.scope_expanding)
        self.assertFalse(legacy.to_record()["scope_expanding"])

        with self.assertRaisesRegex(TaskAuthorizationError, "scope_expanding must be boolean"):
            TaskProvenance.from_record({**legacy_record, "scope_expanding": "true"})

    def test_legacy_unclassified_is_readable_only_in_legacy_scope(self):
        legacy = ReleaseScope(status="legacy-unlocked")
        task = planned_task(
            task_origin="legacy-unclassified",
            release_scope_revision=0,
            plan_item_refs=[],
        )
        self.assertEqual(validate_task_provenance(task, legacy).task_origin, "legacy-unclassified")
        with self.assertRaisesRegex(TaskAuthorizationError, "new-policy"):
            authorize_task_creation(task, locked_scope())

    def test_task_update_cannot_delete_or_rewrite_provenance(self):
        scope = locked_scope()
        original = planned_task()
        with self.assertRaisesRegex(ProvenanceMutationError, "immutable"):
            validate_provenance_update(
                original,
                {**original, "requirement_refs": ["REQ-001"], "plan_item_refs": []},
                scope,
            )


class SchemaTests(unittest.TestCase):
    def test_schema_files_are_valid_json_and_validate_canonical_records(self):
        scope_schema = json.loads((SCHEMAS / "release-scope.schema.json").read_text())
        provenance_schema = json.loads((SCHEMAS / "task-provenance.schema.json").read_text())
        scope = locked_scope()
        amendment = create_amendment(
            scope,
            amendment_id="A001",
            kind="editorial",
            new_source_contents={"MISSION.md": "new\n", "PLAN.md": SOURCES["PLAN.md"]},
            reason="Editorial correction.",
        )
        self.assertEqual(scope_schema["title"], "Herdr Dev Loop Release Scope Lock")
        self.assertEqual(provenance_schema["title"], "Herdr Dev Loop Task Provenance")
        if jsonschema is not None:
            jsonschema.Draft202012Validator(scope_schema).validate(scope.to_record())
            jsonschema.Draft202012Validator(scope_schema).validate(
                ReleaseScope.from_record(
                    {
                        **scope.to_record(),
                        "last_user_input_id": "U0002",
                        "consumed_user_input_ids": ["U0001", "U0002"],
                    }
                ).to_record()
            )
            jsonschema.Draft202012Validator(provenance_schema).validate(
                TaskProvenance.from_record(finding_task()).to_record()
            )
            jsonschema.Draft202012Validator(provenance_schema).validate(
                TaskProvenance.from_record(finding_task(scope_expanding=True)).to_record()
            )
        self.assertEqual(
            type(amendment.to_record()["new_source_digests"]).__name__, "dict"
        )


if __name__ == "__main__":
    unittest.main()
