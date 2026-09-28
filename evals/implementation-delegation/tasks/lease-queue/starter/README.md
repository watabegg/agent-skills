# leasequeue

Complete this dependency-aware in-memory lease queue.

The queue accepts jobs with priorities, dependencies, and retry limits. Workers
acquire time-bounded leases, may heartbeat them, and then complete or fail the
job. Expired leases are retried. Permanent failure and cancellation must keep
dependent jobs from running. Queue state can be snapshotted and restored.

The implementation must be deterministic, must not use wall-clock time or
randomness internally, and must reject malformed inputs and invalid state
transitions with the public errors in `leasequeue/errors.py`.

Run the visible suite with:

```bash
python3 -m unittest discover -s tests -v
```
