from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # pragma: no cover - minimal installations fail explicitly
    jsonschema = None
    Registry = None
    Resource = None
    DRAFT202012 = None


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
SKILL_ROOT = SCRIPT.parents[1]
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader(
    "hloop_review_remediation_cli_v053", str(SCRIPT)
)
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)

from hloop_lib.review import (  # noqa: E402
    ExternalReviewProtocolAdapter,
    ReviewManifest,
    ReviewModelError,
    plan_review_group,
    plan_verification,
    validate_externally_planned_review_manifest,
)
from hloop_lib.review_epoch import (  # noqa: E402
    AuditProcessPlan,
    EpochExecutionOutcome,
    EpochExecutionPlan,
    ReviewEpochPlan,
    canonical_digest,
)

fixtures = __import__(
    "skills.herdr-dev-loop.tests.test_remediation_v053",
    fromlist=["candidate", "locked_scope", "remediation_task"],
)


class ReviewRemediationCliV053Tests(unittest.TestCase):
    namespace = "review-remediation-cli-v053"

    def setUp(self) -> None:
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace(self.namespace)

    def tearDown(self) -> None:
        hloop.configure_loop_namespace(self.previous_namespace)

    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()

    def make_repo(self, root: Path) -> tuple[Path, str, dict]:
        repo = root / "repo"
        repo.mkdir()
        self.git(repo, "init", "--initial-branch=main")
        self.git(repo, "config", "user.name", "Test")
        self.git(repo, "config", "user.email", "test@example.com")
        (repo / ".gitignore").write_text(".ai/\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.git(repo, "add", ".gitignore", "src/feature.py")
        self.git(repo, "commit", "-m", "base")
        head = self.git(repo, "rev-parse", "HEAD")
        state = {
            "state_format_version": 3,
            "schema_revision": 3,
            "namespace": self.namespace,
            "loop_path": hloop.LOOP_DIR.as_posix(),
            "run_id": "run-v053",
            "skill_version": "0.5.3",
            "goal_id": "epoch-test",
            "phase": "dispatching",
            "base_branch": "main",
            "integration_branch": "main",
            "persistence": "local-only",
            "branch_strategy": "integration",
            "worker_protocol": "native",
            "worker_qa_profile": "repo-default",
            "worker_agent_provider": "codex",
            "worker_agent_model": "gpt-5.6-sol",
            "review_policy": {"cadence": "batch", "max_fix_rounds": 2},
            "tasks": {},
            "batches": {},
            "reviews": {},
            "gaps": {},
            "advice": {},
            "decisions": {},
            "review_epochs": {
                "active_epoch_id": "",
                "records": {},
                "protocol_capabilities": {},
            },
            "remediation_ledger": hloop.hloop_remediation.RemediationLedger().to_record(),
            "remediation_source_links": {},
            "inputs_index": {},
        }
        return repo, head, state

    def execution(
        self,
        execution_id: str,
        *,
        source_kind: str,
        process_kind: str,
    ) -> EpochExecutionPlan:
        coordinator = f"{execution_id}-coordinator"
        child_kind = process_kind
        if source_kind == "reviewer":
            processes = [
                AuditProcessPlan(
                    process_id=coordinator,
                    process_kind="coordinator",
                    agent_label="codex-reviewer",
                    provider="codex",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                ),
                AuditProcessPlan(
                    process_id=f"{execution_id}-verifier-01",
                    process_kind="verifier",
                    agent_label="codex-verifier-01",
                    provider="codex",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    parent_process_id=coordinator,
                ),
                AuditProcessPlan(
                    process_id=f"{execution_id}-verifier-02",
                    process_kind="verifier",
                    agent_label="codex-verifier-02",
                    provider="codex",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    parent_process_id=coordinator,
                ),
            ]
        else:
            processes = [
                AuditProcessPlan(
                    process_id=coordinator,
                    process_kind="coordinator",
                    agent_label=f"{execution_id}-coordinator-agent",
                    provider="codex",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                ),
                AuditProcessPlan(
                    process_id=f"{execution_id}-{child_kind}",
                    process_kind=child_kind,
                    agent_label=f"{execution_id}-{child_kind}-agent",
                    provider="codex",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    parent_process_id=coordinator,
                    lane_id=(
                        "coverage" if child_kind == "challenge" else "correctness"
                    ),
                ),
            ]
        return EpochExecutionPlan(
            execution_id=execution_id,
            attempt_id=f"{execution_id}-A001",
            source_kind=source_kind,
            protocol="native",
            independence_key=f"{source_kind}:{execution_id}",
            artifact_ref=(
                f"reviews/{execution_id}.md"
                if source_kind == "reviewer"
                else f"gaps/{execution_id}.md"
            ),
            processes=tuple(processes),
        )

    def epoch_plan(self, head: str) -> ReviewEpochPlan:
        return ReviewEpochPlan(
            epoch_id="E001",
            epoch_revision=1,
            base_sha=head,
            target_sha=head,
            scope_revision=1,
            source_snapshot_revision=1,
            scope_digest=canonical_digest({"scope": 1}),
            source_refs=("MISSION.md", "PLAN.md", "REQ-003"),
            policy_digest=canonical_digest({"policy": "batch"}),
            validation_identity=canonical_digest({"commands": ["unittest"]}),
            audit_agent_budget=5,
            required_executions=(
                self.execution(
                    "R001", source_kind="reviewer", process_kind="discovery"
                ),
                self.execution("G001", source_kind="gap", process_kind="challenge"),
            ),
        )

    def namespace_args(self, repo: Path, **kwargs) -> argparse.Namespace:
        return argparse.Namespace(repo=str(repo), **kwargs)

    def candidate_for_head(self, head: str, *args, **kwargs):
        observed = fixtures.candidate(*args, **kwargs)
        return replace(
            observed,
            target_sha=head,
            source_ref=(
                f"{'reviews' if observed.source_kind == 'reviewer' else 'gaps'}/"
                f"{observed.source_execution_id}.md"
            ),
            classification=replace(observed.classification, target_sha=head),
        )

    def create_epoch(self, repo: Path, state: dict, plan: ReviewEpochPlan) -> None:
        plan_path = repo / "epoch-plan.json"
        plan_path.write_text(json.dumps(plan.to_record()), encoding="utf-8")
        with (
            mock.patch.object(hloop, "preflight_loop", return_value=state),
            mock.patch.object(hloop, "save_state"),
        ):
            hloop.cmd_review_epoch_create(
                self.namespace_args(
                    repo,
                    plan=str(plan_path),
                    protocol_capability=[],
                )
            )

    def complete_execution(
        self,
        repo: Path,
        state: dict,
        plan: ReviewEpochPlan,
        execution_id: str,
        *,
        status: str = "succeeded",
    ) -> None:
        execution = plan.execution(execution_id)
        process_ids = [item.process_id for item in execution.processes]
        with (
            mock.patch.object(hloop, "preflight_loop", return_value=state),
            mock.patch.object(hloop, "save_state"),
        ):
            hloop.cmd_review_epoch_reserve(
                self.namespace_args(
                    repo,
                    epoch_id="E001",
                    revision=1,
                    lease_id=f"lease-{execution_id}",
                    execution_id=execution_id,
                    process_id=process_ids,
                    expires_at="2026-07-17T08:00:00Z",
                )
            )
            hloop.cmd_review_epoch_lease(
                self.namespace_args(
                    repo,
                    epoch_id="E001",
                    revision=1,
                    lease_id=f"lease-{execution_id}",
                    action="terminal",
                    now=None,
                    reason="process tree exited",
                    process_exit_confirmed=True,
                    forced_abort_acknowledged=False,
                )
            )
            artifact = repo / hloop.LOOP_DIR / execution.artifact_ref
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                json.dumps({"execution_id": execution_id, "complete": status == "succeeded"}),
                encoding="utf-8",
            )
            outcome = EpochExecutionOutcome.for_plan(
                plan,
                execution_id,
                artifact_digest=hloop._sha256_labelled(artifact.read_bytes()),
                artifact_complete=status == "succeeded",
                completed_process_ids=process_ids,
                status=status,
                terminal_at="2026-07-17T08:01:00Z",
            )
            outcome_path = repo / f"{execution_id}-outcome.json"
            outcome_path.write_text(json.dumps(outcome.to_record()), encoding="utf-8")
            hloop.cmd_review_epoch_record(
                self.namespace_args(
                    repo,
                    epoch_id="E001",
                    revision=1,
                    outcome=str(outcome_path),
                )
            )

    def test_parser_exposes_epoch_collection_and_split_materialization(self):
        parser = hloop.build_parser()
        create = parser.parse_args(
            ["review", "epoch", "create", "--plan", "epoch.json"]
        )
        approve = parser.parse_args(
            [
                "triage",
                "epoch",
                "E001",
                "--approve-batch",
                "--batch-id",
                "RB001",
                "--approval-bundle",
                "approval.json",
                "--approval-ref",
                "manager:E001",
            ]
        )
        self.assertIs(create.func, hloop.cmd_review_epoch_create)
        self.assertIs(approve.func, hloop.cmd_triage_epoch)

    def test_epoch_cli_collects_all_required_successes_before_triage(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, head, state = self.make_repo(Path(directory))
            plan = self.epoch_plan(head)
            self.create_epoch(repo, state, plan)
            self.complete_execution(repo, state, plan, "R001")
            collecting = hloop.require_review_epoch_collection(state, "E001", 1)
            self.assertEqual(collecting.status, "collecting")
            self.complete_execution(repo, state, plan, "G001")
            ready = hloop.require_review_epoch_collection(state, "E001", 1)
            self.assertEqual(ready.status, "ready_to_triage")
            self.assertEqual(
                {item.independence_key for item in ready.execution_outcomes},
                {"reviewer:R001", "gap:G001"},
            )

    def test_role_start_binding_requires_full_budget_and_exact_reviewer_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, head, state = self.make_repo(Path(directory))
            plan = self.epoch_plan(head)
            self.create_epoch(repo, state, plan)
            collection = hloop.require_review_epoch_collection(state, "E001", 1)
            reviewer_ids = [
                item.process_id for item in plan.execution("R001").processes
            ]
            gap = plan.execution("G001")
            capacity = collection.capacity.reserve(
                plan,
                lease_id="lease-reviewer",
                execution_id="R001",
                process_ids=reviewer_ids,
                expires_at="2026-07-17T08:00:00Z",
            ).reserve(
                plan,
                lease_id="lease-gap-partial",
                execution_id="G001",
                process_ids=(gap.processes[0].process_id,),
                expires_at="2026-07-17T08:00:00Z",
            )
            collection = collection.with_capacity(capacity)
            hloop.store_review_epoch_collection(state, collection)

            binding = hloop.epoch_execution_start_binding(
                state,
                role_id="R001",
                attempt_id="R001-A001",
                source_kind="reviewer",
                target_sha=head,
            )
            reviewer_plan = plan_review_group(
                "single", head_sha=head, provider="codex", model="gpt-5.6-sol"
            )
            hloop.validate_reviewer_epoch_capacity_binding(binding, reviewer_plan)
            with self.assertRaisesRegex(hloop.HLoopError, "exact Reviewer provider capacity"):
                hloop.validate_reviewer_epoch_capacity_binding(
                    binding,
                    plan_review_group(
                        "single",
                        head_sha=head,
                        provider="claude",
                        model="opus",
                    ),
                )
            with self.assertRaisesRegex(hloop.HLoopError, "complete planned process set"):
                hloop.epoch_execution_start_binding(
                    state,
                    role_id="G001",
                    attempt_id="G001-A001",
                    source_kind="gap",
                    target_sha=head,
                )
            collection = hloop.require_review_epoch_collection(state, "E001", 1)
            capacity = collection.capacity.reserve(
                plan,
                lease_id="lease-gap-challenge",
                execution_id="G001",
                process_ids=(gap.processes[1].process_id,),
                expires_at="2026-07-17T08:00:00Z",
            )
            hloop.store_review_epoch_collection(
                state, collection.with_capacity(capacity), make_active=False
            )
            gap_binding = hloop.epoch_execution_start_binding(
                state,
                role_id="G001",
                attempt_id="G001-A001",
                source_kind="gap",
                target_sha=head,
            )
            epoch_execution = hloop.gap_epoch_execution(gap_binding)
            prompt = hloop.render_gap_prompt(
                "G001",
                "main",
                "main",
                [],
                state,
                head_sha=head,
                epoch_execution=epoch_execution,
            )
            self.assertIn(gap.processes[1].process_id, prompt)
            self.assertIn("Do not add unplanned audit Agents", prompt)

    def test_failed_execution_is_persisted_and_blocks_candidate_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, head, state = self.make_repo(Path(directory))
            plan = self.epoch_plan(head)
            self.create_epoch(repo, state, plan)
            self.complete_execution(repo, state, plan, "R001", status="failed")
            collection = hloop.require_review_epoch_collection(state, "E001", 1)
            self.assertEqual(collection.status, "incomplete")
            candidate_path = repo / "candidates.json"
            candidate_path.write_text(json.dumps({"candidates": []}), encoding="utf-8")
            args = self.namespace_args(
                repo,
                epoch_id="E001",
                revision=1,
                batch_id="RB001",
                record_candidates=str(candidate_path),
                approve_batch=False,
                materialize_batch=False,
                close_clean=False,
                approval_bundle=None,
                approval_ref=None,
                first_task_number=None,
                reason=None,
            )
            with mock.patch.object(hloop, "preflight_loop", return_value=state):
                with self.assertRaisesRegex(hloop.HLoopError, "not ready_to_triage"):
                    hloop.cmd_triage_epoch(args)

    def test_external_adapter_validates_exact_plan_without_new_source(self):
        group = plan_review_group("single", head_sha="a" * 40)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=plan_verification(group, ()),
            verifications=(),
        )
        adapter = ExternalReviewProtocolAdapter(
            protocol="codex-review-multi-v2",
            source="https://example.invalid/codex-review-multi-v2",
            version="2.1.0",
            content_digest=canonical_digest({"skill": "2.1.0"}),
            capabilities=("externally-planned-v1",),
        )
        restored = validate_externally_planned_review_manifest(
            manifest.to_record(), expected_plan=group, adapter=adapter
        )
        self.assertEqual(restored.review_id, "R001")
        incomplete = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=(),
            findings=(),
            verification_plan=plan_verification(group, ()),
            verifications=(),
        )
        with self.assertRaisesRegex(ReviewModelError, "incomplete required lanes"):
            validate_externally_planned_review_manifest(
                incomplete, expected_plan=group, adapter=adapter
            )
        other = plan_review_group("single", head_sha="b" * 40)
        with self.assertRaisesRegex(ReviewModelError, "changed the HLoop lane plan"):
            validate_externally_planned_review_manifest(
                manifest.to_record(), expected_plan=other, adapter=adapter
            )

    def test_triage_approval_consumes_once_then_materializes_exact_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, head, state = self.make_repo(Path(directory))
            plan = self.epoch_plan(head)
            self.create_epoch(repo, state, plan)
            state["reviews"]["R001"] = {}
            state["gaps"]["G001"] = {}
            self.complete_execution(repo, state, plan, "R001")
            self.complete_execution(repo, state, plan, "G001")
            semantic = fixtures.fingerprint("cli-remediation")
            review = self.candidate_for_head(
                head,
                "review:F001",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=semantic,
            )
            gap = self.candidate_for_head(
                head,
                "gap:F001",
                source_kind="gap",
                source_execution_id="G001",
                semantic_fingerprint=semantic,
            )
            candidates = repo / "candidates.json"
            candidates.write_text(
                json.dumps({"candidates": [review.to_record(), gap.to_record()]}),
                encoding="utf-8",
            )
            record_args = self.namespace_args(
                repo,
                epoch_id="E001",
                revision=1,
                batch_id="RB001",
                record_candidates=str(candidates),
                approve_batch=False,
                materialize_batch=False,
                close_clean=False,
                approval_bundle=None,
                approval_ref=None,
                first_task_number=None,
                reason=None,
            )
            task_contract = fixtures.remediation_task(
                "T020", "review:F001", 1
            )
            approval = repo / "approval.json"
            approval.write_text(
                json.dumps({"task_contracts": [task_contract]}), encoding="utf-8"
            )
            approve_args = self.namespace_args(
                repo,
                epoch_id="E001",
                revision=1,
                batch_id="RB001",
                record_candidates=None,
                approve_batch=True,
                materialize_batch=False,
                close_clean=False,
                approval_bundle=str(approval),
                approval_ref="manager:E001:RB001",
                first_task_number=20,
                reason=None,
            )
            materialize_args = self.namespace_args(
                repo,
                epoch_id="E001",
                revision=1,
                batch_id="RB001",
                record_candidates=None,
                approve_batch=False,
                materialize_batch=True,
                close_clean=False,
                approval_bundle=None,
                approval_ref=None,
                first_task_number=None,
                reason=None,
            )
            scope = fixtures.locked_scope()
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "assert_release_scope_snapshot", return_value=scope),
            ):
                self.assertEqual(hloop.cmd_triage_epoch(record_args), 0)
                self.assertEqual(hloop.cmd_triage_epoch(approve_args), 0)
                self.assertEqual(hloop.cmd_triage_epoch(approve_args), 0)
                ledger = hloop.remediation_ledger_from_state(state)
                self.assertEqual(ledger.consumed_rounds, 1)
                self.assertEqual(
                    ledger.batch("RB001").status, "materializing"
                )
                self.assertEqual(hloop.cmd_triage_epoch(materialize_args), 0)
            ledger = hloop.remediation_ledger_from_state(state)
            self.assertEqual(ledger.batch("RB001").status, "dispatched")
            self.assertIn("T020", state["tasks"])
            self.assertTrue((repo / hloop.LOOP_DIR / "tasks" / "T020.md").is_file())
            self.assertEqual(
                state["tasks"]["T020"]["remediation_task_contract"],
                task_contract,
            )
            self.assertEqual(state["reviews"]["R001"]["created_fix_tasks"], ["T020"])
            self.assertEqual(state["gaps"]["G001"]["created_fix_tasks"], ["T020"])
            state["reviews"]["R001"].pop("created_fix_tasks")
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "save_state"),
            ):
                with self.assertRaisesRegex(
                    hloop.HLoopError, "remediation_reconcile_required"
                ):
                    hloop.cmd_triage_epoch(materialize_args)

    def test_classification_conflict_is_durable_and_never_approvable(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, head, state = self.make_repo(Path(directory))
            plan = self.epoch_plan(head)
            self.create_epoch(repo, state, plan)
            self.complete_execution(repo, state, plan, "R001")
            self.complete_execution(repo, state, plan, "G001")
            semantic = fixtures.fingerprint("conflict")
            review = self.candidate_for_head(
                head,
                "review:F001",
                source_kind="reviewer",
                source_execution_id="R001",
                semantic_fingerprint=semantic,
                severity="P1",
            )
            gap = self.candidate_for_head(
                head,
                "gap:F001",
                source_kind="gap",
                source_execution_id="G001",
                semantic_fingerprint=semantic,
                severity="P2",
            )
            bundle = repo / "conflict.json"
            bundle.write_text(
                json.dumps({"candidates": [review.to_record(), gap.to_record()]}),
                encoding="utf-8",
            )
            args = self.namespace_args(
                repo,
                epoch_id="E001",
                revision=1,
                batch_id="RB001",
                record_candidates=str(bundle),
                approve_batch=False,
                materialize_batch=False,
                close_clean=False,
                approval_bundle=None,
                approval_ref=None,
                first_task_number=None,
                reason=None,
            )
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "save_state"),
            ):
                self.assertEqual(hloop.cmd_triage_epoch(args), 2)
            ledger = hloop.remediation_ledger_from_state(state)
            self.assertEqual(
                ledger.batch("RB001").status, "classification_conflict"
            )
            self.assertEqual(ledger.consumed_rounds, 0)

    def test_restored_extra_round_revalidates_captured_input_provenance(self):
        authorization = fixtures.ExtraRoundAuthorization(
            input_id="U0042",
            source="manager-chat",
            content_digest=fixtures.EXTRA_INPUT_DIGEST,
            authorized_extra_rounds=1,
            remediation_batch_ids=("RB001",),
        )
        ledger = fixtures.approve(
            fixtures.candidate_batch(
                ledger=fixtures.RemediationLedger(max_fix_rounds=0)
            ),
            "RB001",
            task_number=20,
            remediation_round=1,
            extra_round_authorization_ref="U0042:RB001",
            extra_round_authorization=authorization,
            captured_input_ids=("U0042",),
        )
        state = {
            "remediation_ledger": ledger.to_record(),
            "inputs_index": fixtures.captured_input("U0042"),
        }
        self.assertEqual(hloop.remediation_ledger_from_state(state), ledger)
        state["inputs_index"]["U0042"]["prompt_digest"] = "0" * 64
        with self.assertRaisesRegex(hloop.HLoopError, "no longer matches"):
            hloop.remediation_ledger_from_state(state)

    @unittest.skipIf(jsonschema is None, "jsonschema is required")
    def test_state_and_canonical_schemas_accept_epoch_and_ledger_projection(self):
        state_schema_path = SKILL_ROOT / "references" / "schemas" / "state.schema.json"
        schema = json.loads(state_schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        self.assertIn("review_epochs", schema["properties"])
        self.assertIn("remediation_ledger", schema["properties"])
        epoch_schema = json.loads(
            (SKILL_ROOT / "references" / "schemas" / "review-epoch.schema.json").read_text(
                encoding="utf-8"
            )
        )
        remediation_schema = json.loads(
            (SKILL_ROOT / "references" / "schemas" / "remediation-ledger.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(epoch_schema)
        jsonschema.Draft202012Validator.check_schema(remediation_schema)


if __name__ == "__main__":
    unittest.main()
