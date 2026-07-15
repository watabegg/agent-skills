import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
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

    def test_preflight_probes_the_exact_role_invocation_not_a_generic_stand_in(self):
        """`require_agent_invocation` must probe the real launch argv.

        A role-specific invocation (explicit model/effort/exec runner) that
        the generic TUI/auto preflight probe would never construct must still
        be exactly what gets probed and recorded, so `last_provider_preflight`
        describes the actual launch, not a fixed stand-in.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.write_fake_provider(
                bin_dir,
                "codex",
                "usage: codex exec --sandbox <mode> -c <key=value> --output-last-message <path>",
            )
            output_path = root / "last.txt"
            invocation = hloop.hloop_providers.build_provider_invocation(
                provider="codex",
                runner="exec",
                sandbox="workspace-write",
                prompt_path=root / "prompt.md",
                model="auto",
                effort="high",
                output_path=output_path,
            )

            with self.prepended_path_env(bin_dir):
                state = hloop.preflight_loop(
                    repo,
                    require_agent_invocation=invocation,
                    require_integration_branch=True,
                )

            preflight = state.get("last_provider_preflight")
            self.assertIsNotNone(preflight)
            self.assertEqual(preflight["argv"], list(invocation.argv))
            self.assertEqual(preflight["runner"], "exec")
            self.assertEqual(preflight["effort"], "high")
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

    def test_preflight_blocks_on_git_executable_identity_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            recorded = hloop.git_executable_identity()
            state = hloop.load_state(repo)
            state["git_identity"] = {**recorded, "sha256": "0" * 64}
            hloop.save_state(repo, state)

            with self.assertRaisesRegex(hloop.HLoopError, "git executable identity drift"):
                hloop.preflight_loop(repo, require_integration_branch=True)

            blocked = hloop.load_state(repo)
            self.assertEqual(blocked["phase"], "blocked_environment")
            self.assertIn(
                "git executable identity drift", blocked["last_preflight_error"]["reason"]
            )

    def test_trust_git_updates_recorded_identity_and_requires_idle_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["git_identity"] = {**hloop.git_executable_identity(), "sha256": "0" * 64}
            state["tasks"] = {"T001": {"status": "running"}}
            hloop.save_state(repo, state)

            with self.assertRaisesRegex(hloop.HLoopError, "role is running"):
                hloop.cmd_runtime_trust_git(
                    SimpleNamespace(repo=str(repo), reason="package upgrade")
                )

            state["tasks"]["T001"]["status"] = "merged"
            hloop.save_state(repo, state)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    hloop.cmd_runtime_trust_git(
                        SimpleNamespace(repo=str(repo), reason="package upgrade")
                    ),
                    0,
                )
            trusted = hloop.load_state(repo)
            self.assertEqual(
                trusted["git_identity"]["sha256"], hloop.git_executable_identity()["sha256"]
            )
            hloop.preflight_loop(repo, require_integration_branch=True)

    def test_main_never_executes_a_malicious_path_git_before_identity_check(self):
        """A PATH-substituted `git` must be refused, and never executed.

        `main()` resolves/verifies the trusted git identity before
        `repo_root()`/`loop_lock()` (or any other code path) executes a PATH
        `git`. Fingerprinting is filesystem-only (path/device/inode/hash), so
        the substituted binary should never run even once.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            recorded = hloop.git_executable_identity()
            state = hloop.load_state(repo)
            state["git_identity"] = recorded
            hloop.save_state(repo, state)

            marker = root / "malicious-git-ran.marker"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            malicious_git = bin_dir / "git"
            malicious_git.write_text(
                "#!/bin/sh\n"
                f"echo ran >> {marker}\n"
                "exit 0\n",
                encoding="utf-8",
            )
            malicious_git.chmod(
                malicious_git.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
            )

            previous_trusted = hloop.TRUSTED_GIT_PATH
            try:
                with self.prepended_path_env(bin_dir), contextlib.redirect_stderr(io.StringIO()):
                    code = hloop.main(
                        [
                            "--repo",
                            str(repo),
                            "--namespace",
                            hloop.LOOP_NAMESPACE,
                            "status",
                        ]
                    )
            finally:
                hloop.TRUSTED_GIT_PATH = previous_trusted

            self.assertEqual(code, 2)
            self.assertFalse(marker.exists(), "malicious PATH git must never execute")

            unchanged = hloop.load_state(repo)
            self.assertEqual(unchanged, state)


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
        store = hloop._open_broker_store(repo)
        with store.transaction() as transaction:
            store.register_active_role(
                transaction,
                run_id="run-001",
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
                token="test-report-token",
            )
        return repo

    def report_args(self, repo: Path, **overrides) -> SimpleNamespace:
        base = dict(
            repo=str(repo),
            run_id="run-001",
            role_id="T001",
            attempt_id="T001-A001",
            event_id=None,
            task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
            report_token="test-report-token",
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
            errors = []
            runtime_dir = root / "runtime"
            socket_path = hloop_broker.derive_runtime_socket_path(
                hloop.LOOP_NAMESPACE, "run-001", runtime_directory=runtime_dir
            )

            def report_after_sleep_starts():
                try:
                    deadline = time.monotonic() + 2
                    while not socket_path.exists():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("manager sleep socket was not created")
                        time.sleep(0.005)
                    hloop.cmd_agent_report(
                        self.report_args(
                            repo,
                            event_id=event_id,
                            type="attention",
                            impact="Manager must inspect the fault-path report",
                            attempted=["persisted the report before signalling"],
                            option_text=["inspect the durable inbox event"],
                            recommendation="inspect and explicitly acknowledge the event",
                            blocked_scope=["manager sleep"],
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            reporter = threading.Thread(target=report_after_sleep_starts, daemon=True)
            reporter.start()
            sleep_output = io.StringIO()
            with mock.patch.dict(os.environ, {"HLOOP_RUNTIME_DIR": str(runtime_dir)}):
                with contextlib.redirect_stdout(sleep_output):
                    hloop.cmd_manager_sleep(
                        SimpleNamespace(
                            repo=str(repo), ttl_seconds=2, manager_session_id="sess", pane_id="pane"
                        )
                    )
            reporter.join(timeout=2)
            self.assertEqual(errors, [])
            self.assertIn("manager sleep returned: report", sleep_output.getvalue())
            self.assertIn(event_id, sleep_output.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_next(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

            hloop.cmd_inbox_ack(SimpleNamespace(repo=str(repo), event_id=event_id))
            with self.assertRaisesRegex(hloop.HLoopError, "duplicate"):
                hloop.cmd_inbox_ack(
                    SimpleNamespace(repo=str(repo), event_id=event_id)
                )

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


class BrokerTransportAndAuthenticationTests(unittest.TestCase):
    """Exercises detached-worktree broker sharing, inbox show, report auth, hooks, drain."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-broker-transport-and-auth")

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
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "run_id": "run-001",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        store = hloop._open_broker_store(repo)
        with store.transaction() as transaction:
            store.register_active_role(
                transaction,
                run_id="run-001",
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
                token="test-report-token",
            )
        return repo

    def report_args(self, repo: Path, **overrides) -> SimpleNamespace:
        base = dict(
            repo=str(repo),
            run_id="run-001",
            role_id="T001",
            attempt_id="T001-A001",
            event_id=None,
            task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
            report_token="test-report-token",
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

    def test_broker_transport_is_shared_across_worktrees_without_copying_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            worktree = root / "worker-worktree"
            subprocess.run(
                ["git", "worktree", "add", "-b", "T001-A001", str(worktree), "main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(hloop.broker_root(repo), hloop.broker_root(worktree))
            self.assertFalse(
                str(hloop.broker_root(worktree)).startswith(str(hloop.loop_path(worktree))),
                "broker storage must live outside the per-worktree loop snapshot",
            )

            event_id = str(uuid.uuid4())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(
                    self.report_args(worktree, role_id="T001", attempt_id="T001-A001", event_id=event_id)
                )
            self.assertIn(f"accepted {event_id}", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue(), "Manager's own repo path did not see the Worker's report")

    def test_inbox_show_matches_the_fixed_wake_message_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            event_id = str(uuid.uuid4())
            hloop.cmd_agent_report(self.report_args(repo, event_id=event_id))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_show(SimpleNamespace(repo=str(repo), event_id=event_id))
            shown = json.loads(buffer.getvalue())
            self.assertEqual(shown["event_id"], event_id)
            self.assertEqual(shown["role_id"], "T001")

            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_inbox_show(SimpleNamespace(repo=str(repo), event_id=str(uuid.uuid4())))

    def test_report_token_authentication_accepts_matching_and_rejects_mismatched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            store = hloop._open_broker_store(repo)
            with store.transaction() as txn:
                store.register_active_role(
                    txn,
                    run_id="run-001",
                    role_id="T001",
                    attempt_id="T001-A001",
                    task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                    token="s3cr3t-token",
                )

            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        attempt_id="T001-A001",
                        task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                        report_token="wrong-token",
                    )
                )

            event_id = str(uuid.uuid4())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        event_id=event_id,
                        attempt_id="T001-A001",
                        task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                        report_token="s3cr3t-token",
                    )
                )
            self.assertIn(f"accepted {event_id}", buffer.getvalue())

            with store.transaction() as txn:
                store.revoke_active_role(txn, run_id="run-001", role_id="T001")
            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        attempt_id="T001-A001",
                        task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                        report_token="s3cr3t-token",
                    )
                )

    def test_manager_sleep_surfaces_already_pending_report_instead_of_losing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            event_id = str(uuid.uuid4())
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    event_id=event_id,
                    type="attention",
                    impact="Manager must inspect the pending report",
                    attempted=["persisted the report"],
                    option_text=["inspect the durable inbox event"],
                    recommendation="inspect and explicitly acknowledge the event",
                    blocked_scope=["manager sleep"],
                )
            )

            buffer = io.StringIO()
            with mock.patch.dict(
                os.environ, {"HLOOP_RUNTIME_DIR": str(root / "runtime")}
            ), contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_sleep(
                    SimpleNamespace(
                        repo=str(repo), ttl_seconds=3600, manager_session_id="sess", pane_id="pane"
                    )
                )
            output = buffer.getvalue()
            self.assertIn("manager sleep returned: report", output)
            self.assertIn(event_id, output)

    def test_hooks_install_status_uninstall_round_trip_preserves_user_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            settings_path = repo / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-hook"}]}]}}),
                encoding="utf-8",
            )

            hloop.cmd_hooks_install(
                SimpleNamespace(
                    repo=str(repo),
                    provider="claude",
                    settings_path=None,
                    codex_continuation_capability="unknown",
                )
            )
            installed = json.loads(settings_path.read_text(encoding="utf-8"))
            stop_handlers = [h for group in installed["hooks"]["Stop"] for h in group["hooks"]]
            self.assertEqual(len(stop_handlers), 2, "existing user hook must be preserved")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_hooks_status(
                    SimpleNamespace(repo=str(repo), provider="claude", settings_path=None)
                )
            status = json.loads(buffer.getvalue())
            self.assertEqual(status["owned_stop_hooks"], 1)

            hloop.cmd_hooks_uninstall(
                SimpleNamespace(repo=str(repo), provider="claude", settings_path=None)
            )
            after = json.loads(settings_path.read_text(encoding="utf-8"))
            remaining = [h for group in after["hooks"]["Stop"] for h in group["hooks"]]
            self.assertEqual(len(remaining), 1)
            self.assertIn("echo user-hook", remaining[0]["command"])

    def test_hooks_guard_returns_fixed_context_only_when_active_roles_lack_a_valid_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["tasks"] = {"T001": {"status": "running"}}
            hloop.save_state(repo, state)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_hooks_guard(
                    SimpleNamespace(repo=str(repo), provider="claude", hloop_hook_owner="x")
                )
            response = json.loads(buffer.getvalue())
            self.assertIn("additionalContext", response["hookSpecificOutput"])

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                store.register_wake_lease(
                    transaction,
                    run_id="run-001",
                    manager_session_id="sess",
                    pane_id="pane",
                    expires_at="2999-01-01T00:00:00+00:00",
                )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_hooks_guard(
                    SimpleNamespace(repo=str(repo), provider="claude", hloop_hook_owner="x")
                )
            self.assertEqual(json.loads(buffer.getvalue()), {})

    def test_message_drain_resends_pending_message_and_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "pane_id": "pane-1",
                "agent_provider": "codex",
                "pending_manager_messages": [],
            }
            pending_rel = hloop.write_pending_manager_message(
                repo,
                role="Worker",
                agent_id="T001",
                pane_id="pane-1",
                source="argv",
                message="please retry",
                error="simulated send failure",
            )
            task_state["pending_manager_messages"].append(
                {"at": hloop.now_iso(), "source": "argv", "pending_path": pending_rel.as_posix(), "error": "boom"}
            )
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)

            sent = []

            def capture_send(provider, pane_id, message, *unused):
                sent.append((pane_id, message))

            with mock.patch.object(hloop, "send_agent_tui_message", side_effect=capture_send), mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), contextlib.redirect_stdout(io.StringIO()) as buffer:
                hloop.cmd_message_drain(
                    SimpleNamespace(
                        repo=str(repo),
                        timeout_ms=1,
                        input_settle_ms=0,
                        submit_verify_ms=1,
                        submit_attempts=1,
                    )
                )
            self.assertIn("drained 1 pending message(s); 0 still pending", buffer.getvalue())
            self.assertEqual(len(sent), 1)
            self.assertIn("please retry", sent[0][1])
            self.assertEqual(task_state["pending_manager_messages"], [])
            self.assertFalse((repo / pending_rel).exists())

    def test_message_drain_only_retries_undelivered_and_never_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "pane_id": "pane-1",
                "agent_provider": "codex",
                "pending_manager_messages": [],
            }
            undelivered_rel = hloop.write_pending_manager_message(
                repo,
                role="Worker",
                agent_id="T001",
                pane_id="pane-1",
                source="argv",
                message="please retry me",
                error="simulated send failure",
            )
            unknown_rel = hloop.write_pending_manager_message(
                repo,
                role="Worker",
                agent_id="T001",
                pane_id="pane-1",
                source="argv",
                message="do not auto-resend me",
                error="simulated ambiguous delivery",
            )
            task_state["pending_manager_messages"] = [
                {
                    "at": hloop.now_iso(),
                    "source": "argv",
                    "pending_path": undelivered_rel.as_posix(),
                    "error": "boom",
                    "status": "undelivered",
                },
                {
                    "at": hloop.now_iso(),
                    "source": "argv",
                    "pending_path": unknown_rel.as_posix(),
                    "error": "ambiguous",
                    "status": "unknown",
                },
            ]
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)

            sent = []

            def capture_send(provider, pane_id, message, *unused):
                sent.append((pane_id, message))

            with mock.patch.object(hloop, "send_agent_tui_message", side_effect=capture_send), mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), contextlib.redirect_stdout(io.StringIO()) as buffer:
                hloop.cmd_message_drain(
                    SimpleNamespace(
                        repo=str(repo),
                        timeout_ms=1,
                        input_settle_ms=0,
                        submit_verify_ms=1,
                        submit_attempts=1,
                    )
                )
            self.assertIn("drained 1 pending message(s); 1 still pending", buffer.getvalue())
            self.assertEqual(len(sent), 1)
            self.assertIn("please retry me", sent[0][1])
            self.assertEqual(len(task_state["pending_manager_messages"]), 1)
            self.assertEqual(task_state["pending_manager_messages"][0]["status"], "unknown")
            self.assertFalse((repo / undelivered_rel).exists())
            self.assertTrue((repo / unknown_rel).exists())

    def test_message_drain_preserves_undelivered_entry_when_payload_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "pane_id": "pane-1",
                "agent_provider": "codex",
                "pending_manager_messages": [
                    {
                        "at": hloop.now_iso(),
                        "source": "argv",
                        "pending_path": ".ai/missing-message.md",
                        "error": "original delivery failed",
                        "status": "undelivered",
                    }
                ],
            }
            state["tasks"] = {"T001": task_state}

            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(hloop, "send_agent_tui_message") as send, contextlib.redirect_stdout(
                io.StringIO()
            ) as buffer:
                hloop.cmd_message_drain(
                    SimpleNamespace(
                        repo=str(repo),
                        timeout_ms=1,
                        input_settle_ms=0,
                        submit_verify_ms=1,
                        submit_attempts=1,
                    )
                )

            send.assert_not_called()
            self.assertIn("0 pending message(s); 1 still pending", buffer.getvalue())
            self.assertEqual(len(task_state["pending_manager_messages"]), 1)
            self.assertIn("file is missing", task_state["pending_manager_messages"][0]["error"])

    def test_supersede_marks_every_non_applied_message_terminal(self):
        agent_state = {
            "manager_messages": [
                {"message_id": "delivered", "delivery_status": "delivered"},
                {"message_id": "acked", "delivery_status": "acknowledged"},
                {"message_id": "applied", "delivery_status": "applied"},
            ]
        }

        hloop.supersede_pending_manager_messages(Path("/unused"), agent_state)

        statuses = {
            entry["message_id"]: entry["delivery_status"]
            for entry in agent_state["manager_messages"]
        }
        self.assertEqual(statuses["delivered"], "superseded")
        self.assertEqual(statuses["acked"], "superseded")
        self.assertEqual(statuses["applied"], "applied")

    def test_record_manager_message_carries_digest_and_valid_delivery_status(self):
        target = {}
        hloop.record_manager_message(target, "argv", "hello world")
        entry = target["manager_messages"][-1]
        self.assertEqual(entry["delivery_status"], "delivered")
        self.assertEqual(entry["digest"], hloop.message_digest("hello world"))
        with self.assertRaisesRegex(hloop.HLoopError, "unsupported message delivery status"):
            hloop.record_manager_message(target, "argv", "hello", delivery_status="bogus")

    def test_message_unknown_ack_and_applied_resolution_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "attempt_id": "T001-A001",
                "unknown_manager_messages": [],
            }
            _, envelope = hloop.manager_message_envelope(
                state, "T001", task_state, "apply the bounded fix"
            )
            entry = hloop.record_manager_message(
                task_state,
                "argv",
                envelope,
                delivery_status="unknown",
                delivery_error="visual confirmation timed out",
            )
            task_state["unknown_manager_messages"].append(
                {
                    "message_id": entry["message_id"],
                    "error": "visual confirmation timed out",
                }
            )
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)

            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_message_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="T001",
                        message_id=entry["message_id"],
                        status="acknowledged",
                        result=None,
                        error=None,
                    )
                )
                hloop.cmd_message_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="T001",
                        message_id=entry["message_id"],
                        status="applied",
                        result="fix applied and tests queued",
                        error=None,
                    )
                )
            reloaded = hloop.load_state(repo)["tasks"]["T001"]
            resolved = hloop.manager_message_by_id(reloaded, entry["message_id"])
            self.assertEqual(resolved["delivery_status"], "applied")
            self.assertEqual(resolved["result"], "fix applied and tests queued")
            self.assertEqual(
                [item["to"] for item in reloaded["manager_message_events"]],
                ["acknowledged", "applied"],
            )
            self.assertEqual(reloaded["unknown_manager_messages"], [])

    def test_every_long_running_role_identity_and_prompt_is_authenticated(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            store = hloop._open_broker_store(repo)
            identities = {
                "T009": "T009-A001",
                "R009": "R009-A001",
                "G009": "G009-A001",
                "A009/P1": "A009-P1-A001",
            }
            for role_id, attempt_id in identities.items():
                digest = hashlib.sha256(role_id.encode()).hexdigest()
                token = hloop.register_role_report_identity(
                    repo,
                    state,
                    role_id=role_id,
                    attempt_id=attempt_id,
                    task_contract_digest=digest,
                )
                with store.transaction() as transaction:
                    self.assertTrue(
                        store.authenticate_role_report(
                            transaction,
                            run_id="run-001",
                            role_id=role_id,
                            attempt_id=attempt_id,
                            task_contract_digest=digest,
                            token=token,
                        )
                    )
                contract = hloop.report_contract_text(
                    role_id,
                    attempt_id,
                    state,
                    report_token=token,
                    task_contract_digest=digest,
                )
                self.assertIn(f"--role-id {role_id}", contract)
                self.assertIn(f"--attempt-id {attempt_id}", contract)
                self.assertIn("stop before material work", contract)

            with store.transaction() as transaction:
                self.assertFalse(
                    store.authenticate_role_report(
                        transaction,
                        run_id="run-001",
                        role_id="R999",
                        attempt_id="R999-A001",
                        task_contract_digest="0" * 64,
                        token="unknown",
                    )
                )

    def test_semantic_ack_barrier_blocks_worker_finalize_done_until_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "result_status": "in_progress",
                "attempt_id": "T001-A001",
            }
            hloop.arm_semantic_ack_barrier(task_state, message_id="msg-1", digest="deadbeef")
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)
            hloop.write_text(
                hloop.task_file(repo, "T001"),
                hloop.frontmatter({"id": "T001", "run_id": state["run_id"]}) + "\n\n# Task T001\n",
            )
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    type="ack",
                    stage="planning",
                    summary="contract understood",
                    next="wait for approval",
                    risk=[],
                    understood_goal="complete T001",
                    scope=["tracked.txt"],
                    acceptance=["validation passes"],
                    approach="smallest safe change",
                )
            )

            self.assertTrue(hloop.semantic_ack_barrier_blocking(task_state))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                    hloop.cmd_worker_finalize(
                        SimpleNamespace(
                            repo=str(repo),
                            task_id="T001",
                            status="done",
                            validation_command=[],
                            validation_result=[],
                            blocking_question=[],
                        )
                    )

                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo), agent_id="T001", decision="approve", reason="reviewed the change"
                    )
                )
            reloaded_task_state = hloop.load_state(repo)["tasks"]["T001"]
            self.assertEqual(reloaded_task_state["semantic_ack_barrier"]["status"], "approved")
            self.assertEqual(hloop.semantic_ack_barrier_blocking(reloaded_task_state), "")

    def test_contract_changing_message_rearms_an_already_resolved_barrier(self):
        agent_state = {}
        hloop.arm_semantic_ack_barrier(agent_state, message_id="msg-1", digest="aaa")
        hloop.resolve_semantic_ack_barrier(
            agent_state,
            decision="approve",
            reason="ok",
            latest_ack={"event_id": "ack-1", "sequence": 1},
        )
        self.assertEqual(agent_state["semantic_ack_barrier"]["status"], "approved")
        hloop.arm_semantic_ack_barrier(
            agent_state,
            message_id="msg-2",
            digest="bbb",
            required_reack_after_sequence=1,
        )
        self.assertEqual(agent_state["semantic_ack_barrier"]["status"], "awaiting_ack")
        self.assertEqual(agent_state["semantic_ack_barrier"]["message_id"], "msg-2")
        self.assertTrue(hloop.semantic_ack_barrier_blocking(agent_state))
        with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                agent_state,
                decision="approve",
                reason="old ACK must not approve a new contract",
                latest_ack={"event_id": "ack-1", "sequence": 1},
            )

    def test_reject_and_timeout_require_a_fresh_corrected_ack(self):
        state = {}
        hloop.arm_initial_semantic_ack_barrier(
            state, attempt_id="T001-A001", contract_digest="a" * 64
        )
        first = {"event_id": "ack-1", "sequence": 1}
        hloop.resolve_semantic_ack_barrier(
            state, decision="reject", reason="scope is wrong", latest_ack=first
        )
        self.assertTrue(hloop.semantic_ack_barrier_blocking(state))
        with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                state, decision="approve", reason="retry", latest_ack=first
            )
        hloop.resolve_semantic_ack_barrier(
            state,
            decision="approve",
            reason="corrected",
            latest_ack={"event_id": "ack-2", "sequence": 2},
        )
        self.assertEqual(hloop.semantic_ack_barrier_blocking(state), "")

        hloop.arm_initial_semantic_ack_barrier(
            state, attempt_id="T001-A002", contract_digest="b" * 64
        )
        hloop.resolve_semantic_ack_barrier(
            state, decision="timeout", reason="manager lease expired", latest_ack=None
        )
        self.assertEqual(state["semantic_ack_barrier"]["status"], "timed_out")
        self.assertTrue(hloop.semantic_ack_barrier_blocking(state))
        hloop.resolve_semantic_ack_barrier(
            state,
            decision="approve",
            reason="fresh ACK after timeout",
            latest_ack={"event_id": "ack-3", "sequence": 3},
        )
        self.assertEqual(hloop.semantic_ack_barrier_blocking(state), "")

        hloop.arm_initial_semantic_ack_barrier(
            state,
            attempt_id="T001-A003",
            contract_digest="c" * 64,
            required_reack_after_sequence=7,
        )
        with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                state,
                decision="approve",
                reason="pre-registration ACK must not approve a new role start",
                latest_ack={"event_id": "ack-old", "sequence": 7},
            )
        hloop.resolve_semantic_ack_barrier(
            state,
            decision="approve",
            reason="post-registration ACK",
            latest_ack={"event_id": "ack-new", "sequence": 8},
        )
        self.assertEqual(hloop.semantic_ack_barrier_blocking(state), "")


class ReviewGroupRuntimeTests(unittest.TestCase):
    """Exercises reviewer start/harvest/close for every 0.5 review mode."""

    RUN_ID = "review-runtime-run"

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-review-group-runtime")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> tuple[Path, dict]:
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
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "run_id": self.RUN_ID,
            "skill_version": hloop.SKILL_VERSION,
            "persistence": "local-only",
            "phase": "dispatching",
            "base_branch": "main",
            "integration_branch": "main",
            "reviews": {},
            "reviewer_runner": "tui",
            "reviewer_agent_provider": "codex",
            "reviewer_agent_model": "auto",
        }
        hloop.save_state(repo, state)
        return repo, state

    def start_args(self, repo: Path, mode: str, review_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            repo=str(repo),
            review_id=review_id,
            base=None,
            head=None,
            worktree=str(repo.parent / f"{review_id}-worktree"),
            manager_pane=None,
            direction="down",
            launcher="pane",
            runner="tui",
            agent_provider="codex",
            agent_model="auto",
            mode=mode,
            dry_run=True,
        )

    def write_review_artifacts(
        self,
        repo: Path,
        state: dict,
        *,
        review_id: str,
        mode: str,
        complete: bool = True,
    ) -> dict:
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        agent_config = hloop.role_agent_config(state, "reviewer")
        plan = hloop.build_reviewer_group_plan(mode, head_sha, agent_config)
        worktree = repo.parent / f"{review_id}-artifact-worktree"
        worktree.mkdir()
        review_state = {
            "status": "running",
            "gate_status": "running",
            "mode": mode,
            "review_plan": plan.to_record(),
            "review_providers": list(plan.providers),
            "base": "main",
            "head": "main",
            "head_sha": head_sha,
            "attempt_id": f"{review_id}-A001",
            "skill_version": hloop.SKILL_VERSION,
            "worktree": str(worktree),
            "baseline_dirty_files": [],
        }
        if mode == "single":
            artifact = hloop.review_file(worktree, review_id)
            review_state["worktree_review_path"] = str(artifact)
            review_state["review_path"] = str(hloop.review_file(repo, review_id))
            hloop.write_text(
                artifact,
                hloop.frontmatter(
                    {
                        "review_id": review_id,
                        "run_id": self.RUN_ID,
                        "skill_version": hloop.SKILL_VERSION,
                        "base": "main",
                        "head": "main",
                        "head_sha": head_sha,
                        "status": "reported",
                    }
                )
                + "\n## Fix Task Candidates\n\nNo fix task candidates.\n",
            )
        else:
            lane_results = tuple(lane.result() for lane in plan.expected_lanes)
            if not complete:
                lane_results = lane_results[:-1]
            manifest = hloop.hloop_review.ReviewManifest(
                review_id=review_id,
                plan=plan,
                lane_results=lane_results,
                findings=(),
                verification_plan=hloop.hloop_review.plan_verification(plan, ()),
                verifications=(),
            )
            hloop.write_text(
                hloop.review_manifest_file(worktree, review_id),
                json.dumps(manifest.to_record(), indent=2, sort_keys=True) + "\n",
            )
            for provider in plan.providers:
                hloop.write_text(
                    hloop.review_provider_file(worktree, review_id, provider),
                    hloop.frontmatter(
                        {
                            "review_id": review_id,
                            "run_id": self.RUN_ID,
                            "skill_version": hloop.SKILL_VERSION,
                            "head_sha": head_sha,
                            "provider": provider,
                            "status": "reported",
                        }
                    )
                    + f"\n# {provider} provider report\n",
                )
            artifact = hloop.review_final_file(worktree, review_id)
            review_state["worktree_review_path"] = str(artifact)
            review_state["review_path"] = str(hloop.review_final_file(repo, review_id))
            hloop.write_text(
                artifact,
                hloop.frontmatter(
                    {
                        "review_id": review_id,
                        "run_id": self.RUN_ID,
                        "skill_version": hloop.SKILL_VERSION,
                        "base": "main",
                        "head": "main",
                        "head_sha": head_sha,
                        "status": "reported",
                    }
                )
                + "\n## Fix Task Candidates\n\nNo fix task candidates.\n",
            )
        state["reviews"][review_id] = review_state
        hloop.save_state(repo, state)
        return review_state

    def test_start_prompts_harvest_and_close_every_review_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            for index, mode in enumerate(
                ("single", "swarm", "dual", "dual-swarm"), start=1
            ):
                review_id = f"R{index:03d}"
                with self.subTest(mode=mode):
                    with contextlib.redirect_stdout(io.StringIO()) as buffer:
                        self.assertEqual(
                            hloop.cmd_reviewer_start(
                                self.start_args(repo, mode, review_id)
                            ),
                            0,
                        )
                    self.assertIn("DRY RUN", buffer.getvalue())

                    agent_config = hloop.role_agent_config(state, "reviewer")
                    head_sha = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=repo,
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip()
                    plan = hloop.build_reviewer_group_plan(mode, head_sha, agent_config)
                    prompt = hloop.render_reviewer_prompt(
                        review_id,
                        "main",
                        "main",
                        state,
                        agent_config=agent_config,
                        head_sha=head_sha,
                        review_plan=plan,
                    )
                    if mode == "single":
                        self.assertIn(f"reviews/{review_id}.md", prompt)
                    else:
                        for lane in plan.expected_lanes:
                            self.assertIn(lane.lane_id, prompt)
                            self.assertIn(lane.agent_label, prompt)
                        self.assertIn(f"reviews/{review_id}/MANIFEST.json", prompt)
                        self.assertIn(f"reviews/{review_id}/FINAL.md", prompt)

                    review_state = self.write_review_artifacts(
                        repo, state, review_id=review_id, mode=mode
                    )
                    with mock.patch.object(
                        hloop, "preflight_loop", return_value=state
                    ), mock.patch.object(
                        hloop, "validate_reviewer_worktree_scope", return_value=[]
                    ), mock.patch.object(
                        hloop, "cleanup_completed_agent_pane"
                    ), mock.patch.object(
                        hloop, "cleanup_review_worktree"
                    ), contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(
                            hloop.cmd_reviewer_harvest(
                                SimpleNamespace(
                                    repo=str(repo),
                                    review_id=review_id,
                                    keep_pane=False,
                                    session_cleanup="none",
                                )
                            ),
                            0,
                        )
                        self.assertEqual(review_state["gate_status"], "reported")
                        self.assertEqual(
                            hloop.cmd_reviewer_close(
                                SimpleNamespace(
                                    repo=str(repo),
                                    review_id=review_id,
                                    verdict="passed",
                                    reason="runtime fixture passed",
                                    keep_pane=False,
                                    session_cleanup="none",
                                )
                            ),
                            0,
                        )
                    self.assertEqual(review_state["gate_status"], "triaged")
                    if mode != "single":
                        self.assertTrue(review_state["manifest_complete"])
                        self.assertTrue(Path(review_state["manifest_path"]).is_file())
                        self.assertEqual(
                            set(review_state["provider_report_paths"]),
                            set(plan.providers),
                        )

    def test_config_only_topology_resolves_mode_providers_and_probe_count(self):
        """An unset CLI --mode must not silently narrow a configured topology."""

        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["resolved_config"] = {
                "reviewer": {
                    "mode": "dual-swarm",
                    "providers": ["codex", "claude"],
                    "probes_per_provider": 8,
                }
            }
            state["review_capacity_limits"] = {"codex": 20, "claude": 20}
            hloop.save_state(repo, state)

            topology = hloop.resolved_reviewer_topology(state)
            self.assertEqual(topology["mode"], "dual-swarm")
            self.assertEqual(topology["providers"], ["codex", "claude"])
            self.assertEqual(topology["probes_per_provider"], 8)
            self.assertIsNone(topology["probe_count"])

            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            agent_config = hloop.role_agent_config(state, "reviewer")
            plan = hloop.build_reviewer_group_plan(
                topology["mode"], head_sha, agent_config, topology=topology
            )
            self.assertEqual(plan.mode, "dual-swarm")
            self.assertEqual(set(plan.providers), {"codex", "claude"})
            for provider_plan in plan.provider_plans:
                self.assertEqual(len(provider_plan.lanes), 8)

            args = self.start_args(repo, None, "R901")
            args.providers = None
            args.probe_count = None
            args.probes_per_provider = None
            with contextlib.redirect_stdout(io.StringIO()) as buffer:
                self.assertEqual(hloop.cmd_reviewer_start(args), 0)
            self.assertIn("DRY RUN", buffer.getvalue())

    def test_explicit_cli_mode_overrides_configured_reviewer_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["resolved_config"] = {"reviewer": {"mode": "dual-swarm"}}
            hloop.save_state(repo, state)
            topology = hloop.resolved_reviewer_topology(state, mode="single")
            self.assertEqual(topology["mode"], "single")

    def test_incomplete_swarm_harvests_but_close_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            review_state = self.write_review_artifacts(
                repo, state, review_id="R001", mode="swarm", complete=False
            )
            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "validate_reviewer_worktree_scope", return_value=[]
            ), mock.patch.object(
                hloop, "cleanup_completed_agent_pane"
            ), mock.patch.object(
                hloop, "cleanup_review_worktree"
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    hloop.cmd_reviewer_harvest(
                        SimpleNamespace(
                            repo=str(repo),
                            review_id="R001",
                            keep_pane=False,
                            session_cleanup="none",
                        )
                    ),
                    0,
                )
                self.assertFalse(review_state["manifest_complete"])
                with self.assertRaisesRegex(hloop.HLoopError, "manifest incomplete"):
                    hloop.cmd_reviewer_close(
                        SimpleNamespace(
                            repo=str(repo),
                            review_id="R001",
                            verdict="passed",
                            reason="must not close",
                            keep_pane=False,
                            session_cleanup="none",
                        )
                    )

    def test_reviewer_start_rejects_swarm_exceeding_configured_capacity_before_pane_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["review_capacity_limits"] = {"codex": 3}
            hloop.save_state(repo, state)
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    hloop.hloop_providers.ProviderError, "review swarm capacity exceeded"
                ):
                    hloop.cmd_reviewer_start(self.start_args(repo, "swarm", "R901"))
            self.assertNotIn("R901", state.get("reviews", {}))
            self.assertFalse((repo.parent / "R901-worktree").exists())

    def test_triage_accepts_only_manifest_confirmed_fingerprints(self):
        confirmed = "sha256:" + "a" * 64
        refuted = "sha256:" + "b" * 64
        candidates = [
            {"title": "confirmed", "source_finding": confirmed},
            {"title": "refuted", "source_finding": refuted},
            {"title": "missing source", "source_finding": ""},
        ]

        accepted, rejected = hloop.confirmed_review_fix_task_candidates(
            SimpleNamespace(confirmed_fingerprints=(confirmed,)), candidates, []
        )

        self.assertEqual([item["title"] for item in accepted], ["confirmed"])
        self.assertEqual([item["title"] for item in rejected], ["refuted", "missing source"])
        self.assertTrue(
            all("all confirmed" in item["reasons"][0] for item in rejected)
        )


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
            loop_dir = repo / hloop.LOOP_DIR
            self.assertTrue((loop_dir / "requirements" / "REQUIREMENTS.md").is_file())
            self.assertTrue((loop_dir / "requirements" / "STATUS.md").is_file())

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
            self.assertTrue((loop_dir / "progress" / "LATEST.md").is_file())
            self.assertTrue((loop_dir / "progress" / "P0002.md").is_file())

            hloop.cmd_context_update(
                SimpleNamespace(
                    repo=str(repo),
                    source="U0001",
                    text="Keep authentication changes backward compatible; token=context-secret-12345678.",
                )
            )
            context = (loop_dir / "context" / "MANAGER_CONTEXT.md").read_text()
            self.assertIn("backward compatible", context)
            self.assertIn("U0001", context)
            self.assertNotIn("context-secret-12345678", context)
            self.assertIn("[REDACTED]", context)

    def test_config_apply_is_explicit_dry_run_safe_and_idle_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["resolved_config"] = {
                "max_workers": 3,
                "session_cleanup": "archive",
                "worker": {"provider": "codex", "model": "auto", "effort": "auto"},
                "reviewer": {"provider": "codex", "model": "auto", "effort": "auto"},
            }
            hloop.save_state(repo, state)
            config_home = root / "config"
            config_home.mkdir()
            (config_home / "config.toml").write_text(
                """version = 1
[defaults]
max_workers = 2
session_cleanup = "none"
[defaults.worker]
provider = "claude"
model = "sonnet"
effort = "high"
""",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"HLOOP_CONFIG_HOME": str(config_home)}):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    hloop.cmd_config_apply(
                        SimpleNamespace(repo=str(repo), dry_run=True, apply=False)
                    )
                preview = json.loads(output.getvalue())
                self.assertTrue(preview["changed"])
                self.assertIsNone(hloop.load_state(repo).get("max_workers"))

                hloop.cmd_config_apply(
                    SimpleNamespace(repo=str(repo), dry_run=False, apply=True)
                )
            applied = hloop.load_state(repo)
            self.assertEqual(applied["max_workers"], 2)
            self.assertEqual(applied["session_cleanup"], "none")
            self.assertEqual(applied["worker_agent_provider"], "claude")
            self.assertEqual(applied["worker_agent_model"], "sonnet")

            applied["tasks"] = {"T001": {"status": "running"}}
            hloop.save_state(repo, applied)
            with mock.patch.dict(os.environ, {"HLOOP_CONFIG_HOME": str(config_home)}):
                with self.assertRaisesRegex(hloop.HLoopError, "roles are running"):
                    hloop.cmd_config_apply(
                        SimpleNamespace(repo=str(repo), dry_run=False, apply=True)
                    )

    def test_decision_artifacts_are_plain_language_and_block_only_affected_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["tasks"] = {
                "T001": {"status": "queued", "depends_on": []},
                "T002": {"status": "queued", "depends_on": []},
            }
            hloop.save_state(repo, state)
            hloop.cmd_decision_new(
                SimpleNamespace(
                    repo=str(repo),
                    id="D001",
                    title="既存データの表示方法をどちらにするか",
                    decision_class="deferred-user",
                    affects=["T001"],
                    option=["従来表示を保つ", "新表示へ切り替える"],
                    tradeoff=["互換性を保てる", "操作を簡潔にできる"],
                    recommend_option="opt_1",
                    recommend_rationale="既存利用者への影響が小さいため",
                    source_finding=["scout:F001"],
                    created_from="specification-scout",
                )
            )
            state = hloop.load_state(repo)
            scheduler = hloop.build_decision_scheduler(state)
            self.assertFalse(scheduler.loop_blocked)
            self.assertEqual(scheduler.decision_blocked_task_ids, ("T001",))
            self.assertIn("T002", scheduler.dispatchable_task_ids)
            question = (
                repo / hloop.LOOP_DIR / "decisions" / "D001" / "QUESTION.md"
            ).read_text()
            self.assertIn("# 判断のお願い", question)
            self.assertIn("## 選択肢", question)
            self.assertIn("この判断に依存しない作業は継続できます", question)

            hloop.cmd_decision_respond(
                SimpleNamespace(
                    repo=str(repo),
                    id="D001",
                    text="従来表示を保つ",
                    option="opt_1",
                    recommendation=None,
                    responded_by="user",
                )
            )
            response = (
                repo / hloop.LOOP_DIR / "decisions" / "D001" / "RESPONSE.md"
            ).read_text()
            self.assertIn("従来表示を保つ", response)

    def test_advisor_close_is_idempotent_and_consumes_reported_participants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["advice"] = {
                "A001": {
                    "status": "reported",
                    "gate_status": "reported",
                    "participants": [
                        {"participant_id": "P1", "status": "reported", "gate_status": "reported"}
                    ],
                }
            }
            hloop.save_state(repo, state)
            args = SimpleNamespace(
                repo=str(repo),
                advice_id="A001",
                verdict="no-action",
                reason="evidence is sufficient",
            )
            with mock.patch.object(
                hloop,
                "preflight_loop",
                side_effect=lambda checked_repo, **_kwargs: hloop.load_state(checked_repo),
            ):
                hloop.cmd_advisor_close(args)
                first = hloop.load_state(repo)
                first_time = first["advice"]["A001"]["triaged_at"]
                hloop.cmd_advisor_close(args)
            closed = hloop.load_state(repo)["advice"]["A001"]
            self.assertEqual(closed["triaged_at"], first_time)
            self.assertEqual(closed["participants"][0]["gate_status"], "consumed")

    def test_run_level_handoff_abandon_and_supersede_are_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()

            hloop.cmd_completion_handoff(
                SimpleNamespace(
                    repo=str(repo),
                    next_namespace="next-run",
                    head_sha=head,
                    reason="split remaining scope",
                )
            )
            self.assertEqual(hloop.load_state(repo)["phase"], "handoff")

            state = hloop.load_state(repo)
            state["phase"] = "dispatching"
            state["terminal_outcome"] = {}
            hloop.save_state(repo, state)
            hloop.cmd_completion_abandon(
                SimpleNamespace(repo=str(repo), reason="user cancelled the goal")
            )
            self.assertEqual(hloop.load_state(repo)["phase"], "abandoned_by_user")

            state = hloop.load_state(repo)
            state["phase"] = "done"
            state["final_target_sha"] = head
            state["terminal_outcome"] = {"status": "done", "target_sha": head}
            hloop.save_state(repo, state)
            hloop.cmd_completion_supersede(
                SimpleNamespace(
                    repo=str(repo),
                    head_sha=head,
                    reason="a successor run owns later evidence",
                    next_namespace="successor-run",
                )
            )
            superseded = hloop.load_state(repo)
            self.assertEqual(superseded["phase"], "superseded")
            self.assertEqual(
                superseded["terminal_outcome"]["next_namespace"], "successor-run"
            )
            inventory = hloop.collect_loop_inventory(repo, probe_panes=False)
            self.assertFalse(
                any(
                    "reviewer start" in action or "gap start" in action
                    for action in inventory["next_actions"]
                )
            )

    def test_blocked_outcome_is_explicit_and_outcome_report_rendered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            requirement = hloop.hloop_requirements.Requirement(
                requirement_id="REQ-001",
                source_inputs=("U0001",),
                acceptance=("user decision is resolved",),
                priority="P1",
                accepted_at="2026-07-15T00:00:00+00:00",
            ).to_record()
            requirement["progress"] = hloop.hloop_requirements.RequirementProgress(
                requirement_id="REQ-001",
                status="blocked",
                blockers=("user response",),
            ).to_record()
            state.update(
                {
                    "goal_id": "blocked-goal",
                    "run_id": "run-blocked",
                    "phase": "blocked_user_decision",
                    "requirements": {"REQ-001": requirement},
                    "tasks": {},
                }
            )
            hloop.save_state(repo, state)
            hloop.cmd_outcome_blocked(
                SimpleNamespace(repo=str(repo), reason="same user decision blocks all safe work")
            )
            blocked = hloop.load_state(repo)
            self.assertEqual(blocked["phase"], "blocked")
            report_path = repo / hloop.LOOP_DIR / "reports" / "BLOCKED.md"
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# Blocked Outcome", report)
            self.assertIn("same user decision blocks all safe work", report)


if __name__ == "__main__":
    unittest.main()
