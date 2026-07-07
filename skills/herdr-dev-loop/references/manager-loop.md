# Manager Loop

Manager owns integration and final judgment.

## Start Of Tick Checklist

1. Run `hloop doctor`.
2. Read `.ai/loop/MISSION.md`.
3. Read `.ai/loop/PLAN.md`.
4. Read `.ai/loop/PROFILE.md`.
5. Read `.ai/loop/STATE.json`.
6. Read `.ai/loop/DECISIONS.md`.
7. Check current branch and branch strategy against `STATE.json` and `PROFILE.md`.
8. Check `git status --short`.

`hloop` enforces the same preflight for mutating commands. Treat a preflight failure as an environmental block, not as a reason to continue by hand.

## Pump Mode

Use `tick --once` while inspecting a new repository or uncertain state. Use `pump` when the loop is stable and Manager wants queue-drain behavior:

```bash
hloop pump --max-transitions 20 --max-workers 3 --stop-on-triage
```

`pump` repeatedly runs safe tick transitions. It stops at triage, blocked, done, or the transition limit. By default it keeps ticking through waiting phases so it can notice completed agents, start pending Reviewers/Gap Auditors, and dispatch non-overlapping queued Workers. Pass `--stop-on-waiting` when Manager intentionally wants to pause as soon as all currently safe transitions are exhausted. By default it does not wait for long-running Reviewers or Gap Auditors; pass `--wait` only when Manager intentionally wants to spend the configured wait budget.

## Default Cadence

Keep the loop active by default:

- dispatch up to `max_workers: 3` Workers when write scopes do not overlap
- open the review gate after each validated integration merge (`review_after_merges: 1`)
- open the gap gate less often (`gap_after_merges: 3`) and before final completion
- allow Gap Auditor and Reviewer to run while Workers continue on isolated branches
- do not merge Worker branches while Gap Auditor or Reviewer is reading the integration branch

## Product Profile

Use `.ai/loop/PROFILE.md` as the Manager-owned policy layer. It decides:

- branch strategy: default `integration`, or product-specific `pr-per-task` / `custom`
- Worker protocol: default `native`, or compatibility `codex-impl`
- Reviewer protocol: default `native`, or compatibility `codex-review-multi-v2`
- review lanes
- Worker QA profile
- Manager final QA profile

If `branch_strategy` is `pr-per-task` or `custom`, update `PLAN.md` with the exact merge, PR, release, and QA handoff before dispatching Workers. `hloop` can still coordinate tasks, panes, artifacts, review, gap checks, and triage, but Manager must not silently apply the default integration-branch assumptions.

When a Worker is merge-ready under a non-`integration` branch strategy, `tick` / `pump` stop in `branch_handoff`. Manager then follows `PROFILE.md` and `PLAN.md` for the product-specific PR, release branch, or manual merge path before continuing.

When Gap Auditor or Reviewer findings are actionable, create fix-task drafts with `hloop triage gap <gap-id>` or `hloop triage review <review-id>`. Review the draft, then rerun with `--create-tasks` when the tasks are acceptable. Close the gate with `fix-tasks-created`. Those fix Workers join the next dispatch phase. After the fix tasks merge and validation passes, the review/gap counters drive the next audit cycle.

## TUI Follow-Up Messages

When Manager needs to add requirements or clarify scope for a running Worker or Reviewer, write the follow-up prompt to `.ai/loop/inbox/manager/` and send it with:

```bash
hloop worker message T001 --file .ai/loop/inbox/manager/T001-followup.md
hloop gap message G001 --file .ai/loop/inbox/manager/G001-followup.md
hloop reviewer message R001 --file .ai/loop/inbox/manager/R001-followup.md
```

Do not send follow-ups directly with `herdr pane run` unless debugging the pane itself. `hloop ... message` refuses to send when the target pane is not Codex, is showing the trust prompt, or is still working. It then uses `send-text`, waits for the input to appear, pauses before Enter, and verifies that Codex started working or answered before reporting success.

## Harvest Rules

For each running Worker:

- check Herdr pane output only as a hint
- prefer `results/<task-id>/result.md`
- parse status and merge readiness
- require the result artifact to be committed at Worker `HEAD`
- compute actual changed files from git
- compare changed files to `write_allow` and `write_deny`
- after harvesting the result artifact, close the Worker pane and archive the captured Codex session unless `--keep-pane` is needed for inspection

For each running Gap Auditor:

- read `gaps/<gap-id>.md`
- treat artifact frontmatter `status` as `artifact_status`; use `gate_status` for Manager workflow progress
- inspect live progress with `hloop gap watch <gap-id>` when Manager needs status before the artifact exists
- expect the audit to take several minutes; wait patiently only after other safe Manager work is exhausted
- while the audit is running, do not advance the integration branch being audited
- if the audit is still running, harvest finished Workers, prepare task/validation notes, or dispatch safe queued Workers up to `max_workers` instead of idling
- treat the gap worktree as disposable; harvest copies `.ai/loop/gaps/<gap-id>.md` back to the Manager repo and removes the worktree when no write-scope violation occurred
- triage gaps into fix task, decision, accepted risk, stale-spec update, or false positive
- never ask Gap Auditor to edit code
- after harvesting the gap artifact, close the Gap Auditor pane and archive the captured Codex session unless `--keep-pane` is needed for inspection
- generate fix-task drafts with `hloop triage gap <gap-id>` before closing as `fix-tasks-created`
- close the gap gate with `hloop gap close <gap-id> --verdict <aligned|accepted-risk|fix-tasks-created|decision-needed|stale-spec-updated|blocked>`

For each running Reviewer:

- read `reviews/<review-id>.md`
- treat artifact frontmatter `status` as `artifact_status`; use `gate_status` for Manager workflow progress
- inspect live progress with `hloop reviewer watch <review-id>` when Manager needs status before the artifact exists
- expect the review to take several minutes; wait patiently only after other safe Manager work is exhausted
- while the review is running, do not advance the integration branch being reviewed
- if the review is still running, harvest finished Workers, prepare task/validation notes, or dispatch safe queued Workers up to `max_workers` instead of idling
- treat the review worktree as disposable; harvest copies `.ai/loop/reviews/<review-id>.md` back to the Manager repo and removes the worktree when no write-scope violation occurred
- triage findings into fix task, decision, accepted risk, or false positive
- never ask Reviewer to edit code
- after harvesting the review artifact, close the Reviewer pane and archive the captured Codex session unless `--keep-pane` is needed for inspection
- generate fix-task drafts with `hloop triage review <review-id>` before closing as `fix-tasks-created`
- close the review gate with `hloop reviewer close <review-id> --verdict <passed|accepted-risk|fix-tasks-created>`

## Triage Rules

P0/P1 findings normally create a fix task. If rejected as false positive, record the code evidence in `JOURNAL.md`.

P2 findings create a fix task, accepted risk, or follow-up depending on whether they affect `MISSION.md` done criteria.

P3 findings should not block completion unless the mission explicitly requires them.

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
- validation log paths from `.ai/loop/validation/`
- branch strategy, Worker QA profile, and Manager final QA profile
- gap status
- review status
- accepted risks
- remaining follow-ups
