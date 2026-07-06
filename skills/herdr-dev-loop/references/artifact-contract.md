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
