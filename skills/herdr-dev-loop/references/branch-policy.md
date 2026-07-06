# Branch And Worktree Policy

Use one integration branch and one branch/worktree per Worker task.

```text
main or master
  -> ai/<goal-id>/integration
       -> ai/<goal-id>/T001-foo
       -> ai/<goal-id>/T002-bar
       -> ai/<goal-id>/T003-review-fix
```

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

## Worker Rules

- Worker stays in its own worktree and branch.
- Worker commits its own changes.
- Worker does not checkout, merge, rebase, or push the integration branch.
- Worker writes only allowed paths plus its own `results/<task-id>/result.md`.

## Merge Gate

Before merge, require:

- result artifact exists
- result artifact is committed at `HEAD:.ai/loop/results/<task-id>/result.md`
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
