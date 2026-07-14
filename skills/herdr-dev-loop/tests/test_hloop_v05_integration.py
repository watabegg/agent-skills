import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
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
                self.assertEqual((state["state_format_version"], state["schema_revision"]), (3, 1))
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
                state_path.write_text(json.dumps(state), encoding="utf-8")

                prefix = ["--repo", str(repo), "--namespace", "migration", "migrate"]
                code, output, error = self.run_cli([*prefix, "--dry-run"])
                self.assertEqual((code, error), (0, ""), output)
                plan = json.loads(output)
                self.assertEqual((plan["to_format"], plan["to_revision"]), (3, 1))
                self.assertEqual(
                    plan["applied_steps"],
                    ["format-2-to-3", "format-3-revision-1"],
                )
                code, output, error = self.run_cli([*prefix, "--apply"])
                self.assertEqual((code, error), (0, ""), output)
                migrated = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    (migrated["state_format_version"], migrated["schema_revision"]),
                    (3, 1),
                )
                self.assertIn("artifact_policy", migrated)
                self.assertEqual(len(list((state_path.parent / "migration").glob("STATE.v2.r0.*.json"))), 1)

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
                state_path.write_text(json.dumps(state), encoding="utf-8")

                prefix = ["--repo", str(repo), "--namespace", "revision-zero", "migrate"]
                code, output, error = self.run_cli([*prefix, "--dry-run"])
                self.assertEqual((code, error), (0, ""), output)
                plan = json.loads(output)
                self.assertEqual((plan["from_format"], plan["from_revision"]), (3, 0))
                self.assertEqual(plan["applied_steps"], ["format-3-revision-1"])

                code, output, error = self.run_cli([*prefix, "--apply"])
                self.assertEqual((code, error), (0, ""), output)
                migrated = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    (migrated["state_format_version"], migrated["schema_revision"]),
                    (3, 1),
                )
                self.assertEqual(
                    len(list((state_path.parent / "migration").glob("STATE.v3.r0.*.json"))),
                    1,
                )

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
                state["schema_revision"] = 2
                state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
                journal_path = state_path.parent / "JOURNAL.md"
                prefix = ["--repo", str(repo), "--namespace", "future-state"]

                code, output, error = self.run_cli([*prefix, "status", "--raw-state"])
                self.assertEqual((code, error), (0, ""), output)
                self.assertEqual(json.loads(output)["schema_revision"], 2)
                before_migrate = (state_path.read_bytes(), journal_path.read_bytes())
                code, _, error = self.run_cli([*prefix, "migrate", "--dry-run"])
                self.assertEqual(code, 2)
                self.assertIn(
                    "state format-3.revision-2 is newer than runtime format-3.revision-1",
                    error,
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
                                "state format-3.revision-2 is newer than runtime "
                                "format-3.revision-1",
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
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["batches"] = {
                    "B001": {"id": "B001", "status": "closed", "task_ids": []}
                }
                state["tasks"] = {"T000": {"status": "merged", "batch_id": "B001"}}
                state_path.write_text(json.dumps(state), encoding="utf-8")
                prefix = ["--repo", str(repo), "--namespace", "final-gate"]
                code, output, error = self.run_cli([*prefix, "final-gates", "arm"])
                self.assertEqual((code, error), (0, ""), output)
                armed = json.loads(state_path.read_text(encoding="utf-8"))["final_gate"]
                self.assertEqual(armed["status"], "armed")

                code, output, error = self.run_cli(
                    [*prefix, "task", "new", "follow-up", "--write-allow", "src/**"]
                )
                self.assertEqual((code, error), (0, ""), output)
                disarmed = json.loads(state_path.read_text(encoding="utf-8"))["final_gate"]
                self.assertEqual(disarmed["status"], "disarmed")
                self.assertIn("new task created", disarmed["disarm_reason"])

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
            self.assertIn(f":{expected}:", message)

        with mock.patch.object(hloop, "check_herdr_env"), mock.patch.object(
            hloop, "wait_agent_tui_ready", return_value=({}, "")
        ), mock.patch.object(hloop, "run_cmd"), mock.patch.object(
            hloop, "wait_manager_message_visible", return_value="typed"
        ), mock.patch.object(hloop, "manager_message_submitted", return_value=True):
            hloop.send_agent_tui_message("codex", "p1", "already enveloped", 1, 0, 1, 1)


if __name__ == "__main__":
    unittest.main()
