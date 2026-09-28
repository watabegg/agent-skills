import unittest

from leasequeue import InvalidTransition, LeaseQueue, normalize_specs, topological_layers


RAW = [
    {"id": "deploy", "deps": ["build", "check"], "priority": 1},
    {"id": "check", "priority": 5},
    {"id": "build", "priority": 3, "max_attempts": 2},
]


class VisibleLeaseQueueTests(unittest.TestCase):
    def test_normalize_and_layers(self):
        specs = normalize_specs(RAW)
        self.assertEqual(list(specs), ["build", "check", "deploy"])
        self.assertEqual(topological_layers(specs), [["check", "build"], ["deploy"]])

    def test_complete_dependencies_then_deploy(self):
        queue = LeaseQueue.from_raw(RAW)
        first = queue.acquire("worker", now=10, lease_seconds=5, limit=2)
        self.assertEqual([lease.job_id for lease in first], ["check", "build"])
        for lease in first:
            queue.complete(lease.job_id, lease.token, now=11)
        deploy = queue.acquire("worker", now=11, lease_seconds=5)
        self.assertEqual([lease.job_id for lease in deploy], ["deploy"])

    def test_failure_retries_then_blocks_dependents(self):
        queue = LeaseQueue.from_raw(RAW)
        first = queue.acquire("worker", now=0, lease_seconds=10, limit=2)
        build = next(lease for lease in first if lease.job_id == "build")
        queue.fail("build", build.token, now=1)
        retry = queue.acquire("worker", now=1, lease_seconds=10, limit=2)
        retried_build = next(lease for lease in retry if lease.job_id == "build")
        queue.fail("build", retried_build.token, now=2)
        self.assertEqual(queue.view("build", now=2).status, "failed")
        self.assertEqual(queue.view("deploy", now=2).status, "blocked")

    def test_expired_token_is_stale(self):
        queue = LeaseQueue.from_raw([{"id": "job", "max_attempts": 2}])
        lease = queue.acquire("worker", now=0, lease_seconds=2)[0]
        replacement = queue.acquire("worker-2", now=2, lease_seconds=2)[0]
        self.assertNotEqual(lease.token, replacement.token)
        with self.assertRaises(InvalidTransition):
            queue.complete("job", lease.token, now=2)

    def test_snapshot_round_trip(self):
        queue = LeaseQueue.from_raw(RAW)
        queue.acquire("worker", now=5, lease_seconds=3, limit=1)
        payload = queue.snapshot(now=6)
        restored = LeaseQueue.restore(payload)
        self.assertEqual(restored.snapshot(now=6), payload)


if __name__ == "__main__":
    unittest.main()
