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
- `blocked_user_decision`: a blocking decision is required from the user.
- `blocked_environment`: required tool, credentials, branch, or worktree state is missing.
- `blocked_conflict`: merge conflict or write-scope conflict needs judgment.
- `failed_validation`: integration validation failed and no obvious local fix was applied.
- `done`: all tasks are merged, validation passes, and no blocking review finding remains.

## Tick Order

Each tick starts by reading:

1. `.ai/loop/MISSION.md`
2. `.ai/loop/PLAN.md`
3. `.ai/loop/STATE.json`
4. `.ai/loop/DECISIONS.md`

Then run, at most, one material transition:

1. harvest completed Workers or Reviewers
2. merge one ready Worker
3. validate integration
4. start one Reviewer
5. dispatch queued Workers up to the safe worker limit
6. generate a final report

Prefer a small number of obvious transitions over attempting to finish a goal in one tick.

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
