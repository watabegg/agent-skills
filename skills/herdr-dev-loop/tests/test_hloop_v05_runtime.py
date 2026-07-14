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
        return repo

    def report_args(self, repo: Path, **overrides) -> SimpleNamespace:
        base = dict(
            repo=str(repo),
            run_id=None,
            role_id="T001",
            attempt_id=None,
            event_id=None,
            task_contract_digest=None,
            report_token=None,
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
            hloop.cmd_agent_report(self.report_args(repo, event_id=event_id))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_sleep(
                    SimpleNamespace(
                        repo=str(repo), ttl_seconds=3600, manager_session_id="sess", pane_id="pane"
                    )
                )
            output = buffer.getvalue()
            self.assertIn("unread reports already pending: 1", output)
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

            hloop.cmd_manager_sleep(
                SimpleNamespace(
                    repo=str(repo), ttl_seconds=3600, manager_session_id="sess", pane_id="pane"
                )
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
