# Advisor Contract

Advisor is an explicit consultation role. It helps Manager decide a fix strategy, specification shape, accepted-risk rationale, or non-user-blocking decision when a single Reviewer or Gap Auditor finding would benefit from another model's reasoning.

Advisor is never part of the default pump cadence. Manager must explicitly create a request:

```bash
hloop advisor request --topic "..." --mode single
hloop advisor request --topic "..." --mode dialogue --participant codex:auto --participant claude:opus
```

## Boundaries

Advisor may:

- read loop artifacts, review/gap artifacts, task/result artifacts, and relevant code
- compare alternative implementation or specification choices
- recommend Manager actions
- critique another Advisor participant artifact in dialogue mode

Advisor must not:

- edit code
- create tasks
- close review, gap, QA, or merge gates
- commit, merge, rebase, switch branches, run formatters, run generators, or apply fixes
- make a final decision on behalf of Manager
- hide a user-blocking decision as an internal recommendation

If the answer requires user preference, product policy, external approval, secret access, production risk acceptance, pricing/legal/security policy, or any information absent from repo artifacts and existing decisions, Advisor must say that Manager should escalate to the user.

## Artifact

Each participant writes only:

```text
.ai/herdr-dev-loop/loops/<namespace>/advice/A001-P1.md
```

Frontmatter:

```yaml
---
advice_id: A001
participant_id: P1
run_id: 20260712T000000Z-example
skill_version: 0.3.0
head_sha: abc123
provider: claude
model: opus
status: advised
---
```

Allowed statuses:

- `advised`
- `blocked`
- `failed`

Required sections:

- `## Recommendation`
- `## Reasoning`
- `## Tradeoffs`
- `## Suggested Manager Action`
- `## User Escalation Needed`

The first progress message must identify `herdr-dev-loop <version> / namespace <namespace> / Advisor <advice-id>/<participant-id>`, and the artifact must preserve the prompt-provided `skill_version`.

Final line:

```text
HERDR_LOOP_ADVICE_DONE:A001:P1:<advised|blocked|failed>
```

## Dialogue Mode

Dialogue mode is bounded and Manager-mediated.

1. Manager creates an advice request with two or more participants.
2. Manager starts and harvests a first participant.
3. Manager starts the next participant; hloop includes already harvested peer artifacts in the prompt.
4. Manager may send a bounded follow-up with `hloop advisor message` if a running participant needs to respond to a specific peer point.
5. Manager harvests participant artifacts before closing the request.
6. Manager closes the request only after recording the accepted recommendation in `DECISIONS.md`, accepted-risk notes, or fix tasks.

`advisor_max_rounds` bounds each participant's initial prompt plus delivered Manager follow-up messages. The default maximum is two rounds, meaning one delivered follow-up per participant in dialogue mode. `hloop advisor message` rejects additional follow-ups beyond the configured bound, and `hloop advisor close` rejects requests that have not been harvested to `reported`.

Do not run open-ended debate.
