# Artifact Contract

All loop coordination is file-backed under `.ai/herdr-dev-loop/loops/<namespace>`.

## Directory Layout

```text
.ai/herdr-dev-loop/loops/<namespace>/
  MISSION.md
  PLAN.md
  PROFILE.md
  STATE.json
  JOURNAL.md
  DECISIONS.md
  USER_ACTION_REQUIRED.md
  tasks/
  batches/
  results/
  gaps/
  reviews/
  advice/
  prompts/
  triage/
  validation/
  qa/
  inbox/
  reports/
```

## STATE.json

Required top-level fields:

- `state_format_version`
- `goal_id`
- `run_id`
- `skill_version`
- `namespace`
- `loop_path`
- `persistence`
- `worktree_setup_commands`
- `worker_setup_commands`
- `reviewer_setup_commands`
- `gap_setup_commands`
- `advisor_setup_commands`
- `phase`
- `base_branch`
- `integration_branch`
- `worktree_root`
- `branch_strategy`
- `worker_qa_profile`
- `manager_qa_profile`
- `manager_qa_status`
- `worker_protocol`
- `review_protocol`
- `worker_agent_provider`
- `worker_agent_model`
- `worker_agent_effort`
- `worker_claude_permission_mode`
- `reviewer_agent_provider`
- `reviewer_agent_model`
- `reviewer_agent_effort`
- `reviewer_claude_permission_mode`
- `gap_agent_provider`
- `gap_agent_model`
- `gap_agent_effort`
- `gap_claude_permission_mode`
- `review_lanes`
- `cycle`
- `max_workers`
- `max_reviewers`
- `max_gap_auditors`
- `tasks`
- `batches`
- `reviews`
- `gaps`
- `advice`
- `blocking_decisions`

Treat `pane_id` as advisory only. Re-read Herdr pane state before acting on a pane id.

`namespace` and `loop_path` must match the Manager command's explicit `--namespace`. A command never searches another namespace or legacy `.ai/loop` when the selected `STATE.json` is missing.

`persistence: local-only` copies the namespace snapshot to role worktrees and excludes loop artifacts from integration commits. `persistence: branch-history` requires Manager-owned inputs to be committed at the audited ref. `worktree_setup_commands` contains the ordered repository-specific bootstrap contract applied before role launch; run outcomes are stored separately under `.ai/herdr-dev-loop/experience/worktree-setup.json`.

Recommended optional fields:

- `session_cleanup`: `archive`, `none`, or `delete`; default to `archive`
- `review_wait_ms`: bounded wait for a running Reviewer before returning control
- `gap_wait_ms`: bounded wait for a running Gap Auditor before returning control
- `review_after_merges`: validated integration merge count that opens the review gate; default `1`
- `gap_after_merges`: validated integration merge count that opens the gap gate; default `3`
- `unreviewed_merge_count`: integration merges not yet covered by a closed review gate
- `ungapped_merge_count`: integration merges not yet covered by a closed gap gate
- `spec_sources`: original repo plan/spec files or directories the Gap Auditor should compare against implementation
- `current_batch_id`: active `Bxxx` task batch for rolling loop-state checkpoint commits
- per task/gap/review/advisor `pane_closed_at`, `pane_cleanup_status`, `pane_cleanup_error`
- per task/gap/review/advisor `agent_session_id`, `agent_session_provider`, `agent_session_cleanup`, `agent_session_cleanup_error`
- per task/gap/review/advisor legacy Codex fields `codex_session_id`, `codex_session_cleanup`, `codex_session_cleanup_error` when the provider is Codex
- per task/gap/review/advisor `worktree_cleanup_status`, `worktree_cleanup_error`, `worktree_cleanup_failed_at`
- per task/gap/review/advisor `sandbox`: expected `workspace-write` for hloop-started agents; other values are trust findings in `hloop conductor`
- per task/review/gap/advisor `agent_provider`, `agent_model`, and `fallback_provider`
- per review `worktree`, `worktree_review_path_harvested`
- per review `write_scope_violations`
- per review `gate_status`: Manager gate status such as `running`, `reported`, `triaged`, or `blocked_write_scope`
- per review `artifact_status`: artifact frontmatter status such as `reported`, `blocked`, or `failed`
- per review `triage_drafts`, `created_fix_tasks`
- per gap `worktree`, `worktree_gap_path_harvested`
- per gap `write_scope_violations`
- per gap `gate_status`: Manager gate status such as `running`, `reported`, `triaged`, or `blocked_write_scope`
- per gap `artifact_status`: artifact frontmatter status such as `aligned`, `gaps-found`, `blocked`, or `failed`
- per gap `triage_drafts`, `created_fix_tasks`
- per advice `mode`, `source_refs`, `participants`, `gate_status`, and `verdict`
- per advisor participant `provider`, `model`, `worktree`, `worktree_advice_path_harvested`, `artifact_status`, `write_scope_violations`
- `last_validation.results[].log`: relative path to captured stdout/stderr under `.ai/herdr-dev-loop/loops/<namespace>/validation/`

Do not keep completed agent pane transcripts as durable state. Harvest artifacts first, then close panes and record cleanup status in `STATE.json`.

## PROFILE.md

`PROFILE.md` is Manager-owned and records product-specific loop policy:

- branch strategy: `integration`, `pr-per-task`, or `custom`
- Worker protocol: `native` or `codex-impl`
- Reviewer protocol: `native` or `codex-review-multi-v2`
- Worker / Reviewer / Gap Auditor / Advisor agent provider: `codex` or `claude`
- Worker / Reviewer / Gap Auditor / Advisor agent model: provider-specific model name, or `auto`
- review lanes
- Worker QA profile: `repo-default`, `local`, `staging`, `preview`, `custom`, or `none`
- Manager final QA profile: `repo-default`, `local`, `staging`, `preview`, `custom`, or `none`
- shared worktree root for every subordinate role, or the legacy sibling-path default

When `branch_strategy` is not `integration`, Manager must record the concrete merge, PR, or release handoff in `PLAN.md` before dispatching Workers.

Reviewer artifacts are written in a detached review worktree first, then copied back to the Manager repo during harvest. The review worktree may use `workspace-write`, but only `.ai/herdr-dev-loop/loops/<namespace>/reviews/<review-id>.md` is an allowed Reviewer write.

Gap Auditor artifacts are written in a detached gap worktree first, then copied back to the Manager repo during harvest. The gap worktree may use `workspace-write`, but only `.ai/herdr-dev-loop/loops/<namespace>/gaps/<gap-id>.md` is an allowed Gap Auditor write.

Advisor artifacts are written in detached advisor worktrees first, then copied back to the Manager repo during harvest. Advisor worktrees may write only `.ai/herdr-dev-loop/loops/<namespace>/advice/<advice-id>-<participant-id>.md`. Advisors are never started automatically by `tick` or `pump`; Manager must explicitly create and start an advice request.

## Task File

Each task is `tasks/TNNN.md` with frontmatter:

```md
---
id: T001
run_id: 20260712T000000Z-example
skill_version: 0.3.0
kind: implementation
status: queued
branch: ai/example/T001-login
base_ref: ai/example/integration
base_sha: abc123
priority: P1
batch_id: B001
depends_on: []
write_allow:
  - src/auth/**
  - tests/auth/**
write_deny:
  - db/migrations/**
acceptance:
  - Relevant auth tests pass.
validation_minimum: L1
worker_protocol: native
worker_qa_profile: repo-default
worker_agent_provider: codex
worker_agent_model: auto
---
```

`write_allow` is mandatory and non-empty for implementation and fix tasks unless Manager explicitly uses `--allow-no-write` for an exceptional no-edit task. Use `kind: research` for ordinary no-edit investigation tasks. `write_deny` is optional but should be used for migrations, generated files, or unrelated subsystems. `validation_minimum` may be a single level such as `L1` or a multiline list when the task needs multiple explicit validation requirements.

`worker_agent_provider` and `worker_agent_model` optionally override the default Worker backend from `PROFILE.md` for a single task.

## Batch File

Each batch is `batches/BNNN.md` and groups related implementation and fix tasks into a readable local-history unit. A batch is smaller than `MISSION.md` / `PLAN.md` and larger than an individual task.

```md
---
id: B001
title: Booking review fixes
status: active
task_ids:
  - T001
  - T002
started_at: 2026-07-09T00:00:00+00:00
closed_at: ""
checkpoint_mode: rollup
summary: ""
---
```

Allowed `status` values:

- `active`: Manager may attach newly created tasks and loop-state checkpoints to this batch.
- `closed`: the batch is complete and should no longer receive new tasks unless Manager explicitly reopens or assigns them.
- `abandoned`: the batch is intentionally stopped.

When `STATE.json.current_batch_id` is set, `hloop task new` and triage-created fix tasks attach to that batch by default. `hloop checkpoint --batch BNNN --rollup` records loop-state-only `.ai/herdr-dev-loop/loops/<namespace>` changes with `HLoop-Checkpoint: loop-state` and `HLoop-Batch: BNNN` trailers. Rollup may amend only the current unpushed HEAD commit when that HEAD commit is a `.ai/herdr-dev-loop/loops/<namespace>`-only checkpoint for the same batch.

## Worker Result

Each Worker writes `results/<task-id>/result.md` in its worktree:

```md
---
task_id: T001
run_id: 20260712T000000Z-example
skill_version: 0.3.0
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

`merge_ready: true` requires `status: done`, `validation_recorded: true`, and non-empty `validation_commands` / `validation_results`. Keep validation fields flat; do not use nested YAML maps in frontmatter because `hloop` intentionally uses a stdlib-only parser.

Write non-empty list fields as multiline lists. `hloop` rejects non-empty inline lists for known list fields such as `validation_commands`, `validation_results`, `changed_files`, `blocking_questions`, `write_allow`, `write_deny`, `acceptance`, `depends_on`, and `spec_sources` because comma-splitting command strings is unsafe. Empty lists such as `blocking_questions: []` remain allowed.

The result artifact must be committed on the Worker branch at `HEAD:.ai/herdr-dev-loop/loops/<namespace>/results/<task-id>/result.md`. `hloop worker harvest` rejects artifacts that exist only in the worktree or differ from the committed version.

Use `head_sha: HEAD` when writing the artifact from the Worker branch. `hloop worker harvest` resolves it to the actual branch head; writing the exact commit SHA inside the same commit is not required.

Prefer `hloop worker finalize <task-id> --validation-command ... --validation-result ...` after committing product changes. It derives branch, base SHA, changed files, run ID, merge readiness, and the result path from Git and the task contract, then commits the artifact unless `--no-commit` is passed.

Every task and role artifact records `skill_version`. Reviewer, Gap Auditor, and Advisor artifacts must also record the current `run_id` and exact audited `head_sha`. Harvest rejects artifacts produced by a different skill version, an older loop run, or a different integration head. Loops migrated from before version tracking use `unversioned` until a role is started with a versioned runtime.

`hloop init --force` moves the previous `.ai/herdr-dev-loop/loops/<namespace>` tree under `.ai/herdr-dev-loop/archive/<namespace>/<timestamp>-<goal>/` before creating a new run.

Manager must not edit a Worker result artifact to change task status, merge readiness, commit metadata, validation results, QA evidence, or blocking questions. If the Worker artifact is `partial`, `blocked`, `failed`, uncommitted, mismatched with `HEAD`, or otherwise rejected by `hloop worker harvest` / `hloop merge`, treat it as a task blocker and resolve it by rerunning the Worker, creating a fix task, or recording an environment blocker.

## Manager Final QA Artifact

When `manager_qa_profile` is not `none`, Manager records final combined QA in `qa/FINAL.md`:

```md
---
manager_qa_profile: staging
status: passed
summary: "Staging booking flow passed"
evidence:
  - https://staging.example.test/booking
  - .ai/herdr-dev-loop/loops/<namespace>/reports/screenshots/booking.png
---
```

Allowed statuses:

- `passed`: final QA evidence is sufficient.
- `accepted-risk`: Manager accepts the remaining QA risk and records why.
- `blocked`: required final QA cannot run because a URL, credential, service, data dependency, or cleanup path is missing.
- `failed`: final QA found a blocking product failure.
- `not-required`: final QA is disabled because `manager_qa_profile: none`.

## Review Artifact

Each Reviewer writes `reviews/RNNN.md`:

```md
---
review_id: R001
run_id: 20260712T000000Z-example
skill_version: 0.3.0
base: main
head: ai/example/integration
head_sha: abc123
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

The review artifact frontmatter `status` is copied into `STATE.json.reviews.<id>.artifact_status`. Manager gate progress is tracked separately in `STATE.json.reviews.<id>.gate_status`; after harvest it is `reported`, and after Manager triage it is `triaged`. The legacy `STATE.json.reviews.<id>.status` mirrors the gate status for compatibility.

Review artifacts should include `## Fix Task Candidates` blocks for findings that should become Worker fix tasks. Each candidate must include `action`, `severity` or `priority`, non-empty `write_allow`, non-empty `acceptance`, and non-empty `rationale`; otherwise `hloop triage` lists it under rejected candidates and does not create a task from it. Markdown code spans around `write_allow` / `write_deny` paths are normalized before task creation, but plain path-only values remain preferred.

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

`hloop triage review R001` reads this section and writes `.ai/herdr-dev-loop/loops/<namespace>/triage/R001.fix-task-draft.md`, including rejected candidates and reasons when candidate blocks are incomplete. It creates queued tasks only from valid candidates when Manager reruns with `--create-tasks`.

## Gap Artifact

Each Gap Auditor writes `gaps/GNNN.md`:

```md
---
gap_id: G001
run_id: 20260712T000000Z-example
skill_version: 0.3.0
base: main
head: ai/example/integration
head_sha: abc123
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

The gap artifact frontmatter `status` is copied into `STATE.json.gaps.<id>.artifact_status`. Manager gate progress is tracked separately in `STATE.json.gaps.<id>.gate_status`; after harvest it is `reported`, and after Manager triage it is `triaged`. The legacy `STATE.json.gaps.<id>.status` mirrors the gate status for compatibility.

Gap artifacts should include the same `## Fix Task Candidates` shape for missing or partial requirements that should become Worker fix tasks. Use `priority` instead of `severity` when that is more natural. Each candidate must include `action`, `priority` or `severity`, non-empty `write_allow`, non-empty `acceptance`, and non-empty `rationale`:

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

## Advisor Artifacts

Advisor is an explicit consultation role. Manager creates a request only when a non-user-blocking specification or fix-strategy judgment benefits from another model or cross-model comparison.

The request summary is `advice/ANNN.md`:

```md
---
advice_id: A001
status: requested
mode: dialogue
max_rounds: 2
participants:
  - "P1:codex:auto"
  - "P2:claude:opus"
source_refs:
  - reviews/R001.md
---

# Advice Request A001
```

Each participant writes `advice/ANNN-PN.md`:

```md
---
advice_id: A001
participant_id: P1
run_id: 20260712T000000Z-example
skill_version: 0.3.0
head_sha: abc123
provider: claude
model: opus
status: advised
---

# Advice A001/P1
```

Allowed participant `status` values:

- `advised`: the participant produced a usable recommendation
- `blocked`: the participant needs Manager/user information before advising
- `failed`: the participant failed operationally

In dialogue mode, `max_rounds` bounds each participant's initial prompt plus delivered Manager follow-up messages. The default is `2`, allowing one delivered `hloop advisor message` follow-up per participant. Additional follow-ups are rejected by `hloop`.

Advisor outputs are not decisions. Manager must harvest participant artifacts and then close the request with `hloop advisor close A001 --verdict <decision-recorded|fix-tasks-created|accepted-risk|no-action|blocked> --reason ...` after recording any accepted decision, fix task, accepted risk, or user escalation in the appropriate Manager-owned artifact.

## Triage Draft

`hloop triage review R001` and `hloop triage gap G001` write:

```text
.ai/herdr-dev-loop/loops/<namespace>/triage/R001.fix-task-draft.md
.ai/herdr-dev-loop/loops/<namespace>/triage/G001.fix-task-draft.md
```

Drafts are Manager-reviewed artifacts. They do not make tasks runnable. Rerun triage with `--create-tasks` after Manager approval to create queued fix tasks under `.ai/herdr-dev-loop/loops/<namespace>/tasks/`.
