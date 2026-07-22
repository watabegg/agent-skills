"""Regression tests for Manager-message rediscovery and fail-closed recovery."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
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
loader = importlib.machinery.SourceFileLoader(
    "hloop_manager_message_recovery_v053", str(SCRIPT)
)
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class ManagerMessageRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-manager-message-recovery")

    def tearDown(self) -> None:
        hloop.configure_loop_namespace(self.previous_namespace)

    @staticmethod
    def state() -> dict:
        return {
            "namespace": hloop.LOOP_NAMESPACE,
            "run_id": "run-message-recovery",
            "goal_id": "manager-message-recovery",
            "phase": "dispatching",
            "base_branch": "main",
            "integration_branch": "main",
            "tasks": {},
            "reviews": {},
            "patch_reviews": {},
            "gaps": {},
            "advice": {},
            "decision_liaisons": {},
            "specification_scout_run": {},
            "plan_gap_scout_run": {},
        }

    @staticmethod
    def role_state(attempt_id: str, pane_id: str) -> dict:
        return {
            "status": "running",
            "gate_status": "running",
            "attempt_id": attempt_id,
            "active_attempt_id": attempt_id,
            "pane_id": pane_id,
            "agent_provider": "codex",
        }

    def add_unknown(
        self,
        state: dict,
        role_id: str,
        role_state: dict,
        *,
        end_marker_staged: bool = True,
    ) -> str:
        message_id, message = hloop.manager_message_envelope(
            state, role_id, role_state, "recover this message without resending"
        )
        entry = hloop.record_manager_message(
            role_state,
            "argv",
            message,
            delivery_status="unknown",
            transport=hloop.manager_message_transport_evidence(
                message,
                stage="typed-confirmed",
                pane_session_id=f"session-{role_id.replace('/', '-')}",
                enter_attempts=1,
                end_marker_staged=end_marker_staged,
            ),
        )
        self.assertEqual(entry["message_id"], message_id)
        role_state["unknown_manager_messages"] = [{"message_id": message_id}]
        return message_id

    def test_lists_unknown_records_across_every_supported_role_state(self):
        state = self.state()
        states = {
            "T001": self.role_state("T001-A001", "pane-worker"),
            "R001": self.role_state("R001-A001", "pane-reviewer"),
            "PR-T001-A001-R001": self.role_state(
                "PR-T001-A001-R001-A001", "pane-patch-reviewer"
            ),
            "G001": self.role_state("G001-A001", "pane-gap"),
            "A001/P1": self.role_state("A001-P1-A001", "pane-advisor"),
            "S001/decision": self.role_state("S001-D-A001", "pane-scout"),
            "S001/coverage": self.role_state("S001-C-A001", "pane-coverage"),
            "L-D001": self.role_state("L-D001-A001", "pane-liaison"),
        }
        state["tasks"]["T001"] = states["T001"]
        state["reviews"]["R001"] = states["R001"]
        state["patch_reviews"]["PR-T001-A001-R001"] = states[
            "PR-T001-A001-R001"
        ]
        state["gaps"]["G001"] = states["G001"]
        state["advice"]["A001"] = {
            "participants": [{**states["A001/P1"], "participant_id": "P1"}]
        }
        states["A001/P1"] = state["advice"]["A001"]["participants"][0]
        state["specification_scout_run"] = states["S001/decision"]
        state["plan_gap_scout_run"] = states["S001/coverage"]
        state["decision_liaisons"]["D001"] = states["L-D001"]

        message_ids = {
            role_id: self.add_unknown(state, role_id, role_state)
            for role_id, role_state in states.items()
        }
        rows = hloop.collect_manager_message_inventory(state, status="unknown")

        self.assertEqual({row["role_id"] for row in rows}, set(states))
        self.assertEqual(
            {row["message_id"] for row in rows}, set(message_ids.values())
        )
        self.assertTrue(all(row["identity_valid"] for row in rows))
        self.assertTrue(all(row["end_marker_staged"] is True for row in rows))
        self.assertTrue(all(row["pane_session_id"] for row in rows))
        self.assertTrue(all(row["recovery"] == "message submit" for row in rows))
        self.assertTrue(
            all("message submit" in row["recovery_command"] for row in rows)
        )

    def test_message_list_json_is_read_only_and_filters_unknown(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        reviewer = self.role_state("R001-A001", "pane-reviewer")
        state["tasks"]["T001"] = worker
        state["reviews"]["R001"] = reviewer
        worker_id = self.add_unknown(state, "T001", worker)
        reviewer_id = self.add_unknown(
            state, "R001", reviewer, end_marker_staged=False
        )
        before = copy.deepcopy(state)
        output = io.StringIO()

        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "load_state", return_value=state
        ), contextlib.redirect_stdout(output):
            code = hloop.cmd_message_list(
                SimpleNamespace(repo="/repo", status="unknown", json=True)
            )

        self.assertEqual(code, 0)
        self.assertEqual(state, before)
        rows = json.loads(output.getvalue())
        by_id = {row["message_id"]: row for row in rows}
        self.assertEqual(by_id[worker_id]["recovery"], "message submit")
        self.assertFalse(by_id[worker_id]["recovery_requires_inspection"])
        self.assertIn("--repo /repo", by_id[worker_id]["recovery_command"])
        self.assertIn(
            "--namespace test-manager-message-recovery",
            by_id[worker_id]["recovery_command"],
        )
        self.assertEqual(by_id[reviewer_id]["recovery"], "message resolve")
        self.assertTrue(by_id[reviewer_id]["recovery_requires_inspection"])
        self.assertIn("end-marker", by_id[reviewer_id]["recovery_reason"])
        self.assertEqual(by_id[reviewer_id]["recovery_command"], "")
        self.assertIn(
            "<acknowledged|superseded>",
            by_id[reviewer_id]["recovery_guidance"],
        )
        self.assertIn("--status applied", by_id[reviewer_id]["recovery_guidance"])

        parsed = hloop.build_parser().parse_args(
            ["--repo", "/repo", "message", "list", "--status", "unknown", "--json"]
        )
        self.assertIs(parsed.func, hloop.cmd_message_list)
        self.assertEqual(parsed.status, "unknown")
        self.assertTrue(parsed.json)

        self.assertFalse(hloop.command_requires_loop_lock(parsed))
        self.assertFalse(hloop.command_requires_state_schema_guard(parsed))
        self.assertFalse(hloop.should_record_first_v053_mutation(parsed))

    def test_malformed_legacy_and_orphan_unknown_records_fail_closed(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        reviewer = self.role_state("R001-A001", "pane-reviewer")
        state["tasks"]["T001"] = worker
        state["reviews"]["R001"] = reviewer

        malformed_id = str(uuid.uuid4())
        worker["manager_messages"] = [
            {
                "message_id": malformed_id,
                "delivery_status": "unknown",
                "transport_stage": "typed-confirmed",
                "pane_session_id": "session-worker",
                "end_marker_staged": True,
                "start_marker": "invalid",
                "end_marker": "invalid",
            }
        ]
        orphan_id = str(uuid.uuid4())
        worker["unknown_manager_messages"] = [
            {"message_id": malformed_id},
            {"message_id": orphan_id, "error": "legacy marker without record"},
            "malformed-marker",
        ]

        legacy_id = str(uuid.uuid4())
        payload = (
            "HERDR_LOOP_MESSAGE_START:"
            f"{state['run_id']}:R001:{reviewer['attempt_id']}:{legacy_id}\n"
            f"Manager message id: {legacy_id}\n"
            "legacy Manager message context\n"
            "legacy Manager message\n"
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        legacy_message = (
            payload
            + "HERDR_LOOP_MESSAGE_END:"
            f"{state['run_id']}:R001:{reviewer['attempt_id']}:{legacy_id}:{digest}\n"
        )
        legacy_entry = hloop.record_manager_message(
            reviewer,
            "legacy",
            legacy_message,
            delivery_status="unknown",
            transport=hloop.manager_message_transport_evidence(
                legacy_message,
                stage="typed-confirmed",
                pane_session_id="session-reviewer",
                enter_attempts=1,
                end_marker_staged=False,
            ),
        )
        reviewer["unknown_manager_messages"] = [{"message_id": legacy_id}]

        rows = hloop.collect_manager_message_inventory(state, status="unknown")
        by_id = {row["message_id"]: row for row in rows if row["message_id"]}
        self.assertFalse(by_id[malformed_id]["identity_valid"])
        self.assertEqual(by_id[malformed_id]["recovery"], "inspect state")
        self.assertEqual(by_id[orphan_id]["record_status"], "missing")
        self.assertEqual(by_id[orphan_id]["recovery"], "inspect state")
        self.assertTrue(hloop.manager_message_record_identity(legacy_entry))
        self.assertEqual(by_id[legacy_id]["recovery"], "message resolve")
        self.assertTrue(
            all(row["recovery"] != "message submit" for row in rows)
        )

        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "load_state", return_value=state
        ), mock.patch.object(hloop, "save_state") as save_state, self.assertRaisesRegex(
            hloop.HLoopError, "identity is incomplete or invalid"
        ):
            hloop.cmd_message_resolve(
                SimpleNamespace(
                    repo="/repo",
                    agent_id="T001",
                    message_id=malformed_id,
                    status="acknowledged",
                    result=None,
                    error=None,
                )
            )
        save_state.assert_not_called()
        self.assertEqual(worker["manager_messages"][0]["delivery_status"], "unknown")

    def test_missing_status_and_malformed_container_remain_blocking_unknowns(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        reviewer = self.role_state("R001-A001", "pane-reviewer")
        gap = self.role_state("G001-A001", "pane-gap")
        state["tasks"]["T001"] = worker
        state["reviews"]["R001"] = reviewer
        state["gaps"]["G001"] = gap
        message_id, message = hloop.manager_message_envelope(
            state, "T001", worker, "possibly typed"
        )
        entry = hloop.record_manager_message(
            worker,
            "argv",
            message,
            delivery_status="unknown",
            transport=hloop.manager_message_transport_evidence(
                message,
                stage="send-text-started",
                pane_session_id="session-worker",
                enter_attempts=0,
                end_marker_staged=False,
            ),
        )
        entry.pop("delivery_status")
        reviewer["manager_messages"] = {"not": "a list"}
        gap["unknown_manager_messages"] = {"not": "a list"}

        rows = hloop.collect_manager_message_inventory(state, status="unknown")

        self.assertEqual(len(rows), 3)
        by_role = {row["role_id"]: row for row in rows}
        self.assertEqual(by_role["T001"]["message_id"], message_id)
        self.assertEqual(by_role["T001"]["delivery_status"], "malformed")
        self.assertEqual(by_role["T001"]["recovery_command"], "")
        self.assertTrue(by_role["T001"]["recovery_requires_inspection"])
        self.assertEqual(
            by_role["R001"]["record_status"], "malformed-container"
        )
        self.assertEqual(by_role["R001"]["delivery_status"], "malformed")
        self.assertEqual(
            by_role["G001"]["record_status"], "malformed-unknown-container"
        )
        completion_errors = hloop.unresolved_manager_message_completion_errors(state)
        self.assertEqual(len(completion_errors), 1)
        self.assertIn("T001", completion_errors[0])
        self.assertIn("R001", completion_errors[0])
        self.assertIn("G001", completion_errors[0])

    def test_orphan_and_malformed_pending_markers_block_completion(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        reviewer = self.role_state("R001-A001", "pane-reviewer")
        state["tasks"]["T001"] = worker
        state["reviews"]["R001"] = reviewer
        worker["pending_manager_messages"] = [
            {
                "message_id": str(uuid.uuid4()),
                "status": "undelivered",
                "pending_path": ".ai/orphan-message.md",
            }
        ]
        reviewer["pending_manager_messages"] = {"not": "a list"}

        rows = hloop.collect_manager_message_inventory(state, status="unknown")

        self.assertEqual(len(rows), 2)
        by_role = {row["role_id"]: row for row in rows}
        self.assertEqual(
            by_role["T001"]["record_status"], "malformed-pending-marker"
        )
        self.assertEqual(
            by_role["R001"]["record_status"], "malformed-pending-container"
        )
        self.assertTrue(all(row["delivery_status"] == "malformed" for row in rows))
        completion_errors = hloop.unresolved_manager_message_completion_errors(state)
        self.assertEqual(len(completion_errors), 1)
        self.assertIn("T001", completion_errors[0])
        self.assertIn("R001", completion_errors[0])

    def test_terminal_undelivered_message_requires_explicit_supersede(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        worker["status"] = "merged"
        worker["gate_status"] = "reported"
        state["tasks"]["T001"] = worker
        message_id, message = hloop.manager_message_envelope(
            state, "T001", worker, "do not replay after the role ends"
        )
        entry = hloop.record_manager_message(
            worker,
            "argv",
            message,
            delivery_status="undelivered",
            pending_path=".ai/pending-terminal.md",
        )
        worker["pending_manager_messages"] = [
            {
                "message_id": message_id,
                "status": "undelivered",
                "pending_path": ".ai/pending-terminal.md",
            }
        ]

        rows = hloop.collect_manager_message_inventory(state)

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["role_running"])
        self.assertEqual(rows[0]["recovery"], "message resolve")
        self.assertIn("--status superseded", rows[0]["recovery_command"])

        with mock.patch.object(
            hloop, "repo_root", return_value=Path("/repo")
        ), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(
            hloop, "save_state"
        ), mock.patch.object(
            hloop, "journal"
        ), mock.patch.object(
            hloop, "send_agent_tui_message"
        ) as send, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ) as stderr:
            drain_code = hloop.cmd_message_drain(
                SimpleNamespace(
                    repo="/repo",
                    timeout_ms=1,
                    input_settle_ms=0,
                    submit_verify_ms=1,
                    submit_attempts=1,
                )
            )

        self.assertEqual(drain_code, 3)
        send.assert_not_called()
        self.assertIn("--status superseded", stderr.getvalue())

        with mock.patch.object(
            hloop, "repo_root", return_value=Path("/repo")
        ), mock.patch.object(
            hloop, "load_state", return_value=state
        ), mock.patch.object(
            hloop, "save_state"
        ) as save_state, mock.patch.object(
            hloop, "journal"
        ), contextlib.redirect_stdout(io.StringIO()):
            code = hloop.cmd_message_resolve(
                SimpleNamespace(
                    repo="/repo",
                    agent_id="T001",
                    message_id=message_id,
                    status="superseded",
                    result=None,
                    error=None,
                )
            )

        self.assertEqual(code, 0)
        save_state.assert_called_once()
        self.assertEqual(entry["delivery_status"], "superseded")
        self.assertEqual(worker["pending_manager_messages"], [])
        self.assertEqual(hloop.unresolved_manager_message_completion_errors(state), [])

    def test_submit_rejects_nonstaged_end_marker_before_pane_control(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        state["tasks"]["T001"] = worker
        message_id = self.add_unknown(
            state, "T001", worker, end_marker_staged=False
        )

        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(hloop, "run_cmd") as run_cmd, self.assertRaisesRegex(
            hloop.HLoopError, "end_marker_staged=true"
        ):
            hloop.cmd_message_submit(
                SimpleNamespace(
                    repo="/repo",
                    agent_id="T001",
                    message_id=message_id,
                    input_settle_ms=0,
                    submit_verify_ms=0,
                )
            )

        run_cmd.assert_not_called()

    def test_corrupt_enter_attempts_are_malformed_and_rejected_before_control(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        state["tasks"]["T001"] = worker
        message_id = self.add_unknown(state, "T001", worker)
        entry = hloop.manager_message_by_id(worker, message_id)
        self.assertIsNotNone(entry)
        entry["enter_attempts"] = "corrupt"

        rows = hloop.collect_manager_message_inventory(state, status="unknown")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delivery_status"], "malformed")
        self.assertEqual(rows[0]["recovery"], "inspect state")
        self.assertIn("Enter attempts", rows[0]["transport_evidence_error"])

        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(hloop, "run_cmd") as run_cmd, self.assertRaisesRegex(
            hloop.HLoopError, "complete durable transport identity"
        ):
            hloop.cmd_message_submit(
                SimpleNamespace(
                    repo="/repo",
                    agent_id="T001",
                    message_id=message_id,
                    input_settle_ms=0,
                    submit_verify_ms=0,
                )
            )

        run_cmd.assert_not_called()
        self.assertEqual(entry["enter_attempts"], "corrupt")

    def test_submit_success_preserves_malformed_marker_and_saves(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        state["tasks"]["T001"] = worker
        message_id = self.add_unknown(state, "T001", worker)
        worker["manager_messages"].insert(0, "legacy-malformed-record")
        worker["unknown_manager_messages"].append("legacy-malformed-marker")
        entry = hloop.manager_message_by_id(worker, message_id)
        self.assertIsNotNone(entry)
        identity = hloop.manager_message_record_identity(entry)
        self.assertIsNotNone(identity)
        pane_text = f"{entry['end_marker']}\n{identity['ack_marker']}\n"

        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(
            hloop,
            "same_agent_session_observation",
            return_value=({"agent_status": "idle"}, pane_text),
        ), mock.patch.object(hloop, "save_state") as save_state, mock.patch.object(
            hloop, "journal"
        ), mock.patch.object(hloop, "run_cmd") as run_cmd:
            code = hloop.cmd_message_submit(
                SimpleNamespace(
                    repo="/repo",
                    agent_id="T001",
                    message_id=message_id,
                    input_settle_ms=0,
                    submit_verify_ms=0,
                )
            )

        self.assertEqual(code, 0)
        save_state.assert_called_once()
        run_cmd.assert_not_called()
        self.assertEqual(worker["unknown_manager_messages"], ["legacy-malformed-marker"])
        self.assertEqual(worker["manager_messages"][0], "legacy-malformed-record")
        self.assertEqual(entry["delivery_status"], "acknowledged")

    def test_submit_rejects_malformed_event_container_before_pane_observation(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        state["tasks"]["T001"] = worker
        message_id = self.add_unknown(state, "T001", worker)
        worker["manager_message_events"] = {"not": "a list"}

        with mock.patch.object(hloop, "repo_root", return_value=Path("/repo")), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(
            hloop, "same_agent_session_observation"
        ) as observe, self.assertRaisesRegex(
            hloop.HLoopError, "manager_message_events must be a list"
        ):
            hloop.cmd_message_submit(
                SimpleNamespace(
                    repo="/repo",
                    agent_id="T001",
                    message_id=message_id,
                    input_settle_ms=0,
                    submit_verify_ms=0,
                )
            )

        observe.assert_not_called()

    def test_unrelated_working_pane_without_marker_is_not_submission_evidence(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        state["tasks"]["T001"] = worker
        message_id = self.add_unknown(state, "T001", worker)
        entry = hloop.manager_message_by_id(worker, message_id)
        self.assertIsNotNone(entry)

        observed = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "working"},
            "completely unrelated pane text",
        )

        self.assertEqual(observed, "marker-missing")

        stale_observed = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "working"},
            f"old transcript\n{entry['end_marker']}\n• unrelated later work",
        )
        self.assertEqual(stale_observed, "ambiguous")

    def test_idle_long_staged_input_accepts_exact_end_marker_when_start_is_clipped(self):
        state = self.state()
        worker = self.role_state("T001-A001", "pane-worker")
        state["tasks"]["T001"] = worker
        message_id = self.add_unknown(state, "T001", worker)
        entry = hloop.manager_message_by_id(worker, message_id)
        self.assertIsNotNone(entry)
        entry["transport_stage"] = "send-text-started"

        observed = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "idle"},
            "long input tail whose start is outside the viewport\n"
            + entry["end_marker"]
            + "\n\n─ gpt-test max · /tmp/worktree\n",
        )

        self.assertEqual(observed, "staged-idle")

        footer_without_rule = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "idle"},
            entry["end_marker"] + "\n\ngpt-test max · /tmp/worktree\n",
        )
        self.assertEqual(footer_without_rule, "staged-idle")

        done_observed = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "done"},
            entry["end_marker"] + "\n\ngpt-test max · /tmp/worktree\n",
        )
        self.assertEqual(done_observed, "staged-idle")

        entry["end_marker_staged"] = False
        unproven = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "idle"},
            "long input tail whose start is outside the viewport\n"
            + entry["end_marker"]
            + "\n\n─ gpt-test max · /tmp/worktree\n",
        )
        self.assertEqual(unproven, "ambiguous")

        entry["end_marker_staged"] = True
        transcript_after_marker = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "idle"},
            entry["end_marker"] + "\n› unrelated later prompt\n",
        )
        self.assertEqual(transcript_after_marker, "ambiguous")

        footer_shaped_transcript = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "idle"},
            entry["end_marker"] + "\nstatus · /tmp/worktree\n",
        )
        self.assertEqual(footer_shaped_transcript, "ambiguous")

        submitted = hloop.recorded_manager_message_observation(
            "codex",
            entry,
            {"agent_status": "working"},
            entry["end_marker"]
            + "\n\n• "
            + hloop.manager_message_record_identity(entry)["ack_marker"]
            + "\n",
        )
        self.assertEqual(submitted, "submitted-ack")

    def test_confirmed_contract_message_preserves_artifact_digest_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            contract_path = hloop.task_file(repo, "T001")
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text("original task contract\n", encoding="utf-8")
            artifact_digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()
            message_digest = hashlib.sha256(b"approved contract extension").hexdigest()
            message_id = "22222222-2222-4222-8222-222222222222"
            task_state = {
                "task_contract_digest": artifact_digest,
                "active_report_contract_digest": message_digest,
                "semantic_ack_barrier": {
                    "kind": "message",
                    "message_id": message_id,
                    "digest": message_digest,
                    "report_identity_status": "bound",
                },
            }
            entry = {"message_id": message_id}

            self.assertTrue(
                hloop.apply_contract_message_digest_projection(task_state, entry)
            )
            self.assertEqual(task_state["task_contract_digest"], message_digest)
            self.assertEqual(
                task_state["task_contract_artifact_digest"], artifact_digest
            )
            self.assertFalse(
                hloop.apply_contract_message_digest_projection(task_state, entry)
            )
            self.assertEqual(
                hloop.exact_task_contract_digest(repo, "T001", task_state),
                (artifact_digest, f"sha256:{message_digest}"),
            )

            contract_path.write_text("tampered task contract\n", encoding="utf-8")
            with self.assertRaisesRegex(hloop.HLoopError, "digest drift"):
                hloop.exact_task_contract_digest(repo, "T001", task_state)

    def test_status_inventory_surfaces_unknown_as_p1_next_action(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            state = self.state()
            worker = self.role_state("T001-A001", "pane-worker")
            reviewer = self.role_state("R001-A001", "pane-reviewer")
            state["tasks"]["T001"] = worker
            state["reviews"]["R001"] = reviewer
            message_id = self.add_unknown(state, "T001", worker)
            reviewer["manager_messages"] = {"not": "a list"}
            hloop.ensure_loop_dirs(repo)
            hloop.state_path(repo).write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            inventory = hloop.collect_loop_inventory(repo, probe_panes=False)

        issues = [
            item
            for item in inventory["issues"]
            if item["code"] == "manager-message-delivery-unknown"
        ]
        self.assertEqual(len(issues), 2)
        self.assertTrue(all(issue["severity"] == "P1" for issue in issues))
        unknown_issue = next(
            issue for issue in issues if message_id in issue["subject"]
        )
        malformed_issue = next(
            issue for issue in issues if "R001/<missing-id>" in issue["subject"]
        )
        self.assertIn("message submit", unknown_issue["action"])
        self.assertIn("do not submit", malformed_issue["action"])
        self.assertIn(unknown_issue["action"], inventory["next_actions"])
        self.assertIn(malformed_issue["action"], inventory["next_actions"])
        self.assertEqual(inventory["counts"]["unknown_manager_messages"], 2)
        self.assertEqual(
            {row["role_id"] for row in inventory["manager_messages"]},
            {"T001", "R001"},
        )


if __name__ == "__main__":
    unittest.main()
