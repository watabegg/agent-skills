# Profile And Goal Prompt Examples

Use these examples when the user wants `/goal` to choose branch strategy, review lanes, Worker QA, or Manager final QA behavior.

## Strategy Summary

`branch_strategy: integration` means hloop owns one integration branch. Workers branch from that integration branch, Manager merges Worker branches into it, validation runs after each merge, and Reviewer/Gap Auditor inspect the integration head. This is the most automated mode.

`branch_strategy: pr-per-task` means each Worker branch is expected to become its own PR or product-level handoff. hloop still coordinates tasks, worktrees, artifacts, Reviewers, Gap Auditors, and triage, but when a Worker is merge-ready `tick` / `pump` stop at `branch_handoff` instead of auto-merging. Manager follows PLAN.md for PR creation, preview QA, and final integration.

`branch_strategy: custom` means the product has a different branch or release flow. Manager must write that flow into PLAN.md before dispatching Workers. hloop does not assume how merge, release, deploy, or QA handoff works.

## QA Profile Split

`worker_qa_profile` is task-local. It tells each Worker what QA evidence to record before `merge_ready`.

`manager_qa_profile` is final combined QA. It tells Manager whether to run and record a final local/preview/staging QA gate in `qa/FINAL.md` before `done`.

The deprecated `qa_profile` CLI flag is treated as `worker_qa_profile` only.

## Agent Backend Split

Agent backend selection is separate from protocol selection.

- `worker_protocol` and `review_protocol` choose the HLoop behavior contract.
- `worker_agent_provider`, `reviewer_agent_provider`, `gap_agent_provider`, and `advisor_agent_provider` choose the CLI provider (`codex` or `claude`).
- `worker_agent_model`, `reviewer_agent_model`, `gap_agent_model`, and `advisor_agent_model` choose the provider model, or `auto` for CLI default.
- Codex remains the default fallback for every role.
- Manager is always the agent currently using this skill; hloop only spawns subordinate role agents.

Advisor is opt-in only. It does not run from `tick` or `pump`.

## `/goal` Example: Integration Branch With Local Worker QA And No Final QA

```text
/goal Implement <feature>.

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: integration
- worker_protocol: native
- review_protocol: native
- review_lanes: integration-contract, correctness, risk, validation-qa, ux
- worker_qa_profile: local
- manager_qa_profile: none

Branch/QA requirements:
- Use one hloop integration branch.
- Worker branches should merge into the integration branch only through Manager.
- Workers run repo-native checks plus local browser/API QA for touched user workflows.
- No separate Manager final QA gate is required.
```

Expected Manager setup:

```bash
hloop init ... --branch-strategy integration --worker-protocol native --review-protocol native --worker-qa-profile local --manager-qa-profile none --review-lane integration-contract --review-lane correctness --review-lane risk --review-lane validation-qa --review-lane ux
```

Expected behavior:

- `pump` dispatches non-overlapping Workers.
- Manager auto-merges ready Worker branches into the integration branch when gates allow it.
- Validation runs after each merge.
- Reviewer checks code behavior plus task/result/validation/Worker QA evidence.
- Gap Auditor periodically checks original plan/spec coverage against the integration head.

## `/goal` Example: Mixed Codex/Claude Agents With Advisor Disabled

```text
/goal Implement <feature> with Codex Workers, Claude review, and Codex gap checks.

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: integration
- worker_protocol: native
- review_protocol: native
- worker_agent_provider: codex
- worker_agent_model: auto
- reviewer_agent_provider: claude
- reviewer_agent_model: opus
- gap_agent_provider: codex
- gap_agent_model: auto
- advisor_enabled: false
- worker_qa_profile: none
- manager_qa_profile: none

Review/GAP requirements:
- Run Reviewer after each validated integration advance.
- Run Gap Auditor before final completion.
- Do not run Manager final QA.
```

Expected Manager setup:

```bash
hloop init ... --branch-strategy integration --worker-protocol native --review-protocol native --worker-agent-provider codex --worker-agent-model auto --reviewer-agent-provider claude --reviewer-agent-model opus --gap-agent-provider codex --gap-agent-model auto --worker-qa-profile none --manager-qa-profile none
```

## `/goal` Example: Explicit Cross-Model Advisor

```text
/goal Implement <feature>. Use Advisor only if review/gap findings leave a non-user-blocking specification or fix-strategy choice.

Use $herdr-dev-loop.

Loop profile:
- advisor_enabled: true
- advisor_mode: dialogue
- advisor_agent_provider: claude
- advisor_agent_model: opus

Advisor policy:
- Do not start Advisor automatically.
- When needed, create a dialogue request with one Codex participant and one Claude participant.
- Manager must record the accepted recommendation in DECISIONS.md or create fix tasks; Advisor cannot close gates.
```

Expected Advisor usage:

```bash
hloop advisor request --topic "Choose fix strategy for R001 auth edge case" --mode dialogue --participant codex:auto --participant claude:opus --source reviews/R001.md
hloop advisor start A001 --participant-id P1
hloop advisor harvest A001 --participant-id P1
hloop advisor start A001 --participant-id P2
hloop advisor harvest A001 --participant-id P2
hloop advisor close A001 --verdict decision-recorded --reason "Recorded chosen approach in DECISIONS.md"
```

## `/goal` Example: PR Per Task With Preview Final QA

```text
/goal Implement <feature> as independently reviewable PR slices.

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: pr-per-task
- worker_protocol: native
- review_protocol: native
- review_lanes: integration-contract, correctness, risk, validation-qa
- worker_qa_profile: repo-default
- manager_qa_profile: preview

Branch/QA requirements:
- Each Worker branch should be publishable as its own PR.
- Do not auto-merge Worker branches into an integration branch.
- When a Worker is merge-ready, stop for Manager PR handoff.
- Workers run task-local repo-native validation and QA.
- Manager records preview QA after the PR URL is available.
- If Preview is unavailable, record the exact blocker and any local fallback evidence separately.
```

Expected Manager setup:

```bash
hloop init ... --branch-strategy pr-per-task --worker-protocol native --review-protocol native --worker-qa-profile repo-default --manager-qa-profile preview --review-lane integration-contract --review-lane correctness --review-lane risk --review-lane validation-qa
```

Expected behavior:

- `pump` dispatches Workers and harvests result artifacts.
- When a Worker is merge-ready, hloop sets `phase: branch_handoff` and stops instead of auto-merging.
- Manager follows PLAN.md to publish or update the Worker PR.
- Manager records final preview evidence with `hloop qa record --status passed --summary "..."`

## `/goal` Example: Integration Branch With Final Staging QA

```text
/goal Implement <feature> and verify the combined change on staging before completion.

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: integration
- worker_protocol: native
- review_protocol: native
- review_lanes: integration-contract, correctness, risk, validation-qa
- worker_qa_profile: local
- manager_qa_profile: staging

Branch/QA requirements:
- Workers run local QA for their touched workflows.
- Manager runs staging QA only after integration validation, Reviewer, and Gap Auditor gates are closed.
- Staging QA requires <staging URL/source> and cleanup confirmation for any shared data.
```

Expected final QA record:

```bash
hloop qa record --status passed --summary "Staging smoke passed for <feature>" --evidence https://staging.example.test/<path> --evidence .ai/herdr-dev-loop/loops/<namespace>/reports/screenshots/<case>.png
```

Expected behavior:

- hloop coordinates tasks, panes, artifacts, review, gap checks, and triage.
- Manager writes the product-specific staging QA flow in PLAN.md when `custom` details are needed.
- `pump` stops at `manager_qa` until the final QA artifact is recorded.

## QA Profile Choices

- `repo-default`: use the repository's normal scripts, CI-equivalent checks, and existing evidence conventions.
- `local`: require local app/API/browser evidence when the workflow is runnable.
- `preview`: require PR/preview evidence when a preview URL exists; record infra blockers separately.
- `staging`: require staging evidence when credentials/URL/data rules are available.
- `custom`: follow the QA section in PLAN.md exactly.
- `none`: skip that QA layer only with an explicit recorded reason.
