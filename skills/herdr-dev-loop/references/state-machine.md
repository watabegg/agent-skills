# State Machine

Use a bounded state machine. Do not let Manager, Worker, or Reviewer coordinate through freeform chat when an artifact can represent the state.

## Phases

- `initialized`: `.ai/loop` exists and the integration branch is known.
- `planning`: Manager is still refining `MISSION.md`, `PLAN.md`, or task boundaries.
- `dispatching`: queued tasks can be started.
- `running`: at least one Worker or Reviewer is active.
- `harvesting`: Manager is reading result or review artifacts.
- `merging`: Manager is merging one Worker branch.
- `validating`: Manager is running integration validation.
- `reviewing`: Reviewer is running or its artifact is being triaged.
- `waiting_worker`: Workers are still running and no result artifact is ready.
- `waiting_review`: a Reviewer is still running after the bounded wait.
- `blocked_user_decision`: a blocking decision is required from the user.
- `blocked_environment`: required tool, credentials, branch, or worktree state is missing.
- `blocked_conflict`: merge conflict or write-scope conflict needs judgment.
- `failed_validation`: integration validation failed and no obvious local fix was applied.
- `no_progress`: no safe transition exists and Manager inspection is required.
- `done`: all tasks are merged, validation passes, and no blocking review finding remains.

## Tick Order

Each tick starts by reading:

1. `.ai/loop/MISSION.md`
2. `.ai/loop/PLAN.md`
3. `.ai/loop/STATE.json`
4. `.ai/loop/DECISIONS.md`

Then run, at most, one material transition:

1. harvest completed Workers or Reviewers
2. close any harvested Worker/Reviewer pane and archive the captured Codex session unless inspection is explicitly requested
3. merge one ready Worker when no Reviewer is currently reading the integration branch
4. validate integration
5. start one Reviewer
6. dispatch queued Workers in a batch up to the safe worker limit
7. wait for a running Reviewer only after no other safe transition is available
8. generate a final report

Prefer a small number of obvious transitions over attempting to finish a goal in one tick.

## Reviewer Wait Behavior

Assume review can take minutes. A running Reviewer is not the same as a reported review; do not print `review triage required` until the artifact exists and has been harvested.

While a Reviewer is running:

- do not merge Worker branches into the integration branch under review
- harvest finished Workers and close their panes
- dispatch queued Workers up to `max_workers` when their write scopes are non-overlapping and the state machine allows it
- inspect live Reviewer output with `hloop reviewer watch <review-id>` instead of guessing from pane status alone
- wait up to `review_wait_ms` when there is no other safe work
- tick again later if the wait times out

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
- Reviewer reports a P0/P1 finding that cannot be fixed without a user decision.

Do not dispatch new Workers while blocked.

Waiting for a running Worker or Reviewer is not itself a hard failure. Set `waiting_worker` or `waiting_review`, report the exact agent ids, and tick again later. Set `no_progress` only when no agent is running, no dependency can advance, and the next Manager action is unclear.
