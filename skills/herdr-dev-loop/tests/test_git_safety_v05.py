import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_v05_git_safety", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class ExactGitPathTests(unittest.TestCase):
    SPECIAL_PATHS = (
        "日本語.txt",
        "space name.txt",
        'quote"name.txt',
        "back\\slash.txt",
        "tab\tname.txt",
        "line\nname.txt",
    )
    RENAME_SOURCE = "rename old.txt"
    RENAME_DESTINATION = 'rename 日本語\n\t\\" new.txt'

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        return repo

    def seed_special_paths(self, repo: Path) -> str:
        for name in (*self.SPECIAL_PATHS, self.RENAME_SOURCE):
            (repo / name).write_text(f"base {name!r}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
        return hloop.git(repo, ["rev-parse", "HEAD"])

    def test_status_diff_and_scope_preserve_every_exact_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            base_sha = self.seed_special_paths(repo)
            for name in self.SPECIAL_PATHS:
                (repo / name).write_text(f"changed {name!r}\n", encoding="utf-8")
            (repo / self.RENAME_SOURCE).rename(repo / self.RENAME_DESTINATION)
            expected = set(
                (*self.SPECIAL_PATHS, self.RENAME_SOURCE, self.RENAME_DESTINATION)
            )

            self.assertEqual(set(hloop.porcelain_paths_no_renames(repo)), expected)
            self.assertEqual(
                set(hloop.unsafe_dirty_paths(repo, allow_loop_dirty=False)), expected
            )
            self.assertIn("back\\slash.txt", hloop.porcelain_paths(repo))
            self.assertNotIn("back/slash.txt", hloop.porcelain_paths(repo))

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            self.assertEqual(set(hloop.cached_diff_paths_no_renames(repo)), expected)
            self.assertEqual(set(hloop.staged_paths(repo)), expected)
            subprocess.run(["git", "commit", "-m", "special paths"], cwd=repo, check=True, capture_output=True)
            head_sha = hloop.git(repo, ["rev-parse", "HEAD"])

            self.assertEqual(set(hloop.changed_files(repo, base_sha, head_sha)), expected)
            self.assertEqual(set(hloop.head_changed_paths(repo)), expected)
            self.assertEqual(
                hloop.validate_change_scope(
                    "T001",
                    {"write_allow": list(expected), "write_deny": []},
                    hloop.changed_files(repo, base_sha, head_sha),
                ),
                [],
            )

    def test_changed_file_inventory_is_nul_safe_and_utf8_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            base_sha = self.seed_special_paths(repo)
            for name in self.SPECIAL_PATHS:
                (repo / name).write_text(f"changed {name!r}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "changed special paths"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            head_sha = hloop.git(repo, ["rev-parse", "HEAD"])

            self.assertEqual(
                set(hloop._changed_file_inventory(repo, base_sha, head_sha)),
                set(self.SPECIAL_PATHS),
            )
            with mock.patch.object(
                hloop,
                "git_bytes",
                return_value=b"valid.txt\0invalid-\xff.txt\0",
            ):
                with self.assertRaisesRegex(hloop.HLoopError, "non-UTF-8"):
                    hloop._changed_file_inventory(repo, base_sha, head_sha)

    def test_repository_visible_inventory_is_nul_safe_and_utf8_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            self.seed_special_paths(repo)
            expected = set((*self.SPECIAL_PATHS, self.RENAME_SOURCE))
            self.assertEqual(set(hloop._repository_visible_paths(repo)), expected)

            with mock.patch.object(
                hloop,
                "git_bytes",
                return_value=b"valid.txt\0invalid-\xff.txt\0",
            ):
                with self.assertRaisesRegex(hloop.HLoopError, "non-UTF-8"):
                    hloop._repository_visible_paths(repo)

            with self.assertRaisesRegex(hloop.HLoopError, "non-UTF-8"):
                hloop.git_path_from_bytes(b"invalid-\xff.txt")

    def test_finalize_handoff_and_seal_keep_special_names_exact(self):
        namespace = "special-path-seal"
        hloop.configure_loop_namespace(namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            (repo / self.RENAME_SOURCE).write_text("rename payload\n", encoding="utf-8")
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
            base_sha = hloop.git(repo, ["rev-parse", "HEAD"])
            (repo / ".git" / "info" / "exclude").write_text(
                f".ai/herdr-dev-loop/loops/{namespace}/STATE.json\n"
                f".ai/herdr-dev-loop/loops/{namespace}/tasks/\n",
                encoding="utf-8",
            )
            worktree = root / "worker"
            subprocess.run(
                ["git", "worktree", "add", "-b", "worker-special-paths", str(worktree), "main"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            allowed_paths = list((*self.SPECIAL_PATHS, self.RENAME_SOURCE, self.RENAME_DESTINATION))
            run_id = "run-special-path-seal"
            task_meta = {
                "id": "T001",
                "run_id": run_id,
                "skill_version": hloop.SKILL_VERSION,
                "kind": "fix",
                "status": "running",
                "branch": "worker-special-paths",
                "base_ref": "main",
                "base_sha": base_sha,
                "write_allow": allowed_paths,
                "write_deny": [],
                "acceptance": ["special paths seal"],
            }
            task_text = hloop.frontmatter(task_meta) + "\n\n# Task T001\n"
            hloop.write_text(hloop.task_file(repo, "T001"), task_text)
            hloop.write_text(hloop.task_file(worktree, "T001"), task_text)
            task_state = {
                **task_meta,
                "worktree": str(worktree),
                "attempt_id": "T001-A001",
                "active_attempt_id": "T001-A001",
                "worker_base_sha": base_sha,
                "semantic_ack_barrier": {"status": "approved"},
            }
            manager_state = {
                "state_format_version": hloop.STATE_FORMAT_VERSION,
                "schema_revision": hloop.STATE_SCHEMA_REVISION,
                "namespace": namespace,
                "run_id": run_id,
                "skill_version": hloop.SKILL_VERSION,
                "phase": "running",
                "integration_branch": "main",
                "merge_mode": "squash",
                "persistence": "local-only",
                "tasks": {"T001": task_state},
            }
            hloop.save_state(repo, manager_state)
            hloop.save_state(worktree, json.loads(json.dumps(manager_state)))

            for name in self.SPECIAL_PATHS:
                (worktree / name).write_text(f"payload {name!r}\n", encoding="utf-8")
            (worktree / self.RENAME_SOURCE).rename(worktree / self.RENAME_DESTINATION)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    hloop.cmd_worker_finalize(
                        argparse.Namespace(
                            repo=str(worktree),
                            task_id="T001",
                            status="partial",
                            validation_command=[],
                            validation_result=[],
                            validation_summary=None,
                            blocking_question=[],
                            no_commit=False,
                            handoff=True,
                        )
                    ),
                    0,
                )
            result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
            result_meta = hloop.read_frontmatter(worktree / result_rel)
            expected_scope = set((*allowed_paths, result_rel.as_posix()))
            self.assertEqual(set(result_meta["changed_files"]), expected_scope)

            seal_state = json.loads(json.dumps(manager_state))
            seal_state["tasks"]["T001"]["pane_closed_at"] = "2026-01-01T00:00:00+00:00"
            with mock.patch.object(hloop, "preflight_loop", return_value=seal_state):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        hloop.cmd_worker_seal(
                            argparse.Namespace(
                                repo=str(repo),
                                task_id="T001",
                                attempt_id=None,
                                validation_command=None,
                                validation_summary=None,
                            )
                        ),
                        0,
                    )

            self.assertEqual(hloop.porcelain_paths(worktree), [])
            sealed_meta = hloop.read_frontmatter(worktree / result_rel)
            self.assertEqual(set(sealed_meta["changed_files"]), expected_scope)
            self.assertFalse((worktree / self.RENAME_SOURCE).exists())
            self.assertEqual(
                (worktree / self.RENAME_DESTINATION).read_text(encoding="utf-8"),
                "rename payload\n",
            )

    def test_read_only_role_scope_rejects_staged_rename_from_product_to_artifact(self):
        namespace = "role-rename-scope"
        hloop.configure_loop_namespace(namespace)
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            source = "product-secret.txt"
            (repo / source).write_text("secret\n", encoding="utf-8")
            subprocess.run(["git", "add", source], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "seed product"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            cases = (
                (
                    "reviewer",
                    hloop.LOOP_DIR / "reviews" / "R001.md",
                    lambda: hloop.validate_reviewer_worktree_scope(
                        "R001", {"baseline_dirty_files": []}, repo
                    ),
                ),
                (
                    "gap",
                    hloop.LOOP_DIR / "gaps" / "G001.md",
                    lambda: hloop.validate_gap_worktree_scope(
                        "G001", {"baseline_dirty_files": []}, repo
                    ),
                ),
                (
                    "advisor",
                    hloop.LOOP_DIR / "advice" / "A001-P1.md",
                    lambda: hloop.validate_advisor_worktree_scope(
                        "A001",
                        {"participant_id": "P1", "baseline_dirty_files": []},
                        repo,
                    ),
                ),
                (
                    "specification-scout",
                    hloop.LOOP_DIR / "decisions" / "SCOUT.md",
                    lambda: hloop.validate_decision_role_scope(
                        repo,
                        {"baseline_dirty_files": []},
                        allowed={
                            (hloop.LOOP_DIR / "decisions" / "SCOUT.md").as_posix()
                        },
                    ),
                ),
                (
                    "decision-liaison",
                    hloop.LOOP_DIR / "decisions" / "D001-QUESTION.md",
                    lambda: hloop.validate_decision_role_scope(
                        repo,
                        {"baseline_dirty_files": []},
                        allowed={
                            (
                                hloop.LOOP_DIR / "decisions" / "D001-QUESTION.md"
                            ).as_posix()
                        },
                    ),
                ),
            )
            for role, destination, validate in cases:
                with self.subTest(role=role):
                    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo, check=True)
                    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
                    (repo / source).rename(repo / destination)
                    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

                    self.assertEqual(
                        set(hloop.role_scope_paths(repo)),
                        {source, destination.as_posix()},
                    )
                    self.assertEqual(validate(), [source])


if __name__ == "__main__":
    unittest.main()
