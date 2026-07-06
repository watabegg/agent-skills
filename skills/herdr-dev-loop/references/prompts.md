# Prompt Templates

`scripts/hloop` renders concrete prompts from these contracts. Keep prompts short and force agents back to `.ai/loop` files. Worker prompts are usually sent into interactive Codex TUI panes; Reviewer prompts are usually sent through non-interactive `codex exec`.

## Worker Prompt Shape

```md
You are Worker T001.

Read first:
- .ai/loop/MISSION.md
- .ai/loop/PLAN.md
- .ai/loop/tasks/T001.md

Use $codex-impl.

Manager has fixed kickoff choices:
- implementation gap-check count: 1
- review/fix loop limit: skip review unless the task requires it
- forced QA environment: local

Rules:
- edit only write_allow paths
- do not edit STATE.json, MISSION.md, PLAN.md, other tasks, or other results
- do not merge, rebase, or switch to the integration branch

Required output:
- .ai/loop/results/T001/result.md
- one git commit on your branch

Final terminal line:
HERDR_LOOP_TASK_DONE:T001:<done|blocked|failed|partial>
```

## Reviewer Prompt Shape

```md
You are Reviewer R001.

Compare:
- base branch: main
- integration branch: ai/example/integration

Read:
- .ai/loop/MISSION.md
- .ai/loop/PLAN.md
- .ai/loop/DECISIONS.md

Use $codex-review-multi-v2.

Rules:
- do not edit code
- verify each finding against the code path
- report only actionable issues

Required output:
- .ai/loop/reviews/R001.md

Final terminal line:
HERDR_LOOP_REVIEW_DONE:R001:<reported|blocked|failed>
```
