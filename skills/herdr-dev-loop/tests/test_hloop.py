import argparse
import importlib.machinery
import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class ProviderCommandTests(unittest.TestCase):
    def test_codex_command_carries_effort_and_git_write_directory(self):
        command = hloop.agent_command_for_prompt(
            provider="codex",
            model="auto",
            runner="tui",
            sandbox="workspace-write",
            prompt_rel=Path("prompt.md"),
            effort="high",
            writable_dirs=[Path("/repo/.git")],
        )
        self.assertIn("model_reasoning_effort=high", command)
        self.assertIn("--add-dir /repo/.git", command)

    def test_claude_command_uses_explicit_permission_and_effort(self):
        command = hloop.agent_command_for_prompt(
            provider="claude",
            model="opus",
            runner="exec",
            sandbox="workspace-write",
            prompt_rel=Path("prompt.md"),
            effort="high",
            claude_permission_mode="auto",
        )
        self.assertIn("--permission-mode auto", command)
        self.assertIn("--effort high", command)
        self.assertIn("--model opus", command)


class LifecycleContractTests(unittest.TestCase):
    def test_terminal_marker_is_bound_to_current_attempt(self):
        text = "\n".join(
            [
                "HERDR_LOOP_ROLE_DONE:old:T001:T001-A001:done",
                "HERDR_LOOP_ROLE_DONE:run:T001:T001-A002:reported",
            ]
        )
        self.assertEqual(
            hloop.terminal_status_from_text(
                text, run_id="run", agent_id="T001", attempt_id="T001-A002"
            ),
            "reported",
        )
        self.assertEqual(
            hloop.terminal_status_from_text(
                text, run_id="run", agent_id="T001", attempt_id="T001-A001"
            ),
            "",
        )

    def test_worker_attempt_keeps_immutable_base_until_requeue(self):
        task_state = {}
        task_meta = {}
        attempt, base = hloop.ensure_worker_attempt(
            "T001", task_state, task_meta, "integration", "base-one"
        )
        second_attempt, second_base = hloop.ensure_worker_attempt(
            "T001", task_state, task_meta, "integration", "base-two"
        )
        self.assertEqual((attempt, base), ("T001-A001", "base-one"))
        self.assertEqual((second_attempt, second_base), (attempt, base))

    def test_migration_preserves_identity_and_splits_legacy_setup(self):
        state = {
            "run_id": "run-1",
            "skill_version": "0.3.0",
            "phase": "dispatching",
            "worktree_setup_commands": ["pnpm install"],
            "tasks": {},
            "reviews": {},
            "gaps": {},
            "manager_qa_profile": "none",
        }
        with tempfile.TemporaryDirectory() as directory:
            migrated = hloop.migrated_state(Path(directory), state)
        self.assertEqual(migrated["run_id"], "run-1")
        self.assertEqual(migrated["state_format_version"], 2)
        self.assertEqual(migrated["worker_setup_commands"], ["pnpm install"])
        self.assertEqual(migrated["reviewer_setup_commands"], [])

    def test_setup_guard_rejects_agent_configuration_mutation(self):
        self.assertEqual(hloop.unsafe_setup_command("rm -rf .claude"), ".claude")
        self.assertEqual(hloop.unsafe_setup_command("pnpm install --frozen-lockfile"), "")


class ValidationEnvironmentTests(unittest.TestCase):
    def test_validation_disables_bytecode_and_preserves_log_and_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "probe_module.py").write_text("VALUE = 7\n", encoding="utf-8")
            command = (
                f"{shlex.quote(sys.executable)} -c "
                "'import probe_module; print(probe_module.VALUE)'"
            )
            state = {
                "cycle": 3,
                "integration_branch": "main",
                "phase": "validating",
                "validation_commands": [command],
                "needs_gap_check": False,
                "needs_review": False,
            }
            args = argparse.Namespace(
                repo=str(repo),
                dry_run=False,
                validation_command=None,
                no_cleanup=True,
            )
            with (
                mock.patch.object(hloop, "repo_root", return_value=repo),
                mock.patch.object(hloop, "preflight_loop", return_value=state),
                mock.patch.object(hloop, "git", return_value="abc123"),
                mock.patch.object(hloop, "save_state"),
                mock.patch.object(hloop, "journal"),
                mock.patch.object(hloop, "_changed_file_inventory", return_value=[]),
                mock.patch.object(hloop, "should_open_gap_gate", return_value=False),
                mock.patch.object(hloop, "should_open_review_gate", return_value=False),
            ):
                self.assertEqual(hloop.cmd_validate(args), 0)

            self.assertFalse((repo / "__pycache__").exists())
            result = state["last_validation"]["results"][0]
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["result"], "passed")
            log = (repo / result["log"]).read_text(encoding="utf-8")
            self.assertIn("returncode: 0", log)
            self.assertTrue(log.endswith("7\n"))


class PreflightEnvironmentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-recovery")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path, phase: str = "validating") -> dict:
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": phase,
            "integration_branch": "main",
        }
        hloop.save_state(root, state)
        return state

    def test_corrected_preflight_restores_previous_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.init_repo(repo)
            blocker = repo / "untracked.tmp"
            blocker.write_text("dirty\n", encoding="utf-8")

            with self.assertRaisesRegex(hloop.HLoopError, "non-loop dirty files"):
                hloop.preflight_loop(repo, require_integration_branch=True)

            blocked = json.loads(hloop.state_path(repo).read_text(encoding="utf-8"))
            self.assertEqual(blocked["phase"], "blocked_environment")
            self.assertEqual(blocked["last_preflight_error"]["source"], "preflight")
            self.assertEqual(blocked["last_preflight_error"]["previous_phase"], "validating")
            self.assertIsNone(
                hloop.pump_stop_reason(
                    blocked,
                    argparse.Namespace(stop_on_triage=True, stop_on_waiting=False),
                )
            )

            blocker.unlink()
            recovered = hloop.preflight_loop(repo, require_integration_branch=True)
            self.assertEqual(recovered["phase"], "validating")
            self.assertIn("resolved_at", recovered["last_preflight_error"])
            self.assertEqual(
                recovered["last_preflight_error"]["resolved_phase"], "validating"
            )

    def test_preflight_recovery_does_not_clear_an_existing_environment_block(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            state = self.init_repo(repo, phase="blocked_environment")
            state["last_preflight_error"] = {
                "at": hloop.now_iso(),
                "reason": "temporary dirty state",
                "source": "preflight",
                "previous_phase": "blocked_environment",
            }
            hloop.save_state(repo, state)

            recovered = hloop.preflight_loop(repo, require_integration_branch=True)
            self.assertEqual(recovered["phase"], "blocked_environment")
            stop = hloop.pump_stop_reason(
                recovered,
                argparse.Namespace(stop_on_triage=True, stop_on_waiting=False),
            )
            self.assertEqual(stop, ("phase is blocked_environment", 2))


if __name__ == "__main__":
    unittest.main()
