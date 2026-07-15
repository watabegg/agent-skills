from __future__ import annotations

import hashlib
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib import broker, events, hooks, providers, supervisor  # noqa: E402


CREATED_AT = "2026-07-15T00:00:00+00:00"


def client_event(event_id: str = "28b79cf0-8e32-4b89-88af-e332ee5a5dbe"):
    return events.prepare_client_event(
        {
            "run_id": "run-011",
            "role_id": "T011",
            "attempt_id": "T011-A001",
            "task_contract_digest": hashlib.sha256(b"contract").hexdigest(),
            "type": "ack",
            "stage": "prepared",
            "summary": "supervisor契約を確認しました",
            "understood_goal": "foreground supervisorを実装する",
            "scope": ["supervisor.py"],
            "acceptance": ["reportでManagerが起きる"],
            "approach": "spoolを正本としてsocketはwake signalだけにする",
            "next": "実装を開始します",
            "needs_manager": True,
            "evidence_refs": ["tasks/T011.md"],
            "created_at": CREATED_AT,
        },
        event_id=event_id,
    )


def milestone_event(event_id: str = "18b79cf0-8e32-4b89-88af-e332ee5a5dbe"):
    return events.prepare_client_event(
        {
            "run_id": "run-011",
            "role_id": "T011",
            "attempt_id": "T011-A001",
            "task_contract_digest": hashlib.sha256(b"contract").hexdigest(),
            "type": "milestone",
            "stage": "testing",
            "summary": "targeted tests passed",
            "next": "continue validation",
            "needs_manager": False,
            "risks": ["manager final QA remains"],
            "evidence_refs": ["tests/test_supervisor_v05.py"],
            "created_at": CREATED_AT,
        },
        event_id=event_id,
    )


def attention_event(
    *,
    role_id: str = "T012",
    attempt_id: str = "T012-A001",
    event_id: str = "38b79cf0-8e32-4b89-88af-e332ee5a5dbe",
    summary: str = "second role needs a decision",
):
    return events.prepare_client_event(
        {
            "run_id": "run-011",
            "role_id": role_id,
            "attempt_id": attempt_id,
            "task_contract_digest": hashlib.sha256(role_id.encode("utf-8")).hexdigest(),
            "type": "attention",
            "stage": "blocked",
            "summary": summary,
            "next": "wait for manager",
            "needs_manager": True,
            "impact": "cannot proceed without a decision",
            "attempted": ["investigated the fault"],
            "options": ["escalate to the Manager"],
            "recommendation": "escalate",
            "blocked_scope": ["material edits"],
            "evidence_refs": ["tasks/T012.md"],
            "created_at": CREATED_AT,
        },
        event_id=event_id,
    )


def report_authentication(event, token="report-token"):
    return {
        "run_id": event["run_id"],
        "role_id": event["role_id"],
        "attempt_id": event["attempt_id"],
        "task_contract_digest": event["task_contract_digest"],
        "token": token,
    }


class ProviderPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prompt = self.root / "prompt.md"
        self.output = self.root / "last.txt"
        self.prompt.write_text("bounded prompt", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_fake_codex_records_final_argv_and_model_capability(self):
        invocation = providers.build_provider_invocation(
            provider="codex",
            runner="exec",
            sandbox="workspace-write",
            prompt_path=self.prompt,
            output_path=self.output,
            model="gpt-test",
            effort="high",
            writable_dirs=[self.root],
        )
        expected = (
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "--model",
            "gpt-test",
            "-c",
            "model_reasoning_effort=high",
            "--add-dir",
            str(self.root),
            "--output-last-message",
            str(self.output),
            "-",
        )
        self.assertEqual(invocation.argv, expected)
        calls: list[list[str]] = []

        def fake_codex(argv, **kwargs):
            calls.append(argv)
            self.assertTrue(kwargs["capture_output"])
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="--sandbox --model -c --add-dir --output-last-message",
                stderr="",
            )

        def validate_model(value):
            self.assertEqual(value.argv, expected)
            return providers.ModelProbeResult(
                "supported",
                "fake Codex accepted the resolved model",
                ("/fake/codex", "model", "validate", value.model),
                0,
            )

        result = providers.probe_provider_capability(
            invocation,
            executable_finder=lambda name: f"/fake/{name}",
            command_runner=fake_codex,
            model_validator=validate_model,
        )

        self.assertEqual(calls, [["/fake/codex", "exec", "--help"]])
        self.assertEqual(result.capability, "supported")
        self.assertTrue(result.launch_allowed)
        self.assertEqual(result.as_record()["argv"], list(expected))
        self.assertEqual(result.as_record()["permission_mode"], "never")
        self.assertEqual(result.as_record()["sandbox"], "workspace-write")
        self.assertEqual(result.as_record()["model_probe"]["returncode"], 0)

    def test_fake_claude_never_silently_falls_back_from_unknown_or_bad_model(self):
        invocation = providers.build_provider_invocation(
            provider="claude",
            runner="exec",
            sandbox="workspace-write",
            prompt_path=self.prompt,
            model="claude-test",
            effort="high",
            permission_mode="acceptEdits",
        )

        def fake_claude(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="--print --permission-mode --model --effort",
                stderr="",
            )

        unknown = providers.probe_provider_capability(
            invocation,
            executable_finder=lambda name: f"/fake/{name}",
            command_runner=fake_claude,
        )
        self.assertEqual(unknown.capability, "unknown")
        self.assertEqual(unknown.invocation.model, "claude-test")
        self.assertNotIn("fallback", unknown.as_record())

        rejected = providers.probe_provider_capability(
            invocation,
            executable_finder=lambda name: f"/fake/{name}",
            command_runner=fake_claude,
            model_validator=lambda value: providers.ModelProbeResult(
                "unsupported", f"model {value.model} is unavailable", returncode=2
            ),
        )
        self.assertEqual(rejected.capability, "unsupported")
        self.assertFalse(rejected.launch_allowed)
        self.assertEqual(rejected.invocation.model, "claude-test")

    def test_missing_provider_flag_is_unsupported_before_launch(self):
        invocation = providers.build_provider_invocation(
            provider="claude",
            runner="exec",
            sandbox="workspace-write",
            prompt_path=self.prompt,
            effort="high",
        )
        result = providers.probe_provider_capability(
            invocation,
            executable_finder=lambda name: f"/fake/{name}",
            command_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, stdout="--print --permission-mode", stderr=""
            ),
        )
        self.assertEqual(result.capability, "unsupported")
        self.assertIn("--effort", result.reason)

    def test_review_capacity_probe_is_supported_when_help_documents_config_override(self):
        status, reason = providers.probe_provider_review_capacity(
            "codex",
            executable_finder=lambda name: f"/fake/{name}",
            command_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, stdout="usage: codex [-c key=value] ...", stderr=""
            ),
        )
        self.assertEqual(status, "supported")
        self.assertIn("agents.max_threads", reason)

    def test_review_capacity_probe_stays_unknown_without_documented_controls(self):
        status, reason = providers.probe_provider_review_capacity(
            "claude",
            executable_finder=lambda name: f"/fake/{name}",
            command_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, stdout="usage: claude [--print]", stderr=""
            ),
        )
        self.assertEqual(status, "unknown")
        self.assertNotEqual(status, "supported")

    def test_review_capacity_probe_is_unavailable_when_provider_missing(self):
        status, reason = providers.probe_provider_review_capacity(
            "codex", executable_finder=lambda name: None
        )
        self.assertEqual(status, "unavailable")
        self.assertIn("not found", reason)

    def test_check_review_capacity_fails_closed_on_unsupported_capacity_probe(self):
        with self.assertRaisesRegex(providers.ProviderError, "review swarm capacity exceeded"):
            providers.check_review_capacity(
                {"codex": 3},
                capability={"codex": ("unsupported", "codex --help lacks -c")},
            )

    def test_check_review_capacity_passes_with_supported_or_unverified_probe(self):
        results = providers.check_review_capacity(
            {"codex": 3, "claude": 2},
            capability={
                "codex": ("supported", "documents -c overrides"),
                "claude": ("unknown", "no documented controls"),
            },
        )
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(
            {result.provider: result.capacity_probe for result in results},
            {"codex": "supported", "claude": "unknown"},
        )

    def test_check_review_capacity_without_capability_argument_stays_backward_compatible(self):
        results = providers.check_review_capacity({"codex": 3})
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].capacity_probe, "unknown")


class SupervisorPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = broker.BrokerStore(self.root / "broker")
        self.socket_path = self.root / "runtime" / "run.sock"
        self.metadata_path = self.root / "broker" / "owner.json"
        self.spool = self.root / "broker" / "spool"
        sample = client_event()
        with self.store.transaction() as transaction:
            self.store.register_active_role(
                transaction, **report_authentication(sample)
            )

    def tearDown(self):
        self.temporary.cleanup()

    def make_supervisor(self, **overrides):
        values: dict[str, Any] = {
            "namespace": "namespace",
            "run_id": "run-011",
            "runtime_version": "0.5.0",
            "manager_session_id": "session-manager",
            "pane_id": "wH:p1",
            "socket_path": self.socket_path,
            "owner_metadata_path": self.metadata_path,
            "spool_directory": self.spool,
        }
        values.update(overrides)
        return supervisor.ManagerSleepSupervisor(self.store, **values)

    def test_stale_socket_is_recovered_and_owned_paths_are_cleaned(self):
        self.socket_path.parent.mkdir(parents=True)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.socket_path))
        stale.close()
        stale_inode = self.socket_path.stat().st_ino

        owned = self.make_supervisor()
        metadata = owned.acquire()
        self.assertNotEqual(self.socket_path.stat().st_ino, stale_inode)
        self.assertEqual(self.socket_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(broker.read_owner_metadata(self.metadata_path), metadata)
        owned.release()

        self.assertFalse(self.socket_path.exists())
        self.assertFalse(self.metadata_path.exists())

    def test_duplicate_owner_is_rejected_without_disturbing_live_owner(self):
        first = self.make_supervisor()
        second = self.make_supervisor(manager_session_id="session-other")
        first.acquire()
        with self.assertRaises(supervisor.DuplicateOwnerError):
            second.acquire()
        self.assertTrue(self.socket_path.exists())
        first.release()

    def test_non_socket_path_is_never_removed_as_stale_runtime_state(self):
        self.socket_path.parent.mkdir(parents=True)
        self.socket_path.write_text("user data", encoding="utf-8")
        with self.assertRaises(supervisor.UnsafeSocketError):
            self.make_supervisor().acquire()
        self.assertEqual(self.socket_path.read_text(encoding="utf-8"), "user data")

    def test_socket_parent_symlink_is_rejected_without_chmod_or_socket_placement(self):
        victim = self.root / "victim"
        victim.mkdir(mode=0o755)
        victim.chmod(0o755)
        self.socket_path.parent.symlink_to(victim, target_is_directory=True)

        with self.assertRaises(supervisor.UnsafeSocketError):
            self.make_supervisor()._prepare_socket_path()

        self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o755)
        self.assertFalse((victim / self.socket_path.name).exists())

    def test_socket_parent_owned_by_another_user_is_rejected(self):
        self.socket_path.parent.mkdir(mode=0o700)
        with mock.patch.object(
            supervisor.os, "geteuid", return_value=os.geteuid() + 1
        ):
            with self.assertRaises(supervisor.UnsafeSocketError):
                self.make_supervisor()._prepare_socket_path()

    def test_socket_parent_chmod_failure_is_not_suppressed(self):
        self.socket_path.parent.mkdir(mode=0o755)
        self.socket_path.parent.chmod(0o755)
        with mock.patch.object(
            supervisor.os,
            "fchmod",
            side_effect=PermissionError("simulated chmod failure"),
        ):
            with self.assertRaisesRegex(
                supervisor.UnsafeSocketError, "cannot secure private directory"
            ):
                self.make_supervisor()._prepare_socket_path()

    def test_report_signal_drains_spool_and_wakes_manager(self):
        event = client_event()
        errors: list[BaseException] = []

        def report_from_role():
            try:
                deadline = time.monotonic() + 2
                while not self.socket_path.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("supervisor socket was not created")
                    time.sleep(0.005)
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(1)
                try:
                    client.connect(str(self.socket_path))
                    broker.spool_client_event(
                        self.spool,
                        event,
                        authentication=report_authentication(event),
                    )
                    client.sendall(supervisor.WAKE_SIGNAL)
                finally:
                    client.close()
            except BaseException as exc:
                errors.append(exc)

        reporter = threading.Thread(target=report_from_role, daemon=True)
        reporter.start()
        result = self.make_supervisor().sleep(
            timeout_seconds=2,
            poll_interval_seconds=0.5,
        )
        reporter.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(result.reason, "report")
        self.assertEqual(result.event_ids, (event["event_id"],))
        self.assertEqual(result.drained_reports, 1)
        self.assertEqual(list(self.spool.glob("*.json")), [])
        self.assertFalse(self.socket_path.exists())
        with self.store.transaction() as transaction:
            self.assertEqual(
                [item["event_id"] for item in self.store.inbox(transaction)],
                [event["event_id"]],
            )

    def test_cross_role_burst_within_the_window_is_delivered_as_one_batch(self):
        first = client_event()
        second = attention_event()
        errors: list[BaseException] = []

        def report_second_role():
            try:
                time.sleep(0.05)
                with self.store.transaction() as transaction:
                    self.store.accept_report(transaction, second)
            except BaseException as exc:
                errors.append(exc)

        with self.store.transaction() as transaction:
            self.store.accept_report(transaction, first)

        reporter = threading.Thread(target=report_second_role, daemon=True)
        reporter.start()
        result = self.make_supervisor().sleep(
            timeout_seconds=2,
            wake_burst_window_seconds=0.3,
            wake_burst_poll_seconds=0.02,
        )
        reporter.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(result.reason, "report")
        self.assertEqual(
            set(result.event_ids), {first["event_id"], second["event_id"]}
        )
        with self.store.transaction() as transaction:
            self.assertEqual(len(self.store.events(transaction)), 2)
            self.assertEqual(len(self.store.inbox(transaction)), 2)

    def test_cross_role_burst_outside_the_window_still_wakes_on_the_next_sleep(self):
        first = client_event()
        second = attention_event()
        errors: list[BaseException] = []

        def report_second_role_late():
            try:
                time.sleep(0.25)
                with self.store.transaction() as transaction:
                    self.store.accept_report(transaction, second)
            except BaseException as exc:
                errors.append(exc)

        with self.store.transaction() as transaction:
            self.store.accept_report(transaction, first)

        reporter = threading.Thread(target=report_second_role_late, daemon=True)
        reporter.start()
        first_result = self.make_supervisor().sleep(
            timeout_seconds=2,
            wake_burst_window_seconds=0.05,
            wake_burst_poll_seconds=0.01,
        )
        self.assertEqual(first_result.reason, "report")
        self.assertEqual(first_result.event_ids, (first["event_id"],))

        reporter.join(timeout=2)
        self.assertEqual(errors, [])

        # The Manager explicitly acknowledges the delivered event so the next
        # sleep call is scoped to what is still genuinely unread.
        with self.store.transaction() as transaction:
            self.store.acknowledge_inbox(
                transaction, event_id=first["event_id"], run_id="run-011"
            )

        # Every event is preserved even though it missed the bounded batch
        # window: the next sleep call still delivers it, undropped.
        second_result = self.make_supervisor().sleep(timeout_seconds=2)
        self.assertEqual(second_result.reason, "report")
        self.assertEqual(second_result.event_ids, (second["event_id"],))

    def test_timeout_invalidates_lease_and_cleans_foreground_owner(self):
        result = self.make_supervisor().sleep(
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )
        self.assertEqual(result.reason, "timeout")
        self.assertIsNotNone(result.lease_generation)
        self.assertFalse(self.socket_path.exists())
        self.assertFalse(self.metadata_path.exists())
        with self.store.transaction() as transaction:
            self.assertFalse(
                self.store.lease_generation_matches(
                    transaction,
                    run_id="run-011",
                    generation=result.lease_generation,
                    manager_session_id="session-manager",
                    pane_id="wH:p1",
                )
            )

    def test_report_present_before_sleep_is_returned_without_lost_wake(self):
        event = client_event()
        broker.spool_client_event(
            self.spool, event, authentication=report_authentication(event)
        )
        result = self.make_supervisor().sleep(timeout_seconds=1)

        self.assertEqual(result.reason, "report")
        self.assertEqual(result.event_ids, (event["event_id"],))
        self.assertEqual(result.drained_reports, 1)
        with self.store.transaction() as transaction:
            self.assertFalse(
                self.store.lease_generation_matches(
                    transaction,
                    run_id="run-011",
                    generation=result.lease_generation,
                    manager_session_id="session-manager",
                    pane_id="wH:p1",
                )
            )

    def test_sleep_quarantines_poison_spool_entries_and_still_returns_the_valid_report(self):
        event = client_event()
        broker.spool_client_event(
            self.spool, event, authentication=report_authentication(event)
        )
        poison = self.spool / "ffffffff-ffff-4fff-8fff-ffffffffffff.json"
        poison.write_text('{"not": "an event"}', encoding="utf-8")

        result = self.make_supervisor().sleep(timeout_seconds=1)

        self.assertEqual(result.reason, "report")
        self.assertEqual(result.event_ids, (event["event_id"],))
        self.assertEqual(result.drained_reports, 1)
        self.assertEqual(list(self.spool.glob("*.json")), [])
        quarantined = [
            path
            for path in (self.spool / "quarantine").glob("*.json")
            if not path.name.endswith(".audit.json")
        ]
        self.assertEqual([path.name for path in quarantined], [poison.name])

    def test_milestone_remains_inbox_only_and_does_not_wake_manager(self):
        event = milestone_event()
        with self.store.transaction() as transaction:
            self.store.accept_report(transaction, event)

        result = self.make_supervisor().sleep(
            timeout_seconds=0.03,
            poll_interval_seconds=0.005,
        )

        self.assertEqual(result.reason, "timeout")
        self.assertEqual(result.event_ids, ())
        with self.store.transaction() as transaction:
            self.assertEqual(
                [row["event_id"] for row in self.store.unconsumed_inbox(
                    transaction, run_id="run-011"
                )],
                [event["event_id"]],
            )
            self.assertEqual(self.store.pending_wakes(transaction), [])

    def test_timeout_terminates_hookless_herdr_wait_process(self):
        processes = []

        class PendingWaitProcess:
            def __init__(self):
                self.terminated = False
                self.killed = False

            def poll(self):
                return 0 if self.terminated or self.killed else None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout):
                return 0

            def kill(self):
                self.killed = True

        def fake_popen(argv, **kwargs):
            process = PendingWaitProcess()
            processes.append(process)
            return process

        result = self.make_supervisor(popen_factory=fake_popen).sleep(
            timeout_seconds=0.05,
            fallback_watches=[supervisor.FallbackWatch("wH:p9", "done")],
            poll_interval_seconds=0.01,
        )

        self.assertEqual(result.reason, "timeout")
        self.assertTrue(processes[0].terminated)
        self.assertFalse(processes[0].killed)
        self.assertFalse(self.socket_path.exists())

    def test_hookless_mode_uses_herdr_blocking_wait_fallback(self):
        hook_plan = hooks.plan_stop_hook(
            provider="codex",
            helper_path=self.root / "hloop",
            namespace="namespace",
            enabled=False,
        )
        self.assertFalse(hook_plan.installable)
        calls: list[tuple[list[str], dict[str, Any]]] = []

        class FakeWaitProcess:
            def poll(self):
                return 0

            def terminate(self):
                raise AssertionError("completed fallback must not be terminated")

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return FakeWaitProcess()

        result = self.make_supervisor(popen_factory=fake_popen).sleep(
            timeout_seconds=1,
            fallback_watches=[supervisor.FallbackWatch("wH:p9", "done")],
        )

        self.assertEqual(result.reason, "herdr-fallback")
        self.assertEqual(result.fallback.pane_id, "wH:p9")
        self.assertEqual(
            calls[0][0],
            [
                "herdr",
                "wait",
                "agent-status",
                "wH:p9",
                "--status",
                "done",
                "--timeout",
                calls[0][0][-1],
            ],
        )
        self.assertGreater(int(calls[0][0][-1]), 0)
        self.assertFalse(self.socket_path.exists())


class HookPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.helper = Path("/opt/herdr-dev-loop/scripts/hloop")
        self.user_settings = {
            "theme": "dark",
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /home/user/check.py",
                                "timeout": 30,
                            }
                        ]
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo user"}],
                    }
                ],
            },
        }

    def test_claude_merge_is_idempotent_and_uninstall_preserves_user_hooks(self):
        plan = hooks.plan_stop_hook(
            provider="claude",
            helper_path=self.helper,
            namespace="namespace",
            enabled=True,
        )
        self.assertTrue(plan.installable)
        self.assertTrue(plan.reload_required)
        merged = hooks.merge_stop_hook(self.user_settings, plan)
        self.assertTrue(merged.changed)
        self.assertEqual(
            hooks.owned_stop_hook_count(merged.settings, provider="claude"), 1
        )
        self.assertEqual(
            self.user_settings["hooks"]["Stop"][0]["hooks"][0]["command"],
            "python3 /home/user/check.py",
        )

        merged_again = hooks.merge_stop_hook(merged.settings, plan)
        self.assertFalse(merged_again.changed)
        self.assertEqual(merged_again.settings, merged.settings)

        uninstalled = hooks.uninstall_stop_hook(
            merged.settings, provider="claude"
        )
        self.assertTrue(uninstalled.changed)
        self.assertEqual(uninstalled.settings, self.user_settings)

    def test_codex_unknown_capability_stays_hookless_until_probe_succeeds(self):
        unknown = hooks.plan_stop_hook(
            provider="codex",
            helper_path=self.helper,
            namespace="namespace",
            enabled=True,
            codex_continuation_capability="unknown",
        )
        self.assertFalse(unknown.installable)
        self.assertEqual(unknown.fallback, "herdr")
        self.assertFalse(hooks.merge_stop_hook(self.user_settings, unknown).changed)

        supported = hooks.plan_stop_hook(
            provider="codex",
            helper_path=self.helper,
            namespace="namespace",
            enabled=True,
            codex_continuation_capability="supported",
        )
        self.assertTrue(supported.installable)
        self.assertTrue(supported.trust_required)
        self.assertNotIn("args", supported.handler)
        merged = hooks.merge_stop_hook(self.user_settings, supported)
        self.assertEqual(
            hooks.owned_stop_hook_count(merged.settings, provider="codex"), 1
        )

    def test_uninstall_requires_exact_owner_marker_and_guard_shape(self):
        lookalike = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    "echo 'herdr-dev-loop:manager-sleep-guard:v1 ' "
                                    "hooks guard --provider claude"
                                ),
                            }
                        ]
                    }
                ]
            }
        }
        unchanged = hooks.uninstall_stop_hook(lookalike, provider="claude")
        self.assertFalse(unchanged.changed)
        self.assertEqual(unchanged.settings, lookalike)

    def test_guard_response_contains_only_fixed_sleep_instruction(self):
        self.assertEqual(
            hooks.render_stop_guard_response(
                provider="claude", active_roles=False, valid_wake_lease=False
            ),
            {},
        )
        response = hooks.render_stop_guard_response(
            provider="claude", active_roles=True, valid_wake_lease=False
        )
        self.assertEqual(
            response["hookSpecificOutput"]["hookEventName"], "Stop"
        )
        self.assertIn("hloop manager sleep", str(response))
        self.assertNotIn("role output", str(response))


if __name__ == "__main__":
    unittest.main()
