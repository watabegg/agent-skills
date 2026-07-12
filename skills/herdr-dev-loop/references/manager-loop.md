# Manager Loop

Manager owns integration and final judgment.

## Start Of Tick Checklist

1. Select one explicit `--namespace`, run `hloop namespaces`, and confirm legacy `.ai/loop` is ignored.
2. Run `hloop version` before other work and make the first progress message identify the runtime version, namespace, loop version, and run ID.
3. Run `hloop doctor`.
4. Read `.ai/herdr-dev-loop/loops/<namespace>/MISSION.md`.
5. Read `.ai/herdr-dev-loop/loops/<namespace>/PLAN.md`.
6. Read `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`.
7. Read `.ai/herdr-dev-loop/loops/<namespace>/STATE.json`.
8. Read `.ai/herdr-dev-loop/loops/<namespace>/DECISIONS.md`.
9. Run `hloop dashboard` to inspect phase, queues, pane ids, worktrees, artifacts, and next actions.
10. Run `hloop conductor --no-fail` when resuming a long-running workspace or when the next action is unclear.
11. Check current branch and branch strategy against `STATE.json` and `PROFILE.md`.
12. Check `git status --short`.

`hloop` enforces the same preflight for mutating commands. Treat a preflight failure as an environmental block, not as a reason to continue by hand.

Use an explicit helper command such as `HLOOP="python3 /home/.../skills/herdr-dev-loop/scripts/hloop"` when bare `hloop` is not on `PATH`. A PATH miss for the convenience name is not a loop blocker and must not trigger manual state editing, manual worktree orchestration, or direct `codex exec` launches.

Set `--worktree-root ../wt/<goal>` at init when the repository requires a particular worktree location. Pump and every role-specific start command inherit it. Use `hloop task update` for write scope, acceptance, validation, protocol, QA, or agent changes; do not patch both the task file and `STATE.json` manually.

With `branch-history`, checkpoint newly created or updated task contracts before starting a role. With `local-only`, hloop copies the selected namespace into each role worktree, so task and gate inputs do not need branch-history commits.

Mutating `hloop` commands are serialized with the repo-local Git lock from `git rev-parse --git-path hloop.lock`, but Manager should still treat them as transactions. Do not run `hloop task new`, `tick`, `pump`, `worker harvest`, `merge`, `validate`, `triage`, `gap`, or `reviewer` mutating commands in parallel. Parallelize reads and inspections only.

`dashboard`, `status`, `conductor`, and `doctor --sessions` are read-only inspection commands. Prefer them over manually reading panes one by one when deciding the next Manager action.

## Pump Mode

Use `tick --once` while inspecting a new repository or uncertain state. Use `pump` when the loop is stable and Manager wants queue-drain behavior:

```bash
hloop pump --max-transitions 20 --max-workers 3 --stop-on-triage
```

`pump` repeatedly runs safe tick transitions. It stops at triage, blocked, done, or the transition limit. By default it keeps ticking through waiting phases so it can notice completed agents, start pending Reviewers/Gap Auditors, and dispatch non-overlapping queued Workers. It sleeps briefly between ticks (`--sleep-ms`, default 2000) so waiting phases do not burn the transition budget instantly. Pass `--sleep-ms 0` only when an external scheduler will invoke `pump` again. Pass `--stop-on-waiting` when Manager intentionally wants to pause as soon as all currently safe transitions are exhausted. By default it does not wait for long-running Reviewers or Gap Auditors; pass `--wait` only when Manager intentionally wants to spend the configured wait budget.

Before switching from `pump` to manual intervention, run:

```bash
hloop conductor --no-fail
```

Resolve the concrete finding it reports. Examples: use `hloop worker harvest` when a result artifact is ready, `hloop triage review` / `hloop triage gap` when a harvested gate needs triage, `hloop ... message` when a ready role-agent TUI needs Manager input, or fix the branch/dirty-tree mismatch before the next mutation.

When the only useful action is waiting for a Worker, Reviewer, or Gap Auditor artifact, prefer:

```bash
hloop wait next --harvest --timeout-ms 300000
hloop wait T001 --harvest --timeout-ms 300000
```

This replaces ad hoc `sleep`, `watch`, and `test -f` polling. `wait` does not hold the loop lock while sleeping; with `--harvest` it takes the lock only for the final harvest transition.

Use `hloop batch start "<title>"` when a group of related tasks or review/gap fix tasks should appear as one readable local-history unit. It creates `.ai/herdr-dev-loop/loops/<namespace>/batches/BNNN.md` and sets `STATE.json.current_batch_id` unless `--no-current` is used. While a current batch exists, `hloop task new` and triage-created fix tasks attach to it by default.

Use `hloop checkpoint --batch BNNN --rollup --message "ai-loop(BNNN): ..."` for Manager-owned `.ai/herdr-dev-loop/loops/<namespace>` commits. It stages only `.ai/herdr-dev-loop/loops/<namespace>` paths by default and excludes prompts and legacy lock files unless explicitly requested. `--rollup` amends only when HEAD is an unpushed hloop loop-state checkpoint for the same batch and contains no product paths; otherwise it creates a new checkpoint commit. Use `--force` only when intentionally recording ignored loop artifacts such as validation logs.

## Default Cadence

Keep the loop active by default:

- dispatch up to `max_workers: 3` Workers when write scopes do not overlap
- open the review gate after each validated integration merge (`review_after_merges: 1`)
- open the gap gate less often (`gap_after_merges: 3`) and before final completion
- allow Gap Auditor and Reviewer to run while Workers continue on isolated branches
- do not merge Worker branches while Gap Auditor or Reviewer is reading the integration branch

## Product Profile

Use `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md` as the Manager-owned policy layer. It decides:

- branch strategy: default `integration`, or product-specific `pr-per-task` / `custom`
- Worker protocol: default `native`, or compatibility `codex-impl`
- Reviewer protocol: default `native`, or compatibility `codex-review-multi-v2`
- Worker / Reviewer / Gap Auditor / Advisor agent provider and model
- review lanes
- Worker QA profile
- Manager final QA profile
- Advisor policy: disabled by default; explicit request only

If `branch_strategy` is `pr-per-task` or `custom`, update `PLAN.md` with the exact merge, PR, release, and QA handoff before dispatching Workers. `hloop` can still coordinate tasks, panes, artifacts, review, gap checks, and triage, but Manager must not silently apply the default integration-branch assumptions.

When a Worker is merge-ready under a non-`integration` branch strategy, `tick` / `pump` stop in `branch_handoff`. Manager then follows `PROFILE.md` and `PLAN.md` for the product-specific PR, release branch, or manual merge path before continuing.

When Gap Auditor or Reviewer findings are actionable, create fix-task drafts with `hloop triage gap <gap-id>` or `hloop triage review <review-id>`. Review the draft, then rerun with `--create-tasks` when the tasks are acceptable. Close the gate with `fix-tasks-created`. Those fix Workers join the next dispatch phase. After the fix tasks merge and validation passes, the review/gap counters drive the next audit cycle.

When a review/gap finding raises a hard implementation strategy or specification-shaping question that does not require user input, Manager may create an explicit Advisor request:

```bash
hloop advisor request --topic "..." --mode dialogue --participant codex:auto --participant claude:opus --source reviews/R001.md
hloop advisor start A001 --participant-id P1
hloop advisor harvest A001 --participant-id P1
hloop advisor start A001 --participant-id P2
hloop advisor harvest A001 --participant-id P2
```

Advisor cannot close gates, create tasks, merge, or edit code. Manager must record the accepted recommendation in `DECISIONS.md`, accepted risk notes, or fix tasks, then close the request with `hloop advisor close`.

## TUI Follow-Up Messages

When Manager needs to add requirements or clarify scope for a running Worker or Reviewer, write the follow-up prompt to `.ai/herdr-dev-loop/loops/<namespace>/inbox/manager/` and send it with:

```bash
hloop worker message T001 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/T001-followup.md
hloop gap message G001 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/G001-followup.md
hloop reviewer message R001 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/R001-followup.md
hloop advisor message A001 --participant-id P1 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/A001-P1-followup.md
```

Do not send follow-ups directly with `herdr pane run` unless debugging the pane itself. `hloop ... message` refuses to send when the target pane is not Codex, is showing the trust prompt, or is still working. It then uses `send-text`, waits for the input to appear, pauses before Enter, and verifies that Codex started working or answered before reporting success. If delivery fails after Manager wrote a follow-up, `hloop` records the undelivered message under `.ai/herdr-dev-loop/loops/<namespace>/inbox/pending/` and marks it in the target state; retry that pending file when the pane is ready instead of reconstructing the instruction from memory.

## Harvest Rules

For each running Worker:

- check Herdr pane output only as a hint
- inspect live progress with `hloop worker watch <task-id>` when Manager needs status before the artifact exists
- prefer `results/<task-id>/result.md`
- parse status and merge readiness
- require the result artifact to be committed at Worker `HEAD`
- compute actual changed files from git
- compare changed files to `write_allow` and `write_deny`
- after harvesting the result artifact, close the Worker pane and clean up provider session state when supported unless `--keep-pane` is needed for inspection

Worker `partial`, `blocked`, `failed`, `abandoned`, `merge_ready: false`, missing validation, blocking questions, or uncommitted result artifacts are hard stops for that task. Manager must not edit the Worker result frontmatter to make it merge-ready, must not invent `head_sha` or commit metadata, and must not manually merge a task that `hloop merge` rejects. Create a fix task, rerun the Worker, or record an environment blocker instead.

For each running Gap Auditor:

- read `gaps/<gap-id>.md`
- treat artifact frontmatter `status` as `artifact_status`; use `gate_status` for Manager workflow progress
- inspect live progress with `hloop gap watch <gap-id>` when Manager needs status before the artifact exists
- expect the audit to take several minutes; wait patiently only after other safe Manager work is exhausted
- while the audit is running, do not advance the integration branch being audited
- if the audit is still running, harvest finished Workers, prepare task/validation notes, or dispatch safe queued Workers up to `max_workers` instead of idling
- treat the gap worktree as disposable; harvest copies `.ai/herdr-dev-loop/loops/<namespace>/gaps/<gap-id>.md` back to the Manager repo and removes the worktree when no write-scope violation occurred
- triage gaps into fix task, decision, accepted risk, stale-spec update, or false positive
- never ask Gap Auditor to edit code
- after harvesting the gap artifact, close the Gap Auditor pane and clean up provider session state when supported unless `--keep-pane` is needed for inspection
- generate fix-task drafts with `hloop triage gap <gap-id>` before closing as `fix-tasks-created`
- close the gap gate with `hloop gap close <gap-id> --verdict <aligned|accepted-risk|fix-tasks-created|decision-needed|stale-spec-updated|blocked>`

For each running Reviewer:

- read `reviews/<review-id>.md`
- treat artifact frontmatter `status` as `artifact_status`; use `gate_status` for Manager workflow progress
- inspect live progress with `hloop reviewer watch <review-id>` when Manager needs status before the artifact exists
- expect the review to take several minutes; wait patiently only after other safe Manager work is exhausted
- while the review is running, do not advance the integration branch being reviewed
- if the review is still running, harvest finished Workers, prepare task/validation notes, or dispatch safe queued Workers up to `max_workers` instead of idling
- treat the review worktree as disposable; harvest copies `.ai/herdr-dev-loop/loops/<namespace>/reviews/<review-id>.md` back to the Manager repo and removes the worktree when no write-scope violation occurred
- triage findings into fix task, decision, accepted risk, or false positive
- never ask Reviewer to edit code
- after harvesting the review artifact, close the Reviewer pane and clean up provider session state when supported unless `--keep-pane` is needed for inspection
- generate fix-task drafts with `hloop triage review <review-id>` before closing as `fix-tasks-created`
- close the review gate with `hloop reviewer close <review-id> --verdict <passed|accepted-risk|fix-tasks-created>`

## Triage Rules

P0/P1 findings normally create a fix task. If rejected as false positive, record the code evidence in `JOURNAL.md`.

P2 findings create a fix task, accepted risk, or follow-up depending on whether they affect `MISSION.md` done criteria.

P3 findings should not block completion unless the mission explicitly requires them.

`hloop triage` separates valid and rejected Fix Task Candidates. Rejected candidates are written to the triage draft with reasons such as missing `write_allow`, `acceptance`, `rationale`, or invalid priority. Do not rerun with `--create-tasks` expecting rejected candidates to become tasks; either fix the source artifact/candidate block, create a task manually, or record why the finding is not actionable.

Do not mark the loop done only because a review artifact exists. Manager must close the review gate after triage.

Gap findings are not generic review findings. `missing`, `partial`, and `needs-decision` items that affect `MISSION.md` done criteria normally create a fix task or a decision. `obsolete-spec` items should update or explicitly retire the stale plan/spec source before closing the gap gate.

Do not mark the loop done only because a gap artifact exists. Manager must close the gap gate after triage.

Specification choices that cannot be resolved from the original plan/spec belong in `DECISIONS.md`. If user input is required, also update `USER_ACTION_REQUIRED.md`, set the phase to `blocked_user_decision`, and stop dispatching new Workers until the decision is resolved.

## Manager Final QA

Worker QA is task-local. Manager final QA is a separate combined-implementation gate controlled by `manager_qa_profile`.

When `manager_qa_profile` is `none`, no separate final QA gate is required.

When `manager_qa_profile` is `local`, `preview`, `staging`, `repo-default`, or `custom`:

- run it only after integration validation, review gates, and gap gates are closed for the current implementation head
- record final QA with `hloop qa record --status passed --summary "..."`
- include URLs, screenshots/logs, data setup, cleanup, or blockers with repeated `--evidence`
- use `--status accepted-risk` only when Manager intentionally accepts the remaining QA risk and records why
- use `--status blocked` or `failed` when final QA cannot run or finds a blocking issue

If another Worker merge occurs after Manager final QA, `hloop merge` resets `manager_qa_status` to `pending` when final QA is required.

## Final Report

When done, generate `reports/FINAL.md` with:

- goal id
- base and integration branch
- merged tasks
- cleanup status for local Worker branches/worktrees
- validation commands and results
- Manager final QA profile, status, and artifact
- validation log paths from `.ai/herdr-dev-loop/loops/<namespace>/validation/`
- branch strategy, Worker QA profile, and Manager final QA profile
- role agent providers/models
- gap status
- review status
- advice status when Advisor was used
- accepted risks
- remaining follow-ups
