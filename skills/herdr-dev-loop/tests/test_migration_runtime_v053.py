"""Runtime integration tests for the schema-3.3 migration transaction."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_migration_runtime_v053", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


def migrate_args(repo: Path, mode: str) -> argparse.Namespace:
    return argparse.Namespace(
        repo=str(repo),
        apply=mode == "apply",
        resume=mode == "resume",
        rollback=mode == "rollback",
        dry_run=mode == "dry-run",
    )


class MigrationRuntimeV053Tests(unittest.TestCase):
    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.namespace = "runtime-v053"
        hloop.configure_loop_namespace(self.namespace)
        self.addCleanup(hloop.configure_loop_namespace, self.previous_namespace)
        hloop.loop_path(self.repo).mkdir(parents=True)

    def legacy_state(self, *, task_status: str | None = None) -> dict:
        tasks = {}
        if task_status is not None:
            tasks["T001"] = {
                "id": "T001",
                "status": task_status,
                "kind": "implementation",
                "task_origin": "planned",
                "remediation_round": 0,
                "source_finding": "",
            }
        return {
            "state_format_version": 3,
            "schema_revision": 2,
            "namespace": self.namespace,
            "run_id": "run-v052",
            "skill_version": "0.5.2",
            "tasks": tasks,
            "batches": {},
            "reviews": {},
            "gaps": {},
            "review_protocol": "native",
            "review_policy": {
                "cadence": "batch",
                "pre_final_protocol": "native",
                "manual_final_protocol": "codex-review-multi-v2",
                "max_fix_rounds": 2,
                "scope_expansion_action": "follow_up",
                "final_required": "complete_zero_verified_actionable_findings",
                "lane_count": "auto",
            },
            "review_convergence": {
                "status": "not-started",
                "fix_round": 0,
                "authorized_extra_rounds": 0,
                "extra_round_authorization_refs": [],
            },
            "manual_final_review": {"status": "not-started"},
            "execution_metrics": {"review_fix_rounds": 0},
        }

    def write_legacy_loop(self, *, task_status: str | None = None) -> dict:
        state = self.legacy_state(task_status=task_status)
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if task_status is not None:
            path = hloop.task_file(self.repo, "T001")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                hloop.frontmatter({"id": "T001", "status": task_status})
                + "\n\n# Legacy task\n",
                encoding="utf-8",
            )
        return state

    def revision_three_task(self, *, status: str = "queued") -> dict:
        return {
            "id": "T001",
            "run_id": "run-v052",
            "skill_version": "0.5.3",
            "contract_schema_revision": 3,
            "kind": "implementation",
            "status": status,
            "created_from": "PLAN.md",
            "branch": "ai/T001",
            "base_ref": "main",
            "base_sha": "a" * 40,
            "priority": "P1",
            "batch_id": "B001",
            "depends_on": [],
            "write_allow": ["src/task.py"],
            "write_deny": [],
            "acceptance": ["compatibility is preserved"],
            "validation_minimum": "python3 -m unittest tests.targeted",
            "worker_protocol": "native",
            "worker_qa_profile": "repo-default",
            "worker_agent_provider": "codex",
            "worker_agent_model": "gpt-5.6-sol",
            "worker_agent_effort": "xhigh",
            "preserved_invariants": ["migration remains atomic"],
            "regression_checks": ["mixed revision migration passes"],
            "risk_class": "high",
            "required_gates": ["patch_review", "full_suite"],
            "investigation_goal": "repair migration compatibility",
            "implementation_ready_evidence": ["the compatibility gap is isolated"],
            "exploration_budget_minutes": 15,
            "history_search_allowed": False,
            "task_origin": "planned",
            "release_scope_revision": 1,
            "plan_item_refs": ["P003"],
            "requirement_refs": ["REQ-004"],
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

    def revision_three_state_projection(self, *, status: str = "merged") -> dict:
        task = self.revision_three_task(status=status)
        return {
            "status": status,
            "task_contract_digest": "a" * 64,
            **hloop.revision_three_complete_state_projection(task),
        }

    def implementation_candidate(
        self,
        *,
        task_attempt_id: str = "T001-A001",
        candidate_revision: int = 1,
        base_sha: str = "a" * 40,
    ) -> hloop.hloop_worker_candidate.ImplementationCandidate:
        return hloop.hloop_worker_candidate.ImplementationCandidate(
            run_id="run-v052",
            skill_version="0.5.2",
            task_id="T001",
            attempt_id=task_attempt_id,
            task_contract_digest="sha256:" + "1" * 64,
            semantic_ack_event_id="ack-event-001",
            base_sha=base_sha,
            candidate_revision=candidate_revision,
            completion_mode="commit",
            candidate_tree_sha="b" * 40,
            candidate_artifact_ref=(
                "implementation-candidates/T001/"
                f"{task_attempt_id}/{candidate_revision}.json"
            ),
            changed_files=("src/task.py",),
            validation_commands=("python3 -m unittest",),
            validation_results=("passed",),
            validation_summary="passed",
            invariant_evidence=("migration remains atomic",),
            regression_evidence=("compatibility passes",),
            self_review_summary="reviewed",
            residual_risks=(),
            unrun_checks=(),
        )

    def write_candidate_artifact(
        self,
        repo: Path,
        candidate: hloop.hloop_worker_candidate.ImplementationCandidate,
    ) -> tuple[Path, bytes]:
        path = hloop.implementation_candidate_file(
            repo,
            candidate.task_id,
            candidate.attempt_id,
            candidate.candidate_revision,
        )
        payload = hloop.exact_json_bytes(candidate.to_record())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path, payload

    def archived_task_attempt(
        self, task_attempt_id: str, *, worker_base_sha: str = "a" * 40
    ) -> dict:
        attempt_no = int(task_attempt_id.rsplit("A", 1)[1])
        branch = f"ai/T001-a{attempt_no:03d}"
        return {
            "agent_id": "T001",
            "role": "worker",
            "previous_status": "running",
            "reason": "superseded by a later canonical task attempt",
            "at": "2026-07-18T00:01:00+00:00",
            "attempt_id": task_attempt_id,
            "attempt_no": attempt_no,
            "branch": branch,
            "worker_base_sha": worker_base_sha,
            "head_sha": None,
            "worktree": "/removed/worktrees/T001",
            "pane_id": "wH:p41",
            "superseded_manager_messages": [],
            "archived_branch": (
                f"{branch}-archive-a{attempt_no:03d}-20260718000100"
            ),
        }

    def patch_review_record(
        self,
        review_attempt_id: str,
        *,
        task_attempt_id: str = "T001-A001",
        candidate_revision: int = 1,
        base_sha: str = "a" * 40,
    ) -> dict:
        candidate = self.implementation_candidate(
            task_attempt_id=task_attempt_id,
            candidate_revision=candidate_revision,
            base_sha=base_sha,
        )
        candidate_payload = hloop.exact_json_bytes(candidate.to_record())
        seal = hloop.hloop_worker_candidate.seal_candidate(
            candidate,
            candidate_sha="c" * 40,
            candidate_artifact_digest=hloop._sha256_labelled(candidate_payload),
            observed_tree_sha=candidate.candidate_tree_sha,
            active_attempt_id=candidate.attempt_id,
            active_task_contract_digest=candidate.task_contract_digest,
            approved_ack_event_id=candidate.semantic_ack_event_id,
        )
        return hloop.hloop_worker_candidate.record_patch_review(
            seal,
            review_attempt_id=review_attempt_id,
            review_round=1,
            reviewer_provider="codex",
            reviewer_model="gpt-5.6-sol",
            reviewer_effort="xhigh",
            verdict="passed",
        ).to_record()

    def write_terminal_role_fixture(
        self,
        repo: Path,
        role_kind: str,
        *,
        patch_base_sha: str = "a" * 40,
    ) -> tuple[dict, Path, dict]:
        state = self.legacy_state()
        head_sha = "d" * 40
        common = {
            "harvested_at": "2026-07-18T00:00:00+00:00",
            "pane_id": "",
            "pane_cleanup_status": "closed",
            "worktree_cleanup_status": "removed",
            "head_sha": head_sha,
            "skill_version": "0.5.2",
        }
        if role_kind == "reviewer":
            role_id = "R001"
            role = {
                **common,
                "status": "reported",
                "gate_status": "reported",
                "attempt_id": "R001-A001",
                "artifact_status": "reported",
                "mode": "single",
            }
            state["reviews"] = {role_id: role}
            path = hloop.review_file(repo, role_id)
            artifact = hloop.frontmatter(
                {
                    "review_id": role_id,
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "head_sha": head_sha,
                    "status": "reported",
                }
            ) + "\n\n# Review\n"
        elif role_kind == "patch-reviewer":
            role_id = "PR-T001-A001-R001"
            record = self.patch_review_record(role_id, base_sha=patch_base_sha)
            path = hloop.patch_review_file(repo, "T001", "T001-A001", role_id)
            role = {
                **common,
                "status": "reported",
                "gate_status": "reported",
                "attempt_id": f"{role_id}-A001",
                "review_attempt_id": role_id,
                "task_id": "T001",
                "task_attempt_id": "T001-A001",
                "review_round": 1,
                "finding_identity_contract": record[
                    "finding_identity_contract"
                ],
                "candidate_sha": record["candidate_sha"],
                "candidate_artifact_digest": record[
                    "candidate_artifact_digest"
                ],
                "sealed_task_contract_digest": record["task_contract_digest"],
                "agent_provider": record["reviewer_provider"],
                "agent_model": record["reviewer_model"],
                "agent_effort": record["reviewer_effort"],
                "verdict": record["verdict"],
                "artifact_path": str(path),
            }
            state["tasks"] = {
                "T001": {
                    "id": "T001",
                    "status": "merged",
                    "kind": "implementation",
                    "task_origin": "planned",
                    "remediation_round": 0,
                    "source_finding": "",
                    "patch_review_history": [deepcopy(record)],
                }
            }
            state["patch_reviews"] = {role_id: role}
            artifact = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        elif role_kind == "gap":
            role_id = "G001"
            role = {
                **common,
                "status": "reported",
                "gate_status": "reported",
                "attempt_id": "G001-A001",
                "artifact_status": "aligned",
            }
            state["gaps"] = {role_id: role}
            path = hloop.gap_file(repo, role_id)
            artifact = hloop.frontmatter(
                {
                    "gap_id": role_id,
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "head_sha": head_sha,
                    "status": "aligned",
                }
            ) + "\n\n# Gap\n"
        elif role_kind == "advisor":
            role_id = "A001/P1"
            role = {
                **common,
                "status": "reported",
                "gate_status": "reported",
                "attempt_id": "A001-P1-A001",
                "participant_id": "P1",
                "artifact_status": "advised",
            }
            state["advice"] = {
                "A001": {
                    "status": "reported",
                    "gate_status": "reported",
                    "participants": [role],
                }
            }
            path = hloop.advice_file(repo, "A001", "P1")
            artifact = hloop.frontmatter(
                {
                    "advice_id": "A001",
                    "participant_id": "P1",
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "head_sha": head_sha,
                    "status": "advised",
                }
            ) + "\n\n# Advice\n"
        elif role_kind == "specification-scout":
            role_id = "S001"
            role = {
                **common,
                "status": "reported",
                "gate_status": "reported",
                "attempt_id": "S001-A001",
                "role_id": role_id,
            }
            state["specification_scout_run"] = role
            path = hloop.specification_scout_file(repo)
            artifact = hloop.frontmatter(
                {
                    "role_id": role_id,
                    "attempt_id": "S001-A001",
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "head_sha": head_sha,
                    "status": "reported",
                }
            ) + "\n\n# Scout\n"
        elif role_kind == "decision-liaison":
            role_id = "L-D001"
            role = {
                **common,
                "status": "responded",
                "gate_status": "responded",
                "attempt_id": "L-D001-A001",
                "role_id": role_id,
                "decision_id": "D001",
            }
            state["decision_liaisons"] = {"D001": role}
            path = hloop.decision_liaison_file(repo, "D001")
            artifact = hloop.frontmatter(
                {
                    "decision_id": "D001",
                    "attempt_id": "L-D001-A001",
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "head_sha": head_sha,
                    "responded_by": "liaison",
                    "responded_at": "2026-07-18T00:01:00+00:00",
                    "response_source": "explicit-user-input",
                    "response_channel": "same-pane",
                    "response_turn": "after-question",
                    "user_input_received_at": "2026-07-18T00:00:30+00:00",
                    "user_input_kind": "option",
                    "selected_option": "opt_1",
                }
            ) + "\n\n# 回答\n承認する\n"
        else:  # pragma: no cover - test helper misuse
            raise AssertionError(role_kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(artifact, encoding="utf-8")
        return state, path, role

    def assert_migration_rejected_without_writes(
        self,
        repo: Path,
        state: dict,
        artifact_paths: tuple[Path, ...],
        pattern: str,
        *,
        check_dry_run: bool = False,
    ) -> None:
        hloop.state_path(repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        source_state = hloop.state_path(repo).read_bytes()
        source_artifacts = {
            path: path.read_bytes() if path.is_file() else None
            for path in artifact_paths
        }
        if check_dry_run:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(hloop.cmd_migrate(migrate_args(repo, "dry-run")), 2)
            self.assertRegex(output.getvalue(), pattern)
            self.assertEqual(hloop.state_path(repo).read_bytes(), source_state)
            for path, source in source_artifacts.items():
                self.assertEqual(
                    path.read_bytes() if path.is_file() else None,
                    source,
                )
            self.assertFalse(hloop.migration_runtime_root(repo).exists())
        with self.assertRaisesRegex(hloop.HLoopError, pattern):
            hloop.cmd_migrate(migrate_args(repo, "apply"))
        self.assertEqual(hloop.state_path(repo).read_bytes(), source_state)
        for path, source in source_artifacts.items():
            self.assertEqual(path.read_bytes() if path.is_file() else None, source)
        self.assertFalse(hloop.migration_runtime_root(repo).exists())

    def loop_file_snapshot(self, repo: Path) -> dict[str, bytes]:
        root = hloop.loop_path(repo)
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_dry_run_is_read_only_and_reports_canonical_runtime_projection(self):
        self.write_legacy_loop(task_status="queued")
        state_before = hloop.state_path(self.repo).read_bytes()
        task_before = hloop.task_file(self.repo, "T001").read_bytes()

        output = io.StringIO()
        with redirect_stdout(output):
            code = hloop.cmd_migrate(migrate_args(self.repo, "dry-run"))

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["to_revision"], 3)
        self.assertEqual(payload["transaction_action"], "prepare")
        self.assertEqual(payload["remediation_history"]["status"], "recovered")
        self.assertIn(hloop.state_path(self.repo).relative_to(self.repo).as_posix(), payload["changed_paths"])
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), state_before)
        self.assertEqual(hloop.task_file(self.repo, "T001").read_bytes(), task_before)
        self.assertFalse(hloop.migration_runtime_root(self.repo).exists())

    def test_partial_apply_resumes_and_rollback_restores_exact_source_bytes(self):
        state = self.write_legacy_loop(task_status="queued")
        source_state = hloop.state_path(self.repo).read_bytes()
        source_task = hloop.task_file(self.repo, "T001").read_bytes()
        plan, pairs, _steps = hloop.prepare_migration_transaction(self.repo, state)
        hloop.persist_prepared_migration(self.repo, plan, pairs)
        task_rel = hloop.task_file(self.repo, "T001").relative_to(self.repo).as_posix()
        hloop.write_bytes_durable(hloop.task_file(self.repo, "T001"), pairs[task_rel][1])

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "resume")), 0)
        migrated = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        migrated_task = hloop.parse_frontmatter_text(
            hloop.task_file(self.repo, "T001").read_text(encoding="utf-8")
        )
        marker = hloop.load_migration_marker(self.repo)
        self.assertEqual(migrated["schema_revision"], 3)
        self.assertEqual(migrated_task["contract_schema_revision"], 2)
        self.assertEqual(marker["status"], "committed")
        self.assertEqual(migrated["manager_identity"]["status"], "requested-only")
        self.assertEqual(
            migrated["manager_identity"]["assertion"], "unavailable-warning"
        )
        self.assertEqual(
            migrated["review_protocol_selection"]["ordinary"]["key"],
            "reviewer.protocol",
        )
        self.assertFalse(migrated["config_identity_projection"]["stale"])

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "rollback")), 0)
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), source_state)
        self.assertEqual(hloop.task_file(self.repo, "T001").read_bytes(), source_task)
        self.assertEqual(hloop.load_migration_marker(self.repo)["status"], "rolled-back")

    def test_prepared_apply_and_resume_preserve_valid_role_evidence(self):
        for mode in ("apply", "resume"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, artifact, _terminal = self.write_terminal_role_fixture(
                    repo, "patch-reviewer"
                )
                hloop.state_path(repo).write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                artifact_before = artifact.read_bytes()
                plan, pairs, _steps = hloop.prepare_migration_transaction(repo, state)
                hloop.persist_prepared_migration(repo, plan, pairs)

                self.assertEqual(hloop.cmd_migrate(migrate_args(repo, mode)), 0)

                migrated = json.loads(
                    hloop.state_path(repo).read_text(encoding="utf-8")
                )
                self.assertEqual(migrated["schema_revision"], 3)
                self.assertEqual(hloop.load_migration_marker(repo)["status"], "committed")
                self.assertEqual(artifact.read_bytes(), artifact_before)

    def test_prepared_apply_and_resume_revalidate_role_artifact_before_any_write(self):
        for mode in ("apply", "resume"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, artifact, _terminal = self.write_terminal_role_fixture(
                    repo, "patch-reviewer"
                )
                hloop.state_path(repo).write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                plan, pairs, _steps = hloop.prepare_migration_transaction(repo, state)
                hloop.persist_prepared_migration(repo, plan, pairs)
                artifact.write_bytes(
                    artifact.read_bytes() + b"tampered-after-prepare\n"
                )
                before = self.loop_file_snapshot(repo)

                with self.assertRaisesRegex(hloop.HLoopError, "Patch Review|artifact"):
                    hloop.cmd_migrate(migrate_args(repo, mode))

                self.assertEqual(self.loop_file_snapshot(repo), before)

    def test_resume_revalidates_dirty_role_worktree_before_any_write(self):
        state, _artifact, terminal = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        role_worktree = self.repo / "role-worktree-after-prepare"
        terminal.update(
            {
                "worktree": str(role_worktree),
                "worktree_cleanup_status": "",
            }
        )
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        plan, pairs, _steps = hloop.prepare_migration_transaction(self.repo, state)
        hloop.persist_prepared_migration(self.repo, plan, pairs)
        role_worktree.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=role_worktree,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        dirty_product = role_worktree / "product.py"
        dirty_product.write_text("DIRTY = True\n", encoding="utf-8")
        dirty_before = dirty_product.read_bytes()
        before = self.loop_file_snapshot(self.repo)

        with self.assertRaisesRegex(hloop.HLoopError, "dirty role worktree"):
            hloop.cmd_migrate(migrate_args(self.repo, "resume"))

        self.assertEqual(self.loop_file_snapshot(self.repo), before)
        self.assertEqual(dirty_product.read_bytes(), dirty_before)

    def test_first_material_mutation_closes_rollback_permanently(self):
        self.write_legacy_loop()
        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        mutation_args = argparse.Namespace(
            command="task", task_command="update", dry_run=False
        )

        hloop.record_first_v053_mutation(self.repo, mutation_args)
        state = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        marker = hloop.load_migration_marker(self.repo)
        state_before_rollback = hloop.state_path(self.repo).read_bytes()
        self.assertTrue(state["first_v053_mutation_at"])
        self.assertEqual(state["first_v053_mutation_command"], "task update")
        self.assertFalse(state["migration_v053"]["rollback_eligible"])
        self.assertEqual(
            marker["first_v053_mutation_at"], state["first_v053_mutation_at"]
        )

        with self.assertRaisesRegex(hloop.HLoopError, "rollback is forbidden"):
            hloop.cmd_migrate(migrate_args(self.repo, "rollback"))
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), state_before_rollback)

    def test_resume_rejects_a_persisted_plan_identity_mismatch(self):
        state = self.write_legacy_loop()
        source = hloop.state_path(self.repo).read_bytes()
        plan, pairs, _steps = hloop.prepare_migration_transaction(self.repo, state)
        hloop.persist_prepared_migration(self.repo, plan, pairs)
        manifest = hloop.migration_generation_root(
            self.repo, plan.migration_generation
        ) / "ARTIFACTS.json"
        manifest.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(hloop.HLoopError, "does not match"):
            hloop.cmd_migrate(migrate_args(self.repo, "resume"))
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), source)

    def test_result_is_migrated_even_when_legacy_task_artifact_is_absent(self):
        self.write_legacy_loop(task_status="result_reported")
        hloop.task_file(self.repo, "T001").unlink()
        result = hloop.result_file(self.repo, "T001")
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(
            hloop.frontmatter(
                {
                    "task_id": "T001",
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "attempt_id": "T001-A001",
                    "status": "done",
                    "merge_ready": True,
                    "branch": "ai/T001",
                    "head_sha": "b" * 40,
                    "base_sha": "a" * 40,
                    "changed_files": ["src/task.py"],
                    "validation_recorded": True,
                    "validation_commands": ["python3 -m unittest"],
                    "validation_results": ["passed"],
                    "validation_summary": "passed",
                    "blocking_questions": [],
                    "handoff": False,
                }
            )
            + "\n\n# Legacy result\n",
            encoding="utf-8",
        )
        source = result.read_bytes()

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        migrated = hloop.parse_frontmatter_text(result.read_text(encoding="utf-8"))
        self.assertEqual(migrated["contract_schema_revision"], 2)
        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "rollback")), 0)
        self.assertEqual(result.read_bytes(), source)

    def test_first_mutation_crash_repairs_state_from_marker_conservatively(self):
        self.write_legacy_loop()
        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        marker = hloop.load_migration_marker(self.repo)
        marker["first_v053_mutation_at"] = "2026-07-17T07:00:00+00:00"
        marker["first_v053_mutation_command"] = "task update"
        hloop.write_bytes_durable(
            hloop.migration_active_marker_path(self.repo),
            hloop.migration_json_bytes(marker),
        )

        hloop.record_first_v053_mutation(
            self.repo,
            argparse.Namespace(command="worker", worker_command="start", dry_run=False),
        )

        repaired = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        self.assertEqual(
            repaired["first_v053_mutation_at"], marker["first_v053_mutation_at"]
        )
        self.assertEqual(repaired["first_v053_mutation_command"], "task update")
        self.assertFalse(repaired["migration_v053"]["rollback_eligible"])
        self.assertTrue(repaired["migration_v053"]["first_mutation_recorded"])

    def test_current_state_without_transaction_marker_cannot_claim_rollback(self):
        self.write_legacy_loop()
        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        hloop.migration_active_marker_path(self.repo).unlink()
        source = hloop.state_path(self.repo).read_bytes()

        with self.assertRaisesRegex(hloop.HLoopError, "requires an active"):
            hloop.cmd_migrate(migrate_args(self.repo, "rollback"))
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), source)

    def test_ambiguous_remediation_history_blocks_without_preparing(self):
        state = self.write_legacy_loop()
        state["tasks"] = {
            "T009": {
                "id": "T009",
                "status": "merged",
                "kind": "implementation",
                "task_origin": "finding",
                "remediation_round": 1,
                "source_finding": "",
            },
            "T010": {
                "id": "T010",
                "status": "merged",
                "kind": "implementation",
                "task_origin": "finding",
                "remediation_round": 1,
                "source_finding": "",
            },
        }
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        source = hloop.state_path(self.repo).read_bytes()

        output = io.StringIO()
        with redirect_stdout(output):
            code = hloop.cmd_migrate(migrate_args(self.repo, "dry-run"))

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertTrue(payload["blocking_reasons"])
        self.assertIn("cannot be grouped uniquely", " ".join(payload["blocking_reasons"]))
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), source)
        self.assertFalse(hloop.migration_runtime_root(self.repo).exists())

    def test_orphan_legacy_contract_artifact_blocks_without_writes(self):
        self.write_legacy_loop()
        orphan = hloop.task_file(self.repo, "T999")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text(
            hloop.frontmatter({"id": "T999", "status": "queued"})
            + "\n\n# Orphan\n",
            encoding="utf-8",
        )
        source = hloop.state_path(self.repo).read_bytes()

        output = io.StringIO()
        with redirect_stdout(output):
            code = hloop.cmd_migrate(migrate_args(self.repo, "dry-run"))

        self.assertEqual(code, 2)
        self.assertIn("orphan legacy contract artifacts", output.getvalue())
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), source)
        self.assertFalse(hloop.migration_runtime_root(self.repo).exists())

    def test_only_legacy_finalize_may_cross_the_worker_schema_boundary(self):
        legacy = {"state_format_version": 3, "schema_revision": 2}
        current = {"state_format_version": 3, "schema_revision": 3}
        finalize = argparse.Namespace(command="worker", worker_command="finalize")

        self.assertFalse(hloop.command_requires_state_schema_guard(finalize))
        hloop.assert_worker_finalize_schema_compatible(legacy, current, 2)
        with self.assertRaisesRegex(hloop.HLoopError, "requires schema-3.3"):
            hloop.assert_worker_finalize_schema_compatible(legacy, current, 3)

    def test_queued_contract_migrates_when_runtime_state_is_already_terminal(self):
        state = self.write_legacy_loop(task_status="queued")
        state["tasks"]["T001"]["status"] = "merged"
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        migrated_state = json.loads(
            hloop.state_path(self.repo).read_text(encoding="utf-8")
        )
        migrated_task = hloop.parse_frontmatter_text(
            hloop.task_file(self.repo, "T001").read_text(encoding="utf-8")
        )
        self.assertEqual(migrated_state["tasks"]["T001"]["status"], "merged")
        self.assertEqual(migrated_task["status"], "queued")
        self.assertEqual(migrated_task["contract_schema_revision"], 2)

    def test_queued_contract_migrates_across_supported_runtime_lifecycle(self):
        for runtime_status in ("running", "result_reported", "done", "merged"):
            with self.subTest(runtime_status=runtime_status):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Path(directory)
                    subprocess.run(
                        ["git", "init", "--initial-branch=main"],
                        cwd=repo,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    hloop.loop_path(repo).mkdir(parents=True)
                    state = self.legacy_state(task_status="queued")
                    state["tasks"]["T001"]["status"] = runtime_status
                    hloop.state_path(repo).write_text(
                        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    path = hloop.task_file(repo, "T001")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        hloop.frontmatter({"id": "T001", "status": "queued"})
                        + "\n\n# Legacy task\n",
                        encoding="utf-8",
                    )

                    self.assertEqual(hloop.cmd_migrate(migrate_args(repo, "apply")), 0)
                    migrated_state = json.loads(
                        hloop.state_path(repo).read_text(encoding="utf-8")
                    )
                    migrated_task = hloop.parse_frontmatter_text(
                        path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(
                        migrated_state["tasks"]["T001"]["status"], runtime_status
                    )
                    self.assertEqual(migrated_task["status"], "queued")
                    self.assertEqual(migrated_task["contract_schema_revision"], 2)

    def test_queued_task_and_result_resume_and_rollback_as_one_exact_transaction(self):
        state = self.write_legacy_loop(task_status="queued")
        state["tasks"]["T001"]["status"] = "merged"
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = hloop.result_file(self.repo, "T001")
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(
            hloop.frontmatter(
                {
                    "task_id": "T001",
                    "run_id": "run-v052",
                    "skill_version": "0.5.2",
                    "attempt_id": "T001-A001",
                    "status": "done",
                    "merge_ready": True,
                    "branch": "ai/T001",
                    "head_sha": "b" * 40,
                    "base_sha": "a" * 40,
                    "changed_files": ["src/task.py"],
                    "validation_recorded": True,
                    "validation_commands": ["python3 -m unittest"],
                    "validation_results": ["passed"],
                    "validation_summary": "passed",
                    "blocking_questions": [],
                    "handoff": False,
                }
            )
            + "\n\n# Legacy result\n",
            encoding="utf-8",
        )
        originals = {
            path.relative_to(self.repo).as_posix(): path.read_bytes()
            for path in (
                hloop.state_path(self.repo),
                hloop.task_file(self.repo, "T001"),
                result,
            )
        }
        plan, pairs, _steps = hloop.prepare_migration_transaction(self.repo, state)
        expected_paths = sorted(originals)
        self.assertEqual(sorted(plan.changed_paths), expected_paths)
        self.assertEqual(sorted(pairs), expected_paths)
        hloop.persist_prepared_migration(self.repo, plan, pairs)

        task_rel = hloop.task_file(self.repo, "T001").relative_to(self.repo).as_posix()
        hloop.write_bytes_durable(hloop.task_file(self.repo, "T001"), pairs[task_rel][1])
        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "resume")), 0)

        migrated_state = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        migrated_task = hloop.parse_frontmatter_text(
            hloop.task_file(self.repo, "T001").read_text(encoding="utf-8")
        )
        migrated_result = hloop.parse_frontmatter_text(result.read_text(encoding="utf-8"))
        marker = hloop.load_migration_marker(self.repo)
        self.assertEqual(migrated_state["schema_revision"], 3)
        self.assertEqual(migrated_state["tasks"]["T001"]["status"], "merged")
        self.assertEqual(migrated_task["status"], "queued")
        self.assertEqual(migrated_task["contract_schema_revision"], 2)
        self.assertEqual(migrated_result["contract_schema_revision"], 2)
        self.assertEqual(marker["status"], "committed")
        self.assertEqual(
            sorted(item["path"] for item in marker["artifacts"]), expected_paths
        )

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "rollback")), 0)
        for relative, source in originals.items():
            self.assertEqual((self.repo / relative).read_bytes(), source)
        self.assertEqual(hloop.load_migration_marker(self.repo)["status"], "rolled-back")

    def test_harvested_terminal_patch_reviewer_migrates_but_unsafe_lifecycle_blocks(self):
        state, artifact, terminal = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        clean_worktree = self.repo / "removed-patch-review-worktree"
        terminal["worktree"] = str(clean_worktree)
        hloop.assert_migration_safe(self.repo, state)

        terminal.update(
            {
                "status": "aborted",
                "gate_status": "aborted",
                "aborted_at": "2026-07-18T00:02:00+00:00",
                "abort_reason": "harvest retained before migration",
            }
        )
        hloop.assert_migration_safe(self.repo, state)

        for label, changes in (
            ("starting", {"status": "starting", "gate_status": "starting"}),
            ("running", {"status": "running", "gate_status": "running"}),
            ("waiting", {"status": "waiting", "gate_status": "waiting"}),
            ("unharvested", {"harvested_at": ""}),
            ("aborted-without-time", {"aborted_at": ""}),
            ("aborted-without-reason", {"abort_reason": ""}),
            ("live-pane", {"pane_id": "wH:p99"}),
            ("cleanup-failed", {"worktree_cleanup_status": "failed"}),
        ):
            with self.subTest(label=label):
                unsafe = deepcopy(state)
                unsafe["patch_reviews"]["PR-T001-A001-R001"].update(changes)
                with self.assertRaises(hloop.HLoopError):
                    hloop.assert_migration_safe(self.repo, unsafe)

        dirty_worktree = self.repo / "dirty-patch-review-worktree"
        dirty_worktree.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=dirty_worktree,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (dirty_worktree / "product.py").write_text("DIRTY = True\n", encoding="utf-8")
        unsafe = deepcopy(state)
        unsafe["patch_reviews"]["PR-T001-A001-R001"].update(
            {"worktree": str(dirty_worktree), "worktree_cleanup_status": ""}
        )
        self.assert_migration_rejected_without_writes(
            self.repo,
            unsafe,
            (artifact, dirty_worktree / "product.py"),
            r"dirty role worktree",
            check_dry_run=True,
        )

    def test_migration_requires_fail_closed_git_status_for_existing_role_worktree(self):
        state, artifact, terminal = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        non_git_temp = tempfile.TemporaryDirectory()
        self.addCleanup(non_git_temp.cleanup)
        non_git_worktree = Path(non_git_temp.name)
        (non_git_worktree / "product.py").write_text(
            "VALUE = 'unproven'\n", encoding="utf-8"
        )
        terminal.update(
            {
                "worktree": str(non_git_worktree),
                "worktree_cleanup_status": "",
            }
        )

        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            (artifact, non_git_worktree / "product.py"),
            r"without a valid Git status proof",
            check_dry_run=True,
        )

        corrupt_worktree = self.repo / "corrupt-role-worktree"
        corrupt_worktree.mkdir()
        (corrupt_worktree / ".git").write_text(
            "gitdir: /missing/corrupt-role-gitdir\n", encoding="utf-8"
        )
        (corrupt_worktree / "product.py").write_text(
            "VALUE = 'unproven'\n", encoding="utf-8"
        )
        corrupt_state, corrupt_artifact, corrupt_role = (
            self.write_terminal_role_fixture(self.repo, "patch-reviewer")
        )
        corrupt_role.update(
            {
                "worktree": str(corrupt_worktree),
                "worktree_cleanup_status": "",
            }
        )
        self.assert_migration_rejected_without_writes(
            self.repo,
            corrupt_state,
            (corrupt_artifact, corrupt_worktree / "product.py"),
            r"without a valid Git status proof",
            check_dry_run=True,
        )

        nested_non_worktree = self.repo / "nested-non-worktree"
        nested_non_worktree.mkdir()
        nested_state, nested_artifact, nested_role = (
            self.write_terminal_role_fixture(self.repo, "patch-reviewer")
        )
        nested_role.update(
            {
                "worktree": str(nested_non_worktree),
                "worktree_cleanup_status": "",
            }
        )
        self.assert_migration_rejected_without_writes(
            self.repo,
            nested_state,
            (nested_artifact,),
            r"valid Git status proof: .*Git top-level mismatch",
            check_dry_run=True,
        )

        status_worktree = self.repo / "status-error-role-worktree"
        status_worktree.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=status_worktree,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        status_state, status_artifact, status_role = (
            self.write_terminal_role_fixture(self.repo, "patch-reviewer")
        )
        status_role.update(
            {
                "worktree": str(status_worktree),
                "worktree_cleanup_status": "",
            }
        )
        with mock.patch.object(
            hloop,
            "porcelain_paths",
            side_effect=hloop.HLoopError("simulated Git status failure"),
        ):
            self.assert_migration_rejected_without_writes(
                self.repo,
                status_state,
                (status_artifact,),
                r"valid Git status proof: .*simulated Git status failure",
                check_dry_run=True,
            )

    def test_exact_skipped_scout_sentinel_is_not_a_dispatched_role(self):
        state = self.legacy_state()
        sentinel = {
            "status": "skipped",
            "gate_status": "skipped",
            "policy": "auto",
            "reasons": [],
            "completed_at": "2026-07-18T00:00:00+00:00",
        }
        state["specification_scout_run"] = sentinel
        hloop.assert_migration_safe(self.repo, state)

        malformed_sentinels = (
            {key: value for key, value in sentinel.items() if key != "completed_at"},
            {**sentinel, "attempt_id": "S001-A001"},
            {**sentinel, "policy": "off"},
            {**sentinel, "reasons": [""]},
            {**sentinel, "completed_at": True},
            {**sentinel, "completed_at": "not-a-timestamp"},
            {**sentinel, "completed_at": "2026-07-18 00:00:00+00:00"},
            {**sentinel, "completed_at": "2026-07-18T00:00:00Z"},
        )
        for malformed in malformed_sentinels:
            with self.subTest(malformed=malformed):
                unsafe = deepcopy(state)
                unsafe["specification_scout_run"] = malformed
                self.assert_migration_rejected_without_writes(
                    self.repo,
                    unsafe,
                    (),
                    r"noncanonical role lifecycle: specification-scout",
                    check_dry_run=True,
                )

    def historical_patch_review_fixture(
        self,
        *,
        base_sha: str = "a" * 40,
    ) -> tuple[dict, Path, Path, Path, dict]:
        state, artifact, superseded = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer", patch_base_sha=base_sha
        )
        task_path = hloop.task_file(self.repo, "T001")
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            hloop.frontmatter({"id": "T001", "status": "merged"})
            + "\n\n# Historical task evidence\n",
            encoding="utf-8",
        )
        candidate = self.implementation_candidate(
            task_attempt_id="T001-A001", base_sha=base_sha
        )
        candidate_path, _payload = self.write_candidate_artifact(
            self.repo, candidate
        )
        current_record = self.patch_review_record(
            "PR-T001-A002-R001",
            task_attempt_id="T001-A002",
            base_sha=base_sha,
        )
        state["tasks"]["T001"].update(
            {
                "active_attempt_id": "T001-A002",
                "attempt_id": "T001-A002",
                "attempt_no": 2,
                "attempts": [
                    self.archived_task_attempt(
                        "T001-A001", worker_base_sha=base_sha
                    )
                ],
                "patch_review_history": [current_record],
            }
        )
        return state, task_path, artifact, candidate_path, superseded

    def test_superseded_reported_and_aborted_patch_reviews_migrate_as_audit_history(self):
        state, task_path, artifact, candidate_path, superseded = (
            self.historical_patch_review_fixture()
        )

        hloop.assert_migration_safe(self.repo, state)

        superseded.update(
            {
                "status": "aborted",
                "gate_status": "aborted",
                "aborted_at": "2026-07-18T00:02:00+00:00",
                "abort_reason": "superseded after a later task attempt was sealed",
            }
        )

        hloop.assert_migration_safe(self.repo, state)

        for missing_field in ("aborted_at", "abort_reason"):
            with self.subTest(missing_field=missing_field):
                incomplete = deepcopy(state)
                incomplete["patch_reviews"]["PR-T001-A001-R001"][missing_field] = ""
                self.assert_migration_rejected_without_writes(
                    self.repo,
                    incomplete,
                    (task_path, artifact, candidate_path),
                    r"without abort provenance",
                    check_dry_run=True,
                )

    def test_retained_patch_review_audit_set_requires_one_identity_kind(self):
        state, task_path, historical_artifact, candidate_path, historical_role = (
            self.historical_patch_review_fixture()
        )
        current_record = deepcopy(
            state["tasks"]["T001"]["patch_review_history"][0]
        )
        current_id = current_record["review_attempt_id"]
        current_artifact = hloop.patch_review_file(
            self.repo, "T001", "T001-A002", current_id
        )
        current_artifact.parent.mkdir(parents=True, exist_ok=True)
        current_artifact.write_bytes(hloop.exact_json_bytes(current_record))
        current_role = deepcopy(historical_role)
        current_role.update(
            {
                "attempt_id": f"{current_id}-A001",
                "review_attempt_id": current_id,
                "task_attempt_id": "T001-A002",
                "finding_identity_contract": current_record[
                    "finding_identity_contract"
                ],
                "candidate_sha": current_record["candidate_sha"],
                "candidate_artifact_digest": current_record[
                    "candidate_artifact_digest"
                ],
                "sealed_task_contract_digest": current_record[
                    "task_contract_digest"
                ],
                "artifact_path": str(current_artifact),
            }
        )
        state["patch_reviews"][current_id] = current_role

        hloop.assert_migration_safe(self.repo, state)

        legacy_historical = json.loads(historical_artifact.read_bytes())
        legacy_historical.pop("finding_identity_contract")
        legacy_historical.pop("findings")
        historical_artifact.write_bytes(hloop.exact_json_bytes(legacy_historical))
        historical_role.pop("finding_identity_contract")
        evidence_paths = (
            task_path,
            historical_artifact,
            current_artifact,
            candidate_path,
        )
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            evidence_paths,
            r"Patch Review audit set: legacy and canonical.*cannot be mixed",
            check_dry_run=True,
        )

        legacy_current = deepcopy(current_record)
        legacy_current.pop("finding_identity_contract")
        legacy_current.pop("findings")
        current_artifact.write_bytes(hloop.exact_json_bytes(legacy_current))
        current_role.pop("finding_identity_contract")
        state["tasks"]["T001"]["patch_review_history"] = [legacy_current]
        hloop.assert_migration_safe(self.repo, state)

    def test_historical_patch_review_rejects_active_or_unproven_task_attempt(self):
        state, task_path, artifact, candidate_path, _superseded = (
            self.historical_patch_review_fixture()
        )

        active = deepcopy(state)
        active["tasks"]["T001"].update(
            {
                "active_attempt_id": "T001-A001",
                "attempt_id": "T001-A001",
                "attempt_no": 1,
            }
        )
        self.assert_migration_rejected_without_writes(
            self.repo,
            active,
            (task_path, artifact, candidate_path),
            r"active task attempt is missing its canonical history entry",
            check_dry_run=True,
        )

        for label, attempts, pattern in (
            ("missing", [], r"exactly one archived task-attempt record"),
            (
                "ambiguous",
                [
                    self.archived_task_attempt("T001-A001"),
                    self.archived_task_attempt("T001-A001"),
                ],
                r"exactly one archived task-attempt record",
            ),
        ):
            with self.subTest(label=label):
                unsafe = deepcopy(state)
                unsafe["tasks"]["T001"]["attempts"] = attempts
                self.assert_migration_rejected_without_writes(
                    self.repo,
                    unsafe,
                    (task_path, artifact, candidate_path),
                    pattern,
                    check_dry_run=True,
                )

        invalid_archive = deepcopy(state)
        invalid_archive["tasks"]["T001"]["attempts"][0]["archived_branch"] = ""
        self.assert_migration_rejected_without_writes(
            self.repo,
            invalid_archive,
            (task_path, artifact, candidate_path),
            r"noncanonical archived task-attempt provenance: archived_branch",
            check_dry_run=True,
        )

    def test_historical_patch_review_validates_canonical_active_attempt_first(self):
        state, task_path, artifact, candidate_path, _superseded = (
            self.historical_patch_review_fixture()
        )
        evidence_paths = (task_path, artifact, candidate_path)

        without_compat_attempt = deepcopy(state)
        without_compat_attempt["tasks"]["T001"].pop("attempt_id")
        hloop.assert_migration_safe(self.repo, without_compat_attempt)

        for label, changes, expected_field in (
            (
                "conflicting-attempt-id",
                {"attempt_id": "T001-A001"},
                "attempt_id",
            ),
            ("attempt-number-drift", {"attempt_no": 1}, "attempt_no"),
        ):
            with self.subTest(label=label):
                unsafe = deepcopy(state)
                unsafe["tasks"]["T001"].update(changes)
                self.assert_migration_rejected_without_writes(
                    self.repo,
                    unsafe,
                    evidence_paths,
                    rf"noncanonical active task-attempt identity: {expected_field}",
                    check_dry_run=True,
                )

    def test_historical_patch_review_accepts_exact_40_and_64_character_worker_base_sha(self):
        for length in (40, 64):
            with self.subTest(length=length):
                base_sha = "a" * length
                state, task_path, artifact, candidate_path, _superseded = (
                    self.historical_patch_review_fixture(base_sha=base_sha)
                )
                hloop.assert_migration_safe(self.repo, state)

        mismatched = deepcopy(state)
        mismatched["tasks"]["T001"]["attempts"][0][
            "worker_base_sha"
        ] = "b" * 64
        self.assert_migration_rejected_without_writes(
            self.repo,
            mismatched,
            (task_path, artifact, candidate_path),
            r"noncanonical archived task-attempt provenance: worker_base_sha",
            check_dry_run=True,
        )

        mismatched_base = deepcopy(state)
        mismatched_base["tasks"]["T001"]["attempts"][0][
            "worker_base_sha"
        ] = "e" * 40
        self.assert_migration_rejected_without_writes(
            self.repo,
            mismatched_base,
            (task_path, artifact, candidate_path),
            r"noncanonical archived task-attempt provenance: worker_base_sha",
            check_dry_run=True,
        )

    def test_historical_patch_review_rejects_coerced_worker_base_sha(self):
        numeric_sha = "1" * 40
        numeric_state, numeric_task, numeric_artifact, numeric_candidate, _ = (
            self.historical_patch_review_fixture(base_sha=numeric_sha)
        )
        numeric_state["tasks"]["T001"]["attempts"][0][
            "worker_base_sha"
        ] = int(numeric_sha)
        self.assert_migration_rejected_without_writes(
            self.repo,
            numeric_state,
            (numeric_task, numeric_artifact, numeric_candidate),
            r"noncanonical archived task-attempt provenance: worker_base_sha",
            check_dry_run=True,
        )

        padded_sha = "a" * 40
        padded_state, padded_task, padded_artifact, padded_candidate, _ = (
            self.historical_patch_review_fixture(base_sha=padded_sha)
        )
        padded_state["tasks"]["T001"]["attempts"][0][
            "worker_base_sha"
        ] = f" {padded_sha} "
        self.assert_migration_rejected_without_writes(
            self.repo,
            padded_state,
            (padded_task, padded_artifact, padded_candidate),
            r"noncanonical archived task-attempt provenance: worker_base_sha",
            check_dry_run=True,
        )

    def test_historical_patch_review_rejects_invalid_candidate_evidence(self):
        state, task_path, artifact, candidate_path, _superseded = (
            self.historical_patch_review_fixture()
        )
        candidate_payload = candidate_path.read_bytes()
        review_payload = artifact.read_bytes()
        evidence_paths = (task_path, artifact, candidate_path)

        candidate_path.unlink()
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            evidence_paths,
            r"implementation candidate is missing",
            check_dry_run=True,
        )
        candidate_path.write_bytes(candidate_payload)

        candidate_path.write_bytes(candidate_payload + b"\n")
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            evidence_paths,
            r"candidate artifact digest mismatch",
            check_dry_run=True,
        )
        candidate_path.write_bytes(candidate_payload)

        candidate_path.write_text("{}\n", encoding="utf-8")
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            evidence_paths,
            r"invalid implementation candidate",
            check_dry_run=True,
        )
        candidate_path.write_bytes(candidate_payload)

        invalid_ref_record = json.loads(candidate_payload)
        invalid_ref_record["candidate_artifact_ref"] = (
            "implementation-candidates/T001/T001-A001/2.json"
        )
        candidate_path.write_bytes(hloop.exact_json_bytes(invalid_ref_record))
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            evidence_paths,
            r"invalid implementation candidate.*candidate_artifact_ref",
            check_dry_run=True,
        )
        candidate_path.write_bytes(candidate_payload)

        identity_mutations = (
            ("run_id", {"run_id": "foreign-run"}),
            ("skill_version", {"skill_version": "0.5.1"}),
            (
                "task_id",
                {
                    "task_id": "T002",
                    "attempt_id": "T002-A001",
                    "candidate_artifact_ref": (
                        "implementation-candidates/T002/T002-A001/1.json"
                    ),
                },
            ),
            (
                "attempt_id",
                {
                    "attempt_id": "T001-A009",
                    "candidate_artifact_ref": (
                        "implementation-candidates/T001/T001-A009/1.json"
                    ),
                },
            ),
            (
                "task_contract_digest",
                {"task_contract_digest": "sha256:" + "2" * 64},
            ),
            ("semantic_ack_event_id", {"semantic_ack_event_id": "other-ack"}),
            ("base_sha", {"base_sha": "d" * 40}),
            (
                "candidate_revision",
                {
                    "candidate_revision": 2,
                    "candidate_artifact_ref": (
                        "implementation-candidates/T001/T001-A001/2.json"
                    ),
                },
            ),
        )
        for field, changes in identity_mutations:
            with self.subTest(field=field):
                candidate_record = json.loads(candidate_payload)
                candidate_record.update(changes)
                mutated_payload = hloop.exact_json_bytes(candidate_record)
                candidate_path.write_bytes(mutated_payload)
                review_record = json.loads(review_payload)
                review_record["candidate_artifact_digest"] = (
                    hloop._sha256_labelled(mutated_payload)
                )
                artifact.write_text(
                    json.dumps(review_record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                unsafe = deepcopy(state)
                unsafe["patch_reviews"]["PR-T001-A001-R001"][
                    "candidate_artifact_digest"
                ] = review_record["candidate_artifact_digest"]
                self.assert_migration_rejected_without_writes(
                    self.repo,
                    unsafe,
                    evidence_paths,
                    rf"candidate/Patch Review identity mismatch: .*{field}",
                    check_dry_run=True,
                )
                candidate_path.write_bytes(candidate_payload)
                artifact.write_bytes(review_payload)

    def test_multiple_current_patch_review_history_matches_fail_before_write(self):
        state, artifact, _role = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        target_record = json.loads(artifact.read_bytes())
        state["tasks"]["T001"]["patch_review_history"] = [
            target_record,
            deepcopy(target_record),
        ]
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            (artifact,),
            r"without exactly one canonical task patch-review history entry",
            check_dry_run=True,
        )

    def test_historical_patch_review_role_identity_mismatch_fails_before_write(self):
        state, task_path, artifact, candidate_path, _superseded = (
            self.historical_patch_review_fixture()
        )
        mismatched = deepcopy(state)
        mismatched["patch_reviews"]["PR-T001-A001-R001"]["candidate_sha"] = "e" * 40
        self.assert_migration_rejected_without_writes(
            self.repo,
            mismatched,
            (task_path, artifact, candidate_path),
            r"mismatched artifact: candidate_sha",
            check_dry_run=True,
        )

    def test_every_non_worker_role_requires_its_canonical_terminal_lifecycle(self):
        borrowed_terminal = {
            "reviewer": ("responded", "responded"),
            "patch-reviewer": ("triaged", "triaged"),
            "gap": ("consumed", "consumed"),
            "advisor": ("completed", "completed"),
            "specification-scout": ("triaged", "triaged"),
            "decision-liaison": ("reported", "reported"),
        }
        for role_kind, lifecycle in borrowed_terminal.items():
            with self.subTest(role_kind=role_kind), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, artifact, role = self.write_terminal_role_fixture(
                    repo, role_kind
                )
                hloop.assert_migration_safe(repo, state)
                role["status"], role["gate_status"] = lifecycle
                self.assert_migration_rejected_without_writes(
                    repo,
                    state,
                    (artifact,),
                    rf"noncanonical role lifecycle: {role_kind}",
                )

    def test_every_non_worker_role_requires_a_canonical_harvested_artifact(self):
        role_kinds = (
            "reviewer",
            "patch-reviewer",
            "gap",
            "advisor",
            "specification-scout",
            "decision-liaison",
        )
        for role_kind in role_kinds:
            with self.subTest(role_kind=role_kind), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, artifact, role = self.write_terminal_role_fixture(
                    repo, role_kind
                )
                hloop.assert_migration_safe(repo, state)
                valid_artifact = artifact.read_bytes()

                artifact.unlink()
                self.assert_migration_rejected_without_writes(
                    repo,
                    state,
                    (artifact,),
                    r"missing|noncanonical artifact",
                )

                wrong_artifact = (
                    b"{}\n"
                    if role_kind == "patch-reviewer"
                    else (
                        hloop.frontmatter(
                            {"status": "reported", "wrong_role_id": "X001"}
                        )
                        + "\n\n# Wrong role\n"
                    ).encode()
                )
                artifact.write_bytes(wrong_artifact)
                self.assert_migration_rejected_without_writes(
                    repo,
                    state,
                    (artifact,),
                    r"invalid artifact|noncanonical artifact",
                )
                artifact.write_bytes(valid_artifact)
                if role_kind == "patch-reviewer":
                    recorded_path = role["artifact_path"]
                    role["artifact_path"] = str(artifact.with_name("wrong-kind.json"))
                    self.assert_migration_rejected_without_writes(
                        repo,
                        state,
                        (artifact,),
                        r"mismatched artifact path",
                    )
                    role["artifact_path"] = recorded_path
                hloop.assert_migration_safe(repo, state)

    def test_harvested_aborted_audit_roles_require_canonical_abort_provenance(self):
        for role_kind in (
            "reviewer",
            "patch-reviewer",
            "gap",
            "advisor",
            "specification-scout",
        ):
            with self.subTest(role_kind=role_kind), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, _artifact, role = self.write_terminal_role_fixture(
                    repo, role_kind
                )
                role.update(
                    {
                        "status": "aborted",
                        "gate_status": "aborted",
                        "aborted_at": "2026-07-18T00:02:00+00:00",
                        "abort_reason": "harvested artifact retained before migration",
                    }
                )
                hloop.assert_migration_safe(repo, state)

                for missing_field in ("aborted_at", "abort_reason"):
                    unsafe = deepcopy(state)
                    unsafe_role = next(
                        item
                        for kind, _role_id, item in hloop.iter_all_roles(unsafe)
                        if kind == role_kind
                    )
                    unsafe_role[missing_field] = ""
                    with self.subTest(missing_field=missing_field), self.assertRaisesRegex(
                        hloop.HLoopError,
                        rf"aborted {role_kind} without abort provenance",
                    ):
                        hloop.assert_migration_safe(repo, unsafe)

    def test_agent_abort_padded_reason_migrates_and_is_preserved_exactly(self):
        state, artifact, _role = self.write_terminal_role_fixture(
            self.repo, "reviewer"
        )
        hloop.save_state(self.repo, state)
        reason = " retained "

        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                hloop.cmd_agent_abort(
                    argparse.Namespace(
                        repo=str(self.repo),
                        agent_id="R001",
                        reason=reason,
                        keep_worktree=False,
                        force_cleanup=False,
                    )
                ),
                0,
            )

        aborted = hloop.load_state(self.repo)
        self.assertEqual(aborted["reviews"]["R001"]["abort_reason"], reason)
        artifact_source = artifact.read_bytes()
        hloop.assert_migration_safe(self.repo, aborted)

        with redirect_stdout(io.StringIO()):
            self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)

        migrated = hloop.load_state(self.repo)
        self.assertEqual(migrated["schema_revision"], 3)
        self.assertEqual(migrated["reviews"]["R001"]["abort_reason"], reason)
        self.assertEqual(artifact.read_bytes(), artifact_source)

    def test_non_worker_lifecycle_provenance_requires_canonical_types_and_shapes(self):
        role_kinds = (
            "reviewer",
            "patch-reviewer",
            "gap",
            "advisor",
            "specification-scout",
            "decision-liaison",
        )
        malformed_timestamps = (
            True,
            1,
            ["2026-07-18T00:00:00+00:00"],
            "not-a-timestamp",
            " 2026-07-18T00:00:00+00:00 ",
            "2026-07-18T00:00:00",
            "2026-07-18 00:00:00+00:00",
            "2026-07-18T00:00:00Z",
            "2026-07-18T00:00:00+01:00",
            "2026-07-18T00:00:00.123456+00:00",
        )
        for role_kind in role_kinds:
            with self.subTest(role_kind=role_kind), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, artifact, _role = self.write_terminal_role_fixture(
                    repo, role_kind
                )
                for value in malformed_timestamps:
                    unsafe = deepcopy(state)
                    unsafe_role = next(
                        item
                        for kind, _role_id, item in hloop.iter_all_roles(unsafe)
                        if kind == role_kind
                    )
                    unsafe_role["harvested_at"] = value
                    with self.subTest(harvested_at=value):
                        self.assert_migration_rejected_without_writes(
                            repo,
                            unsafe,
                            (artifact,),
                            rf"noncanonical harvested_at.*{role_kind}",
                        )

        for role_kind in (
            "reviewer",
            "patch-reviewer",
            "gap",
            "advisor",
            "specification-scout",
        ):
            with self.subTest(aborted_role_kind=role_kind), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state, artifact, role = self.write_terminal_role_fixture(
                    repo, role_kind
                )
                role.update(
                    {
                        "status": "aborted",
                        "gate_status": "aborted",
                        "aborted_at": "2026-07-18T00:02:00+00:00",
                        "abort_reason": "harvested artifact retained before migration",
                    }
                )
                for field, value in (
                    ("aborted_at", True),
                    ("aborted_at", "not-a-timestamp"),
                    ("aborted_at", " 2026-07-18T00:02:00+00:00 "),
                    ("abort_reason", True),
                    ("abort_reason", ["retained"]),
                    ("abort_reason", ""),
                ):
                    unsafe = deepcopy(state)
                    unsafe_role = next(
                        item
                        for kind, _role_id, item in hloop.iter_all_roles(unsafe)
                        if kind == role_kind
                    )
                    unsafe_role[field] = value
                    expected_pattern = (
                        rf"aborted {role_kind} without abort provenance"
                        if field == "abort_reason" and value == ""
                        else rf"noncanonical {field}.*{role_kind}"
                    )
                    with self.subTest(field=field, value=value):
                        self.assert_migration_rejected_without_writes(
                            repo,
                            unsafe,
                            (artifact,),
                            expected_pattern,
                        )

    def test_patch_review_orphan_fails_before_write(self):
        state, artifact, _role = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        state["tasks"].pop("T001")
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            (artifact,),
            r"Patch Review task is missing",
            check_dry_run=True,
        )

    def test_patch_review_identity_tampering_fails_before_write(self):
        state, artifact, _role = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        valid_artifact = artifact.read_bytes()

        for field, value in (
            ("reviewer_model", "gpt-5.6-terra"),
            ("candidate_revision", 2),
        ):
            with self.subTest(field=field):
                tampered = json.loads(valid_artifact)
                tampered[field] = value
                if field == "candidate_revision":
                    tampered["candidate_artifact_ref"] = (
                        "implementation-candidates/T001/T001-A001/2.json"
                    )
                artifact.write_text(
                    json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                self.assert_migration_rejected_without_writes(
                    self.repo,
                    state,
                    (artifact,),
                    rf"{field}",
                    check_dry_run=True,
                )
        artifact.write_bytes(valid_artifact)

    def test_semantic_patch_review_state_rejects_legacy_artifact_and_history(self):
        state, artifact, role = self.write_terminal_role_fixture(
            self.repo, "patch-reviewer"
        )
        legacy_record = json.loads(artifact.read_bytes())
        legacy_record.pop("finding_identity_contract")
        legacy_record.pop("findings")
        artifact.write_text(
            json.dumps(legacy_record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        state["tasks"]["T001"]["patch_review_history"] = [
            deepcopy(legacy_record)
        ]

        semantic_contract = role.pop("finding_identity_contract")
        hloop.assert_migration_safe(self.repo, state)
        role["finding_identity_contract"] = semantic_contract
        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            (artifact,),
            r"finding_identity_contract",
            check_dry_run=True,
        )

    def test_revision_three_task_artifact_missing_id_fails_before_write(self):
        state = self.legacy_state()
        task_path = hloop.task_file(self.repo, "T001")
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task = self.revision_three_task()
        for field in (
            "id",
            "release_scope_revision",
            "remediation_round",
        ):
            task.pop(field)
        task["task_contract_digest"] = "b" * 64
        task_path.write_text(
            hloop.frontmatter(task) + "\n\n# Invalid revision three task\n",
            encoding="utf-8",
        )
        parsed_task = hloop.parse_frontmatter_text(
            task_path.read_text(encoding="utf-8")
        )
        state_task = hloop.revision_three_complete_state_projection(parsed_task)
        state_task["status"] = parsed_task["status"]
        state_task["task_contract_digest"] = hashlib.sha256(
            task_path.read_bytes()
        ).hexdigest()
        state["tasks"] = {"T001": state_task}

        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            (task_path,),
            r"cannot migrate task artifact T001.*id",
            check_dry_run=True,
        )

    def test_revision_three_task_in_revision_two_state_is_validated_and_preserved(self):
        state = self.write_legacy_loop()
        state_task = self.revision_three_state_projection()
        task_path = hloop.task_file(self.repo, "T001")
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_record = self.revision_three_task()
        task_record["release_scope_revision"] = "1"
        task_record["remediation_round"] = "0"
        task_path.write_text(
            hloop.frontmatter(task_record)
            + "\n\n# Revision three task\n",
            encoding="utf-8",
        )
        state_task["task_contract_digest"] = hashlib.sha256(
            task_path.read_bytes()
        ).hexdigest()
        state["tasks"] = {"T001": deepcopy(state_task)}
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        original_state = hloop.state_path(self.repo).read_bytes()
        original_task = task_path.read_bytes()

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                hloop.cmd_migrate(migrate_args(self.repo, "dry-run")),
                0,
                output.getvalue(),
            )
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), original_state)
        self.assertEqual(task_path.read_bytes(), original_task)
        self.assertFalse(hloop.migration_runtime_root(self.repo).exists())

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        migrated = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        self.assertEqual(migrated["tasks"]["T001"], state_task)
        self.assertEqual(task_path.read_bytes(), original_task)
        self.assertEqual(migrated["first_v053_mutation_at"], "")
        self.assertEqual(migrated["first_v053_mutation_command"], "")

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "rollback")), 0)
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), original_state)
        self.assertEqual(task_path.read_bytes(), original_task)

    def test_historical_revision_three_state_projection_is_preserved(self):
        state = self.write_legacy_loop()
        state_task = self.revision_three_state_projection()
        for field in hloop.REVISION_THREE_TASK_POST_HISTORICAL_STATE_FIELDS:
            state_task.pop(field)
        task_path = hloop.task_file(self.repo, "T001")
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_record = self.revision_three_task()
        task_record["release_scope_revision"] = "1"
        task_record["remediation_round"] = "0"
        task_path.write_text(
            hloop.frontmatter(task_record)
            + "\n\n# Historical revision three task\n",
            encoding="utf-8",
        )
        state_task["task_contract_digest"] = hashlib.sha256(
            task_path.read_bytes()
        ).hexdigest()
        state["tasks"] = {"T001": deepcopy(state_task)}
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        original_state = hloop.state_path(self.repo).read_bytes()
        original_task = task_path.read_bytes()

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        migrated = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        self.assertEqual(migrated["tasks"]["T001"], state_task)
        self.assertEqual(task_path.read_bytes(), original_task)

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "rollback")), 0)
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), original_state)
        self.assertEqual(task_path.read_bytes(), original_task)

    def test_intermediate_revision_three_state_projection_is_preserved(self):
        state = self.write_legacy_loop()
        state_task = self.revision_three_state_projection()
        state_task.pop("created_from")
        state_task.pop("priority")
        task_path = hloop.task_file(self.repo, "T001")
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_record = self.revision_three_task()
        task_record["release_scope_revision"] = "1"
        task_record["remediation_round"] = "0"
        task_path.write_text(
            hloop.frontmatter(task_record)
            + "\n\n# Intermediate revision three task\n",
            encoding="utf-8",
        )
        state_task["task_contract_digest"] = hashlib.sha256(
            task_path.read_bytes()
        ).hexdigest()
        state["tasks"] = {"T001": deepcopy(state_task)}
        hloop.state_path(self.repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        original_state = hloop.state_path(self.repo).read_bytes()
        original_task = task_path.read_bytes()

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "apply")), 0)
        migrated = json.loads(hloop.state_path(self.repo).read_text(encoding="utf-8"))
        self.assertEqual(migrated["tasks"]["T001"], state_task)
        self.assertEqual(task_path.read_bytes(), original_task)

        self.assertEqual(hloop.cmd_migrate(migrate_args(self.repo, "rollback")), 0)
        self.assertEqual(hloop.state_path(self.repo).read_bytes(), original_state)
        self.assertEqual(task_path.read_bytes(), original_task)

    def test_state_projection_accepts_all_runtime_only_statuses(self):
        runtime_only = (
            hloop.hloop_task_contract.LEGACY_RUNTIME_TASK_STATUSES
            - hloop.hloop_task_contract.TASK_STATUSES
        )
        self.assertEqual(
            runtime_only,
            {
                "aborted",
                "blocked_merge_conflict",
                "blocked_environment",
                "blocked_head_mismatch",
                "blocked_base_mismatch",
                "blocked_write_scope",
            },
        )
        for status in sorted(runtime_only):
            with self.subTest(status=status):
                validation = hloop.hloop_task_contract.validate_task_state_projection(
                    self.revision_three_state_projection(status=status)
                )
                validation.raise_for_errors()

        invalid = self.revision_three_state_projection(status="not-canonical")
        with self.assertRaises(hloop.hloop_task_contract.ContractValidationError):
            hloop.hloop_task_contract.validate_task_state_projection(
                invalid
            ).raise_for_errors()

    def test_invalid_mismatched_and_future_revision_three_tasks_fail_before_write(self):
        for label, state_revision, artifact_revision, mutate_state in (
            ("invalid", 3, 3, lambda task: task.pop("preserved_invariants")),
            (
                "partial-historical-projection",
                3,
                3,
                lambda task: task.pop("created_from"),
            ),
            (
                "numeric-string-release-scope",
                3,
                3,
                lambda task: task.update(release_scope_revision="1"),
            ),
            (
                "numeric-string-remediation-round",
                3,
                3,
                lambda task: task.update(remediation_round="0"),
            ),
            ("mismatched", 3, 2, lambda task: None),
            ("future", 4, 4, lambda task: None),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state = self.legacy_state()
                state_task = self.revision_three_state_projection()
                state_task["contract_schema_revision"] = state_revision
                task = self.revision_three_task()
                task["contract_schema_revision"] = artifact_revision
                task_path = hloop.task_file(repo, "T001")
                task_path.parent.mkdir(parents=True, exist_ok=True)
                task_path.write_text(
                    hloop.frontmatter(task) + "\n\n# Task\n", encoding="utf-8"
                )
                state_task["task_contract_digest"] = hashlib.sha256(
                    task_path.read_bytes()
                ).hexdigest()
                mutate_state(state_task)
                state["tasks"] = {"T001": state_task}
                hloop.state_path(repo).write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                source_state = hloop.state_path(repo).read_bytes()
                source_task = task_path.read_bytes()

                with self.assertRaises(hloop.HLoopError):
                    hloop.cmd_migrate(migrate_args(repo, "apply"))
                self.assertEqual(hloop.state_path(repo).read_bytes(), source_state)
                self.assertEqual(task_path.read_bytes(), source_task)
                self.assertFalse(hloop.migration_runtime_root(repo).exists())

    def test_foreign_run_revision_three_task_artifact_fails_before_write(self):
        state = self.legacy_state()
        state_task = self.revision_three_state_projection()
        task = self.revision_three_task()
        task["run_id"] = "foreign-run"
        task_path = hloop.task_file(self.repo, "T001")
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            hloop.frontmatter(task) + "\n\n# Foreign task\n",
            encoding="utf-8",
        )
        state_task["task_contract_digest"] = hashlib.sha256(
            task_path.read_bytes()
        ).hexdigest()
        state["tasks"] = {"T001": state_task}

        self.assert_migration_rejected_without_writes(
            self.repo,
            state,
            (task_path,),
            r"task artifact run identity mismatch",
            check_dry_run=True,
        )

    def test_revision_three_task_digest_identity_fails_before_write(self):
        for label, recorded_digest, write_artifact in (
            ("invalid-digest", "sha256:" + "a" * 64, True),
            ("digest-mismatch", "b" * 64, True),
            ("missing-artifact", "a" * 64, False),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(
                    ["git", "init", "--initial-branch=main"],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                hloop.loop_path(repo).mkdir(parents=True)
                state = self.legacy_state()
                state_task = self.revision_three_state_projection()
                state_task["task_contract_digest"] = recorded_digest
                state["tasks"] = {"T001": state_task}
                hloop.state_path(repo).write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                task_path = hloop.task_file(repo, "T001")
                if write_artifact:
                    task_path.parent.mkdir(parents=True, exist_ok=True)
                    task_path.write_text(
                        hloop.frontmatter(self.revision_three_task())
                        + "\n\n# Task\n",
                        encoding="utf-8",
                    )
                source_state = hloop.state_path(repo).read_bytes()
                source_task = task_path.read_bytes() if task_path.is_file() else None

                with self.assertRaises(hloop.HLoopError):
                    hloop.cmd_migrate(migrate_args(repo, "apply"))
                self.assertEqual(hloop.state_path(repo).read_bytes(), source_state)
                self.assertEqual(
                    task_path.read_bytes() if task_path.is_file() else None,
                    source_task,
                )
                self.assertFalse(hloop.migration_runtime_root(repo).exists())

    def test_revision_three_task_complete_projection_drift_fails_before_write(self):
        task = self.revision_three_task()
        expected = hloop.revision_three_complete_state_projection(task)
        mismatches = {
            "created_from": "DECISIONS.md",
            "acceptance": ["different acceptance"],
            "priority": "P2",
            "validation_minimum": "python3 -m unittest tests.other",
            "depends_on": ["T002"],
            "write_allow": ["src/other.py"],
            "write_deny": ["secrets/**"],
            "worker_protocol": "codex-impl",
            "worker_qa_profile": "local",
            "agent_provider": "claude",
            "agent_model": "gpt-5.6-terra",
            "batch_id": "B002",
            "preserved_invariants": ["different invariant"],
            "regression_checks": ["different regression"],
            "risk_class": "normal",
            "required_gates": list(reversed(task["required_gates"])),
            "worker_agent_effort": "max",
            "investigation_goal": "different investigation",
            "implementation_ready_evidence": ["different evidence"],
            "exploration_budget_minutes": 16,
            "history_search_allowed": True,
            "task_origin": "finding",
            "release_scope_revision": 2,
            "plan_item_refs": ["P004"],
            "requirement_refs": ["REQ-005"],
            "scope_refs": ["approved-v0.5.3-plan"],
            "source_finding": "sha256:" + "b" * 64,
            "authorization_input_id": "U0001",
            "why_fix_now": "different rationale",
            "operational_reason": "different operation",
            "origin": "introduced",
            "contract_relation": "in_scope",
            "release_effect": "blocking",
            "remediation_round": 1,
            "fact_status": "confirmed",
            "disposition": "fix_now",
            "severity": "P2",
            "decision_requirement": "none",
            "scope_expanding": True,
        }
        self.assertEqual(
            set(mismatches),
            set(expected) - {"contract_schema_revision"},
        )

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            hloop.loop_path(repo).mkdir(parents=True)
            task_path = hloop.task_file(repo, "T001")
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(
                hloop.frontmatter(task) + "\n\n# Task\n", encoding="utf-8"
            )
            artifact_digest = hashlib.sha256(task_path.read_bytes()).hexdigest()

            for field, value in mismatches.items():
                for drift in ("mismatched", "missing"):
                    with self.subTest(field=field, drift=drift):
                        state = self.legacy_state()
                        state_task = self.revision_three_state_projection()
                        state_task["task_contract_digest"] = artifact_digest
                        if drift == "mismatched":
                            state_task[field] = deepcopy(value)
                        else:
                            state_task.pop(field)
                        state["tasks"] = {"T001": state_task}
                        hloop.state_path(repo).write_text(
                            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        source_state = hloop.state_path(repo).read_bytes()
                        source_task = task_path.read_bytes()

                        with self.assertRaisesRegex(
                            hloop.HLoopError,
                            rf"{field}",
                        ):
                            hloop.cmd_migrate(migrate_args(repo, "apply"))
                        self.assertEqual(
                            hloop.state_path(repo).read_bytes(), source_state
                        )
                        self.assertEqual(task_path.read_bytes(), source_task)
                        self.assertFalse(hloop.migration_runtime_root(repo).exists())


if __name__ == "__main__":
    unittest.main()
