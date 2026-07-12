# Worker Contract

Worker receives one task and one worktree.

After committing product changes and completing validation, use `hloop worker finalize` to generate and commit `result.md`. Do not hand-copy `task_id`, `run_id`, `skill_version`, `base_sha`, `head_sha`, `changed_files`, or `merge_ready` when the helper is available.

Default runner: interactive role-agent TUI. Codex is the default provider, but Manager may select Claude or a specific model in `PROFILE.md`, task frontmatter, or `hloop worker start`. This keeps the Worker visible in Herdr so the Manager can add requirements, inspect progress, or interrupt the Worker before it finishes. Use `--runner exec` only for well-bounded automation tasks that should complete without interaction.

The Worker prompt must say:

- make the first progress message identify `herdr-dev-loop <version> / namespace <namespace> / Worker <task-id>`
- read `MISSION.md`, `PLAN.md`, `PROFILE.md`, `DECISIONS.md`, and `tasks/<task-id>.md`
- follow the HLoop Worker Protocol by default
- edit only `write_allow`
- do not edit Manager-owned loop files
- write `results/<task-id>/result.md`
- include the prompt-provided `skill_version` in the result artifact
- commit the branch
- print `HERDR_LOOP_TASK_DONE:<task-id>:<done|blocked|failed|partial>`

## HLoop Worker Protocol

Native Worker protocol is self-contained and does not depend on `$codex-impl`.

Worker must:

1. Re-read loop artifacts and the task contract from disk.
2. Verify the task is still valid against the current code.
3. Compare acceptance criteria against the implementation before editing.
4. Implement the smallest coherent change inside `write_allow`.
5. Self-review the diff for correctness, product behavior, security/privacy, data integrity, UX, and validation risk.
6. Run validation commands that fit the task and repository.
7. Apply the Worker QA profile from `PROFILE.md` or task frontmatter.
8. Write the result artifact with flat frontmatter fields.
9. Commit the work on the Worker branch.

## Worker QA Profile

Worker follows `worker_qa_profile` from the task frontmatter or `PROFILE.md`. Older tasks may contain `qa_profile`; treat it as a Worker QA compatibility alias.

- `repo-default`: choose repository-native checks from scripts, CI, docs, or existing QA artifacts.
- `local`: run local app/API/browser checks when the touched workflow is runnable.
- `staging` or `preview`: collect evidence only when the URL and credentials are already available in the task or PROFILE; otherwise report blocked QA explicitly.
- `custom`: follow the QA plan in `PLAN.md` exactly.
- `none`: record why QA is intentionally skipped.

Manager final staging/preview QA is separate. Do not block a Worker on final Manager QA unless the task explicitly makes that environment check part of the task acceptance criteria.

## Compatibility Mode

Use `worker_protocol: codex-impl` only when Manager intentionally wants `$codex-impl` behavior for a specific loop or task.

In compatibility mode, Manager fixes the usual kickoff choices in the Worker prompt so the Worker does not ask the user again:

- implementation gap-check count: `1` for this bounded task branch only
- review/fix loop limit: `skip review` unless the task says otherwise
- Worker QA profile: from task or `PROFILE.md`
- QA target: this task branch diff

This Worker-local gap check is not the final plan/spec coverage gate. Manager runs a separate Gap Auditor against the integration branch when the loop needs to confirm that the original repository plan/spec is still aligned with the combined implementation.

## Blocking

Worker must report `status: blocked` instead of guessing when the task requires:

- user-visible behavior change not specified by the task
- public API or DB schema decision
- security, privacy, auth, or authorization policy
- backward compatibility tradeoff
- irreversible migration or destructive operation
- staging/preview QA that needs unavailable credentials or URLs

Worker records blocking questions in its result artifact. Manager decides whether to create a decision record.
