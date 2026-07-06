# Validation Policy

Validation is repository-specific. Do not invent framework commands when the repository already has scripts, CI, Make targets, or documented checks.

## Levels

- `L0`: formatting, lint, typecheck, `git diff --check`, artifact/schema checks
- `L1`: relevant unit tests for changed files
- `L2`: broader build or test suite
- `L3`: e2e, browser/manual verification, deploy/preview QA

## Command Selection

Pick commands from current repository evidence:

- `package.json` scripts for npm/pnpm/yarn projects
- `Makefile` targets
- `go.mod` and existing `go test` patterns
- `Cargo.toml` and existing cargo commands
- CI workflow files
- README or project docs

Record exact commands and results in Worker result artifacts and in `STATE.json.last_validation`. Integration validation must also capture stdout/stderr under `.ai/loop/validation/` and record the relative log path in `STATE.json.last_validation.results[].log`.

## Integration Failure

When integration validation fails:

1. stop dispatching and merging
2. record command output path or summary
3. set phase to `failed_validation`
4. create a fix task or ask the user if rollback/fix direction is not obvious

Do not continue merging more Worker branches into a broken integration branch.

When integration validation succeeds after a Worker integration, remove that Worker's local worktree and branch. For squash integrations, branch deletion normally requires force deletion because the branch commits are intentionally not ancestors of the integration branch.
