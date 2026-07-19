# State Machine

Use a bounded state machine. Do not let Manager, Worker, Gap Auditor, Reviewer, or Advisor coordinate through freeform chat when an artifact can represent the state. Use `PROFILE.md` for product-specific branch, review, QA, and agent backend strategy.

Each state machine is selected by `--namespace` and lives only below `.ai/herdr-dev-loop/loops/<namespace>`. Legacy `.ai/loop` is never a fallback source. Multiple namespaces may coexist, while the repository-and-namespace runtime lock serializes their mutations.

At session entry, Manager prints `hloop version` and identifies the runtime version, loop-pinned version, and `run_id` in the first progress message. Each started role likewise identifies its version and role ID before investigation. `skill_version` in state and artifacts is part of the durable state-machine identity, not merely display metadata.

Run all loop mutations through `hloop` using the absolute helper path when needed. The helper serializes mutations with `/tmp/herdr-dev-loop-<uid>/locks/<sha256>.lock`, where the digest identifies the canonical Git common directory and namespace. This fixed POSIX location is independent of `HLOOP_RUNTIME_DIR`, `XDG_RUNTIME_DIR`, and `TMPDIR`, is private to the UID, and remains outside Git metadata. Manager should not update `STATE.json`, start Worker/Reviewer/Gap/Advisor sessions, merge Worker branches, or rewrite result artifacts by hand to bypass a helper failure.

Use `hloop status`, `hloop dashboard`, `hloop conductor`, and `hloop doctor --sessions` as read-only state inspection surfaces. They do not advance the state machine; they help Manager decide which bounded transition to run next. `conductor` also audits trust signals left in `STATE.json` and pane output, including unsafe sandbox values, dangerous Codex launch markers, non-hloop prompt paths, unharvested artifact states, untrusted Worker head markers such as `manager-working-tree` or `pending_code_commit`, Manager-owned Worker result paths, and manual integration traces.

Before creating a role worktree, hloop verifies that the role's Manager-owned input files are committed at the target branch or SHA. A stale snapshot stops without creating the worktree and tells Manager to checkpoint the inputs.

For `persistence: local-only`, committed snapshot checks are replaced by copying the selected namespace into the role worktree. Repository-specific `worktree_setup_commands` run before pane launch. Setup or launcher failure rolls back only the worktree and branch created by that start attempt; pre-existing worktrees and branches are preserved.

An artifact-less role can transition to `aborted` through `hloop agent abort`. `hloop agent requeue` archives attempt metadata and makes the Worker or role ID startable again with a new attempt id. The original attempt base is immutable; an unmerged branch with commits is archived instead of silently reused or deleted. Worktree cleanup refuses product-dirty paths unless Manager explicitly chooses `--force-cleanup`.

State format 3 revision 3 is required by herdr-dev-loop 0.5.3. The migration path is format 1 -> 2 -> 3.0 -> 3.1 -> 3.2 -> 3.3; first run `hloop migrate --dry-run`, inspect the complete transaction, then run `hloop migrate --apply`. Migration preserves `run_id`, writes a digest-bound archive and marker, and refuses active, unharvested, live, dirty, or cleanup-failed role/merge/remediation state. Use `--resume` after interruption. Rollback is allowed only before the first recorded 0.5.3 mutation. Unknown future revisions allow explicit read-only inspection but reject mutation and downgrade.

## Phases

- `initialized`: `.ai/herdr-dev-loop/loops/<namespace>` exists and the integration branch is known.
- `planning`: Manager is still refining `MISSION.md`, `PLAN.md`, or task boundaries.
- `dispatching`: queued tasks can be started.
- `running`: at least one Worker or Reviewer is active.
- `harvesting`: Manager is reading result or review artifacts.
- `merging`: Manager is merging one Worker branch.
- `branch_handoff`: a Worker result is ready, but `branch_strategy` requires Manager-controlled PR, release, or custom branch handoff instead of automatic merge.
- `validating`: Manager is running integration validation.
- `gap_checking`: Gap Auditor is running or its artifact is being triaged.
- `reviewing`: Reviewer is running or its artifact is being triaged.
- `review_readiness`: the current batch is closed and the fixed-target review prerequisites are being checked.
- `review_convergence`: a fixed-target convergence plan or manifest is pending, or a bounded remediation round is required.
- `review_convergence_exhausted`: the convergence round limit was reached with verified actionable findings; only an explicit reopen can select the next action.
- `awaiting_manual_final_review`: convergence is complete and the separate manual-final certification is prepared or awaiting its evidence.
- `manual_final_review_passed`: the fixed-target manual-final evidence is complete and has zero verified actionable findings.
- `manual_final_review_incomplete`: manual-final evidence is missing lanes, verification, report, or other required completeness data.
- `manual_final_review_failed`: manual-final evidence is stale, inconsistent, or contains a verified actionable finding.
- `advising`: explicit Advisor consultation is requested, running, or awaiting Manager review.
- `manager_qa`: Manager final QA is required before completion.
- `waiting_worker`: Workers are still running and no result artifact is ready.
- `waiting_gap`: a Gap Auditor is still running after the bounded wait.
- `waiting_review`: a Reviewer is still running after the bounded wait.
- `paused`: Manager explicitly paused the loop; use `hloop resume` after checking the environment.
- `blocked_agent`: a current-attempt terminal marker was observed without its required artifact.
- `blocked_user_decision`: a blocking decision is required from the user.
- `blocked_environment`: required tool, credentials, branch, or worktree state is missing.
- `blocked_conflict`: merge conflict or write-scope conflict needs judgment.
- `failed_validation`: integration validation failed and no obvious local fix was applied.
- `failed_manager_qa`: Manager final QA found a blocking failure.
- `no_progress`: no safe transition exists and Manager inspection is required.
- `ready_to_finish`: all automatic work is complete and Manager must run `hloop finish`.
- `done`: `hloop finish` confirmed the current integration SHA has passing validation, closed review and gap gates, required Manager QA, no active agents/merge, a clean Manager checkout, and no unresolved cleanup.

## Tick Order

Each tick starts by reading:

1. `.ai/herdr-dev-loop/loops/<namespace>/MISSION.md`
2. `.ai/herdr-dev-loop/loops/<namespace>/PLAN.md`
3. `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`
4. `.ai/herdr-dev-loop/loops/<namespace>/STATE.json`
5. `.ai/herdr-dev-loop/loops/<namespace>/DECISIONS.md`

Then run, at most, one material transition:

1. replay a valid broker spool and drain the durable Manager inbox
2. harvest completed Workers, Gap Auditors, Reviewers, or explicitly started Advisors
3. close any harvested Worker/Gap Auditor/Reviewer/Advisor pane and clean up provider sessions when supported unless inspection is explicitly requested
4. merge one ready Worker when no Gap Auditor or Reviewer is currently reading the integration branch
5. validate integration and update requirement progress only after verifying artifact, SHA, and test or QA evidence
6. require Manager triage for harvested Gap Auditor or Reviewer artifacts
7. start one Gap Auditor when validation passes and the gap gate is open
8. start one Reviewer when validation passes and the review gate is open
9. dispatch queued Workers in a batch up to the safe worker limit, excluding only dependency-scoped decision blocks
10. wait for a running role only after no other safe transition is available, using a Manager wake lease for structured reports
11. require Manager final QA when `manager_qa_profile` is not `none`
12. arm stable final gates and generate the terminal report only after all current-head gates pass

Prefer a small number of obvious transitions over attempting to finish a goal in one tick.

For a new 0.5.3 loop, the review transition is bounded by planning, candidate, epoch, and artifact gates:

1. close the current task batch and verify current planning, release-scope, candidate, Patch Review, full-suite, clean-checkout, and role/merge prerequisites;
2. create an immutable review epoch at one target SHA, reserve shared capacity before each required Reviewer/Gap process, record every terminal outcome, and close the collection barrier only when all required executions are complete;
3. register every normalized candidate, stop on classification conflict, then approve and materialize at most one deterministic write-ahead remediation batch; a changed target starts a new epoch;
4. after a clean epoch, enter fixed-target convergence and record complete lane and verification evidence; if actionable findings remain, keep dispatch frozen while the Manager performs at most two automatic fix rounds;
5. when convergence records zero verified actionable findings, prepare `reviews/final/PLAN.json`, `MANIFEST.json`, and `FINAL.md`, then record the independent manual-final certification;
6. permit `hloop review reopen` only for an exhausted, failed, or incomplete review state, with explicit user-input provenance and the selected remediation, scope amendment, feature action, retry, or abort policy.

The convergence and manual-final artifacts are not interchangeable. A new task,
scope amendment, target SHA drift, or source snapshot drift invalidates the
affected fixed-target evidence and returns the state to dispatching or a
reopen-required phase. Do not reset `STATE.json`, reuse a stale manifest, or
turn a non-blocking follow-up into a new in-scope task without authorization.

`pump` repeats this bounded tick order up to `--max-transitions` and sleeps briefly between ticks by default so waiting phases can be polled instead of consuming the whole transition budget immediately. It must stop when:

- a Gap Auditor or Reviewer artifact needs Manager triage
- branch strategy requires Manager handoff before merge or publish
- all safe immediate transitions are exhausted and the loop is waiting, only when `--stop-on-waiting` is set
- a paused, blocked, failed, no-progress, ready-to-finish, or done phase is reached
- the transition limit is reached

Do not let `pump` turn review/gap findings directly into queued tasks without Manager approval. Use `hloop triage ...` to draft fix tasks first, then rerun with `--create-tasks` only after the draft is accepted.

## Default Cadence

Defaults are intentionally active for the scheduler, while review cadence is
policy-driven:

- `max_workers: 3`
- `max_reviewers: 1`
- `max_gap_auditors: 1`
- `review_policy.cadence: batch` for new 0.5.3 loops
- `review_policy.pre_final_protocol: codex-review-multi-v2`
- `review_policy.manual_final_protocol: codex-review-multi-v2`
- `review_policy.max_fix_rounds: 2`
- `review_policy.final_required: complete_zero_verified_actionable_findings`
- `review_after_merges: 1` and `gap_after_merges: 3` remain legacy/merge-count knobs
- `branch_strategy: integration`
- `worker_protocol: native`
- `review_protocol: codex-review-multi-v2` for fresh ordinary review
- canonical Reviewer topology: six lanes
- role agent providers/models: Codex `auto` by default
- `worker_qa_profile: repo-default`
- `manager_qa_profile: none`

Fresh 0.5.3 ordinary review defaults to `reviewer.protocol = "codex-review-multi-v2"` with the canonical six-lane Reviewer topology. `--review-protocol native` is an explicit override for ordinary review only. Select the supported native pre-final path separately with `[defaults.review] pre_final_protocol = "native"`. Manual-final has no native override; `manual_final_protocol` accepts only `codex-review-multi-v2`.

The manual-final policy is fail-closed: its schema accepts only the implemented
`codex-review-multi-v2` protocol. Neither an ordinary `--review-protocol native`
override nor a separate native pre-final setting makes `native` valid for
manual-final certification.

For new loops, ordinary review waits for a closed batch and the Manager explicitly
prepares fixed-target convergence; it is not restarted after every incremental
commit. Gap Auditor cadence remains product/profile-driven. Migrated or explicitly
configured `merge-count` loops retain the historical review and gap thresholds.

Advisor has no default cadence. Manager may explicitly create an Advisor request when review/gap findings require another model's reasoning but do not require user input.

## Gap And Reviewer Wait Behavior

Assume gap audits and reviews can take minutes. A running Gap Auditor or Reviewer is not the same as a reported artifact; do not print `gap triage required` or `review triage required` until the artifact exists and has been harvested.

While a Gap Auditor or Reviewer is running:

- do not merge Worker branches into the integration branch under review
- harvest finished Workers and close their panes
- dispatch queued Workers up to `max_workers` when their write scopes are non-overlapping and the state machine allows it
- start Gap Auditor and Reviewer on the same integration head in separate ticks when both gates are open
- inspect live Gap Auditor output with `hloop gap watch <gap-id>` instead of guessing from pane status alone
- inspect live Reviewer output with `hloop reviewer watch <review-id>` instead of guessing from pane status alone
- wait up to `gap_wait_ms` or `review_wait_ms` when there is no other safe work
- tick again later if the wait times out

When the gap artifact appears, harvest it from the detached gap worktree, verify the Gap Auditor changed no files except the gap artifact, close the Gap Auditor pane, clean up provider session state when supported, remove the gap worktree, and require Manager triage before closing the gap gate. Worktree cleanup is best-effort after harvest; if filesystem permissions prevent removal, record the cleanup failure in `STATE.json` and continue gate triage.

When the review artifact appears, harvest it from the detached review worktree, verify the Reviewer changed no files except the review artifact, close the Reviewer pane, clean up provider session state when supported, remove the review worktree, and require Manager triage before closing the review gate. Worktree cleanup is best-effort after harvest; if filesystem permissions prevent removal, record the cleanup failure in `STATE.json` and continue gate triage.

For Reviewers and Gap Auditors, artifact frontmatter status and Manager gate status are separate. `artifact_status` stores the artifact's reported result, while `gate_status` tracks Manager workflow progress such as `running`, `reported`, or `triaged`. The legacy per-agent `status` field mirrors `gate_status` for compatibility.

For Advisors, participant artifacts are harvested into `.ai/herdr-dev-loop/loops/<namespace>/advice/`. Advisor outputs never close review/gap gates by themselves; Manager records the accepted recommendation in `DECISIONS.md`, accepted-risk notes, or fix tasks, then closes the advice request.

## Stop Conditions

Set a blocked or failed phase and stop when:

- `HERDR_ENV=1` is absent.
- required CLI tools are missing.
- `STATE.json` is unreadable or contradicts the current branch.
- the release-scope lock is absent, stale, or source digests no longer match.
- a new task or role is requested while dispatch freeze is active.
- every remaining safe task is dependency-blocked by an unresolved `blocking-user` decision.
- a Worker result is missing required fields.
- a Worker result reports `partial`, `blocked`, `failed`, `abandoned`, `merge_ready: false`, blocking questions, or missing validation.
- a Worker changed files outside its allowed scope.
- a merge conflict appears.
- validation fails.
- convergence is incomplete or exhausted, or manual-final certification is incomplete or failed.
- Manager final QA fails or is blocked when required.
- Gap Auditor reports a missing, partial, or needs-decision item that affects mission done criteria.
- Reviewer reports a P0/P1 finding that cannot be fixed without a user decision.

Do not dispatch new Workers while blocked.

Waiting for a running Worker, Gap Auditor, Reviewer, or explicit Advisor is not itself a hard failure. Set `waiting_worker`, `waiting_gap`, or `waiting_review` for automatic loop lanes, report the exact agent ids, and tick again later. Advisor waits are Manager-directed through `hloop advisor watch`, `hloop wait`, or `hloop harvest`. Set `no_progress` only when no agent is running, no dependency can advance, and the next Manager action is unclear.

When the phase is `no_progress` or a long-running loop appears stuck, run `hloop conductor --no-fail` before changing strategy. Treat its P0/P1 findings as the next concrete Manager action unless the finding is proven stale by disk state.

Do not continue normal dispatch or merge work while `conductor` reports a P0 trust issue. Stop the affected pane if it is still running, restart the agent through `hloop`, or record the run as unsafe. Treat P1 trust issues as blockers for the affected task/gate until Manager has rerun, harvested, or explicitly recorded the residual risk.

An unresolved `blocking-user` decision blocks its named tasks and their unmerged dependencies. It becomes a loop-wide `blocked_user_decision` only after no unaffected queued task, running role, merge-ready result, validation, review, or gap work remains. A response alone does not unblock the tasks; the Manager must record a terminal decision resolution.

Before terminal completion, Manager closes the current batch, completes review triage, clears fix-task drafts, and runs `hloop final-gates arm`. The arm is pinned to one target SHA. A newly created task disarms it. `hloop finish` rechecks merged tasks, current-head validation, review, gap, Manager QA, cleanup, and the arm before writing the final target and report.
