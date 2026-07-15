---
name: herdr-dev-loop
description: Orchestrate a bounded Herdr-managed multi-agent coding loop with hierarchical config, requirement and decision tracking, structured agent reports, event-driven Manager wake, synthetic release evidence, and single, swarm, dual, or dual-swarm review. Use inside Herdr when a Manager needs Codex or Claude role agents in isolated git worktrees with namespaced durable state, verified artifacts, scoped user-decision blocking, explicit QA and final gates, and safe migration or recovery.
---

# Herdr Dev Loop

Use this skill as the Manager Agent for a file-backed development loop. The Manager is the agent currently using this skill. The loop coordinates isolated Worker agents, independent Gap Auditor agents, Reviewer Coordinators, and explicit opt-in Advisor agents through git branches, worktrees, Herdr panes, structured reports, and `.ai/herdr-dev-loop/loops/<namespace>` artifacts. Subordinate roles default to Codex, but configuration, `PROFILE.md`, task contracts, and explicit start overrides can select Codex or Claude without changing the role protocol.

This skill is intentionally conservative. Prefer one bounded tick at a time until the loop has proven stable for the current repository.

## Required Preflight

Before starting or continuing a loop:

1. Choose an explicit namespace and use it for every command: `HLOOP="python3 <this-skill>/scripts/hloop --namespace <namespace>"`. Run `$HLOOP namespaces` when resuming. Never read or migrate legacy `.ai/loop`; it is intentionally ignored.
2. Before other investigation or mutation, run `$HLOOP version` and make the first user-visible progress message state `herdr-dev-loop <runtime-version> / namespace <namespace> を使用します` together with the reported `loop_skill_version` and `run_id` when present. This announcement is mandatory even if the user did not explicitly ask for version output.
3. Verify `HERDR_ENV=1`.
4. Run `$HLOOP doctor`.
5. After installing or updating this skill, run `$HLOOP selftest`.
6. Confirm `herdr`, `git`, and the configured subordinate agent CLIs are available. Codex is the default fallback provider; Claude is optional unless selected for a role. `$codex-impl` and `$codex-review-multi-v2` are optional compatibility protocols, not default dependencies.
7. Require Python 3.11 or later. Run `$HLOOP config validate --json` when a config file is selected, then use `$HLOOP config explain --repo <repo> --json` before initializing a new loop.
8. Read the current `.ai/herdr-dev-loop/loops/<namespace>/MISSION.md`, `.ai/herdr-dev-loop/loops/<namespace>/PLAN.md`, `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`, `.ai/herdr-dev-loop/loops/<namespace>/STATE.json`, and `.ai/herdr-dev-loop/loops/<namespace>/DECISIONS.md` if they exist.
9. Run `$HLOOP dashboard` and `$HLOOP conductor --no-fail` when resuming. Drain `$HLOOP manager next` before polling role panes.
10. Continue from namespaced disk state and the durable inbox, not from thread memory or free-form pane output.

If `HERDR_ENV=1` is absent, stop and tell the user this skill requires Herdr.

Use the absolute helper path when `hloop` is not installed on `PATH`. A missing bare `hloop` command is not a reason to recreate Worker, Reviewer, Gap Auditor, merge, validation, or triage behavior by hand.

## Quick Start

Use the helper script instead of hand-typing pane prompts:

```bash
HLOOP="python3 <this-skill>/scripts/hloop --namespace <namespace>"
$HLOOP version
$HLOOP config validate --json
$HLOOP config explain --repo <repo> --json
$HLOOP selftest
$HLOOP doctor
$HLOOP init --goal-id <goal-id> --goal "<goal>" --base <main-or-master> --create-branch --persistence local-only --worktree-root ../wt/<goal-id> --merge-mode squash --branch-strategy integration --worker-protocol native --review-protocol native --worker-agent-provider codex --worker-agent-model auto --reviewer-agent-provider codex --reviewer-agent-model auto --gap-agent-provider codex --gap-agent-model auto --worker-qa-profile repo-default --manager-qa-profile none --worker-runner tui --gap-runner tui --reviewer-runner tui --max-workers 3 --max-reviewers 1 --max-gap-auditors 1 --review-after-merges 1 --gap-after-merges 3 --validation-command '<repo test command>' --session-cleanup archive --gap-wait-ms 600000 --review-wait-ms 600000
$HLOOP batch start "Initial implementation batch"
$HLOOP input record --source manager-chat --text '<user requirement>'
$HLOOP requirement new --source-input U0001 --acceptance '<observable result>' --priority P1
$HLOOP task new "Implement bounded slice" --write-allow 'src/foo/**' --write-allow 'tests/foo/**'
$HLOOP dashboard
$HLOOP pump --max-transitions 20 --max-workers 3
$HLOOP final-gates arm
$HLOOP finish
```

Use `tick --once` for one material transition when inspecting a new repository. Use `pump` after the loop is stable; it repeatedly runs safe tick transitions until it reaches triage, blocked, done, or the transition limit. Add `--stop-on-waiting` when Manager wants to pause as soon as all currently safe transitions are exhausted.

The script is deliberately explicit. Use `--dry-run` on `worker start`, `gap start`, `reviewer start`, `tick`, `pump`, and `triage` when checking the commands before spawning panes or creating fix-task drafts.

Mutating `hloop` commands enforce their own preflight. `pump`, `tick`, `worker start`, `worker harvest`, `merge`, `validate`, `triage`, `gap start`, `gap harvest`, `reviewer start`, and `reviewer harvest` check the relevant Herdr environment, current branch, required commands, and non-loop dirty files before changing state.

Treat every mutating `hloop` command as a serialized state transaction. The helper takes a repository-and-namespace lock at `/tmp/herdr-dev-loop-<uid>/locks/<sha256>.lock`; the digest identifies the canonical Git common directory and namespace, independently of `HLOOP_RUNTIME_DIR`, `XDG_RUNTIME_DIR`, and `TMPDIR`. The UID directory is mode `0700`, the lock is mode `0600`, and the path stays outside Git metadata. Manager should still avoid launching multiple mutating `hloop` commands in parallel. Parallelize reads, not loop-state writes.

Worker, Gap Auditor, Reviewer, and Advisor agents default to interactive TUI panes so the Manager can inspect progress, add requirements, or interrupt them in Herdr. The default provider is Codex; use `--worker-agent-provider`, `--reviewer-agent-provider`, `--gap-agent-provider`, `--advisor-agent-provider`, and matching `--*-agent-model` flags when a role should use Claude or a specific model. Gap Auditors, Reviewers, and Advisors run in detached worktrees with write access only for their Markdown artifacts; the prompt and harvest guards still forbid code edits. Override with `--runner exec` only when non-interactive work is intentionally preferred.

When sending additional instructions to a running TUI, use `hloop worker message <task-id> --file <prompt.md>`, `hloop gap message <gap-id> --file <prompt.md>`, `hloop reviewer message <review-id> --file <prompt.md>`, or `hloop advisor message <advice-id> --participant-id P1 --file <prompt.md>`. Do not send prompts directly with `herdr pane run` unless you have manually verified the pane is a ready role-agent TUI. The helper blocks common mistakes: shell panes, pending trust prompts, and busy agent sessions. It sends via `send-text`, waits for the input to appear, pauses before Enter, and verifies that the agent started working or answered; if the first Enter races the TUI, it retries.

Every long-running role uses `hloop agent report` for semantic `ack`, `milestone`, `attention`, and `completion` events. ACK binds the role's understood goal, scope, acceptance, and approach before material edits. A completion report is communication, not proof; harvest still verifies the artifact, SHA, write scope, and validation. See `references/report-protocol.md`.

Inspect running agents with `hloop worker watch <task-id>`, `hloop gap watch <gap-id>`, or `hloop reviewer watch <review-id>`. Use direct `herdr pane read` only for debugging the helper itself.

For ordinary progress, use `hloop inbox list` and `hloop manager next`. When the inbox has no actionable event, register a bounded wake lease with `hloop manager sleep`. The broker provides durable at-least-once delivery, so acknowledge a handled event with `hloop inbox ack <event-id>` and deduplicate by event ID and lease generation.

When Manager is only waiting for an artifact, prefer `hloop wait <task-id-or-gate-id> --harvest` or `hloop wait next --harvest` over hand-written `sleep`, `watch`, and `test -f` polling loops. Use `--timeout-ms`, `--poll-ms`, and `--quiet` to tune long waits.

Use `hloop dashboard` for the Manager's one-screen view of phase, queues, running agents, panes, artifacts, and next actions. Use `hloop conductor` when investigating stuck sessions; it reports P0/P1 attention items such as missing panes, blocked Codex prompts, reported review/gap artifacts needing triage, non-loop dirty files, branch mismatches, unsafe sandbox values, non-hloop prompt paths, unharvested artifacts, untrusted Worker head markers, and manual integration traces. Add `--no-fail` when the command is informational and should not return non-zero.

Use `hloop batch start "<batch title>"` to group several related tasks or fix tasks into a local-history unit larger than one Worker task and smaller than the whole mission. When a current batch exists, `hloop task new` and triage-created fix tasks attach to it by default. Use `hloop batch close --summary "..."` when the batch is finished.

Use `hloop task update <task-id>` for write scope, acceptance, validation minimum, protocol, QA, or agent-backend changes. Checkpoint the changed contract before starting a Worker only in `branch-history`; `local-only` snapshots it directly. Updating a running Worker rebinds its authenticated report identity to the new task digest and re-arms the semantic ACK barrier. Also send the new requirement through `hloop worker message`; the Worker must submit a corrected ACK for that digest and wait for Manager approval before finalize, harvest, or merge.

Workers should commit product changes first, then run `hloop worker finalize <task-id> --validation-command <command> --validation-result passed --validation-summary <summary>`. Validation results are the explicit `passed`, `failed`, or `blocked` enum; every result must be `passed` for status `done`. The helper derives and commits the result metadata. `wait`, `tick`, and `pump` do not harvest a Worker result until the exact artifact is committed at Worker HEAD. Harvest copies the verified result into the canonical Manager loop path and retains the role-worktree path only as provenance.

When `init --force` replaces a loop, the previous tree is archived below `.ai/herdr-dev-loop/archive/<namespace>/`. Every new loop pins its namespace, runtime `skill_version`, persistence mode, and `run_id`. Every started role records the runtime version, prints it in its first progress message, and preserves it in its artifact; Reviewer, Gap Auditor, and Advisor artifacts must also match the run and audited `head_sha`.

`local-only` is the default persistence mode. Manager state is copied into role worktrees without requiring public branch history, and squash merge unstages namespaced loop artifacts before the product commit. Use `branch-history` only when the repository intentionally versions loop artifacts.

Recover artifact-less or failed roles with `hloop agent abort <id> --reason ...` and `hloop agent requeue <id> --reason ...`. Each start has an attempt id and immutable Worker base; requeue archives the prior attempt and any clean unmerged branch before creating a new attempt. Cleanup refuses to discard product changes unless Manager explicitly adds `--force-cleanup`.

For a loop created by an older runtime, run `hloop migrate --dry-run` and then `hloop migrate --apply` before continuing. `pump` may reach `ready_to_finish`, but it never marks the loop done; run `hloop finish` to recheck validation, review, gap, Manager QA, cleanup, merge, agent, and checkout gates against the current integration SHA.

Repository-specific worktree setup commands may be passed repeatedly with role-specific `init --worker-setup-command`, `--reviewer-setup-command`, `--gap-setup-command`, and `--advisor-setup-command`. The legacy `--worktree-setup-command` is Worker-only. Every outcome is appended to `.ai/herdr-dev-loop/experience/worktree-setup.json`. Curate reusable Worker defaults with `hloop experience recommend --command ...`; a later `init` reuses recommended commands when no explicit Worker setup command is supplied.

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
- `inputs/`: redacted local-only user inputs; never checkpointed or committed
- `STATE.json.requirements`: accepted requirements and evidence-gated progress
- `STATE.json.decisions`: machine-readable scoped decision records backing the readable `DECISIONS.md` ledger
- `inbox/` and broker storage: local-only reports, wake records, and recovery spool
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
2. Drain broker recovery and the Manager inbox. Process `attention` and `completion` before polling panes.
3. Harvest completed Workers, Gap Auditors, or Reviewers.
4. Validate result artifacts, SHAs, write scopes, and validation evidence before updating requirement progress.
5. Integrate at most one Worker branch according to `PROFILE.md`; built-in automation defaults to squash merge into the integration branch.
6. Run integration validation.
7. Triage harvested Gap Auditor or Reviewer artifacts before starting more work from stale assumptions.
8. Start a Gap Auditor when the gap gate is open; default frequency is lower than review (`gap_after_merges: 3`).
9. Start a Reviewer when the review gate is open; default frequency is high (`review_after_merges: 1`).
10. Dispatch queued implementation or fix Workers up to `max_workers` when `write_allow` patterns do not overlap and no scoped decision blocks them.
11. Record requirement-oriented progress before user-visible updates and before terminal phase changes.
12. Triage gap/review findings into fix tasks, decisions, accepted risk, stale-spec updates, or false positives.
13. Arm final gates only after the batch, triage, and fix-task-draft set are stable. Stop if done, externally blocked, or unsafe.

Do not run an unbounded loop. Prefer `tick --once` while inspecting a new repository; use `pump --max-transitions <n>` only after the workflow is stable.

Use `hloop triage review <review-id>` or `hloop triage gap <gap-id>` to convert machine-readable `Fix Task Candidates` sections into `.ai/herdr-dev-loop/loops/<namespace>/triage/*.fix-task-draft.md`. Add `--create-tasks` only after Manager approval; this creates queued fix Workers from the candidates.

Default cadence is intentionally busy: keep up to three Workers running, run Reviewer after each validated integration advance, and run Gap Auditor every three validated merges or before final completion. Gap Auditor and Reviewer may run while Workers continue on isolated branches, but Manager must not merge Worker branches while a Gap Auditor or Reviewer is actively reading the integration branch.

Default protocols are native to this skill:

- Workers follow the HLoop Worker Protocol: inspect context, implement inside write scope, self-review, run repo-appropriate validation/QA, write the result artifact, and commit.
- Reviewers follow the HLoop Native Review Protocol: review task/result artifacts, write-scope and merge safety, product correctness, risk, and validation/QA evidence. Use `$codex-review-multi-v2` only when `review_protocol: codex-review-multi-v2` is intentionally selected.
- `$codex-impl` is only a Worker compatibility mode. Use it only when `worker_protocol: codex-impl` is intentionally selected.

Review topology is independent of Reviewer protocol. `single` is the low-cost path, `swarm` uses four to eight discovery lanes on one provider, `dual` uses one lane per Codex and Claude, and `dual-swarm` uses four to eight lanes per provider. All lanes target one SHA. Critical and specification-decision findings require independent verification; budget exhaustion leaves `insufficient_evidence` rather than silently confirming or dropping a candidate.

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
- `references/configuration.md`: config discovery, scope matching, precedence, snapshots, and review-mode settings
- `references/report-protocol.md`: semantic agent reports, durable inbox, wake leases, broker recovery, and privacy
- `references/requirements-decisions-outcomes.md`: redacted input capture, requirement evidence, scoped decisions, and terminal output
- `references/review-swarm.md`: single/swarm/dual/dual-swarm topology, normalization, budgets, and manifest gates
- `references/migration-install.md`: format 3 migration, Codex/Claude install parity, discovery, and rollback
- `references/public-repo-safety.md`: files and data that must not be committed
- `references/prompts.md`: prompt templates used by `hloop`
- `references/cli-notes.md`: local Herdr and Codex CLI assumptions to re-check with `hloop doctor`

## Hard Stops

Stop immediately when:

- `HERDR_ENV=1` is not set.
- `.ai/herdr-dev-loop/loops/<namespace>/STATE.json` is missing for a non-init operation.
- every remaining safe task is dependency-blocked by an unresolved `blocking-user` decision. A scoped decision does not stop unrelated work.
- an unresolved plan/spec choice must be decided from outside the original plan; record it in `DECISIONS.md` and `USER_ACTION_REQUIRED.md`.
- a Worker changed files outside `write_allow` or inside `write_deny`.
- a Worker reports `partial`, `blocked`, `failed`, or `merge_ready: false`.
- merge conflict requires judgment.
- integration validation fails and rollback is not obvious.
- Gap Auditor reports a missing, partial, or needs-decision gap that affects the mission done criteria.
- Reviewer reports P0/P1 that needs a user decision.
- no progressable transition exists and no Worker, Gap Auditor, or Reviewer is merely still running.

When stopped, update `STATE.json`, `JOURNAL.md`, and `USER_ACTION_REQUIRED.md` with the concrete blocker before asking the user.
