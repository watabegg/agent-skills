# Manager Loop

Manager owns integration and final judgment.

## Start Of Tick Checklist

1. Run `hloop doctor`.
2. Read `.ai/loop/MISSION.md`.
3. Read `.ai/loop/PLAN.md`.
4. Read `.ai/loop/STATE.json`.
5. Read `.ai/loop/DECISIONS.md`.
6. Check current branch against `STATE.json`.
7. Check `git status --short`.

## TUI Follow-Up Messages

When Manager needs to add requirements or clarify scope for a running Worker or Reviewer, write the follow-up prompt to `.ai/loop/inbox/manager/` and send it with:

```bash
hloop worker message T001 --file .ai/loop/inbox/manager/T001-followup.md
hloop reviewer message R001 --file .ai/loop/inbox/manager/R001-followup.md
```

Do not send follow-ups directly with `herdr pane run` unless debugging the pane itself. `hloop ... message` refuses to send when the target pane is not Codex, is showing the trust prompt, or is still working. It then uses `send-text`, waits for the input to appear, pauses before Enter, and verifies that Codex started working or answered before reporting success.

## Harvest Rules

For each running Worker:

- check Herdr pane output only as a hint
- prefer `results/<task-id>/result.md`
- parse status and merge readiness
- compute actual changed files from git
- compare changed files to `write_allow` and `write_deny`
- after harvesting the result artifact, close the Worker pane and archive the captured Codex session unless `--keep-pane` is needed for inspection

For each running Reviewer:

- read `reviews/<review-id>.md`
- inspect live progress with `hloop reviewer watch <review-id>` when Manager needs status before the artifact exists
- expect the review to take several minutes; wait patiently only after other safe Manager work is exhausted
- while the review is running, do not advance the integration branch being reviewed
- if the review is still running, harvest finished Workers, prepare task/validation notes, or dispatch safe queued Workers up to `max_workers` instead of idling
- treat the review worktree as disposable; harvest copies `.ai/loop/reviews/<review-id>.md` back to the Manager repo and removes the worktree when no write-scope violation occurred
- triage findings into fix task, decision, accepted risk, or false positive
- never ask Reviewer to edit code
- after harvesting the review artifact, close the Reviewer pane and archive the captured Codex session unless `--keep-pane` is needed for inspection
- close the review gate with `hloop reviewer close <review-id> --verdict <passed|accepted-risk|fix-tasks-created>`

## Triage Rules

P0/P1 findings normally create a fix task. If rejected as false positive, record the code evidence in `JOURNAL.md`.

P2 findings create a fix task, accepted risk, or follow-up depending on whether they affect `MISSION.md` done criteria.

P3 findings should not block completion unless the mission explicitly requires them.

Do not mark the loop done only because a review artifact exists. Manager must close the review gate after triage.

## Final Report

When done, generate `reports/FINAL.md` with:

- goal id
- base and integration branch
- merged tasks
- cleanup status for local Worker branches/worktrees
- validation commands and results
- review status
- accepted risks
- remaining follow-ups
