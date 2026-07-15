from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import importlib.machinery
import importlib.util
import sys


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_policy_cli_v052", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class PolicyCliV052Tests(unittest.TestCase):
    namespace = "policy-cli-v052"

    def setUp(self) -> None:
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace(self.namespace)

    def tearDown(self) -> None:
        hloop.configure_loop_namespace(self.previous_namespace)

    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        return repo

    def run_cli(self, repo: Path, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = hloop.main(["--repo", str(repo), "--namespace", self.namespace, *arguments])
        return result, output.getvalue()

    def init_and_lock(self, repo: Path) -> Path:
        result, output = self.run_cli(
            repo,
            "init",
            "--goal-id",
            "policy-cli-v052",
            "--goal",
            "policy CLI",
            "--base",
            "main",
            "--integration",
            "main",
            "--create-branch",
            "--specification-scout",
            "off",
        )
        self.assertEqual(result, 0, output)
        loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
        source_args = [
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/MISSION.md",
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/PLAN.md",
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/PROFILE.md",
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/DECISIONS.md",
        ]
        result, output = self.run_cli(
            repo,
            "release-scope",
            "lock",
            *source_args,
            "--plan-item-ref",
            "P004c",
            "--requirement-ref",
            "REQ-007",
            "--scope-ref",
            "release-contract",
        )
        self.assertEqual(result, 0, output)
        return loop

    def test_task_creation_and_update_use_immutable_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            result, output = self.run_cli(
                repo,
                "init",
                "--goal-id",
                "policy-cli-v052",
                "--goal",
                "policy CLI",
                "--base",
                "main",
                "--integration",
                "main",
                "--create-branch",
                "--specification-scout",
                "off",
            )
            self.assertEqual(result, 0, output)
            loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
            state_path = loop / "STATE.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["release_scope"] = {
                "status": "unlocked",
                "source_refs": [],
                "source_digests": {},
                "scope_revision": 0,
                "source_snapshot_revision": 0,
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "before lock",
                "--kind",
                "research",
                "--allow-no-write",
            )
            self.assertNotEqual(result, 0)
            loop = self.init_and_lock(repo)
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "planned task",
                "--id",
                "T010",
                "--kind",
                "research",
                "--allow-no-write",
                "--task-origin",
                "planned",
                "--plan-item-ref",
                "P004c",
            )
            self.assertEqual(result, 0, output)
            task_meta = hloop.read_frontmatter(loop / "tasks" / "T010.md")
            self.assertEqual(task_meta["task_origin"], "planned")
            self.assertEqual(task_meta["release_scope_revision"], "1")
            self.assertEqual(task_meta["plan_item_refs"], ["P004c"])

            result, output = self.run_cli(
                repo,
                "task",
                "update",
                "T010",
                "--add-acceptance",
                "still immutable",
            )
            self.assertEqual(result, 0, output)
            updated_meta = hloop.read_frontmatter(loop / "tasks" / "T010.md")
            self.assertEqual(updated_meta["task_origin"], "planned")
            self.assertEqual(updated_meta["plan_item_refs"], ["P004c"])

            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "scope expansion",
                "--kind",
                "research",
                "--allow-no-write",
                "--task-origin",
                "planned",
                "--plan-item-ref",
                "P004c",
                "--contract-relation",
                "outside_release",
            )
            self.assertNotEqual(result, 0)

    def test_scope_amendment_dispatch_freeze_and_follow_up_deduplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            loop = self.init_and_lock(repo)
            (loop / "MISSION.md").write_text("authorized scope amendment\n", encoding="utf-8")
            (loop / "SCOPE.md").write_text("new authorized scope\n", encoding="utf-8")
            source_args = [
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/MISSION.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PLAN.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PROFILE.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/DECISIONS.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/SCOPE.md",
            ]
            result, output = self.run_cli(
                repo,
                "release-scope",
                "amend",
                "--kind",
                "scope-change",
                "--reason",
                "authorized scope clarification",
                *source_args,
                "--basis-ref",
                "REQ-007",
                "--user-input-id",
                "U0002",
            )
            self.assertEqual(result, 0, output)
            result, output = self.run_cli(repo, "dispatch", "freeze", "--reason", "validation in progress")
            self.assertEqual(result, 0, output)
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "blocked task",
                "--kind",
                "research",
                "--allow-no-write",
                "--task-origin",
                "user-amendment",
                "--authorization-input-id",
                "U0002",
            )
            self.assertNotEqual(result, 0)
            result, output = self.run_cli(repo, "dispatch", "unfreeze", "--user-input-id", "U0003")
            self.assertEqual(result, 0, output)

            fingerprint = "sha256:" + "0" * 64
            follow_up_args = [
                "follow-up",
                "add",
                "--title",
                "Deferred integration concern",
                "--component",
                "integration",
                "--trigger-class",
                "review-follow-up",
                "--product-impact",
                "operator visibility",
                "--source-review-fingerprint",
                fingerprint,
                "--discovered-head",
                "HEAD",
                "--evidence",
                "review R001 evidence",
                "--impact",
                "No current release behavior is affected.",
                "--affected-path",
                "docs/follow-up.md",
                "--deferred-reason",
                "Outside the locked release contract.",
                "--reconsider-condition",
                "When the next release scope includes the integration surface.",
            ]
            result, output = self.run_cli(repo, *follow_up_args)
            self.assertEqual(result, 0, output)
            result, output = self.run_cli(repo, "follow-up", "list", "--json")
            self.assertEqual(result, 0, output)
            listed = json.loads(output)
            self.assertEqual(listed[0]["id"], "F001")
            result, output = self.run_cli(repo, "follow-up", "show", "F001", "--json")
            self.assertEqual(result, 0, output)
            shown = json.loads(output)
            self.assertEqual(shown["issue_key"], listed[0]["issue_key"])
            self.assertEqual(shown["root_cause"], "")
            result, output = self.run_cli(
                repo,
                *follow_up_args[:14],
                "--evidence",
                "second review evidence",
                "--impact",
                "Same semantic concern remains deferred.",
                "--affected-path",
                "docs/follow-up.md",
                "--deferred-reason",
                "Still outside the current release contract.",
                "--reconsider-condition",
                "At the next release-scope lock.",
            )
            self.assertEqual(result, 0, output)
            state = hloop.load_state(repo)
            self.assertEqual(len(state["follow_ups"]["issue_keys"]), 1)
            self.assertEqual(state["follow_ups"]["open_count"], 1)
            result, output = self.run_cli(repo, "follow-up", "export", "--output", "docs/follow-ups.md")
            self.assertEqual(result, 0, output)
            self.assertTrue((repo / "docs" / "follow-ups.md").is_file())
            result, output = self.run_cli(repo, "dashboard", "--json", "--no-pane-probe")
            self.assertEqual(result, 0, output)
            payload = json.loads(output)
            self.assertFalse(payload["loop"]["dispatch_frozen"])
            self.assertNotIn("hloop reviewer start", payload["next_actions"])


if __name__ == "__main__":
    unittest.main()
