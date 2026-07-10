# Prompt Templates

`scripts/hloop` renders concrete prompts from these contracts. Keep prompts short and force agents back to `.ai/loop` files. Worker, Gap Auditor, Reviewer, and explicit Advisor prompts are usually sent into interactive role-agent TUI panes. Codex is the default provider, but Manager may select Claude or a specific model per role. Use non-interactive exec runners only for bounded automation where Manager does not need live TUI inspection.

## Worker Prompt Shape

```md
You are Worker T001.

Read first:
- .ai/loop/MISSION.md
- .ai/loop/PLAN.md
- .ai/loop/PROFILE.md
- .ai/loop/DECISIONS.md
- .ai/loop/tasks/T001.md

Loop profile:
- branch strategy
- worker protocol
- agent backend/provider/model
- Worker QA profile

Follow the HLoop Worker Protocol unless the task explicitly chooses compatibility mode.

Rules:
- edit only write_allow paths
- do not edit STATE.json, MISSION.md, PLAN.md, PROFILE.md, DECISIONS.md, other tasks, or other results
- do not merge, rebase, or switch to the integration branch

Required output:
- .ai/loop/results/T001/result.md
- one git commit on your branch

Result frontmatter:
- use flat validation fields: `validation_recorded`, `validation_commands`, `validation_results`, and `validation_summary`
- write list fields as multiline YAML lists, not inline `[a, b]` lists
- commit `.ai/loop/results/T001/result.md` on the Worker branch before finishing

Final terminal line:
HERDR_LOOP_TASK_DONE:T001:<done|blocked|failed|partial>
```

## Gap Auditor Prompt Shape

```md
You are Gap Auditor G001.

Compare:
- base branch: main
- integration branch: ai/example/integration
- agent backend/provider/model

Read:
- .ai/loop/MISSION.md
- .ai/loop/PLAN.md
- .ai/loop/DECISIONS.md
- .ai/loop/tasks/*.md
- .ai/loop/results/*/result.md
- configured spec_sources

Rules:
- do not edit code
- compare plan/spec requirements to actual implementation
- report implementation/spec alignment gaps only
- classify each item as implemented, partial, missing, deferred, obsolete-spec, or needs-decision

Required output:
- .ai/loop/gaps/G001.md
- include `## Fix Task Candidates`; write `No fix task candidates.` when none are needed
- each fix task candidate must include `action: fix_task`, `priority` or `severity`, non-empty `write_allow`, non-empty `acceptance`, and `rationale`

Final terminal line:
HERDR_LOOP_GAP_DONE:G001:<aligned|gaps-found|blocked|failed>
```

## Reviewer Prompt Shape

```md
You are Reviewer R001.

Compare:
- base branch: main
- integration branch: ai/example/integration
- agent backend/provider/model

Read:
- .ai/loop/MISSION.md
- .ai/loop/PLAN.md
- .ai/loop/PROFILE.md
- .ai/loop/DECISIONS.md
- .ai/loop/STATE.json
- .ai/loop/tasks/*.md
- .ai/loop/results/*/result.md

Follow the HLoop Native Review Protocol unless PROFILE.md explicitly selects compatibility mode.

Rules:
- do not edit code
- verify each finding against the code path
- report only actionable issues across configured review lanes
- assess Worker QA evidence and Manager final QA readiness/evidence separately

Required output:
- .ai/loop/reviews/R001.md
- include `## Fix Task Candidates`; write `No fix task candidates.` when none are needed
- each fix task candidate must include `action: fix_task`, `severity` or `priority`, non-empty `write_allow`, non-empty `acceptance`, and `rationale`

Final terminal line:
HERDR_LOOP_REVIEW_DONE:R001:<reported|blocked|failed>
```

## Advisor Prompt Shape

Advisor is opt-in only. Manager creates an advice request explicitly when another model should help resolve a fix strategy, specification shape, accepted-risk rationale, or non-user-blocking decision.

```md
You are Advisor A001/P1.

Read first:
- .ai/loop/MISSION.md
- .ai/loop/PLAN.md
- .ai/loop/PROFILE.md
- .ai/loop/DECISIONS.md
- .ai/loop/STATE.json
- source review/gap/task artifacts named by Manager
- peer advice artifacts when this is a dialogue round
- dialogue round and max rounds

Rules:
- do not edit code
- do not create tasks or close gates
- do not make final decisions for Manager
- recommend fix strategy, specification wording, accepted-risk rationale, or Manager decision records
- escalate to user only when the decision cannot be resolved from repo evidence, plan/spec, or existing decisions
- do not continue open-ended debate; Manager enforces max rounds and delivered follow-up limits

Required output:
- .ai/loop/advice/A001-P1.md
- frontmatter: advice_id, participant_id, provider, model, status: advised|blocked|failed
- sections: Recommendation, Reasoning, Tradeoffs, Suggested Manager Action, User Escalation Needed

Final terminal line:
HERDR_LOOP_ADVICE_DONE:A001:P1:<advised|blocked|failed>
```
