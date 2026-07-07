# Artifact Contract

All loop coordination is file-backed under `.ai/loop`.

## Directory Layout

```text
.ai/loop/
  MISSION.md
  PLAN.md
  STATE.json
  JOURNAL.md
  DECISIONS.md
  USER_ACTION_REQUIRED.md
  tasks/
  results/
  gaps/
  reviews/
  prompts/
  triage/
  validation/
  reports/
```

## STATE.json

Required top-level fields:

- `goal_id`
- `phase`
- `base_branch`
- `integration_branch`
- `cycle`
- `max_workers`
- `max_reviewers`
- `max_gap_auditors`
- `tasks`
- `reviews`
- `gaps`
- `blocking_decisions`

Treat `pane_id` as advisory only. Re-read Herdr pane state before acting on a pane id.

Recommended optional fields:

- `session_cleanup`: `archive`, `none`, or `delete`; default to `archive`
- `review_wait_ms`: bounded wait for a running Reviewer before returning control
- `gap_wait_ms`: bounded wait for a running Gap Auditor before returning control
- `review_after_merges`: validated integration merge count that opens the review gate; default `1`
- `gap_after_merges`: validated integration merge count that opens the gap gate; default `3`
- `unreviewed_merge_count`: integration merges not yet covered by a closed review gate
- `ungapped_merge_count`: integration merges not yet covered by a closed gap gate
- `spec_sources`: original repo plan/spec files or directories the Gap Auditor should compare against implementation
- per task/gap/review `pane_closed_at`, `pane_cleanup_status`, `pane_cleanup_error`
- per task/gap/review `codex_session_id`, `codex_session_cleanup`, `codex_session_cleanup_error`
- per review `worktree`, `worktree_review_path_harvested`, `worktree_cleanup_status`
- per review `write_scope_violations`
- per review `triage_drafts`, `created_fix_tasks`
- per gap `worktree`, `worktree_gap_path_harvested`, `worktree_cleanup_status`
- per gap `write_scope_violations`
- per gap `triage_drafts`, `created_fix_tasks`
- `last_validation.results[].log`: relative path to captured stdout/stderr under `.ai/loop/validation/`

Do not keep completed agent pane transcripts as durable state. Harvest artifacts first, then close panes and record cleanup status in `STATE.json`.

Reviewer artifacts are written in a detached review worktree first, then copied back to the Manager repo during harvest. The review worktree may use `workspace-write`, but only `.ai/loop/reviews/<review-id>.md` is an allowed Reviewer write.

Gap Auditor artifacts are written in a detached gap worktree first, then copied back to the Manager repo during harvest. The gap worktree may use `workspace-write`, but only `.ai/loop/gaps/<gap-id>.md` is an allowed Gap Auditor write.

## Task File

Each task is `tasks/TNNN.md` with frontmatter:

```md
---
id: T001
kind: implementation
status: queued
branch: ai/example/T001-login
base_ref: ai/example/integration
base_sha: abc123
priority: P1
depends_on: []
write_allow:
  - src/auth/**
  - tests/auth/**
write_deny:
  - db/migrations/**
acceptance:
  - Relevant auth tests pass.
validation_minimum: L1
---
```

`write_allow` is mandatory for implementation tasks. `write_deny` is optional but should be used for migrations, generated files, or unrelated subsystems.

## Worker Result

Each Worker writes `results/<task-id>/result.md` in its worktree:

```md
---
task_id: T001
status: done
merge_ready: true
branch: ai/example/T001-login
head_sha: HEAD
base_sha: abc123
changed_files:
  - src/auth/login.ts
validation_recorded: true
validation_commands:
  - npm test -- tests/auth/login.test.ts
validation_results:
  - passed
validation_summary: "auth login tests passed"
blocking_questions: []
---
```

Allowed `status` values:

- `done`
- `partial`
- `blocked`
- `failed`
- `abandoned`

Only `status: done` may set `merge_ready: true`.

`merge_ready: true` requires `validation_recorded: true`. Keep validation fields flat; do not use nested YAML maps in frontmatter because `hloop` intentionally uses a stdlib-only parser.

The result artifact must be committed on the Worker branch at `HEAD:.ai/loop/results/<task-id>/result.md`. `hloop worker harvest` rejects artifacts that exist only in the worktree or differ from the committed version.

Use `head_sha: HEAD` when writing the artifact from the Worker branch. `hloop worker harvest` resolves it to the actual branch head; writing the exact commit SHA inside the same commit is not required.

## Review Artifact

Each Reviewer writes `reviews/RNNN.md`:

```md
---
review_id: R001
base: main
head: ai/example/integration
status: reported
---

# Review R001
```

Findings use fixed severities:

- `P0`: data loss, security break, app cannot boot
- `P1`: correctness bug or serious regression
- `P2`: edge case, important missing test, maintainability risk with bug potential
- `P3`: nit or non-blocking cleanup

Manager must triage every P0/P1 and any P2 that affects the mission done criteria.

Review artifacts should include:

```md
## Fix Task Candidates

### FT001: Fix concrete regression
action: fix_task
severity: P1
write_allow:
  - src/example/**
acceptance:
  - The regression is fixed.
rationale: The reviewed code path can fail when ...
```

`hloop triage review R001` reads this section and writes `.ai/loop/triage/R001.fix-task-draft.md`. It creates queued tasks only when Manager reruns with `--create-tasks`.

## Gap Artifact

Each Gap Auditor writes `gaps/GNNN.md`:

```md
---
gap_id: G001
base: main
head: ai/example/integration
status: gaps-found
spec_sources:
  - spec/product-plan.md
gap_count: 2
---

# Gap Audit G001
```

Allowed `status` values:

- `aligned`: implementation matches the relevant plan/spec contract
- `gaps-found`: one or more implementation/spec alignment gaps were found
- `blocked`: the auditor could not complete without a Manager or user decision
- `failed`: the auditor failed for an operational reason

Findings should classify each checked requirement as one of:

- `implemented`
- `partial`
- `missing`
- `deferred`
- `obsolete-spec`
- `needs-decision`

Manager must triage every `missing`, `partial`, or `needs-decision` item that affects `MISSION.md` done criteria.

Gap artifacts should include the same `## Fix Task Candidates` shape for missing or partial requirements that should become Worker fix tasks. Use `priority` instead of `severity` when that is more natural:

```md
## Fix Task Candidates

### FT001: Implement missing plan requirement
action: fix_task
priority: P1
write_allow:
  - src/example/**
acceptance:
  - The plan requirement is implemented.
rationale: The plan requires X, but the integration branch only implements Y.
```

## Triage Draft

`hloop triage review R001` and `hloop triage gap G001` write:

```text
.ai/loop/triage/R001.fix-task-draft.md
.ai/loop/triage/G001.fix-task-draft.md
```

Drafts are Manager-reviewed artifacts. They do not make tasks runnable. Rerun triage with `--create-tasks` after Manager approval to create queued fix tasks under `.ai/loop/tasks/`.
