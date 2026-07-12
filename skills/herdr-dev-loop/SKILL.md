---
name: herdr-dev-loop
description: Orchestrate a bounded Herdr-managed multi-agent coding loop with pump/triage scheduling and interactive Worker, Gap Auditor, Reviewer, and opt-in Advisor panes. Use inside Herdr when a Manager agent needs to run Codex or Claude subordinate agents across git worktrees, persist the goal in explicitly namespaced .ai/herdr-dev-loop artifacts, drain task/fix-task queues with hloop pump, use HLoop-native Worker and Reviewer protocols by default, compare original plan/spec sources against implementation, generate Manager-approved fix-task drafts from review/gap artifacts with hloop triage, optionally consult cross-model Advisors for non-user-blocking specification or fix strategy decisions, adapt branch/review/QA/agent backend strategy through the namespaced PROFILE.md, merge into an integration or project-specific branch flow, monitor agent panes, harvest artifacts from detached worktrees, and stop on blocking specification decisions or unsafe state.
---

# Herdr Dev Loop

Use this skill as the Manager Agent for a file-backed development loop. The Manager is the agent currently using this skill. The loop coordinates isolated Worker agents, independent Gap Auditor agents, independent Reviewer agents, and explicit opt-in Advisor agents through git branches, worktrees, Herdr panes, and `.ai/herdr-dev-loop/loops/<namespace>` artifacts. Subordinate role agents default to Codex, but role-specific Codex/Claude provider and model choices can be recorded in `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`.

This skill is intentionally conservative. Prefer one bounded tick at a time until the loop has proven stable for the current repository.

## Required Preflight

Before starting or continuing a loop:

1. Choose an explicit namespace and use it for every command: `HLOOP="python3 <this-skill>/scripts/hloop --namespace <namespace>"`. Run `$HLOOP namespaces` when resuming. Never read or migrate legacy `.ai/loop`; it is intentionally ignored.
2. Before other investigation or mutation, run `$HLOOP version` and make the first user-visible progress message state `herdr-dev-loop <runtime-version> / namespace <namespace> を使用します` together with the reported `loop_skill_version` and `run_id` when present. This announcement is mandatory even if the user did not explicitly ask for version output.
3. Verify `HERDR_ENV=1`.
4. Run `$HLOOP doctor`.
5. After installing or updating this skill, run `$HLOOP selftest`.
6. Confirm `herdr`, `git`, and the configured subordinate agent CLIs are available. Codex is the default fallback provider; Claude is optional unless selected for a role. `$codex-impl` and `$codex-review-multi-v2` are optional compatibility protocols, not default dependencies.
7. Read the current `.ai/herdr-dev-loop/loops/<namespace>/MISSION.md`, `.ai/herdr-dev-loop/loops/<namespace>/PLAN.md`, `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`, `.ai/herdr-dev-loop/loops/<namespace>/STATE.json`, and `.ai/herdr-dev-loop/loops/<namespace>/DECISIONS.md` if they exist.
8. Run `$HLOOP dashboard` or `$HLOOP conductor --no-fail` when resuming an existing loop so pane/worktree/artifact drift is visible before mutating state.
9. Continue from disk state, not from thread memory.

If `HERDR_ENV=1` is absent, stop and tell the user this skill requires Herdr.

Use the absolute helper path when `hloop` is not installed on `PATH`. A missing bare `hloop` command is not a reason to recreate Worker, Reviewer, Gap Auditor, merge, validation, or triage behavior by hand.

## Quick Start

Use the helper script instead of hand-typing pane prompts:

```bash
HLOOP="python3 <this-skill>/scripts/hloop --namespace <namespace>"
$HLOOP version
$HLOOP selftest
$HLOOP doctor
$HLOOP init --goal-id <goal-id> --goal "<goal>" --base <main-or-master> --create-branch --persistence local-only --worktree-root ../wt/<goal-id> --merge-mode squash --branch-strategy integration --worker-protocol native --review-protocol native --worker-agent-provider codex --worker-agent-model auto --reviewer-agent-provider codex --reviewer-agent-model auto --gap-agent-provider codex --gap-agent-model auto --worker-qa-profile repo-default --manager-qa-profile none --worker-runner tui --gap-runner tui --reviewer-runner tui --max-workers 3 --max-reviewers 1 --max-gap-auditors 1 --review-after-merges 1 --gap-after-merges 3 --session-cleanup archive --gap-wait-ms 600000 --review-wait-ms 600000
$HLOOP batch start "Initial implementation batch"
$HLOOP task new "Implement bounded slice" --write-allow 'src/foo/**' --write-allow 'tests/foo/**'
$HLOOP dashboard
$HLOOP pump --max-transitions 20 --max-workers 3
```

Use `tick --once` for one material transition when inspecting a new repository. Use `pump` after the loop is stable; it repeatedly runs safe tick transitions until it reaches triage, blocked, done, or the transition limit. Add `--stop-on-waiting` when Manager wants to pause as soon as all currently safe transitions are exhausted.

The script is deliberately explicit. Use `--dry-run` on `worker start`, `gap start`, `reviewer start`, `tick`, `pump`, and `triage` when checking the commands before spawning panes or creating fix-task drafts.

Mutating `hloop` commands enforce their own preflight. `pump`, `tick`, `worker start`, `worker harvest`, `merge`, `validate`, `triage`, `gap start`, `gap harvest`, `reviewer start`, and `reviewer harvest` check the relevant Herdr environment, current branch, required commands, and non-loop dirty files before changing state.

Treat every mutating `hloop` command as a serialized state transaction. The helper takes a repo-local Git lock (`git rev-parse --git-path hloop.lock`), but Manager should still avoid launching multiple mutating `hloop` commands in parallel. Parallelize reads, not loop-state writes.

Worker, Gap Auditor, Reviewer, and Advisor agents default to interactive TUI panes so the Manager can inspect progress, add requirements, or interrupt them in Herdr. The default provider is Codex; use `--worker-agent-provider`, `--reviewer-agent-provider`, `--gap-agent-provider`, `--advisor-agent-provider`, and matching `--*-agent-model` flags when a role should use Claude or a specific model. Gap Auditors, Reviewers, and Advisors run in detached worktrees with write access only for their Markdown artifacts; the prompt and harvest guards still forbid code edits. Override with `--runner exec` only when non-interactive work is intentionally preferred.

When sending additional instructions to a running TUI, use `hloop worker message <task-id> --file <prompt.md>`, `hloop gap message <gap-id> --file <prompt.md>`, `hloop reviewer message <review-id> --file <prompt.md>`, or `hloop advisor message <advice-id> --participant-id P1 --file <prompt.md>`. Do not send prompts directly with `herdr pane run` unless you have manually verified the pane is a ready role-agent TUI. The helper blocks common mistakes: shell panes, pending trust prompts, and busy agent sessions. It sends via `send-text`, waits for the input to appear, pauses before Enter, and verifies that the agent started working or answered; if the first Enter races the TUI, it retries.

Inspect running agents with `hloop worker watch <task-id>`, `hloop gap watch <gap-id>`, or `hloop reviewer watch <review-id>`. Use direct `herdr pane read` only for debugging the helper itself.

When Manager is only waiting for an artifact, prefer `hloop wait <task-id-or-gate-id> --harvest` or `hloop wait next --harvest` over hand-written `sleep`, `watch`, and `test -f` polling loops. Use `--timeout-ms`, `--poll-ms`, and `--quiet` to tune long waits.

Use `hloop dashboard` for the Manager's one-screen view of phase, queues, running agents, panes, artifacts, and next actions. Use `hloop conductor` when investigating stuck sessions; it reports P0/P1 attention items such as missing panes, blocked Codex prompts, reported review/gap artifacts needing triage, non-loop dirty files, branch mismatches, unsafe sandbox values, non-hloop prompt paths, unharvested artifacts, untrusted Worker head markers, and manual integration traces. Add `--no-fail` when the command is informational and should not return non-zero.

Use `hloop batch start "<batch title>"` to group several related tasks or fix tasks into a local-history unit larger than one Worker task and smaller than the whole mission. When a current batch exists, `hloop task new` and triage-created fix tasks attach to it by default. Use `hloop batch close --summary "..."` when the batch is finished.

Use `hloop task update <task-id>` for write scope, acceptance, validation minimum, protocol, QA, or agent-backend changes. Checkpoint the changed contract before starting a Worker only in `branch-history`; `local-only` snapshots it directly. If that Worker is already running, also send the new requirement through `hloop worker message`.

Workers should commit product changes first, then run `hloop worker finalize <task-id> --validation-command <command> --validation-result passed --validation-summary <summary>`. The helper derives and commits the result metadata. `wait`, `tick`, and `pump` do not harvest a Worker result until the exact artifact is committed at Worker HEAD.

When `init --force` replaces a loop, the previous tree is archived below `.ai/herdr-dev-loop/archive/<namespace>/`. Every new loop pins its namespace, runtime `skill_version`, persistence mode, and `run_id`. Every started role records the runtime version, prints it in its first progress message, and preserves it in its artifact; Reviewer, Gap Auditor, and Advisor artifacts must also match the run and audited `head_sha`.

`local-only` is the default persistence mode. Manager state is copied into role worktrees without requiring public branch history, and squash merge unstages namespaced loop artifacts before the product commit. Use `branch-history` only when the repository intentionally versions loop artifacts.

Recover artifact-less or failed roles with `hloop agent abort <id> --reason ...` and `hloop agent requeue <id> --reason ...`. Cleanup refuses to discard product changes unless Manager explicitly adds `--force-cleanup`.

Repository-specific worktree setup commands may be passed repeatedly with `init --worktree-setup-command`. Every outcome is appended to `.ai/herdr-dev-loop/experience/worktree-setup.json`. Curate reusable defaults with `hloop experience recommend --command ...`; a later `init` reuses recommended commands when no explicit setup command is supplied.

With `branch-history` persistence, use `hloop checkpoint --batch BNNN --rollup --message "ai-loop(BNNN): ..."` to commit namespaced loop state without staging product files. `local-only` does not require checkpoints for role visibility. Avoid hand-written Git staging unless debugging the helper itself.

After a Worker, Gap Auditor, Reviewer, or Advisor artifact is harvested, close its Herdr pane and clean up the captured session when the provider supports it unless the Manager intentionally passes `--keep-pane` or `--session-cleanup none` for inspection. Treat `.ai/herdr-dev-loop/loops/<namespace>` artifacts as the durable record; do not leave completed agent panes open as informal state.

## Source Of Truth

The durable state lives under `.ai/herdr-dev-loop/loops/<namespace>`:

- `MISSION.md`: user goal, constraints, non-goals, base branch, integration branch, done criteria
- `PLAN.md`: task graph, product-specific branch handoff, parallelization rules, validation plan, Worker QA plan, Manager final QA plan, review plan
- `PROFILE.md`: Manager-owned branch strategy, Worker protocol, Reviewer protocol, review lanes, Worker QA profile, and Manager final QA profile
- `STATE.json`: current phase, branches, task/review status, pane ids, worktrees
- `DECISIONS.md`: pending, accepted, and rejected specification decisions that cannot be answered from the original plan/spec alone
- `USER_ACTION_REQUIRED.md`: blocking questions for the user
- `tasks/*.md`: Worker task contracts
- `batches/*.md`: Manager-owned task batches for readable loop-state checkpoint history
- `results/<task-id>/result.md`: Worker completion artifacts
- `gaps/*.md`: Gap Auditor plan/spec alignment artifacts
- `reviews/*.md`: Reviewer artifacts
- `advice/*.md`: explicit Advisor consultation requests and participant artifacts
- `triage/*.fix-task-draft.md`: Manager-reviewed fix-task drafts generated from review/gap artifacts
- `qa/FINAL.md`: Manager-owned final QA evidence when `manager_qa_profile` is not `none`
- `reports/FINAL.md`: final report

If thread memory and `.ai/herdr-dev-loop/loops/<namespace>` disagree, trust `.ai/herdr-dev-loop/loops/<namespace>` and record the discrepancy in `JOURNAL.md`.

## Roles

Manager owns:

- integration branch or product-specific branch handoff defined in `PROFILE.md` / `PLAN.md`
- `.ai/herdr-dev-loop/loops/<namespace>/MISSION.md`, `PLAN.md`, `PROFILE.md`, `STATE.json`, `DECISIONS.md`, `JOURNAL.md`
- task creation, merge decisions, validation, review triage, and user escalation

Worker owns:

- only its branch and worktree
- only paths allowed by the task `write_allow`
- `.ai/herdr-dev-loop/loops/<namespace>/results/<task-id>/result.md`

Reviewer owns:

- only `.ai/herdr-dev-loop/loops/<namespace>/reviews/<review-id>.md`
- no code edits

Gap Auditor owns:

- only `.ai/herdr-dev-loop/loops/<namespace>/gaps/<gap-id>.md`
- no code edits
- no generic code-review findings unless they directly prove plan/spec drift

Advisor owns:

- only `.ai/herdr-dev-loop/loops/<namespace>/advice/<advice-id>-<participant-id>.md`
- no code edits
- no gate closure, task creation, merge, or final decision authority
- recommendations for fix strategy, specification shape, accepted-risk rationale, or Manager decision records

Advisor is opt-in only. `tick` and `pump` must not start Advisors automatically. Use `hloop advisor request`, `hloop advisor start`, `hloop advisor harvest`, and `hloop advisor close` when Manager explicitly wants a consultation. For cross-model consultation, create a dialogue request with multiple participants such as `--participant codex:auto --participant claude:opus`, harvest each participant artifact, and let Manager record the accepted decision or task.

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

Use `hloop triage review <review-id>` or `hloop triage gap <gap-id>` to convert machine-readable `Fix Task Candidates` sections into `.ai/herdr-dev-loop/loops/<namespace>/triage/*.fix-task-draft.md`. Add `--create-tasks` only after Manager approval; this creates queued fix Workers from the candidates.

Default cadence is intentionally busy: keep up to three Workers running, run Reviewer after each validated integration advance, and run Gap Auditor every three validated merges or before final completion. Gap Auditor and Reviewer may run while Workers continue on isolated branches, but Manager must not merge Worker branches while a Gap Auditor or Reviewer is actively reading the integration branch.

Default protocols are native to this skill:

- Workers follow the HLoop Worker Protocol: inspect context, implement inside write scope, self-review, run repo-appropriate validation/QA, write the result artifact, and commit.
- Reviewers follow the HLoop Native Review Protocol: review task/result artifacts, write-scope and merge safety, product correctness, risk, and validation/QA evidence. Use `$codex-review-multi-v2` only when `review_protocol: codex-review-multi-v2` is intentionally selected.
- `$codex-impl` is only a Worker compatibility mode. Use it only when `worker_protocol: codex-impl` is intentionally selected.

Agent backend selection is separate from protocol selection:

- `worker_agent_provider`, `reviewer_agent_provider`, `gap_agent_provider`, and `advisor_agent_provider` choose the CLI provider (`codex` or `claude`) for each role.
- `*_agent_model` selects the provider model; `auto` lets the CLI choose its default.
- Task frontmatter may override the Worker provider/model for a specific task.
- Codex remains the default fallback provider for all roles.

Use `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md` to adapt the loop to product reality. `branch_strategy: integration` enables the built-in integration-branch flow. `pr-per-task` and `custom` require Manager to record the exact handoff in `PLAN.md` and avoid assuming the default merge/publish steps. `worker_qa_profile` is the QA each Worker must record for its task; `manager_qa_profile` is the separate final QA gate Manager records in `qa/FINAL.md` after integration/review/gap gates.

Assume Gap Auditor and Reviewer runs can take several minutes. Use `hloop gap watch <gap-id>` or `hloop reviewer watch <review-id>` to inspect the TUI pane. If an auditor or reviewer is still running, wait up to `gap_wait_ms` or `review_wait_ms` only after all other safe transitions for the tick have been considered. While waiting, continue Manager work that does not mutate the inspected integration head: refine tasks, prepare validation notes, harvest finished Workers, create fix tasks from already triaged findings, or dispatch non-overlapping queued Workers up to `max_workers`.

Before manually intervening in a running loop, run `hloop conductor --no-fail`. If it reports a missing pane, blocked prompt, idle agent without artifact, ready artifact, branch mismatch, unsafe sandbox, non-hloop prompt path, unharvested artifact, untrusted Worker head, or manual integration trace, resolve that explicit condition through the relevant `hloop ... watch`, `message`, `harvest`, `triage`, branch command, Worker rerun, or recorded blocker instead of replacing the loop with ad hoc Herdr/agent commands.

## References

Load only the reference needed for the current operation:

- `references/state-machine.md`: phases, tick behavior, and stop conditions
- `references/artifact-contract.md`: required `.ai/herdr-dev-loop/loops/<namespace>` file shapes and frontmatter
- `references/branch-policy.md`: branch/worktree topology, default integration flow, and custom branch strategy rules
- `references/profile-examples.md`: `/goal` prompt examples for branch strategy, review lanes, Worker QA, and Manager final QA selection
- `references/manager-loop.md`: Manager checklist and triage rules
- `references/worker-contract.md`: HLoop Worker Protocol and optional compatibility mode
- `references/gap-contract.md`: Gap Auditor prompt contract and plan/spec alignment rules
- `references/reviewer-contract.md`: HLoop Native Review Protocol and optional compatibility mode
- `references/advisor-contract.md`: explicit opt-in Advisor consultation and cross-model dialogue rules
- `references/decision-policy.md`: blocking decision criteria
- `references/validation-policy.md`: validation levels and command selection
- `references/public-repo-safety.md`: files and data that must not be committed
- `references/prompts.md`: prompt templates used by `hloop`
- `references/cli-notes.md`: local Herdr and Codex CLI assumptions to re-check with `hloop doctor`

## Hard Stops

Stop immediately when:

- `HERDR_ENV=1` is not set.
- `.ai/herdr-dev-loop/loops/<namespace>/STATE.json` is missing for a non-init operation.
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
