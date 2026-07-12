# Branch And Worktree Policy

Default strategy uses one integration branch and one branch/worktree per Worker task.

```text
main or master
  -> ai/<goal-id>/integration
       -> ai/<goal-id>/T001-foo
       -> ai/<goal-id>/T002-bar
       -> ai/<goal-id>/T003-review-fix
```

The selected strategy lives in `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md` and `STATE.json.branch_strategy`.

Persistence is independent of branch strategy. `local-only` keeps namespaced loop artifacts in local `.ai` state, copies the snapshot into role worktrees, requires squash integration, and removes loop paths from the squash index before committing product changes. `branch-history` retains the older committed-snapshot contract.

Supported strategy labels:

- `integration`: built-in hloop automation owns an integration branch and merges Worker branches into it.
- `pr-per-task`: Worker branches may be published or reviewed as PRs; Manager must record the product-specific merge and QA handoff in `PLAN.md`.
- `custom`: project rules in `PLAN.md` override default branch assumptions. Manager must keep merge, validation, QA, and cleanup gates explicit.

Only `integration` is fully automated by `hloop merge` / `hloop pump`. Other strategies can still use hloop for task, artifact, pane, review, gap, and triage coordination, but Manager must not pretend the default merge/publish path applies.

## Manager Rules

- Manager works on the integration branch.
- Manager creates task branches from the current integration branch.
- `worker start` records the current integration branch HEAD as the Manager-owned task base.
- Manager integrates one Worker branch at a time.
- Default to `squash` so each task becomes one integration commit.
- Use `cherry-pick` only when preserving Worker commit boundaries matters.
- Manager runs validation after each merge.
- Manager removes the local Worker worktree and branch only after integration validation succeeds.
- Manager does not edit Worker branches directly.
- Manager does not keep merging when the integration branch is broken.

For `pr-per-task` or `custom`, rewrite these rules in `PLAN.md` before dispatching Workers. Keep these invariants even when branch names or publish flow change:

- each Worker has one clear write scope
- Manager can identify the exact base commit
- Manager can validate the produced diff before accepting it
- Reviewers and Gap Auditors know what head they are reading
- cleanup or PR handoff is recorded in the final report

## Worker Rules

- Worker stays in its own worktree and branch.
- Worker commits its own changes.
- Worker does not checkout, merge, rebase, or push the integration branch.
- Worker writes only allowed paths plus its own `results/<task-id>/result.md`.

## Merge Gate

Before merge, require:

- result artifact exists
- result artifact is committed at `HEAD:.ai/herdr-dev-loop/loops/<namespace>/results/<task-id>/result.md`
- result `status: done`
- result `merge_ready: true`
- result `head_sha` matches the Worker branch head
- changed files match `write_allow`
- changed files do not match `write_deny`
- no blocking questions
- validation commands are recorded
- validation is recorded with flat result frontmatter fields, not nested YAML maps
- no conflict markers are present

If validation fails after integration, stop and keep the Worker branch/worktree for debugging. Revert only when the revert is mechanically obvious and safe.
