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

    def make_repo(
        self,
        root: Path,
        *,
        commit_count: int,
        object_format: str = "sha1",
        commit_encoding: str | None = None,
    ):
        namespace = f"cherry-pick-crash-{commit_count}"
        hloop.configure_loop_namespace(namespace)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            [
                "git",
                "init",
                f"--object-format={object_format}",
                "--initial-branch=main",
            ],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        if commit_encoding:
            subprocess.run(
                ["git", "config", "i18n.commitEncoding", commit_encoding],
                cwd=repo,
                check=True,
            )
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
            if commit_encoding:
                message_path = root / f"message-{index}.txt"
                message_path.write_bytes(f"caf\N{LATIN SMALL LETTER E WITH ACUTE} {index}\n".encode("latin-1"))
                commit_args = ["git", "commit", "-F", str(message_path)]
            else:
                commit_args = ["git", "commit", "-m", f"feature {index}"]
            subprocess.run(commit_args, cwd=repo, check=True, capture_output=True)
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
            "active_merge": hloop.build_active_merge_record(
                transaction,
                worker_base_sha=worker_base,
            ),
        }
        hloop.save_state(repo, state)
        return repo, state, source_commits

    def install_failing_prepare_commit_msg_hook(self, repo: Path):
        hook = Path(hloop.git(repo, ["rev-parse", "--git-path", "hooks/prepare-commit-msg"]))
        if not hook.is_absolute():
            hook = repo / hook
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        hook.chmod(0o755)

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

    def run_recorded_cherry_pick(
        self,
        repo: Path,
        state: dict,
        source_commits: tuple[str, ...],
        *,
        check: bool,
    ):
        transaction = hloop.MergeTransaction.from_record(state["active_merge"])
        _, env = hloop.prepare_cherry_pick_evidence(
            repo,
            state,
            transaction,
            resolved_tree=None,
        )
        return subprocess.run(
            ["git", *hloop.CHERRY_PICK_GIT_CONFIG, "cherry-pick", *source_commits],
            cwd=repo,
            check=check,
            capture_output=True,
            env=env,
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
            "active_merge": hloop.build_active_merge_record(
                transaction,
                worker_base_sha=worker_base,
            ),
        }
        hloop.save_state(repo, state)
        cherry_pick = self.run_recorded_cherry_pick(
            repo,
            state,
            source_commits,
            check=False,
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

    def test_new_transaction_evidence_policy_cannot_be_downgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, source_commits = self.make_repo(Path(directory), commit_count=1)
            active = state["active_merge"]
            self.assertEqual(
                active["cherry_pick_transaction_version"],
                hloop.CHERRY_PICK_TRANSACTION_VERSION,
            )
            self.assertEqual(
                active["cherry_pick_evidence_policy"],
                hloop.CHERRY_PICK_EVIDENCE_POLICY,
            )
            self.assertEqual(
                active["cherry_pick_evidence_version"],
                hloop.CHERRY_PICK_EVIDENCE_VERSION,
            )
            self.assertEqual(active["cherry_pick_evidence_legacy_prefix_count"], 0)
            self.assertEqual(active["cherry_pick_evidence"], [])

            transaction = hloop.MergeTransaction.from_record(active)
            initial_head = hloop.git(repo, ["rev-parse", "HEAD"])
            initial_mutations = {
                "missing-policy": lambda candidate: candidate.pop(
                    "cherry_pick_evidence_policy"
                ),
                "rewritten-policy": lambda candidate: candidate.update(
                    {
                        "cherry_pick_evidence_policy": (
                            hloop.CHERRY_PICK_EVIDENCE_LEGACY_POLICY
                        ),
                        "cherry_pick_evidence_legacy_migration_digest": (
                            hloop.legacy_cherry_pick_migration_digest(transaction, 0)
                        ),
                    }
                ),
                "missing-transaction-version": lambda candidate: candidate.pop(
                    "cherry_pick_transaction_version"
                ),
                "missing-version": lambda candidate: candidate.pop(
                    "cherry_pick_evidence_version"
                ),
                "missing-evidence": lambda candidate: candidate.pop(
                    "cherry_pick_evidence"
                ),
            }
            for name, mutation in initial_mutations.items():
                with self.subTest(name=name):
                    candidate = copy.deepcopy(active)
                    mutation(candidate)
                    with self.assertRaisesRegex(
                        hloop.HLoopError, "evidence|transaction"
                    ):
                        hloop.validate_cherry_pick_evidence(
                            repo,
                            candidate,
                            transaction,
                            applied_head=initial_head,
                            applied_count=0,
                        )

            self.run_recorded_cherry_pick(
                repo,
                state,
                source_commits,
                check=True,
            )
            landed_head = hloop.git(repo, ["rev-parse", "HEAD"])
            applied_transaction = hloop.MergeTransaction.from_record(
                state["active_merge"]
            )
            applied_mutations = {
                "legacy-prefix": lambda candidate: candidate.__setitem__(
                    "cherry_pick_evidence_legacy_prefix_count", 1
                ),
                "deleted-records": lambda candidate: candidate.__setitem__(
                    "cherry_pick_evidence", []
                ),
            }
            for name, mutation in applied_mutations.items():
                with self.subTest(name=name):
                    candidate = copy.deepcopy(state["active_merge"])
                    mutation(candidate)
                    with self.assertRaisesRegex(hloop.HLoopError, "evidence|legacy"):
                        hloop.validate_cherry_pick_evidence(
                            repo,
                            candidate,
                            applied_transaction,
                            applied_head=landed_head,
                            applied_count=1,
                        )

    def test_only_source_less_legacy_transaction_enters_explicit_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, _ = self.make_conflicted_repo(Path(directory), commit_count=1)
            versioned = copy.deepcopy(state["active_merge"])
            versioned.pop("source_commits")
            with self.assertRaisesRegex(hloop.HLoopError, "versioned cherry-pick"):
                hloop.cherry_pick_transaction_from_active(repo, versioned)

            legacy = copy.deepcopy(state["active_merge"])
            legacy.pop("source_commits")
            for field in (
                "cherry_pick_transaction_version",
                "cherry_pick_evidence_policy",
                "cherry_pick_evidence_version",
                "cherry_pick_evidence_legacy_prefix_count",
                "cherry_pick_evidence",
            ):
                legacy.pop(field)
            hloop.preflight_merge_transaction(repo, legacy, "continue")
            self.assertEqual(
                legacy["cherry_pick_evidence_policy"],
                hloop.CHERRY_PICK_EVIDENCE_LEGACY_POLICY,
            )
            self.assertEqual(
                legacy["cherry_pick_evidence_version"],
                hloop.CHERRY_PICK_EVIDENCE_VERSION,
            )
            self.assertEqual(legacy["cherry_pick_evidence_legacy_prefix_count"], 0)
            self.assertTrue(legacy["cherry_pick_evidence_legacy_migration_digest"])

    def test_git_native_prediction_matches_sha1_sha256_encoding_and_hook_config(self):
        for object_format, hash_length in (("sha1", 40), ("sha256", 64)):
            for commit_count in (1, 3):
                with self.subTest(
                    object_format=object_format,
                    commit_count=commit_count,
                ), tempfile.TemporaryDirectory() as directory:
                    repo, state, source_commits = self.make_repo(
                        Path(directory),
                        commit_count=commit_count,
                        object_format=object_format,
                        commit_encoding="ISO-8859-1",
                    )
                    self.install_failing_prepare_commit_msg_hook(repo)
                    subprocess.run(
                        ["git", "config", "commit.gpgSign", "true"],
                        cwd=repo,
                        check=True,
                    )
                    self.run_recorded_cherry_pick(
                        repo,
                        state,
                        source_commits,
                        check=True,
                    )
                    landed_head = hloop.git(repo, ["rev-parse", "HEAD"])
                    evidence = state["active_merge"]["cherry_pick_evidence"]
                    expected_landed = tuple(
                        commit
                        for record in evidence
                        for commit in record["expected_landed_commits"]
                    )
                    landed = tuple(
                        hloop.git(
                            repo,
                            [
                                "rev-list",
                                "--reverse",
                                "--first-parent",
                                f"{state['active_merge']['pre_merge_head']}..{landed_head}",
                            ],
                        ).splitlines()
                    )
                    self.assertEqual(expected_landed, landed)
                    self.assertTrue(all(len(commit) == hash_length for commit in landed))
                    for source_commit, landed_commit in zip(source_commits, landed):
                        self.assertEqual(
                            hloop.commit_author_message(repo, source_commit),
                            hloop.commit_author_message(repo, landed_commit),
                        )
                        raw = hloop.git_bytes(repo, ["cat-file", "commit", landed_commit])
                        self.assertIn(b"\nencoding ISO-8859-1\n", raw)
                    self.assertTrue(
                        hloop.reconcile_completed_cherry_pick(repo, state, "T001")
                    )

    def test_conflict_continue_disables_prepare_hook_and_automatic_signing(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state, _ = self.make_conflicted_repo(Path(directory), commit_count=3)
            self.install_failing_prepare_commit_msg_hook(repo)
            subprocess.run(
                ["git", "config", "commit.gpgSign", "true"],
                cwd=repo,
                check=True,
            )
            recovered = self.continue_then_crash_before_state_completion(repo, state)
            landed_head = hloop.git(repo, ["rev-parse", "HEAD"])
            self.assertEqual(
                recovered["active_merge"]["cherry_pick_evidence"][0][
                    "expected_landed_commits"
                ][-1],
                landed_head,
            )

    def test_single_and_multi_commit_crash_after_git_completion_reconcile_once(self):
        for commit_count in (1, 3):
            with self.subTest(commit_count=commit_count), tempfile.TemporaryDirectory() as directory:
                repo, state, source_commits = self.make_repo(
                    Path(directory), commit_count=commit_count
                )
                self.run_recorded_cherry_pick(
                    repo,
                    state,
                    source_commits,
                    check=True,
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
            self.run_recorded_cherry_pick(
                repo,
                state,
                source_commits,
                check=True,
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
            self.run_recorded_cherry_pick(
                repo,
                state,
                source_commits,
                check=True,
            )
            (repo / "feature-0.txt").write_text("feature  0\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature-0.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "--amend", "--no-edit"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            with self.assertRaisesRegex(hloop.HLoopError, "landed commit identity"):
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
            with self.assertRaisesRegex(hloop.HLoopError, "missing an applied sequence"):
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
