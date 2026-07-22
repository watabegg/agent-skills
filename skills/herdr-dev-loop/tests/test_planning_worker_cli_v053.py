from __future__ import annotations

import argparse
import copy
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
                "digest": digest,
                "status": "approved",
                "ack_event_id": "ack-event-001",
                "ack_sequence": 1,
                "semantic_decision": {
                    "status": "approved",
                    "ack_event_id": "ack-event-001",
                    "ack_sequence": 1,
                },
                "approval_application": {
                    "status": "applied",
                    "ack_event_id": "ack-event-001",
                    "application_event_id": "application-event-001",
                    "application_event_digest": "a" * 64,
                    "application_attempt_id": "T001-A001",
                    "application_task_contract_digest": digest,
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

    def install_applied_message_ack(
        self,
        repo: Path,
        state: dict,
        task_state: dict,
    ) -> str:
        digest = hloop.hashlib.sha256(b"replacement Manager message").hexdigest()
        ack_event_id = "11111111-1111-4111-8111-111111111111"
        task_state["active_report_contract_digest"] = digest
        task_state["semantic_ack_barrier"] = {
            "kind": "message",
            "attempt_id": "T001-A001",
            "message_id": "22222222-2222-4222-8222-222222222222",
            "digest": digest,
            "status": "approved",
            "ack_event_id": ack_event_id,
            "ack_sequence": 2,
            "required_reack_after_sequence": 1,
            "report_identity_status": "bound",
            "report_identity_attempt_id": "T001-A001",
            "rendered_exchange_digest": digest,
            "semantic_decision": {
                "status": "approved",
                "ack_event_id": ack_event_id,
                "ack_sequence": 2,
            },
            "approval_application": {
                "status": "applied",
                "ack_event_id": ack_event_id,
                "application_event_id": "33333333-3333-4333-8333-333333333333",
                "application_event_digest": "b" * 64,
                "application_attempt_id": "T001-A001",
                "application_task_contract_digest": digest,
            },
            "approval_availability": {
                "status": "available",
                "message_id": "22222222-2222-4222-8222-222222222222",
                "task_contract_digest": digest,
                "ack_event_id": ack_event_id,
            },
        }
        hloop.write_text(
            repo / hloop.LOOP_DIR / "STATE.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        return ack_event_id

    def submit_and_seal_current_candidate(
        self,
        repo: Path,
        state: dict,
        *,
        mode: str,
    ):
        old_cwd = Path.cwd()
        try:
            hloop.os.chdir(repo)
            hloop.cmd_worker_submit(self.submit_args(mode))
        finally:
            hloop.os.chdir(old_cwd)
        candidate, _payload = hloop.load_candidate_artifact(
            repo, "T001", "T001-A001", 1
        )
        with (
            mock.patch.object(hloop, "preflight_loop", return_value=state),
            mock.patch.object(hloop, "require_worker_pane_quiesced_preserved"),
        ):
            hloop.cmd_worker_candidate_seal(
                argparse.Namespace(
                    repo=str(repo), task_id="T001", candidate_revision=1
                )
            )
        return candidate

    def harvest_and_dry_run_merge(
        self,
        repo: Path,
        state: dict,
        task_state: dict,
    ) -> None:
        with (
            mock.patch.object(hloop, "preflight_loop", return_value=state),
            mock.patch.object(hloop, "cleanup_completed_agent_pane"),
            mock.patch.object(hloop, "revoke_active_role_report_identity"),
        ):
            hloop.cmd_worker_harvest(
                argparse.Namespace(
                    repo=str(repo),
                    task_id="T001",
                    keep_pane=True,
                    session_cleanup=None,
                )
            )
        self.assertEqual(task_state["status"], "result_reported")
        self.assertTrue(task_state["merge_ready"])
        with mock.patch.object(hloop, "preflight_loop", return_value=state):
            self.assertEqual(
                hloop.cmd_merge(
                    argparse.Namespace(
                        repo=str(repo),
                        task_id="T001",
                        abort=False,
                        continue_merge=False,
                        retry=False,
                        mode="squash",
                        dry_run=True,
                    )
                ),
                0,
            )

    def patch_finding(self, label: str):
        return hloop.hloop_worker_candidate.PatchReviewFinding.from_evidence(
            scope="unresolved",
            file_path="src/task.py",
            symbol=f"task_{label}",
            trigger=f"Trigger {label}",
            product_impact=f"Impact {label}",
            proposed_fix=f"Fix {label}",
        )

    def install_extra_round_evidence(
        self,
        repo: Path,
        state: dict,
        finding_fingerprint: str,
        *,
        decision_id: str = "D007",
        input_id: str = "U0005",
    ) -> None:
        decision = {
            "id": decision_id,
            "class": hloop.DECISION_BLOCKING_USER,
            "status": hloop.DECISION_ACCEPTED,
            "question": "Allow one task-local Patch Review round?",
            "options": [
                {
                    "id": "opt1",
                    "label": "Allow one round",
                    "tradeoffs": ["The third round remains task-local."],
                },
                {
                    "id": "opt2",
                    "label": "Stop",
                    "tradeoffs": ["The task remains blocked."],
                },
            ],
            "recommendation": {
                "option_id": "opt1",
                "rationale": "One bounded round closes the retained findings.",
            },
            "affected_task_ids": ["T001"],
            "source_findings": [finding_fingerprint],
            "response": {
                "responded_by": "user",
                "responded_at": "2026-07-19T12:00:00Z",
                "selected_option": "opt1",
            },
            "resolution": {
                "outcome": "accepted",
                "rationale": "The user accepted the bounded round.",
                "resolved_by": "manager",
                "resolved_at": "2026-07-19T12:00:01Z",
                "selected_option": "opt1",
            },
        }
        state.setdefault("decisions", {})[decision_id] = decision
        input_record = hloop.hloop_requirements.InputRecord.capture(
            input_id=input_id,
            received_at="2026-07-19T12:00:00Z",
            source="user",
            raw_input="bounded authorization response",
        )
        input_path = hloop.local_sensitive_input_dir(repo) / f"{input_id}.json"
        hloop.write_text(
            input_path,
            json.dumps(input_record.to_record(), ensure_ascii=False, indent=2)
            + "\n",
        )
        state.setdefault("inputs_index", {})[input_id] = {
            "prompt_digest": input_record.prompt_digest,
            "redactions": [],
            "received_at": input_record.received_at,
            "source": "user",
        }

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

    def exhaust_patch_review_rounds(
        self, repo: Path, state: dict, task_state: dict
    ) -> tuple[object, object]:
        (repo / "src" / "task.py").write_text("VALUE = 40\n", encoding="utf-8")
        _candidate1, seal1 = self.submit_candidate(repo, state, task_state)
        review1 = hloop.hloop_worker_candidate.record_patch_review(
            seal1,
            review_attempt_id="PR-T001-R001",
            review_round=1,
            reviewer_provider="codex",
            reviewer_model="gpt-5.6-sol",
            reviewer_effort="xhigh",
            verdict="fix_required",
            findings=(self.patch_finding("one"),),
        )
        hloop.apply_patch_review_record(repo, state, "T001", task_state, review1)
        hloop.write_text(
            repo / hloop.LOOP_DIR / "STATE.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        (repo / "src" / "task.py").write_text("VALUE = 50\n", encoding="utf-8")
        _candidate2, seal2 = self.submit_candidate(repo, state, task_state)
        review2 = hloop.hloop_worker_candidate.record_patch_review(
            seal2,
            review_attempt_id="PR-T001-R002",
            review_round=2,
            reviewer_provider="codex",
            reviewer_model="gpt-5.6-sol",
            reviewer_effort="xhigh",
            verdict="fix_required",
            findings=(self.patch_finding("two"),),
        )
        hloop.apply_patch_review_record(repo, state, "T001", task_state, review2)
        hloop.write_text(
            repo / hloop.LOOP_DIR / "STATE.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        return seal2, review2

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
            mock.patch.object(
                hloop,
                "plan_gap_scout_binding_issues",
                return_value=([], dict(plan_gap["checker"])),
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

    def test_coverage_scout_binding_is_exact_and_stale_evidence_fails(self) -> None:
        fixtures = importlib.import_module(
            "skills.herdr-dev-loop.tests.test_planning_v053"
        )
        current, _impact, _graph, _coverage, plan_gap = fixtures.planning_bundle()
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            artifact = hloop.planning_artifact_file(
                repo, hloop.hloop_planning.PLAN_GAP_RECORD_TYPE
            )
            hloop.write_text(
                artifact,
                json.dumps(plan_gap, ensure_ascii=False, indent=2) + "\n",
            )
            checker = plan_gap["checker"]
            run = {
                "status": "completed",
                "verdict": "clean",
                "head_sha": checker["head_sha"],
                "planning_identity": current.to_dict(),
                "planning_identity_digest": checker["planning_identity_digest"],
                "attempt_id": checker["attempt_id"],
                "task_contract_digest": checker["task_contract_digest"],
                "input_artifact_digests": checker["input_artifact_digests"],
                "agent_config": {
                    "provider": checker["provider"],
                    "model": checker["model"],
                    "effort": checker["effort"],
                    "sources": checker["config_sources"],
                },
                "completion_event_id": "completion-event-001",
                "artifact_digest": hloop._sha256_labelled(artifact.read_bytes()),
                "artifact_contract_digest": plan_gap["artifact_digest"],
            }
            state = {"plan_gap_scout_run": run}
            with mock.patch.object(
                hloop,
                "current_integration_target",
                return_value=checker["head_sha"],
            ):
                issues, expected = hloop.plan_gap_scout_binding_issues(
                    repo, state, current, plan_gap
                )
                self.assertEqual(issues, [])
                self.assertEqual(expected, checker)

                run["attempt_id"] = "S001-C999"
                stale, _expected = hloop.plan_gap_scout_binding_issues(
                    repo, state, current, plan_gap
                )
            self.assertNotEqual(
                hloop.plan_gap_scout_checker(state, run), checker
            )
            self.assertEqual(stale, [])
            validation = hloop.hloop_planning.validate_planning_artifact(
                plan_gap,
                expected_plan_gap_checker=hloop.plan_gap_scout_checker(state, run),
            )
            self.assertIn(
                "checker-identity-mismatch",
                {issue.code for issue in validation.issues},
            )

    def test_coverage_scout_completion_requires_exact_fresh_broker_event(self) -> None:
        state = {"run_id": "run-coverage"}
        run = {
            "attempt_id": "S001-C001",
            "head_sha": "a" * 40,
            "prior_completion_event_id": "",
        }
        artifact = ".ai/herdr-dev-loop/loops/test/planning/PLAN-GAP.json"
        artifact_digest = "sha256:" + "b" * 64
        event = {
            "event_id": "event-001",
            "run_id": state["run_id"],
            "role_id": "S001",
            "attempt_id": run["attempt_id"],
            "type": "completion",
            "head_sha": run["head_sha"],
            "artifact": artifact,
            "artifact_digest": artifact_digest,
        }
        store = mock.MagicMock()
        store.latest_role_event.return_value = event
        with mock.patch.object(hloop, "_open_broker_store", return_value=store):
            accepted = hloop.authenticated_plan_gap_completion(
                Path("."),
                state,
                run,
                artifact_ref=artifact,
                artifact_digest=artifact_digest,
            )
            self.assertEqual(accepted, event)

            with self.assertRaisesRegex(hloop.HLoopError, "identity mismatch"):
                hloop.authenticated_plan_gap_completion(
                    Path("."),
                    state,
                    run,
                    artifact_ref=artifact,
                    artifact_digest="sha256:" + "c" * 64,
                )

            replayed = {**run, "prior_completion_event_id": event["event_id"]}
            with self.assertRaisesRegex(hloop.HLoopError, "replayed"):
                hloop.authenticated_plan_gap_completion(
                    Path("."),
                    state,
                    replayed,
                    artifact_ref=artifact,
                    artifact_digest=artifact_digest,
                )

    def test_coverage_scout_uses_common_s001_lifecycle_through_harvest(self) -> None:
        fixtures = importlib.import_module(
            "skills.herdr-dev-loop.tests.test_planning_v053"
        )
        current, impact, task_graph, coverage, plan_gap_template = (
            fixtures.planning_bundle()
        )
        head_sha = plan_gap_template["checker"]["head_sha"]
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            worktree = repo / "coverage-worktree"
            worktree.mkdir()
            state = {
                "run_id": "run-coverage",
                "goal_id": "coverage-lifecycle",
                "integration_branch": "main",
                "session_cleanup": "none",
                "plan_gap_scout_run": {},
                "specification_scout_run": {"status": "completed"},
                "tasks": {},
                "reviews": {},
                "patch_reviews": {},
                "gaps": {},
                "advice": {},
                "decision_liaisons": {},
            }
            agent_config = {
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "effort": "xhigh",
                "sources": {
                    "provider": "config-defaults",
                    "model": "scope:tree:/repo",
                    "effort": "loop-snapshot",
                },
                "provenance": {"provider": [], "model": [], "effort": []},
            }
            invocation = SimpleNamespace(as_record=lambda: {"provider": "codex"})

            def fake_git(_repo, command):
                return head_sha if command[0] == "rev-parse" else ""

            start_args = SimpleNamespace(
                repo=str(repo),
                worktree=str(worktree),
                agent_provider=None,
                agent_model=None,
                agent_effort=None,
                runner="tui",
                dry_run=False,
                launcher="pane",
                direction="right",
                manager_pane=None,
            )
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "git", side_effect=fake_git),
                mock.patch.object(
                    hloop, "current_planning_identity", return_value=current
                ),
                mock.patch.object(
                    hloop,
                    "_plan_gap_input_artifacts",
                    return_value=(impact, task_graph, coverage),
                ),
                mock.patch.object(hloop, "dispatch_start_preflight"),
                mock.patch.object(
                    hloop, "role_agent_config", return_value=agent_config
                ),
                mock.patch.object(
                    hloop,
                    "role_agent_command",
                    return_value=("codex", invocation),
                ),
                mock.patch.dict(hloop.os.environ, {"HERDR_ENV": "1"}),
                mock.patch.object(hloop, "command_exists", return_value=True),
                mock.patch.object(hloop, "prepare_role_worktree"),
                mock.patch.object(hloop, "ensure_advisor_visible_in_worktree"),
                mock.patch.object(
                    hloop,
                    "register_role_report_identity_and_ack_floor",
                    return_value=(repo / "credential.json", 0),
                ),
                mock.patch.object(hloop, "role_scope_paths", return_value=[]),
                mock.patch.object(
                    hloop, "start_pane_launcher", return_value="pane-coverage"
                ),
                mock.patch.object(hloop, "invalidate_planning_evidence"),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
            ):
                self.assertEqual(hloop.cmd_plan_gap_scout_start(start_args), 0)
            run = state["plan_gap_scout_run"]
            self.assertEqual(run["status"], "running")
            self.assertEqual(run["attempt_id"], "S001-C001")

            role, selected = hloop.resolve_agent_state(state, "S001")
            self.assertEqual(role, "coverage-scout")
            self.assertIs(selected, run)
            self.assertEqual(hloop.running_agent_ids(state), ["S001"])
            self.assertIn(run, hloop.long_running_role_states(state))
            self.assertEqual(hloop.wait_targets(state, "next"), ["S001"])
            wait_status = hloop.agent_wait_status(repo, state, "S001")
            self.assertEqual(wait_status["role"], "coverage-scout")
            self.assertEqual(wait_status["readiness_reason"], "missing")

            def approve_ack(agent, **_kwargs):
                barrier = agent["semantic_ack_barrier"]
                barrier.update(
                    status="approved",
                    ack_event_id="ack-coverage",
                    approval_application={
                        "status": "applied",
                        "ack_event_id": "ack-coverage",
                        "application_event_id": "application-coverage",
                        "application_event_digest": "b" * 64,
                        "application_attempt_id": agent["attempt_id"],
                        "application_task_contract_digest": barrier["digest"],
                    },
                )
                barrier["semantic_decision"].update(
                    status="approved", ack_event_id="ack-coverage"
                )
                return barrier

            store = mock.MagicMock()
            store.transaction.return_value.__enter__.return_value = mock.MagicMock()
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "load_state", return_value=state),
                mock.patch.object(hloop, "_open_broker_store", return_value=store),
                mock.patch.object(
                    hloop, "resolve_semantic_ack_barrier", side_effect=approve_ack
                ) as resolve_ack,
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
                mock.patch.object(
                    hloop, "send_manager_message_and_record", return_value=0
                ),
            ):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="S001",
                        decision="approved",
                        reason="coverage contract understood",
                    )
                )
            self.assertIs(resolve_ack.call_args.args[0], run)
            self.assertEqual(run["semantic_ack_barrier"]["status"], "approved")

            message_args = SimpleNamespace(
                repo=str(repo),
                agent_id="S001",
                message="continue coverage",
                file=None,
                contract_changing=False,
                timeout_ms=1000,
                input_settle_ms=0,
                submit_verify_ms=0,
                submit_attempts=1,
            )
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(
                    hloop, "send_manager_message_and_record", return_value=0
                ) as send_message,
            ):
                hloop.cmd_agent_message(message_args)
            self.assertIs(send_message.call_args.args[2], run)

            plan_gap = copy.deepcopy(plan_gap_template)
            plan_gap["checker"] = hloop.plan_gap_scout_checker(state, run)
            plan_gap["artifact_digest"] = hloop.hloop_planning.artifact_digest(
                plan_gap
            )
            source = worktree / hloop.LOOP_DIR / "planning" / "PLAN-GAP.json"
            hloop.write_text(
                source, json.dumps(plan_gap, ensure_ascii=False, indent=2) + "\n"
            )
            completion = {
                "event_id": "completion-coverage",
                "artifact_digest": hloop._sha256_labelled(source.read_bytes()),
            }
            harvest_args = SimpleNamespace(repo=str(repo), agent_id="S001")
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "load_state", return_value=state),
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "git", side_effect=fake_git),
                mock.patch.object(
                    hloop, "current_planning_identity", return_value=current
                ),
                mock.patch.object(
                    hloop,
                    "_plan_gap_input_artifacts",
                    return_value=(impact, task_graph, coverage),
                ),
                mock.patch.object(
                    hloop, "validate_decision_role_scope", return_value=[]
                ),
                mock.patch.object(
                    hloop,
                    "authenticated_plan_gap_completion",
                    return_value=completion,
                ) as authenticate_completion,
                mock.patch.object(hloop, "cleanup_completed_agent_pane"),
                mock.patch.object(hloop, "cleanup_decision_role_worktree"),
                mock.patch.object(hloop, "revoke_active_role_report_identity"),
                mock.patch.object(hloop, "invalidate_planning_evidence"),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
            ):
                self.assertEqual(hloop.cmd_harvest(harvest_args), 0)
            self.assertEqual(harvest_args.mode, "coverage")
            authenticate_completion.assert_called_once()
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["completion_event_id"], "completion-coverage")

            aborting_run = copy.deepcopy(run)
            aborting_run.update(status="running", gate_status="running")
            aborting_state = {**state, "plan_gap_scout_run": aborting_run}
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "load_state", return_value=aborting_state),
                mock.patch.object(hloop, "cleanup_completed_agent_pane"),
                mock.patch.object(hloop, "cleanup_agent_worktree_for_lifecycle"),
                mock.patch.object(hloop, "revoke_active_role_report_identity"),
                mock.patch.object(hloop, "supersede_pending_manager_messages"),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
            ):
                hloop.cmd_agent_abort(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="S001",
                        reason="operator abort",
                        keep_worktree=True,
                        force_cleanup=False,
                    )
                )
            self.assertEqual(aborting_run["status"], "aborted")
            self.assertEqual(aborting_run["gate_status"], "aborted")

    def test_scout_modes_are_mutually_exclusive_in_both_directions(self) -> None:
        decision_running = {
            "specification_scout_run": {
                "status": "running",
                "gate_status": "running",
            }
        }
        coverage_running = {
            "plan_gap_scout_run": {
                "status": "running",
                "gate_status": "running",
            }
        }
        with mock.patch.object(
            hloop, "preflight_loop", return_value=decision_running
        ), self.assertRaisesRegex(hloop.HLoopError, "decision-mode.*already running"):
            hloop.cmd_plan_gap_scout_start(SimpleNamespace(repo="."))

        with mock.patch.object(
            hloop, "preflight_loop", return_value=coverage_running
        ), self.assertRaisesRegex(hloop.HLoopError, "coverage-mode.*already running"):
            hloop.cmd_specification_scout_start(
                SimpleNamespace(repo=".", mode="decision", force=True)
            )

    def test_both_scout_starts_use_the_atomic_loop_lock_boundary(self) -> None:
        parser = hloop.build_parser()
        for mode in ("decision", "coverage"):
            args = parser.parse_args(
                ["specification-scout", "start", "--mode", mode]
            )
            with self.subTest(mode=mode):
                self.assertTrue(hloop.command_requires_loop_lock(args))
                self.assertTrue(hloop.command_requires_state_schema_guard(args))
        for command in ("harvest", "close"):
            argv = ["specification-scout", command]
            if command == "close":
                argv.extend(["--verdict", "no-decision", "--reason", "clean"])
            args = parser.parse_args(argv)
            self.assertTrue(hloop.command_requires_loop_lock(args))

    def test_scout_cleanup_ids_are_mode_qualified_and_resolve_both_outcomes(self) -> None:
        decision = {
            "attempt_id": "S001-A001",
            "worktree": "/tmp/decision-scout",
            "worktree_cleanup_status": "failed",
            "worktree_cleanup_error": "decision cleanup failed",
            "worktree_cleanup_error_fingerprint": "sha256:" + "a" * 64,
        }
        coverage = {
            "attempt_id": "S001-C001",
            "worktree": "/tmp/coverage-scout",
            "worktree_cleanup_status": "failed",
            "worktree_cleanup_error": "coverage cleanup failed",
            "worktree_cleanup_error_fingerprint": "sha256:" + "b" * 64,
        }
        state = {
            "run_id": "run-cleanup",
            "session_cleanup": "none",
            "tasks": {},
            "reviews": {},
            "patch_reviews": {},
            "gaps": {},
            "advice": {},
            "decision_liaisons": {},
            "specification_scout_run": decision,
            "plan_gap_scout_run": coverage,
        }
        roles = {
            role_id: (role_kind, role_state)
            for role_kind, role_id, role_state in hloop.iter_all_roles(state)
        }
        self.assertIs(roles["S001/decision"][1], decision)
        self.assertIs(roles["S001/coverage"][1], coverage)
        with self.assertRaisesRegex(hloop.HLoopError, "ambiguous"):
            hloop.find_role_for_cleanup(state, "S001")

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def clear_decision(_repo, _role_id, role_state, **kwargs):
                self.assertEqual(kwargs["prompt_suffix"], "scout")
                role_state["worktree_cleanup_status"] = "removed"

            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(
                    hloop,
                    "cleanup_decision_role_worktree",
                    side_effect=clear_decision,
                ),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
            ):
                self.assertEqual(
                    hloop.cmd_cleanup_resolve(
                        SimpleNamespace(
                            repo=str(repo),
                            role_id="S001/decision",
                            status="cleaned",
                            reason="retry succeeded",
                        )
                    ),
                    0,
                )
            self.assertFalse(hloop.unresolved_cleanup_failures(decision))
            self.assertEqual(
                state["cleanup_history"][-1]["role_id"], "S001/decision"
            )

            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
            ):
                self.assertEqual(
                    hloop.cmd_cleanup_resolve(
                        SimpleNamespace(
                            repo=str(repo),
                            role_id="S001/coverage",
                            status="accepted-risk",
                            reason="provider cannot remove the worktree",
                        )
                    ),
                    0,
                )
            self.assertFalse(hloop.unresolved_cleanup_failures(coverage))
            self.assertEqual(
                coverage["cleanup_resolutions"]["worktree"]["role_id"],
                "S001/coverage",
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
                    "patch_review_extra_round_authorization": {
                        "record_type": "stale-authorization"
                    },
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
                "patch_review_extra_round_authorization",
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

    def test_message_replacement_ack_completes_commit_candidate_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(
                repo, base_sha, gates=(), completion_mode="commit"
            )
            replacement_ack = self.install_applied_message_ack(
                repo, state, task_state
            )
            (repo / "src" / "task.py").write_text("VALUE = 8\n", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                candidate = self.submit_and_seal_current_candidate(
                    repo, state, mode="commit"
                )
            self.assertEqual(candidate.semantic_ack_event_id, replacement_ack)
            self.assertEqual(
                task_state["implementation_candidate"]["semantic_ack_event_id"],
                replacement_ack,
            )
            self.assertEqual(task_state["candidate_lifecycle_status"], "candidate_sealed")
            self.assertEqual(task_state["completion_mode_ack_event_id"], "ack-event-001")

            old_cwd = Path.cwd()
            output = io.StringIO()
            try:
                hloop.os.chdir(repo)
                with contextlib.redirect_stdout(output):
                    hloop.cmd_worker_finalize(self.finalize_args())
            finally:
                hloop.os.chdir(old_cwd)
            self.assertIn(
                "HERDR_LOOP_ROLE_DONE:run-v053:T001:T001-A001:done",
                output.getvalue(),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.harvest_and_dry_run_merge(repo, state, task_state)

    def test_message_replacement_ack_completes_handoff_candidate_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(
                repo, base_sha, gates=(), completion_mode="handoff"
            )
            replacement_ack = self.install_applied_message_ack(
                repo, state, task_state
            )
            (repo / "src" / "task.py").write_text("VALUE = 9\n", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                candidate = self.submit_and_seal_current_candidate(
                    repo, state, mode="handoff"
                )
            self.assertEqual(candidate.semantic_ack_event_id, replacement_ack)
            self.assertEqual(
                task_state["implementation_candidate"]["semantic_ack_event_id"],
                replacement_ack,
            )
            self.assertEqual(task_state["candidate_lifecycle_status"], "candidate_sealed")
            self.assertEqual(task_state["completion_mode_ack_event_id"], "ack-event-001")

            finalize = self.finalize_args()
            finalize.handoff = True
            old_cwd = Path.cwd()
            handoff_output = io.StringIO()
            try:
                hloop.os.chdir(repo)
                with contextlib.redirect_stdout(handoff_output):
                    hloop.cmd_worker_finalize(finalize)
            finally:
                hloop.os.chdir(old_cwd)
            self.assertNotIn("HERDR_LOOP_ROLE_DONE", handoff_output.getvalue())

            seal_output = io.StringIO()
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "require_worker_pane_quiesced_and_closed"),
                contextlib.redirect_stdout(seal_output),
            ):
                hloop.cmd_worker_seal(
                    argparse.Namespace(
                        repo=str(repo),
                        task_id="T001",
                        attempt_id="T001-A001",
                        validation_command=["true"],
                        validation_summary="Manager seal validation passed",
                    )
                )
            self.assertIn(
                "HERDR_LOOP_ROLE_DONE:run-v053:T001:T001-A001:done",
                seal_output.getvalue(),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.harvest_and_dry_run_merge(repo, state, task_state)

    def test_message_replacement_ack_tampering_after_submit_blocks_candidate_seal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(
                repo, base_sha, gates=(), completion_mode="commit"
            )
            self.install_applied_message_ack(repo, state, task_state)
            (repo / "src" / "task.py").write_text("VALUE = 10\n", encoding="utf-8")
            old_cwd = Path.cwd()
            try:
                hloop.os.chdir(repo)
                with contextlib.redirect_stdout(io.StringIO()):
                    hloop.cmd_worker_submit(self.submit_args("commit"))
            finally:
                hloop.os.chdir(old_cwd)

            task_state["semantic_ack_barrier"]["attempt_id"] = "T001-A000"
            state_path = repo / hloop.LOOP_DIR / "STATE.json"
            hloop.write_text(
                state_path,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            state_before = state_path.read_bytes()
            head_before = self.git(repo, "rev-parse", "HEAD")
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "require_worker_pane_quiesced_preserved"),
                self.assertRaisesRegex(hloop.HLoopError, "report identity is not fully bound"),
            ):
                hloop.cmd_worker_candidate_seal(
                    argparse.Namespace(
                        repo=str(repo), task_id="T001", candidate_revision=1
                    )
                )
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), head_before)
            self.assertNotIn("candidate_seal", task_state)
            self.assertNotIn("implementation_candidate", task_state)

    def test_patch_fix_resubmission_stays_in_attempt_and_stops_at_round_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            (repo / "src" / "task.py").write_text("VALUE = 4\n", encoding="utf-8")
            candidate1, seal1 = self.submit_candidate(repo, state, task_state)
            finding1 = self.patch_finding("one")
            review1 = hloop.hloop_worker_candidate.record_patch_review(
                seal1,
                review_attempt_id="PR-T001-R001",
                review_round=1,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                findings=(finding1,),
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
            finding2 = self.patch_finding("two")
            review2 = hloop.hloop_worker_candidate.record_patch_review(
                seal2,
                review_attempt_id="PR-T001-R002",
                review_round=2,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                findings=(finding2,),
            )
            decision2 = hloop.apply_patch_review_record(
                repo, state, "T001", task_state, review2
            )

            self.assertTrue(decision2.requires_user_decision)
            self.assertEqual(task_state["candidate_lifecycle_status"], "blocked_patch_review")
            self.assertEqual(task_state["active_attempt_id"], "T001-A001")
            self.assertEqual(decision2.last_candidate_sha, seal2.candidate_sha)
            self.assertEqual(decision2.automatic_task_ids, ())

    def test_authorize_extra_round_requires_exact_decision_input_and_consumes_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            _seal2, review2 = self.exhaust_patch_review_rounds(
                repo, state, task_state
            )
            self.install_extra_round_evidence(
                repo,
                state,
                review2.unresolved_finding_fingerprints[0],
            )
            authorize_args = SimpleNamespace(
                repo=str(repo),
                task_id="T001",
                decision_id="D007",
                authorization_input_id="U0005",
            )

            state["decisions"]["D007"]["source_findings"] = [
                "sha256:" + "0" * 64
            ]
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "blocking evidence"),
            ):
                hloop.cmd_patch_review_authorize_extra_round(authorize_args)
            self.assertNotIn(
                "patch_review_extra_round_authorization", task_state
            )

            state["decisions"]["D007"]["source_findings"] = list(
                review2.unresolved_finding_fingerprints
            )
            state["inputs_index"]["U0005"]["prompt_digest"] = "0" * 64
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "prompt digest"),
            ):
                hloop.cmd_patch_review_authorize_extra_round(authorize_args)
            input_record = hloop.hloop_requirements.InputRecord.from_record(
                json.loads(
                    (
                        hloop.local_sensitive_input_dir(repo) / "U0005.json"
                    ).read_text(encoding="utf-8")
                )
            )
            state["inputs_index"]["U0005"]["prompt_digest"] = (
                input_record.prompt_digest
            )

            wrong_input = hloop.hloop_requirements.InputRecord.capture(
                input_id="U0004",
                received_at="2026-07-19T11:59:00Z",
                source="user",
                raw_input="another valid user response",
            )
            hloop.write_text(
                hloop.local_sensitive_input_dir(repo) / "U0004.json",
                json.dumps(wrong_input.to_record(), ensure_ascii=False, indent=2)
                + "\n",
            )
            state["inputs_index"]["U0004"] = {
                "prompt_digest": wrong_input.prompt_digest,
                "redactions": [],
                "received_at": wrong_input.received_at,
                "source": "user",
            }
            authorize_args.authorization_input_id = "U0004"
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "unique latest"),
            ):
                hloop.cmd_patch_review_authorize_extra_round(authorize_args)
            self.assertNotIn(
                "patch_review_extra_round_authorization", task_state
            )

            ambiguous_input = hloop.hloop_requirements.InputRecord.capture(
                input_id="U0006",
                received_at="2026-07-19T12:00:00Z",
                source="user",
                raw_input="simultaneous user response",
            )
            hloop.write_text(
                hloop.local_sensitive_input_dir(repo) / "U0006.json",
                json.dumps(
                    ambiguous_input.to_record(), ensure_ascii=False, indent=2
                )
                + "\n",
            )
            state["inputs_index"]["U0006"] = {
                "prompt_digest": ambiguous_input.prompt_digest,
                "redactions": [],
                "received_at": ambiguous_input.received_at,
                "source": "user",
            }
            authorize_args.authorization_input_id = "U0005"
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "ambiguous"),
            ):
                hloop.cmd_patch_review_authorize_extra_round(authorize_args)
            state["inputs_index"].pop("U0006")

            with mock.patch.object(hloop, "preflight_loop", return_value=state):
                self.assertEqual(
                    hloop.cmd_patch_review_authorize_extra_round(authorize_args),
                    0,
                )
            authorization = hloop.task_patch_review_extra_round_authorization(
                task_state
            )
            self.assertIsNotNone(authorization)
            self.assertEqual(authorization.granted_review_round, 3)
            self.assertEqual(authorization.status, "active")
            self.assertEqual(
                task_state["candidate_lifecycle_status"], "patch_fix_running"
            )
            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "already authorized"),
            ):
                hloop.cmd_patch_review_authorize_extra_round(authorize_args)

            (repo / "src" / "task.py").write_text(
                "VALUE = 60\n", encoding="utf-8"
            )
            _candidate3, seal3 = self.submit_candidate(repo, state, task_state)
            review3 = hloop.hloop_worker_candidate.record_patch_review(
                seal3,
                review_attempt_id="PR-T001-R003",
                review_round=3,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="passed",
            )
            decision3 = hloop.apply_patch_review_record(
                repo, state, "T001", task_state, review3
            )
            self.assertEqual(decision3.action, "finalize_allowed")
            consumed = hloop.task_patch_review_extra_round_authorization(
                task_state
            )
            self.assertEqual(consumed.status, "consumed")
            self.assertEqual(
                consumed.consumed_review_attempt_id, "PR-T001-R003"
            )

            review4 = hloop.hloop_worker_candidate.record_patch_review(
                seal3,
                review_attempt_id="PR-T001-R004",
                review_round=4,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="passed",
            )
            with self.assertRaisesRegex(hloop.HLoopError, "already consumed"):
                hloop.apply_patch_review_record(
                    repo, state, "T001", task_state, review4
                )

    def test_pending_next_candidate_can_bootstrap_same_extra_round_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            _seal2, review2 = self.exhaust_patch_review_rounds(
                repo, state, task_state
            )

            task_path = repo / hloop.LOOP_DIR / "tasks" / "T001.md"
            task_meta = hloop.read_frontmatter(task_path)
            task_meta["acceptance"] = [
                *task_meta["acceptance"],
                "accepted decision authorizes exactly one third round",
            ]
            hloop.replace_frontmatter_record(task_path, task_meta)
            task_state["task_contract_digest"] = hloop.hashlib.sha256(
                task_path.read_bytes()
            ).hexdigest()
            task_state["candidate_lifecycle_status"] = "patch_fix_running"
            state["phase"] = "running"
            hloop.write_text(
                repo / hloop.LOOP_DIR / "STATE.json",
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            (repo / "src" / "task.py").write_text(
                "VALUE = 70\n", encoding="utf-8"
            )
            candidate3, _seal3 = self.submit_candidate(repo, state, task_state)
            self.assertEqual(
                task_state["candidate_lifecycle_status"], "patch_review_pending"
            )
            start_args = SimpleNamespace(
                repo=str(repo),
                task_id="T001",
                review_attempt_id=None,
                worktree=str(repo / "round-3-review"),
                manager_pane=None,
                direction="down",
                launcher="pane",
                runner="tui",
                agent_provider=None,
                agent_model=None,
                agent_effort=None,
                dry_run=True,
            )
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "load_state", return_value=state),
                self.assertRaisesRegex(hloop.HLoopError, "round limit"),
            ):
                hloop.cmd_patch_review_start(start_args)
            self.install_extra_round_evidence(
                repo,
                state,
                review2.unresolved_finding_fingerprints[0],
            )
            with mock.patch.object(hloop, "preflight_loop", return_value=state):
                self.assertEqual(
                    hloop.cmd_patch_review_authorize_extra_round(
                        SimpleNamespace(
                            repo=str(repo),
                            task_id="T001",
                            decision_id="D007",
                            authorization_input_id="U0005",
                        )
                    ),
                    0,
                )
            authorization = hloop.task_patch_review_extra_round_authorization(
                task_state
            )
            self.assertEqual(
                authorization.task_contract_digest,
                candidate3.task_contract_digest,
            )
            self.assertEqual(
                authorization.blocked_candidate_sha, review2.candidate_sha
            )
            self.assertEqual(
                task_state["candidate_lifecycle_status"], "patch_review_pending"
            )
            agent_config = {
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "effort": "xhigh",
                "sources": {
                    "provider": "defaults",
                    "model": "defaults",
                    "effort": "defaults",
                },
                "provenance": {"provider": [], "model": [], "effort": []},
            }
            invocation = SimpleNamespace(as_record=lambda: {"provider": "codex"})
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "load_state", return_value=state),
                mock.patch.object(
                    hloop, "patch_reviewer_agent_config", return_value=agent_config
                ),
                mock.patch.object(
                    hloop,
                    "role_agent_command",
                    return_value=("codex", invocation),
                ),
            ):
                self.assertEqual(hloop.cmd_patch_review_start(start_args), 0)

    def test_patch_review_record_rejects_mixed_identity_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo, base_sha = self.make_repo(Path(directory))
            state, task_state = self.write_fixture(repo, base_sha)
            (repo / "src" / "task.py").write_text("VALUE = 6\n", encoding="utf-8")
            _candidate, seal = self.submit_candidate(repo, state, task_state)
            retained = hloop.hloop_worker_candidate.record_patch_review(
                seal,
                review_attempt_id="PR-T001-R001",
                review_round=1,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                findings=(self.patch_finding("legacy-retained"),),
            ).to_record()
            retained.pop("finding_identity_contract")
            retained.pop("findings")
            task_state["patch_review_history"] = [retained]
            state_path = repo / hloop.LOOP_DIR / "STATE.json"
            hloop.write_text(
                state_path,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            patch_root = repo / hloop.LOOP_DIR / "patch-reviews"
            artifact_bytes_before = {
                path.relative_to(patch_root).as_posix(): path.read_bytes()
                for path in patch_root.rglob("*")
                if path.is_file()
            } if patch_root.exists() else {}
            state_before = state_path.read_bytes()
            prospective = hloop.patch_review_file(
                repo, "T001", "T001-A001", "PR-T001-R002"
            )
            args = argparse.Namespace(
                repo=str(repo),
                task_id="T001",
                review_attempt_id="PR-T001-R002",
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                unresolved_finding_evidence=[
                    (
                        "src/task.py",
                        "task_semantic",
                        "Trigger semantic",
                        "Impact semantic",
                        "Fix semantic",
                    )
                ],
                follow_up_finding_evidence=[],
                unresolved_finding_fingerprint=[],
                follow_up_finding_fingerprint=[],
            )

            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                self.assertRaisesRegex(
                    hloop.HLoopError, "legacy and canonical.*cannot be mixed"
                ),
            ):
                hloop.cmd_patch_review_record(args)

            artifact_bytes_after = {
                path.relative_to(patch_root).as_posix(): path.read_bytes()
                for path in patch_root.rglob("*")
                if path.is_file()
            } if patch_root.exists() else {}
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(artifact_bytes_after, artifact_bytes_before)
            self.assertFalse(prospective.exists())

    def test_patch_review_harvest_rejects_mixed_identity_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, base_sha = self.make_repo(root)
            state, task_state = self.write_fixture(repo, base_sha)
            (repo / "src" / "task.py").write_text("VALUE = 7\n", encoding="utf-8")
            _candidate, seal = self.submit_candidate(repo, state, task_state)
            canonical_retained = hloop.hloop_worker_candidate.record_patch_review(
                seal,
                review_attempt_id="PR-T001-R001",
                review_round=1,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="fix_required",
                findings=(self.patch_finding("legacy-retained-harvest"),),
            )
            retained = canonical_retained.to_record()
            retained.pop("finding_identity_contract")
            retained.pop("findings")
            task_state["patch_review_history"] = [retained]

            review_attempt_id = "PR-T001-R002"
            review = hloop.hloop_worker_candidate.record_patch_review(
                seal,
                review_attempt_id=review_attempt_id,
                review_round=2,
                reviewer_provider="codex",
                reviewer_model="gpt-5.6-sol",
                reviewer_effort="xhigh",
                verdict="passed",
            )
            review_worktree = root / "patch-review-worktree"
            self.git(root, "clone", str(repo), str(review_worktree))
            source = hloop.patch_review_file(
                review_worktree,
                "T001",
                seal.attempt_id,
                review_attempt_id,
            )
            source.parent.mkdir(parents=True, exist_ok=True)
            source_payload = hloop.exact_json_bytes(review.to_record())
            source.write_bytes(source_payload)
            state["patch_reviews"] = {
                review_attempt_id: {
                    "status": "running",
                    "gate_status": "running",
                    "task_id": "T001",
                    "task_attempt_id": seal.attempt_id,
                    "review_attempt_id": review_attempt_id,
                    "review_round": 2,
                    "finding_identity_contract": (
                        hloop.hloop_worker_candidate.PATCH_REVIEW_FINDING_IDENTITY_CONTRACT
                    ),
                    "candidate_sha": seal.candidate_sha,
                    "candidate_artifact_digest": seal.candidate_artifact_digest,
                    "sealed_task_contract_digest": seal.task_contract_digest,
                    "agent_provider": "codex",
                    "agent_model": "gpt-5.6-sol",
                    "agent_effort": "xhigh",
                    "worktree": str(review_worktree),
                    "baseline_dirty_files": [],
                }
            }
            state_path = repo / hloop.LOOP_DIR / "STATE.json"
            hloop.write_text(
                state_path,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            patch_root = repo / hloop.LOOP_DIR / "patch-reviews"
            artifact_bytes_before = {
                path.relative_to(patch_root).as_posix(): path.read_bytes()
                for path in patch_root.rglob("*")
                if path.is_file()
            } if patch_root.exists() else {}
            state_before = state_path.read_bytes()
            target = hloop.patch_review_file(
                repo,
                "T001",
                seal.attempt_id,
                review_attempt_id,
            )
            args = argparse.Namespace(
                repo=str(repo),
                review_attempt_id=review_attempt_id,
                keep_pane=False,
                session_cleanup="none",
            )

            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(
                    hloop, "semantic_ack_barrier_blocking", return_value=""
                ),
                self.assertRaisesRegex(
                    hloop.HLoopError, "legacy and canonical.*cannot be mixed"
                ),
            ):
                hloop.cmd_patch_review_harvest(args)

            artifact_bytes_after = {
                path.relative_to(patch_root).as_posix(): path.read_bytes()
                for path in patch_root.rglob("*")
                if path.is_file()
            } if patch_root.exists() else {}
            self.assertEqual(source.read_bytes(), source_payload)
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(artifact_bytes_after, artifact_bytes_before)
            self.assertFalse(target.exists())

            task_state["patch_review_history"] = [canonical_retained.to_record()]
            hloop.write_text(
                state_path,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            real_git = hloop.git

            def preserve_registered_test_worktree(cwd: Path, git_args: list[str]) -> str:
                if cwd == repo and git_args[:3] == ["worktree", "remove", "--force"]:
                    return ""
                return real_git(cwd, git_args)

            with (
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(
                    hloop, "semantic_ack_barrier_blocking", return_value=""
                ),
                mock.patch.object(hloop, "cleanup_completed_agent_pane"),
                mock.patch.object(hloop, "revoke_active_role_report_identity"),
                mock.patch.object(
                    hloop, "git", side_effect=preserve_registered_test_worktree
                ),
            ):
                self.assertEqual(hloop.cmd_patch_review_harvest(args), 0)

            harvested = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(source.read_bytes(), source_payload)
            self.assertEqual(target.read_bytes(), source_payload)
            self.assertEqual(
                harvested["tasks"]["T001"]["current_patch_review"][
                    "review_attempt_id"
                ],
                review_attempt_id,
            )
            self.assertEqual(
                harvested["patch_reviews"][review_attempt_id]["status"],
                "reported",
            )

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
            self.assertEqual(task_state["changed_files"], ["src/task.py"])
            self.assertTrue(
                all(
                    path != hloop.LOOP_DIR.as_posix()
                    and not path.startswith(hloop.LOOP_DIR.as_posix() + "/")
                    for path in task_state["changed_files"]
                )
            )
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
