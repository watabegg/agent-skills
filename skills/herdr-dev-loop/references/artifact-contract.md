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
  reviews/
  prompts/
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
- `tasks`
- `reviews`
- `blocking_decisions`

Treat `pane_id` as advisory only. Re-read Herdr pane state before acting on a pane id.

Recommended optional fields:

- `session_cleanup`: `archive`, `none`, or `delete`; default to `archive`
- `review_wait_ms`: bounded wait for a running Reviewer before returning control
- per task/review `pane_closed_at`, `pane_cleanup_status`, `pane_cleanup_error`
- per task/review `codex_session_id`, `codex_session_cleanup`, `codex_session_cleanup_error`
- per review `worktree`, `worktree_review_path_harvested`, `worktree_cleanup_status`
- per review `write_scope_violations`

Do not keep completed agent pane transcripts as durable state. Harvest artifacts first, then close panes and record cleanup status in `STATE.json`.

Reviewer artifacts are written in a detached review worktree first, then copied back to the Manager repo during harvest. The review worktree may use `workspace-write`, but only `.ai/loop/reviews/<review-id>.md` is an allowed Reviewer write.

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
head_sha: def456
base_sha: abc123
changed_files:
  - src/auth/login.ts
validation:
  - command: npm test -- tests/auth/login.test.ts
    result: passed
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
