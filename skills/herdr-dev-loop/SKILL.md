---
name: herdr-dev-loop
description: Orchestrate a bounded Herdr-managed multi-agent coding loop with interactive Worker and Reviewer Codex TUI panes. Use inside Herdr when Codex needs to run Manager, Worker, and Reviewer agents across git worktrees, persist the goal in .ai/loop artifacts, make Workers use $codex-impl, make Reviewers use $codex-review-multi-v2, merge into an integration branch, monitor Reviewer panes, harvest review artifacts from detached review worktrees, and stop on blocking specification decisions or unsafe state.
---

# Herdr Dev Loop

Use this skill as the Manager Agent for a file-backed development loop. The loop coordinates isolated Worker Codex agents and independent Reviewer Codex agents through git branches, worktrees, Herdr panes, and `.ai/loop` artifacts.

This skill is intentionally conservative. Prefer one bounded tick at a time until the loop has proven stable for the current repository.

## Required Preflight

Before starting or continuing a loop:

1. Verify `HERDR_ENV=1`.
2. Run `python3 <this-skill>/scripts/hloop doctor`.
3. Confirm `herdr`, `codex`, `git`, `$codex-impl`, and `$codex-review-multi-v2` are available.
4. Read the current `.ai/loop/MISSION.md`, `.ai/loop/PLAN.md`, `.ai/loop/STATE.json`, and `.ai/loop/DECISIONS.md` if they exist.
5. Continue from disk state, not from thread memory.

If `HERDR_ENV=1` is absent, stop and tell the user this skill requires Herdr.

## Quick Start

Use the helper script instead of hand-typing pane prompts:

```bash
python3 <this-skill>/scripts/hloop doctor
python3 <this-skill>/scripts/hloop init --goal-id <goal-id> --goal "<goal>" --base <main-or-master> --create-branch --merge-mode squash --worker-runner tui --reviewer-runner tui --max-workers 3 --session-cleanup archive --review-wait-ms 600000
python3 <this-skill>/scripts/hloop task new "Implement bounded slice" --write-allow 'src/foo/**' --write-allow 'tests/foo/**'
git add .ai/loop && git commit -m "ai-loop: initialize goal"
python3 <this-skill>/scripts/hloop tick --once --max-workers 3
```

The script is deliberately explicit. Use `--dry-run` on `worker start`, `reviewer start`, and `tick` when checking the commands before spawning panes.

Worker and Reviewer agents default to interactive Codex TUI panes so the Manager can inspect progress, add requirements, or interrupt them in Herdr. Reviewers run in detached review worktrees with `workspace-write` sandbox so the final review report can be written reliably; the prompt and harvest guard still forbid code edits. Override with `--runner exec` only when non-interactive review is intentionally preferred.

After a Worker or Reviewer artifact is harvested, close its Herdr pane and archive its captured Codex session unless the Manager intentionally passes `--keep-pane` or `--session-cleanup none` for inspection. Treat `.ai/loop` artifacts as the durable record; do not leave completed agent panes open as informal state.

## Source Of Truth

The durable state lives under `.ai/loop`:

- `MISSION.md`: user goal, constraints, non-goals, base branch, integration branch, done criteria
- `PLAN.md`: task graph, parallelization rules, validation plan, review plan
- `STATE.json`: current phase, branches, task/review status, pane ids, worktrees
- `DECISIONS.md`: pending, accepted, and rejected specification decisions
- `USER_ACTION_REQUIRED.md`: blocking questions for the user
- `tasks/*.md`: Worker task contracts
- `results/<task-id>/result.md`: Worker completion artifacts
- `reviews/*.md`: Reviewer artifacts
- `reports/FINAL.md`: final report

If thread memory and `.ai/loop` disagree, trust `.ai/loop` and record the discrepancy in `JOURNAL.md`.

## Roles

Manager owns:

- integration branch
- `.ai/loop/MISSION.md`, `PLAN.md`, `STATE.json`, `DECISIONS.md`, `JOURNAL.md`
- task creation, merge decisions, validation, review triage, and user escalation

Worker owns:

- only its branch and worktree
- only paths allowed by the task `write_allow`
- `.ai/loop/results/<task-id>/result.md`

Reviewer owns:

- only `.ai/loop/reviews/<review-id>.md`
- no code edits

Do not let Workers edit `STATE.json`, `MISSION.md`, `PLAN.md`, other task files, or other result files.

## Loop

Run bounded ticks:

```bash
python3 skills/herdr-dev-loop/scripts/hloop tick --once --max-workers 3 --stop-on-user-decision
```

Each tick must:

1. Preflight the environment and disk state.
2. Harvest completed Workers or Reviewers.
3. Validate result artifacts and write scopes.
4. Integrate at most one Worker branch into the integration branch with squash merge by default.
5. Run integration validation.
6. Start or harvest a Reviewer when needed.
7. Triage review findings into fix tasks, decisions, accepted risk, or false positives.
8. Dispatch queued tasks only when `write_allow` patterns do not overlap.
9. Stop if done, blocked, or unsafe.

Do not run an unbounded loop. Prefer `--once`; use `--max-cycles` only after the workflow is stable.

Assume Reviewer runs can take several minutes. Use `hloop reviewer watch <review-id>` to inspect the TUI pane. If a Reviewer is still running, wait up to `review_wait_ms` only after all other safe transitions for the tick have been considered. While waiting for review completion, continue Manager work that does not mutate the reviewed integration head: refine tasks, prepare validation notes, harvest finished Workers, or dispatch non-overlapping queued Workers up to `max_workers`. Do not merge Worker branches while a Reviewer is actively reading the integration branch.

## References

Load only the reference needed for the current operation:

- `references/state-machine.md`: phases, tick behavior, and stop conditions
- `references/artifact-contract.md`: required `.ai/loop` file shapes and frontmatter
- `references/branch-policy.md`: branch/worktree topology and merge rules
- `references/manager-loop.md`: Manager checklist and triage rules
- `references/worker-contract.md`: Worker prompt contract and `$codex-impl` usage
- `references/reviewer-contract.md`: Reviewer prompt contract and `$codex-review-multi-v2` usage
- `references/decision-policy.md`: blocking decision criteria
- `references/validation-policy.md`: validation levels and command selection
- `references/public-repo-safety.md`: files and data that must not be committed
- `references/prompts.md`: prompt templates used by `hloop`
- `references/cli-notes.md`: local Herdr and Codex CLI assumptions to re-check with `hloop doctor`

## Hard Stops

Stop immediately when:

- `HERDR_ENV=1` is not set.
- `.ai/loop/STATE.json` is missing for a non-init operation.
- a blocking user decision exists.
- a Worker changed files outside `write_allow` or inside `write_deny`.
- a Worker reports `partial`, `blocked`, `failed`, or `merge_ready: false`.
- merge conflict requires judgment.
- integration validation fails and rollback is not obvious.
- Reviewer reports P0/P1 that needs a user decision.
- no progress was made in the last tick.

When stopped, update `STATE.json`, `JOURNAL.md`, and `USER_ACTION_REQUIRED.md` with the concrete blocker before asking the user.
