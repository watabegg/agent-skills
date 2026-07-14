import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_v05_runtime_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)
hloop_broker = hloop.hloop_broker


class ProviderPreflightIntegrationTests(unittest.TestCase):
    """Exercises the hloop_lib.providers wiring inside preflight_loop."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-provider-preflight")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        return repo

    def write_fake_provider(self, bin_dir: Path, name: str, help_text: str) -> None:
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--help\" ] || [ \"$2\" = \"--help\" ]; then\n"
            f"  printf '%s' {self._quote(help_text)}\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _quote(text: str) -> str:
        return "'" + text.replace("'", "'\\''") + "'"

    def prepended_path_env(self, bin_dir: Path):
        return mock.patch.dict(
            os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        )

    def test_preflight_records_provider_capability_when_flags_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.write_fake_provider(
                bin_dir,
                "codex",
                "usage: codex --sandbox <mode> --ask-for-approval <mode> --no-alt-screen",
            )

            with self.prepended_path_env(bin_dir):
                state = hloop.preflight_loop(
                    repo, require_agent_provider="codex", require_integration_branch=True
                )

            preflight = state.get("last_provider_preflight")
            self.assertIsNotNone(preflight)
            self.assertEqual(preflight["provider"], "codex")
            self.assertEqual(preflight["capability"], "supported")
            self.assertTrue(preflight["launch_allowed"])

    def test_preflight_fails_when_provider_lacks_required_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.write_fake_provider(bin_dir, "codex", "usage: codex [subcommand]")

            with self.prepended_path_env(bin_dir):
                with self.assertRaisesRegex(hloop.HLoopError, "lacks required flags"):
                    hloop.preflight_loop(
                        repo,
                        require_agent_provider="codex",
                        require_integration_branch=True,
                    )

            blocked = hloop.load_state(repo)
            self.assertEqual(blocked["phase"], "blocked_environment")
            preflight = blocked.get("last_provider_preflight")
            self.assertIsNotNone(preflight)
            self.assertEqual(preflight["capability"], "unsupported")
            self.assertFalse(preflight["launch_allowed"])
            self.assertEqual(
                blocked["last_preflight_error"]["reason"], preflight["reason"]
            )


class BrokerReportLifecycleTests(unittest.TestCase):
    """Exercises agent report / inbox / manager sleep-next / broker status-recover."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-broker-report-lifecycle")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "run_id": "run-001",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        return repo

    def report_args(self, repo: Path, **overrides) -> SimpleNamespace:
        base = dict(
            repo=str(repo),
            run_id=None,
            role_id="T001",
            attempt_id=None,
            event_id=None,
            task_contract_digest=None,
            type="milestone",
            stage="implementing",
            summary="made progress",
            next="continue implementation",
            evidence_ref=["skills/herdr-dev-loop/scripts/hloop:1"],
            understood_goal=None,
            scope=None,
            acceptance=None,
            approach=None,
            risk=["none identified"],
            impact=None,
            attempted=None,
            option_text=None,
            recommendation=None,
            blocked_scope=None,
            artifact=None,
            head_sha=None,
            validation_result_ref=None,
            residual_risk=None,
            handoff=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_report_inbox_manager_sleep_next_and_ack_round_trip(self):
        event_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            hloop.cmd_manager_sleep(
                SimpleNamespace(
                    repo=str(repo), ttl_seconds=3600, manager_session_id="sess", pane_id="pane"
                )
            )
            hloop.cmd_agent_report(self.report_args(repo, event_id=event_id))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_next(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

            hloop.cmd_inbox_ack(SimpleNamespace(repo=str(repo), event_id=event_id))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_next(SimpleNamespace(repo=str(repo)))
            self.assertIn("no pending wakes", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_broker_status(SimpleNamespace(repo=str(repo)))
            self.assertIn('"events": 1', buffer.getvalue())

    def test_report_spools_when_broker_is_unavailable(self):
        event_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            with mock.patch.object(
                hloop,
                "_open_broker_store",
                side_effect=hloop_broker.BrokerStorageError("simulated broker outage"),
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    hloop.cmd_agent_report(self.report_args(repo, event_id=event_id))
                self.assertIn(f"spooled {event_id}", buffer.getvalue())

            spool_dir = hloop.broker_spool_dir(repo)
            spooled_files = list(spool_dir.glob("*"))
            self.assertTrue(spooled_files, "expected a spooled report file")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_broker_recover(SimpleNamespace(repo=str(repo)))
            self.assertIn("replayed 1 spooled report", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())


class RequirementProgressOutcomeTests(unittest.TestCase):
    """Exercises input record / requirement new / progress record / outcome show."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-requirement-progress-outcome")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        return repo

    def test_input_requirement_progress_outcome_round_trip_redacts_and_gates_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_input_record(
                    SimpleNamespace(
                        repo=str(repo),
                        source="manager-chat",
                        text="please add auth token=abcdefghijklmnop123456",
                    )
                )
            self.assertIn("captured U0001", buffer.getvalue())

            input_path = hloop.local_sensitive_input_dir(repo) / "U0001.json"
            stored = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertNotIn("abcdefghijklmnop123456", stored["raw_input"])
            self.assertIn("credential-assignment", stored["redactions"])

            # STATE.json (checked into the loop's checkpoint) must never retain
            # the raw/redacted prompt text itself, only a digest pointer.
            state_after_input = hloop.load_state(repo)
            self.assertNotIn("raw_input", json.dumps(state_after_input["inputs_index"]))

            # The local-sensitive inputs/** file itself must never be swept
            # into `hloop checkpoint`, even though it lives under LOOP_DIR.
            checkpoint_candidates = hloop.checkpoint_paths(
                repo, SimpleNamespace(path=None, force=True, include_lock=False, include_prompts=False)
            )
            self.assertFalse(any("inputs/" in path for path in checkpoint_candidates))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_requirement_new(
                    SimpleNamespace(
                        repo=str(repo),
                        id=None,
                        source_input=["U0001"],
                        acceptance=["auth token support is added"],
                        priority="P1",
                        depends_on=None,
                    )
                )
            self.assertIn("created REQ-001", buffer.getvalue())

            hloop.cmd_progress_record(
                SimpleNamespace(
                    repo=str(repo),
                    requirement_id="REQ-001",
                    status="in_progress",
                    task_id=["T001"],
                    evidence_kind=None,
                    evidence_ref=None,
                    verified_by=None,
                    head_sha=None,
                    result=None,
                    remaining_work="implement token handling",
                    blocker=None,
                )
            )

            # An agent-only assertion of "verified" without Manager/HLoop-checked
            # artifact+test evidence must be rejected (requirement evidence gate).
            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_progress_record(
                    SimpleNamespace(
                        repo=str(repo),
                        requirement_id="REQ-001",
                        status="verified",
                        task_id=None,
                        evidence_kind="agent-report",
                        evidence_ref="worker says done",
                        verified_by=None,
                        head_sha=None,
                        result=None,
                        remaining_work=None,
                        blocker=None,
                    )
                )

            hloop.cmd_progress_record(
                SimpleNamespace(
                    repo=str(repo),
                    requirement_id="REQ-001",
                    status="implemented_unverified",
                    task_id=None,
                    evidence_kind=None,
                    evidence_ref=None,
                    verified_by=None,
                    head_sha=None,
                    result=None,
                    remaining_work=None,
                    blocker=None,
                )
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_outcome_show(SimpleNamespace(repo=str(repo), requirement_id="REQ-001"))
            outcome = json.loads(buffer.getvalue())
            self.assertEqual(outcome["requirement"]["id"], "REQ-001")
            self.assertEqual(outcome["progress"]["status"], "implemented_unverified")


if __name__ == "__main__":
    unittest.main()
