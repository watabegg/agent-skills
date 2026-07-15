# Decision Policy

Specification decisions that cannot be resolved from the original plan/spec belong in namespaced decision artifacts, not in pane chat. `DECISIONS.md` is the readable ledger; `hloop decision` maintains the machine-readable record used by the scheduler.

## Specification Scout

`specification_scout` controls a read-only, pre-dispatch check for unresolved specification choices:

- `auto` runs the Scout when queued implementation work has specification-risk markers, multiple accepted requirements, or requirement dependencies.
- `always` runs the Scout before queued implementation work.
- `off` skips the automatic check. Manager may still run `hloop specification-scout start --force`.

When Herdr is available, the Scout runs as the dedicated `S001` role in a detached worktree and may write only `decisions/SCOUT.md`. If its pane cannot be started, HLoop records `manager-fallback` and prints the same investigation prompt for Manager to perform. Implementation dispatch remains closed while the Scout is running, awaiting Manager work, or reported. After inspecting the report and creating any necessary decision records, Manager closes the Scout with `hloop specification-scout close`.

Use `DECISIONS.md` as the durable decision ledger:

- `Pending Decisions`: unresolved choices and the exact question
- `Accepted Decisions`: chosen behavior, evidence, and date
- `Rejected Decisions`: alternatives that were considered and why they were rejected

## Decision Classes

- `advisory`: Manager may proceed while preserving the recommendation and rationale.
- `deferred-user`: user input is needed later, but the currently safe task graph may continue.
- `blocking-user`: only explicitly affected tasks and their unmerged dependencies stop.

Create a user-decision record with two or three options, an explicit recommendation, rationale, and at least one affected task. Store the answer with `hloop decision respond`. An answer remains non-terminal until Manager validates it and runs `hloop decision resolve`; a conflicting second response or resolution is rejected.

## Decision Liaison

For `deferred-user` and `blocking-user` records, HLoop starts a dedicated Decision Liaison before entering a loop-wide wait. The Liaison explains one decision in plain Japanese, with two or three choices, their tradeoffs, the recommended choice and rationale, what can continue while waiting, and a free-text response route. User-facing text must not expose decision IDs, task IDs, state-machine terms, or logical expressions.

When Herdr is available, the Liaison runs in a detached role worktree and may write only `decisions/<decision-id>/RESPONSE.md`. `hloop decision liaison harvest` validates and records that response; Manager still owns the terminal `decision resolve` step. When the pane is unavailable or fails to launch, HLoop records `manager-fallback` and prints the same plain-Japanese question so Manager can relay the answer through `hloop decision respond`.

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
