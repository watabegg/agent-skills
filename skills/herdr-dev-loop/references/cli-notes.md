# CLI Notes

These notes describe the local command assumptions used by `scripts/hloop`. Re-check with `hloop doctor` because Herdr, Codex CLI, and Claude Code CLI can change.

`hloop doctor` treats `git`, `herdr`, and `codex` as hard requirements because Codex is the default fallback provider. It reports `claude` when available and role starts require Claude only when that role selects `--*-agent-provider claude`. `$codex-impl` remains an optional Worker compatibility skill. Fresh 0.5.3 defaults set ordinary `reviewer.protocol`, `review.pre_final_protocol`, and `review.manual_final_protocol` to `$codex-review-multi-v2` with the canonical six-lane Reviewer topology. `--review-protocol native` changes only ordinary review. The supported native pre-final path is selected separately with `pre_final_protocol = "native"` in `[defaults.review]` or a matching scope. Manual-final has no native override and accepts only `codex-review-multi-v2`. An execution that selects the external protocol requires its pinned `externally-planned-v1` companion capability. The shipped 0.5.3 record is release-ready and validates the vendored companion against an immutable hardened-fork commit, exact adapter version, and payload digest. The `$herdr` skill file is useful context but the Herdr CLI is authoritative; a missing `$herdr` skill path is a warning unless `--strict-skills` is used.

`hloop` is not assumed to be installed on `PATH`. Prefer an explicit shell variable in every Manager session:

```bash
HLOOP_SKILL_DIR="/absolute/path/to/herdr-dev-loop"
hloop() {
  python3 "$HLOOP_SKILL_DIR/scripts/hloop" --namespace <namespace> "$@"
}
hloop version
hloop doctor
```

If bare `hloop` fails with `command not found`, keep using the absolute helper path. Do not switch to hand-written Herdr/Codex/Claude orchestration for loop mutations.

Include the namespace in the command prefix:

```bash
HLOOP_SKILL_DIR="/absolute/path/to/herdr-dev-loop"
hloop() {
  python3 "$HLOOP_SKILL_DIR/scripts/hloop" --namespace feature-x "$@"
}
hloop namespaces
```

`namespaces` lists coexisting loops and reports `.ai/loop` only as `legacy ignored`. `agent abort` and `agent requeue` recover roles that exited without artifacts. `experience show` and `experience recommend` expose the repo-local worktree setup history below `.ai/herdr-dev-loop/experience/`.

Use `migrate --dry-run` and then `migrate --apply` for pre-3.3 state. herdr-dev-loop 0.5.3 writes format 3 revision 3 and rejects mutation from an unknown future revision. Use `migrate --resume` for an interrupted prepared transaction; `migrate --rollback` is accepted only before the first recorded 0.5.3 mutation. Use `pause --reason ...` / `resume` for an intentional loop stop. `pump` stops at `ready_to_finish`; only `finish` can transition the loop to `done` after rechecking every completion gate against the current integration SHA.

Use `config path`, `config validate`, `config explain`, `config init`, and `config apply` for hierarchical TOML settings. A missing config file uses built-in defaults. `init` snapshots the selected source and resolved values; `config apply --dry-run` previews changes, while `config apply --apply` updates an idle active loop's runtime-facing snapshot and `review_policy`. Changing the review policy invalidates the existing readiness, convergence, and manual-final evidence. New-loop review defaults are stored under `review_policy` and use batch cadence, a two-round fix cap, `follow_up` for scope expansion, and complete-zero manual final certification.

## 0.5.3 release-scope and review commands

These commands are explicit state transitions; inspect with `--json` and keep the namespace prefix on every invocation:

```bash
hloop release-scope lock --source MISSION.md --source PLAN.md \
  --plan-item-ref P001 --requirement-ref R001 --scope-ref release-scope-contract
hloop release-scope status --json
hloop release-scope amend --kind editorial --reason 'fix a source typo' --source PLAN.md

hloop dispatch freeze --reason 'awaiting review evidence' --user-input-id U0001
hloop dispatch status --json
hloop dispatch unfreeze --user-input-id U0002

hloop review readiness --json
hloop review epoch create --plan reviews/epochs/E001/PLAN.json \
  --protocol-capability "$(dirname "$HLOOP_SKILL_DIR")/codex-review-multi-v2/capabilities/externally-planned-v1.json"
hloop review epoch reserve E001 --lease-id L001 --execution-id R001 \
  --process-id reviewer-R001 --expires-at 2026-07-17T12:00:00+00:00
hloop review epoch record E001 --outcome reviews/epochs/E001/R001-outcome.json
hloop review epoch status E001 --json
hloop triage epoch E001 --record-candidates reviews/epochs/E001/candidates.json
hloop triage epoch E001 --approve-batch --approval-bundle approvals/E001.json
hloop triage epoch E001 --materialize-batch

hloop review convergence prepare --mode swarm --json
hloop review convergence record --fix-round 0 --json
hloop review reopen --action retry-certification --user-input-id U0003 --authorized-extra-rounds 1 --json

hloop final-review prepare --mode swarm --json
hloop final-review record --json
hloop final-review status --json

hloop follow-up add --title 'next release item' --component 'component' \
  --trigger-class 'trigger' --product-impact 'impact' --impact 'current impact' \
  --affected-path 'src/example.py' --source-review-fingerprint sha256:<64 hex> \
  --evidence reviews/R001.md --deferred-reason 'outside current scope' \
  --reconsider-condition 'next release scope lock'
hloop follow-up list --json
hloop follow-up show fu:v1:sha256:<64 hex> --json
hloop follow-up export --output docs/follow-ups.md
```

`review epoch create` fixes the Reviewer/Gap plan, target, protocol capability, topology, and capacity policy. Reserve capacity before starting each process, record every terminal outcome, and require a closed collection barrier before triage. Candidate registration, approval, and materialization are separate idempotent transitions; classification conflict or digest drift blocks approval. `review convergence prepare` freezes a fixed integration SHA but does not start a Reviewer. `record` rejects stale targets, plan drift, incomplete lanes, verification shortfall, and nonzero actionable findings at the round limit. `review reopen` is the only path from failed/incomplete/exhausted certification back to task creation and requires a user input id. `final-review record` recomputes complete-zero evidence; a count of zero alone is insufficient.

Mutating helper commands take `/tmp/herdr-dev-loop-<uid>/locks/<sha256>.lock` and write files atomically. The digest is derived from the canonical Git common directory and namespace. The fixed `/tmp` root does not follow `HLOOP_RUNTIME_DIR`, `XDG_RUNTIME_DIR`, or `TMPDIR`; its UID directory is secured to mode `0700`, lock files are mode `0600` and opened without following symlinks where the platform supports `O_NOFOLLOW`, and all lock state remains outside Git metadata. This protects the state from accidental concurrent invocations, but Manager should still run mutating helper commands serially so the journal and reasoning remain easy to audit.

## Herdr

Required commands:

- `herdr pane current --current`
- `herdr pane list`
- `herdr pane get <pane-id>`
- `herdr pane split <pane-id> --direction right|down --cwd <path> --no-focus`
- `herdr pane run <pane-id> <command>`
- `herdr pane send-text <pane-id> <text>`
- `herdr pane send-keys <pane-id> Enter`
- `herdr pane read <pane-id> --source recent-unwrapped --lines <n>`
- `herdr pane close <pane-id>`
- `herdr agent list`
- `herdr agent start <name> --cwd <path> --workspace <workspace-id> --split right|down --no-focus -- <argv...>`
- `herdr agent read <target> --source recent-unwrapped --lines <n>`
- `herdr wait output <pane-id> --match <text> --timeout <ms>`
- `herdr wait agent-status <pane-id> --status done --timeout <ms>`

Herdr pane ids are not durable and may not use the old `1-3` shape. Parse ids from JSON output, usually `result.pane.pane_id`.

When available, prefer environment-provided `HERDR_PANE_ID`, `HERDR_WORKSPACE_ID`, and `HERDR_TAB_ID` for the current Manager context. Some Herdr `--current` subcommands can refer to the UI-focused pane, so pass explicit ids after preflight.

`herdr agent start` is useful for named Worker/Gap Auditor/Reviewer/Advisor agents. `hloop` supports a pane launcher and an agent launcher; use `--dry-run` before relying on a launcher in a new Herdr version.

After `hloop worker harvest`, `hloop gap harvest`, `hloop reviewer harvest`, or `hloop advisor harvest`, the helper closes the completed pane by default. Use `--keep-pane` only when the Manager needs to inspect the live transcript.

## Codex CLI

Required command shape:

```bash
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/herdr-dev-loop/loops/<namespace>/prompts/T001.worker.md)"
codex exec --sandbox workspace-write - < .ai/herdr-dev-loop/loops/<namespace>/prompts/T001.worker.md
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/herdr-dev-loop/loops/<namespace>/prompts/G001.gap.md)"
codex exec --sandbox workspace-write --output-last-message .ai/herdr-dev-loop/loops/<namespace>/gaps/G001.md - < .ai/herdr-dev-loop/loops/<namespace>/prompts/G001.gap.md
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/herdr-dev-loop/loops/<namespace>/prompts/R001.reviewer.md)"
codex exec --sandbox workspace-write --output-last-message .ai/herdr-dev-loop/loops/<namespace>/reviews/R001.md - < .ai/herdr-dev-loop/loops/<namespace>/prompts/R001.reviewer.md
```

The helper uses `--sandbox workspace-write` for Workers, Gap Auditors, and Reviewers, passes the repository Git common directory through `--add-dir`, and maps role effort to `model_reasoning_effort`. Worker default is TUI. Gap Auditor and Reviewer default are also TUI, but run in detached worktrees so the Manager can monitor progress and each agent can write only the final Markdown artifact. `hloop gap harvest` and `hloop reviewer harvest` copy the artifact back to the Manager repo and block if the detached worktree changed any other file.

`init --worktree-root <path>` stores one worktree root in `STATE.json`. Relative paths resolve from the Manager repository. Worker paths use `<root>/Txxx`; the other roles use their IDs below the same root. Without this option, the legacy sibling naming remains in effect.

`hloop version` reports the runtime skill version and, when a loop exists, the version pinned in `STATE.json` plus its `run_id`. Manager should print this at the start of every skill-using session. `doctor` also warns when the installed runtime differs from the loop version.

`wait`, `tick`, and `pump` treat a Worker result as ready only when its frontmatter is current and the exact file is committed at Worker HEAD. Role artifacts must match the version stored when that role started; Reviewer, Gap Auditor, and Advisor readiness also requires matching `run_id` and audited `head_sha`.

## Claude Code CLI

Required command shape when a role selects `--*-agent-provider claude`:

```bash
claude --permission-mode auto --ax-screen-reader "$(cat .ai/herdr-dev-loop/loops/<namespace>/prompts/T001.worker.md)"
claude --print --permission-mode auto --model opus --effort high < .ai/herdr-dev-loop/loops/<namespace>/prompts/R001.reviewer.md > .ai/herdr-dev-loop/loops/<namespace>/reviews/R001.md
claude --print --permission-mode auto --model opus < .ai/herdr-dev-loop/loops/<namespace>/prompts/A001-P1.advisor.md > .ai/herdr-dev-loop/loops/<namespace>/advice/A001-P1.md
```

Claude does not use Codex's `--sandbox` flag. hloop still isolates Reviewer, Gap Auditor, and Advisor writes through detached worktrees and harvest-time write-scope validation. Claude session archive/delete is not attempted; hloop records provider session cleanup as skipped when the provider does not support Codex-style session cleanup.

Validation has no implicit `git diff --check` fallback. Configure at least one real command with `init --validation-command ...` or `validation configure --command ...`. A merge conflict remains an explicit transaction: use `merge <task-id> --abort`, `--retry`, or, after resolving and staging only allowed files, `--continue`.

## Harvest And Wait

Use `hloop harvest <id>` when the id prefix is already known. It delegates `T...` to Worker harvest, `R...` to Reviewer harvest, `G...` to Gap Auditor harvest, and `A.../P...` to Advisor participant harvest.

Use this while a gap audit or review is running:

```bash
python3 <skill>/scripts/hloop worker watch T001 --lines 120
python3 <skill>/scripts/hloop gap watch G001 --lines 120
python3 <skill>/scripts/hloop reviewer watch R001 --lines 120
python3 <skill>/scripts/hloop advisor watch A001 --participant-id P1 --lines 120
```

Use this instead of hand-written polling when Manager is waiting for the next artifact:

```bash
python3 <skill>/scripts/hloop wait next --harvest --timeout-ms 300000
python3 <skill>/scripts/hloop wait T001 --harvest --timeout-ms 300000
```

`wait` checks Worker result artifacts, Reviewer artifacts, Gap Auditor artifacts, and Advisor participant artifacts. It returns when an artifact is present and non-empty, or returns non-zero on timeout with the last known agent and pane status. It sleeps without holding the loop lock; `--harvest` locks only for the harvest step.

Use the helper to send additional Manager instructions into a TUI:

```bash
python3 <skill>/scripts/hloop worker message T001 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/T001-followup.md
python3 <skill>/scripts/hloop gap message G001 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/G001-followup.md
python3 <skill>/scripts/hloop reviewer message R001 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/R001-followup.md
python3 <skill>/scripts/hloop advisor message A001 --participant-id P1 --file .ai/herdr-dev-loop/loops/<namespace>/inbox/manager/A001-P1-followup.md
```

Avoid direct `herdr pane run <pane> "<prompt>"` for Manager follow-ups. Empirical failure modes in Herdr 0.7.1 / Codex CLI 0.142.5:

- sending to a pane before Codex starts executes the prompt as a shell command
- sending while the Codex trust prompt is visible consumes Enter for trust and drops the prompt
- sending while Codex is working can mix the new instruction into the active turn
- sending `send-text` and `Enter` back-to-back can leave the prompt typed but not submitted
- `herdr wait output --match <marker>` can match the echoed prompt text before Codex has answered

`hloop ... message` checks that the pane is a role-agent TUI, rejects trust prompts and working sessions, then sends each body byte exactly once. For Codex, it sends a compact start marker, the body, and a compact end marker as three ordered chunks. Before the first chunk, between chunks, and at every control-key boundary it reads pane metadata both before and after pane text, requires the same non-empty provider and session to remain idle, and verifies the expected staged marker. It sends `Ctrl+E` after the end chunk so a long multiline input exposes its tail without changing or submitting the text. The helper then waits two seconds by default before Enter and sends at most two delayed Enter attempts. Initial delivery may use provider output or the exact compact ACK as submit evidence; later `message submit` recovery requires that exact message ACK because global pane working state or arbitrary output after an old marker cannot be safely correlated. UI-looking words inside the Manager payload, a changed pane snapshot, or a newline alone are not success. Claude and legacy marker-less messages retain the single-send path. Tune `--input-settle-ms`, `--submit-verify-ms`, and `--submit-attempts` only after inspecting the pane. For long or multi-line instructions, prefer `--file` to avoid shell quoting issues.

If no body was typed, the command records an `undelivered` message under `.ai/herdr-dev-loop/loops/<namespace>/inbox/pending/`; only these messages are eligible for `hloop message drain`, and only while the addressed role is still running. A terminal role is never a drain target: `hloop message list` emits an exact `message resolve ... --status superseded` command when its durable identity is current, or requires state inspection when it is not. If a drain retry becomes delivery-unknown, drain exits nonzero and reports the transition. If the body may already be typed, delivery is `unknown` and the body is never auto-resent. When the transport evidence says the end marker was staged, the diagnostic pins the absolute runtime, canonical Manager repo, namespace, role, and message id and offers Enter-only recovery:

```bash
hloop message submit T001 <message-id>
```

If the end marker was not staged, the diagnostic does not offer `message submit`; inspect the pane and explicitly choose `acknowledged`, `applied --result ...`, or `superseded` with `message resolve` only after establishing the actual delivery or application result. No executable resolution command is preselected before that observation. If the staged input cannot be distinguished from transcript history, `message submit` also fails closed without sending Enter. A failed Enter transport attempt remains `unknown`, records the attempt, and requires another pane inspection before retry.

After a Manager restart or lost diagnostic, run `hloop message list --status unknown` to enumerate unresolved records across all role types; this filter also includes malformed message records, pending markers, and pending containers so they cannot disappear from the recovery gate. The list is read-only and emits a canonical repository/namespace-pinned `message submit` command only when the durable identity is complete. Malformed or incomplete records remain blocking, inspection-only, and never authorize a body resend or a preselected resolution outcome.

Codex saved sessions can be archived after pane cleanup:

```bash
codex archive <session-id>
```

`hloop` reads the active Codex session id from `herdr pane get` when Herdr exposes `agent_session.value`. Default session cleanup is `archive`; use `--session-cleanup none` to keep sessions visible in `codex resume`, or `--session-cleanup delete` only when permanent deletion is intended.

## Cadence Options

New 0.5.3 loops use planning evidence, batch review cadence, task-local Patch Review, review epochs, and explicit fixed-target final certification:

```bash
hloop selftest
hloop init ... --branch-strategy integration --worker-protocol native --review-protocol native --worker-qa-profile repo-default --manager-qa-profile none --max-workers 3 --max-reviewers 1 --max-gap-auditors 1 --review-after-merges 1 --gap-after-merges 3
```

`--review-after-merges`と`--gap-after-merges`はstateへ保存されるlegacy/merge-count knobsであり、新規loopの`review_policy.cadence = "batch"`ではbatch closeと明示的な`review convergence`が優先されます。manual finalは`final-review prepare`と`final-review record`をManagerが実行し、complete-zero evidenceがなければfinishできません。

この例の`--review-protocol native`はordinary reviewだけに適用されます。pre-finalのnative pathは`[defaults.review]`の`pre_final_protocol = "native"`で別途選択し、manual-finalはnative overrideをサポートしません。

Run `hloop selftest` after updating or installing the skill. It does not require `HERDR_ENV=1`; it checks skill-local frontmatter, agent metadata, JSON schemas, sample artifact parsing, and required field drift between `artifact-contract.md` and `state.schema.json`.

Use `dashboard` for the Manager's live status view:

```bash
hloop status
hloop status --json
hloop status --raw-state
hloop dashboard
hloop dashboard --json
```

`status --json` emits a loop inventory object with `loop`, `counts`, `workers`, `reviewers`, `gaps`, `issues`, and `next_actions`. Use `--raw-state` only when a consumer needs the literal `.ai/herdr-dev-loop/loops/<namespace>/STATE.json` document.

Use `conductor` when a loop feels stuck or when resuming a long-running Herdr workspace:

```bash
hloop conductor
hloop conductor --no-fail
hloop conductor --json
hloop doctor --sessions --json
```

`conductor` is read-only. It inspects `STATE.json`, git branch/dirty state, known worktrees, artifacts, and Herdr pane status when `HERDR_ENV=1`. It returns non-zero when P0/P1 attention items exist unless `--no-fail` is passed. Typical findings are missing panes for running agents, Codex trust prompts, idle panes without artifacts, ready artifacts that should be harvested, review/gap artifacts that need triage, branch mismatches, non-loop dirty files, unsafe sandbox values, dangerous Codex launch markers, non-hloop prompt paths, unharvested artifact states, untrusted Worker head markers, Manager-owned Worker result paths, and manual integration traces.

Treat P0 conductor findings as unsafe to continue. In particular, `danger-full-access` or `dangerously-bypass-approvals-and-sandbox` in STATE or pane output means the agent must be stopped and recreated through `hloop` with `workspace-write` sandbox. Treat P1 trust findings as blockers for the affected task/gate until Manager has rerun, harvested, or documented the residual risk.

`review_after_merges` and `gap_after_merges` count validated integration merges that have not yet been covered by a closed review or gap gate. Review is intentionally frequent; Gap Auditor is intentionally less frequent but still required before final completion when enabled.

Structured role reports use `agent report --invocation-id <opaque-key>`; callers create one shell-safe key per logical report and reuse it for uncertain retries before sending a newer report. The key starts with an ASCII alphanumeric character and then contains only ASCII alphanumerics or `.`, `_`, `:`, `/`, `-`. `--invocation-id` is mutually exclusive with compatibility `--event-id`, and its guarantee ends when the bounded 64-entry role outbox evicts the original envelope. Manager reads reports through `inbox list` and `manager next`, registers a wake lease with `manager sleep`, and consumes a handled event with `inbox ack`. `broker recover` replays the local outage spool. These broker, inbox, input, and spool paths remain local-only and are never checkpointed.

Use `pump` for queue-drain behavior:

```bash
hloop pump --max-transitions 20 --max-workers 3 --stop-on-triage
```

Use `hloop checkpoint --message "ai-loop: ..."` to commit Manager-owned `.ai/herdr-dev-loop/loops/<namespace>` changes without accidentally staging product files. By default it excludes `.ai/herdr-dev-loop/loops/<namespace>/prompts/` and `.ai/herdr-dev-loop/loops/<namespace>/LOCK`; pass `--include-prompts` or `--include-lock` only for deliberate debugging artifacts. Pass `--force` when `.ai/herdr-dev-loop/loops/<namespace>` is intentionally gitignored; it discovers ignored untracked loop files and stages only the filtered loop path set with `git add -f`.

Use the same explicit `hloop()` function when bare `hloop` is not on `PATH`.

Use `triage` to turn machine-readable `Fix Task Candidates` into a Manager-readable draft, and only then create queued fix tasks:

```bash
hloop triage review R001
hloop triage review R001 --create-tasks
hloop triage gap G001
hloop triage gap G001 --create-tasks
```

## Local Skill Dependencies

Required commands:

- `git`
- `herdr`
- `codex`

Optional compatibility skills:

- `$codex-impl`: normally at `~/.codex/skills/codex-impl/SKILL.md`
- `$codex-review-multi-v2`: normally at `~/.codex/skills/codex-review-multi-v2/SKILL.md`
- `$herdr`: often at `~/.agents/skills/herdr/SKILL.md`, or set `HERDR_SKILL_PATH`; only strict when `hloop doctor --strict-skills` is used

Do not copy private skill contents into public artifacts unless they are already intended for publication.
