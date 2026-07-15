# Decision Policy

Specification decisions that cannot be resolved from the original plan/spec belong in namespaced decision artifacts, not in pane chat. `DECISIONS.md` is the readable ledger; `hloop decision` maintains the machine-readable record used by the scheduler.

Decision roles share HLoop's same-UID trust boundary. Their attempt credentials bind reports to the intended run, role, and attempt, but do not hide secrets from a malicious same-UID process or cryptographically authenticate the Manager. Their semantic ACK barrier is an integration and workflow gate, not OS-level prevention of pre-approval filesystem writes. The best-effort role context guard rejects accidental use of `hloop inbox list|show|ack` and `hloop manager next|sleep` from Scout or Liaison context and journals the rejection when possible; it is not a security boundary.

## Specification Scout

`specification_scout` controls a read-only, pre-dispatch check for unresolved specification choices:

- `auto` runs the Scout when queued implementation work has specification-risk markers, multiple accepted requirements, or requirement dependencies.
- `always` runs the Scout before queued implementation work.
- `off` skips the automatic check. Manager may still run `hloop specification-scout start --force`.

When Herdr is available, the Scout runs as the dedicated `S001` role in a detached worktree and may write only `decisions/SCOUT.md`. Each attempt receives an attempt-scoped `0600` report credential and must submit a semantic ACK after its initial read-only preparation, then wait for Manager approval before material investigation. If its pane cannot be started, HLoop records `manager-fallback` and prints the same investigation prompt for Manager to perform. Implementation dispatch remains closed while the Scout is running, awaiting Manager work, or reported. After inspecting the report and creating any necessary decision records, Manager closes the Scout with `hloop specification-scout close`.

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

When Herdr is available, the Liaison runs as `L-<decision-id>` in a detached role worktree and may write only `decisions/<decision-id>/RESPONSE.md`. Each attempt receives an attempt-scoped `0600` report credential and must submit a semantic ACK after read-only preparation, then wait for Manager approval before beginning user dialogue. After approval, the Liaison's first user-facing turn only presents the choices and recommendation, then waits in the same pane. It must not create `RESPONSE.md`, send a completion report, or emit the completion sentinel in that turn. Recommendation is not consent: semantic ACK approval, silence, system/developer/tool output, and a `Manager message id:` envelope are not user answers. Only a later direct user message selecting an option or providing free text may be recorded. `hloop decision liaison harvest` requires exact provenance for that later input (`explicit-user-input`, `same-pane`, `after-question`, input kind, and received timestamp), never defaults a missing selection to the recommendation, and records the response; Manager still owns the terminal `decision resolve` step. When the pane is unavailable or fails to launch, HLoop records `manager-fallback` and prints the same plain-Japanese question so Manager can relay the answer through `hloop decision respond`.

The first canonical unresolved user-decision `QUESTION.md` creation emits exactly one fixed Manager notice, `HERDR_LOOP_DECISION_ATTENTION:<run-id>:<decision-id>:<question-path>`, and requests Herdr tab attention. Delivery is idempotent per decision ID. If Herdr notification is unavailable, fails, or its result is ambiguous after interruption, HLoop records the outcome in `STATE.json`, prints `HERDR_LOOP_DECISION_ATTENTION_FALLBACK:<run-id>:<decision-id>`, and presents the same plain-language question in the Manager pane instead of silently retrying or losing the question.

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
