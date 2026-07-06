# Reviewer Contract

Reviewer compares the integration branch against the base branch.

Default runner: interactive Codex TUI in a detached review worktree. Use this so Manager can monitor progress and the Reviewer can reliably write the final Markdown artifact.

Reviewer TUI uses `workspace-write` sandbox because the final report must be written. Treat the codebase as read-only during investigation. The only permitted write is `.ai/loop/reviews/<review-id>.md` after review is complete. `hloop reviewer harvest` validates the review worktree and blocks if any other file changed.

Assume Reviewer runs are long-running. Manager should inspect progress with `hloop reviewer watch <review-id>`, wait with `hloop tick --review-wait-ms <ms>`, or continue other safe Manager work while the Reviewer is running. The Reviewer is not complete until `reviews/<review-id>.md` exists and is non-empty.

The Reviewer prompt must say:

- use `$codex-review-multi-v2`
- do not edit code
- do not commit, merge, rebase, switch branches, run formatters, or run automatic fixes
- review base branch vs integration branch
- verify that each finding can actually occur
- distinguish newly introduced, diff-expanded pre-existing, and unrelated pre-existing issues
- write no files while investigating
- after review is complete, write only `reviews/<review-id>.md`
- print `HERDR_LOOP_REVIEW_DONE:<review-id>:<reported|blocked|failed>`

## Review Scope

Default review mode is branch-style diff:

```text
git diff <base-branch>...<integration-branch>
```

Use uncommitted diff review only when Manager intentionally wants to review local integration changes before commit.

## Manager Action Labels

Each finding should recommend one action:

- `fix_task`
- `decision_needed`
- `accepted_risk_candidate`
- `false_positive_candidate`

Manager, not Reviewer, makes the final triage decision.

After reading the artifact, Manager closes the review gate explicitly:

```bash
python3 <this-skill>/scripts/hloop reviewer close R001 --verdict passed --reason "No actionable findings"
```

Use `fix-tasks-created` when P0/P1/P2 findings were converted into new tasks, and `accepted-risk` only when the risk is recorded with a reason.

After harvesting the review artifact, Manager should close the Reviewer Herdr pane and archive the captured Codex session. Keep the pane only when Manager needs to inspect the live transcript.
