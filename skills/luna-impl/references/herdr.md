# Herdr pane adapter for Luna workers

Load and follow `$herdr` before controlling panes.

- Verify `HERDR_ENV=1` before any Herdr control command.
- Discover the installed Herdr and Codex CLI syntax instead of assuming flags.
- Start a Codex agent configured for model `gpt-5.6-luna` and reasoning effort `max`; verify the actual launch configuration or agent metadata before reporting that Luna/max was used.
- Prompt through the Herdr agent surface and use the task contract from `task-contract.md`.
- Give each writing agent an exclusive worktree or disjoint write scope. Keep user focus unchanged for background work.
- Treat `blocked` and `unknown` as states requiring inspection, not completion.
