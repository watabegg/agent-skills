# Dependency-aware lease queue

Implement the `leasequeue` package in `starter/leasequeue`. It is a deterministic,
in-memory queue driven only by caller-supplied time. Use the public errors in
`leasequeue/errors.py` at the boundaries described below. Use only the Python
standard library.

The starter's dataclasses, exported names, fields, and method signatures are part
of the API. Preserve them. Public job statuses are exactly `pending`, `leased`,
`succeeded`, `failed`, `cancelled`, and `blocked`.

## Job specifications and dependency graph

`normalize_specs(raw_specs)` returns a fresh mapping from canonical IDs to
`JobSpec` values, with mapping keys in ascending canonical-ID order. It must not
mutate or retain caller-owned containers.

- The root is a non-string iterable of `Mapping` items. Reject a root Mapping,
  strings, bytes, non-iterables, and non-Mapping elements with `InvalidGraph`.
- Each `id` and dependency ID is a string. Strip surrounding whitespace; reject
  empty results. The stripped value is the canonical ID.
- `deps` defaults to an empty iterable. It must be a non-string,
  non-bytes, non-Mapping iterable. Canonicalize its IDs, remove duplicates, and
  sort them ascending. Malformed or hostile iterables must raise `InvalidGraph`.
- `priority` defaults to `0` and must be an `int` other than `bool`.
  `max_attempts` defaults to `3` and must be a positive `int` other than
  `bool`.
- Reject duplicate canonical IDs, self-dependencies, and unknown dependencies
  with `InvalidGraph`.

`topological_layers(specs)` validates a normalized mapping before computing its
maximal Kahn-style dependency layers. Validate that the mapping keys equal each
`JobSpec.id`, field types are valid, dependencies are known and not self
references, and the graph is acyclic. Raise `InvalidGraph` for violations. Within
each layer, order jobs by priority descending and then ID ascending. Return `[]`
for an empty mapping. Do not mutate the input.

## Queue behavior

`LeaseQueue(specs)` takes a validated mapping of `JobSpec` values;
`LeaseQueue.from_raw(raw_specs)` normalizes raw specifications. A queue starts
with every job pending and zero attempts.

- `now` and `lease_seconds` must be finite `int` or `float` values, excluding
  `bool`. `lease_seconds` must be positive. `worker_id` must be a string whose
  stripped value is nonempty; store and return the stripped value. `limit` must
  be a positive `int`, excluding `bool`.
- Invalid arguments, unknown jobs, stale tokens, and invalid transitions raise
  `InvalidTransition`. Caller-supplied time is external and need not be
  monotonic; never read a wall clock.
- `view` and every state-changing operation reap all leases with
  `deadline <= now` before producing a result. `snapshot(now)` also reaps first.
- `acquire(worker_id, now, lease_seconds, limit=1)` returns a tuple of `Lease`
  values. A job is runnable when it is pending and all its direct dependencies
  succeeded. Order runnable jobs by priority descending, then ID ascending, and
  issue at most `limit` leases.
- Each lease issue increments the job's attempts and a queue-wide monotonic token
  counter. Tokens are `lease-` followed by the counter zero-padded to at least
  eight digits, starting at `lease-00000001`. The lease deadline is
  `now + lease_seconds`; its worker ID is canonicalized.
- `heartbeat(job_id, token, now, lease_seconds)` requires the job's current
  token. It returns the renewed `Lease`, with deadline `now + lease_seconds`,
  preserving its job ID, token, worker ID, and attempt. A heartbeat at or after
  the old deadline first reaps the expired lease, so its token is stale.
- `complete(job_id, token, now)` changes a valid leased job to `succeeded`.
  Succeeded jobs are terminal and make dependent jobs runnable.
- `fail(job_id, token, now)` returns a job to pending while its attempts are
  below `max_attempts`; otherwise it makes the job failed. Lease expiry uses
  this same retry and exhaustion rule. At the exact deadline the lease is
  expired, and its old token is stale.
- A failed, cancelled, or blocked dependency blocks its descendants. For a
  blocked job, `blocked_by` is the sorted tuple of only its direct dependencies
  whose statuses are failed, cancelled, or blocked. Propagate blocking without
  changing unrelated jobs or active leases.
- `cancel(job_id, now)` accepts pending or leased jobs. It invalidates an active
  token, cancels the target, propagates blocking, and returns a sorted tuple of
  every job ID whose state changed. Cancelling an already-cancelled job returns
  `()`. Cancelling any other terminal status raises `InvalidTransition`.

## Snapshot codec and restore

`dumps_snapshot(data)` accepts a `Mapping` root and returns canonical JSON text:
UTF-8 characters are not escaped, object keys are sorted, separators are
`(",", ":")`, and NaN or infinity is rejected. `loads_snapshot(payload)` accepts
valid JSON text and returns primitive Python data. If the existing API accepts
bytes-like input, it must accept only valid UTF-8. Reject malformed JSON,
duplicate object keys, non-object roots, NaN/infinity, and incompatible input
types with `InvalidSnapshot`.

`snapshot(now)` returns a canonical JSON string with exactly these top-level
keys:

- `version`: integer `1`.
- `next_token`: the next positive token number to issue.
- `specs`: an ID-sorted list of exact objects `{id, deps, priority,
  max_attempts}`; each `deps` value is a list.
- `jobs`: an ID-sorted list of exact objects `{id, status, attempts, lease,
  blocked_by}`; each `blocked_by` value is a list.

A leased job has a `lease` object with exactly `{job_id, token, worker_id,
deadline, attempt}`. Every other job has `lease: null`.

`LeaseQueue.restore(snapshot)` strictly validates the snapshot. Reject missing or
unknown fields at every level, duplicate spec/job IDs, mismatched spec/job ID
sets, invalid graph data, invalid types or ranges, status/lease mismatches,
attempts outside `0..max_attempts`, malformed or reused tokens, a non-advancing
`next_token`, impossible dependency states, and inconsistent `blocked_by`.
Convert every restore-boundary failure to `InvalidSnapshot`. A restored queue is
independent of the source queue, preserves token sequencing, and produces a
byte-identical snapshot at the same time.

The successful return values of `complete` and `fail` are unspecified in this
contract and are not graded.
