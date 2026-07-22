import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_v05_integration_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class HLoopV05IntegrationTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        (repo / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
        return repo

    def run_cli(self, argv):
        argv = list(argv)
        for index in range(len(argv) - 1):
            if argv[index : index + 2] == ["task", "new"] and "--preserved-invariant" not in argv:
                argv.extend(
                    [
                        "--preserved-invariant",
                        "preserve integration fixture behavior",
                        "--regression-check",
                        "run the integration fixture regression",
                        "--risk-class",
                        "normal",
                        "--required-gate",
                        "patch_review",
                        "--required-gate",
                        "full_suite",
                    ]
                )
                break
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = hloop.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def init_loop(self, repo: Path, namespace: str):
        code, stdout, stderr = self.run_cli(
            [
                "--repo",
                str(repo),
                "--namespace",
                namespace,
                "init",
                "--goal",
                "integration fixture",
                "--integration",
                "master",
                "--persistence",
                "branch-history",
            ]
        )
        self.assertEqual((code, stderr), (0, ""), stdout)
        return repo / ".ai" / "herdr-dev-loop" / "loops" / namespace / "STATE.json"

    def isolated_config_env(self, root: Path):
        return mock.patch.dict(
            os.environ,
            {
                "HOME": str(root / "home"),
                "HLOOP_CONFIG_HOME": str(root / "config-home"),
                "XDG_CONFIG_HOME": str(root / "xdg"),
            },
            clear=False,
        )

    def test_config_cli_and_init_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                code, output, error = self.run_cli(["config", "init", "--json"])
                self.assertEqual((code, error), (0, ""), output)
                config_path = Path(json.loads(output)["created"])
                config_path.write_text(
                    """version = 1

[defaults]
max_workers = 5
session_cleanup = "delete"

[defaults.worker]
provider = "claude"
model = "sonnet"
effort = "high"

[defaults.reviewer]
provider = "codex"
model = "auto"
effort = "medium"
""",
                    encoding="utf-8",
                )
                code, output, error = self.run_cli(["config", "validate", "--json"])
                self.assertEqual((code, error), (0, ""), output)
                code, output, error = self.run_cli(
                    ["config", "explain", "--repo", str(repo), "--json"]
                )
                self.assertEqual((code, error), (0, ""), output)
                self.assertEqual(json.loads(output)["resolved"]["max_workers"], 5)

                state_path = self.init_loop(repo, "config-snapshot")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual((state["state_format_version"], state["schema_revision"]), (3, 3))
                self.assertEqual(state["max_workers"], 5)
                self.assertEqual(state["session_cleanup"], "delete")
                self.assertEqual(state["worker_agent_provider"], "claude")
                self.assertEqual(state["worker_agent_model"], "sonnet")
                self.assertEqual(state["config_source"]["path"], str(config_path))
                self.assertEqual(state["resolved_config"]["worker"]["effort"], "high")

    def test_format_two_migration_reaches_format_three_revision_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                state_path = self.init_loop(repo, "migration")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["state_format_version"] = 2
                state.pop("schema_revision", None)
                state["skill_version"] = "0.4.0"
                state.pop("first_v053_mutation_at", None)
                state.pop("first_v053_mutation_command", None)
                state_path.write_text(json.dumps(state), encoding="utf-8")

                prefix = ["--repo", str(repo), "--namespace", "migration", "migrate"]
                code, output, error = self.run_cli([*prefix, "--dry-run"])
                self.assertEqual((code, error), (0, ""), output)
                plan = json.loads(output)
                self.assertEqual((plan["to_format"], plan["to_revision"]), (3, 3))
                self.assertEqual(
                    plan["applied_steps"],
                    [
                        "format-2-to-3",
                        "format-3-revision-1",
                        "format-3-revision-2",
                        "format-3-revision-3",
                    ],
                )
                code, output, error = self.run_cli([*prefix, "--apply"])
                self.assertEqual((code, error), (0, ""), output)
                migrated = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    (migrated["state_format_version"], migrated["schema_revision"]),
                    (3, 3),
                )
                self.assertIn("artifact_policy", migrated)
                marker = hloop.load_migration_marker(repo)
                self.assertEqual(marker["status"], "committed")
                archived_state = (
                    hloop.migration_generation_root(repo, marker["migration_generation"])
                    / "archive"
                    / state_path.relative_to(repo)
                )
                self.assertTrue(archived_state.is_file())

    def test_format_three_revision_zero_migrates_and_current_contract_requires_revision(self):
        skill_root = SCRIPT.parents[1]
        schema = json.loads(
            (skill_root / "references" / "schemas" / "state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        contract = (skill_root / "references" / "artifact-contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("schema_revision", schema["required"])
        self.assertIn("schema_revision", hloop.contract_required_state_fields(contract))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                state_path = self.init_loop(repo, "revision-zero")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state.pop("schema_revision")
                state.pop("first_v053_mutation_at", None)
                state.pop("first_v053_mutation_command", None)
                state_path.write_text(json.dumps(state), encoding="utf-8")

                prefix = ["--repo", str(repo), "--namespace", "revision-zero", "migrate"]
                code, output, error = self.run_cli([*prefix, "--dry-run"])
                self.assertEqual((code, error), (0, ""), output)
                plan = json.loads(output)
                self.assertEqual((plan["from_format"], plan["from_revision"]), (3, 0))
                self.assertEqual(
                    plan["applied_steps"],
                    [
                        "format-3-revision-1",
                        "format-3-revision-2",
                        "format-3-revision-3",
                    ],
                )

                code, output, error = self.run_cli([*prefix, "--apply"])
                self.assertEqual((code, error), (0, ""), output)
                migrated = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    (migrated["state_format_version"], migrated["schema_revision"]),
                    (3, 3),
                )
                marker = hloop.load_migration_marker(repo)
                self.assertEqual(marker["status"], "committed")
                archived_state = (
                    hloop.migration_generation_root(repo, marker["migration_generation"])
                    / "archive"
                    / state_path.relative_to(repo)
                )
                self.assertTrue(archived_state.is_file())

    def test_selftest_rejects_revision_missing_from_schema_and_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            copied_skill = Path(directory) / "herdr-dev-loop"
            shutil.copytree(SCRIPT.parents[1], copied_skill)
            schema_path = copied_skill / "references" / "schemas" / "state.schema.json"
            schema_text = schema_path.read_text(encoding="utf-8")
            schema_path.write_text(
                schema_text.replace('    "schema_revision",\n', "", 1),
                encoding="utf-8",
            )
            contract_path = copied_skill / "references" / "artifact-contract.md"
            contract_text = contract_path.read_text(encoding="utf-8")
            contract_path.write_text(
                contract_text.replace("- `schema_revision`\n", "", 1),
                encoding="utf-8",
            )

            code, output, error = self.run_cli(
                ["selftest", "--skill-dir", str(copied_skill), "--json"]
            )
            self.assertEqual((code, error), (1, ""), output)
            self.assertIn(
                "state.schema.json lacks required schema_revision",
                json.loads(output)["errors"],
            )

    def test_future_revision_rejects_mutating_command_matrix_before_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                state_path = self.init_loop(repo, "future-state")
                worker_worktree = root / "worker-worktree"
                subprocess.run(
                    ["git", "worktree", "add", "-b", "future-worker", str(worker_worktree), "HEAD"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["schema_revision"] = 4
                state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
                journal_path = state_path.parent / "JOURNAL.md"
                prefix = ["--repo", str(repo), "--namespace", "future-state"]

                code, output, error = self.run_cli([*prefix, "status", "--raw-state"])
                self.assertEqual((code, error), (0, ""), output)
                self.assertEqual(json.loads(output)["schema_revision"], 4)
                before_migrate = (state_path.read_bytes(), journal_path.read_bytes())
                code, output, error = self.run_cli([*prefix, "migrate", "--dry-run"])
                self.assertEqual(code, 2)
                self.assertIn(
                    "state format-3.revision-4 is newer than runtime format-3.revision-3",
                    output + error,
                )
                self.assertEqual(
                    (state_path.read_bytes(), journal_path.read_bytes()),
                    before_migrate,
                )

                state["tasks"] = {
                    "T001": {
                        "status": "running",
                        "branch": "future-worker",
                        "worktree": str(worker_worktree),
                        "attempt_no": 1,
                    }
                }
                state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

                lock_output = subprocess.run(
                    ["git", "rev-parse", "--git-path", "hloop.lock"],
                    cwd=repo,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                lock_path = Path(lock_output)
                if not lock_path.is_absolute():
                    lock_path = repo / lock_path
                lock_path.unlink(missing_ok=True)

                def snapshot():
                    return {
                        "state": state_path.read_bytes(),
                        "journal": journal_path.read_bytes(),
                        "branches": subprocess.run(
                            ["git", "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads"],
                            cwd=repo,
                            check=True,
                            text=True,
                            capture_output=True,
                        ).stdout,
                        "worktrees": subprocess.run(
                            ["git", "worktree", "list", "--porcelain"],
                            cwd=repo,
                            check=True,
                            text=True,
                            capture_output=True,
                        ).stdout,
                        "worker_head": subprocess.run(
                            ["git", "rev-parse", "HEAD"],
                            cwd=worker_worktree,
                            check=True,
                            text=True,
                            capture_output=True,
                        ).stdout,
                    }

                before = snapshot()
                commands = {
                    "pause": ["pause", "--reason", "must refuse"],
                    "validation configure": [
                        "validation",
                        "configure",
                        "--command",
                        "python3 -m unittest",
                    ],
                    "agent abort": ["agent", "abort", "T001", "--reason", "must refuse"],
                    "agent requeue": ["agent", "requeue", "T001", "--reason", "must refuse"],
                }
                with mock.patch.object(
                    hloop,
                    "cleanup_completed_agent_pane",
                    side_effect=AssertionError("pane cleanup must not run"),
                ):
                    for label, command in commands.items():
                        with self.subTest(command=label):
                            code, _, error = self.run_cli([*prefix, *command])
                            self.assertEqual(code, 2)
                            self.assertIn(
                                "state format-3.revision-4 is newer than runtime "
                                "format-3.revision-3",
                                error,
                            )
                            self.assertEqual(snapshot(), before)
                            self.assertFalse(lock_path.exists())

    def test_pre_final_pause_resumes_without_unreached_gate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                state_path = self.init_loop(repo, "pre-final-resume")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["manager_qa_profile"] = "local"
                state["manager_qa_status"] = "pending"
                state_path.write_text(json.dumps(state), encoding="utf-8")
                prefix = ["--repo", str(repo), "--namespace", "pre-final-resume"]

                code, output, error = self.run_cli(
                    [*prefix, "pause", "--reason", "operator handoff"]
                )
                self.assertEqual((code, error), (0, ""), output)
                code, output, error = self.run_cli([*prefix, "resume"])
                self.assertEqual((code, error), (0, ""), output)
                resumed = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(resumed["phase"], "dispatching")
                self.assertEqual(resumed["resume_requirements"], [])

    def test_resume_rejects_only_previously_passing_gate_that_became_stale(self):
        gate_states = {
            "validation": lambda state, head: state.update(
                {"last_validation": {"head_sha": head, "results": [{"result": "passed"}]}}
            ),
            "review": lambda state, head: state.update(
                {
                    "reviews": {
                        "R001": {
                            "status": "triaged",
                            "gate_status": "triaged",
                            "head_sha": head,
                            "closed_head_sha": head,
                        }
                    }
                }
            ),
            "gap": lambda state, head: state.update(
                {
                    "gaps": {
                        "G001": {
                            "status": "triaged",
                            "gate_status": "triaged",
                            "head_sha": head,
                            "closed_head_sha": head,
                        }
                    }
                }
            ),
            "manager-qa": lambda state, head: state.update(
                {
                    "manager_qa_profile": "local",
                    "manager_qa_status": "passed",
                    "manager_qa_head_sha": head,
                    "completion_target_sha": head,
                }
            ),
        }
        for gate_name, configure_gate in gate_states.items():
            with self.subTest(gate=gate_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = self.make_repo(root)
                with self.isolated_config_env(root):
                    namespace = f"stale-{gate_name}"
                    state_path = self.init_loop(repo, namespace)
                    old_head = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=repo,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip()
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    configure_gate(state, old_head)
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    prefix = ["--repo", str(repo), "--namespace", namespace]

                    code, output, error = self.run_cli(
                        [*prefix, "pause", "--reason", "target may advance"]
                    )
                    self.assertEqual((code, error), (0, ""), output)
                    (repo / "advance.txt").write_text("advance\n", encoding="utf-8")
                    subprocess.run(["git", "add", "advance.txt"], cwd=repo, check=True)
                    subprocess.run(
                        ["git", "commit", "-m", "advance target"],
                        cwd=repo,
                        check=True,
                        capture_output=True,
                    )

                    code, _, error = self.run_cli([*prefix, "resume"])
                    self.assertEqual(code, 2)
                    self.assertIn(f"{gate_name} was recorded for a different target", error)
                    paused = json.loads(state_path.read_text(encoding="utf-8"))
                    self.assertEqual(paused["phase"], "paused")
                    self.assertEqual(len(paused["resume_requirements"]), 1)
                    requirement = paused["resume_requirements"][0]
                    self.assertEqual(requirement["code"], "gate-stale")
                    self.assertEqual(requirement["subject"], gate_name)

    def test_final_gate_arms_only_stable_batch_and_new_task_disarms_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                state_path = self.init_loop(repo, "final-gate")
                (state_path.parent / "PLAN.md").write_text(
                    "# Plan\n\n- P001: final gate fixture\n",
                    encoding="utf-8",
                )
                prefix = ["--repo", str(repo), "--namespace", "final-gate"]
                code, output, error = self.run_cli(
                    [*prefix, "release-scope", "lock", "--plan-item-ref", "P001"]
                )
                self.assertEqual((code, error), (0, ""), output)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["batches"] = {
                    "B001": {"id": "B001", "status": "closed", "task_ids": []}
                }
                state["tasks"] = {"T000": {"status": "merged", "batch_id": "B001"}}
                state_path.write_text(json.dumps(state), encoding="utf-8")
                code, output, error = self.run_cli([*prefix, "final-gates", "arm"])
                self.assertEqual((code, error), (0, ""), output)
                armed = json.loads(state_path.read_text(encoding="utf-8"))["final_gate"]
                self.assertEqual(armed["status"], "armed")

                code, output, error = self.run_cli(
                    [
                        *prefix,
                        "task",
                        "new",
                        "follow-up",
                        "--write-allow",
                        "src/**",
                        "--task-origin",
                        "planned",
                        "--plan-item-ref",
                        "P001",
                    ]
                )
                self.assertEqual((code, error), (0, ""), output)
                disarmed = json.loads(state_path.read_text(encoding="utf-8"))["final_gate"]
                self.assertEqual(disarmed["status"], "disarmed")
                self.assertIn("new task created", disarmed["disarm_reason"])

    def test_final_strict_review_and_gap_gates_require_manager_arm(self):
        """Merging the last task alone must not open the final strict gates.

        Cadence review/gap thresholds during ongoing dispatch are untouched;
        only the all-tasks-merged "final completion" trigger requires an
        explicit `hloop final-gates arm`, and a subsequent disarm (from a new
        fix task) must return that trigger to closed, not leave it armed.
        """

        state = {"tasks": {"T001": {"status": "merged"}}, "reviews": {}, "gaps": {}}
        self.assertFalse(hloop.should_open_review_gate(state))
        self.assertFalse(hloop.should_open_gap_gate(state))

        state["final_gate"] = {
            "generation": 1,
            "status": "armed",
            "target_sha": "deadbeef",
            "armed_at": "2026-01-01T00:00:00+00:00",
            "armed_by": "manager",
            "disarmed_at": "",
            "disarmed_by": "",
            "disarm_reason": "",
        }
        self.assertTrue(hloop.should_open_review_gate(state))
        self.assertTrue(hloop.should_open_gap_gate(state))

        hloop.disarm_final_gate_for_new_task(state, "T002")
        self.assertEqual(state["final_gate"]["status"], "disarmed")
        self.assertFalse(hloop.should_open_review_gate(state))
        self.assertFalse(hloop.should_open_gap_gate(state))

    def test_done_target_drift_is_p0_on_status_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            with self.isolated_config_env(root):
                state_path = self.init_loop(repo, "done-drift")
                old_head = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
                ).stdout.strip()
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["phase"] = "done"
                state["final_target_sha"] = old_head
                state_path.write_text(json.dumps(state), encoding="utf-8")
                (repo / "next.txt").write_text("next\n", encoding="utf-8")
                subprocess.run(["git", "add", "next.txt"], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "advance"], cwd=repo, check=True, capture_output=True)

                hloop.configure_loop_namespace("done-drift")
                inventory = hloop.collect_loop_inventory(repo, probe_panes=False)
                issue = next(item for item in inventory["issues"] if item["code"] == "done-target-drift")
                self.assertEqual(issue["severity"], "P0")
                self.assertIn(old_head, issue["message"])
                self.assertIn("1 advancing commits", issue["message"])

    def test_harvested_artifact_paths_use_manager_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            hloop.configure_loop_namespace("artifacts")
            manager_result = hloop.result_file(repo, "T001")
            manager_result.parent.mkdir(parents=True)
            manager_result.write_text("result\n", encoding="utf-8")
            task = {
                "status": "merged",
                "harvested_at": "now",
                "result_path": str(manager_result),
                "worktree": str(repo / "removed-worktree"),
                "cleanup_done": True,
            }
            path = hloop.worker_artifact_path(repo, {"tasks": {"T001": task}}, "T001", task)
            self.assertEqual(path, manager_result)
            worktree = hloop.worktree_state(task["worktree"])
            self.assertEqual(
                hloop.artifact_location_status(task, path, worktree),
                "cleaned-after-harvest",
            )
            manager_result.unlink()
            self.assertEqual(
                hloop.artifact_location_status(task, path, worktree),
                "unexpectedly-missing",
            )

    def test_format_two_migration_rewrites_harvested_artifacts_to_manager_paths(self):
        previous = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("migration-artifacts")
        try:
            state = {
                "state_format_version": 2,
                "skill_version": "0.4.0",
                "tasks": {
                    "T001": {
                        "status": "merged",
                        "result_path": "/tmp/old-worker/results/T001/result.md",
                    }
                },
                "reviews": {
                    "R001": {
                        "status": "triaged",
                        "gate_status": "triaged",
                        "mode": "single",
                        "review_path": "/tmp/old-review/reviews/R001.md",
                    }
                },
                "gaps": {
                    "G001": {
                        "status": "triaged",
                        "gate_status": "triaged",
                        "gap_path": "/tmp/old-gap/gaps/G001.md",
                    }
                },
            }
            migrated = hloop.migrate_format_two_to_three(state)
            loop = hloop.LOOP_DIR.as_posix()
            self.assertEqual(
                migrated["tasks"]["T001"]["result_path"],
                f"{loop}/results/T001/result.md",
            )
            self.assertEqual(
                migrated["reviews"]["R001"]["review_path"],
                f"{loop}/reviews/R001.md",
            )
            self.assertEqual(
                migrated["gaps"]["G001"]["gap_path"],
                f"{loop}/gaps/G001.md",
            )
            self.assertIn(
                "/tmp/old-worker",
                migrated["tasks"]["T001"]["worktree_result_path_harvested"],
            )
            self.assertEqual(
                migrated["specification_scout_run"]["status"], "skipped"
            )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_harvested_worker_lookup_ignores_stale_recorded_worktree_path(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                hloop.configure_loop_namespace("canonical-worker")
                manager_result = hloop.result_file(repo, "T001")
                manager_result.parent.mkdir(parents=True)
                manager_result.write_text("manager copy\n", encoding="utf-8")
                task = {
                    "status": "merged",
                    "result_path": "/tmp/removed-worktree/result.md",
                }
                self.assertEqual(
                    hloop.worker_artifact_path(repo, {"tasks": {"T001": task}}, "T001", task),
                    manager_result,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_migrated_worker_results_use_canonical_manager_copy_without_warning(self):
        previous = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("canonical-migrated-workers")
        try:
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                tasks = {
                    f"T{index:03d}": {
                        "status": "merged",
                        "result_path": f"/tmp/removed-T{index:03d}/result.md",
                        "worktree": f"/tmp/removed-T{index:03d}",
                    }
                    for index in range(1, 25)
                }
                migrated = hloop.migrate_format_two_to_three(
                    {
                        "state_format_version": 2,
                        "skill_version": "0.4.0",
                        "tasks": tasks,
                    }
                )
                for task_id, task in migrated["tasks"].items():
                    self.assertEqual(
                        task["result_path"],
                        f"{hloop.LOOP_DIR.as_posix()}/results/{task_id}/result.md",
                    )
                    issue_codes = {
                        issue["code"]
                        for issue in hloop.agent_contract_issues(
                            repo,
                            role="worker",
                            agent_id=task_id,
                            agent=task,
                        )
                    }
                    self.assertNotIn("manager-owned-worker-result", issue_codes)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_failed_worker_validation_blocks_finalize_harvest_and_merge(self):
        previous = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("worker-validation-gates")
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = self.make_repo(root)
                base_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                worktree = root / "worker"
                subprocess.run(
                    ["git", "worktree", "add", "-b", "worker-validation", str(worktree), "master"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                task_meta = {
                    "contract_schema_revision": 2,
                    "id": "T001",
                    "run_id": "run-validation",
                    "kind": "fix",
                    "status": "running",
                    "branch": "worker-validation",
                    "base_ref": "master",
                    "base_sha": base_sha,
                    "write_allow": ["README.md"],
                    "write_deny": [],
                    "acceptance": ["validation passes"],
                }
                task_text = hloop.frontmatter(task_meta) + "\n\n# Task T001\n"
                hloop.write_text(hloop.task_file(repo, "T001"), task_text)
                hloop.write_text(hloop.task_file(worktree, "T001"), task_text)
                result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
                result_meta = {
                    "contract_schema_revision": 2,
                    "task_id": "T001",
                    "run_id": "run-validation",
                    "skill_version": hloop.SKILL_VERSION,
                    "attempt_id": "T001-A001",
                    "status": "done",
                    "merge_ready": True,
                    "branch": "worker-validation",
                    "head_sha": "HEAD",
                    "base_sha": base_sha,
                    "changed_files": [result_rel.as_posix()],
                    "validation_recorded": True,
                    "validation_commands": ["false"],
                    "validation_results": ["failed"],
                    "validation_summary": "expected fixture failure",
                    "blocking_questions": [],
                }
                hloop.write_text(
                    worktree / result_rel,
                    hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n",
                )
                subprocess.run(
                    ["git", "add", "-f", result_rel.as_posix()],
                    cwd=worktree,
                    check=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", "fixture result"],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                )
                task_state = {
                    **task_meta,
                    "status": "running",
                    "worktree": str(worktree),
                    "attempt_id": "T001-A001",
                    "active_attempt_id": "T001-A001",
                    "worker_base_sha": base_sha,
                    "skill_version": hloop.SKILL_VERSION,
                    "semantic_ack_barrier": {"status": "approved"},
                }
                state = {
                    "state_format_version": hloop.STATE_FORMAT_VERSION,
                    "schema_revision": hloop.STATE_SCHEMA_REVISION,
                    "namespace": hloop.LOOP_NAMESPACE,
                    "run_id": "run-validation",
                    "skill_version": hloop.SKILL_VERSION,
                    "phase": "running",
                    "integration_branch": "master",
                    "merge_mode": "squash",
                    "persistence": "local-only",
                    "tasks": {"T001": task_state},
                }
                worker_state = json.loads(json.dumps(state))
                state["tasks"]["T001"]["semantic_ack_barrier"] = {
                    "status": "awaiting_ack",
                    "message_id": "task-contract:" + "a" * 64,
                }
                hloop.save_state(repo, state)
                hloop.save_state(worktree, worker_state)

                with mock.patch.object(hloop, "porcelain_paths", return_value=[]), mock.patch.object(hloop, "porcelain_paths_no_renames", return_value=[]):
                    with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                        hloop.cmd_worker_finalize(
                            argparse.Namespace(
                                repo=str(worktree),
                                task_id="T001",
                                status="done",
                                validation_command=["true"],
                                validation_result=["passed"],
                                validation_summary="passed",
                                blocking_question=[],
                                no_commit=True,
                            )
                        )

                state["tasks"]["T001"]["semantic_ack_barrier"] = {"status": "approved"}
                hloop.save_state(repo, state)

                with mock.patch.object(hloop, "porcelain_paths", return_value=[]), mock.patch.object(hloop, "porcelain_paths_no_renames", return_value=[]):
                    with self.assertRaisesRegex(hloop.HLoopError, "validation did not pass"):
                        hloop.cmd_worker_finalize(
                            argparse.Namespace(
                                repo=str(worktree),
                                task_id="T001",
                                status="done",
                                validation_command=["false"],
                                validation_result=["failed"],
                                validation_summary="failed",
                                blocking_question=[],
                                no_commit=True,
                            )
                        )

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(
                        hloop.HLoopError, "merge_ready is true with a non-passed validation result"
                    ):
                        hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo),
                                task_id="T001",
                                keep_pane=False,
                                session_cleanup=None,
                            )
                        )
                self.assertFalse(hloop.result_file(repo, "T001").exists())

                merge_state = json.loads(json.dumps(state))
                merge_state["tasks"]["T001"].update(
                    {
                        "status": "result_reported",
                        "result_status": "done",
                        "merge_ready": True,
                        "validation_recorded": True,
                        "validation_commands": ["false"],
                        "validation_results": ["failed"],
                        "blocking_questions": [],
                    }
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=merge_state):
                    with self.assertRaisesRegex(hloop.HLoopError, "validation did not pass"):
                        hloop.cmd_merge(
                            argparse.Namespace(
                                repo=str(repo),
                                task_id="T001",
                                abort=False,
                                continue_merge=False,
                                retry=False,
                                mode=None,
                                dry_run=False,
                            )
                        )
        finally:
            hloop.configure_loop_namespace(previous)

    def make_worker_seal_fixture(self, root: Path, *, namespace: str, status: str = "done"):
        """Build a Worker worktree with an uncommitted (workspace-write-style) handoff.

        Seeds a tracked file the Worker will modify, a tracked file the Worker
        will delete, and leaves an untracked file plus `result.md` dirty --
        exactly what `hloop worker finalize --handoff` produces without ever
        invoking Git. The Worker pane is recorded as already quiesced and
        closed (`pane_closed_at` set) since that is a separate, dedicated
        precondition covered by its own tests.
        """

        hloop.configure_loop_namespace(namespace)
        repo = self.make_repo(root)
        (repo / "keep.txt").write_text("base\n", encoding="utf-8")
        (repo / "drop.txt").write_text("base\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "tracked.txt").write_text("tracked sibling\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "keep.txt", "drop.txt", "src/tracked.txt"], cwd=repo, check=True
        )
        subprocess.run(["git", "commit", "-m", "seed files"], cwd=repo, check=True, capture_output=True)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        worktree = root / "worker"
        subprocess.run(
            ["git", "worktree", "add", "-b", "worker-seal", str(worktree), "master"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        run_id = f"run-{namespace}"
        task_meta = {
            "contract_schema_revision": 2,
            "id": "T001",
            "run_id": run_id,
            "kind": "fix",
            "status": "running",
            "branch": "worker-seal",
            "base_ref": "master",
            "base_sha": base_sha,
            "write_allow": ["keep.txt", "drop.txt", "new.txt"],
            "write_deny": [],
            "acceptance": ["handoff seals cleanly"],
        }
        hloop.write_text(hloop.task_file(repo, "T001"), hloop.frontmatter(task_meta) + "\n\n# Task T001\n")

        # Simulate a workspace-write sandbox: only file edits, never Git.
        (worktree / "keep.txt").write_text("changed\n", encoding="utf-8")
        (worktree / "drop.txt").unlink()
        (worktree / "new.txt").write_text("untracked\n", encoding="utf-8")
        result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
        result_meta = {
            "contract_schema_revision": 2,
            "task_id": "T001",
            "run_id": run_id,
            "skill_version": hloop.SKILL_VERSION,
            "attempt_id": "T001-A001",
            "status": status,
            "merge_ready": False,
            "branch": "worker-seal",
            "head_sha": "HEAD",
            "base_sha": base_sha,
            "changed_files": ["keep.txt", "drop.txt", "new.txt", result_rel.as_posix()],
            "validation_recorded": False,
            "validation_commands": [],
            "validation_results": [],
            "validation_summary": "",
            "blocking_questions": [],
            "handoff": True,
        }
        hloop.write_text(
            worktree / result_rel,
            hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n",
        )
        task_state = {
            **task_meta,
            "worktree": str(worktree),
            "attempt_id": "T001-A001",
            "active_attempt_id": "T001-A001",
            "worker_base_sha": base_sha,
            "skill_version": hloop.SKILL_VERSION,
            "semantic_ack_barrier": {"status": "approved"},
            "pane_closed_at": "2026-01-01T00:00:00+00:00",
        }
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "run_id": run_id,
            "skill_version": hloop.SKILL_VERSION,
            "phase": "running",
            "integration_branch": "master",
            "merge_mode": "squash",
            "persistence": "local-only",
            "tasks": {"T001": task_state},
        }
        return repo, worktree, state, result_rel

    def seal_args(self, repo, *, attempt_id=None, validation_command=None, validation_summary=None):
        return argparse.Namespace(
            repo=str(repo),
            task_id="T001",
            attempt_id=attempt_id,
            validation_command=validation_command,
            validation_summary=validation_summary,
        )

    def snapshot_worktree_state(self, worktree: Path) -> dict:
        """Byte-for-byte descriptor of every path under `worktree` (excluding
        `.git`), including empty directories -- used to assert an isolated
        validation command left the original worktree completely untouched."""

        state = {}
        for root, dirnames, filenames in os.walk(worktree, followlinks=False):
            root_path = Path(root)
            if root_path == worktree and ".git" in dirnames:
                dirnames.remove(".git")
            rel_root = root_path.relative_to(worktree).as_posix()
            if rel_root != ".":
                state[rel_root] = ("dir", None)
            for name in filenames:
                full = root_path / name
                rel = full.relative_to(worktree).as_posix()
                if full.is_symlink():
                    state[rel] = ("symlink", os.readlink(full))
                else:
                    mode = "x" if os.access(full, os.X_OK) else ""
                    state[rel] = ("file" + mode, full.read_bytes())
        return state

    def test_worker_seal_commits_handoff_with_tracked_untracked_and_deleted_changes(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-success"
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=["exit 0"], validation_summary="ok")
                        )
                self.assertEqual(code, 0)
                # Everything -- tracked edit, tracked deletion, untracked add,
                # and the result artifact -- must be sealed into one commit.
                self.assertEqual(hloop.porcelain_paths(worktree), [])
                head_message = subprocess.run(
                    ["git", "log", "-1", "--format=%s"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                self.assertIn("seal worker handoff", head_message)
                self.assertIn("T001-A001", head_message)
                self.assertEqual((worktree / "keep.txt").read_text(encoding="utf-8"), "changed\n")
                self.assertFalse((worktree / "drop.txt").exists())
                self.assertEqual((worktree / "new.txt").read_text(encoding="utf-8"), "untracked\n")
                sealed = hloop.read_frontmatter(worktree / result_rel)
                self.assertTrue(hloop.normalize_bool(sealed["merge_ready"]))
                self.assertEqual(sealed["validation_commands"], ["exit 0"])
                self.assertEqual(sealed["validation_results"], ["passed"])
                self.assertEqual(sealed["validation_summary"], "ok")

                # A sealed handoff must flow through the existing harvest path unchanged.
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        harvest_code = hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo),
                                task_id="T001",
                                keep_pane=False,
                                session_cleanup=None,
                            )
                        )
                self.assertEqual(harvest_code, 0)
                harvested = hloop.read_frontmatter(hloop.result_file(repo, "T001"))
                self.assertEqual(harvested["status"], "done")
                self.assertTrue(hloop.normalize_bool(harvested["merge_ready"]))
                self.assertIn("keep.txt", harvested["changed_files"])
                self.assertIn("drop.txt", harvested["changed_files"])
                self.assertIn("new.txt", harvested["changed_files"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_persists_exact_tree_before_validation_and_commits_it(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-tree-before-validation", status="done"
                )
                observed = {}
                real_build = hloop.build_validation_snapshot

                def inspect_transaction(snapshot_source, staged_tree, expected_parent):
                    transaction = state["tasks"]["T001"]["seal_transaction"]
                    self.assertEqual(transaction["staged_tree"], staged_tree)
                    self.assertEqual(transaction["expected_parent"], expected_parent)
                    self.assertFalse(transaction["validation_passed"])
                    self.assertEqual(
                        subprocess.run(
                            ["git", "write-tree"],
                            cwd=worktree,
                            check=True,
                            text=True,
                            capture_output=True,
                        ).stdout.strip(),
                        staged_tree,
                    )
                    staged_result = hloop.read_frontmatter(worktree / result_rel)
                    self.assertEqual(staged_result["validation_results"], ["passed"])
                    observed["tree"] = staged_tree
                    return real_build(snapshot_source, staged_tree, expected_parent)

                with mock.patch.object(
                    hloop, "build_validation_snapshot", side_effect=inspect_transaction
                ):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            code = hloop.cmd_worker_seal(
                                self.seal_args(repo, validation_command=["exit 0"])
                            )

                self.assertEqual(code, 0)
                self.assertEqual(
                    subprocess.run(
                        ["git", "show", "-s", "--format=%T", "HEAD"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip(),
                    observed["tree"],
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_rejects_noncanonical_source_index_flags_before_scope_scan(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            for option, expected in (
                ("--assume-unchanged", "assume-unchanged"),
                ("--skip-worktree", "skip-worktree"),
            ):
                with self.subTest(option=option):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        repo, worktree, state, _ = self.make_worker_seal_fixture(
                            root,
                            namespace=f"worker-seal-index-{expected}",
                            status="partial",
                        )
                        subprocess.run(
                            ["git", "update-index", option, "--", "src/tracked.txt"],
                            cwd=worktree,
                            check=True,
                        )
                        with mock.patch.object(hloop, "preflight_loop", return_value=state):
                            with self.assertRaisesRegex(
                                hloop.HLoopError, rf"{expected}.*src/tracked.txt|src/tracked.txt.*{expected}"
                            ):
                                hloop.cmd_worker_seal(self.seal_args(repo))
                        self.assertNotIn("seal_transaction", state["tasks"]["T001"])
                        self.assertEqual(
                            subprocess.run(
                                ["git", "diff", "--cached", "--quiet"],
                                cwd=worktree,
                                check=False,
                            ).returncode,
                            0,
                        )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_out_of_scope_dirty_path(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-scope", status="partial"
                )
                (worktree / "out-of-scope.txt").write_text("nope\n", encoding="utf-8")
                # Keep the handoff's declared changed_files matching what is
                # actually on disk so this test isolates the scope check from
                # the (separately tested) stale-artifact check.
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                result_meta["changed_files"] = [*result_meta["changed_files"], "out-of-scope.txt"]
                hloop.write_text(result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n")
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "write-scope violations"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                # Fail closed: nothing may be staged or committed.
                self.assertIn("out-of-scope.txt", hloop.porcelain_paths(worktree))
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_out_of_scope_rename_source(self):
        """A rename's source path must not escape scope checking behind its destination."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                hloop.configure_loop_namespace("worker-seal-rename-source")
                repo = self.make_repo(root)
                (repo / "secret.txt").write_text("classified\n", encoding="utf-8")
                subprocess.run(["git", "add", "secret.txt"], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "seed secret"], cwd=repo, check=True, capture_output=True)
                base_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
                ).stdout.strip()
                worktree = root / "worker"
                subprocess.run(
                    ["git", "worktree", "add", "-b", "worker-seal-rename", str(worktree), "master"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                run_id = "run-rename-source"
                task_meta = {
                    "contract_schema_revision": 2,
                    "id": "T001",
                    "run_id": run_id,
                    "kind": "fix",
                    "status": "running",
                    "branch": "worker-seal-rename",
                    "base_ref": "master",
                    "base_sha": base_sha,
                    "write_allow": ["allowed.txt"],
                    "write_deny": [],
                    "acceptance": ["x"],
                }
                hloop.write_text(hloop.task_file(repo, "T001"), hloop.frontmatter(task_meta) + "\n\n# Task T001\n")
                # Rename an out-of-scope tracked file straight into an
                # in-scope destination name, with content unchanged -- the
                # shape Git's rename heuristic detects most eagerly.
                (worktree / "secret.txt").rename(worktree / "allowed.txt")
                result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
                result_meta = {
                    "contract_schema_revision": 2,
                    "task_id": "T001",
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "attempt_id": "T001-A001",
                    "status": "partial",
                    "merge_ready": False,
                    "branch": "worker-seal-rename",
                    "head_sha": "HEAD",
                    "base_sha": base_sha,
                    "changed_files": ["secret.txt", "allowed.txt", result_rel.as_posix()],
                    "validation_recorded": False,
                    "validation_commands": [],
                    "validation_results": [],
                    "validation_summary": "",
                    "blocking_questions": [],
                    "handoff": True,
                }
                hloop.write_text(
                    worktree / result_rel, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n"
                )
                task_state = {
                    **task_meta,
                    "worktree": str(worktree),
                    "attempt_id": "T001-A001",
                    "active_attempt_id": "T001-A001",
                    "worker_base_sha": base_sha,
                    "skill_version": hloop.SKILL_VERSION,
                    "semantic_ack_barrier": {"status": "approved"},
                    "pane_closed_at": "2026-01-01T00:00:00+00:00",
                }
                state = {
                    "state_format_version": hloop.STATE_FORMAT_VERSION,
                    "schema_revision": hloop.STATE_SCHEMA_REVISION,
                    "namespace": hloop.LOOP_NAMESPACE,
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "phase": "running",
                    "integration_branch": "master",
                    "merge_mode": "squash",
                    "persistence": "local-only",
                    "tasks": {"T001": task_state},
                }
                # Git only collapses a rename's source/destination once the
                # change is staged, so the source must still be individually
                # scope-checked once `git add -A` runs during sealing.
                self.assertIn("secret.txt", hloop.porcelain_paths_no_renames(worktree))
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "write-scope violations"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertIn("secret.txt", hloop.porcelain_paths_no_renames(worktree))
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_stale_declared_changed_files(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-stale-scope", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                # Understate reality: drop "drop.txt" from the declared scope
                # even though it is actually deleted on disk.
                result_meta["changed_files"] = ["keep.txt", "new.txt", result_rel.as_posix()]
                hloop.write_text(result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n")
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "stale handoff artifact"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_stale_attempt(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-stale-attempt", status="partial"
                )
                state["tasks"]["T001"]["active_attempt_id"] = "T001-A002"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "handoff attempt_id mismatch"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_empty_handoff_attempt_id(self):
        """An empty/missing attempt_id must fail closed exactly like a mismatched one."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-empty-attempt", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                result_meta["attempt_id"] = ""
                hloop.write_text(result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n")
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "invalid attempt_id"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_preserves_worker_validation_fields_for_non_done_status(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-preserve-validation", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                result_meta["validation_recorded"] = True
                result_meta["validation_commands"] = ["npm test"]
                result_meta["validation_results"] = ["failed"]
                result_meta["validation_summary"] = "unit tests failed on edge case"
                hloop.write_text(result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n")
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertEqual(code, 0)
                sealed = hloop.read_frontmatter(result_path)
                # No Manager validation ran for a non-`done` handoff -- the Worker's own
                # declared validation evidence must survive the seal untouched.
                self.assertTrue(hloop.normalize_bool(sealed["validation_recorded"]))
                self.assertEqual(sealed["validation_commands"], ["npm test"])
                self.assertEqual(sealed["validation_results"], ["failed"])
                self.assertEqual(sealed["validation_summary"], "unit tests failed on edge case")
                self.assertFalse(hloop.normalize_bool(sealed["merge_ready"]))
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_inconsistent_worker_validation_for_non_done_status(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-inconsistent-validation", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                # validation_recorded is true but the commands/results counts do not match.
                result_meta["validation_recorded"] = True
                result_meta["validation_commands"] = ["npm test", "npm lint"]
                result_meta["validation_results"] = ["failed"]
                hloop.write_text(result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n")
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(
                        hloop.HLoopError, "invalid validation record in handoff artifact"
                    ):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_harvest_fails_closed_on_attempt_id_mismatch(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-harvest-attempt-mismatch"
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=["exit 0"])
                        )
                self.assertEqual(code, 0)

                # The Manager's recorded active attempt moved on (e.g. a requeue)
                # after the result was committed, but the committed artifact still
                # reports the earlier attempt. Harvest must not silently accept it.
                state["tasks"]["T001"]["active_attempt_id"] = "T001-A002"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "result attempt_id mismatch"):
                        hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo),
                                task_id="T001",
                                keep_pane=False,
                                session_cleanup=None,
                            )
                        )
                self.assertNotEqual(state["tasks"]["T001"].get("status"), "result_reported")
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_when_semantic_ack_not_approved(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-ack", status="partial"
                )
                state["tasks"]["T001"]["semantic_ack_barrier"] = {
                    "status": "awaiting_ack",
                    "message_id": "task-contract:" + "a" * 64,
                }
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_non_handoff_artifact(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-non-handoff", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                result_meta["handoff"] = False
                hloop.write_text(
                    result_path,
                    hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n",
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "not a durable handoff"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_missing_manager_validation_command(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-missing-validation", status="done"
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "without at least one Manager"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_does_not_commit_when_manager_validation_fails_even_with_mutation(self):
        """A validation command's mutation runs against the isolated snapshot
        only -- it must never reach the original worktree, whether the
        command fails or (see below) passes."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-validation-fail", status="done"
                )
                head_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                # Relative to `cwd` -- with isolation, validation always runs
                # inside the disposable snapshot, never in `worktree` itself.
                mutate_and_fail = "echo side-effect > side-effect.txt; exit 1"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Manager validation command failed"):
                        hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_fail])
                        )
                head_after = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                self.assertEqual(head_before, head_after)
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
                # The mutation happened only inside the (now-discarded)
                # isolated snapshot -- it never touched this worktree.
                self.assertFalse((worktree / "side-effect.txt").exists())
                self.assertEqual((worktree / "keep.txt").read_text(encoding="utf-8"), "changed\n")
                self.assertFalse((worktree / "drop.txt").exists())
                self.assertEqual((worktree / "new.txt").read_text(encoding="utf-8"), "untracked\n")
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_retries_cleanly_after_manager_validation_failure(self):
        """A failing validation command never mutates this worktree (it ran in
        an isolated, discarded snapshot), so a retried seal with a passing
        command must succeed -- not get stuck on a stale handoff artifact."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-validation-retry", status="done"
                )
                mutate_and_fail = "echo side-effect > side-effect.txt; exit 1"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Manager validation command failed"):
                        hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_fail])
                        )
                self.assertFalse((worktree / "side-effect.txt").exists())

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(
                                repo, validation_command=["exit 0"], validation_summary="retry ok"
                            )
                        )
                self.assertEqual(code, 0)
                sealed = hloop.read_frontmatter(worktree / result_rel)
                self.assertTrue(hloop.normalize_bool(sealed["merge_ready"]))
                self.assertEqual(sealed["validation_commands"], ["exit 0"])
                self.assertEqual(sealed["validation_summary"], "retry ok")
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_ignores_out_of_scope_mutation_from_passing_validation_command(self):
        """A passing validation command may still mutate its isolated snapshot
        (e.g. a formatter or generated output), but that mutation must never
        leak into the sealed commit -- seal must land exactly the
        pre-validation recorded tree, with the mutated path absent from both
        the worktree and HEAD afterward."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-validation-mutation", status="done"
                )
                head_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                mutate_and_pass = "echo side-effect > side-effect.txt"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_pass])
                        )
                self.assertEqual(code, 0)
                head_after = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                self.assertNotEqual(head_before, head_after)
                # The mutation stayed in the discarded snapshot -- it must
                # not exist in the worktree or be part of the sealed commit.
                self.assertFalse((worktree / "side-effect.txt").exists())
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", "HEAD:side-effect.txt"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
                sealed = hloop.read_frontmatter(worktree / result_rel)
                self.assertTrue(hloop.normalize_bool(sealed["merge_ready"]))
                self.assertNotIn("side-effect.txt", sealed["changed_files"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_absolute_original_path_change_survives_only_unstaged(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, _ = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-absolute-original", status="done"
                )
                absolute_keep = shlex.quote(str(worktree / "keep.txt"))
                validation = f"printf 'changed outside snapshot\\n' > {absolute_keep}"

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[validation])
                        )

                self.assertEqual(code, 0)
                self.assertEqual(
                    subprocess.run(
                        ["git", "show", "HEAD:keep.txt"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout,
                    "changed\n",
                )
                self.assertEqual(
                    (worktree / "keep.txt").read_text(encoding="utf-8"),
                    "changed outside snapshot\n",
                )
                self.assertIn("keep.txt", hloop.porcelain_paths_no_renames(worktree))
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_validation_commands_share_one_recorded_tree_snapshot(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, _ = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-sequential-validation", status="done"
                )
                commands = ["printf ready > sequence.marker", "test -f sequence.marker"]
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=commands)
                        )
                self.assertEqual(code, 0)
                self.assertFalse((worktree / "sequence.marker").exists())
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", "HEAD:sequence.marker"],
                        cwd=worktree,
                        check=False,
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_aborts_and_resets_when_worktree_changes_while_staging(self):
        """Simulate the TOCTOU window between the pre-stage scan and `git add`."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-toctou", status="partial"
                )
                real_dirty = hloop.porcelain_paths_no_renames(worktree)
                raced_dirty = [*real_dirty, "late-arriving.txt"]
                with mock.patch.object(
                    hloop, "porcelain_paths_no_renames", side_effect=[real_dirty, raced_dirty]
                ):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaisesRegex(hloop.HLoopError, "worktree changed while staging"):
                            hloop.cmd_worker_seal(self.seal_args(repo))
                # Fail closed: the race must abort with everything unstaged and uncommitted.
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_commits_recorded_tree_when_same_path_is_restaged_before_ref_update(self):
        """Same-path content re-staged after the tree checkpoint must survive
        in the worktree but must never replace the recorded commit tree."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-recorded-tree-normal", status="partial"
                )
                real_run_cmd = hloop.run_cmd
                injected = False

                def restage_before_ref_update(cmd, *cmd_args, **cmd_kwargs):
                    nonlocal injected
                    if isinstance(cmd, list) and "update-ref" in cmd and not injected:
                        injected = True
                        (worktree / "keep.txt").write_text("tampered\n", encoding="utf-8")
                        subprocess.run(["git", "add", "keep.txt"], cwd=worktree, check=True)
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=restage_before_ref_update):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            code = hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertEqual(code, 0)
                committed_keep = subprocess.run(
                    ["git", "show", "HEAD:keep.txt"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout
                self.assertEqual(committed_keep, "changed\n")
                self.assertEqual((worktree / "keep.txt").read_text(encoding="utf-8"), "tampered\n")
                self.assertIn("keep.txt", hloop.porcelain_paths_no_renames(worktree))
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resumes_after_crash_between_stage_and_commit(self):
        """A crash right after `git add -A` but before `git commit` must be recoverable."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-crash-resume", status="done"
                )

                real_run_cmd = hloop.run_cmd

                class SimulatedCrash(Exception):
                    pass

                def crashing_run_cmd(cmd, *cmd_args, **cmd_kwargs):
                    if isinstance(cmd, list) and "commit-tree" in cmd:
                        raise SimulatedCrash("process died before commit")
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=crashing_run_cmd):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            with self.assertRaises(SimulatedCrash):
                                hloop.cmd_worker_seal(
                                    self.seal_args(
                                        repo, validation_command=["exit 0"], validation_summary="ok"
                                    )
                                )

                # The crash left the index staged and a resumable transaction recorded --
                # not lost work requiring manual `git reset`/`git add` repair.
                self.assertIn("seal_transaction", state["tasks"]["T001"])
                self.assertTrue(
                    state["tasks"]["T001"]["seal_transaction"]["validation_passed"]
                )
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    1,
                )
                self.assertEqual(hloop.porcelain_paths_no_renames(worktree), [])

                # A fresh seal invocation -- without re-supplying
                # --validation-command, since that already ran and was
                # recorded before the crash -- must recognize and finish the
                # crashed transaction instead of failing closed on "already
                # has staged changes".
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertEqual(code, 0)
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
                self.assertEqual(hloop.porcelain_paths(worktree), [])
                head_message = subprocess.run(
                    ["git", "log", "-1", "--format=%s"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                self.assertIn("seal worker handoff", head_message)
                self.assertIn("T001-A001", head_message)
                sealed = hloop.read_frontmatter(worktree / result_rel)
                self.assertTrue(hloop.normalize_bool(sealed["merge_ready"]))
                self.assertEqual(sealed["validation_commands"], ["exit 0"])
                self.assertEqual(sealed["validation_summary"], "ok")

                # No work was lost across the crash.
                self.assertEqual((worktree / "keep.txt").read_text(encoding="utf-8"), "changed\n")
                self.assertFalse((worktree / "drop.txt").exists())
                self.assertEqual((worktree / "new.txt").read_text(encoding="utf-8"), "untracked\n")

                # A resumed seal flows through harvest unchanged.
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        harvest_code = hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo),
                                task_id="T001",
                                keep_pane=False,
                                session_cleanup=None,
                            )
                        )
                self.assertEqual(harvest_code, 0)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_crash_before_validation_marker_reconciles_and_reruns(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, _ = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-prevalidation-marker-crash", status="done"
                )

                class SimulatedProcessDeath(BaseException):
                    pass

                with mock.patch.object(
                    hloop,
                    "run_seal_validation_command",
                    side_effect=SimulatedProcessDeath("died before pass marker"),
                ):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaises(SimulatedProcessDeath):
                            hloop.cmd_worker_seal(
                                self.seal_args(repo, validation_command=["exit 0"])
                            )

                transaction = state["tasks"]["T001"]["seal_transaction"]
                self.assertIn("staged_tree", transaction)
                self.assertFalse(transaction["validation_passed"])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    1,
                )

                measured = []

                def passing_validation(snapshot, command):
                    measured.append((snapshot, command))
                    return True, ""

                with mock.patch.object(
                    hloop, "run_seal_validation_command", side_effect=passing_validation
                ):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            code = hloop.cmd_worker_seal(
                                self.seal_args(repo, validation_command=["exit 0"])
                            )
                self.assertEqual(code, 0)
                self.assertEqual(len(measured), 1)
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resume_commits_recorded_tree_when_same_path_is_restaged_before_ref_update(self):
        """Resume must also commit the durable tree when same-path index
        content changes after the resume verification window."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-recorded-tree-resume", status="done"
                )
                real_run_cmd = hloop.run_cmd

                class SimulatedCrash(Exception):
                    pass

                def crash_before_commit_tree(cmd, *cmd_args, **cmd_kwargs):
                    if isinstance(cmd, list) and "commit-tree" in cmd:
                        raise SimulatedCrash("process died before exact-tree commit")
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=crash_before_commit_tree):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaises(SimulatedCrash):
                            hloop.cmd_worker_seal(
                                self.seal_args(repo, validation_command=["exit 0"])
                            )

                injected = False

                def restage_before_ref_update(cmd, *cmd_args, **cmd_kwargs):
                    nonlocal injected
                    if isinstance(cmd, list) and "update-ref" in cmd and not injected:
                        injected = True
                        (worktree / "keep.txt").write_text("tampered\n", encoding="utf-8")
                        subprocess.run(["git", "add", "keep.txt"], cwd=worktree, check=True)
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=restage_before_ref_update):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            code = hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertEqual(code, 0)
                committed_keep = subprocess.run(
                    ["git", "show", "HEAD:keep.txt"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout
                self.assertEqual(committed_keep, "changed\n")
                self.assertEqual((worktree / "keep.txt").read_text(encoding="utf-8"), "tampered\n")
                self.assertIn("keep.txt", hloop.porcelain_paths_no_renames(worktree))
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resume_preserves_post_validation_worktree_change_unstaged(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, _ = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-resume-dirty-worktree", status="done"
                )
                real_run_cmd = hloop.run_cmd

                class SimulatedCrash(Exception):
                    pass

                def crash_before_commit(cmd, *cmd_args, **cmd_kwargs):
                    if isinstance(cmd, list) and "commit-tree" in cmd:
                        raise SimulatedCrash("died after validation marker")
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=crash_before_commit):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaises(SimulatedCrash):
                            hloop.cmd_worker_seal(
                                self.seal_args(repo, validation_command=["exit 0"])
                            )
                self.assertTrue(
                    state["tasks"]["T001"]["seal_transaction"]["validation_passed"]
                )
                (worktree / "keep.txt").write_text(
                    "changed after validation\n", encoding="utf-8"
                )

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertEqual(code, 0)
                self.assertEqual(
                    subprocess.run(
                        ["git", "show", "HEAD:keep.txt"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout,
                    "changed\n",
                )
                self.assertEqual(
                    (worktree / "keep.txt").read_text(encoding="utf-8"),
                    "changed after validation\n",
                )
                self.assertIn("keep.txt", hloop.porcelain_paths_no_renames(worktree))
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_when_branch_moves_before_ref_cas(self):
        """A concurrent branch advance must win or lose atomically; the seal
        commit must never be installed on top of an unexpected parent."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-ref-cas", status="partial"
                )
                real_run_cmd = hloop.run_cmd
                competitor = ""

                def move_branch_before_cas(cmd, *cmd_args, **cmd_kwargs):
                    nonlocal competitor
                    if isinstance(cmd, list) and "update-ref" in cmd and not competitor:
                        update_index = cmd.index("update-ref")
                        branch_ref = cmd[update_index + 1]
                        expected_parent = cmd[update_index + 3]
                        parent_tree = real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "rev-parse",
                                f"{expected_parent}^{{tree}}",
                            ],
                            check=True,
                        ).stdout.strip()
                        competitor = real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "commit-tree",
                                parent_tree,
                                "-p",
                                expected_parent,
                                "-m",
                                "concurrent branch advance",
                            ],
                            check=True,
                        ).stdout.strip()
                        real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "update-ref",
                                branch_ref,
                                competitor,
                                expected_parent,
                            ],
                            check=True,
                        )
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=move_branch_before_cas):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaisesRegex(hloop.HLoopError, "compare-and-swap refused"):
                            hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertTrue(competitor)
                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip(),
                    competitor,
                )
                self.assertEqual(
                    subprocess.run(
                        ["git", "show", "HEAD:keep.txt"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout,
                    "base\n",
                )
                self.assertIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_reconciles_crash_after_ref_cas_without_discarding_worktree(self):
        """A crash after update-ref wins must be recognized from commit_sha
        and finished without producing a second commit."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-post-cas-crash", status="partial"
                )
                real_run_cmd = hloop.run_cmd

                class SimulatedCrash(Exception):
                    pass

                def crash_before_reconcile(cmd, *cmd_args, **cmd_kwargs):
                    if isinstance(cmd, list) and "read-tree" in cmd and cmd[-1] != "HEAD":
                        raise SimulatedCrash("process died after ref CAS")
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=crash_before_reconcile):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaises(SimulatedCrash):
                            hloop.cmd_worker_seal(self.seal_args(repo))

                transaction = state["tasks"]["T001"]["seal_transaction"]
                landed_commit = transaction["commit_sha"]
                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip(),
                    landed_commit,
                )

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertEqual(code, 0)
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip(),
                    landed_commit,
                )
                self.assertEqual(hloop.porcelain_paths_no_renames(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resume_fails_closed_on_foreign_staged_changes(self):
        """Staged content that does not match a recorded transaction must still fail closed."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-foreign-staged", status="partial"
                )
                subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "already has staged changes"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resume_fails_closed_on_stale_recorded_transaction(self):
        """A recorded transaction for a different attempt must not authorize a resume."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-stale-transaction", status="partial"
                )
                state["tasks"]["T001"]["seal_transaction"] = {
                    "attempt_id": "T001-A000",
                    "result_rel": result_rel.as_posix(),
                    "staged_paths": ["keep.txt"],
                    "commit_message": "ai-loop(T001): seal worker handoff (T001-A000)",
                }
                subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "already has staged changes"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                # The stale transaction record must not be treated as resolved.
                self.assertIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resume_fails_closed_on_tampered_staged_tree(self):
        """Same staged paths but different content after a crash must fail closed."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-tampered-tree", status="done"
                )

                real_run_cmd = hloop.run_cmd

                class SimulatedCrash(Exception):
                    pass

                def crashing_run_cmd(cmd, *cmd_args, **cmd_kwargs):
                    if isinstance(cmd, list) and "commit-tree" in cmd:
                        raise SimulatedCrash("process died before commit")
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=crashing_run_cmd):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            with self.assertRaises(SimulatedCrash):
                                hloop.cmd_worker_seal(
                                    self.seal_args(
                                        repo, validation_command=["exit 0"], validation_summary="ok"
                                    )
                                )

                transaction = state["tasks"]["T001"]["seal_transaction"]
                self.assertIn("staged_tree", transaction)
                head_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()

                # Tamper: the same staged path (`keep.txt`) now carries different
                # content than what was durably recorded before the crash --
                # simulating something re-staging or altering it in the window
                # between the crash and the next seal attempt.
                (worktree / "keep.txt").write_text("tampered\n", encoding="utf-8")
                subprocess.run(["git", "add", "keep.txt"], cwd=worktree, check=True)

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "staged tree identity"):
                        hloop.cmd_worker_seal(self.seal_args(repo))

                # Fail closed: nothing committed, and the transaction is cleared
                # rather than left around to authorize a later silent resume.
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
                head_after = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                self.assertEqual(head_before, head_after)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_reconciles_and_revalidates_when_tree_identity_not_yet_durable(self):
        """A pre-tree crash safely unstages and requires validation again."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-pretree-crash", status="done"
                )

                real_git = hloop.git

                class SimulatedCrash(Exception):
                    pass

                def crashing_git(repo_path, args, check=True):
                    if args and args[0] == "write-tree":
                        raise SimulatedCrash("process died before tree identity was durable")
                    return real_git(repo_path, args, check=check)

                with mock.patch.object(hloop, "git", side_effect=crashing_git):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with contextlib.redirect_stdout(io.StringIO()):
                            with self.assertRaises(SimulatedCrash):
                                hloop.cmd_worker_seal(
                                    self.seal_args(
                                        repo, validation_command=["exit 0"], validation_summary="ok"
                                    )
                                )

                # The index is staged (`add -A`/`add -f` already ran) but the
                # crash happened before the durable tree-identity checkpoint,
                # so the recorded transaction must not carry a `staged_tree`.
                transaction = state["tasks"]["T001"]["seal_transaction"]
                self.assertNotIn("staged_tree", transaction)
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    1,
                )
                head_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "without at least one Manager"):
                        hloop.cmd_worker_seal(self.seal_args(repo))

                # Must not silently resume-commit an unverified tree. The
                # index-only reconciliation preserves every working byte,
                # clears the stale transaction, and reaches the ordinary
                # missing-validation gate for a fresh attempt.
                self.assertNotIn("seal_transaction", state["tasks"]["T001"])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
                head_after = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                self.assertEqual(head_before, head_after)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_when_branch_moves_during_index_reconciliation(self):
        """A competitor advance racing the post-CAS index reconciliation must
        survive untouched; seal must fail closed and never move the ref back
        the way a mixed reset would."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-reconcile-race", status="partial"
                )
                real_run_cmd = hloop.run_cmd
                competitor = ""
                triggered = False

                def move_branch_during_reconcile(cmd, *cmd_args, **cmd_kwargs):
                    nonlocal competitor, triggered
                    if (
                        isinstance(cmd, list)
                        and "read-tree" in cmd
                        and cmd[-1] != "HEAD"
                        and not triggered
                    ):
                        triggered = True
                        landed = real_run_cmd(
                            [hloop.TRUSTED_GIT_PATH, "-C", str(worktree), "rev-parse", "HEAD"],
                            check=True,
                        ).stdout.strip()
                        landed_tree = real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "rev-parse",
                                f"{landed}^{{tree}}",
                            ],
                            check=True,
                        ).stdout.strip()
                        competitor = real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "commit-tree",
                                landed_tree,
                                "-p",
                                landed,
                                "-m",
                                "concurrent branch advance during reconciliation",
                            ],
                            check=True,
                        ).stdout.strip()
                        real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "update-ref",
                                "refs/heads/worker-seal",
                                competitor,
                                landed,
                            ],
                            check=True,
                        )
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=move_branch_during_reconcile):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaisesRegex(hloop.HLoopError, "index reconciliation aborted"):
                            hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertTrue(competitor)
                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                    ).stdout.strip(),
                    competitor,
                )
                self.assertIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_resume_fails_closed_when_branch_moves_during_index_reconciliation(self):
        """A crash right at the post-CAS index reconciliation checkpoint,
        followed by a competitor advance racing the resumed reconciliation,
        must still fail closed and preserve the competitor -- crash resume
        gets no special exemption from the ref-safety check."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-resume-reconcile-race", status="partial"
                )
                real_run_cmd = hloop.run_cmd

                class SimulatedCrash(Exception):
                    pass

                def crash_before_reconcile(cmd, *cmd_args, **cmd_kwargs):
                    if isinstance(cmd, list) and "read-tree" in cmd and cmd[-1] != "HEAD":
                        raise SimulatedCrash("process died after ref CAS")
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=crash_before_reconcile):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaises(SimulatedCrash):
                            hloop.cmd_worker_seal(self.seal_args(repo))

                transaction = state["tasks"]["T001"]["seal_transaction"]
                landed_commit = transaction["commit_sha"]
                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                    ).stdout.strip(),
                    landed_commit,
                )

                competitor = ""
                triggered = False

                def move_branch_during_resume_reconcile(cmd, *cmd_args, **cmd_kwargs):
                    nonlocal competitor, triggered
                    if (
                        isinstance(cmd, list)
                        and "read-tree" in cmd
                        and cmd[-1] != "HEAD"
                        and not triggered
                    ):
                        triggered = True
                        landed_tree = real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "rev-parse",
                                f"{landed_commit}^{{tree}}",
                            ],
                            check=True,
                        ).stdout.strip()
                        competitor = real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "commit-tree",
                                landed_tree,
                                "-p",
                                landed_commit,
                                "-m",
                                "concurrent branch advance during resumed reconciliation",
                            ],
                            check=True,
                        ).stdout.strip()
                        real_run_cmd(
                            [
                                hloop.TRUSTED_GIT_PATH,
                                "-C",
                                str(worktree),
                                "update-ref",
                                "refs/heads/worker-seal",
                                competitor,
                                landed_commit,
                            ],
                            check=True,
                        )
                    return real_run_cmd(cmd, *cmd_args, **cmd_kwargs)

                with mock.patch.object(hloop, "run_cmd", side_effect=move_branch_during_resume_reconcile):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaisesRegex(hloop.HLoopError, "index reconciliation aborted"):
                            hloop.cmd_worker_seal(self.seal_args(repo))

                self.assertTrue(competitor)
                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                    ).stdout.strip(),
                    competitor,
                )
                self.assertIn("seal_transaction", state["tasks"]["T001"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_validation_failure_deleting_pre_existing_empty_directory_leaves_worktree_unchanged(self):
        """A failing validation command that deletes a pre-existing empty
        directory must never affect the original worktree -- it ran against
        an isolated snapshot, which is discarded whole on failure."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-isolation-empty-dir", status="done"
                )
                empty_dir = worktree / "pre-existing-empty"
                empty_dir.mkdir()
                before = self.snapshot_worktree_state(worktree)

                # Relative to `cwd` -- the command executes inside the
                # disposable snapshot, so "pre-existing-empty" here refers to
                # the snapshot's own copy of that directory.
                mutate_and_fail = "rmdir pre-existing-empty; exit 1"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Manager validation command failed"):
                        hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_fail])
                        )

                self.assertTrue(empty_dir.is_dir())
                self.assertEqual(self.snapshot_worktree_state(worktree), before)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_validation_failure_creating_new_ignored_tree_leaves_worktree_unchanged(self):
        """A failing validation command that creates a whole new ignored
        directory tree (e.g. a build/formatter output dir matching
        `.gitignore`) must leave the original worktree byte-for-byte
        unchanged -- Git status would never even report it as dirty, but the
        isolation snapshot must still make sure it never lands there."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-isolation-ignored-tree", status="done"
                )
                # An excludes file outside the worktree keeps "build-output/"
                # ignored without adding a new untracked path inside the
                # worktree itself (which would be a separate, unrelated
                # write-scope violation for this fixture's declared scope).
                excludes_file = root / "excludes"
                excludes_file.write_text("build-output/\n", encoding="utf-8")
                subprocess.run(
                    ["git", "config", "core.excludesFile", str(excludes_file)],
                    cwd=worktree,
                    check=True,
                )
                before = self.snapshot_worktree_state(worktree)

                new_dir = "build-output"
                mutate_and_fail = f"mkdir -p {new_dir}/nested; echo artifact > {new_dir}/nested/out.bin; exit 1"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Manager validation command failed"):
                        hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_fail])
                        )

                self.assertFalse((worktree / new_dir).exists())
                self.assertEqual(self.snapshot_worktree_state(worktree), before)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_validation_success_seals_original_content_despite_snapshot_mutation(self):
        """A passing validation command that mutates already-dirty content,
        resurrects a pre-validation deletion, and changes file mode -- all
        inside its isolated snapshot -- must still result in exactly the
        pre-validation recorded tree being sealed, with the worktree left
        untouched by any of it."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-isolation-success-content", status="done"
                )
                keep_path = worktree / "keep.txt"
                drop_path = worktree / "drop.txt"
                self.assertFalse(os.access(keep_path, os.X_OK))
                result_key = result_rel.as_posix()
                before = self.snapshot_worktree_state(worktree)
                before.pop(result_key, None)

                mutate_and_pass = (
                    "echo further-mutated > keep.txt; "
                    "chmod +x keep.txt; "
                    "echo resurrected > drop.txt"
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_pass])
                        )
                self.assertEqual(code, 0)

                # The worktree is untouched by the snapshot mutation: not
                # further-mutated content, not made executable, and the
                # pre-validation deletion is not resurrected. The result
                # artifact itself legitimately changes -- seal overwrites its
                # validation fields -- so it is excluded from this
                # comparison and checked separately below.
                after = self.snapshot_worktree_state(worktree)
                after.pop(result_key, None)
                self.assertEqual(after, before)

                # And the sealed commit carries exactly the pre-validation
                # recorded tree -- "changed\n" for keep.txt, not executable,
                # and drop.txt still absent.
                self.assertEqual(
                    subprocess.run(
                        ["git", "show", "HEAD:keep.txt"], cwd=worktree, check=True, text=True, capture_output=True
                    ).stdout,
                    "changed\n",
                )
                mode = subprocess.run(
                    ["git", "ls-tree", "HEAD", "keep.txt"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout
                self.assertTrue(mode.startswith("100644"))
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", "HEAD:drop.txt"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_validation_preserves_existing_directory_and_tracked_sibling_on_failure(self):
        """A failing validation command writing a new file under an existing
        tracked directory, inside its isolated snapshot, must leave that
        directory and its tracked sibling in the original worktree exactly
        as they were."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-isolation-tracked-sibling", status="done"
                )
                tracked = worktree / "src" / "tracked.txt"
                created = worktree / "src" / "new.txt"
                before = self.snapshot_worktree_state(worktree)

                mutate_and_fail = "echo generated > src/new.txt; exit 1"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Manager validation command failed"):
                        hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=[mutate_and_fail])
                        )

                self.assertTrue((worktree / "src").is_dir())
                self.assertEqual(tracked.read_text(encoding="utf-8"), "tracked sibling\n")
                self.assertFalse(created.exists())
                self.assertEqual(self.snapshot_worktree_state(worktree), before)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_validation_replacing_symlink_with_directory_leaves_worktree_unchanged(self):
        """A failing validation command that deletes a dirty symlink and
        replaces it with a directory, inside its isolated snapshot, must
        leave the original symlink completely untouched."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                namespace = "worker-seal-isolation-symlink-dir"
                hloop.configure_loop_namespace(namespace)
                repo = self.make_repo(root)
                (repo / "target.txt").write_text("target\n", encoding="utf-8")
                subprocess.run(["git", "add", "target.txt"], cwd=repo, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "seed target"], cwd=repo, check=True, capture_output=True
                )
                base_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
                ).stdout.strip()
                worktree = root / "worker"
                subprocess.run(
                    ["git", "worktree", "add", "-b", "worker-seal-symlink-dir", str(worktree), "master"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                run_id = f"run-{namespace}"
                task_meta = {
                    "contract_schema_revision": 2,
                    "id": "T001",
                    "run_id": run_id,
                    "kind": "fix",
                    "status": "running",
                    "branch": "worker-seal-symlink-dir",
                    "base_ref": "master",
                    "base_sha": base_sha,
                    "write_allow": ["target.txt", "link.txt"],
                    "write_deny": [],
                    "acceptance": ["handoff seals cleanly"],
                }
                hloop.write_text(
                    hloop.task_file(repo, "T001"), hloop.frontmatter(task_meta) + "\n\n# Task T001\n"
                )
                (worktree / "link.txt").symlink_to("target.txt")
                result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
                result_meta = {
                    "contract_schema_revision": 2,
                    "task_id": "T001",
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "attempt_id": "T001-A001",
                    "status": "done",
                    "merge_ready": False,
                    "branch": "worker-seal-symlink-dir",
                    "head_sha": "HEAD",
                    "base_sha": base_sha,
                    "changed_files": ["link.txt", result_rel.as_posix()],
                    "validation_recorded": False,
                    "validation_commands": [],
                    "validation_results": [],
                    "validation_summary": "",
                    "blocking_questions": [],
                    "handoff": True,
                }
                hloop.write_text(
                    worktree / result_rel, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n"
                )
                task_state = {
                    **task_meta,
                    "worktree": str(worktree),
                    "attempt_id": "T001-A001",
                    "active_attempt_id": "T001-A001",
                    "worker_base_sha": base_sha,
                    "skill_version": hloop.SKILL_VERSION,
                    "semantic_ack_barrier": {"status": "approved"},
                    "pane_closed_at": "2026-01-01T00:00:00+00:00",
                }
                state = {
                    "state_format_version": hloop.STATE_FORMAT_VERSION,
                    "schema_revision": hloop.STATE_SCHEMA_REVISION,
                    "namespace": hloop.LOOP_NAMESPACE,
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "phase": "running",
                    "integration_branch": "master",
                    "merge_mode": "squash",
                    "persistence": "local-only",
                    "tasks": {"T001": task_state},
                }

                link_path = worktree / "link.txt"
                before = self.snapshot_worktree_state(worktree)
                # Relative to `cwd` -- this operates on the snapshot's own
                # copy of link.txt, never on the original worktree.
                mutate_symlink_to_directory_and_fail = (
                    "rm link.txt; mkdir -p link.txt; echo stray > link.txt/stray.txt; exit 1"
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Manager validation command failed"):
                        hloop.cmd_worker_seal(
                            self.seal_args(
                                repo, validation_command=[mutate_symlink_to_directory_and_fail]
                            )
                        )

                self.assertTrue(link_path.is_symlink())
                self.assertEqual(os.readlink(link_path), "target.txt")
                self.assertEqual(self.snapshot_worktree_state(worktree), before)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_build_validation_snapshot_matches_worktree_git_status_and_diff(self):
        """The snapshot must be git-aware in a way that is indistinguishable
        from the original worktree: `git status`/`git diff` run inside it
        must report exactly the same thing they would in the worktree
        itself (tracked modification, staged-free, and untracked files
        alike)."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
            (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            expected_parent = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            staged_tree = subprocess.run(
                ["git", "write-tree"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()

            expected_status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout
            expected_diff = subprocess.run(
                ["git", "diff"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout

            snapshot = hloop.build_validation_snapshot(repo, staged_tree, expected_parent)
            try:
                observed_status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=snapshot,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout
                observed_diff = subprocess.run(
                    ["git", "diff"], cwd=snapshot, check=True, text=True, capture_output=True
                ).stdout
                self.assertEqual(observed_status, expected_status)
                self.assertEqual(observed_diff, expected_diff)
            finally:
                hloop.remove_validation_snapshot(snapshot)

    def test_build_validation_snapshot_fails_closed_on_symlink_escaping_worktree(self):
        """A symlink whose target resolves outside the worktree could let a
        validation command read or write arbitrary filesystem paths even
        from inside the disposable snapshot -- there is no way to isolate
        that, so building the snapshot must fail closed before anything
        runs, and the original worktree/target must be left untouched."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            secret = root / "secret.txt"
            secret.write_text("classified\n", encoding="utf-8")
            (repo / "escape.txt").symlink_to(secret)
            subprocess.run(["git", "add", "escape.txt"], cwd=repo, check=True)
            expected_parent = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            staged_tree = subprocess.run(
                ["git", "write-tree"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            before_repo = self.snapshot_worktree_state(repo)
            before_secret = secret.read_bytes()

            with self.assertRaisesRegex(
                hloop.HLoopError, "resolves outside the worktree and cannot be safely isolated"
            ):
                hloop.build_validation_snapshot(repo, staged_tree, expected_parent)

            self.assertEqual(self.snapshot_worktree_state(repo), before_repo)
            self.assertEqual(secret.read_bytes(), before_secret)

    def test_build_validation_snapshot_rewrites_absolute_internal_symlink_via_worktree_alias(self):
        """An absolute symlink target must be judged and rewritten by its
        canonical (symlink-resolved) location, not by a literal string
        comparison against the worktree path handed in -- otherwise a
        worktree reached through an alias (a symlinked ancestor directory,
        as with some systems' temp directories) would misclassify a
        perfectly legitimate internal absolute symlink as escaping."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_root = root / "real"
            real_root.mkdir()
            alias_root = root / "alias"
            alias_root.symlink_to(real_root, target_is_directory=True)

            repo = real_root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "master", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
            (repo / "target.txt").write_text("hi\n", encoding="utf-8")
            # Written using the *real* (non-alias) absolute path, while the
            # worktree handed to `build_validation_snapshot` below is the
            # aliased one -- a literal-string containment check would see no
            # shared prefix and wrongly reject this as escaping.
            (repo / "link.txt").symlink_to((repo / "target.txt").resolve())
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

            aliased_repo = alias_root / "repo"
            expected_parent = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=aliased_repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            staged_tree = subprocess.run(
                ["git", "write-tree"], cwd=aliased_repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            snapshot = hloop.build_validation_snapshot(
                aliased_repo, staged_tree, expected_parent
            )
            try:
                rewritten = os.readlink(snapshot / "link.txt")
                self.assertTrue(rewritten.startswith(str(snapshot) + os.sep))
                self.assertEqual(Path(rewritten).read_text(encoding="utf-8"), "hi\n")
            finally:
                hloop.remove_validation_snapshot(snapshot)

    def test_build_validation_snapshot_preserves_directory_mode_and_mtime(self):
        """Ignored dependency directory metadata is reproduced too."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            (repo / ".gitignore").write_text("restricted/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "ignore dependency"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            restricted = repo / "restricted"
            restricted.mkdir()
            (restricted / "inside.txt").write_text("hi\n", encoding="utf-8")
            os.chmod(restricted, 0o700)
            os.utime(restricted, (1_600_000_000, 1_600_000_000))
            expected_stat = os.stat(restricted)

            expected_parent = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            staged_tree = subprocess.run(
                ["git", "write-tree"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            snapshot = hloop.build_validation_snapshot(repo, staged_tree, expected_parent)
            try:
                observed_stat = os.stat(snapshot / "restricted")
                self.assertEqual(stat.S_IMODE(observed_stat.st_mode), stat.S_IMODE(expected_stat.st_mode))
                self.assertEqual(int(observed_stat.st_mtime), int(expected_stat.st_mtime))
            finally:
                os.chmod(restricted, 0o755)
                hloop.remove_validation_snapshot(snapshot)

    def test_build_validation_snapshot_isolates_nested_repo_and_linked_submodule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def make_origin(name):
                origin = root / name
                origin.mkdir()
                subprocess.run(
                    ["git", "init", "-q", "-b", "master"], cwd=origin, check=True
                )
                subprocess.run(
                    ["git", "config", "user.email", "nested@example.com"],
                    cwd=origin,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "Nested"], cwd=origin, check=True
                )
                (origin / "tracked.txt").write_text(f"{name}\n", encoding="utf-8")
                subprocess.run(["git", "add", "tracked.txt"], cwd=origin, check=True)
                subprocess.run(
                    ["git", "commit", "-qm", "seed"], cwd=origin, check=True
                )
                return origin

            sub_origin = make_origin("sub-origin")
            nested_origin = make_origin("nested-origin")
            repo = self.make_repo(root)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "add",
                    "-q",
                    str(sub_origin),
                    "deps/sub",
                ],
                cwd=repo,
                check=True,
            )
            (repo / "tools").mkdir()
            subprocess.run(
                ["git", "clone", "-q", str(nested_origin), str(repo / "tools/nested")],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "add", ".gitmodules", "deps/sub", "tools/nested"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "add nested repositories"], cwd=repo, check=True
            )

            worktree = root / "linked-super"
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "linked-super", str(worktree), "master"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "update",
                    "--init",
                    "-q",
                    "deps/sub",
                ],
                cwd=worktree,
                check=True,
            )
            nested_worktree = worktree / "tools/nested"
            nested_worktree.rmdir()
            subprocess.run(
                ["git", "clone", "-q", str(nested_origin), str(nested_worktree)],
                cwd=worktree,
                check=True,
            )
            sub_worktree = worktree / "deps/sub"
            self.assertTrue((sub_worktree / ".git").is_file())
            self.assertTrue((nested_worktree / ".git").is_dir())

            (sub_worktree / "tracked.txt").write_text("sub modified\n", encoding="utf-8")
            (sub_worktree / "staged.txt").write_text("sub staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "staged.txt"], cwd=sub_worktree, check=True)
            (nested_worktree / "tracked.txt").write_text(
                "nested modified\n", encoding="utf-8"
            )
            (nested_worktree / "untracked.txt").write_text(
                "nested untracked\n", encoding="utf-8"
            )

            def git_observation(path):
                return tuple(
                    subprocess.run(
                        command,
                        cwd=path,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout
                    for command in (
                        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                        ["git", "diff"],
                        ["git", "diff", "--cached"],
                    )
                )

            expected = {
                ".": git_observation(worktree),
                "deps/sub": git_observation(sub_worktree),
                "tools/nested": git_observation(nested_worktree),
            }
            expected_parent = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            staged_tree = subprocess.run(
                ["git", "write-tree"],
                cwd=worktree,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

            snapshot = hloop.build_validation_snapshot(
                worktree, staged_tree, expected_parent
            )
            try:
                for relative, observation in expected.items():
                    self.assertEqual(git_observation(snapshot / relative), observation)
                    self.assertEqual(
                        subprocess.run(
                            ["git", "remote"],
                            cwd=snapshot / relative,
                            check=True,
                            text=True,
                            capture_output=True,
                        ).stdout,
                        "",
                    )
                self.assertTrue((snapshot / "deps/sub/.git").is_dir())
                self.assertTrue((snapshot / "tools/nested/.git").is_dir())

                snapshot_sub = snapshot / "deps/sub"
                source_before = git_observation(sub_worktree)
                unique = f"snapshot-only-{os.getpid()}-{time.time_ns()}\n"
                object_id = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    cwd=snapshot_sub,
                    input=unique,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                (snapshot_sub / "snapshot-only.txt").write_text(unique, encoding="utf-8")
                subprocess.run(["git", "add", "snapshot-only.txt"], cwd=snapshot_sub, check=True)
                subprocess.run(
                    ["git", "update-ref", "refs/heads/snapshot-only", "HEAD"],
                    cwd=snapshot_sub,
                    check=True,
                )

                self.assertEqual(git_observation(sub_worktree), source_before)
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "show-ref", "--verify", "refs/heads/snapshot-only"],
                        cwd=sub_worktree,
                        check=False,
                        capture_output=True,
                    ).returncode,
                    0,
                )
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", object_id],
                        cwd=sub_worktree,
                        check=False,
                        capture_output=True,
                    ).returncode,
                    0,
                )
            finally:
                hloop.remove_validation_snapshot(snapshot)

    def test_build_validation_snapshot_dissociates_borrowed_object_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = self.make_repo(root)
            source = root / "shared-source"
            subprocess.run(
                ["git", "clone", "-q", "--shared", str(origin), str(source)],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "shared@example.com"],
                cwd=source,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Shared"], cwd=source, check=True
            )
            (source / "README.md").write_text("staged candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            expected_parent = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            staged_tree = subprocess.run(
                ["git", "write-tree"],
                cwd=source,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

            snapshot = hloop.build_validation_snapshot(
                source, staged_tree, expected_parent
            )
            try:
                alternates = Path(
                    subprocess.run(
                        [
                            "git",
                            "rev-parse",
                            "--path-format=absolute",
                            "--git-path",
                            "objects/info/alternates",
                        ],
                        cwd=snapshot,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip()
                )
                self.assertFalse(alternates.exists())
                self.assertEqual(
                    subprocess.run(
                        ["git", "write-tree"],
                        cwd=snapshot,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip(),
                    staged_tree,
                )
                unique = f"snapshot-borrowed-{time.time_ns()}\n"
                object_id = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    cwd=snapshot,
                    input=unique,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", object_id],
                        cwd=source,
                        check=False,
                        capture_output=True,
                    ).returncode,
                    0,
                )
            finally:
                hloop.remove_validation_snapshot(snapshot)

    def test_remove_validation_snapshot_repairs_read_only_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot"
            locked = snapshot / "locked"
            locked.mkdir(parents=True)
            artifact = locked / "artifact.bin"
            artifact.write_bytes(b"immutable")
            os.chmod(artifact, 0o400)
            os.chmod(locked, 0o500)
            os.chmod(snapshot, 0o500)

            hloop.remove_validation_snapshot(snapshot)

            self.assertFalse(snapshot.exists())

    def test_remove_validation_snapshot_surfaces_retained_path_on_retry_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot"
            snapshot.mkdir()
            (snapshot / "artifact").write_text("x", encoding="utf-8")
            with mock.patch.object(
                hloop.shutil, "rmtree", side_effect=PermissionError("still locked")
            ):
                with self.assertRaisesRegex(
                    hloop.HLoopError, rf"retained path: {re.escape(str(snapshot))}"
                ):
                    hloop.remove_validation_snapshot(snapshot)
            self.assertTrue(snapshot.exists())
            shutil.rmtree(snapshot)

    def test_worker_seal_snapshot_cleanup_failure_prevents_success_and_reruns(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, _ = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-cleanup-failure", status="done"
                )
                head_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                real_remove = hloop.remove_validation_snapshot

                def remove_then_report_failure(snapshot):
                    real_remove(snapshot)
                    raise hloop.HLoopError(
                        f"failed to remove validation snapshot; retained path: {snapshot}"
                    )

                with mock.patch.object(
                    hloop, "remove_validation_snapshot", side_effect=remove_then_report_failure
                ):
                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                        with self.assertRaisesRegex(hloop.HLoopError, "retained path"):
                            hloop.cmd_worker_seal(
                                self.seal_args(repo, validation_command=["exit 0"])
                            )

                self.assertEqual(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=worktree,
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip(),
                    head_before,
                )
                transaction = state["tasks"]["T001"]["seal_transaction"]
                self.assertFalse(transaction["validation_passed"])

                # The next invocation safely reconciles that unvalidated
                # index and runs Manager validation again.
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=["exit 0"])
                        )
                self.assertEqual(code, 0)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_force_stages_result_artifact_when_ai_is_gitignored(self):
        """The namespaced result artifact must land in the seal commit even in
        a repository that gitignores `.ai`; product paths keep normal
        `git add -A` semantics."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                namespace = "worker-seal-gitignore-ai"
                hloop.configure_loop_namespace(namespace)
                repo = self.make_repo(root)
                (repo / ".gitignore").write_text(".ai/\n", encoding="utf-8")
                (repo / "keep.txt").write_text("base\n", encoding="utf-8")
                subprocess.run(["git", "add", ".gitignore", "keep.txt"], cwd=repo, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "seed with gitignore"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                base_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
                ).stdout.strip()
                worktree = root / "worker"
                subprocess.run(
                    ["git", "worktree", "add", "-b", "worker-seal-gitignore", str(worktree), "master"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                run_id = f"run-{namespace}"
                task_meta = {
                    "contract_schema_revision": 2,
                    "id": "T001",
                    "run_id": run_id,
                    "kind": "fix",
                    "status": "running",
                    "branch": "worker-seal-gitignore",
                    "base_ref": "master",
                    "base_sha": base_sha,
                    "write_allow": ["keep.txt"],
                    "write_deny": [],
                    "acceptance": ["handoff seals cleanly"],
                }
                hloop.write_text(
                    hloop.task_file(repo, "T001"), hloop.frontmatter(task_meta) + "\n\n# Task T001\n"
                )
                (worktree / "keep.txt").write_text("changed\n", encoding="utf-8")
                result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
                result_meta = {
                    "contract_schema_revision": 2,
                    "task_id": "T001",
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "attempt_id": "T001-A001",
                    "status": "partial",
                    "merge_ready": False,
                    "branch": "worker-seal-gitignore",
                    "head_sha": "HEAD",
                    "base_sha": base_sha,
                    "changed_files": ["keep.txt", result_rel.as_posix()],
                    "validation_recorded": False,
                    "validation_commands": [],
                    "validation_results": [],
                    "validation_summary": "",
                    "blocking_questions": [],
                    "handoff": True,
                }
                hloop.write_text(
                    worktree / result_rel, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n"
                )
                task_state = {
                    **task_meta,
                    "worktree": str(worktree),
                    "attempt_id": "T001-A001",
                    "active_attempt_id": "T001-A001",
                    "worker_base_sha": base_sha,
                    "skill_version": hloop.SKILL_VERSION,
                    "semantic_ack_barrier": {"status": "approved"},
                    "pane_closed_at": "2026-01-01T00:00:00+00:00",
                }
                state = {
                    "state_format_version": hloop.STATE_FORMAT_VERSION,
                    "schema_revision": hloop.STATE_SCHEMA_REVISION,
                    "namespace": hloop.LOOP_NAMESPACE,
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "phase": "running",
                    "integration_branch": "master",
                    "merge_mode": "squash",
                    "persistence": "local-only",
                    "tasks": {"T001": task_state},
                }

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertEqual(code, 0)
                committed_files = subprocess.run(
                    ["git", "show", "--stat", "--name-only", "-1"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout
                self.assertIn(result_rel.as_posix(), committed_files)
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_result_contract_rejects_missing_required_field_consistently(self):
        """A Worker result missing a schema-required field (`base_sha`) must be
        rejected identically by artifact readiness, seal precheck, and
        harvest -- the one shared validator, not three independent checks."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-contract-incomplete", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                del result_meta["base_sha"]
                hloop.write_text(
                    result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n"
                )

                ready, reason = hloop.artifact_readiness(
                    repo, state, "T001", "worker", state["tasks"]["T001"], result_path
                )
                self.assertFalse(ready)
                self.assertIn("base_sha", reason)

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "base_sha"):
                        hloop.cmd_worker_seal(self.seal_args(repo))

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "base_sha"):
                        hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo), task_id="T001", keep_pane=False, session_cleanup=None
                            )
                        )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_result_contract_rejects_missing_attempt_id_consistently(self):
        """An omitted artifact attempt_id must fail closed at readiness,
        seal precheck, and harvest through the shared validator."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-result-missing-attempt", status="partial"
                )
                result_path = worktree / result_rel
                result_meta = hloop.parse_frontmatter_text(hloop.read_text(result_path))
                del result_meta["attempt_id"]
                hloop.write_text(
                    result_path, hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n"
                )

                ready, reason = hloop.artifact_readiness(
                    repo, state, "T001", "worker", state["tasks"]["T001"], result_path
                )
                self.assertFalse(ready)
                self.assertIn("invalid attempt_id", reason)

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "invalid attempt_id"):
                        hloop.cmd_worker_seal(self.seal_args(repo))

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "invalid attempt_id"):
                        hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo), task_id="T001", keep_pane=False, session_cleanup=None
                            )
                        )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_result_contract_rejects_missing_active_attempt_in_readiness_and_harvest(self):
        """Manager state without an active expected attempt must never make
        artifact attempt validation optional."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-result-missing-active-attempt", status="partial"
                )
                task_state = state["tasks"]["T001"]
                task_state.pop("active_attempt_id", None)
                task_state.pop("attempt_id", None)
                result_path = worktree / result_rel

                ready, reason = hloop.artifact_readiness(
                    repo, state, "T001", "worker", task_state, result_path
                )
                self.assertFalse(ready)
                self.assertIn("invalid active attempt", reason)

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "invalid active attempt"):
                        hloop.cmd_worker_harvest(
                            argparse.Namespace(
                                repo=str(repo), task_id="T001", keep_pane=False, session_cleanup=None
                            )
                        )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_seal_required_action_text_includes_validation_command_placeholder(self):
        text = hloop.seal_required_action_text("T001")
        self.assertTrue(text.startswith("hloop worker seal T001"))
        self.assertIn("--validation-command", text)
        self.assertIn("<manager-approved-command>", text)

    def test_frontmatter_round_trips_string_values_equal_to_true_or_false(self):
        """A validation command literally named `true`/`false` must round-trip
        as the exact string, not be silently reinterpreted as a Python bool --
        the shared Worker result contract validator strictly requires
        `validation_commands` items to be `str`."""

        text = hloop.frontmatter(
            {
                "validation_commands": ["false", "true"],
                "validation_summary": "false",
            }
        )
        meta = hloop.parse_frontmatter_text(text)
        self.assertEqual(meta["validation_commands"], ["false", "true"])
        self.assertTrue(all(type(item) is str for item in meta["validation_commands"]))
        self.assertEqual(meta["validation_summary"], "false")
        self.assertIs(type(meta["validation_summary"]), str)

    def test_worker_seal_pending_handoff_is_seal_required_not_terminal(self):
        """A valid uncommitted handoff must read as seal-required, never terminal-without-artifact."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-readiness", status="done"
                )
                result_path = worktree / result_rel
                ready, reason = hloop.artifact_readiness(
                    repo, state, "T001", "worker", state["tasks"]["T001"], result_path
                )
                self.assertFalse(ready)
                self.assertEqual(reason, "seal-required")

                status = hloop.agent_wait_status(repo, state, "T001")
                self.assertTrue(status["seal_required"])
                self.assertFalse(status["terminal_without_artifact"])
                self.assertFalse(status["artifact_ready"])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_wait_surfaces_seal_required_action(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-wait", status="done"
                )
                with mock.patch.object(hloop, "load_state", return_value=state):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        code = hloop.cmd_wait(
                            argparse.Namespace(
                                repo=str(repo),
                                target="T001",
                                timeout_ms=0,
                                poll_ms=250,
                                harvest=False,
                                quiet=True,
                            )
                        )
                self.assertEqual(code, 3)
                self.assertIn("seal required", out.getvalue())
                self.assertIn("hloop worker seal T001", out.getvalue())
                self.assertIn("--validation-command", out.getvalue())
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_on_branch_mismatch(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-branch", status="partial"
                )
                state["tasks"]["T001"]["branch"] = "some-other-branch"
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "Worker branch mismatch"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_without_pending_handoff(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-clean", status="partial"
                )
                subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "worker committed everything already"],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                )
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "nothing to seal"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_without_pane_evidence(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-no-pane", status="partial"
                )
                del state["tasks"]["T001"]["pane_closed_at"]
                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with self.assertRaisesRegex(hloop.HLoopError, "no pane_id on record"):
                        hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_when_pane_is_manager_pane(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-manager-pane", status="partial"
                )
                del state["tasks"]["T001"]["pane_closed_at"]
                state["tasks"]["T001"]["pane_id"] = "pane-123"
                with mock.patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_PANE_ID": "pane-123"}):
                    with mock.patch.object(hloop, "command_exists", return_value=True):
                        with mock.patch.object(hloop, "preflight_loop", return_value=state):
                            with self.assertRaisesRegex(
                                hloop.HLoopError, "is the current Manager pane"
                            ):
                                hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertNotEqual(hloop.porcelain_paths(worktree), [])
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_fails_closed_when_pane_close_fails(self):
        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, worktree, state, result_rel = self.make_worker_seal_fixture(
                    root, namespace="worker-seal-close-fails", status="partial"
                )
                del state["tasks"]["T001"]["pane_closed_at"]
                state["tasks"]["T001"]["pane_id"] = "pane-999"

                real_run_cmd = hloop.run_cmd

                def fake_run_cmd(cmd, *args, **kwargs):
                    if cmd[:3] == ["herdr", "pane", "close"]:
                        return SimpleNamespace(returncode=1, stdout="", stderr="pane busy")
                    return real_run_cmd(cmd, *args, **kwargs)

                with mock.patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_PANE_ID": "manager-pane"}):
                    with mock.patch.object(hloop, "command_exists", return_value=True):
                        with mock.patch.object(
                            hloop, "pane_info", return_value={"agent": "codex", "agent_status": "idle"}
                        ):
                            with mock.patch.object(hloop, "pane_text", return_value=""):
                                with mock.patch.object(hloop, "run_cmd", side_effect=fake_run_cmd):
                                    with mock.patch.object(hloop, "preflight_loop", return_value=state):
                                        with self.assertRaisesRegex(
                                            hloop.HLoopError, "failed to close Worker pane"
                                        ):
                                            hloop.cmd_worker_seal(self.seal_args(repo))
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_finalize_handoff_records_both_rename_sides_and_seal_succeeds(self):
        """A legitimate in-scope rename must seal end to end via finalize --handoff."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                hloop.configure_loop_namespace("worker-seal-rename-ok")
                repo = self.make_repo(root)
                # Loop artifacts are not part of the product; exclude them the
                # way a properly configured repository would so they never
                # show up as dirty product paths for scope checking.
                (repo / ".git" / "info" / "exclude").write_text(
                    ".ai/herdr-dev-loop/loops/worker-seal-rename-ok/STATE.json\n"
                    ".ai/herdr-dev-loop/loops/worker-seal-rename-ok/tasks/\n",
                    encoding="utf-8",
                )
                (repo / "old-name.txt").write_text("payload\n", encoding="utf-8")
                subprocess.run(["git", "add", "old-name.txt"], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
                base_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
                ).stdout.strip()
                worktree = root / "worker"
                subprocess.run(
                    ["git", "worktree", "add", "-b", "worker-seal-rename-ok", str(worktree), "master"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                run_id = "run-rename-ok"
                task_meta = {
                    "contract_schema_revision": 2,
                    "id": "T001",
                    "run_id": run_id,
                    "kind": "fix",
                    "status": "running",
                    "branch": "worker-seal-rename-ok",
                    "base_ref": "master",
                    "base_sha": base_sha,
                    "write_allow": ["old-name.txt", "new-name.txt"],
                    "write_deny": [],
                    "acceptance": ["rename handoff seals"],
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
                    "skill_version": hloop.SKILL_VERSION,
                    "semantic_ack_barrier": {"status": "approved"},
                }
                manager_state = {
                    "state_format_version": hloop.STATE_FORMAT_VERSION,
                    "schema_revision": hloop.STATE_SCHEMA_REVISION,
                    "namespace": hloop.LOOP_NAMESPACE,
                    "run_id": run_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "phase": "running",
                    "integration_branch": "master",
                    "merge_mode": "squash",
                    "persistence": "local-only",
                    "tasks": {"T001": task_state},
                }
                hloop.save_state(repo, manager_state)
                hloop.save_state(worktree, json.loads(json.dumps(manager_state)))

                # Simulate the workspace-write sandbox: rename via plain file
                # ops only, never Git.
                (worktree / "old-name.txt").rename(worktree / "new-name.txt")

                with contextlib.redirect_stdout(io.StringIO()):
                    finalize_code = hloop.cmd_worker_finalize(
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
                    )
                self.assertEqual(finalize_code, 0)

                result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
                finalized_meta = hloop.read_frontmatter(worktree / result_rel)
                self.assertTrue(hloop.normalize_bool(finalized_meta["handoff"]))
                self.assertIn("old-name.txt", finalized_meta["changed_files"])
                self.assertIn("new-name.txt", finalized_meta["changed_files"])
                # finalize --handoff must never touch Git.
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
                    ).returncode,
                    0,
                )

                seal_state = json.loads(json.dumps(manager_state))
                seal_state["tasks"]["T001"]["pane_closed_at"] = "2026-01-01T00:00:00+00:00"
                with mock.patch.object(hloop, "preflight_loop", return_value=seal_state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        seal_code = hloop.cmd_worker_seal(self.seal_args(repo))
                self.assertEqual(seal_code, 0)
                self.assertEqual(hloop.porcelain_paths(worktree), [])
                self.assertFalse((worktree / "old-name.txt").exists())
                self.assertEqual((worktree / "new-name.txt").read_text(encoding="utf-8"), "payload\n")
                sealed_meta = hloop.read_frontmatter(worktree / result_rel)
                self.assertIn("old-name.txt", sealed_meta["changed_files"])
                self.assertIn("new-name.txt", sealed_meta["changed_files"])
        finally:
            hloop.configure_loop_namespace(previous)

    def make_cherry_pick_recovery_fixture(self, root: Path):
        repo = self.make_repo(root)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        subprocess.run(["git", "switch", "-c", "worker"], cwd=repo, check=True, capture_output=True)
        (repo / "prefix.txt").write_text("prefix\n", encoding="utf-8")
        subprocess.run(["git", "add", "prefix.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "prefix"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("worker\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "conflict"], cwd=repo, check=True, capture_output=True)
        (repo / "tail.txt").write_text("tail\n", encoding="utf-8")
        subprocess.run(["git", "add", "tail.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "tail"], cwd=repo, check=True, capture_output=True)
        worker_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        source_commits = tuple(
            subprocess.run(
                ["git", "rev-list", "--reverse", f"{base}..{worker_head}"],
                cwd=repo,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.splitlines()
        )
        subprocess.run(["git", "switch", "master"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("manager\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "manager"], cwd=repo, check=True, capture_output=True)
        start_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        transaction = hloop.build_merge_transaction(
            task_id="T001",
            attempt_id="T001-A001",
            branch="worker",
            pre_merge_head=start_head,
            worker_head=worker_head,
            index_state=hloop.merge_transaction_index_state(repo, "HEAD"),
            changed_paths=("README.md", "prefix.txt", "tail.txt"),
            mode="cherry-pick",
            source_commits=source_commits,
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "blocked_conflict",
            "integration_branch": "master",
            "merge_mode": "cherry-pick",
            "manager_qa_profile": "none",
            "tasks": {
                "T001": {
                    "status": "blocked_merge_conflict",
                    "branch": "worker",
                    "write_allow": ["README.md", "prefix.txt", "tail.txt"],
                    "write_deny": [],
                }
            },
            "active_merge": hloop.build_active_merge_record(
                transaction,
                worker_base_sha=base,
            ),
        }
        hloop.save_state(repo, state)
        _, cherry_pick_env = hloop.prepare_cherry_pick_evidence(
            repo,
            state,
            transaction,
            resolved_tree=None,
        )
        cherry_pick_env["GIT_EDITOR"] = "true"
        cherry_pick = subprocess.run(
            ["git", *hloop.CHERRY_PICK_GIT_CONFIG, "cherry-pick", *source_commits],
            cwd=repo,
            text=True,
            capture_output=True,
            env=cherry_pick_env,
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
        return repo, state, start_head, source_commits, observed

    def test_multi_commit_cherry_pick_continue_and_abort_accept_applied_prefix(self):
        previous = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-cherry-pick-recovery")
        try:
            with tempfile.TemporaryDirectory() as directory:
                repo, state, _, source_commits, observed = self.make_cherry_pick_recovery_fixture(
                    Path(directory)
                )
                self.assertEqual(observed.applied_commits, source_commits[:1])
                legacy_active = dict(state["active_merge"])
                for field in ("source_commits", "applied_commits", "applied_head"):
                    legacy_active.pop(field, None)
                for field in (
                    "cherry_pick_transaction_version",
                    "cherry_pick_evidence_policy",
                    "cherry_pick_evidence_version",
                    "cherry_pick_evidence_legacy_prefix_count",
                    "cherry_pick_evidence",
                ):
                    legacy_active.pop(field)
                legacy_observed = hloop.preflight_merge_transaction(
                    repo, legacy_active, "continue"
                )
                self.assertEqual(legacy_observed.source_commits, source_commits)
                (repo / "README.md").write_text("resolved\n", encoding="utf-8")
                subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)

                self.assertEqual(hloop.cmd_merge_continue(repo, state, "T001"), 0)

                self.assertEqual(state["tasks"]["T001"]["status"], "merged")
                self.assertEqual((repo / "prefix.txt").read_text(encoding="utf-8"), "prefix\n")
                self.assertEqual((repo / "tail.txt").read_text(encoding="utf-8"), "tail\n")
                self.assertNotIn("active_merge", state)

            with tempfile.TemporaryDirectory() as directory:
                repo, state, start_head, _, _ = self.make_cherry_pick_recovery_fixture(
                    Path(directory)
                )
                active = state["active_merge"]
                self.assertEqual(
                    hloop.preflight_merge_transaction(repo, active, "retry").pre_merge_head,
                    start_head,
                )
                self.assertEqual(hloop.cmd_merge_abort(repo, state, "T001"), 0)
                restored = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                self.assertEqual(restored, start_head)
                self.assertEqual(state["tasks"]["T001"]["status"], "result_reported")
                self.assertFalse((hloop.merge_git_dir(repo) / "sequencer").exists())
                status_lines = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=repo,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.splitlines()
                self.assertEqual(
                    [line for line in status_lines if not line.endswith(" .ai/")],
                    [],
                )
        finally:
            hloop.configure_loop_namespace(previous)

    def test_cherry_pick_recovery_rejects_sequencer_tampering(self):
        previous = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-cherry-pick-tamper")
        try:
            with tempfile.TemporaryDirectory() as directory:
                repo, state, _, _, _ = self.make_cherry_pick_recovery_fixture(Path(directory))
                todo_path = hloop.merge_git_dir(repo) / "sequencer" / "todo"
                todo_lines = todo_path.read_text(encoding="utf-8").splitlines()
                self.assertGreaterEqual(len(todo_lines), 2)
                todo_path.write_text("\n".join(reversed(todo_lines)) + "\n", encoding="utf-8")

                with self.assertRaisesRegex(hloop.HLoopError, "expected source suffix"):
                    hloop.preflight_merge_transaction(repo, state["active_merge"], "continue")
        finally:
            hloop.configure_loop_namespace(previous)

    def test_message_envelopes_are_bound_to_each_role_without_undefined_names(self):
        state = {
            "run_id": "run-1",
            "advisor_max_rounds": 2,
            "tasks": {"T001": {"status": "running", "pane_id": "p1", "attempt_id": "T001-A001"}},
            "reviews": {"R001": {"status": "running", "gate_status": "running", "pane_id": "p2", "attempt_id": "R001-A001"}},
            "gaps": {"G001": {"status": "running", "gate_status": "running", "pane_id": "p3", "attempt_id": "G001-A001"}},
            "advice": {
                "A001": {
                    "mode": "single",
                    "participants": [
                        {"participant_id": "P1", "status": "running", "gate_status": "running", "pane_id": "p4", "attempt_id": "A001-P1-A001"}
                    ],
                }
            },
        }
        sent = []

        def capture_send(provider, pane_id, message, *unused):
            sent.append((pane_id, message))

        common = {
            "repo": ".",
            "message": "please continue",
            "file": None,
            "timeout_ms": 1,
            "input_settle_ms": 0,
            "submit_verify_ms": 1,
            "submit_attempts": 1,
        }
        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(hloop, "send_agent_tui_message", side_effect=capture_send), mock.patch.object(
            hloop, "save_state"
        ), mock.patch.object(hloop, "journal"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hloop.cmd_worker_message(argparse.Namespace(**common, task_id="T001")), 0)
            self.assertEqual(hloop.cmd_reviewer_message(argparse.Namespace(**common, review_id="R001")), 0)
            self.assertEqual(hloop.cmd_gap_message(argparse.Namespace(**common, gap_id="G001")), 0)
            self.assertEqual(
                hloop.cmd_advisor_message(
                    argparse.Namespace(**common, advice_id="A001", participant_id="P1")
                ),
                0,
            )
        for expected, (_, message) in zip(("T001", "R001", "G001", "A001/P1"), sent):
            identity = hloop.manager_message_transport_identity(message)
            self.assertIsNotNone(identity)
            self.assertEqual(identity["role_id"], expected)
            self.assertIn(f" role={expected} attempt=", message)

        with mock.patch.object(hloop, "check_herdr_env"), mock.patch.object(
            hloop,
            "wait_agent_tui_ready",
            return_value=({"agent": "codex", "agent_status": "idle", "session_id": "session-1"}, ""),
        ), mock.patch.object(hloop, "run_cmd"), mock.patch.object(
            hloop, "wait_manager_message_visible", return_value="typed"
        ), mock.patch.object(
            hloop,
            "pane_info",
            return_value={"agent": "codex", "agent_status": "idle", "session_id": "session-1"},
        ), mock.patch.object(
            hloop, "pane_text", return_value="input> already enveloped"
        ), mock.patch.object(hloop, "manager_message_submitted", return_value=True):
            hloop.send_agent_tui_message("codex", "p1", "already enveloped", 1, 0, 1, 1)

    def make_worker_seal_ignored_result_only_fixture(self, root: Path, *, namespace: str):
        """Build a worktree where the loop namespace is git-excluded, the
        product change is already committed by the Worker, and only
        `result.md` is pending -- exactly what is left behind when a
        workspace-write Worker self-commits its product change, its own
        result commit fails, and it falls back to `worker finalize
        --handoff`. `git status` never reports an ignored path even when it
        genuinely differs from HEAD, so this reproduces the scenario where a
        naive dirty-worktree check misreports `nothing to seal`.
        """

        hloop.configure_loop_namespace(namespace)
        repo = self.make_repo(root)
        (repo / ".gitignore").write_text(".ai/\n", encoding="utf-8")
        (repo / "keep.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "keep.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "seed files"], cwd=repo, check=True, capture_output=True)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        worktree = root / "worker"
        subprocess.run(
            ["git", "worktree", "add", "-b", "worker-seal-ignored", str(worktree), "master"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        run_id = f"run-{namespace}"
        task_meta = {
            "contract_schema_revision": 2,
            "id": "T001",
            "run_id": run_id,
            "kind": "fix",
            "status": "running",
            "branch": "worker-seal-ignored",
            "base_ref": "master",
            "base_sha": base_sha,
            "write_allow": ["keep.txt"],
            "write_deny": [],
            "acceptance": ["handoff seals cleanly even when the namespace is gitignored"],
        }
        hloop.write_text(hloop.task_file(repo, "T001"), hloop.frontmatter(task_meta) + "\n\n# Task T001\n")

        # Simulate the result-only handoff shape: the product change is
        # already self-committed by the Worker, the result commit failed,
        # and `worker finalize --handoff` wrote a fresh (uncommitted)
        # result.md.
        (worktree / "keep.txt").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "add", "keep.txt"], cwd=worktree, check=True)
        subprocess.run(
            ["git", "commit", "-m", "worker product change"], cwd=worktree, check=True, capture_output=True
        )
        expected_parent = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
        ).stdout.strip()

        # An arbitrary, unrelated ignored path under the same gitignored
        # namespace -- e.g. Worker scratch state. It must never be swept
        # into the seal commit; only the exact active-attempt result.md path
        # may ever be force-added.
        extra_ignored_rel = hloop.LOOP_DIR / "scratch" / "not-the-result.txt"
        hloop.write_text(worktree / extra_ignored_rel, "unrelated scratch state\n")

        result_rel = hloop.LOOP_DIR / "results" / "T001" / "result.md"
        result_meta = {
            "contract_schema_revision": 2,
            "task_id": "T001",
            "run_id": run_id,
            "skill_version": hloop.SKILL_VERSION,
            "attempt_id": "T001-A001",
            "status": "done",
            "merge_ready": False,
            "branch": "worker-seal-ignored",
            "head_sha": "HEAD",
            "base_sha": base_sha,
            "changed_files": ["keep.txt", result_rel.as_posix()],
            "validation_recorded": False,
            "validation_commands": [],
            "validation_results": [],
            "validation_summary": "",
            "blocking_questions": [],
            "handoff": True,
        }
        hloop.write_text(
            worktree / result_rel,
            hloop.frontmatter(result_meta) + "\n\n# Worker Result T001\n",
        )
        task_state = {
            **task_meta,
            "worktree": str(worktree),
            "attempt_id": "T001-A001",
            "active_attempt_id": "T001-A001",
            "worker_base_sha": base_sha,
            "skill_version": hloop.SKILL_VERSION,
            "semantic_ack_barrier": {"status": "approved"},
            "pane_closed_at": "2026-01-01T00:00:00+00:00",
        }
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "run_id": run_id,
            "skill_version": hloop.SKILL_VERSION,
            "phase": "running",
            "integration_branch": "master",
            "merge_mode": "squash",
            "persistence": "local-only",
            "tasks": {"T001": task_state},
        }
        return repo, worktree, state, result_rel, extra_ignored_rel, expected_parent

    def test_worker_seal_detects_and_commits_ignored_result_only_handoff(self):
        """A result-only handoff (product already committed, only
        `result.md` pending) must be detected and sealed even when the loop
        namespace is gitignored in the target repo, instead of being
        misreported as `nothing to seal`."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (
                    repo,
                    worktree,
                    state,
                    result_rel,
                    extra_ignored_rel,
                    expected_parent,
                ) = self.make_worker_seal_ignored_result_only_fixture(
                    root, namespace="worker-seal-ignored-result-only"
                )
                # Confirm the fixture actually reproduces the reported bug:
                # `git status` must not see the pending result artifact at
                # all once the loop namespace is gitignored.
                self.assertEqual(hloop.porcelain_paths_no_renames(worktree), [])

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=["exit 0"], validation_summary="ok")
                        )
                self.assertEqual(code, 0)

                head_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True
                ).stdout.strip()
                self.assertNotEqual(head_sha, expected_parent)
                head_message = subprocess.run(
                    ["git", "log", "-1", "--format=%s"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
                self.assertIn("seal worker handoff", head_message)
                self.assertIn("T001-A001", head_message)

                committed_result = subprocess.run(
                    ["git", "show", f"HEAD:{result_rel.as_posix()}"],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout
                self.assertIn("merge_ready: true", committed_result)

                # The unrelated ignored path must never have been swept into
                # the seal commit -- only the exact active-attempt result.md
                # path may ever be force-added.
                extra_in_head = subprocess.run(
                    ["git", "cat-file", "-e", f"HEAD:{extra_ignored_rel.as_posix()}"],
                    cwd=worktree,
                )
                self.assertNotEqual(extra_in_head.returncode, 0)
                self.assertTrue((worktree / extra_ignored_rel).exists())
        finally:
            hloop.configure_loop_namespace(previous)

    def test_worker_seal_does_not_force_add_arbitrary_ignored_paths(self):
        """Seal's force-add must stay scoped to the single active
        task/attempt result artifact path -- an arbitrary ignored file must
        never be picked up even though it sits under the same gitignored
        namespace and passes a completely clean `git status`."""

        previous = hloop.LOOP_NAMESPACE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (
                    repo,
                    worktree,
                    state,
                    result_rel,
                    extra_ignored_rel,
                    expected_parent,
                ) = self.make_worker_seal_ignored_result_only_fixture(
                    root, namespace="worker-seal-ignored-scope"
                )

                with mock.patch.object(hloop, "preflight_loop", return_value=state):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = hloop.cmd_worker_seal(
                            self.seal_args(repo, validation_command=["exit 0"], validation_summary="ok")
                        )
                self.assertEqual(code, 0)

                cached_after = subprocess.run(
                    ["git", "log", "-1", "--name-only", "--format="],
                    cwd=worktree,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.splitlines()
                self.assertIn(result_rel.as_posix(), cached_after)
                self.assertNotIn(extra_ignored_rel.as_posix(), cached_after)
                self.assertTrue((worktree / extra_ignored_rel).exists())
                self.assertEqual(
                    (worktree / extra_ignored_rel).read_text(encoding="utf-8"),
                    "unrelated scratch state\n",
                )
        finally:
            hloop.configure_loop_namespace(previous)


if __name__ == "__main__":
    unittest.main()
