# Worker Contract

Worker receives one task and one worktree.

Default runner: interactive Codex TUI. This keeps the Worker visible in Herdr so the Manager can add requirements, inspect progress, or interrupt the Worker before it finishes. Use `--runner exec` only for well-bounded automation tasks that should complete without interaction.

The Worker prompt must say:

- read `MISSION.md`, `PLAN.md`, and `tasks/<task-id>.md`
- use `$codex-impl`
- use the Manager-provided preflight defaults instead of asking the user again
- edit only `write_allow`
- do not edit Manager-owned loop files
- write `results/<task-id>/result.md`
- commit the branch
- print `HERDR_LOOP_TASK_DONE:<task-id>:<done|blocked|failed|partial>`

## `$codex-impl` Defaults For Workers

Manager may fix these kickoff choices in the Worker prompt:

- implementation gap-check count: `1` for this bounded task branch only
- review/fix loop limit: `skip review` unless the task says otherwise
- forced QA environment: `local`
- QA target: this task branch diff

This prevents Workers from blocking on a preflight question that Manager already answered for the bounded task.

This Worker-local gap check is not the final plan/spec coverage gate. Manager runs a separate Gap Auditor against the integration branch when the loop needs to confirm that the original repository plan/spec is still aligned with the combined implementation.

## Blocking

Worker must report `status: blocked` instead of guessing when the task requires:

- user-visible behavior change not specified by the task
- public API or DB schema decision
- security, privacy, auth, or authorization policy
- backward compatibility tradeoff
- irreversible migration or destructive operation

Worker records blocking questions in its result artifact. Manager decides whether to create a decision record.
