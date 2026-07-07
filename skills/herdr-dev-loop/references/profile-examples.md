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
hloop qa record --status passed --summary "Staging smoke passed for <feature>" --evidence https://staging.example.test/<path> --evidence .ai/loop/reports/screenshots/<case>.png
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
