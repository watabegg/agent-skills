from __future__ import annotations

import hashlib
import json
import multiprocessing
import queue
import sqlite3
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib import broker, events  # noqa: E402


CREATED_AT = "2026-07-15T00:00:00+00:00"
EXPIRES_AT = "2026-07-16T00:00:00+00:00"


def initialize_broker_after_barrier(root, barrier, result_queue):
    """Open one shared broker root from a spawned child process."""

    try:
        barrier.wait(timeout=30)
        broker.BrokerStore(Path(root), timeout_seconds=30)
    except BaseException as exc:  # pragma: no cover - asserted via the queue
        result_queue.put(f"{type(exc).__name__}: {exc}")
    else:
        result_queue.put("ok")


def report(
    *,
    summary: str = "契約を確認しました",
    report_type: str = "ack",
    needs_manager: bool | None = None,
) -> dict[str, object]:
    if needs_manager is None:
        needs_manager = report_type in {"ack", "attention"}
    value: dict[str, object] = {
        "run_id": "run-001",
        "role_id": "T003",
        "attempt_id": "T003-A001",
        "task_contract_digest": hashlib.sha256(b"contract").hexdigest(),
        "type": report_type,
        "stage": "prepared",
        "summary": summary,
        "next": "実装を開始します",
        "needs_manager": needs_manager,
        "evidence_refs": ["tasks/T003.md"],
        "created_at": CREATED_AT,
    }
    if report_type == "ack":
        value.update(
            {
                "understood_goal": "report broker primitivesを実装する",
                "scope": ["events.py", "broker.py"],
                "acceptance": ["重複eventを一度だけ保存する"],
                "approach": "純粋validationとtransactional storageを分離する",
            }
        )
    elif report_type == "milestone":
        value["risks"] = ["broker restart後の順序を引き続き確認する"]
    elif report_type == "attention":
        value.update(
            {
                "impact": "Managerへcompletionを配送できない",
                "attempted": ["brokerを再起動した"],
                "options": ["spoolを再送する", "taskを停止する"],
                "recommendation": "spoolを再送する",
                "blocked_scope": ["completion reportの配送"],
            }
        )
    elif report_type == "completion":
        value.update(
            {
                "artifact": "results/T003/result.md",
                "head_sha": "a" * 40,
                "validation_results": ["focused events suite passed"],
                "residual_risks": ["Manager final QAは別gateである"],
                "handoff": "artifactとhead SHAを照合してharvestする",
            }
        )
    return value


def client_event(
    *, summary: str = "契約を確認しました", event_id: str | None = None
) -> dict[str, object]:
    return events.prepare_client_event(
        report(summary=summary), event_id=event_id or str(uuid.uuid4())
    )


def report_authentication(event: dict[str, object], token: str = "report-token"):
    return {
        "run_id": event["run_id"],
        "role_id": event["role_id"],
        "attempt_id": event["attempt_id"],
        "task_contract_digest": event["task_contract_digest"],
        "token": token,
    }


class ReportValidationTests(unittest.TestCase):
    def test_client_assigns_uuid_and_digest_while_sequence_is_broker_only(self):
        prepared = events.prepare_client_event(
            report(), event_id="b06cb72e-504a-44c8-b86c-28c25f2e9b3a"
        )

        self.assertEqual(
            prepared["payload_digest"], events.payload_digest(report())
        )
        self.assertEqual(events.validate_client_event(prepared), prepared)
        self.assertNotIn("sequence", prepared)

        forged = {**prepared, "sequence": 99}
        with self.assertRaisesRegex(
            events.ReportValidationError, "broker-assigned"
        ):
            events.validate_client_event(forged)

    def test_report_schema_rejects_missing_ack_contract_and_digest_tampering(self):
        incomplete = report()
        del incomplete["approach"]
        with self.assertRaisesRegex(events.ReportValidationError, "ack reports require"):
            events.validate_report(incomplete)

        empty_scope = report()
        empty_scope["scope"] = []
        with self.assertRaisesRegex(events.ReportValidationError, "non-empty scope"):
            events.validate_report(empty_scope)

        prepared = events.prepare_client_event(report())
        prepared["summary"] = "payloadだけ変更"
        with self.assertRaisesRegex(events.ReportValidationError, "does not match"):
            events.validate_client_event(prepared)

    def test_attention_is_actionable_and_milestone_cannot_smuggle_manager_work(self):
        with self.assertRaisesRegex(events.ReportValidationError, "needs_manager=true"):
            events.validate_report(
                report(report_type="attention", needs_manager=False)
            )
        with self.assertRaisesRegex(events.ReportValidationError, "type=attention"):
            events.validate_report(
                report(report_type="milestone", needs_manager=True)
            )

    def test_each_report_type_requires_nonempty_semantic_payload(self):
        required_field = {
            "ack": "understood_goal",
            "milestone": "risks",
            "attention": "impact",
            "completion": "artifact",
        }
        for report_type, field in required_field.items():
            with self.subTest(report_type=report_type, condition="valid"):
                normalized = events.validate_report(report(report_type=report_type))
                self.assertEqual(normalized["type"], report_type)
            with self.subTest(report_type=report_type, condition="missing"):
                missing = report(report_type=report_type)
                del missing[field]
                with self.assertRaisesRegex(
                    events.ReportValidationError, f"{report_type} reports require"
                ):
                    events.validate_report(missing)
            with self.subTest(report_type=report_type, condition="empty"):
                empty = report(report_type=report_type)
                empty[field] = [] if field == "risks" else ""
                with self.assertRaisesRegex(events.ReportValidationError, field):
                    events.validate_report(empty)

        completion = events.validate_report(report(report_type="completion"))
        self.assertEqual(completion["artifact"], "results/T003/result.md")
        self.assertEqual(completion["head_sha"], "a" * 40)
        self.assertTrue(completion["validation_results"])
        self.assertTrue(completion["residual_risks"])
        self.assertTrue(completion["handoff"])

        invalid_head = report(report_type="completion")
        invalid_head["head_sha"] = "not-a-sha"
        with self.assertRaisesRegex(events.ReportValidationError, "head_sha"):
            events.validate_report(invalid_head)

        unknown = report(report_type="milestone")
        unknown["unreviewed_command"] = "run me"
        with self.assertRaisesRegex(events.ReportValidationError, "unknown fields"):
            events.validate_report(unknown)


class BrokerStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = broker.BrokerStore(self.root / "broker")
        self.report_token = "report-token"
        sample = client_event()
        with self.store.transaction() as transaction:
            self.store.register_active_role(
                transaction,
                **report_authentication(sample, self.report_token),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_concurrent_first_open_is_serialized_across_processes(self):
        process_count = 8
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(process_count)
        result_queue = context.Queue()
        root = self.root / "concurrent-broker"
        processes = [
            context.Process(
                target=initialize_broker_after_barrier,
                args=(str(root), barrier, result_queue),
            )
            for _ in range(process_count)
        ]

        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=45)
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

            results = []
            for _ in processes:
                try:
                    results.append(result_queue.get(timeout=2))
                except queue.Empty:
                    results.append("missing child result")
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            result_queue.close()
            result_queue.join_thread()

        self.assertEqual(
            [process.exitcode for process in processes], [0] * process_count
        )
        self.assertEqual(results, ["ok"] * process_count)
        connection = sqlite3.connect(root / "broker.sqlite3")
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM broker_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                str(broker.BROKER_SCHEMA_VERSION),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM broker_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_event_inbox_and_sequence_are_transactional_and_idempotent(self):
        event_id = "28b79cf0-8e32-4b89-88af-e332ee5a5dbe"
        first = client_event(event_id=event_id)
        second = client_event(summary="次の報告")

        with self.store.transaction() as transaction:
            accepted = self.store.accept_report(transaction, first)
            duplicate = self.store.accept_report(transaction, first)
            accepted_second = self.store.accept_report(transaction, second)

            self.assertTrue(accepted.inserted)
            self.assertFalse(duplicate.inserted)
            self.assertEqual(accepted.event["sequence"], duplicate.event["sequence"])
            self.assertEqual(accepted_second.event["sequence"], 2)
            self.assertEqual(len(self.store.inbox(transaction)), 2)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                transaction.connection.execute(
                    "UPDATE events SET role_id = 'tampered' WHERE sequence = 1"
                )

        conflicting = client_event(summary="異なるpayload", event_id=event_id)
        with self.store.transaction() as transaction:
            with self.assertRaises(broker.IdempotencyConflict):
                self.store.accept_report(transaction, conflicting)
            self.assertEqual([item["sequence"] for item in self.store.events(transaction)], [1, 2])

    def test_unread_milestone_projection_coalesces_duplicates_but_keeps_attention_and_append_only_events(self):
        milestone_a_first = events.prepare_client_event(
            report(summary="進捗1", report_type="milestone"), event_id=str(uuid.uuid4())
        )
        milestone_a_second = events.prepare_client_event(
            report(summary="進捗1", report_type="milestone"), event_id=str(uuid.uuid4())
        )
        milestone_b = events.prepare_client_event(
            report(summary="進捗2", report_type="milestone"), event_id=str(uuid.uuid4())
        )
        attention_first = events.prepare_client_event(
            report(summary="進捗1", report_type="attention"), event_id=str(uuid.uuid4())
        )
        attention_second = events.prepare_client_event(
            report(summary="進捗1", report_type="attention"), event_id=str(uuid.uuid4())
        )

        with self.store.transaction() as transaction:
            for candidate in (
                milestone_a_first,
                milestone_a_second,
                milestone_b,
                attention_first,
                attention_second,
            ):
                self.store.accept_report(transaction, candidate)

            self.assertEqual(len(self.store.events(transaction)), 5)
            self.assertEqual(len(self.store.inbox(transaction)), 5)

            unread = self.store.unconsumed_inbox(transaction, run_id="run-001")

        self.assertEqual(
            [item["event_id"] for item in unread],
            [
                milestone_a_second["event_id"],
                milestone_b["event_id"],
                attention_first["event_id"],
                attention_second["event_id"],
            ],
        )

    def test_acknowledging_coalesced_milestone_drains_displayed_duplicates_without_changing_wakes(self):
        milestone_first = events.prepare_client_event(
            report(summary="進捗1", report_type="milestone"),
            event_id=str(uuid.uuid4()),
        )
        milestone_displayed = events.prepare_client_event(
            report(summary="進捗1", report_type="milestone"),
            event_id=str(uuid.uuid4()),
        )
        attention = events.prepare_client_event(
            report(summary="進捗1", report_type="attention"),
            event_id=str(uuid.uuid4()),
        )

        with self.store.transaction() as transaction:
            for candidate in (milestone_first, milestone_displayed, attention):
                self.store.accept_report(transaction, candidate)
            lease = self.store.register_wake_lease(
                transaction,
                run_id="run-001",
                manager_session_id="session-1",
                pane_id="wH:p1",
                expires_at=EXPIRES_AT,
                created_at=CREATED_AT,
            )
            wakes = self.store.enqueue_unacknowledged_for_lease(
                transaction,
                run_id="run-001",
                lease_generation=lease["generation"],
                manager_session_id="session-1",
                pane_id="wH:p1",
                report_types=frozenset({"ack", "attention", "completion"}),
                created_at=CREATED_AT,
            )

            self.assertEqual(
                [
                    item["event_id"]
                    for item in self.store.unconsumed_inbox(
                        transaction, run_id="run-001"
                    )
                ],
                [milestone_displayed["event_id"], attention["event_id"]],
            )
            self.assertEqual(
                [item.wake["event_id"] for item in wakes],
                [attention["event_id"]],
            )

            acknowledged = self.store.acknowledge_inbox(
                transaction,
                event_id=milestone_displayed["event_id"],
                run_id="run-001",
                acknowledged_at=CREATED_AT,
            )

            self.assertEqual(
                acknowledged,
                broker.InboxAcknowledgement(True, "acknowledged"),
            )
            self.assertEqual(
                [
                    item["event_id"]
                    for item in self.store.unconsumed_inbox(
                        transaction, run_id="run-001"
                    )
                ],
                [attention["event_id"]],
            )
            self.assertEqual(
                [
                    item["event_id"]
                    for item in self.store.pending_wakes(
                        transaction, at=CREATED_AT
                    )
                ],
                [attention["event_id"]],
            )
            acknowledgement_ids = transaction.connection.execute(
                "SELECT event_id FROM inbox_acknowledgements ORDER BY acknowledgement_id"
            ).fetchall()
            self.assertEqual(
                [row["event_id"] for row in acknowledgement_ids],
                [milestone_first["event_id"], milestone_displayed["event_id"]],
            )

            milestone_later = events.prepare_client_event(
                report(summary="進捗1", report_type="milestone"),
                event_id=str(uuid.uuid4()),
            )
            self.store.accept_report(transaction, milestone_later)
            self.assertEqual(
                [
                    item["event_id"]
                    for item in self.store.unconsumed_inbox(
                        transaction, run_id="run-001"
                    )
                ],
                [attention["event_id"], milestone_later["event_id"]],
            )
            self.assertEqual(len(self.store.events(transaction)), 4)
            self.assertEqual(len(self.store.inbox(transaction)), 4)

    def test_exception_rolls_back_event_and_inbox_together(self):
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.store.transaction() as transaction:
                self.store.accept_report(transaction, client_event())
                raise RuntimeError("rollback")

        with self.store.transaction() as transaction:
            self.assertEqual(self.store.events(transaction), [])
            self.assertEqual(self.store.inbox(transaction), [])

    def test_lease_generation_and_duplicate_wake_consumption(self):
        event = client_event()
        with self.store.transaction() as transaction:
            stored = self.store.accept_report(transaction, event)
            lease = self.store.register_wake_lease(
                transaction,
                run_id="run-001",
                manager_session_id="session-1",
                pane_id="wH:p1",
                expires_at=EXPIRES_AT,
                created_at=CREATED_AT,
            )
            wake = self.store.enqueue_wake(
                transaction,
                event_id=stored.event["event_id"],
                lease_generation=lease["generation"],
                manager_session_id="session-1",
                pane_id="wH:p1",
                created_at=CREATED_AT,
            )
            duplicate = self.store.enqueue_wake(
                transaction,
                event_id=stored.event["event_id"],
                lease_generation=lease["generation"],
                created_at=CREATED_AT,
            )
            self.assertTrue(wake.inserted)
            self.assertFalse(duplicate.inserted)
            self.assertNotIn(str(stored.event["summary"]), wake.wake["message"])
            self.assertIn(
                f"hloop inbox show {stored.event['event_id']}", wake.wake["message"]
            )

            consumed = self.store.consume_wake(
                transaction,
                event_id=stored.event["event_id"],
                lease_generation=lease["generation"],
                consumed_at=CREATED_AT,
            )
            consumed_again = self.store.consume_wake(
                transaction,
                event_id=stored.event["event_id"],
                lease_generation=lease["generation"],
                consumed_at=CREATED_AT,
            )
            self.assertEqual(consumed, broker.WakeConsumption(True, "consumed"))
            self.assertEqual(consumed_again, broker.WakeConsumption(False, "duplicate"))
            self.assertEqual(self.store.pending_wakes(transaction), [])

        with self.store.transaction() as transaction:
            newer = self.store.register_wake_lease(
                transaction,
                run_id="run-001",
                manager_session_id="session-1",
                pane_id="wH:p1",
                expires_at=EXPIRES_AT,
                created_at=CREATED_AT,
            )
            self.assertEqual(newer["generation"], 2)
            self.assertFalse(
                self.store.lease_generation_matches(
                    transaction,
                    run_id="run-001",
                    generation=1,
                    manager_session_id="session-1",
                    pane_id="wH:p1",
                    at=CREATED_AT,
                )
            )

    def test_stale_wakes_remain_unprocessed_and_reenqueue_on_fresh_lease(self):
        first_event = client_event(summary="旧lease向け")
        second_event = client_event(summary="新lease向け")
        with self.store.transaction() as transaction:
            first = self.store.accept_report(transaction, first_event)
            second = self.store.accept_report(transaction, second_event)
            old_lease = self.store.register_wake_lease(
                transaction,
                run_id="run-001",
                manager_session_id="session-1",
                pane_id="wH:p1",
                expires_at=EXPIRES_AT,
                created_at=CREATED_AT,
            )
            old_wake = self.store.enqueue_wake(
                transaction,
                event_id=first.event["event_id"],
                lease_generation=old_lease["generation"],
                created_at=CREATED_AT,
            )
            new_lease = self.store.register_wake_lease(
                transaction,
                run_id="run-001",
                manager_session_id="session-2",
                pane_id="wH:p2",
                expires_at=EXPIRES_AT,
                created_at=CREATED_AT,
            )
            new_wake = self.store.enqueue_wake(
                transaction,
                event_id=second.event["event_id"],
                lease_generation=new_lease["generation"],
                created_at=CREATED_AT,
            )
            requeued = self.store.enqueue_unacknowledged_for_lease(
                transaction,
                run_id="run-001",
                lease_generation=new_lease["generation"],
                manager_session_id="session-2",
                pane_id="wH:p2",
                created_at=CREATED_AT,
            )

            pending = self.store.pending_wakes(transaction, at=CREATED_AT)
            self.assertEqual(
                [(item["event_id"], item["lease_generation"]) for item in pending],
                [
                    (new_wake.wake["event_id"], new_lease["generation"]),
                    (old_wake.wake["event_id"], new_lease["generation"]),
                ],
            )
            self.assertEqual(len(requeued), 2)
            terminals = transaction.connection.execute(
                """
                SELECT event_id, lease_generation FROM wake_consumptions
                ORDER BY consumption_id
                """
            ).fetchall()
            self.assertEqual(list(terminals), [])
            self.assertEqual(
                self.store.consume_wake(
                    transaction,
                    event_id=old_wake.wake["event_id"],
                    lease_generation=old_lease["generation"],
                    consumed_at=CREATED_AT,
                ),
                broker.WakeConsumption(False, "stale-lease"),
            )
            self.assertEqual(
                self.store.acknowledge_inbox(
                    transaction,
                    event_id=old_wake.wake["event_id"],
                    run_id="run-001",
                    acknowledged_at=CREATED_AT,
                ),
                broker.InboxAcknowledgement(True, "acknowledged"),
            )
            self.assertEqual(
                self.store.acknowledge_inbox(
                    transaction,
                    event_id=old_wake.wake["event_id"],
                    run_id="run-001",
                    acknowledged_at=CREATED_AT,
                ),
                broker.InboxAcknowledgement(False, "duplicate"),
            )
            self.assertEqual(
                [item["event_id"] for item in self.store.pending_wakes(transaction, at=CREATED_AT)],
                [new_wake.wake["event_id"]],
            )
            self.assertEqual(
                self.store.pending_wakes(transaction, at=EXPIRES_AT), []
            )
            terminal_count = transaction.connection.execute(
                "SELECT COUNT(*) AS count FROM wake_consumptions"
            ).fetchone()["count"]
            self.assertEqual(terminal_count, 0)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                transaction.connection.execute(
                    "UPDATE inbox_acknowledgements SET acknowledged_at = ?",
                    (EXPIRES_AT,),
                )

    def test_spool_replay_deletes_only_after_commit_and_replays_idempotently(self):
        spool = self.root / "spool"
        first = client_event(event_id="ffffffff-ffff-4fff-8fff-ffffffffffff")
        second = client_event(event_id="00000000-0000-4000-8000-000000000001")
        broker.spool_client_event(
            spool, first, authentication=report_authentication(first, self.report_token)
        )
        with self.assertRaises(broker.IdempotencyConflict):
            broker.spool_client_event(
                spool,
                client_event(
                    event_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
                    summary="same id, different report",
                ),
                authentication=report_authentication(first, self.report_token),
            )
        broker.spool_client_event(
            spool, second, authentication=report_authentication(second, self.report_token)
        )

        restarted = broker.BrokerStore(self.root / "broker")
        with restarted.transaction() as transaction:
            replayed = restarted.replay_spool(transaction, spool)
            self.assertEqual([item.event["sequence"] for item in replayed], [1, 2])
            self.assertEqual(
                [item.event["event_id"] for item in replayed],
                [first["event_id"], second["event_id"]],
            )
            self.assertEqual(len(list(spool.glob("*.json"))), 2)
        self.assertEqual(list(spool.glob("*.json")), [])

        broker.spool_client_event(
            spool, first, authentication=report_authentication(first, self.report_token)
        )
        with restarted.transaction() as transaction:
            replayed = restarted.replay_spool(transaction, spool)
            self.assertFalse(replayed[0].inserted)
            self.assertEqual(replayed[0].event["sequence"], 1)
        self.assertEqual(list(spool.glob("*.json")), [])

    def test_spool_replay_quarantines_conflict_and_replays_later_valid_entry(self):
        spool = self.root / "spool"
        conflict_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        stored = client_event(event_id=conflict_id)
        conflicting = client_event(
            event_id=conflict_id,
            summary="same id, different spooled report",
        )
        valid = client_event(event_id="00000000-0000-4000-8000-000000000001")

        with self.store.transaction() as transaction:
            self.store.accept_report(transaction, stored)
        broker.spool_client_event(
            spool,
            conflicting,
            authentication=report_authentication(conflicting, self.report_token),
        )
        broker.spool_client_event(
            spool,
            valid,
            authentication=report_authentication(valid, self.report_token),
        )

        with self.store.transaction() as transaction:
            replayed = self.store.replay_spool(transaction, spool)
        self.assertEqual(
            [item.event["event_id"] for item in replayed],
            [valid["event_id"]],
        )
        with self.store.transaction() as transaction:
            self.assertEqual(
                [item["event_id"] for item in self.store.events(transaction)],
                [stored["event_id"], valid["event_id"]],
            )
        self.assertEqual(list(spool.glob("*.json")), [])
        quarantine_dir = spool / "quarantine"
        quarantined = [
            path
            for path in quarantine_dir.glob("*.json")
            if not path.name.endswith(".audit.json")
        ]
        self.assertEqual([path.name for path in quarantined], [f"{conflict_id}.json"])
        audit = json.loads(
            (quarantine_dir / f"{conflict_id}.json.audit.json").read_text()
        )
        self.assertEqual(audit["reason"], "idempotency-conflict")
        self.assertEqual(audit["event_id"], conflict_id)
        self.assertIn("different digest", audit["detail"])

    def test_spool_replay_quarantines_unknown_role_and_stale_attempt_without_blocking_valid_entries(self):
        spool = self.root / "spool"
        unknown = client_event()
        unknown["role_id"] = "T999"
        unknown["payload_digest"] = events.payload_digest(
            {key: value for key, value in unknown.items() if key not in {"event_id", "payload_digest"}}
        )
        broker.spool_client_event(
            spool,
            unknown,
            authentication=report_authentication(unknown, "unknown-token"),
        )
        valid = client_event(event_id="00000000-0000-4000-8000-0000000000aa")
        broker.spool_client_event(
            spool, valid, authentication=report_authentication(valid, self.report_token)
        )

        with self.store.transaction() as transaction:
            replayed = self.store.replay_spool(transaction, spool)
        self.assertEqual([item.event["event_id"] for item in replayed], [valid["event_id"]])
        self.assertEqual(list(spool.glob("*.json")), [])
        quarantine_dir = spool / "quarantine"
        quarantined = [
            path for path in quarantine_dir.glob("*.json") if not path.name.endswith(".audit.json")
        ]
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].name, f"{unknown['event_id']}.json")
        audit = json.loads((quarantine_dir / f"{unknown['event_id']}.json.audit.json").read_text())
        self.assertEqual(audit["reason"], "stale-run")
        self.assertEqual(audit["event_id"], unknown["event_id"])

        for path in quarantine_dir.glob("*"):
            path.unlink()
        stale = client_event()
        with self.store.transaction() as transaction:
            self.store.register_active_role(
                transaction,
                run_id="run-001",
                role_id="T003",
                attempt_id="T003-A002",
                task_contract_digest=stale["task_contract_digest"],
                token="new-token",
            )
        broker.spool_client_event(
            spool,
            stale,
            authentication=report_authentication(stale, self.report_token),
        )
        with self.store.transaction() as transaction:
            replayed = self.store.replay_spool(transaction, spool)
        self.assertEqual(replayed, [])
        self.assertEqual(list(spool.glob("*.json")), [])
        audit = json.loads(
            (quarantine_dir / f"{stale['event_id']}.json.audit.json").read_text()
        )
        self.assertEqual(audit["reason"], "revoked-attempt")
        self.assertIn("T003/T003-A001", audit["detail"])

    def test_future_schema_is_rejected_without_ddl_mutation(self):
        future_root = self.root / "future-broker"
        future_root.mkdir()
        database_path = future_root / "broker.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.executescript(
            """
            CREATE TABLE broker_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO broker_meta(key, value) VALUES('schema_version', '2');
            CREATE TABLE future_records (id INTEGER PRIMARY KEY, value TEXT);
            CREATE TRIGGER future_records_allow_update
            AFTER UPDATE ON future_records BEGIN SELECT 1; END;
            """
        )
        before = connection.execute(
            """
            SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name
            """
        ).fetchall()
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.close()

        with self.assertRaisesRegex(
            broker.BrokerStorageError, "unsupported broker schema version: 2"
        ):
            broker.BrokerStore(future_root)

        connection = sqlite3.connect(database_path)
        after = connection.execute(
            """
            SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name
            """
        ).fetchall()
        self.assertEqual(after, before)
        self.assertEqual(
            connection.execute("PRAGMA schema_version").fetchone()[0], schema_version
        )
        self.assertEqual(
            connection.execute(
                "SELECT value FROM broker_meta WHERE key = 'schema_version'"
            ).fetchone()[0],
            "2",
        )
        connection.close()

        reopened = broker.BrokerStore(self.root / "broker")
        with reopened.transaction() as transaction:
            self.assertEqual(
                [item["sequence"] for item in reopened.events(transaction)], []
            )

    def test_runtime_socket_and_broker_owner_metadata(self):
        socket_path = broker.derive_runtime_socket_path(
            "namespace", "run-001", runtime_directory=Path("/tmp")
        )
        self.assertEqual(
            socket_path,
            broker.derive_runtime_socket_path(
                "namespace", "run-001", runtime_directory=Path("/tmp")
            ),
        )
        self.assertLessEqual(
            len(str(socket_path).encode()), broker.UNIX_SOCKET_PATH_MAX_BYTES
        )
        self.assertNotIn("run-001", str(socket_path))

        with self.store.transaction() as transaction:
            first = self.store.register_owner(
                transaction,
                namespace="namespace",
                run_id="run-001",
                runtime_version="0.5.0",
                socket_path=socket_path,
                pid=1234,
                started_at=CREATED_AT,
            )
            second = self.store.register_owner(
                transaction,
                namespace="namespace",
                run_id="run-001",
                runtime_version="0.5.0",
                socket_path=socket_path,
                pid=5678,
                started_at=CREATED_AT,
            )
            self.assertEqual((first["owner_epoch"], second["owner_epoch"]), (1, 2))
            self.assertEqual(
                self.store.current_owner(
                    transaction, namespace="namespace", run_id="run-001"
                ),
                second,
            )

        metadata_path = self.root / "owner.json"
        broker.write_owner_metadata(metadata_path, second)
        self.assertEqual(broker.read_owner_metadata(metadata_path), second)
        self.assertEqual(metadata_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_spool_entry_is_quarantined_while_valid_entries_still_replay(self):
        spool = self.root / "spool"
        event = client_event()
        broker.spool_client_event(
            spool, event, authentication=report_authentication(event, self.report_token)
        )
        invalid = spool / "ffffffff-ffff-4fff-8fff-ffffffffffff.json"
        invalid.write_text(json.dumps({"not": "an event"}), encoding="utf-8")

        with self.store.transaction() as transaction:
            replayed = self.store.replay_spool(transaction, spool)
        self.assertEqual([item.event["event_id"] for item in replayed], [event["event_id"]])

        with self.store.transaction() as transaction:
            self.assertEqual(
                [item["event_id"] for item in self.store.events(transaction)],
                [event["event_id"]],
            )
        self.assertEqual(list(spool.glob("*.json")), [])
        quarantine_dir = spool / "quarantine"
        quarantined = [
            path for path in quarantine_dir.glob("*.json") if not path.name.endswith(".audit.json")
        ]
        self.assertEqual([path.name for path in quarantined], ["ffffffff-ffff-4fff-8fff-ffffffffffff.json"])
        audit = json.loads(
            (quarantine_dir / "ffffffff-ffff-4fff-8fff-ffffffffffff.json.audit.json").read_text()
        )
        self.assertEqual(audit["reason"], "invalid")
        self.assertIn("invalid spool entry", audit["detail"])


class RoleOutboxTests(unittest.TestCase):
    """Exercises the pre-broker-send role-local outbox primitive."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.outbox_path = Path(self.temporary.name) / "outbox" / "role.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_outbox_persists_atomically_with_private_permissions_before_any_broker_call(self):
        event_id = broker.role_outbox_event_id(
            self.outbox_path, payload_digest="a" * 64
        )
        self.assertTrue(uuid.UUID(event_id))
        self.assertTrue(self.outbox_path.exists())
        self.assertEqual(stat.S_IMODE(self.outbox_path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(self.outbox_path.parent.stat().st_mode), 0o700
        )
        stored = json.loads(self.outbox_path.read_text(encoding="utf-8"))
        self.assertEqual(stored, {"entries": [["a" * 64, event_id]]})

    def test_retry_with_identical_digest_reuses_the_persisted_event_id(self):
        first = broker.role_outbox_event_id(self.outbox_path, payload_digest="b" * 64)
        second = broker.role_outbox_event_id(self.outbox_path, payload_digest="b" * 64)
        self.assertEqual(first, second)

        different = broker.role_outbox_event_id(self.outbox_path, payload_digest="c" * 64)
        self.assertNotEqual(first, different)

    def test_requested_event_id_takes_precedence_and_is_recorded_for_later_reuse(self):
        requested = "28b79cf0-8e32-4b89-88af-e332ee5a5dbe"
        resolved = broker.role_outbox_event_id(
            self.outbox_path, payload_digest="d" * 64, requested_event_id=requested
        )
        self.assertEqual(resolved, requested)

        reused = broker.role_outbox_event_id(self.outbox_path, payload_digest="d" * 64)
        self.assertEqual(reused, requested)

    def test_outbox_trims_the_oldest_digests_while_keeping_recent_ones(self):
        first_event_id = broker.role_outbox_event_id(
            self.outbox_path, payload_digest="0" * 64
        )
        for index in range(1, 70):
            broker.role_outbox_event_id(
                self.outbox_path, payload_digest=f"{index:064x}"
            )
        stored = json.loads(self.outbox_path.read_text(encoding="utf-8"))
        digests = [pair[0] for pair in stored["entries"]]
        self.assertLessEqual(len(digests), 64)
        self.assertIn(f"{69:064x}", digests)
        self.assertNotIn("0" * 64, digests)

        # The trimmed digest is durably gone: a "retry" for it mints a fresh
        # event ID rather than resurrecting the evicted one.
        recreated = broker.role_outbox_event_id(
            self.outbox_path, payload_digest="0" * 64
        )
        self.assertNotEqual(recreated, first_event_id)


if __name__ == "__main__":
    unittest.main()
