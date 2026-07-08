---
name: herdr-dev-loop
description: Orchestrate a bounded Herdr-managed multi-agent coding loop with pump/triage scheduling and interactive Worker, Gap Auditor, and Reviewer Codex TUI panes. Use inside Herdr when Codex needs to run Manager, Worker, Gap Auditor, and Reviewer agents across git worktrees, persist the goal in .ai/loop artifacts, drain task/fix-task queues with hloop pump, use HLoop-native Worker and Reviewer protocols by default, compare original plan/spec sources against implementation, generate Manager-approved fix-task drafts from review/gap artifacts with hloop triage, adapt branch/review/QA strategy through .ai/loop/PROFILE.md, merge into an integration or project-specific branch flow, monitor agent panes, harvest artifacts from detached worktrees, and stop on blocking specification decisions or unsafe state.
---

# Herdr Dev Loop

Use this skill as the Manager Agent for a file-backed development loop. The loop coordinates isolated Worker Codex agents, independent Gap Auditor Codex agents, and independent Reviewer Codex agents through git branches, worktrees, Herdr panes, and `.ai/loop` artifacts.

This skill is intentionally conservative. Prefer one bounded tick at a time until the loop has proven stable for the current repository.

## Required Preflight

Before starting or continuing a loop:

1. Verify `HERDR_ENV=1`.
2. Run `python3 <this-skill>/scripts/hloop doctor`.
3. After installing or updating this skill, run `python3 <this-skill>/scripts/hloop selftest`.
4. Confirm `herdr`, `codex`, and `git` are available. `$codex-impl` and `$codex-review-multi-v2` are optional compatibility protocols, not default dependencies.
5. Read the current `.ai/loop/MISSION.md`, `.ai/loop/PLAN.md`, `.ai/loop/PROFILE.md`, `.ai/loop/STATE.json`, and `.ai/loop/DECISIONS.md` if they exist.
6. Run `python3 <this-skill>/scripts/hloop dashboard` or `conductor --no-fail` when resuming an existing loop so pane/worktree/artifact drift is visible before mutating state.
7. Continue from disk state, not from thread memory.

If `HERDR_ENV=1` is absent, stop and tell the user this skill requires Herdr.

Use the absolute helper path when `hloop` is not installed on `PATH`. A missing bare `hloop` command is not a reason to recreate Worker, Reviewer, Gap Auditor, merge, validation, or triage behavior by hand.

## Quick Start

Use the helper script instead of hand-typing pane prompts:

```bash
HLOOP="python3 <this-skill>/scripts/hloop"
$HLOOP selftest
$HLOOP doctor
$HLOOP init --goal-id <goal-id> --goal "<goal>" --base <main-or-master> --create-branch --merge-mode squash --branch-strategy integration --worker-protocol native --review-protocol native --worker-qa-profile repo-default --manager-qa-profile none --worker-runner tui --gap-runner tui --reviewer-runner tui --max-workers 3 --max-reviewers 1 --max-gap-auditors 1 --review-after-merges 1 --gap-after-merges 3 --session-cleanup archive --gap-wait-ms 600000 --review-wait-ms 600000
$HLOOP task new "Implement bounded slice" --write-allow 'src/foo/**' --write-allow 'tests/foo/**'
git add .ai/loop && git commit -m "ai-loop: initialize goal"
$HLOOP dashboard
$HLOOP pump --max-transitions 20 --max-workers 3
```

Use `tick --once` for one material transition when inspecting a new repository. Use `pump` after the loop is stable; it repeatedly runs safe tick transitions until it reaches triage, blocked, done, or the transition limit. Add `--stop-on-waiting` when Manager wants to pause as soon as all currently safe transitions are exhausted.

The script is deliberately explicit. Use `--dry-run` on `worker start`, `gap start`, `reviewer start`, `tick`, `pump`, and `triage` when checking the commands before spawning panes or creating fix-task drafts.

Mutating `hloop` commands enforce their own preflight. `pump`, `tick`, `worker start`, `worker harvest`, `merge`, `validate`, `triage`, `gap start`, `gap harvest`, `reviewer start`, and `reviewer harvest` check the relevant Herdr environment, current branch, required commands, and non-loop dirty files before changing state.

Treat every mutating `hloop` command as a serialized state transaction. The helper takes a repo-local Git lock (`git rev-parse --git-path hloop.lock`), but Manager should still avoid launching multiple mutating `hloop` commands in parallel. Parallelize reads, not loop-state writes.

Worker, Gap Auditor, and Reviewer agents default to interactive Codex TUI panes so the Manager can inspect progress, add requirements, or interrupt them in Herdr. Gap Auditors and Reviewers run in detached worktrees with `workspace-write` sandbox so the final Markdown artifact can be written reliably; the prompt and harvest guard still forbid code edits. Override with `--runner exec` only when non-interactive work is intentionally preferred.

When sending additional instructions to a running TUI, use `hloop worker message <task-id> --file <prompt.md>`, `hloop gap message <gap-id> --file <prompt.md>`, or `hloop reviewer message <review-id> --file <prompt.md>`. Do not send prompts directly with `herdr pane run` unless you have manually verified the pane is a ready Codex TUI. The helper blocks common mistakes: shell panes, pending Codex trust prompts, and busy Codex sessions. It sends via `send-text`, waits for the input to appear, pauses before Enter, and verifies that Codex started working or answered; if the first Enter races the TUI, it retries.

Inspect running agents with `hloop worker watch <task-id>`, `hloop gap watch <gap-id>`, or `hloop reviewer watch <review-id>`. Use direct `herdr pane read` only for debugging the helper itself.

Use `hloop dashboard` for the Manager's one-screen view of phase, queues, running agents, panes, artifacts, and next actions. Use `hloop conductor` when investigating stuck sessions; it reports P0/P1 attention items such as missing panes, blocked Codex prompts, reported review/gap artifacts needing triage, non-loop dirty files, branch mismatches, unsafe sandbox values, non-hloop prompt paths, unharvested artifacts, untrusted Worker head markers, and manual integration traces. Add `--no-fail` when the command is informational and should not return non-zero.

After a Worker, Gap Auditor, or Reviewer artifact is harvested, close its Herdr pane and archive its captured Codex session unless the Manager intentionally passes `--keep-pane` or `--session-cleanup none` for inspection. Treat `.ai/loop` artifacts as the durable record; do not leave completed agent panes open as informal state.

## Source Of Truth

The durable state lives under `.ai/loop`:

- `MISSION.md`: user goal, constraints, non-goals, base branch, integration branch, done criteria
- `PLAN.md`: task graph, product-specific branch handoff, parallelization rules, validation plan, Worker QA plan, Manager final QA plan, review plan
- `PROFILE.md`: Manager-owned branch strategy, Worker protocol, Reviewer protocol, review lanes, Worker QA profile, and Manager final QA profile
- `STATE.json`: current phase, branches, task/review status, pane ids, worktrees
- `DECISIONS.md`: pending, accepted, and rejected specification decisions that cannot be answered from the original plan/spec alone
- `USER_ACTION_REQUIRED.md`: blocking questions for the user
- `tasks/*.md`: Worker task contracts
- `results/<task-id>/result.md`: Worker completion artifacts
- `gaps/*.md`: Gap Auditor plan/spec alignment artifacts
- `reviews/*.md`: Reviewer artifacts
- `triage/*.fix-task-draft.md`: Manager-reviewed fix-task drafts generated from review/gap artifacts
- `qa/FINAL.md`: Manager-owned final QA evidence when `manager_qa_profile` is not `none`
- `reports/FINAL.md`: final report

If thread memory and `.ai/loop` disagree, trust `.ai/loop` and record the discrepancy in `JOURNAL.md`.

## Roles

Manager owns:

- integration branch or product-specific branch handoff defined in `PROFILE.md` / `PLAN.md`
- `.ai/loop/MISSION.md`, `PLAN.md`, `PROFILE.md`, `STATE.json`, `DECISIONS.md`, `JOURNAL.md`
- task creation, merge decisions, validation, review triage, and user escalation

Worker owns:

- only its branch and worktree
- only paths allowed by the task `write_allow`
- `.ai/loop/results/<task-id>/result.md`

Reviewer owns:

- only `.ai/loop/reviews/<review-id>.md`
- no code edits

Gap Auditor owns:

- only `.ai/loop/gaps/<gap-id>.md`
- no code edits
- no generic code-review findings unless they directly prove plan/spec drift

Do not let Workers edit `STATE.json`, `MISSION.md`, `PLAN.md`, `PROFILE.md`, `DECISIONS.md`, other task files, or other result files.

Do not let Manager edit a Worker result to turn `partial`, `blocked`, or `failed` into `done`. If a Worker cannot commit or reports `partial`, stop, record the blocker, rerun or create a fix task. Do not spoof `head_sha`, commit metadata, validation results, or QA status to pass a gate.

## Loop

Run bounded ticks:

```bash
python3 skills/herdr-dev-loop/scripts/hloop tick --once --max-workers 3 --stop-on-user-decision
```

Run the scheduler pump after the loop has proven stable:

```bash
python3 skills/herdr-dev-loop/scripts/hloop pump --max-transitions 20 --max-workers 3 --stop-on-triage
```

Each tick or pump transition must:

1. Preflight the environment and disk state.
2. Harvest completed Workers, Gap Auditors, or Reviewers.
3. Validate result artifacts and write scopes.
4. Integrate at most one Worker branch according to `PROFILE.md`; built-in automation defaults to squash merge into the integration branch.
5. Run integration validation.
6. Triage harvested Gap Auditor or Reviewer artifacts before starting more work from stale assumptions.
7. Start a Gap Auditor when the gap gate is open; default frequency is lower than review (`gap_after_merges: 3`).
8. Start a Reviewer when the review gate is open; default frequency is high (`review_after_merges: 1`).
9. Dispatch queued implementation or fix Workers up to `max_workers` when `write_allow` patterns do not overlap.
10. Triage gap/review findings into fix tasks, decisions, accepted risk, stale-spec updates, or false positives.
11. Stop if done, blocked, or unsafe.

Do not run an unbounded loop. Prefer `tick --once` while inspecting a new repository; use `pump --max-transitions <n>` only after the workflow is stable.

Use `hloop triage review <review-id>` or `hloop triage gap <gap-id>` to convert machine-readable `Fix Task Candidates` sections into `.ai/loop/triage/*.fix-task-draft.md`. Add `--create-tasks` only after Manager approval; this creates queued fix Workers from the candidates.

Default cadence is intentionally busy: keep up to three Workers running, run Reviewer after each validated integration advance, and run Gap Auditor every three validated merges or before final completion. Gap Auditor and Reviewer may run while Workers continue on isolated branches, but Manager must not merge Worker branches while a Gap Auditor or Reviewer is actively reading the integration branch.

Default protocols are native to this skill:

- Workers follow the HLoop Worker Protocol: inspect context, implement inside write scope, self-review, run repo-appropriate validation/QA, write the result artifact, and commit.
- Reviewers follow the HLoop Native Review Protocol: review task/result artifacts, write-scope and merge safety, product correctness, risk, and validation/QA evidence. Use `$codex-review-multi-v2` only when `review_protocol: codex-review-multi-v2` is intentionally selected.
- `$codex-impl` is only a Worker compatibility mode. Use it only when `worker_protocol: codex-impl` is intentionally selected.

Use `.ai/loop/PROFILE.md` to adapt the loop to product reality. `branch_strategy: integration` enables the built-in integration-branch flow. `pr-per-task` and `custom` require Manager to record the exact handoff in `PLAN.md` and avoid assuming the default merge/publish steps. `worker_qa_profile` is the QA each Worker must record for its task; `manager_qa_profile` is the separate final QA gate Manager records in `qa/FINAL.md` after integration/review/gap gates.

Assume Gap Auditor and Reviewer runs can take several minutes. Use `hloop gap watch <gap-id>` or `hloop reviewer watch <review-id>` to inspect the TUI pane. If an auditor or reviewer is still running, wait up to `gap_wait_ms` or `review_wait_ms` only after all other safe transitions for the tick have been considered. While waiting, continue Manager work that does not mutate the inspected integration head: refine tasks, prepare validation notes, harvest finished Workers, create fix tasks from already triaged findings, or dispatch non-overlapping queued Workers up to `max_workers`.

Before manually intervening in a running loop, run `hloop conductor --no-fail`. If it reports a missing pane, blocked prompt, idle agent without artifact, ready artifact, branch mismatch, unsafe sandbox, non-hloop prompt path, unharvested artifact, untrusted Worker head, or manual integration trace, resolve that explicit condition through the relevant `hloop ... watch`, `message`, `harvest`, `triage`, branch command, Worker rerun, or recorded blocker instead of replacing the loop with ad hoc Herdr/Codex commands.

## References

Load only the reference needed for the current operation:

- `references/state-machine.md`: phases, tick behavior, and stop conditions
- `references/artifact-contract.md`: required `.ai/loop` file shapes and frontmatter
- `references/branch-policy.md`: branch/worktree topology, default integration flow, and custom branch strategy rules
- `references/profile-examples.md`: `/goal` prompt examples for branch strategy, review lanes, Worker QA, and Manager final QA selection
- `references/manager-loop.md`: Manager checklist and triage rules
- `references/worker-contract.md`: HLoop Worker Protocol and optional compatibility mode
- `references/gap-contract.md`: Gap Auditor prompt contract and plan/spec alignment rules
- `references/reviewer-contract.md`: HLoop Native Review Protocol and optional compatibility mode
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
- an unresolved plan/spec choice must be decided from outside the original plan; record it in `DECISIONS.md` and `USER_ACTION_REQUIRED.md`.
- a Worker changed files outside `write_allow` or inside `write_deny`.
- a Worker reports `partial`, `blocked`, `failed`, or `merge_ready: false`.
- merge conflict requires judgment.
- integration validation fails and rollback is not obvious.
- Gap Auditor reports a missing, partial, or needs-decision gap that affects the mission done criteria.
- Reviewer reports P0/P1 that needs a user decision.
- no progressable transition exists and no Worker, Gap Auditor, or Reviewer is merely still running.

When stopped, update `STATE.json`, `JOURNAL.md`, and `USER_ACTION_REQUIRED.md` with the concrete blocker before asking the user.
