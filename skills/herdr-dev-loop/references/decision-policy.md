# Decision Policy

Specification decisions that cannot be resolved from the original plan/spec belong in namespaced decision artifacts, not in pane chat. `DECISIONS.md` is the readable ledger; `hloop decision` maintains the machine-readable record used by the scheduler.

Use `DECISIONS.md` as the durable decision ledger:

- `Pending Decisions`: unresolved choices and the exact question
- `Accepted Decisions`: chosen behavior, evidence, and date
- `Rejected Decisions`: alternatives that were considered and why they were rejected

## Decision Classes

- `advisory`: Manager may proceed while preserving the recommendation and rationale.
- `deferred-user`: user input is needed later, but the currently safe task graph may continue.
- `blocking-user`: only explicitly affected tasks and their unmerged dependencies stop.

Create a user-decision record with two or three options, an explicit recommendation, rationale, and at least one affected task. Store the answer with `hloop decision respond`. An answer remains non-terminal until Manager validates it and runs `hloop decision resolve`; a conflicting second response or resolution is rejected.

## Blocking Decisions

Create a blocking decision when the unresolved choice affects:

- user-visible behavior
- public API or DB schema
- migrations or rollback strategy
- security, privacy, auth, or authorization
- backward compatibility
- pricing, destructive operations, or data retention
- any choice whose wrong answer has high rollback cost

When a blocking decision exists:

1. create it with `hloop decision new --class blocking-user --affects <task>`
2. update the readable `DECISIONS.md` and `USER_ACTION_REQUIRED.md`
3. stop only the affected tasks and their unmerged dependencies
4. keep dispatching unrelated safe work
5. enter loop-wide `blocked_user_decision` only when no unaffected queued task, running role, merge-ready result, validation, review, or gap work remains
6. ask one plain-language question with two or three concrete options and a recommendation

## Non-Blocking Choices

Manager may decide reversible implementation details:

- private helper names
- test file organization
- small refactor shape
- internal data structure that does not affect behavior
- local command selection when the repository has no convention

Record notable choices in `JOURNAL.md`.
