# CLI Notes

These notes describe the local command assumptions used by `scripts/hloop`. Re-check with `hloop doctor` because Herdr and Codex CLI can change.

## Herdr

Required commands:

- `herdr pane current --current`
- `herdr pane list`
- `herdr pane get <pane-id>`
- `herdr pane split <pane-id> --direction right|down --cwd <path> --no-focus`
- `herdr pane run <pane-id> <command>`
- `herdr pane read <pane-id> --source recent-unwrapped --lines <n>`
- `herdr pane close <pane-id>`
- `herdr agent list`
- `herdr agent start <name> --cwd <path> --workspace <workspace-id> --split right|down --no-focus -- <argv...>`
- `herdr agent read <target> --source recent-unwrapped --lines <n>`
- `herdr wait output <pane-id> --match <text> --timeout <ms>`
- `herdr wait agent-status <pane-id> --status done --timeout <ms>`

Herdr pane ids are not durable and may not use the old `1-3` shape. Parse ids from JSON output, usually `result.pane.pane_id`.

When available, prefer environment-provided `HERDR_PANE_ID`, `HERDR_WORKSPACE_ID`, and `HERDR_TAB_ID` for the current Manager context. Some Herdr `--current` subcommands can refer to the UI-focused pane, so pass explicit ids after preflight.

`herdr agent start` is useful for named Worker/Reviewer agents. `hloop` supports a pane launcher and an agent launcher; use `--dry-run` before relying on a launcher in a new Herdr version.

After `hloop worker harvest` or `hloop reviewer harvest`, the helper closes the completed pane by default. Use `--keep-pane` only when the Manager needs to inspect the live transcript.

## Codex CLI

Required command shape:

```bash
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/loop/prompts/T001.worker.md)"
codex exec --sandbox workspace-write - < .ai/loop/prompts/T001.worker.md
codex --sandbox workspace-write --ask-for-approval never --no-alt-screen "$(cat .ai/loop/prompts/R001.reviewer.md)"
codex exec --sandbox workspace-write --output-last-message .ai/loop/reviews/R001.md - < .ai/loop/prompts/R001.reviewer.md
```

The helper uses `--sandbox workspace-write` for Workers and Reviewers. Worker default is TUI. Reviewer default is also TUI, but runs in a detached review worktree so the Manager can monitor progress and the Reviewer can write only the final review artifact. `hloop reviewer harvest` copies the artifact back to the Manager repo and blocks if the review worktree changed any other file.

Use this while a review is running:

```bash
python3 <skill>/scripts/hloop reviewer watch R001 --lines 120
```

Codex saved sessions can be archived after pane cleanup:

```bash
codex archive <session-id>
```

`hloop` reads the active Codex session id from `herdr pane get` when Herdr exposes `agent_session.value`. Default session cleanup is `archive`; use `--session-cleanup none` to keep sessions visible in `codex resume`, or `--session-cleanup delete` only when permanent deletion is intended.

## Local Skill Dependencies

Required skills:

- `$codex-impl`: normally at `~/.codex/skills/codex-impl/SKILL.md`
- `$codex-review-multi-v2`: normally at `~/.codex/skills/codex-review-multi-v2/SKILL.md`
- `$herdr`: often at `~/.agents/skills/herdr/SKILL.md`

Do not copy private skill contents into public artifacts unless they are already intended for publication.
