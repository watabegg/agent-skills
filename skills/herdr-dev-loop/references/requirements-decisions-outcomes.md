# Requirements, Decisions, And Outcomes

herdr-dev-loop 0.5.1 separates user intent, specification decisions, task execution, and terminal evidence. A task can finish while its requirement remains unverified, and one unresolved decision can block only the dependent tasks while unrelated work continues.

## User inputs and requirements

Record a new instruction before changing the task graph:

```bash
$HLOOP input record --source manager-chat --text '<user instruction>'
$HLOOP requirement new \
  --source-input U0001 \
  --acceptance '<observable acceptance criterion>' \
  --priority P1
```

The raw input is redacted and stored as a local-only record. `STATE.json` retains a digest pointer, not the prompt text. Accepted requirements use stable `REQ-NNN` IDs and cannot be silently overwritten.

Requirement progress follows this explicit sequence: `not_started`, `in_progress`, `implemented_unverified`, then `verified`. `blocked`, `deferred`, and `superseded` are explicit alternatives. A transition to `verified` requires Manager or HLoop evidence on one head SHA, including an artifact and a passing test or QA record. An agent's completion report alone is not sufficient.

```bash
$HLOOP progress record \
  --requirement-id REQ-001 \
  --status implemented_unverified \
  --task-id T001 \
  --remaining-work '統合validationを実行する'

$HLOOP outcome show --requirement-id REQ-001
```

## Scoped decisions

Use `decision new` when the original mission, plan, and accepted requirements do not determine a safe choice. `advisory` records a non-blocking choice. `deferred-user` records a choice that may wait. `blocking-user` requires an explicit affected-task list and blocks only those tasks and their unmerged dependants.

```bash
$HLOOP decision new \
  --title '公開APIの互換性を維持するか' \
  --class blocking-user \
  --affects T004 \
  --option '互換性を維持する' \
  --option '破壊的変更を採用する' \
  --recommend-option opt_1 \
  --recommend-rationale '既存利用者への移行期間が必要なため'
```

`decision respond` stores the answer; it does not mutate implementation state. The Manager validates the answer and records the terminal `accepted`, `rejected`, or `superseded` outcome with `decision resolve`. A second conflicting response or resolution is rejected.

The scheduler enters a loop-wide user-decision block only after every remaining queued task is affected and no running role, merge-ready result, validation, review, or gap work can progress. Until then, the decision is a dependency-scoped stop.

## Outcome projection and terminal reports

`outcome show` projects one requirement and its current evidence without changing phase. `hloop report` writes a draft `reports/FINAL.md` from the current state. Only `hloop finish` may mark the loop done, after it rechecks merged tasks, current-head validation, closed review and gap gates, Manager QA, cleanup, and an armed final gate.

The terminal report identifies the integration target, requirement status, validation and QA, review findings, accepted decisions and risks, cleanup status, and remaining user action. A blocked external goal must preserve the concrete blocker instead of presenting an incomplete draft as a final result.
