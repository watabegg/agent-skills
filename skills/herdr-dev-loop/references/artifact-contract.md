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
  inputs/                 # local-only redacted input records
  tasks/
  batches/
  results/
  gaps/
  reviews/
    convergence/
    final/
  advice/
  release-scope/
    amendments/
  follow-ups/
  prompts/
  triage/
  validation/
  qa/
  inbox/                  # local-only report and wake records
  broker/                 # local-only durable broker storage
  broker-spool/           # local-only outage recovery spool
  reports/
```

## STATE.json

Required top-level fields:

- `state_format_version`
- `schema_revision`
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
- `decisions`

Treat `pane_id` as advisory only. Re-read Herdr pane state before acting on a pane id.

`namespace` and `loop_path` must match the Manager command's explicit `--namespace`. A command never searches another namespace or legacy `.ai/loop` when the selected `STATE.json` is missing.

Current format 3 state must include `schema_revision`. A format 3 artifact without that field is treated only as the legacy 3.r0 migration source; run `hloop migrate --dry-run` and then `hloop migrate --apply` to write current 3.r2 state.

The current 0.5.2 contract is `state_format_version: 3` and `schema_revision: 2`. Mutation rejects an unknown future revision. Migration preserves `run_id`, writes a versioned backup, and applies every declared revision rather than rebinding old evidence to the new schema. The runtime chain accepts format 1/2 and format 3 revision 0/1, then reaches format 3 revision 2.

### 0.5.2 policy blocks

New state includes the following policy and evidence blocks:

- `review_policy`: new-loop batch cadence, pre-final/manual-final protocol, maximum two automatic fix rounds, scope-expansion action, complete-zero final requirement, and lane count.
- `release_scope`: lock status, source refs and digests, semantic `scope_revision`, editorial `source_snapshot_revision`, plan/requirement refs, and amendment refs.
- `dispatch_freeze`: whether new task/role starts are frozen, why, the authorizing input, and the running roles allowed to finish.
- `review_convergence`: fixed base/target SHA, prepared plan, current fix round, extra-round authorizations, manifest completeness, and verified actionable finding count.
- `manual_final_review`: certification id, PLAN/MANIFEST/report paths and digest, fixed target SHA, completeness, verified actionable finding count, and attempt history.
- `follow_ups`: first-class artifact refs, open count, stable issue keys, aliases, and exported report paths.
- `manager_invocation` and `execution_metrics`: Manager provider/model/reasoning effort plus task-origin, disposition, remediation-round, stale/aborted-review, and parallelism metrics.

Legacy migration initializes these blocks with legacy-safe statuses. In particular, a migrated legacy loop keeps its stored merge-count cadence, marks existing tasks `legacy-unclassified`, and uses `not-required-for-legacy-run` for manual final certification.

`persistence: local-only` copies the namespace snapshot to role worktrees and excludes loop artifacts from integration commits. `persistence: branch-history` requires Manager-owned inputs to be committed at the audited ref. `worktree_setup_commands` contains the ordered repository-specific bootstrap contract applied before role launch; run outcomes are stored separately under `.ai/herdr-dev-loop/experience/worktree-setup.json`.

Recommended optional fields:

- `session_cleanup`: `archive`, `none`, or `delete`; default to `archive`
- `review_wait_ms`: bounded wait for a running Reviewer before returning control
- `gap_wait_ms`: bounded wait for a running Gap Auditor before returning control
- `review_after_merges`: legacy/explicit `merge-count` validated integration merge count that opens the review gate; default `1`
- `gap_after_merges`: legacy/explicit `merge-count` validated integration merge count that opens the gap gate; default `3`
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
- `config_source` and `resolved_config`: the configuration file identity and immutable init snapshot
- `attempt_history`: append-only role attempt identities
- `resume_requirements`: stale gates, blockers, dirty paths, and running roles discovered while paused
- `pending_fix_task_drafts` and `final_gate`: stable final-review arm state
- `terminal_outcome`: final target and terminal status
- `artifact_policy`: canonical active/harvested locations and local-only artifact classes

Raw or redacted input bodies, inbox events, broker databases, sockets, spooled reports, and provider credentials are never checkpoint-eligible. `STATE.json` may retain safe digests, IDs, counts, and resolved non-secret configuration, but not the underlying prompt or transport secret.

Accepted requirements, their progress, and machine-readable decision records currently live under `STATE.json.requirements` and `STATE.json.decisions`. `DECISIONS.md` remains the human-readable decision ledger. The 0.5.2 CLI does not create separate `requirements/`, `progress/`, `context/`, or `decisions/` directories; release scope, convergence, manual final, and follow-up records use the namespaced paths described below.

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

### Task provenance after release-scope lock

Once `STATE.json.release_scope.status` is `locked`, every new task records immutable authorization metadata in addition to `kind`:

- `task_origin`: `planned`, `finding`, `user-amendment`, or `operational`; migrated legacy tasks use `legacy-unclassified` and cannot be used as the origin for a new task.
- `release_scope_revision`: the locked scope revision at task creation.
- `plan_item_refs`, `requirement_refs`, and `scope_refs`: references required for a `planned` task.
- `source_finding`, `why_fix_now`, `origin`, `contract_relation`, `release_effect`, `fact_status`, `disposition`, and `remediation_round`: evidence required for a `finding` task.
- `authorization_input_id`: required for a `user-amendment` task together with the updated scope revision.
- `operational_reason`: required for an `operational` task; it cannot authorize a product or release-artifact behavior change.

`hloop task new`, review/gap triage, and other task-creation paths use one authorization preflight. `task update` may add details but cannot erase provenance or change the task origin. A new origin requires closing the old task and creating another task from the new evidence.

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

### Durable handoff and Manager seal

A Codex `workspace-write` Worker may not be able to run `git add`/`git commit` at all. `hloop worker finalize <task-id> --handoff` supports this: it tolerates dirty product paths that are already inside `write_allow`, writes `result.md` with `handoff: true`, and performs no Git metadata writes. Nothing is staged or committed; the Worker's product edits and `result.md` remain plain uncommitted files in the worktree.

Manager (not the Worker) then runs `hloop worker seal <task-id> [--attempt-id <id>] [--validation-command <command>...] [--validation-summary <text>]` from the Manager checkout to turn that handoff into a normal committed Worker result. Seal fails closed, before staging or committing anything, if any of the following hold:

- the task has no running attempt, or `--attempt-id` does not match the Manager's recorded active attempt
- the semantic ACK barrier is not approved
- the Worker's pane cannot be confirmed quiesced and closed first (see below)
- the worktree's current branch does not match the task's recorded branch, the root or an initialized nested Git index has `assume-unchanged` / `skip-worktree` entries, or the index already has staged changes that do not exactly match a validated seal transaction this same command previously recorded and crashed before committing (see crash recovery below)
- `result.md` is missing, has no frontmatter, or does not record `handoff: true`
- the artifact's `task_id`, `run_id`, `skill_version`, or `attempt_id` does not match the active attempt
- the handoff's declared `changed_files` does not exactly match the measured write scope (stale artifact)
- the combined write scope -- everything already committed on top of `base_sha` plus every currently dirty (tracked, untracked, or deleted) path, with rename detection off so a rename's source cannot hide behind its destination -- has any file outside `write_allow` or inside `write_deny`
- for `status: done`, Manager did not supply at least one `--validation-command`, or one of them fails when actually run in an isolated snapshot of the Worker's worktree (see below)
- for `partial`/`blocked`/`failed`, the Worker's own declared validation fields are internally inconsistent (mismatched command/result counts, an unsupported result value, or evidence present while `validation_recorded` is false)
- the worktree changes underneath seal while it is staging (a re-check after `git add` requires no unstaged/untracked path remains and the staged diff matches exactly what was scanned)

Before touching Git or running any validation, seal confirms the Worker's `pane_id` is quiesced (not busy, no pending trust prompt) and closes it, refusing to proceed if there is no pane record to close, the pane is the current Manager pane, or the close itself fails -- so nothing can still be mutating the worktree while seal reads or commits it.

For `status: done`, seal executes the Manager-supplied validation commands for real and overwrites `result.md`'s `validation_recorded`, `validation_commands`, `validation_results`, `validation_summary`, `merge_ready`, and `changed_files` with what it actually measured; this is the one sanctioned exception to "Manager must not edit a Worker result artifact" below, because seal is the only path by which an unwitnessed handoff becomes a trusted result. Other statuses keep the Worker's own declared validation fields byte-for-byte (after the consistency check above), rather than overwriting them with an empty record; merge readiness is always false for them regardless.

Before Manager validation, seal writes the final Manager-measured validation fields that would be eligible to commit, stages the approved product/result scope, re-verifies that exact scope, computes `git write-tree`, and durably records the resulting `staged_tree`, expected parent, attached branch ref, and `validation_passed: false`. The result's pre-recorded `passed` values are predictions inside an uncommitted candidate tree, not evidence by themselves: the ref cannot move until every real command passes and the durable transaction marker becomes true.

Manager validation commands for `status: done` never use the mutable Worker's index or ordinary working files as their root. `build_validation_snapshot` makes a local, no-hardlink clone, attaches its `HEAD` to the recorded parent, loads the recorded `staged_tree` into its independent index, and checks out exactly that tree. It overlays only ignored files/directories and empty directories needed by local validation. Initialized nested Git repositories -- including submodules whose `.git` file points into a linked worktree gitdir -- are discovered separately; each receives its own no-hardlink object store, real `.git` directory, exact copied index and HEAD, and exact working-filesystem overlay. Root and nested `git status`, `git diff`, and `git diff --cached` therefore describe the immutable candidate plus its dependency state, while index, ref, and object writes in the snapshot cannot mutate any original gitdir.

Symlinks still get individual treatment: a relative target must resolve inside the snapshot, an absolute target inside the source worktree is rewritten to the equivalent snapshot path, and an escaping target fails closed before any command runs. Every `--validation-command` runs sequentially in the same snapshot and stops at the first failure. Snapshot deletion first uses ordinary recursive removal, repairs owner permissions and retries for read-only build output, then fails with the retained path if cleanup still cannot complete; cleanup failure prevents success. Snapshot-relative formatter/build effects never enter the source. A trusted validation command that deliberately writes an absolute path back into the original worktree can change it, but that change is never added to the already-recorded tree and survives after sealing only as unstaged content.

After every command and snapshot cleanup pass, seal saves `validation_passed: true`. The index is not the commit source after this point: seal creates one `ai-loop(<task-id>): seal worker handoff (<attempt-id>)` commit with `git commit-tree <recorded-tree> -p <expected-parent>`, persists that commit id, and advances `refs/heads/<worker-branch>` with `git update-ref <ref> <new-commit> <expected-parent>`. The old-value argument is an atomic compare-and-swap; concurrent ref movement fails closed instead of silently changing the parent. The namespaced result artifact is force-staged (`git add -f`) before validation so it remains part of the candidate even when `.ai` is ignored; other approved paths use normal `git add -A` semantics. A successful result remains indistinguishable from a Worker-committed result to harvest and merge.

If content is written or re-staged at the same path after the tree checkpoint, neither normal seal nor validated crash resume can put it into the commit because `commit-tree` reads the recorded tree object, not the mutable index. After the ref CAS succeeds, seal reconciles the index to the landed commit with `git read-tree <commit>` -- an index-only operation -- never a mixed (or hard) reset, which would also set the current branch ref to `<commit>` as a side effect. Seal verifies the branch ref immediately before and after `read-tree`; concurrent movement fails closed without moving it back or clearing the transaction.

Crash recovery treats the validation marker as the authorization boundary. A transaction that crashed before validation passed, or before `validation_passed: true` was durably saved, is never resume-committed. If its branch still equals the recorded parent, seal uses `git read-tree <expected-parent>` to reconcile only the index, preserves all working-file content, clears the unvalidated transaction, and requires supplied commands to run again. A transaction with the durable pass marker retains exact-tree resume: the staged path set and recomputed tree must match before commit-object creation, and an already-created or landed commit must match the recorded tree, single parent, and message. Foreign staged content, stale attempt identity, unexpected objects, or concurrent branch movement still fail closed; tampered content is never committed.

A handoff that is well-formed, correctly identified, and not stale, but simply not yet committed, reports readiness `seal-required` rather than the generic `not-committed`. `wait`, `dashboard`/`status`, `tick`, and `pump` treat `seal-required` as an actionable prompt to run `hloop worker seal <task-id> --validation-command '<manager-approved-command>'` -- never as `terminal_without_artifact`, and never as a reason to abort or requeue the task.

Seal precheck, `artifact_readiness`, and `hloop worker harvest` all validate the Worker result contract through the same shared check: presence and strict type of every field `references/schemas/result.schema.json` marks required (`task_id`, `run_id`, `skill_version`, `attempt_id`, `status`, `merge_ready`, `branch`, `head_sha`, `base_sha`, `changed_files`, `validation_recorded`, `blocking_questions`), the type of optional fields when present, a non-empty active expected attempt in Manager state, a non-empty string artifact `attempt_id` equal to that active attempt, internal validation-record consistency, and the schema's `merge_ready` guard (true only for a `done` status with `validation_recorded` true and non-empty, equal-length, all-`passed` `validation_commands`/`validation_results`). Missing active-attempt state never disables identity validation, and a malformed or incomplete artifact is rejected identically at every call site rather than passing one check and failing another.

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

## Fixed-target convergence artifacts

The 0.5.2 pre-final convergence commands write fixed-target JSON artifacts below:

```text
reviews/convergence/PLAN.json
reviews/convergence/MANIFEST.json
```

`PLAN.json` records `base_ref`, `base_sha`, `target_ref`, `target_sha`, `fix_round`, `max_fix_rounds`, the review plan, readiness checks, protocol, and preparation time. `MANIFEST.json` records the same review-plan identity, lane results, normalized findings, verifier assignments/results, completeness issues, and the recomputed verified actionable finding count. The prepared target SHA and plan must match exactly when the manifest is recorded. An incomplete manifest or a nonzero actionable count keeps convergence open or exhausted; the Manager must not substitute a plain review artifact for these fixed-target records.

Public validation entry points are:

- `schemas/final-review-plan.schema.json`
- `schemas/final-review-manifest.schema.json`

They reference the canonical definitions in `references/schemas/` and are intended for offline JSON-schema validation.

## Manual final review artifacts

`hloop final-review prepare` writes the manual certification bundle below:

```text
reviews/final/PLAN.json
reviews/final/MANIFEST.json
reviews/final/FINAL.md
```

The plan fixes certification id, base/target SHA, base/target ref, scope source and digest, scope revisions, protocol, lane plan, and verification policy. The manifest must include lane completion, verification completeness, all normalized findings and evidence, `manifest_complete`, `verified_actionable_findings`, and `patch_verdict`. The report must be non-empty. `hloop final-review record` recomputes completeness and invalidates the certification when target SHA or plan identity drifts. `finish` accepts only a `passed` certification whose evidence is complete and whose verified actionable finding count is zero. A follow-up may remain open without failing this gate when it is non-blocking and the current contract is satisfied.

## First-class follow-up artifact

Each follow-up is stored as `follow-ups/FNNN.md` and indexed from `STATE.json.follow_ups`. Its issue key has the form `fu:v1:sha256:<64 hex>` and is computed only from normalized `component`, `trigger_class`, `product_impact`, and optional `root_cause`. Review fingerprint, target SHA, severity, title, affected line, and proposed fix are evidence fields and do not create a second issue. Re-adding the same semantic issue updates the existing artifact and returns `deduplicated` instead of allocating another `FNNN`.

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
