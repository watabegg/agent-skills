# Reviewer Contract

Reviewer compares the integration result against the base branch and the loop artifacts. Reviewer is a Manager gate, not a generic review bot.

Default runner: interactive Codex TUI in a detached review worktree. Use this so Manager can monitor progress and the Reviewer can reliably write the final Markdown artifact.

Reviewer TUI uses `workspace-write` sandbox because the final report must be written. Treat the codebase as read-only during investigation. The only permitted write is `.ai/loop/reviews/<review-id>.md` after review is complete. `hloop reviewer harvest` validates the review worktree and blocks if any other file changed.

Assume Reviewer runs are long-running. Manager should inspect progress with `hloop reviewer watch <review-id>`, wait with `hloop tick --review-wait-ms <ms>`, or continue other safe Manager work while the Reviewer is running. The Reviewer is not complete until `reviews/<review-id>.md` exists and is non-empty.

The Reviewer prompt must say:

- follow the HLoop Native Review Protocol by default
- do not edit code
- do not commit, merge, rebase, switch branches, run formatters, or run automatic fixes
- review base branch vs integration branch plus task/result/validation artifacts
- verify that each finding can actually occur
- distinguish newly introduced, diff-expanded pre-existing, and unrelated pre-existing issues
- write no files while investigating
- after review is complete, write only `reviews/<review-id>.md`
- include `## Fix Task Candidates` with machine-readable task candidate blocks for findings that should become Worker fix tasks
- print `HERDR_LOOP_REVIEW_DONE:<review-id>:<reported|blocked|failed>`

## HLoop Native Review Protocol

Native review is tuned for this loop and does not depend on `$codex-review-multi-v2`.

Default review lanes:

- `integration-contract`: task/result artifacts, write-scope boundaries, merge safety, generated-code boundaries, and state consistency
- `correctness`: user-visible behavior, edge cases, API/schema contracts, backward compatibility, and repository conventions
- `risk`: security, auth/authorization, privacy, tenant scope, data integrity, concurrency, idempotency, migrations, performance, rollback, and observability
- `validation-qa`: whether validation commands, logs, Worker QA evidence, and Manager final QA readiness/evidence are sufficient for the changed product surface

Add `ux` as a lane when UI is touched, or define custom lane names in `PROFILE.md`.

Native review must follow this quality floor:

1. Build an inventory with `git diff --name-status <base>...<head>`, `git diff --stat <base>...<head>`, task/result artifacts, validation logs, and PROFILE/PLAN/DECISIONS.
2. Read changed files plus nearby callers, callees, tests, schemas, migrations, generated-code sources, and CI/workflow files needed to verify the behavior.
3. Cross-check Worker result claims against actual changed files, write scopes, validation commands, Worker QA profile, and acceptance criteria.
4. For every candidate finding, prove the trigger path in current code and discard weak, speculative, unrelated pre-existing, or unsupported claims.
5. Treat missing validation or QA evidence as a finding only when it leaves a concrete product risk, and name the exact missing evidence. Separate task-local Worker QA gaps from final Manager QA gaps.
6. Prefer fewer high-confidence findings over broad checklists.

Each finding must include:

- severity `P0|P1|P2|P3`
- file path and line number where possible
- triggering scenario
- why it matters for the product or loop gate
- whether it is newly introduced, diff-expanded pre-existing, or unrelated pre-existing
- recommended Manager action

Prefer no finding over weak or speculative findings.

## Review Scope

Default review mode is branch-style diff plus loop artifacts:

```text
git diff <base-branch>...<integration-branch>
.ai/loop/MISSION.md
.ai/loop/PLAN.md
.ai/loop/PROFILE.md
.ai/loop/DECISIONS.md
.ai/loop/tasks/*.md
.ai/loop/results/*/result.md
.ai/loop/validation/*.log
```

Use uncommitted diff review only when Manager intentionally wants to review local integration changes before commit.

## Compatibility Mode

Use `review_protocol: codex-review-multi-v2` only when Manager intentionally wants `$codex-review-multi-v2`. Even then, Reviewer must still produce the HLoop artifact, action labels, and `Fix Task Candidates`.

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

Manager can generate a draft from the artifact:

```bash
python3 <this-skill>/scripts/hloop triage review R001
```

Add `--create-tasks` only after Manager approves the generated draft.

After harvesting the review artifact, Manager should close the Reviewer Herdr pane and archive the captured Codex session. Keep the pane only when Manager needs to inspect the live transcript.
