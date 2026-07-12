import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
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


if __name__ == "__main__":
    unittest.main()
