# Decision Policy

Specification decisions that cannot be resolved from the original plan/spec belong in `.ai/herdr-dev-loop/loops/<namespace>/DECISIONS.md`, not in pane chat.

Use `DECISIONS.md` as the durable decision ledger:

- `Pending Decisions`: unresolved choices and the exact question
- `Accepted Decisions`: chosen behavior, evidence, and date
- `Rejected Decisions`: alternatives that were considered and why they were rejected

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

1. append it to `DECISIONS.md` under `Pending Decisions`
2. update `USER_ACTION_REQUIRED.md`
3. set `STATE.json.phase` to `blocked_user_decision`
4. stop dispatching new Workers
5. ask the user concrete questions

## Non-Blocking Choices

Manager may decide reversible implementation details:

- private helper names
- test file organization
- small refactor shape
- internal data structure that does not affect behavior
- local command selection when the repository has no convention

Record notable choices in `JOURNAL.md`.
