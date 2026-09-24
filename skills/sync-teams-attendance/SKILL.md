---
name: sync-teams-attendance
description: Synchronize the user's Teams attendance punches into the configured integrated Google Sheets workbook, preview missing intervals, or diagnose its attendance layout.
---

Resolve `attendance_skill_dir` to the absolute directory containing this SKILL.md. Configuration is `~/.config/sync-teams-attendance/config.json` (mode 600); use `config.example.json` for its shape, never print real values.

Preview when the user asks to inspect; add `--apply` when attendance synchronization was authorized:

```sh
node "$attendance_skill_dir/scripts/sync_teams_attendance.mjs" --json
node "$attendance_skill_dir/scripts/sync_teams_attendance.mjs" --apply --json
```

Choose one command for the request. An authorized apply already inspects the workbook, pairs punches, checks anomalies, refreshes the sheet immediately before writing, excludes existing intervals, and verifies the read-back. Do not first launch separate inspect and dry-run sessions unless the user requested a preview or a previous failure needs diagnosis. Optional `--since YYYY-MM-DD` limits the work-date window; otherwise the script derives it from the sheet.

Stdout is one JSON result; progress goes to stderr. Report `changed`, date range and verification:

- `completed`/exit 0: preview, already synchronized, or verified append. A preview has `reason: dry_run` and does not update the sheet.
- `needs_input`/exit 2: punch-order anomaly. Use `anomalies` and the target dates to explain what remains unresolved; do not invent missing punches.
- `failed`/exit 1: inspect the error. `reason: write_unverified` and `changed: null` mean a partial write may exist; inspect the live sheet before retrying.

The script may request a Microsoft verification email and read its code from Gmail. It does not send Teams messages or mail. Never report verification codes or private chat content.

The configured contract is `integrated-attendance-v1`: row 1, A=`日付`, B=`出勤`, C=`退勤`, D=`労働時間`. Only missing date/start/end values may be appended to A:C of `勤怠明細`. The workbook owns formulas, rates, monthly totals, invoice, month selector and history. Do not paste whole rows or repair the workbook during routine sync. Layout/pairing failures need diagnosis before any write.

Read [maintenance.md](references/maintenance.md) only when changing or troubleshooting the script or workbook. `--help` and `--self-test` are offline. For a requested invoice/PDF/Gmail draft after successful synchronization, use `prepare-invoice-email`; synchronization alone does not authorize that extra work.
