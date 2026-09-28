import copy
import json
import math
import sys
import unittest

from leasequeue import (
    InvalidGraph,
    InvalidSnapshot,
    InvalidTransition,
    JobSpec,
    LeaseQueue,
    normalize_specs,
    topological_layers,
)
from leasequeue.codec import dumps_snapshot, loads_snapshot


class NormalizationTests(unittest.TestCase):
    def test_canonical_normalization_and_input_immutability(self):
        raw = [
            {"id": " build ", "deps": [" check ", "check"], "priority": 2},
            {"id": "check", "max_attempts": 4},
        ]
        before = copy.deepcopy(raw)
        specs = normalize_specs(raw)
        self.assertEqual(raw, before)
        self.assertEqual(list(specs), ["build", "check"])
        self.assertEqual(specs["build"], JobSpec("build", ("check",), 2, 3))
        self.assertEqual(specs["check"], JobSpec("check", (), 0, 4))

    def test_generator_input_is_supported(self):
        specs = normalize_specs({"id": value} for value in ["b", "a"])
        self.assertEqual(list(specs), ["a", "b"])

    def test_invalid_root_containers_are_rejected(self):
        for value in ["abc", b"abc", {"id": "a"}, 42, None]:
            with self.subTest(value=value), self.assertRaises(InvalidGraph):
                normalize_specs(value)

    def test_invalid_items_and_ids_are_rejected(self):
        cases = [["a"], [{}], [{"id": 1}], [{"id": "  "}]]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(InvalidGraph):
                normalize_specs(value)

    def test_duplicate_ids_after_canonicalization_are_rejected(self):
        with self.assertRaises(InvalidGraph):
            normalize_specs([{"id": "a"}, {"id": " a "}])

    def test_dependency_container_and_members_are_validated(self):
        cases = [
            [{"id": "a", "deps": "b"}, {"id": "b"}],
            [{"id": "a", "deps": {"b": True}}, {"id": "b"}],
            [{"id": "a", "deps": [1]}],
            [{"id": "a", "deps": ["  "]}],
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(InvalidGraph):
                normalize_specs(value)

    def test_self_and_unknown_dependencies_are_rejected(self):
        for value in [
            [{"id": "a", "deps": ["a"]}],
            [{"id": "a", "deps": ["missing"]}],
        ]:
            with self.subTest(value=value), self.assertRaises(InvalidGraph):
                normalize_specs(value)

    def test_priority_and_attempt_limits_are_validated(self):
        cases = [
            [{"id": "a", "priority": True}],
            [{"id": "a", "priority": 1.5}],
            [{"id": "a", "max_attempts": True}],
            [{"id": "a", "max_attempts": 0}],
            [{"id": "a", "max_attempts": -1}],
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(InvalidGraph):
                normalize_specs(value)


class GraphTests(unittest.TestCase):
    def test_layers_are_maximal_and_deterministic(self):
        specs = normalize_specs([
            {"id": "finish", "deps": ["b", "a"]},
            {"id": "b", "priority": 1},
            {"id": "a", "priority": 1},
            {"id": "top", "priority": 9},
        ])
        self.assertEqual(topological_layers(specs), [["top", "a", "b"], ["finish"]])

    def test_empty_graph(self):
        self.assertEqual(topological_layers({}), [])

    def test_mapping_key_must_match_spec_id(self):
        with self.assertRaises(InvalidGraph):
            topological_layers({"a": JobSpec("b")})

    def test_manual_unknown_self_and_cycle_are_rejected(self):
        cases = [
            {"a": JobSpec("a", ("missing",))},
            {"a": JobSpec("a", ("a",))},
            {"a": JobSpec("a", ("b",)), "b": JobSpec("b", ("a",))},
        ]
        for specs in cases:
            with self.subTest(specs=specs), self.assertRaises(InvalidGraph):
                topological_layers(specs)


class QueueTests(unittest.TestCase):
    def test_initial_views_are_pending(self):
        queue = LeaseQueue.from_raw([{"id": " a "}])
        self.assertEqual(queue.view("a", 0).status, "pending")
        self.assertEqual(queue.view("a", 0).attempts, 0)

    def test_acquire_argument_validation(self):
        invalid_calls = [
            ("worker", True, 1, 1),
            ("worker", math.nan, 1, 1),
            ("worker", math.inf, 1, 1),
            ("worker", 0, 0, 1),
            ("worker", 0, math.inf, 1),
            ("worker", 0, 1, True),
            ("worker", 0, 1, 0),
            ("  ", 0, 1, 1),
        ]
        for worker, now, seconds, limit in invalid_calls:
            with self.subTest(args=(worker, now, seconds, limit)), self.assertRaises(InvalidTransition):
                LeaseQueue.from_raw([{"id": "a"}]).acquire(worker, now, seconds, limit)

    def test_acquire_order_tokens_attempts_and_limit(self):
        queue = LeaseQueue.from_raw([
            {"id": "b", "priority": 1},
            {"id": "a", "priority": 1},
            {"id": "top", "priority": 5},
        ])
        leases = queue.acquire(" worker ", 10, 2.5, limit=2)
        self.assertEqual([lease.job_id for lease in leases], ["top", "a"])
        self.assertEqual([lease.token for lease in leases], ["lease-00000001", "lease-00000002"])
        self.assertEqual([lease.worker_id for lease in leases], ["worker", "worker"])
        self.assertEqual([lease.deadline for lease in leases], [12.5, 12.5])
        self.assertEqual([lease.attempt for lease in leases], [1, 1])

    def test_dependencies_unlock_only_after_success(self):
        queue = LeaseQueue.from_raw([{"id": "b", "deps": ["a"]}, {"id": "a"}])
        first = queue.acquire("w", 0, 10)[0]
        self.assertEqual(first.job_id, "a")
        self.assertEqual(queue.acquire("w", 1, 10), ())
        queue.complete("a", first.token, 1)
        self.assertEqual(queue.acquire("w", 1, 10)[0].job_id, "b")

    def test_heartbeat_extends_from_now(self):
        queue = LeaseQueue.from_raw([{"id": "a"}])
        lease = queue.acquire("w", 10, 5)[0]
        updated = queue.heartbeat("a", lease.token, 12, 4)
        self.assertEqual(updated.deadline, 16.0)
        self.assertEqual(updated.token, lease.token)
        self.assertEqual(updated.attempt, 1)

    def test_stale_and_unknown_tokens_are_rejected(self):
        queue = LeaseQueue.from_raw([{"id": "a"}])
        lease = queue.acquire("w", 0, 10)[0]
        for operation in [
            lambda: queue.complete("a", "bad", 1),
            lambda: queue.fail("a", "bad", 1),
            lambda: queue.heartbeat("a", "bad", 1, 2),
            lambda: queue.complete("missing", lease.token, 1),
        ]:
            with self.assertRaises(InvalidTransition):
                operation()

    def test_deadline_equality_expires_and_retries(self):
        queue = LeaseQueue.from_raw([{"id": "a", "max_attempts": 2}])
        first = queue.acquire("w", 0, 2)[0]
        second = queue.acquire("w2", 2, 2)[0]
        self.assertEqual(second.attempt, 2)
        self.assertEqual(second.token, "lease-00000002")
        with self.assertRaises(InvalidTransition):
            queue.complete("a", first.token, 2)

    def test_expiration_exhaustion_blocks_descendants(self):
        queue = LeaseQueue.from_raw([
            {"id": "a", "max_attempts": 1},
            {"id": "b", "deps": ["a"]},
        ])
        queue.acquire("w", 0, 1)
        self.assertEqual(queue.view("a", 1).status, "failed")
        self.assertEqual(queue.view("b", 1).status, "blocked")
        self.assertEqual(queue.view("b", 1).blocked_by, ("a",))

    def test_explicit_failure_retries_then_exhausts(self):
        queue = LeaseQueue.from_raw([{"id": "a", "max_attempts": 2}])
        first = queue.acquire("w", 0, 10)[0]
        queue.fail("a", first.token, 1)
        self.assertEqual(queue.view("a", 1).status, "pending")
        second = queue.acquire("w", 1, 10)[0]
        queue.fail("a", second.token, 2)
        self.assertEqual(queue.view("a", 2).status, "failed")

    def test_permanent_failure_does_not_touch_unrelated_jobs(self):
        queue = LeaseQueue.from_raw([
            {"id": "a", "max_attempts": 1, "priority": 2},
            {"id": "independent", "priority": 1},
            {"id": "child", "deps": ["a"]},
        ])
        leases = queue.acquire("w", 0, 10, 2)
        a = next(item for item in leases if item.job_id == "a")
        independent = next(item for item in leases if item.job_id == "independent")
        queue.fail("a", a.token, 1)
        self.assertEqual(queue.view("independent", 1).lease, independent)

    def test_succeeded_jobs_are_terminal(self):
        queue = LeaseQueue.from_raw([{"id": "a"}])
        lease = queue.acquire("w", 0, 10)[0]
        queue.complete("a", lease.token, 1)
        self.assertEqual(queue.view("a", 1).status, "succeeded")
        self.assertEqual(queue.acquire("w", 1, 10), ())

    def test_cancel_pending_propagates_and_returns_sorted_ids(self):
        queue = LeaseQueue.from_raw([
            {"id": "c", "deps": ["b"]},
            {"id": "b", "deps": ["a"]},
            {"id": "a"},
            {"id": "z"},
        ])
        self.assertEqual(queue.cancel("a", 0), ("a", "b", "c"))
        self.assertEqual(queue.view("a", 0).status, "cancelled")
        self.assertEqual(queue.view("b", 0).blocked_by, ("a",))
        self.assertEqual(queue.view("c", 0).blocked_by, ("b",))
        self.assertEqual(queue.view("z", 0).status, "pending")

    def test_cancel_leased_job_invalidates_token(self):
        queue = LeaseQueue.from_raw([{"id": "a"}])
        lease = queue.acquire("w", 0, 10)[0]
        self.assertEqual(queue.cancel("a", 1), ("a",))
        with self.assertRaises(InvalidTransition):
            queue.complete("a", lease.token, 1)

    def test_cancel_idempotency_and_terminal_rules(self):
        cancelled = LeaseQueue.from_raw([{"id": "a"}])
        cancelled.cancel("a", 0)
        self.assertEqual(cancelled.cancel("a", 0), ())
        succeeded = LeaseQueue.from_raw([{"id": "a"}])
        lease = succeeded.acquire("w", 0, 10)[0]
        succeeded.complete("a", lease.token, 1)
        with self.assertRaises(InvalidTransition):
            succeeded.cancel("a", 1)

    def test_blocked_by_contains_only_direct_bad_dependencies(self):
        queue = LeaseQueue.from_raw([
            {"id": "a", "max_attempts": 1},
            {"id": "b", "deps": ["a"]},
            {"id": "c", "deps": ["a", "b"]},
        ])
        lease = queue.acquire("w", 0, 10)[0]
        queue.fail("a", lease.token, 1)
        self.assertEqual(queue.view("b", 1).blocked_by, ("a",))
        self.assertEqual(queue.view("c", 1).blocked_by, ("a", "b"))

    def test_view_reaps_and_validates(self):
        queue = LeaseQueue.from_raw([{"id": "a", "max_attempts": 1}])
        queue.acquire("w", 0, 1)
        self.assertEqual(queue.view("a", 1).status, "failed")
        for job_id, now in [("missing", 1), ("a", math.nan), ("a", True)]:
            with self.subTest(job_id=job_id, now=now), self.assertRaises(InvalidTransition):
                queue.view(job_id, now)

    def test_time_is_external_and_need_not_be_monotonic(self):
        queue = LeaseQueue.from_raw([{"id": "a"}])
        queue.acquire("w", 10, 5)
        self.assertEqual(queue.view("a", 9).status, "leased")
        self.assertIsInstance(queue.snapshot(8), str)

    def test_token_sequence_survives_retries(self):
        queue = LeaseQueue.from_raw([{"id": "a", "max_attempts": 3}])
        first = queue.acquire("w", 0, 10)[0]
        queue.fail("a", first.token, 1)
        second = queue.acquire("w", 1, 10)[0]
        self.assertEqual((first.token, second.token), ("lease-00000001", "lease-00000002"))


class SnapshotTests(unittest.TestCase):
    def _active_snapshot(self):
        queue = LeaseQueue.from_raw([
            {"id": "日本語", "priority": 2},
            {"id": "child", "deps": ["日本語"]},
        ])
        queue.acquire("作業者", 3, 5)
        return queue, queue.snapshot(4)

    def test_snapshot_is_canonical_and_has_fixed_schema(self):
        _, payload = self._active_snapshot()
        data = json.loads(payload)
        self.assertEqual(set(data), {"version", "next_token", "specs", "jobs"})
        self.assertIn("日本語", payload)
        self.assertEqual(payload, json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        self.assertEqual(data["next_token"], 2)

    def test_snapshot_round_trip_is_byte_identical(self):
        _, payload = self._active_snapshot()
        restored = LeaseQueue.restore(payload)
        self.assertEqual(restored.snapshot(4), payload)

    def test_restore_continues_token_sequence(self):
        queue, payload = self._active_snapshot()
        original = queue.view("日本語", 4).lease
        queue.complete("日本語", original.token, 4)
        restored = LeaseQueue.restore(payload)
        active = restored.view("日本語", 4).lease
        restored.complete("日本語", active.token, 4)
        child = restored.acquire("w", 4, 2)[0]
        self.assertEqual(child.token, "lease-00000002")

    def test_codec_canonical_round_trip_and_invalid_roots(self):
        data = {"z": 1, "a": "日本語"}
        payload = dumps_snapshot(data)
        self.assertEqual(payload, '{"a":"日本語","z":1}')
        self.assertEqual(loads_snapshot(payload), data)
        for value in ["[]", "null", "1", "{", '{"x":NaN}']:
            with self.subTest(value=value), self.assertRaises(InvalidSnapshot):
                loads_snapshot(value)

    def test_restore_rejects_top_level_shape_and_version(self):
        _, payload = self._active_snapshot()
        base = json.loads(payload)
        variants = []
        for key in list(base):
            value = copy.deepcopy(base)
            del value[key]
            variants.append(value)
        extra = copy.deepcopy(base)
        extra["extra"] = True
        variants.append(extra)
        bad_version = copy.deepcopy(base)
        bad_version["version"] = 2
        variants.append(bad_version)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(InvalidSnapshot):
                LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_duplicate_spec_and_job_ids(self):
        _, payload = self._active_snapshot()
        base = json.loads(payload)
        for field in ["specs", "jobs"]:
            value = copy.deepcopy(base)
            value[field].append(copy.deepcopy(value[field][0]))
            with self.subTest(field=field), self.assertRaises(InvalidSnapshot):
                LeaseQueue.restore(json.dumps(value))

    def test_restore_wraps_invalid_graph(self):
        _, payload = self._active_snapshot()
        value = json.loads(payload)
        value["specs"][0]["deps"] = ["missing"]
        with self.assertRaises(InvalidSnapshot):
            LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_status_lease_mismatch(self):
        _, payload = self._active_snapshot()
        value = json.loads(payload)
        leased = next(item for item in value["jobs"] if item["status"] == "leased")
        leased["lease"] = None
        with self.assertRaises(InvalidSnapshot):
            LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_invalid_attempt_count(self):
        _, payload = self._active_snapshot()
        value = json.loads(payload)
        value["jobs"][0]["attempts"] = -1
        with self.assertRaises(InvalidSnapshot):
            LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_succeeded_job_with_unsatisfied_dependency(self):
        queue = LeaseQueue.from_raw([{"id": "a"}, {"id": "b", "deps": ["a"]}])
        value = json.loads(queue.snapshot(0))
        next(item for item in value["jobs"] if item["id"] == "b")["status"] = "succeeded"
        with self.assertRaises(InvalidSnapshot):
            LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_inconsistent_blocked_by(self):
        queue = LeaseQueue.from_raw([{"id": "a"}, {"id": "b", "deps": ["a"]}])
        value = json.loads(queue.snapshot(0))
        b = next(item for item in value["jobs"] if item["id"] == "b")
        b["status"] = "blocked"
        b["blocked_by"] = ["missing"]
        with self.assertRaises(InvalidSnapshot):
            LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_reused_token_sequence(self):
        _, payload = self._active_snapshot()
        value = json.loads(payload)
        value["next_token"] = 1
        with self.assertRaises(InvalidSnapshot):
            LeaseQueue.restore(json.dumps(value))

    def test_restore_rejects_malformed_active_lease_fields(self):
        _, payload = self._active_snapshot()
        base = json.loads(payload)
        leased_index = next(i for i, item in enumerate(base["jobs"]) if item["status"] == "leased")
        bad_values = {
            "token": "bad-token",
            "worker_id": "  ",
            "deadline": math.inf,
            "attempt": 0,
        }
        for field, replacement in bad_values.items():
            value = copy.deepcopy(base)
            value["jobs"][leased_index]["lease"][field] = replacement
            with self.subTest(field=field), self.assertRaises(InvalidSnapshot):
                LeaseQueue.restore(json.dumps(value))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print(f"BLIND_SCORE={passed}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
