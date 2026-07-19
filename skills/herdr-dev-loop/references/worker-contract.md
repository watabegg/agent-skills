# Worker Contract

Worker receives one task and one worktree.

The initial semantic ACK probes Git metadata and binds one completion mode to the attempt. Apply the approved ACK with `hloop agent ack status <task-id> --attempt-id <attempt-id> --apply`; do not infer the mode from the current filesystem. A contract re-ACK keeps the original attempt mode.

Revision-3 work is submitted as a nonterminal implementation candidate before finalization. In commit mode, commit the product changes first and run `hloop worker submit <task-id> --completion-mode commit` with validation, invariant, regression, self-review, residual-risk, and unrun-check evidence. In handoff mode, do not stage or commit; run the same command with `--completion-mode handoff`. The candidate keeps `merge_ready: false` and is bound to the attempt, task contract, semantic ACK, base, exact product tree, and candidate revision.

When the task requires Patch Review, wait for a review of that exact candidate. A fix creates a new candidate revision and invalidates the older review. After every required gate passes, commit mode runs `hloop worker finalize` without `--handoff`; handoff mode runs it with `--handoff` and asks Manager to seal the quiesced worktree. Do not hand-copy result identity fields or switch completion mode at submit/finalize time.

Default runner: interactive role-agent TUI. Codex is the default provider, but Manager may select Claude or a specific model in `PROFILE.md`, task frontmatter, or `hloop worker start`. This keeps the Worker visible in Herdr so the Manager can add requirements, inspect progress, or interrupt the Worker before it finishes. Use `--runner exec` only for well-bounded automation tasks that should complete without interaction.

The Worker prompt must say:

- make the first progress message identify `herdr-dev-loop <version> / namespace <namespace> / Worker <task-id>`
- read `MISSION.md`, `PLAN.md`, `PROFILE.md`, `DECISIONS.md`, and `tasks/<task-id>.md`
- follow the HLoop Worker Protocol by default
- edit only `write_allow`
- do not edit Manager-owned loop files
- write `results/<task-id>/result.md`
- include the prompt-provided `skill_version` in the result artifact
- obey the attempt-bound commit or handoff result path
- print `HERDR_LOOP_TASK_DONE:<task-id>:<done|blocked|failed|partial>`
- submit a semantic `ack` report after read-only investigation and before material edits
- submit `milestone` only when achievement, risk, or next action changes; use `attention` when Manager action is required
- submit `completion` with artifact, head SHA, validation references, residual risks, and handoff before the final sentinel

## HLoop Worker Protocol

Native Worker protocol is self-contained and does not depend on `$codex-impl`.

Worker must:

1. Re-read loop artifacts and the task contract from disk.
2. Verify the task is still valid against the current code.
3. Compare acceptance criteria against the implementation before editing.
4. Submit `hloop agent report --type ack` with the understood goal, scope, acceptance, and approach. Stop before material edits if the contract is wrong.
5. Implement the smallest coherent change inside `write_allow`.
6. Self-review the diff for correctness, product behavior, security/privacy, data integrity, UX, and validation risk.
7. Run validation commands that fit the task and repository.
8. Apply the Worker QA profile from `PROFILE.md` or task frontmatter.
9. Learn and apply the Manager-approved completion mode, then submit the exact revision-3 implementation candidate with all five QA evidence classes.
10. Resolve required Patch Review and full-suite gates against that candidate; a changed candidate must be resubmitted and reviewed again.
11. Finalize through the approved commit or handoff path, then submit a completion report. The report does not replace the candidate, Patch Review, final result, or Manager seal.

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
