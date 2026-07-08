# Validation Policy

Validation is repository-specific. Do not invent framework commands when the repository already has scripts, CI, Make targets, or documented checks.

## Levels

- `L0`: formatting, lint, typecheck, `git diff --check`, artifact/schema checks
- `L1`: relevant unit tests for changed files
- `L2`: broader build or test suite
- `L3`: e2e, browser/manual verification, deploy/preview QA

`worker_qa_profile` is configured in `.ai/loop/PROFILE.md` and may be overridden per task. It is the QA each Worker records before reporting `merge_ready`.

`manager_qa_profile` is configured in `.ai/loop/PROFILE.md`. It is the separate final QA gate Manager records in `qa/FINAL.md` after the combined implementation is ready.

- `repo-default`: use repository-native checks and evidence conventions.
- `local`: prefer local service/browser/API verification for user-facing workflows.
- `staging`: use staging only when credentials, URLs, and cleanup rules are already known.
- `preview`: use PR/preview only when a URL is available or discoverable from repo tooling.
- `custom`: follow `PLAN.md` exactly.
- `none`: record why QA is intentionally skipped.

Worker QA quality floor:

1. Select QA from repository evidence, not generic preference.
2. Tie each QA case to a changed user workflow, API contract, migration/data path, or risk called out in MISSION/PLAN/task artifacts.
3. For browser-accessible workflows, capture browser-visible evidence when the selected profile provides a runnable URL or local app.
4. For staging/preview, record URL, environment, data setup, cleanup, screenshots/logs, and blockers.
5. For destructive or shared-data checks, record seed identifiers and cleanup confirmation.
6. If QA is blocked, report the exact missing credential, URL, migration, service, or data dependency instead of silently downgrading to unit tests.
7. If `worker_qa_profile: none`, record the reason in the Worker result.

Manager final QA quality floor:

1. Run only after integration validation, review triage, and gap triage are closed for the current implementation head.
2. Treat staging/preview/local final QA as an environment gate, not as a Worker task-local check.
3. Record the profile, status, summary, URLs, screenshots/logs, data setup, cleanup, and blockers in `qa/FINAL.md`.
4. If final QA is not required, use `manager_qa_profile: none`; do not use Worker QA settings to imply Manager final QA.
5. If final QA cannot run, record `blocked` with the exact missing credential, URL, service, migration, or data dependency.
6. If final QA fails, record `failed` and create fix tasks or ask for a decision before marking the loop done.

## Command Selection

Pick commands from current repository evidence:

- `package.json` scripts for npm/pnpm/yarn projects
- `Makefile` targets
- `go.mod` and existing `go test` patterns
- `Cargo.toml` and existing cargo commands
- CI workflow files
- README or project docs

Record exact commands and results in Worker result artifacts and in `STATE.json.last_validation`. Integration validation must also capture stdout/stderr under `.ai/loop/validation/` and record the relative log path in `STATE.json.last_validation.results[].log`. `hloop validate` preserves the live console output but trims trailing whitespace from saved validation logs so `.ai/loop/validation/*.log` does not create `git diff --check` noise.

Record Worker QA evidence paths, preview URLs, staging URLs, screenshots, seeded data cleanup, or blockers in the Worker result artifact when they apply. Record Manager final QA evidence in `qa/FINAL.md` and the final report. Do not force browser QA for every repository; choose the product-appropriate QA surface from PROFILE and PLAN.

In frontmatter, write non-empty command and result lists as multiline lists. `hloop` rejects non-empty inline lists for known list fields because comma-splitting command strings is unsafe. Empty lists such as `blocking_questions: []` remain allowed.

## Integration Failure

When integration validation fails:

1. stop dispatching and merging
2. record command output path or summary
3. set phase to `failed_validation`
4. create a fix task or ask the user if rollback/fix direction is not obvious

Do not continue merging more Worker branches into a broken integration branch.

When integration validation succeeds after a Worker integration, remove that Worker's local worktree and branch. For squash integrations, branch deletion normally requires force deletion because the branch commits are intentionally not ancestors of the integration branch.
