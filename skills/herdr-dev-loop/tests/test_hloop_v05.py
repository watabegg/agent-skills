import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_v05_recovery", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class CompletedCherryPickRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def make_repo(self, root: Path, *, commit_count: int):
        namespace = f"cherry-pick-crash-{commit_count}"
        hloop.configure_loop_namespace(namespace)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        worker_base = hloop.git(repo, ["rev-parse", "HEAD"])

        subprocess.run(["git", "switch", "-c", "worker"], cwd=repo, check=True, capture_output=True)
        changed_paths = []
        for index in range(commit_count):
            path = f"feature-{index}.txt"
            changed_paths.append(path)
            (repo / path).write_text(f"feature {index}\n", encoding="utf-8")
            subprocess.run(["git", "add", path], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"feature {index}"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
        worker_head = hloop.git(repo, ["rev-parse", "HEAD"])
        source_commits = tuple(
            hloop.git(repo, ["rev-list", "--reverse", f"{worker_base}..{worker_head}"]).splitlines()
        )

        subprocess.run(["git", "switch", "main"], cwd=repo, check=True, capture_output=True)
        (repo / "manager.txt").write_text("manager\n", encoding="utf-8")
        subprocess.run(["git", "add", "manager.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "manager"], cwd=repo, check=True, capture_output=True)
        pre_head = hloop.git(repo, ["rev-parse", "HEAD"])

        transaction = hloop.build_merge_transaction(
            task_id="T001",
            attempt_id="T001-A001",
            branch="worker",
            pre_merge_head=pre_head,
            worker_head=worker_head,
            index_state=hloop.merge_transaction_index_state(repo, "HEAD"),
            changed_paths=changed_paths,
            mode="cherry-pick",
            source_commits=source_commits,
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": namespace,
            "run_id": f"run-{namespace}",
            "skill_version": hloop.SKILL_VERSION,
            "phase": "running",
            "integration_branch": "main",
            "merge_mode": "cherry-pick",
            "manager_qa_profile": "none",
            "tasks": {
                "T001": {
                    "status": "result_reported",
                    "branch": "worker",
                    "write_allow": changed_paths,
                    "write_deny": [],
                }
            },
            "active_merge": {
                **transaction.to_record(),
                "worker_base_sha": worker_base,
            },
        }
        hloop.save_state(repo, state)
        return repo, state, source_commits

    def merge_args(self, repo: Path):
        return argparse.Namespace(
            repo=str(repo),
            task_id="T001",
            abort=False,
            continue_merge=False,
            retry=False,
            mode=None,
            dry_run=False,
        )

    def test_single_and_multi_commit_crash_after_git_completion_reconcile_once(self):
        for commit_count in (1, 3):
            with self.subTest(commit_count=commit_count), tempfile.TemporaryDirectory() as directory:
                repo, state, source_commits = self.make_repo(
                    Path(directory), commit_count=commit_count
                )
                subprocess.run(
                    ["git", "cherry-pick", *source_commits],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                landed_head = hloop.git(repo, ["rev-parse", "HEAD"])
                landed_count = hloop.git(repo, ["rev-list", "--count", "HEAD"])
                self.assertFalse((hloop.merge_git_dir(repo) / "sequencer").exists())

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(hloop.cmd_merge(self.merge_args(repo)), 0)

                self.assertIn("reconciled merge T001", output.getvalue())
                self.assertEqual(state["tasks"]["T001"]["status"], "merged")
                self.assertNotIn("active_merge", state)
                self.assertEqual(hloop.git(repo, ["rev-parse", "HEAD"]), landed_head)
                self.assertEqual(hloop.git(repo, ["rev-list", "--count", "HEAD"]), landed_count)

    def test_completed_recovery_rejects_extra_first_parent_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, source_commits = self.make_repo(Path(directory), commit_count=1)
            subprocess.run(
                ["git", "cherry-pick", *source_commits],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            subprocess.run(["git", "add", "foreign.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "foreign"], cwd=repo, check=True, capture_output=True)

            with self.assertRaisesRegex(hloop.HLoopError, "first-parent suffix length"):
                hloop.reconcile_completed_cherry_pick(repo, state, "T001")
            self.assertIn("active_merge", state)

    def test_completed_recovery_rejects_whitespace_only_tree_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, source_commits = self.make_repo(Path(directory), commit_count=1)
            subprocess.run(
                ["git", "cherry-pick", *source_commits],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "feature-0.txt").write_text("feature  0\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature-0.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "--amend", "--no-edit"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            with self.assertRaisesRegex(hloop.HLoopError, "landed tree"):
                hloop.reconcile_completed_cherry_pick(repo, state, "T001")
            self.assertIn("active_merge", state)


if __name__ == "__main__":
    unittest.main()
