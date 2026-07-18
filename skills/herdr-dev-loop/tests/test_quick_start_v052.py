from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).parents[1]
SCRIPT = SKILL_DIR / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_quick_start_v052", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class QuickStartV052Tests(unittest.TestCase):
    namespace = "quick-start-v052"

    def setUp(self) -> None:
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace(self.namespace)

    def tearDown(self) -> None:
        hloop.configure_loop_namespace(self.previous_namespace)

    def run_cli(self, repo: Path, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = hloop.main(
                ["--repo", str(repo), "--namespace", self.namespace, *arguments]
            )
        return result, output.getvalue()

    def quick_start_block(self, path: Path, heading: str) -> str:
        text = path.read_text(encoding="utf-8")
        heading_start = text.index(heading)
        block_start = text.index("```bash", heading_start) + len("```bash\n")
        block_end = text.index("```", block_start)
        return text[block_start:block_end]

    def test_public_quick_starts_pin_requirement_only_order(self) -> None:
        documents = (
            (SKILL_DIR / "SKILL.md", "## Quick Start"),
            (SKILL_DIR / "README.md", "### 3. Quick Start"),
        )
        commands = (
            "input record",
            "requirement new",
            "release-scope lock",
            "batch start",
            "task new",
        )
        for path, heading in documents:
            with self.subTest(path=path):
                block = self.quick_start_block(path, heading)
                positions = [block.index(command) for command in commands]
                self.assertEqual(positions, sorted(positions))
                self.assertIn("--requirement-ref REQ-001", block)
                self.assertNotIn("--plan-item-ref P001", block)

    def test_requirement_only_quick_start_is_cli_authorizable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            (repo / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "fixture"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            for arguments in (
                (
                    "init",
                    "--goal-id",
                    self.namespace,
                    "--goal",
                    "requirement-only quick start",
                    "--base",
                    "main",
                    "--integration",
                    "main",
                    "--create-branch",
                    "--specification-scout",
                    "off",
                ),
                (
                    "input",
                    "record",
                    "--source",
                    "manager-chat",
                    "--text",
                    "authorize a requirement-only implementation",
                ),
                (
                    "requirement",
                    "new",
                    "--source-input",
                    "U0001",
                    "--acceptance",
                    "the requirement-only task is authorized",
                    "--priority",
                    "P1",
                ),
                (
                    "release-scope",
                    "lock",
                    "--source",
                    "MISSION.md",
                    "--source",
                    "PLAN.md",
                    "--requirement-ref",
                    "REQ-001",
                    "--scope-ref",
                    "release-scope-contract",
                ),
                ("batch", "start", "Initial implementation batch"),
            ):
                result, output = self.run_cli(repo, *arguments)
                self.assertEqual(result, 0, output)

            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "Requirement-only task",
                "--kind",
                "implementation",
                "--task-origin",
                "planned",
                "--requirement-ref",
                "REQ-001",
                "--write-allow",
                "reports/**",
                "--preserved-invariant",
                "preserve requirement-only authorization",
                "--regression-check",
                "run the requirement-only quick-start regression",
                "--risk-class",
                "normal",
                "--required-gate",
                "patch_review",
                "--required-gate",
                "full_suite",
            )
            self.assertEqual(result, 0, output)
            loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
            task = hloop.read_frontmatter(loop / "tasks" / "T001.md")
            self.assertEqual(task["task_origin"], "planned")
            self.assertEqual(task["plan_item_refs"], [])
            self.assertEqual(task["requirement_refs"], ["REQ-001"])


if __name__ == "__main__":
    unittest.main()
