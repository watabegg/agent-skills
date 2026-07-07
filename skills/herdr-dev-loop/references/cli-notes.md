# CLI Notes

These notes describe the local command assumptions used by `scripts/hloop`. Re-check with `hloop doctor` because Herdr and Codex CLI can change.

`hloop doctor` treats `git`, `herdr`, `codex`, `$codex-impl`, and `$codex-review-multi-v2` as hard requirements. The `$herdr` skill file is useful context but the Herdr CLI is authoritative; a missing `$herdr` skill path is a warning unless `--strict-skills` is used.

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

`herdr agent start` is useful for named Worker/Gap Auditor/Reviewer agents. `hloop` supports a pane launcher and an agent launcher; use `--dry-run` before relying on a launcher in a new Herdr version.

After `hloop worker harvest`, `hloop gap harvest`, or `hloop reviewer harvest`, the helper closes the completed pane by default. Use `--keep-pane` only when the Manager needs to inspect the live transcript.

## Codex CLI

Required command shape:

```bash
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/loop/prompts/T001.worker.md)"
codex exec --sandbox workspace-write - < .ai/loop/prompts/T001.worker.md
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/loop/prompts/G001.gap.md)"
codex exec --sandbox workspace-write --output-last-message .ai/loop/gaps/G001.md - < .ai/loop/prompts/G001.gap.md
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/loop/prompts/R001.reviewer.md)"
codex exec --sandbox workspace-write --output-last-message .ai/loop/reviews/R001.md - < .ai/loop/prompts/R001.reviewer.md
```

The helper uses `--sandbox workspace-write` for Workers, Gap Auditors, and Reviewers. Worker default is TUI. Gap Auditor and Reviewer default are also TUI, but run in detached worktrees so the Manager can monitor progress and each agent can write only the final Markdown artifact. `hloop gap harvest` and `hloop reviewer harvest` copy the artifact back to the Manager repo and block if the detached worktree changed any other file.

Use this while a gap audit or review is running:

```bash
python3 <skill>/scripts/hloop gap watch G001 --lines 120
python3 <skill>/scripts/hloop reviewer watch R001 --lines 120
```

Use the helper to send additional Manager instructions into a TUI:

```bash
python3 <skill>/scripts/hloop worker message T001 --file .ai/loop/inbox/manager/T001-followup.md
python3 <skill>/scripts/hloop gap message G001 --file .ai/loop/inbox/manager/G001-followup.md
python3 <skill>/scripts/hloop reviewer message R001 --file .ai/loop/inbox/manager/R001-followup.md
```

Avoid direct `herdr pane run <pane> "<prompt>"` for Manager follow-ups. Empirical failure modes in Herdr 0.7.1 / Codex CLI 0.142.5:

- sending to a pane before Codex starts executes the prompt as a shell command
- sending while the Codex trust prompt is visible consumes Enter for trust and drops the prompt
- sending while Codex is working can mix the new instruction into the active turn
- sending `send-text` and `Enter` back-to-back can leave the prompt typed but not submitted
- `herdr wait output --match <marker>` can match the echoed prompt text before Codex has answered

`hloop ... message` checks that the pane is a Codex TUI, rejects trust prompts and working sessions, then uses `pane send-text`, waits until the prompt is visible, pauses before `pane send-keys Enter`, and verifies that Codex started working or answered. If verification fails because the prompt stayed typed, it retries Enter. Tune with `--input-settle-ms`, `--submit-verify-ms`, and `--submit-attempts` only after inspecting the pane. For long or multi-line instructions, prefer `--file` to avoid shell quoting issues.

Codex saved sessions can be archived after pane cleanup:

```bash
codex archive <session-id>
```

`hloop` reads the active Codex session id from `herdr pane get` when Herdr exposes `agent_session.value`. Default session cleanup is `archive`; use `--session-cleanup none` to keep sessions visible in `codex resume`, or `--session-cleanup delete` only when permanent deletion is intended.

## Cadence Options

Default init cadence keeps the loop active:

```bash
hloop init ... --max-workers 3 --max-reviewers 1 --max-gap-auditors 1 --review-after-merges 1 --gap-after-merges 3
```

`review_after_merges` and `gap_after_merges` count validated integration merges that have not yet been covered by a closed review or gap gate. Review is intentionally frequent; Gap Auditor is intentionally less frequent but still required before final completion when enabled.

Use `pump` for queue-drain behavior:

```bash
hloop pump --max-transitions 20 --max-workers 3 --stop-on-triage
```

Use `triage` to turn machine-readable `Fix Task Candidates` into a Manager-readable draft, and only then create queued fix tasks:

```bash
hloop triage review R001
hloop triage review R001 --create-tasks
hloop triage gap G001
hloop triage gap G001 --create-tasks
```

## Local Skill Dependencies

Required skills:

- `$codex-impl`: normally at `~/.codex/skills/codex-impl/SKILL.md`
- `$codex-review-multi-v2`: normally at `~/.codex/skills/codex-review-multi-v2/SKILL.md`
- `$herdr`: often at `~/.agents/skills/herdr/SKILL.md`, or set `HERDR_SKILL_PATH`

Do not copy private skill contents into public artifacts unless they are already intended for publication.
