# Gap Auditor Contract

Gap Auditor compares the original plan/spec contract against the current integration branch implementation.

Default runner: interactive role-agent TUI in a detached gap worktree. Codex is the default provider, but Manager may select Claude or a specific model in `PROFILE.md` or `hloop gap start`. Use this so Manager can monitor progress and the Gap Auditor can reliably write the final Markdown artifact.

Gap Auditor TUI uses `workspace-write` sandbox because the final report must be written. Treat the codebase as read-only during investigation. The only permitted write is `.ai/herdr-dev-loop/loops/<namespace>/gaps/<gap-id>.md` after the audit is complete. `hloop gap harvest` validates the gap worktree and blocks if any other file changed.

Assume Gap Auditor runs are long-running. Manager should inspect progress with `hloop gap watch <gap-id>`, wait with `hloop tick --gap-wait-ms <ms>`, or continue other safe Manager work while the auditor is running. The auditor is not complete until `gaps/<gap-id>.md` exists and is non-empty.

The Gap Auditor prompt must say:

- make the first progress message identify `herdr-dev-loop <version> / namespace <namespace> / Gap Auditor <gap-id>`
- compare plan/spec sources against the current integration branch implementation
- read `MISSION.md`, `PLAN.md`, `DECISIONS.md`, `tasks/*.md`, and `results/*/result.md`
- read Manager-provided `spec_sources` when configured
- do not edit code
- do not commit, merge, rebase, switch branches, run formatters, or run automatic fixes
- verify each reported gap against code, generated artifacts, tests, or missing files
- report plan/spec alignment gaps only; do not duplicate general code review
- classify each requirement as `implemented`, `partial`, `missing`, `deferred`, `obsolete-spec`, or `needs-decision`
- include `## Fix Task Candidates` with machine-readable task candidate blocks for missing or partial requirements that should become Worker fix tasks; each candidate needs `action: fix_task`, `priority` or `severity`, non-empty `write_allow`, non-empty `acceptance`, and `rationale`
- after audit is complete, write only `gaps/<gap-id>.md`
- include the prompt-provided `skill_version` in artifact frontmatter
- print `HERDR_LOOP_GAP_DONE:<gap-id>:<aligned|gaps-found|blocked|failed>`
- submit semantic ACK before investigation, attention when the specification boundary cannot be audited safely, and completion only after the gap artifact is ready

## Gap Scope

Default scope is plan/spec coverage, not diff review:

```text
spec_sources + .ai/herdr-dev-loop/loops/<namespace>/MISSION.md + .ai/herdr-dev-loop/loops/<namespace>/PLAN.md -> integration branch behavior
```

Use `git diff <base-branch>...<integration-branch>` only as supporting evidence for when the integration branch claims to implement a requirement. Do not treat every diff risk as a gap unless it contradicts or omits the plan/spec.

If no extra `spec_sources` are configured, audit the durable loop files and task/result artifacts as the contract. Do not guess hidden product requirements.

For a revision-3 review epoch, Gap uses one Coordinator, four requirement-audit lanes, and one coverage challenge. Each lane maps accepted requirements to implementation evidence and must not turn into a generic code-review lane. The epoch plan fixes target SHA, lane identity, provider/model/effort, audit-process identity, and capacity reservation. Manager collects the Gap execution with the required Reviewer execution before approving remediation; a missing lane, missing challenge, stale plan revision, or incomplete execution stops the collection barrier.

## Manager Action Labels

Each gap should recommend one action:

- `fix_task`
- `decision_needed`
- `accepted_risk_candidate`
- `stale_spec_update`
- `no_action`

Manager, not Gap Auditor, makes the final triage decision.

After reading the artifact, Manager closes the gap gate explicitly:

```bash
python3 <this-skill>/scripts/hloop gap close G001 --verdict aligned --reason "No mission-blocking plan/spec gaps"
```

Use `fix-tasks-created` when missing or partial requirements were converted into new Worker tasks, `decision-needed` when the gap requires user judgment, `stale-spec-updated` when the spec was corrected, and `accepted-risk` only when the risk is recorded with a reason.

Manager can generate a draft from the artifact:

```bash
python3 <this-skill>/scripts/hloop triage gap G001
```

Add `--create-tasks` only after Manager approves the generated draft.

After harvesting the gap artifact, Manager should close the Gap Auditor Herdr pane and clean up provider session state when supported. Keep the pane only when Manager needs to inspect the live transcript.
