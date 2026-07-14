from __future__ import annotations

import hashlib
import json
import sqlite3
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
    return value


def client_event(
    *, summary: str = "契約を確認しました", event_id: str | None = None
) -> dict[str, object]:
    return events.prepare_client_event(
        report(summary=summary), event_id=event_id or str(uuid.uuid4())
    )


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


class BrokerStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = broker.BrokerStore(self.root / "broker")

    def tearDown(self):
        self.temporary.cleanup()

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

    def test_spool_replay_deletes_only_after_commit_and_replays_idempotently(self):
        spool = self.root / "spool"
        first = client_event(event_id="a9a68f73-af7b-4416-97bb-a043d0f702fa")
        second = client_event(event_id="baf37552-86dd-42ba-a036-7d555e34a479")
        broker.spool_client_event(spool, first)
        with self.assertRaises(broker.IdempotencyConflict):
            broker.spool_client_event(
                spool,
                client_event(
                    event_id="a9a68f73-af7b-4416-97bb-a043d0f702fa",
                    summary="same id, different report",
                ),
            )
        broker.spool_client_event(spool, second)

        with self.store.transaction() as transaction:
            replayed = self.store.replay_spool(transaction, spool)
            self.assertEqual([item.event["sequence"] for item in replayed], [1, 2])
            self.assertEqual(len(list(spool.glob("*.json"))), 2)
        self.assertEqual(list(spool.glob("*.json")), [])

        broker.spool_client_event(spool, first)
        with self.store.transaction() as transaction:
            replayed = self.store.replay_spool(transaction, spool)
            self.assertFalse(replayed[0].inserted)
            self.assertEqual(replayed[0].event["sequence"], 1)
        self.assertEqual(list(spool.glob("*.json")), [])

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

    def test_invalid_spool_entry_rolls_back_the_whole_replay_batch(self):
        spool = self.root / "spool"
        broker.spool_client_event(spool, client_event())
        invalid = spool / "ffffffff-ffff-4fff-8fff-ffffffffffff.json"
        invalid.write_text(json.dumps({"not": "an event"}), encoding="utf-8")

        with self.assertRaisesRegex(broker.BrokerStorageError, "invalid spool entry"):
            with self.store.transaction() as transaction:
                self.store.replay_spool(transaction, spool)

        with self.store.transaction() as transaction:
            self.assertEqual(self.store.events(transaction), [])


if __name__ == "__main__":
    unittest.main()
