# Reviewer Contract

Reviewer compares the integration branch against the base branch.

Default runner: non-interactive `codex exec` with read-only sandbox. Use interactive TUI only when Manager intentionally wants to discuss review scope with the Reviewer.

The Reviewer prompt must say:

- use `$codex-review-multi-v2`
- do not edit code
- review base branch vs integration branch
- verify that each finding can actually occur
- distinguish newly introduced, diff-expanded pre-existing, and unrelated pre-existing issues
- write only `reviews/<review-id>.md`
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
