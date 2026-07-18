from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional for minimal installs
    jsonschema = None


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_planning_worker_cli_v053", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class PlanningWorkerCliV053Tests(unittest.TestCase):
    namespace = "planning-worker-cli-v053"

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

    def make_repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        self.git(repo, "init", "--initial-branch=main")
        self.git(repo, "config", "user.name", "Test")
        self.git(repo, "config", "user.email", "test@example.com")
        (repo / ".gitignore").write_text(".ai/\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "task.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.git(repo, "add", ".gitignore", "src/task.py")
        self.git(repo, "commit", "-m", "base")
        return repo, self.git(repo, "rev-parse", "HEAD")

    def task_record(
        self,
        base_sha: str,
        *,
        gates: tuple[str, ...] = ("patch_review", "full_suite"),
    ) -> dict:
        return {
            "id": "T001",
            "run_id": "run-v053",
            "skill_version": "0.5.3",
            "contract_schema_revision": 3,
            "kind": "implementation",
            "status": "queued",
            "created_from": "PLAN.md",
            "branch": "main",
            "base_ref": "main",
            "base_sha": base_sha,
            "priority": "P0",
            "depends_on": [],
            "write_allow": ["src/task.py"],
            "write_deny": [],
            "acceptance": ["candidate gates are exact"],
            "validation_minimum": "python3 -m unittest tests.targeted",
            "worker_protocol": "native",
            "worker_qa_profile": "repo-default",
            "worker_agent_provider": "codex",
            "worker_agent_model": "gpt-5.6-sol",
            "worker_agent_effort": "xhigh",
            "preserved_invariants": ["legacy completion remains isolated"],
            "regression_checks": ["targeted candidate test passes"],
            "risk_class": "high",
            "required_gates": list(gates),
            "task_origin": "planned",
            "release_scope_revision": 1,
            "plan_item_refs": ["P003"],
            "requirement_refs": ["REQ-002"],
            "scope_refs": ["runtime-release"],
            "source_finding": "",
            "authorization_input_id": "",
            "why_fix_now": "",
            "operational_reason": "",
            "origin": "",
            "contract_relation": "",
            "decision_requirement": "",
            "release_effect": "",
            "remediation_round": 0,
            "fact_status": "",
            "disposition": "",
            "scope_expanding": False,
        }

    def write_fixture(
        self,
        repo: Path,
        base_sha: str,
        *,
        gates: tuple[str, ...] = ("patch_review", "full_suite"),
        completion_mode: str = "commit",
    ) -> tuple[dict, dict]:
        loop = repo / hloop.LOOP_DIR
        task_path = loop / "tasks" / "T001.md"
        task = self.task_record(base_sha, gates=gates)
        hloop.write_text(
            task_path,
            hloop.frontmatter(task) + "\n# Task T001\n",
        )
        digest = hloop.hashlib.sha256(task_path.read_bytes()).hexdigest()
        task_state = {
            "status": "running",
            "branch": "main",
            "worktree": str(repo),
            "active_attempt_id": "T001-A001",
            "attempt_id": "T001-A001",
            "worker_base_sha": base_sha,
            "base_sha": base_sha,
            "base_ref": "main",
            "task_contract_digest": digest,
            "contract_schema_revision": 3,
            "required_gates": list(gates),
            "write_allow": ["src/task.py"],
            "write_deny": [],
            "semantic_ack_barrier": {
                "kind": "initial",
                "message_id": "initial:T001-A001",
                "status": "approved",
                "ack_event_id": "ack-event-001",
                "ack_sequence": 1,
                "semantic_decision": {
                    "status": "approved",
                    "ack_event_id": "ack-event-001",
                    "ack_sequence": 1,
                },
            },
            "completion_mode": completion_mode,
            "completion_mode_attempt_id": "T001-A001",
            "completion_mode_ack_event_id": "ack-event-001",
            "completion_mode_probe": {
                "version": 1,
                "mode": completion_mode,
                "status": "writable" if completion_mode == "commit" else "unwritable",
                "checked_at": "2026-07-18T00:00:00+00:00",
                "git_metadata_paths": [str(repo / ".git")],
                "checks": [
                    {
                        "resource": "git-metadata",
                        "path": str(repo / ".git"),
                        "status": "writable" if completion_mode == "commit" else "unwritable",
                        "detail": "fixture",
                    }
                ],
            },
        }
        state = {
            "state_format_version": 3,
            "schema_revision": 3,
            "namespace": self.namespace,
            "loop_path": hloop.LOOP_DIR.as_posix(),
            "run_id": "run-v053",
            "skill_version": "0.5.3",
            "goal_id": "v053-test",
            "phase": "running",
            "base_branch": "main",
            "integration_branch": "main",
            "persistence": "local-only",
            "branch_strategy": "integration",
            "session_cleanup": "none",
            "resolved_config": {
                "audit": {"max_patch_review_rounds_per_task": 2}
            },
            "tasks": {"T001": task_state},
            "reviews": {},
            "patch_reviews": {},
            "gaps": {},
            "advice": {},
            "decisions": {},
        }
        hloop.write_text(
            loop / "STATE.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        return state, task_state

    def submit_args(self, mode: str = "commit") -> argparse.Namespace:
        return argparse.Namespace(
            repo=".",
            task_id="T001",
            completion_mode=mode,
            candidate_revision=None,
            validation_command=["python3 -m unittest tests.targeted"],
            validation_result=["passed"],
            validation_summary="targeted tests passed",
            invariant_evidence=["legacy completion fixture stayed valid"],
            regression_evidence=["targeted candidate test passed"],
            self_review_summary="diff, scope, compatibility, and errors reviewed",
            residual_risk=[],
            unrun_check=["integration validation remains Manager-owned"],
        )

    def finalize_args(self, *commands: str) -> argparse.Namespace:
        return argparse.Namespace(
            repo=".",
            task_id="T001",
            status="done",
            validation_command=list(commands),
            validation_result=["passed"] * len(commands),
            validation_summary="candidate and full suite passed",
            blocking_question=[],
            no_commit=False,
            handoff=False,
            invariant_evidence=[],
            regression_evidence=[],
            self_review_summary=None,
            residual_risk=[],
            unrun_check=[],
        )

    def submit_candidate(
        self,
        repo: Path,
        state: dict,
        task_state: dict,
        *,
        mode: str = "commit",
    ) -> tuple[object, object]:
        old_cwd = Path.cwd()
        try:
            hloop.os.chdir(repo)
            hloop.cmd_worker_submit(self.submit_args(mode))
        finally:
            hloop.os.chdir(old_cwd)
        revision = max(hloop.candidate_revision_numbers(repo, "T001", "T001-A001"))
        candidate, payload = hloop.load_candidate_artifact(
            repo, "T001", "T001-A001", revision
        )
        candidate_sha = self.git(repo, "rev-parse", "HEAD")
        seal = hloop.hloop_worker_candidate.seal_candidate(
            candidate,
            candidate_sha=candidate_sha,
            candidate_artifact_digest=hloop._sha256_labelled(payload),
            observed_tree_sha=candidate.candidate_tree_sha,
            active_attempt_id="T001-A001",
            active_task_contract_digest=candidate.task_contract_digest,
            approved_ack_event_id="ack-event-001",
        )
        task_state.update(
            {
                "candidate_revision": revision,
                "implementation_candidate": candidate.to_record(),
                "candidate_seal": seal.to_record(),
                "candidate_artifact_digest": hloop._sha256_labelled(payload),
                "candidate_sha": candidate_sha,
                "candidate_tree_sha": candidate.candidate_tree_sha,
                "completion_mode": mode,
                "candidate_lifecycle_status": "patch_review_pending",
                "merge_ready": False,
            }
        )
        hloop.write_text(
            repo / hloop.LOOP_DIR / "STATE.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        return candidate, seal

    def test_planning_dispatch_uses_locked_scope_not_artifact_self_declaration(self) -> None:
        fixtures = importlib.import_module(
            "skills.herdr-dev-loop.tests.test_planning_v053"
        )
        current, impact, graph, coverage, plan_gap = fixtures.planning_bundle()
        state = {
            "planning": {
                "status": "ready",
                "identity": current.to_dict(),
                "artifact_digests": {
                    "impact": impact["artifact_digest"],
                    "task_graph": graph["artifact_digest"],
                    "coverage": coverage["artifact_digest"],
                    "plan_gap": plan_gap["artifact_digest"],
                },
                "allowed_scope_refs": ["runtime-release"],
            },
            "tasks": {
                "T005": {
                    "depends_on": [],
                    "write_allow": [
                        "skills/herdr-dev-loop/scripts/hloop_lib/planning.py"
                    ],
                    "scope_refs": ["runtime-release"],
                }
            },
        }
        locked_scope = SimpleNamespace(
            release_scope_refs=("runtime-release",),
            requirement_refs=("REQ-001",),
            plan_item_refs=("P002",),
        )
        with (
            mock.patch.object(hloop, "current_planning_identity", return_value=current),
            mock.patch.object(
                hloop,
                "load_planning_bundle",
                return_value=(impact, graph, coverage, plan_gap),
            ),
            mock.patch.object(
                hloop, "assert_release_scope_snapshot", return_value=locked_scope
            ),
        ):
            accepted = hloop.planning_dispatch_validation(Path("."), state)
            locked_scope.release_scope_refs = ()
            rejected = hloop.planning_dispatch_validation(Path("."), state)

        self.assertTrue(accepted.ok, accepted.issues)
        self.assertIn(
            "authoritative-scope-required",
            {issue.code for issue in rejected.issues},
        )

    def test_queued_revision_two_task_is_blocked_before_start(self) -> None:
        legacy = self.task_record("a" * 40)
        for field in (
            "contract_schema_revision",
            "preserved_invariants",
            "regression_checks",
            "risk_class",
            "required_gates",
            "worker_agent_effort",
        ):
            legacy.pop(field, None)
        state = {"schema_revision": 3, "tasks": {"T001": {"status": "queued"}}}
        with self.assertRaisesRegex(hloop.HLoopError, "risk-classification-required"):
            hloop.runtime_worker_task_preflight(
                Path("."), state, task_id="T001", task_meta=legacy
            )

    def test_worker_requeue_clears_attempt_scoped_candidate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            self.git(repo, "branch", "worker-candidate")
            task_state.update(
                {
                    "branch": "worker-candidate",
                    "root_branch": "worker-candidate",
                    "attempt_no": 1,
                    "candidate_revision": 2,
                    "candidate_lifecycle_status": "patch_review_passed",
                    "implementation_candidate": {"record_type": "candidate"},
                    "candidate_seal": {"record_type": "seal"},
                    "patch_review_history": [{"review_attempt_id": "PR-old"}],
                    "current_patch_review": {"review_attempt_id": "PR-old"},
                    "legacy_result_acceptance": {"reason": "stale"},
                }
            )
            hloop.write_text(
                repo / hloop.LOOP_DIR / "STATE.json",
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            args = argparse.Namespace(
                repo=str(repo),
                agent_id="T001",
                reason="retry revision 3 task",
                force_cleanup=False,
            )
            with (
                mock.patch.object(hloop, "cleanup_completed_agent_pane"),
                mock.patch.object(hloop, "cleanup_agent_worktree_for_lifecycle"),
                mock.patch.object(hloop, "revoke_active_role_report_identity"),
            ):
                hloop.cmd_agent_requeue(args)

            updated = hloop.load_state(repo)["tasks"]["T001"]
            self.assertEqual(updated["status"], "queued")
            self.assertEqual(updated["branch"], "worker-candidate-a002")
            for field in (
                "candidate_revision",
                "candidate_lifecycle_status",
                "implementation_candidate",
                "candidate_seal",
                "patch_review_history",
                "current_patch_review",
                "legacy_result_acceptance",
            ):
                self.assertNotIn(field, updated)

    def test_reported_revision_two_result_requires_exact_explicit_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha, gates=())
            task_path = repo / hloop.LOOP_DIR / "tasks" / "T001.md"
            legacy_task = hloop.read_frontmatter(task_path)
            for field in (
                "preserved_invariants",
                "regression_checks",
                "risk_class",
                "required_gates",
                "worker_agent_effort",
            ):
                legacy_task.pop(field, None)
            legacy_task["contract_schema_revision"] = 2
            hloop.replace_frontmatter_record(task_path, legacy_task)
            task_state.update(
                {
                    "status": "result_reported",
                    "result_status": "done",
                    "merge_ready": True,
                    "head_sha": base_sha,
                    "contract_schema_revision": 2,
                    "task_contract_digest": hloop.hashlib.sha256(
                        task_path.read_bytes()
                    ).hexdigest(),
                }
            )
            result_fields = {
                "task_id": "T001",
                "run_id": "run-v053",
                "skill_version": "0.5.2",
                "contract_schema_revision": 2,
                "attempt_id": "T001-A001",
                "status": "done",
                "merge_ready": True,
                "branch": "main",
                "head_sha": base_sha,
                "base_sha": base_sha,
                "changed_files": [],
                "validation_recorded": True,
                "validation_commands": ["python3 -m unittest tests.legacy"],
                "validation_results": ["passed"],
                "validation_summary": "legacy validation passed",
                "blocking_questions": [],
                "handoff": False,
            }
            result_path = repo / hloop.LOOP_DIR / "results" / "T001" / "result.md"
            hloop.write_text(
                result_path,
                hloop.frontmatter(result_fields) + "\n# Legacy result\n",
            )
            hloop.write_text(
                repo / hloop.LOOP_DIR / "STATE.json",
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            with self.assertRaisesRegex(hloop.HLoopError, "explicit Manager acceptance"):
                hloop.assert_legacy_result_merge_accepted(
                    repo, state, "T001", task_state
                )
            args = argparse.Namespace(
                repo=str(repo), task_id="T001", reason="preserve valid 0.5.2 evidence"
            )
            with mock.patch.object(hloop, "preflight_loop", return_value=state):
                hloop.cmd_worker_accept_legacy_result(args)
            hloop.assert_legacy_result_merge_accepted(repo, state, "T001", task_state)
            hloop.replace_frontmatter(result_path, {"validation_summary": "changed"})
            with self.assertRaisesRegex(hloop.HLoopError, "artifact_digest changed"):
                hloop.assert_legacy_result_merge_accepted(
                    repo, state, "T001", task_state
                )

    def test_commit_submit_is_nonterminal_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            (repo / "src" / "task.py").write_text("VALUE = 2\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                candidate, seal = self.submit_candidate(repo, state, task_state)

            self.assertIn("merge_ready=false", output.getvalue())
            self.assertNotIn("HERDR_LOOP_ROLE_DONE", output.getvalue())
            self.assertEqual(candidate.candidate_tree_sha, self.git(repo, "rev-parse", "HEAD^{tree}"))
            self.assertEqual(seal.candidate_sha, self.git(repo, "rev-parse", "HEAD"))
            self.assertEqual(candidate.changed_files, ("src/task.py",))
            self.assertFalse((repo / hloop.LOOP_DIR / "results" / "T001" / "result.md").exists())

    def test_handoff_submit_tree_can_be_committed_without_changing_attempt_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(
                repo, base_sha, completion_mode="handoff"
            )
            (repo / "src" / "task.py").write_text("VALUE = 3\n", encoding="utf-8")
            old_cwd = Path.cwd()
            try:
                hloop.os.chdir(repo)
                hloop.cmd_worker_submit(self.submit_args("handoff"))
            finally:
                hloop.os.chdir(old_cwd)
            candidate, _ = hloop.load_candidate_artifact(
                repo, "T001", "T001-A001", 1
            )

            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), base_sha)
            committed = hloop.commit_handoff_candidate(
                repo,
                "T001",
                ["src/task.py"],
                candidate.candidate_tree_sha,
            )
            self.assertEqual(self.git(repo, "rev-parse", "HEAD^{tree}"), candidate.candidate_tree_sha)
            self.assertEqual(task_state["active_attempt_id"], "T001-A001")
            self.assertTrue(committed)

    def test_patch_fix_resubmission_stays_in_attempt_and_stops_at_round_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            (repo / "src" / "task.py").write_text("VALUE = 4\n", encoding="utf-8")
            candidate1, seal1 = self.submit_candidate(repo, state, task_state)
            finding1 = hloop.hloop_worker_candidate.canonical_digest({"finding": 1})
            review1 = hloop.hloop_worker_candidate.record_patch_review(
                seal1,
                review_attempt_id="PR-T001-R001",
                review_round=1,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                unresolved_finding_fingerprints=(finding1,),
            )
            decision1 = hloop.apply_patch_review_record(
                repo, state, "T001", task_state, review1
            )
            self.assertEqual(decision1.action, "patch_fix_running")
            self.assertEqual(task_state["active_attempt_id"], candidate1.attempt_id)
            hloop.write_text(
                repo / hloop.LOOP_DIR / "STATE.json",
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )

            (repo / "src" / "task.py").write_text("VALUE = 5\n", encoding="utf-8")
            candidate2, seal2 = self.submit_candidate(repo, state, task_state)
            finding2 = hloop.hloop_worker_candidate.canonical_digest({"finding": 2})
            review2 = hloop.hloop_worker_candidate.record_patch_review(
                seal2,
                review_attempt_id="PR-T001-R002",
                review_round=2,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                unresolved_finding_fingerprints=(finding2,),
            )
            decision2 = hloop.apply_patch_review_record(
                repo, state, "T001", task_state, review2
            )

            self.assertTrue(decision2.requires_user_decision)
            self.assertEqual(task_state["candidate_lifecycle_status"], "blocked_patch_review")
            self.assertEqual(task_state["active_attempt_id"], "T001-A001")
            self.assertEqual(decision2.last_candidate_sha, seal2.candidate_sha)
            self.assertEqual(decision2.automatic_task_ids, ())

    def test_finalize_requires_independent_full_suite_and_exact_reviewed_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            (repo / "src" / "task.py").write_text("VALUE = 6\n", encoding="utf-8")
            candidate, seal = self.submit_candidate(repo, state, task_state)
            review = hloop.hloop_worker_candidate.record_patch_review(
                seal,
                review_attempt_id="PR-T001-R001",
                review_round=1,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="passed",
            )
            hloop.apply_patch_review_record(repo, state, "T001", task_state, review)
            hloop.write_text(
                repo / hloop.LOOP_DIR / "STATE.json",
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            old_cwd = Path.cwd()
            try:
                hloop.os.chdir(repo)
                no_commit = self.finalize_args(
                    "python3 -m unittest tests.full_suite"
                )
                no_commit.no_commit = True
                with self.assertRaisesRegex(
                    hloop.HLoopError, "must commit the final result"
                ):
                    hloop.cmd_worker_finalize(no_commit)
                with self.assertRaisesRegex(hloop.HLoopError, "full_suite"):
                    hloop.cmd_worker_finalize(
                        self.finalize_args("python3 -m unittest tests.targeted")
                    )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    hloop.cmd_worker_finalize(
                        self.finalize_args("python3 -m unittest tests.full_suite")
                    )
            finally:
                hloop.os.chdir(old_cwd)

            result_head = self.git(repo, "rev-parse", "HEAD")
            self.assertEqual(self.git(repo, "rev-parse", "HEAD^"), seal.candidate_sha)
            self.assertIn("HERDR_LOOP_ROLE_DONE:run-v053:T001:T001-A001:done", output.getvalue())
            result_meta = hloop.read_frontmatter(
                repo / hloop.LOOP_DIR / "results" / "T001" / "result.md"
            )
            hloop.validate_revision_three_committed_result(
                repo,
                repo,
                state,
                "T001",
                task_state,
                result_meta,
                result_head,
            )
            self.assertEqual(result_meta["changed_files"], list(candidate.changed_files))

            harvest_args = argparse.Namespace(
                repo=str(repo),
                task_id="T001",
                keep_pane=True,
                session_cleanup=None,
            )
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "cleanup_completed_agent_pane"),
                mock.patch.object(hloop, "revoke_active_role_report_identity"),
            ):
                hloop.cmd_worker_harvest(harvest_args)
            self.assertEqual(task_state["status"], "result_reported")
            self.assertTrue(task_state["merge_ready"])

            merge_args = argparse.Namespace(
                repo=str(repo),
                task_id="T001",
                abort=False,
                continue_merge=False,
                retry=False,
                mode="squash",
                dry_run=True,
            )
            with mock.patch.object(hloop, "preflight_loop", return_value=state):
                self.assertEqual(hloop.cmd_merge(merge_args), 0)
            task_state["current_patch_review"]["candidate_sha"] = "d" * 40
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "Patch Review|stale"),
            ):
                hloop.cmd_merge(merge_args)

    def test_handoff_final_seal_rechecks_gates_and_emits_terminal_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(
                repo, base_sha, completion_mode="handoff"
            )
            (repo / "src" / "task.py").write_text("VALUE = 7\n", encoding="utf-8")
            old_cwd = Path.cwd()
            try:
                hloop.os.chdir(repo)
                hloop.cmd_worker_submit(self.submit_args("handoff"))
            finally:
                hloop.os.chdir(old_cwd)
            candidate, payload = hloop.load_candidate_artifact(
                repo, "T001", "T001-A001", 1
            )
            candidate_sha = hloop.commit_handoff_candidate(
                repo,
                "T001",
                ["src/task.py"],
                candidate.candidate_tree_sha,
            )
            seal = hloop.hloop_worker_candidate.seal_candidate(
                candidate,
                candidate_sha=candidate_sha,
                candidate_artifact_digest=hloop._sha256_labelled(payload),
                observed_tree_sha=candidate.candidate_tree_sha,
                active_attempt_id="T001-A001",
                active_task_contract_digest=candidate.task_contract_digest,
                approved_ack_event_id="ack-event-001",
            )
            task_state.update(
                {
                    "candidate_revision": 1,
                    "implementation_candidate": candidate.to_record(),
                    "candidate_seal": seal.to_record(),
                    "candidate_artifact_digest": hloop._sha256_labelled(payload),
                    "candidate_sha": candidate_sha,
                    "candidate_tree_sha": candidate.candidate_tree_sha,
                    "completion_mode": "handoff",
                    "candidate_lifecycle_status": "patch_review_pending",
                    "merge_ready": False,
                }
            )
            review = hloop.hloop_worker_candidate.record_patch_review(
                seal,
                review_attempt_id="PR-T001-R001",
                review_round=1,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="passed",
            )
            hloop.apply_patch_review_record(repo, state, "T001", task_state, review)
            hloop.write_text(
                repo / hloop.LOOP_DIR / "STATE.json",
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            finalize = self.finalize_args("python3 -m unittest tests.full_suite")
            finalize.handoff = True
            old_cwd = Path.cwd()
            try:
                hloop.os.chdir(repo)
                finalize_output = io.StringIO()
                with contextlib.redirect_stdout(finalize_output):
                    hloop.cmd_worker_finalize(finalize)
            finally:
                hloop.os.chdir(old_cwd)
            self.assertNotIn("HERDR_LOOP_ROLE_DONE", finalize_output.getvalue())

            seal_args = argparse.Namespace(
                repo=str(repo),
                task_id="T001",
                attempt_id="T001-A001",
                validation_command=["true"],
                validation_summary="Manager seal validation passed",
            )
            output = io.StringIO()
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(
                    hloop, "require_worker_pane_quiesced_and_closed"
                ),
                contextlib.redirect_stdout(output),
            ):
                hloop.cmd_worker_seal(seal_args)

            final_head = self.git(repo, "rev-parse", "HEAD")
            self.assertEqual(self.git(repo, "rev-parse", "HEAD^"), candidate_sha)
            self.assertIn(
                "HERDR_LOOP_ROLE_DONE:run-v053:T001:T001-A001:done",
                output.getvalue(),
            )
            result_meta = hloop.read_frontmatter(
                repo / hloop.LOOP_DIR / "results" / "T001" / "result.md"
            )
            hloop.validate_revision_three_committed_result(
                repo,
                repo,
                state,
                "T001",
                task_state,
                result_meta,
                final_head,
            )

    @unittest.skipIf(jsonschema is None, "jsonschema is optional")
    def test_state_schema_accepts_planning_candidate_and_patch_review_projection(self) -> None:
        schema_path = (
            Path(__file__).parents[1] / "references" / "schemas" / "state.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        self.assertIn("planning", schema["properties"])
        self.assertIn("patch_reviews", schema["properties"])
        task_properties = schema["$defs"]["workerCandidateTaskProjection"]["properties"]
        for field in (
            "contract_schema_revision",
            "candidate_lifecycle_status",
            "implementation_candidate",
            "candidate_seal",
            "patch_review_history",
            "legacy_result_acceptance",
        ):
            self.assertIn(field, task_properties)


if __name__ == "__main__":
    unittest.main()
