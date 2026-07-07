# State Machine

Use a bounded state machine. Do not let Manager, Worker, Gap Auditor, or Reviewer coordinate through freeform chat when an artifact can represent the state.

## Phases

- `initialized`: `.ai/loop` exists and the integration branch is known.
- `planning`: Manager is still refining `MISSION.md`, `PLAN.md`, or task boundaries.
- `dispatching`: queued tasks can be started.
- `running`: at least one Worker or Reviewer is active.
- `harvesting`: Manager is reading result or review artifacts.
- `merging`: Manager is merging one Worker branch.
- `validating`: Manager is running integration validation.
- `gap_checking`: Gap Auditor is running or its artifact is being triaged.
- `reviewing`: Reviewer is running or its artifact is being triaged.
- `waiting_worker`: Workers are still running and no result artifact is ready.
- `waiting_gap`: a Gap Auditor is still running after the bounded wait.
- `waiting_review`: a Reviewer is still running after the bounded wait.
- `blocked_user_decision`: a blocking decision is required from the user.
- `blocked_environment`: required tool, credentials, branch, or worktree state is missing.
- `blocked_conflict`: merge conflict or write-scope conflict needs judgment.
- `failed_validation`: integration validation failed and no obvious local fix was applied.
- `no_progress`: no safe transition exists and Manager inspection is required.
- `done`: all tasks are merged, validation passes, and no blocking gap or review finding remains.

## Tick Order

Each tick starts by reading:

1. `.ai/loop/MISSION.md`
2. `.ai/loop/PLAN.md`
3. `.ai/loop/STATE.json`
4. `.ai/loop/DECISIONS.md`

Then run, at most, one material transition:

1. harvest completed Workers, Gap Auditors, or Reviewers
2. close any harvested Worker/Gap Auditor/Reviewer pane and archive the captured Codex session unless inspection is explicitly requested
3. merge one ready Worker when no Gap Auditor or Reviewer is currently reading the integration branch
4. validate integration
5. require Manager triage for harvested Gap Auditor or Reviewer artifacts
6. dispatch queued Workers in a batch up to the safe worker limit
7. start one Gap Auditor when validation passes and the gap gate is open
8. start one Reviewer when validation passes and the review gate is open
9. wait for a running Gap Auditor or Reviewer only after no other safe transition is available
10. generate a final report

Prefer a small number of obvious transitions over attempting to finish a goal in one tick.

`pump` repeats this bounded tick order up to `--max-transitions`. It must stop when:

- a Gap Auditor or Reviewer artifact needs Manager triage
- all safe immediate transitions are exhausted and the loop is waiting, only when `--stop-on-waiting` is set
- a blocked, failed, no-progress, or done phase is reached
- the transition limit is reached

Do not let `pump` turn review/gap findings directly into queued tasks without Manager approval. Use `hloop triage ...` to draft fix tasks first, then rerun with `--create-tasks` only after the draft is accepted.

## Default Cadence

Defaults are intentionally active:

- `max_workers: 3`
- `max_reviewers: 1`
- `max_gap_auditors: 1`
- `review_after_merges: 1`
- `gap_after_merges: 3`

Reviewer should normally run after each validated integration advance. Gap Auditor is lower frequency and should run every three validated merges, or before final completion if no fresh gap audit covers the latest integration state.

## Gap And Reviewer Wait Behavior

Assume gap audits and reviews can take minutes. A running Gap Auditor or Reviewer is not the same as a reported artifact; do not print `gap triage required` or `review triage required` until the artifact exists and has been harvested.

While a Gap Auditor or Reviewer is running:

- do not merge Worker branches into the integration branch under review
- harvest finished Workers and close their panes
- dispatch queued Workers up to `max_workers` when their write scopes are non-overlapping and the state machine allows it
- start Gap Auditor and Reviewer on the same integration head in separate ticks when both gates are open
- inspect live Gap Auditor output with `hloop gap watch <gap-id>` instead of guessing from pane status alone
- inspect live Reviewer output with `hloop reviewer watch <review-id>` instead of guessing from pane status alone
- wait up to `gap_wait_ms` or `review_wait_ms` when there is no other safe work
- tick again later if the wait times out

When the gap artifact appears, harvest it from the detached gap worktree, verify the Gap Auditor changed no files except the gap artifact, close the Gap Auditor pane, archive the captured Codex session, remove the gap worktree, and require Manager triage before closing the gap gate.

When the review artifact appears, harvest it from the detached review worktree, verify the Reviewer changed no files except the review artifact, close the Reviewer pane, archive the captured Codex session, remove the review worktree, and require Manager triage before closing the review gate.

## Stop Conditions

Set a blocked or failed phase and stop when:

- `HERDR_ENV=1` is absent.
- required CLI tools are missing.
- `STATE.json` is unreadable or contradicts the current branch.
- a pending decision has `Blocking: true`.
- a Worker result is missing required fields.
- a Worker changed files outside its allowed scope.
- a merge conflict appears.
- validation fails.
- Gap Auditor reports a missing, partial, or needs-decision item that affects mission done criteria.
- Reviewer reports a P0/P1 finding that cannot be fixed without a user decision.

Do not dispatch new Workers while blocked.

Waiting for a running Worker, Gap Auditor, or Reviewer is not itself a hard failure. Set `waiting_worker`, `waiting_gap`, or `waiting_review`, report the exact agent ids, and tick again later. Set `no_progress` only when no agent is running, no dependency can advance, and the next Manager action is unclear.
