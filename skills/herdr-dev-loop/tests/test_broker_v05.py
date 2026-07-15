from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib import broker  # noqa: E402


class BrokerStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = broker.BrokerStore(self.root / "broker")

    def tearDown(self):
        self.temporary.cleanup()

    def test_operational_error_at_transaction_begin_is_retryable_unavailability(self):
        connection = mock.Mock()
        connection.execute.side_effect = sqlite3.OperationalError("database is locked")
        connection.close.return_value = None

        with mock.patch.object(self.store, "_connect", return_value=connection):
            with self.assertRaises(broker.BrokerUnavailableError):
                with self.store.transaction():
                    self.fail("transaction body must not run")

    def test_database_error_at_connect_is_retryable_unavailability(self):
        with mock.patch.object(
            self.store,
            "_connect",
            side_effect=sqlite3.DatabaseError("file is not a database"),
        ):
            with self.assertRaises(broker.BrokerUnavailableError):
                with self.store.transaction():
                    self.fail("transaction body must not run")

    def test_integrity_error_is_permanent_storage_semantics(self):
        connection = mock.Mock()
        connection.execute.side_effect = sqlite3.IntegrityError(
            "unique constraint failed"
        )
        connection.close.return_value = None

        with mock.patch.object(self.store, "_connect", return_value=connection):
            with self.assertRaises(broker.BrokerIntegrityError):
                with self.store.transaction():
                    self.fail("transaction body must not run")

    def test_cleanup_failure_does_not_reclassify_a_semantic_conflict(self):
        connection = mock.Mock()
        connection.execute.return_value = None
        connection.close.side_effect = sqlite3.OperationalError("close failed")

        with mock.patch.object(self.store, "_connect", return_value=connection):
            with self.assertRaises(broker.IdempotencyConflict):
                with self.store.transaction():
                    raise broker.IdempotencyConflict("permanent conflict")


if __name__ == "__main__":
    unittest.main()
