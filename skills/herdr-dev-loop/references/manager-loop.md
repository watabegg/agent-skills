# Manager Loop

Manager owns integration and final judgment.

## Start Of Tick Checklist

1. Select one explicit `--namespace`, run `hloop namespaces`, and confirm legacy `.ai/loop` is ignored.
2. Run `hloop version` before other work and make the first progress message identify the runtime version, namespace, loop version, and run ID.
3. Run `hloop doctor`.
4. Validate the selected `config.toml` and inspect `config explain` before initializing a new namespace.
5. Read `.ai/herdr-dev-loop/loops/<namespace>/MISSION.md`.
6. Read `.ai/herdr-dev-loop/loops/<namespace>/PLAN.md`.
7. Read `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`.
8. Read `.ai/herdr-dev-loop/loops/<namespace>/STATE.json`.
9. Read `.ai/herdr-dev-loop/loops/<namespace>/DECISIONS.md`.
10. Replay broker recovery if needed and run `hloop manager next`.
11. Run `hloop dashboard` to inspect phase, queues, pane ids, worktrees, artifacts, and next actions.
12. Run `hloop conductor --no-fail` when resuming a long-running workspace or when the next action is unclear.
13. Check current branch and branch strategy against `STATE.json` and `PROFILE.md`.
14. Check `git status --short`.

`hloop` enforces the same preflight for mutating commands. Treat a preflight failure as an environmental block, not as a reason to continue by hand.

Use an explicit helper command such as `HLOOP="python3 /home/.../skills/herdr-dev-loop/scripts/hloop"` when bare `hloop` is not on `PATH`. A PATH miss for the convenience name is not a loop blocker and must not trigger manual state editing, manual worktree orchestration, or direct `codex exec` launches.

Set `--worktree-root ../wt/<goal>` at init when the repository requires a particular worktree location. Pump and every role-specific start command inherit it. Use `hloop task update` for write scope, acceptance, validation, protocol, QA, or agent changes; do not patch both the task file and `STATE.json` manually.

With `branch-history`, checkpoint newly created or updated task contracts before starting a role. With `local-only`, hloop copies the selected namespace into each role worktree, so task and gate inputs do not need branch-history commits.

Mutating `hloop` commands are serialized with `/tmp/herdr-dev-loop-<uid>/locks/<sha256>.lock`. The digest identifies the canonical Git common directory and namespace, while the fixed UID-private runtime root remains the same across `HLOOP_RUNTIME_DIR`, `XDG_RUNTIME_DIR`, and `TMPDIR` differences and stays outside Git metadata. Manager should still treat commands as transactions. Do not run `hloop task new`, `tick`, `pump`, `worker harvest`, `worker seal`, `merge`, `validate`, `triage`, `gap`, or `reviewer` mutating commands in parallel. Parallelize reads and inspections only.

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

For new 0.5.3 loops, keep implementation work bounded by task batches and current planning evidence:

Before the first Worker in a planning revision, run `hloop planning check --json`. It validates the Repository Impact Map, Task Risk Graph, coverage ledger, and Plan Gap artifact against one identity made from plan, requirements, release scope, and source digests. A stale or incomplete artifact blocks dispatch; do not replace it with a Manager assertion.

- dispatch up to `max_workers: 3` Workers when write scopes do not overlap
- close the current batch before opening ordinary review work (`review_policy.cadence: batch`)
- create one immutable review epoch for the target SHA, reserve capacity before each required Reviewer/Gap process, record every terminal outcome, and close the collection barrier before triage
- register all canonical candidates, resolve classification conflicts, then approve and materialize one deterministic remediation batch
- run fixed-target convergence explicitly with `hloop review readiness` and `hloop review convergence prepare|record`
- prepare a complete manual final certification after convergence, rather than treating a zero finding count as sufficient
- preserve `review_after_merges`/`gap_after_merges` for explicitly configured `merge-count` and migrated legacy loops
- allow Gap Auditor and Reviewer to run while Workers continue on isolated branches, but do not merge the branch they are reading

The scheduler does not silently replace a prepared convergence or manual-final plan with an ordinary Reviewer. A dispatch freeze may stop new task and role starts while validation, harvest, merge, follow-up recording, and final evidence continue.

## Release Scope And Task Authorization

Before dispatching a new release, lock the source snapshot:

```bash
hloop release-scope lock --source MISSION.md --source PLAN.md \
  --plan-item-ref P001 --requirement-ref R001 --scope-ref release-scope-contract
hloop release-scope status --json
```

The lock stores source digests, `scope_revision`, `source_snapshot_revision`, and stable plan/requirement references. Use `release-scope amend` for an editorial correction, a clarification, or an explicitly authorized scope change. An unrecorded source drift blocks review readiness and final certification.

After the lock, every task must carry `task_origin` and a matching authorization: `planned` references a plan item or requirement, `finding` references a confirmed in-scope finding and why it is fixed now, `user-amendment` references the input and new scope revision, and `operational` records a non-product reason. `hloop task new`, triage, pump, and conductor all use the shared preflight. A review candidate alone is not permission to create a remediation task.

## Bounded Convergence And Manual Final

At a stable batch boundary:

```bash
hloop review readiness --json
hloop review convergence prepare --mode swarm --json
hloop review convergence record --fix-round 0 --json
hloop final-review prepare --mode swarm --json
hloop final-review record --json
```

`convergence prepare` fixes base and target SHA and writes a plan; it does not start a Reviewer. `convergence record` recomputes manifest completeness and verified actionable findings. New loops allow at most two automatic fix rounds. `final-review record` recomputes lane completion, independent verification, shortfalls, plan/manifest identity, scope snapshot, report presence, and the zero-actionable-finding condition. A follow-up can remain open only when its disposition is non-blocking and current acceptance is satisfied.

When convergence is exhausted or manual final is failed/incomplete, `hloop review reopen --action ... --user-input-id Uxxxx` is the only transition that may reopen task creation. The transition is atomic with respect to dispatch freeze, certification invalidation, scope amendments, and authorized extra rounds; do not reset `STATE.json` by hand.

## Event-Driven Progress

Role reports are typed as `ack`, `milestone`, `attention`, or `completion`.
Require the blocking `agent ack exchange` to return an exact approval before
material edits for long-running work. Resolve it with `agent ack resolve`; the
default durably publishes decision and availability without calling the pane
message API. Use `--notify-pane` only for explicit advisory/debug notification.
Decision, availability, authenticated role application, and optional pane
notification are separate audit records. Treat milestone as inbox-only unless
its state change requires intervention. Handle attention promptly. Verify a
completion report against the committed artifact, target SHA, write scope, and
validation before harvest or requirement progress changes.

Use `hloop inbox list` and `hloop manager next` before reading panes. When no event needs action, use `hloop manager sleep --ttl-seconds <n>` to register a run-bound wake lease. Consume a handled wake with `hloop inbox ack <event-id>`. Delivery is at least once; event ID and lease generation are the idempotency boundary.

If the broker is unavailable, the role writes to the local spool. Use `hloop broker status` and `hloop broker recover`; do not reconstruct or copy report bodies through pane chat. Pane inspection remains a fallback for silent exit, crash, or missing report.

## Requirement And Decision Progress

Record new user input with `hloop input record` before changing requirements. Accept stable requirement IDs with `hloop requirement new`, then use `hloop progress record` only for legal transitions. `verified` requires Manager or HLoop evidence for an artifact and passing test or QA on one head SHA; an agent report alone is insufficient.

Create `advisory`, `deferred-user`, or `blocking-user` decisions with `hloop decision new`. User-decision classes require affected tasks. Continue unrelated work until every safe transition is dependency-blocked. Store the answer with `decision respond` and the Manager-confirmed outcome with `decision resolve`.

Before a user-visible progress reply or terminal phase change, use requirement states to report verified, implemented but unverified, blocked, deferred, and superseded work. Do not use task counts as a substitute for user outcomes.

## Product Profile

Use `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md` as the Manager-owned policy layer. It decides:

- branch strategy: default `integration`, or product-specific `pr-per-task` / `custom`
- Worker protocol: default `native`, or compatibility `codex-impl`
- ordinary Reviewer protocol: fresh 0.5.3 default `codex-review-multi-v2`, or explicit `native` override
- pre-final protocol: default `codex-review-multi-v2`, with a separate supported `native` setting
- manual-final protocol: only `codex-review-multi-v2`; no `native` override
- Worker / Reviewer / Gap Auditor / Advisor agent provider and model
- review lanes: canonical fresh Reviewer topology is six lanes
- Worker QA profile
- Manager final QA profile
- Advisor policy: disabled by default; explicit request only

Fresh 0.5.3 ordinary review defaults to `reviewer.protocol = "codex-review-multi-v2"` with the canonical six-lane Reviewer topology. `--review-protocol native` is an explicit override for ordinary review only. Select the supported native pre-final path separately with `[defaults.review] pre_final_protocol = "native"`. Manual-final has no native override; `manual_final_protocol` accepts only `codex-review-multi-v2`.

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

Do not use this TUI follow-up path to resume a semantic ACK. ACK resolution and
application use the broker/state exchange above. Do not send other follow-ups
directly with `herdr pane run` unless debugging the pane itself. `hloop ...
message` refuses to send when the target pane is not Codex, is showing the
trust prompt, or is still working. It then uses `send-text`, waits for the input
to appear, pauses before Enter, and verifies that Codex started working or
answered before reporting success. If delivery fails after Manager wrote a
follow-up, `hloop` records the undelivered message under
`.ai/herdr-dev-loop/loops/<namespace>/inbox/pending/` and marks it in the target
state; retry that pending file when the pane is ready instead of reconstructing
the instruction from memory.

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

Classify each candidate on independent axes before choosing an action: `fact_status` (`confirmed`, `refuted`, `insufficient_evidence`), `severity` (`P0`–`P3`), `origin` (`introduced`, `diff-expanded-pre-existing`, `unrelated-pre-existing`, `unknown`), `contract_relation` (`in_scope`, `outside_release`, `ambiguous`), `decision_requirement` (`none`, `spec`, `user`), `disposition`, and `release_effect` (`blocking`, `non_blocking`). Do not infer release disposition from severity alone.

An introduced or diff-expanded in-scope regression cannot be deferred as a follow-up. An in-scope P0/P1 requires `fix_now`, `disable_feature`, or `user_decision`; confirmed outside-release work normally becomes `defer_follow_up` or `discard`; an accepted risk requires explicit authorization. A refuted candidate is discarded. Insufficient evidence that prevents current acceptance or safety requires a user decision; insufficient evidence outside the release may remain a non-blocking follow-up.

P0/P1 findings normally create a provenance-linked fix task only after Manager verifies the trigger and contract relation. P2 findings create a fix task, accepted risk, or follow-up depending on whether they affect `MISSION.md` done criteria. P3 findings should not block completion unless the mission explicitly requires them. If rejected as false positive or unrelated pre-existing, record the code evidence and classification in the Manager-owned triage/journal artifacts.

`hloop triage` separates valid and rejected Fix Task Candidates. Rejected candidates are written to the triage draft with reasons such as missing `write_allow`, `acceptance`, `rationale`, or invalid priority. Do not rerun with `--create-tasks` expecting rejected candidates to become tasks; either fix the source artifact/candidate block, create a task manually, or record why the finding is not actionable.

For revision-3 review, use the epoch collection barrier before triage. Record all required Reviewer and Gap execution outcomes, including failures, then register every canonical candidate. Resolve classification conflicts before `triage epoch <epoch> --approve-batch`; approval is a single transition bound to the exact candidate set, scope, round, authorization, and artifact paths. `--materialize-batch` writes its plan before creating tasks and an exact retry repairs only digest-matching partial work.

Do not mark the loop done only because a review artifact exists. Manager must close the review gate after triage.

Gap findings are not generic review findings. `missing`, `partial`, and `needs-decision` items that affect `MISSION.md` done criteria normally create a fix task or a decision. `obsolete-spec` items should update or explicitly retire the stale plan/spec source before closing the gap gate.

For non-blocking candidates, use `hloop follow-up add` rather than creating a task directly. The CLI requires a review fingerprint, evidence, affected path or symbol, deferral reason, and reconsider condition, then deduplicates by the stable semantic issue key (`fu:v1:sha256:<digest>`). Follow-up issue keys intentionally exclude target SHA, severity, title, and proposed fix so repeated reviews update one follow-up.

Do not mark the loop done only because a gap artifact exists. Manager must close the gap gate after triage.

Specification choices that cannot be resolved from the original plan/spec belong in `DECISIONS.md`. If user input is required, also update `USER_ACTION_REQUIRED.md`, set the phase to `blocked_user_decision`, and stop dispatching new Workers until the decision is resolved.

## Manager Final QA

Worker QA is task-local. Manager final QA is a separate combined-implementation gate controlled by `manager_qa_profile`.

When `manager_qa_profile` is `none`, no separate final QA gate is required.

This setting is separate from 0.5.3 review-epoch and manual-final certification. Even when `manager_qa_profile: none`, a new loop must complete required epoch collection, bounded remediation, fixed-target convergence, and `final-review` evidence. Manual final is the review certification gate; Manager QA is product-environment QA.

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
- release-scope lock status, scope/source revisions, and amendments
- convergence target/fix rounds and manual-final certification status
- finding dispositions, accepted risks, and first-class follow-up issue keys
- advice status when Advisor was used
- accepted risks
- remaining follow-ups

Before `finish`, close the current batch, complete review triage, clear pending fix-task drafts, and run `hloop final-gates arm`. Creating a new task disarms the arm. `finish` must be the only transition to done and must recheck all current-head gates.
