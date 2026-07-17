"""Runtime integration tests for the schema-3.3 migration transaction."""

from __future__ import annotations

import argparse
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


if __name__ == "__main__":
    unittest.main()
