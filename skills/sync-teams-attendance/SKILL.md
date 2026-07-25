---
name: sync-teams-attendance
description: Synchronize Microsoft Teams attendance punch messages into the configured integrated Google Sheets attendance and invoicing workbook through temporary-profile Chrome automation, including Gmail-delivered Microsoft verification codes, punch pairing, deduplication, append-only writes to 勤怠明細, workbook-contract checks, and post-write verification. Use when Codex needs to collect start, pause, resume, end, or clock-out messages, update this workbook safely, or reason about its 設定, 月次集計, 請求書, PDF export, and 請求履歴 boundaries.
---

# Sync Teams Attendance

## Overview

Use the bundled script to read the configured Teams chat through the user's existing Chrome/Gmail session, convert punch messages into closed work intervals, validate the integrated workbook contract, and append only missing intervals to `勤怠明細`. Keep personal identifiers, spreadsheet IDs, and browser state outside this repository.

## Configuration

Use `~/.config/sync-teams-attendance/config.json` by default. Copy `config.example.json` there, fill the user-specific values, and set the file mode to `600` and its directory to `700`.

Never print or commit the config, cookies, Microsoft verification codes, OAuth URLs, tokens, or copied Chrome profile data. The script copies only the configured Chrome profile into a temporary directory and removes it in `finally`.

The configured Chrome profile must already be signed into the Google account that can read Gmail and edit the target spreadsheet. Enter the configured Teams email on Microsoft's sign-in page, choose the passwordless email-code action, and retrieve that newly requested code from Gmail. Never enter, read, configure, or persist a Microsoft password.

Set `spreadsheet.url` to the `勤怠明細` tab URL. The default `spreadsheet.layoutContract` is `integrated-attendance-v1`; set it explicitly in new configs.

## Workbook Contract

Read [references/workbook-format.md](references/workbook-format.md) before any spreadsheet write or any requested change to formulas, tabs, dropdowns, invoice layout, or PDF export.

The normal sync owns only the three semantic fields in newly appended `勤怠明細` rows:

- `日付`
- `出勤`
- `退勤`

The workbook owns `労働時間`, hidden legacy columns, settings, rates, monthly calculations, invoice formulas, the month dropdown, the PDF link, and invoice history. Never paste entire rows.

## Standard Workflow

1. Confirm the config exists without printing it:
   ```bash
   test -f ~/.config/sync-teams-attendance/config.json && \
     stat -c '%a %n' ~/.config/sync-teams-attendance/config.json
   ```
2. Inspect the `勤怠明細` contract read-only:
   ```bash
   node skills/sync-teams-attendance/scripts/sync_teams_attendance.mjs --inspect-sheet
   ```
3. Run a dry run. This may sign into Teams and retrieve a newly delivered Microsoft verification code from Gmail, but it does not edit the sheet:
   ```bash
   node skills/sync-teams-attendance/scripts/sync_teams_attendance.mjs
   ```
4. Confirm inspection reports header row 1 with `日付`/`出勤`/`退勤` in A/B/C and `労働時間` in D. Review proposed intervals and anomalies. Do not invent a missing start or end. Stop if the contract, header mapping, or event ordering is invalid.
5. Run with `--apply` only when the user explicitly asked to update the sheet:
   ```bash
   node skills/sync-teams-attendance/scripts/sync_teams_attendance.mjs --apply
   ```
6. Report the number and date range of appended intervals plus the verification result. Do not report the verification code or private chat content.

Use `--since YYYY-MM-DD` when the user specifies a range or when the sheet has no parseable existing attendance rows. Use `--config <path>` only for a non-default config location.

## Attendance Write Rules

- Validate the configured workbook as `integrated-attendance-v1`: header row 1, A=`日付`, B=`出勤`, C=`退勤`, D=`労働時間`.
- Still infer date, start, and end semantically before validating the fixed contract. Treat inference as a corruption guard, not permission to write a different layout.
- Determine the append row from A/B/C only. Hidden legacy columns and formula-filled D rows must not move the append position.
- Write A, B, and C separately so D formulas and all downstream tabs remain untouched.
- Treat duration and compensation columns as sheet-owned. Do not overwrite them or synthesize their formulas.
- Deduplicate using normalized local date, start minute, and end minute with a configurable overlap window.
- Verify every appended interval by exporting the sheet again after the write.
- If the workbook contract changes intentionally, update the contract reference, validation fixtures, and script together. Do not silently fall back to another layout.

Read [references/sheet-adaptation.md](references/sheet-adaptation.md) before changing header inference, deduplication, or write behavior.

## Punch Interpretation

- Start or resume opens an interval.
- Pause, end, clock-out, or configured equivalent closes the current interval.
- A pause closes an interval even when no later resume occurs.
- Preserve the Teams `<time datetime>` instant and convert it to the configured timezone; use minute precision to match the visible Teams timestamp.
- Ignore unrelated chat messages and other authors.
- Allow a trailing open interval in dry-run output, but never write it.
- Refuse `--apply` when a non-trailing duplicate open or orphan close affects the candidate window.

## Safety Boundaries

- Default to dry-run. `--apply` is the only mutation mode.
- Restrict navigation to the configured Microsoft Teams, Gmail, and Google Sheets URLs.
- Read Gmail only to obtain the newly requested Microsoft verification code from the configured sender. Never log or persist the code.
- Use only Microsoft's passwordless email-code route. Never inspect Chrome Password Manager or enter a Microsoft password.
- Do not send Teams messages, edit existing attendance rows, unhide legacy columns, change formulas, change the month dropdown, follow the PDF link, or alter workbook structure during a normal sync.
- Treat `設定`, `月次集計`, `請求書`, and `請求履歴` as read-only unless the user explicitly requests workbook maintenance distinct from attendance synchronization.
- For explicitly requested workbook maintenance, preserve the ownership and print boundaries in `references/workbook-format.md`, verify the resulting PDF when invoice layout or export changes, and restore the selected invoice month after testing.
- Keep screenshots and debug artifacts out of the repository. If temporary evidence is necessary, store it under `/tmp` and remove authentication artifacts afterward.
- If Google authentication is missing, stop and ask the user to sign into the configured Chrome profile. Do not automate Google password entry.

## Validation

Run the deterministic fixtures and skill validator after changing the script or instructions:

```bash
node skills/sync-teams-attendance/scripts/sync_teams_attendance.mjs --self-test
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/sync-teams-attendance
```
