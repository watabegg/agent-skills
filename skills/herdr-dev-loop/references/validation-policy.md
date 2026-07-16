# Validation Policy

Validation is repository-specific. Do not invent framework commands when the repository already has scripts, CI, Make targets, or documented checks.

## Levels

- `L0`: formatting, lint, typecheck, `git diff --check`, artifact/schema checks
- `L1`: relevant unit tests for changed files
- `L2`: broader build or test suite
- `L3`: e2e, browser/manual verification, deploy/preview QA

`worker_qa_profile` is configured in `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md` and may be overridden per task. It is the QA each Worker records before reporting `merge_ready`.

`manager_qa_profile` is configured in `.ai/herdr-dev-loop/loops/<namespace>/PROFILE.md`. It is the separate final QA gate Manager records in `qa/FINAL.md` after the combined implementation is ready.

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

## 0.5.2 release and review evidence

Validation for a new 0.5.2 loop must be tied to the locked release scope and
one concrete integration target SHA. Task artifacts record `task_origin`, the
matching `release_scope_revision`, and the plan, requirement, finding, user
input, or operational references that authorize the task. A review candidate
without confirmed in-scope evidence is not sufficient authorization for a new
fix task.

At the closed-batch boundary, `hloop review readiness` must pass the release
scope snapshot, current validation, clean checkout, task/merge state, and
absence of blocking decisions. `hloop review convergence prepare|record`
validates fixed base/target SHAs, lane completion, verification coverage,
manifest completeness, and the recomputed count of verified actionable
findings. The bounded automatic remediation budget is at most two rounds for a
new loop. Findings are triaged across the independent axes
`fact_status`, `origin`, `contract_relation`, `decision_requirement`,
`severity`, `disposition`, and `release_effect`; an in-scope regression cannot
be made non-blocking merely by labeling it a follow-up.

Manual-final certification is a separate review evidence gate. Its
`reviews/final/PLAN.json`, `MANIFEST.json`, and non-empty `REPORT.md` must
share the prepared certification id, release-scope snapshot, base/target
identity, and target SHA. Recording recomputes all expected lanes, required
independent verifications, shortfalls, manifest completeness, report
presence, and verified actionable findings. This is a complete-zero
certification: `finish` accepts only a passed manual final with complete
evidence and zero verified actionable findings; a
zero count asserted by an agent or a skipped provider probe does not satisfy
this condition. Recovery from failed, incomplete, or exhausted review uses
atomic `hloop review reopen` with explicit user-input provenance.

The public schema entry points for these artifacts are
`schemas/final-review-plan.schema.json` and
`schemas/final-review-manifest.schema.json`. They resolve to the canonical
reference schemas and are checked by the 0.5.2 selftest. Provider E2E is
reported separately: `--allow-skip --skip-reason <reason>` proves that the
probe was intentionally not run, but it never turns into a provider pass.

## Command Selection

Pick commands from current repository evidence:

- `package.json` scripts for npm/pnpm/yarn projects
- `Makefile` targets
- `go.mod` and existing `go test` patterns
- `Cargo.toml` and existing cargo commands
- CI workflow files
- README or project docs

Record exact commands and results in Worker result artifacts and in `STATE.json.last_validation`. Integration validation must also capture stdout/stderr under `.ai/herdr-dev-loop/loops/<namespace>/validation/` and record the relative log path in `STATE.json.last_validation.results[].log`. `hloop validate` preserves the live console output but trims trailing whitespace from saved validation logs so `.ai/herdr-dev-loop/loops/<namespace>/validation/*.log` does not create `git diff --check` noise.

Record Worker QA evidence paths, preview URLs, staging URLs, screenshots, seeded data cleanup, or blockers in the Worker result artifact when they apply. Record Manager final QA evidence in `qa/FINAL.md` and the final report. Do not force browser QA for every repository; choose the product-appropriate QA surface from PROFILE and PLAN.

For a herdr-dev-loop release, unit tests and selftest are not substitutes for the scenario runners. Run `tests/run_synthetic_e2e.py --json` and preserve its structured scenario results. Run `tests/run_provider_e2e.py --provider <codex|claude> --json` only with an authenticated disposable session; the provider runner performs the live read-only probe by default. When credentials or a session are unavailable, use the runner's explicit `--allow-skip --skip-reason <reason>` path; a skipped result proves non-execution and does not count as a live provider pass.

In frontmatter, write non-empty command and result lists as multiline lists. `hloop` rejects non-empty inline lists for known list fields because comma-splitting command strings is unsafe. Empty lists such as `blocking_questions: []` remain allowed.

## Integration Failure

When integration validation fails:

1. stop dispatching and merging
2. record command output path or summary
3. set phase to `failed_validation`
4. create a fix task or ask the user if rollback/fix direction is not obvious

Do not continue merging more Worker branches into a broken integration branch.

When integration validation succeeds after a Worker integration, remove that Worker's local worktree and branch. For squash integrations, branch deletion normally requires force deletion because the branch commits are intentionally not ancestors of the integration branch.
