import argparse
import copy
import contextlib
import importlib.machinery
import importlib.util
import io
import os
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

    def make_conflicted_repo(self, root: Path, *, commit_count: int):
        namespace = f"cherry-pick-conflict-{commit_count}"
        hloop.configure_loop_namespace(namespace)
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
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True
        )
        worker_base = hloop.git(repo, ["rev-parse", "HEAD"])

        subprocess.run(
            ["git", "switch", "-c", "worker"], cwd=repo, check=True, capture_output=True
        )
        (repo / "README.md").write_text("worker\n", encoding="utf-8")
        subprocess.run(
            ["git", "commit", "-am", "conflict"], cwd=repo, check=True, capture_output=True
        )
        changed_paths = ["README.md"]
        for index in range(1, commit_count):
            path = f"tail-{index}.txt"
            changed_paths.append(path)
            (repo / path).write_text(f"tail {index}\n", encoding="utf-8")
            subprocess.run(["git", "add", path], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"tail {index}"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
        worker_head = hloop.git(repo, ["rev-parse", "HEAD"])
        source_commits = tuple(
            hloop.git(repo, ["rev-list", "--reverse", f"{worker_base}..{worker_head}"]).splitlines()
        )

        subprocess.run(["git", "switch", "main"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("manager\n", encoding="utf-8")
        subprocess.run(
            ["git", "commit", "-am", "manager"], cwd=repo, check=True, capture_output=True
        )
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
            "phase": "blocked_conflict",
            "integration_branch": "main",
            "merge_mode": "cherry-pick",
            "manager_qa_profile": "none",
            "tasks": {
                "T001": {
                    "status": "blocked_merge_conflict",
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
        cherry_pick = subprocess.run(
            ["git", "cherry-pick", *source_commits],
            cwd=repo,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(cherry_pick.returncode, 0)
        observed = hloop.observed_merge_transaction(repo, transaction)
        hloop.transition_merge_transaction(
            repo,
            state,
            transaction,
            hloop.MERGE_CONTENT_CONFLICT,
            observed=observed,
        )
        hloop.save_state(repo, state)
        return repo, state, source_commits

    def resolve_conflict(self, repo: Path):
        (repo / "README.md").write_text("resolved\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)

    def continue_then_crash_before_state_completion(self, repo: Path, state: dict):
        self.resolve_conflict(repo)
        with mock.patch.object(
            hloop,
            "complete_merge_state",
            side_effect=RuntimeError("simulated crash before STATE completion"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                hloop.cmd_merge_continue(repo, state, "T001")
        return hloop.load_state(repo)

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

    def test_conflict_resolution_crash_reconciles_exact_single_and_multi_commit(self):
        for commit_count in (1, 3):
            with self.subTest(commit_count=commit_count), tempfile.TemporaryDirectory() as directory:
                repo, state, source_commits = self.make_conflicted_repo(
                    Path(directory), commit_count=commit_count
                )
                recovered = self.continue_then_crash_before_state_completion(repo, state)
                landed_head = hloop.git(repo, ["rev-parse", "HEAD"])
                evidence = recovered["active_merge"]["cherry_pick_evidence"]
                self.assertEqual(len(evidence), 1)
                self.assertEqual(evidence[0]["kind"], "resolution")
                self.assertEqual(evidence[0]["sequence_start"], 0)
                self.assertEqual(tuple(evidence[0]["source_commits"]), source_commits)
                self.assertEqual(evidence[0]["expected_parent"], recovered["active_merge"]["pre_merge_head"])
                self.assertEqual(evidence[0]["resolved_tree"], evidence[0]["expected_trees"][0])
                self.assertEqual(evidence[0]["expected_landed_commits"][-1], landed_head)

                self.assertTrue(hloop.reconcile_completed_cherry_pick(repo, recovered, "T001"))
                self.assertEqual(recovered["tasks"]["T001"]["status"], "merged")
                self.assertNotIn("active_merge", recovered)
                self.assertEqual(hloop.git(repo, ["rev-parse", "HEAD"]), landed_head)

    def test_conflict_resolution_evidence_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, source_commits = self.make_conflicted_repo(
                Path(directory), commit_count=3
            )
            self.resolve_conflict(repo)
            real_git = hloop.git

            def crash_before_continue(repo_arg, args, *positional, **keywords):
                if args[-2:] == ["cherry-pick", "--continue"]:
                    raise RuntimeError("stop after durable evidence")
                return real_git(repo_arg, args, *positional, **keywords)

            with mock.patch.object(hloop, "git", side_effect=crash_before_continue):
                with self.assertRaisesRegex(RuntimeError, "durable evidence"):
                    hloop.cmd_merge_continue(repo, state, "T001")
            active = hloop.load_state(repo)["active_merge"]
            original = active["cherry_pick_evidence"][0]

            def changed(field):
                record = copy.deepcopy(original)
                field(record)
                record["evidence_digest"] = hloop.cherry_pick_evidence_digest(record)
                candidate = copy.deepcopy(active)
                candidate["cherry_pick_evidence"] = [record]
                return candidate

            mutations = (
                ("tree", lambda record: record.__setitem__("resolved_tree", record["expected_parent"])),
                ("source", lambda record: record["source_commits"].__setitem__(0, source_commits[-1])),
                ("parent", lambda record: record.__setitem__("expected_parent", source_commits[0])),
                ("sequence", lambda record: record.__setitem__("sequence_start", 1)),
            )
            for field, mutation in mutations:
                with self.subTest(field=field):
                    with self.assertRaisesRegex(hloop.HLoopError, "evidence"):
                        hloop.preflight_merge_transaction(repo, changed(mutation), "continue")

    def test_unrecorded_manual_resolution_and_committer_only_amend_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, _ = self.make_conflicted_repo(Path(directory), commit_count=1)
            self.resolve_conflict(repo)
            subprocess.run(
                ["git", "cherry-pick", "--continue"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            with self.assertRaisesRegex(hloop.HLoopError, "does not replay cleanly"):
                hloop.reconcile_completed_cherry_pick(repo, state, "T001")

        with tempfile.TemporaryDirectory() as directory:
            repo, state, _ = self.make_conflicted_repo(Path(directory), commit_count=1)
            recovered = self.continue_then_crash_before_state_completion(repo, state)
            expected_head = hloop.git(repo, ["rev-parse", "HEAD"])
            missing_evidence = copy.deepcopy(recovered)
            missing_evidence["active_merge"]["cherry_pick_evidence"] = []
            with self.assertRaisesRegex(hloop.HLoopError, "missing an applied sequence"):
                hloop.reconcile_completed_cherry_pick(repo, missing_evidence, "T001")
            amend_env = dict(os.environ)
            amend_env["GIT_COMMITTER_DATE"] = "2000-01-01T00:00:00 +0000"
            subprocess.run(
                ["git", "commit", "--amend", "--no-edit"],
                cwd=repo,
                check=True,
                capture_output=True,
                env=amend_env,
            )
            self.assertNotEqual(hloop.git(repo, ["rev-parse", "HEAD"]), expected_head)
            with self.assertRaisesRegex(hloop.HLoopError, "landed commit identity"):
                hloop.reconcile_completed_cherry_pick(repo, recovered, "T001")

    def test_conflict_resolution_recovery_requires_clean_index_and_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, _ = self.make_conflicted_repo(Path(directory), commit_count=1)
            recovered = self.continue_then_crash_before_state_completion(repo, state)
            (repo / "README.md").write_text("staged drift\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            with self.assertRaisesRegex(hloop.HLoopError, "index is not clean"):
                hloop.reconcile_completed_cherry_pick(repo, recovered, "T001")

        with tempfile.TemporaryDirectory() as directory:
            repo, state, _ = self.make_conflicted_repo(Path(directory), commit_count=1)
            recovered = self.continue_then_crash_before_state_completion(repo, state)
            (repo / "README.md").write_text("worktree drift\n", encoding="utf-8")
            with self.assertRaisesRegex(hloop.HLoopError, "product worktree is dirty"):
                hloop.reconcile_completed_cherry_pick(repo, recovered, "T001")


if __name__ == "__main__":
    unittest.main()
