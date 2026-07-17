from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import sys
import unittest
from pathlib import Path

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # pragma: no cover - minimal installations fail explicitly
    jsonschema = None
    Registry = None
    Resource = None
    DRAFT202012 = None


SKILL_ROOT = Path(__file__).parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
REFERENCE_SCHEMA = SKILL_ROOT / "references" / "schemas" / "review-epoch.schema.json"
PUBLIC_SCHEMA = SKILL_ROOT / "schemas" / "review-epoch.schema.json"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.review import plan_review_group  # noqa: E402
from hloop_lib.review_epoch import (  # noqa: E402
    AuditProcessPlan,
    CapacityLease,
    EpochCapacityLedger,
    EpochExecutionOutcome,
    EpochExecutionPlan,
    ReviewEpochError,
    ReviewEpochCollection,
    ReviewEpochPlan,
    canonical_digest,
    create_successor_collection,
    create_successor_revision,
    inherit_artifact,
    validate_successor_revision,
)


def digest(label: str) -> str:
    return canonical_digest({"identity": label})


def reviewer_execution(
    execution_id: str = "R001",
    *,
    attempt: str = "R001-A001",
    protocol: str = "native",
) -> EpochExecutionPlan:
    prefix = execution_id
    coordinator = f"{prefix}-coordinator"
    return EpochExecutionPlan(
        execution_id=execution_id,
        attempt_id=attempt,
        source_kind="reviewer",
        protocol=protocol,
        independence_key=f"reviewer:{execution_id}",
        artifact_ref=f"reviews/{execution_id}/MANIFEST.json",
        processes=(
            AuditProcessPlan(
                process_id=coordinator,
                process_kind="coordinator",
                agent_label=f"{prefix}-coordinator-agent",
                provider="codex",
                model="gpt-5.6-sol",
                effort="xhigh",
            ),
            AuditProcessPlan(
                process_id=f"{prefix}-lane-correctness",
                process_kind="discovery",
                agent_label=f"{prefix}-correctness-agent",
                provider="codex",
                model="gpt-5.6-sol",
                effort="xhigh",
                parent_process_id=coordinator,
                lane_id="product-correctness",
            ),
            AuditProcessPlan(
                process_id=f"{prefix}-verifier",
                process_kind="verifier",
                agent_label=f"{prefix}-verifier-agent",
                provider="codex",
                model="gpt-5.6-sol",
                effort="xhigh",
                parent_process_id=coordinator,
            ),
        ),
    )


def gap_execution(execution_id: str = "G001") -> EpochExecutionPlan:
    prefix = execution_id
    coordinator = f"{prefix}-coordinator"
    return EpochExecutionPlan(
        execution_id=execution_id,
        attempt_id=f"{execution_id}-A001",
        source_kind="gap",
        protocol="native",
        independence_key=f"gap:{execution_id}",
        artifact_ref=f"gaps/{execution_id}/MANIFEST.json",
        processes=(
            AuditProcessPlan(
                process_id=coordinator,
                process_kind="coordinator",
                agent_label=f"{prefix}-coordinator-agent",
                provider="codex",
                model="gpt-5.6-sol",
                effort="xhigh",
            ),
            AuditProcessPlan(
                process_id=f"{prefix}-lane-coverage",
                process_kind="discovery",
                agent_label=f"{prefix}-coverage-agent",
                provider="codex",
                model="gpt-5.6-sol",
                effort="xhigh",
                parent_process_id=coordinator,
                lane_id="requirement-coverage",
            ),
            AuditProcessPlan(
                process_id=f"{prefix}-challenge",
                process_kind="challenge",
                agent_label=f"{prefix}-challenge-agent",
                provider="codex",
                model="gpt-5.6-sol",
                effort="xhigh",
                parent_process_id=coordinator,
                lane_id="coverage-challenge",
            ),
        ),
    )


def epoch_plan(
    *,
    budget: int = 12,
    executions: tuple[EpochExecutionPlan, ...] | None = None,
) -> ReviewEpochPlan:
    return ReviewEpochPlan(
        epoch_id="E001",
        epoch_revision=1,
        base_sha="base-abc",
        target_sha="target-def",
        scope_revision=3,
        source_snapshot_revision=7,
        scope_digest=digest("scope"),
        source_refs=("MISSION.md", "PLAN.md", "REQ-003"),
        policy_digest=digest("policy"),
        validation_identity=digest("validation"),
        audit_agent_budget=budget,
        required_executions=executions or (reviewer_execution(), gap_execution()),
    )


def completed_epoch_collection() -> ReviewEpochCollection:
    plan = epoch_plan()
    collection = ReviewEpochCollection.create(plan)
    for execution in plan.required_executions:
        process_ids = tuple(item.process_id for item in execution.processes)
        capacity = collection.capacity.reserve(
            plan,
            lease_id=f"lease-{execution.execution_id}",
            execution_id=execution.execution_id,
            process_ids=process_ids,
            expires_at="2026-07-17T08:00:00Z",
        ).mark_running(
            f"lease-{execution.execution_id}"
        ).mark_terminal(
            f"lease-{execution.execution_id}",
            reason="process tree exited",
            process_exit_confirmed=True,
        )
        collection = collection.with_capacity(capacity)
        collection = collection.record_outcome(
            EpochExecutionOutcome.for_plan(
                plan,
                execution.execution_id,
                artifact_digest=digest(f"artifact-{execution.execution_id}"),
                artifact_complete=True,
                completed_process_ids=process_ids,
                status="succeeded",
                terminal_at="2026-07-17T08:01:00Z",
            )
        )
    return collection


def offline_validator(schema_path: Path):
    if (
        jsonschema is None
        or Registry is None
        or Resource is None
        or DRAFT202012 is None
    ):
        raise AssertionError("review epoch schema tests require jsonschema")

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


class ReviewEpochIdentityTests(unittest.TestCase):
    def test_plan_digest_round_trip_freezes_every_audit_identity_axis(self):
        plan = epoch_plan()
        record = json.loads(json.dumps(plan.to_record()))

        restored = ReviewEpochPlan.from_record(record)

        self.assertEqual(restored, plan)
        self.assertEqual(restored.plan_digest, plan.plan_digest)
        self.assertEqual(record["topology_digest"], plan.topology_digest)
        identity = plan.artifact_identity("R001").to_record()
        self.assertEqual(identity["epoch_id"], "E001")
        self.assertEqual(identity["epoch_revision"], 1)
        self.assertEqual(identity["attempt_id"], "R001-A001")
        self.assertEqual(identity["plan_digest"], plan.plan_digest)
        self.assertEqual(
            identity["execution_digest"], plan.execution("R001").execution_digest
        )

    def test_target_scope_policy_validation_lane_model_and_effort_change_digest(self):
        plan = epoch_plan()
        reviewer = plan.execution("R001")
        lane = reviewer.processes[1]

        variants = (
            replace(plan, target_sha="another-target"),
            replace(plan, scope_digest=digest("another-scope")),
            replace(plan, policy_digest=digest("another-policy")),
            replace(plan, validation_identity=digest("another-validation")),
            replace(
                plan,
                required_executions=(
                    replace(
                        reviewer,
                        processes=(
                            reviewer.processes[0],
                            replace(lane, lane_id="security"),
                            reviewer.processes[2],
                        ),
                    ),
                    plan.execution("G001"),
                ),
            ),
            replace(
                plan,
                required_executions=(
                    replace(
                        reviewer,
                        processes=(
                            reviewer.processes[0],
                            replace(lane, model="gpt-5.6-terra"),
                            reviewer.processes[2],
                        ),
                    ),
                    plan.execution("G001"),
                ),
            ),
            replace(
                plan,
                required_executions=(
                    replace(
                        reviewer,
                        processes=(
                            reviewer.processes[0],
                            replace(lane, effort="max"),
                            reviewer.processes[2],
                        ),
                    ),
                    plan.execution("G001"),
                ),
            ),
        )

        self.assertEqual(len({item.plan_digest for item in variants}), len(variants))
        self.assertNotIn(plan.plan_digest, {item.plan_digest for item in variants})

    def test_epoch_and_nested_plan_objects_are_frozen(self):
        plan = epoch_plan()
        with self.assertRaises(FrozenInstanceError):
            plan.target_sha = "mutated"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            plan.required_executions[0].protocol = "mutated"  # type: ignore[misc]

    def test_execution_and_independence_identity_cannot_be_double_counted(self):
        reviewer = reviewer_execution()
        duplicate_execution = replace(reviewer, independence_key="other-source")
        with self.assertRaisesRegex(ReviewEpochError, "duplicate execution_id"):
            epoch_plan(executions=(reviewer, duplicate_execution))

        other = reviewer_execution("R002", attempt="R002-A001")
        other = replace(other, independence_key=reviewer.independence_key)
        with self.assertRaisesRegex(ReviewEpochError, "duplicate independence_key"):
            epoch_plan(executions=(reviewer, other))

    def test_tampered_execution_topology_and_plan_digests_fail_closed(self):
        record = epoch_plan().to_record()
        record["required_executions"][0]["processes"][1]["model"] = "tampered"
        with self.assertRaisesRegex(ReviewEpochError, "execution_digest"):
            ReviewEpochPlan.from_record(record)

        record = epoch_plan().to_record()
        record["target_sha"] = "tampered-target"
        with self.assertRaisesRegex(ReviewEpochError, "plan_digest"):
            ReviewEpochPlan.from_record(record)

        record = epoch_plan().to_record()
        record["record_type"] = "review_epoch_capacity"
        with self.assertRaisesRegex(ReviewEpochError, "record_type"):
            ReviewEpochPlan.from_record(record)

    def test_deserialization_requires_persisted_identity_digests(self):
        for field_name in ("plan_digest", "topology_digest"):
            with self.subTest(field_name=field_name):
                record = epoch_plan().to_record()
                del record[field_name]
                with self.assertRaisesRegex(ReviewEpochError, field_name):
                    ReviewEpochPlan.from_record(record)

        record = epoch_plan().to_record()
        del record["required_executions"][0]["execution_digest"]
        with self.assertRaisesRegex(ReviewEpochError, "execution_digest"):
            ReviewEpochPlan.from_record(record)

    def test_existing_review_group_contract_remains_unchanged(self):
        group = plan_review_group("swarm", head_sha="target-def", probe_count=6)
        self.assertEqual(len(group.expected_lanes), 6)
        self.assertEqual(group.to_record()["head_sha"], "target-def")


class ReviewEpochSuccessorTests(unittest.TestCase):
    def test_same_sha_supplement_creates_append_only_successor_with_inheritance(self):
        parent = epoch_plan()
        parent_record = parent.to_record()
        inherited = inherit_artifact(
            parent, "R001", artifact_digest=digest("review-manifest-bytes")
        )
        supplement = reviewer_execution(
            "R002", attempt="R002-A001", protocol="native-challenge"
        )

        successor = create_successor_revision(
            parent,
            additional_executions=(supplement,),
            inherited_artifacts=(inherited,),
        )

        validate_successor_revision(parent, successor)
        self.assertEqual(parent.to_record(), parent_record)
        self.assertEqual(successor.epoch_revision, 2)
        self.assertEqual(successor.target_sha, parent.target_sha)
        self.assertEqual(successor.parent_plan_digest, parent.plan_digest)
        self.assertEqual(successor.required_executions[:2], parent.required_executions)
        self.assertEqual(successor.additional_execution_ids, ("R002",))
        self.assertEqual(successor.inherited_artifacts, (inherited,))
        self.assertEqual(inherited.execution_digest, parent.execution("R001").execution_digest)

    def test_successor_rejects_changed_sha_scope_policy_or_parent_execution(self):
        parent = epoch_plan()
        successor = create_successor_revision(
            parent, additional_executions=(reviewer_execution("R002"),)
        )
        for candidate in (
            replace(successor, target_sha="new-target"),
            replace(successor, scope_digest=digest("new-scope")),
            replace(successor, policy_digest=digest("new-policy")),
            replace(
                successor,
                required_executions=(
                    replace(parent.required_executions[0], protocol="changed"),
                    *successor.required_executions[1:],
                ),
            ),
        ):
            with self.subTest(plan_digest=candidate.plan_digest):
                with self.assertRaises(ReviewEpochError):
                    validate_successor_revision(parent, candidate)

    def test_successor_rejects_forged_inherited_artifact_identity(self):
        parent = epoch_plan()
        inherited = inherit_artifact(
            parent, "R001", artifact_digest=digest("review-manifest")
        )
        successor = create_successor_revision(
            parent,
            additional_executions=(reviewer_execution("R002"),),
            inherited_artifacts=(inherited,),
        )
        forged = replace(inherited, execution_digest=digest("wrong-execution"))

        with self.assertRaisesRegex(ReviewEpochError, "execution identity"):
            validate_successor_revision(
                parent, replace(successor, inherited_artifacts=(forged,))
            )

    def test_successor_requires_real_addition_and_monotonic_revision(self):
        parent = epoch_plan()
        with self.assertRaisesRegex(ReviewEpochError, "additional executions"):
            create_successor_revision(parent, additional_executions=())
        successor = create_successor_revision(
            parent, additional_executions=(reviewer_execution("R002"),)
        )
        with self.assertRaisesRegex(ReviewEpochError, "increase by exactly one"):
            validate_successor_revision(parent, replace(successor, epoch_revision=3))


class EpochCapacityLeaseTests(unittest.TestCase):
    def test_successor_capacity_ledger_requires_exact_predecessor(self):
        parent = epoch_plan(budget=1)
        successor = create_successor_revision(
            parent, additional_executions=(reviewer_execution("R002"),)
        )

        with self.assertRaisesRegex(ReviewEpochError, "requires predecessor"):
            successor.capacity_ledger()
        with self.assertRaisesRegex(ReviewEpochError, "successor lineage"):
            successor.capacity_ledger(
                replace(parent.capacity_ledger(), plan_digest=digest("unrelated"))
            )
        predecessor = parent.capacity_ledger().reserve(
            parent,
            lease_id="lease-parent",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T10:00:00Z",
        )
        ledger = successor.capacity_ledger(predecessor)
        self.assertEqual(ledger.available_slots, 0)
        with self.assertRaisesRegex(ReviewEpochError, "audit_agent_budget"):
            ledger.reserve(
                successor,
                lease_id="lease-successor",
                execution_id="R002",
                process_ids=("R002-coordinator",),
                expires_at="2026-07-17T10:00:00Z",
            )

    def test_successor_shares_every_ancestor_lease_and_quarantine(self):
        revision_one = epoch_plan(budget=3)
        ledger = revision_one.capacity_ledger().reserve(
            revision_one,
            lease_id="lease-starting",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T10:00:00Z",
        )

        revision_two = create_successor_revision(
            revision_one,
            additional_executions=(reviewer_execution("R002"),),
        )
        ledger = revision_two.capacity_ledger(ledger).reserve(
            revision_two,
            lease_id="lease-running",
            execution_id="R002",
            process_ids=("R002-coordinator",),
            expires_at="2026-07-17T10:00:00Z",
        ).mark_running("lease-running")

        revision_three = create_successor_revision(
            revision_two,
            additional_executions=(reviewer_execution("R003"),),
        )
        ledger = revision_three.capacity_ledger(ledger).reserve(
            revision_three,
            lease_id="lease-quarantined",
            execution_id="R003",
            process_ids=("R003-coordinator",),
            expires_at="2026-07-17T08:00:00Z",
        ).mark_running("lease-quarantined").mark_expired_quarantined(
            "lease-quarantined", now="2026-07-17T08:00:00Z"
        )

        revision_four = create_successor_revision(
            revision_three,
            additional_executions=(reviewer_execution("R004"),),
        )
        ledger = revision_four.capacity_ledger(ledger)

        self.assertEqual(
            ledger.ancestor_plan_digests,
            (
                revision_one.plan_digest,
                revision_two.plan_digest,
                revision_three.plan_digest,
            ),
        )
        self.assertEqual(
            {lease.epoch_revision: lease.status for lease in ledger.leases},
            {1: "starting", 2: "running", 3: "expired_quarantined"},
        )
        self.assertEqual(ledger.reserved_slots, 3)
        self.assertEqual(ledger.live_slots, 2)
        self.assertEqual(ledger.available_slots, 0)
        self.assertTrue(ledger.blocks_new_starts)
        restored = EpochCapacityLedger.from_record(
            json.loads(json.dumps(ledger.to_record()))
        )
        restored.validate_for_plan(revision_four)
        self.assertEqual(restored, ledger)
        tampered = ledger.to_record()
        tampered["ancestor_plan_digests"][-1] = digest("wrong-ancestor")
        with self.assertRaisesRegex(ReviewEpochError, "epoch lineage"):
            EpochCapacityLedger.from_record(tampered)
        with self.assertRaisesRegex(ReviewEpochError, "blocks every new start"):
            ledger.reserve(
                revision_four,
                lease_id="lease-successor",
                execution_id="R004",
                process_ids=("R004-coordinator",),
                expires_at="2026-07-17T11:00:00Z",
            )

        ledger = ledger.mark_terminal(
            "lease-quarantined",
            reason="forced abort acknowledged",
            forced_abort_acknowledged=True,
        )
        self.assertFalse(ledger.blocks_new_starts)
        ledger = ledger.reserve(
            revision_four,
            lease_id="lease-successor",
            execution_id="R004",
            process_ids=("R004-coordinator",),
            expires_at="2026-07-17T11:00:00Z",
        )
        self.assertEqual(ledger.reserved_slots, 3)

    def test_one_aggregate_budget_counts_every_audit_process_kind(self):
        plan = epoch_plan(budget=6)
        ledger = plan.capacity_ledger()
        reviewer_ids = [item.process_id for item in plan.execution("R001").processes]
        gap_ids = [item.process_id for item in plan.execution("G001").processes]

        ledger = ledger.reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=reviewer_ids,
            expires_at="2026-07-17T08:00:00Z",
        )
        ledger = ledger.reserve(
            plan,
            lease_id="lease-gap",
            execution_id="G001",
            process_ids=gap_ids,
            expires_at="2026-07-17T08:00:00Z",
        )

        kinds = Counter(
            reservation.process_kind
            for lease in ledger.leases
            for reservation in lease.reservations
        )
        self.assertEqual(
            kinds,
            Counter(
                {"coordinator": 2, "discovery": 2, "verifier": 1, "challenge": 1}
            ),
        )
        self.assertEqual(ledger.reserved_slots, 6)
        self.assertEqual(ledger.available_slots, 0)
        ledger = ledger.mark_running("lease-reviewer").mark_running("lease-gap")
        self.assertEqual(ledger.live_slots, 6)

    def test_aggregate_budget_rejects_cross_execution_overcommit(self):
        plan = epoch_plan(budget=5)
        reviewer_ids = [item.process_id for item in plan.execution("R001").processes]
        gap_ids = [item.process_id for item in plan.execution("G001").processes]
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=reviewer_ids,
            expires_at="2026-07-17T08:00:00Z",
        )

        with self.assertRaisesRegex(ReviewEpochError, "audit_agent_budget"):
            ledger.reserve(
                plan,
                lease_id="lease-gap",
                execution_id="G001",
                process_ids=gap_ids,
                expires_at="2026-07-17T08:00:00Z",
            )

    def test_expiry_quarantines_capacity_and_blocks_all_new_epoch_starts(self):
        plan = epoch_plan(budget=6)
        reviewer_ids = [item.process_id for item in plan.execution("R001").processes]
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=reviewer_ids,
            expires_at="2026-07-17T08:00:00Z",
        ).mark_running("lease-reviewer")

        with self.assertRaisesRegex(ReviewEpochError, "has not expired"):
            ledger.mark_expired_quarantined(
                "lease-reviewer", now="2026-07-17T07:59:59Z"
            )
        ledger = ledger.mark_expired_quarantined(
            "lease-reviewer", now="2026-07-17T08:00:00Z"
        )

        lease = ledger.lease("lease-reviewer")
        self.assertEqual(lease.status, "expired_quarantined")
        self.assertTrue(lease.credential_revoked)
        self.assertEqual(ledger.reserved_slots, 3)
        self.assertEqual(ledger.live_slots, 3)
        self.assertTrue(ledger.blocks_new_starts)
        with self.assertRaisesRegex(ReviewEpochError, "blocks every new start"):
            ledger.reserve(
                plan,
                lease_id="lease-gap",
                execution_id="G001",
                process_ids=("G001-coordinator",),
                expires_at="2026-07-17T09:00:00Z",
            )
        with self.assertRaisesRegex(ReviewEpochError, "capacity remains held"):
            ledger.mark_terminal("lease-reviewer", reason="timeout cleanup")

        ledger = ledger.mark_terminal(
            "lease-reviewer",
            reason="provider process exited",
            process_exit_confirmed=True,
        )
        self.assertEqual(ledger.reserved_slots, 0)
        self.assertFalse(ledger.blocks_new_starts)
        ledger = ledger.reserve(
            plan,
            lease_id="lease-gap",
            execution_id="G001",
            process_ids=("G001-coordinator",),
            expires_at="2026-07-17T09:00:00Z",
        )
        self.assertEqual(ledger.reserved_slots, 1)

    def test_forced_abort_acknowledgement_revokes_credential_and_releases(self):
        plan = epoch_plan()
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T08:00:00Z",
        ).mark_running("lease-reviewer")

        ledger = ledger.mark_terminal(
            "lease-reviewer",
            reason="forced abort acknowledged",
            forced_abort_acknowledged=True,
        )

        lease = ledger.lease("lease-reviewer")
        self.assertEqual(lease.status, "terminal")
        self.assertTrue(lease.credential_revoked)
        self.assertTrue(lease.forced_abort_acknowledged)
        self.assertEqual(ledger.reserved_slots, 0)

    def test_lease_and_ledger_round_trip_recompute_derived_capacity(self):
        plan = epoch_plan()
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=("R001-coordinator", "R001-lane-correctness"),
            expires_at="2026-07-17T08:00:00+00:00",
        )
        restored = EpochCapacityLedger.from_record(
            json.loads(json.dumps(ledger.to_record()))
        )
        restored.validate_for_plan(plan)
        self.assertEqual(restored, ledger)
        self.assertEqual(restored.lease("lease-reviewer").expires_at, "2026-07-17T08:00:00Z")

        tampered = ledger.to_record()
        tampered["reserved_slots"] = 0
        with self.assertRaisesRegex(ReviewEpochError, "reserved_slots"):
            EpochCapacityLedger.from_record(tampered)
        tampered = ledger.lease("lease-reviewer").to_record()
        tampered["reserved_slots"] = 1
        with self.assertRaisesRegex(ReviewEpochError, "reserved_slots"):
            CapacityLease.from_record(tampered)

    def test_active_process_cannot_be_reserved_twice(self):
        plan = epoch_plan()
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-one",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T08:00:00Z",
        )
        with self.assertRaisesRegex(ReviewEpochError, "already has an active lease"):
            ledger.reserve(
                plan,
                lease_id="lease-two",
                execution_id="R001",
                process_ids=("R001-coordinator",),
                expires_at="2026-07-17T08:00:00Z",
            )

    def test_terminal_process_identity_cannot_be_reserved_again(self):
        plan = epoch_plan()
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-one",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T08:00:00Z",
        ).mark_terminal(
            "lease-one",
            reason="process exited",
            process_exit_confirmed=True,
        )

        with self.assertRaisesRegex(ReviewEpochError, "terminal lease history"):
            ledger.reserve(
                plan,
                lease_id="lease-retry",
                execution_id="R001",
                process_ids=("R001-coordinator",),
                expires_at="2026-07-17T09:00:00Z",
            )


class ReviewEpochCollectionTests(unittest.TestCase):
    def completed_collection(self) -> ReviewEpochCollection:
        return completed_epoch_collection()

    def test_all_successful_complete_executions_are_ready_and_round_trip(self):
        collection = self.completed_collection()
        self.assertEqual(collection.status, "ready_to_triage")
        restored = ReviewEpochCollection.from_record(
            json.loads(json.dumps(collection.to_record()))
        )
        self.assertEqual(restored, collection)
        self.assertEqual(
            {item.independence_key for item in restored.execution_outcomes},
            {"reviewer:R001", "gap:G001"},
        )

    def test_failed_or_artifact_incomplete_execution_never_reaches_triage(self):
        plan = epoch_plan()
        execution = plan.execution("R001")
        process_ids = tuple(item.process_id for item in execution.processes)
        collection = ReviewEpochCollection.create(plan)
        capacity = collection.capacity.reserve(
            plan,
            lease_id="lease-R001",
            execution_id="R001",
            process_ids=process_ids,
            expires_at="2026-07-17T08:00:00Z",
        ).mark_terminal(
            "lease-R001",
            reason="failed",
            process_exit_confirmed=True,
        )
        collection = collection.with_capacity(capacity).record_outcome(
            EpochExecutionOutcome.for_plan(
                plan,
                "R001",
                artifact_complete=False,
                completed_process_ids=process_ids,
                status="failed",
                terminal_at="2026-07-17T08:01:00Z",
            )
        )
        self.assertEqual(collection.status, "incomplete")
        with self.assertRaisesRegex(ReviewEpochError, "before ready_to_triage"):
            collection.close(reason="incorrect clean close")

    def test_terminal_outcome_rejects_nonterminal_or_missing_process_leases(self):
        plan = epoch_plan()
        execution = plan.execution("R001")
        process_ids = tuple(item.process_id for item in execution.processes)
        collection = ReviewEpochCollection.create(plan)
        capacity = collection.capacity.reserve(
            plan,
            lease_id="lease-R001",
            execution_id="R001",
            process_ids=process_ids,
            expires_at="2026-07-17T08:00:00Z",
        ).mark_running("lease-R001")
        collection = collection.with_capacity(capacity)
        with self.assertRaisesRegex(ReviewEpochError, "nonterminal or missing"):
            collection.record_outcome(
                EpochExecutionOutcome.for_plan(
                    plan,
                    "R001",
                    artifact_complete=False,
                    completed_process_ids=(),
                    status="timeout",
                    terminal_at="2026-07-17T08:01:00Z",
                )
            )

    def test_terminal_outcome_retry_is_exact_and_changed_retry_fails(self):
        collection = self.completed_collection()
        first = collection.execution_outcomes[0]
        self.assertIs(collection.record_outcome(first), collection)
        with self.assertRaisesRegex(ReviewEpochError, "changed on retry"):
            collection.record_outcome(replace(first, terminal_at="2026-07-17T09:00:00Z"))

    def test_successor_collection_retry_restores_exact_same_state(self):
        parent = self.completed_collection()
        outcomes = {
            item.execution_id: item for item in parent.execution_outcomes
        }
        inherited = tuple(
            inherit_artifact(
                parent.plan,
                execution.execution_id,
                artifact_digest=outcomes[execution.execution_id].artifact_digest,
            )
            for execution in parent.plan.required_executions
        )
        successor_plan = create_successor_revision(
            parent.plan,
            additional_executions=(reviewer_execution("R002", attempt="R002-A001"),),
            inherited_artifacts=inherited,
        )
        superseded, successor = create_successor_collection(parent, successor_plan)
        retried_parent, retried_successor = create_successor_collection(
            superseded, successor_plan
        )
        self.assertEqual(retried_parent, superseded)
        self.assertEqual(retried_successor, successor)


class ReviewEpochSchemaTests(unittest.TestCase):
    def assert_schema_and_python_reject(
        self,
        validators,
        record,
        loader,
        error_pattern: str,
        *,
        case_name: str,
    ) -> None:
        for schema_path, validator in validators:
            with self.subTest(schema=schema_path.name, case=case_name):
                self.assertTrue(list(validator.iter_errors(record)))
                with self.assertRaisesRegex(ReviewEpochError, error_pattern):
                    loader(record)

    def test_plan_top_level_python_and_schema_validation_have_parity(self):
        valid = epoch_plan().to_record()
        validators = tuple(
            (schema_path, offline_validator(schema_path))
            for schema_path in (REFERENCE_SCHEMA, PUBLIC_SCHEMA)
        )

        for schema_path, validator in validators:
            with self.subTest(schema=schema_path.name, case="valid"):
                self.assertEqual(list(validator.iter_errors(valid)), [])
                self.assertEqual(ReviewEpochPlan.from_record(valid), epoch_plan())

        for field_name in valid:
            record = deepcopy(valid)
            del record[field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                ReviewEpochPlan.from_record,
                field_name,
                case_name=f"missing-{field_name}",
            )

        self.assert_schema_and_python_reject(
            validators,
            {**valid, "record_type": "review_epoch_capacity"},
            ReviewEpochPlan.from_record,
            "record_type",
            case_name="invalid-record-type",
        )
        self.assert_schema_and_python_reject(
            validators,
            {**valid, "unexpected_top_level": True},
            ReviewEpochPlan.from_record,
            "unknown fields",
            case_name="unknown-property",
        )

    def test_plan_nested_record_property_sets_match_canonical_schema(self):
        validators = tuple(
            (schema_path, offline_validator(schema_path))
            for schema_path in (REFERENCE_SCHEMA, PUBLIC_SCHEMA)
        )
        parent = epoch_plan()
        successor = create_successor_revision(
            parent,
            additional_executions=(reviewer_execution("R002"),),
            inherited_artifacts=(
                inherit_artifact(
                    parent,
                    "R001",
                    artifact_digest=digest("review-manifest"),
                ),
            ),
        )

        plan_record = parent.to_record()
        execution_fields = tuple(plan_record["required_executions"][0])
        for field_name in execution_fields:
            record = deepcopy(plan_record)
            del record["required_executions"][0][field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                ReviewEpochPlan.from_record,
                field_name,
                case_name=f"execution-missing-{field_name}",
            )

        process_fields = tuple(
            plan_record["required_executions"][0]["processes"][0]
        )
        for field_name in process_fields:
            record = deepcopy(plan_record)
            del record["required_executions"][0]["processes"][0][field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                ReviewEpochPlan.from_record,
                field_name,
                case_name=f"audit-process-missing-{field_name}",
            )

        successor_record = successor.to_record()
        inherited_fields = tuple(successor_record["inherited_artifacts"][0])
        for field_name in inherited_fields:
            record = deepcopy(successor_record)
            del record["inherited_artifacts"][0][field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                ReviewEpochPlan.from_record,
                field_name,
                case_name=f"inherited-artifact-missing-{field_name}",
            )

        for case_name, base_record, path in (
            (
                "execution-unknown-property",
                plan_record,
                ("required_executions", 0),
            ),
            (
                "audit-process-unknown-property",
                plan_record,
                ("required_executions", 0, "processes", 0),
            ),
            (
                "inherited-artifact-unknown-property",
                successor_record,
                ("inherited_artifacts", 0),
            ),
        ):
            record = deepcopy(base_record)
            nested = record
            for key in path:
                nested = nested[key]
            nested["unexpected_nested"] = True
            self.assert_schema_and_python_reject(
                validators,
                record,
                ReviewEpochPlan.from_record,
                "unknown fields",
                case_name=case_name,
            )

    def test_capacity_nested_record_property_sets_match_canonical_schema(self):
        validators = tuple(
            (schema_path, offline_validator(schema_path))
            for schema_path in (REFERENCE_SCHEMA, PUBLIC_SCHEMA)
        )
        plan = epoch_plan()
        valid = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T08:00:00Z",
        ).to_record()

        for field_name in tuple(valid):
            record = deepcopy(valid)
            del record[field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                EpochCapacityLedger.from_record,
                field_name,
                case_name=f"capacity-ledger-missing-{field_name}",
            )

        for field_name in tuple(valid["leases"][0]):
            record = deepcopy(valid)
            del record["leases"][0][field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                EpochCapacityLedger.from_record,
                field_name,
                case_name=f"capacity-lease-missing-{field_name}",
            )

        for field_name in tuple(valid["leases"][0]["reservations"][0]):
            record = deepcopy(valid)
            del record["leases"][0]["reservations"][0][field_name]
            self.assert_schema_and_python_reject(
                validators,
                record,
                EpochCapacityLedger.from_record,
                field_name,
                case_name=f"lease-reservation-missing-{field_name}",
            )

        for case_name, path in (
            ("capacity-ledger-unknown-property", ()),
            ("capacity-lease-unknown-property", ("leases", 0)),
            (
                "lease-reservation-unknown-property",
                ("leases", 0, "reservations", 0),
            ),
        ):
            record = deepcopy(valid)
            nested = record
            for key in path:
                nested = nested[key]
            nested["unexpected_nested"] = True
            self.assert_schema_and_python_reject(
                validators,
                record,
                EpochCapacityLedger.from_record,
                "unknown fields",
                case_name=case_name,
            )

    def test_canonical_and_public_schemas_validate_all_epoch_records(self):
        plan = epoch_plan()
        ledger = plan.capacity_ledger().reserve(
            plan,
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=("R001-coordinator", "R001-verifier"),
            expires_at="2026-07-17T08:00:00Z",
        )
        successor = create_successor_revision(
            plan, additional_executions=(reviewer_execution("R002"),)
        )
        successor_ledger = successor.capacity_ledger(ledger)
        completed = completed_epoch_collection()
        for schema_path in (REFERENCE_SCHEMA, PUBLIC_SCHEMA):
            validator = offline_validator(schema_path)
            for record in (
                plan.to_record(),
                ledger.to_record(),
                successor.to_record(),
                successor_ledger.to_record(),
                completed.execution_outcomes[0].to_record(),
                completed.to_record(),
            ):
                with self.subTest(schema=schema_path.name, record=record["record_type"]):
                    self.assertEqual(list(validator.iter_errors(record)), [])

    def test_schema_rejects_missing_model_and_unacknowledged_terminal_release(self):
        validator = offline_validator(REFERENCE_SCHEMA)
        plan_record = epoch_plan().to_record()
        del plan_record["required_executions"][0]["processes"][0]["model"]
        self.assertTrue(list(validator.iter_errors(plan_record)))

        ledger = epoch_plan().capacity_ledger().reserve(
            epoch_plan(),
            lease_id="lease-reviewer",
            execution_id="R001",
            process_ids=("R001-coordinator",),
            expires_at="2026-07-17T08:00:00Z",
        )
        ledger_record = ledger.to_record()
        lease_record = ledger_record["leases"][0]
        lease_record["status"] = "terminal"
        lease_record["terminal_reason"] = "released without evidence"
        self.assertTrue(list(validator.iter_errors(ledger_record)))


if __name__ == "__main__":
    unittest.main()
