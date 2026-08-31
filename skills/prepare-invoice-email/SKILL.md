---
name: prepare-invoice-email
description: Export a selected month from the integrated Google Sheets invoice tab as a verified one-page PDF, create or resume a matching Gmail draft from prior invoice mail, and normalize recipients to one primary To plus the remaining prior recipients in CC without sending. Use for invoice PDF creation, invoice-email drafting, recipient correction, or an end-to-end attendance-to-invoice workflow after sync-teams-attendance.
---

# Prepare Invoice Email

## Purpose

Use the bundled scripts instead of ad-hoc browser code. They copy the configured Chrome profile to a temporary directory, export the invoice, verify the PDF, create or resume one Gmail draft, attach the PDF, and normalize recipients. They contain no send action.

Use `~/.config/sync-teams-attendance/config.json` by default. It must be mode `600` and provide `chrome.userDataDir`, `chrome.profileDirectory`, `accounts.googleEmail`, and the integrated workbook's `spreadsheet.url`. Never print or commit the config, addresses, cookies, account data, invoice contents, or browser profile.

If the request also includes Teams attendance synchronization, finish `$sync-teams-attendance` first and verify its appended rows before preparing the invoice.

## Target Month

- Use the user's explicit `YYYY-MM` month when supplied.
- Otherwise use the latest completed billing month with synchronized attendance. In the usual monthly workflow this is the previous calendar month.
- Never create a current partial-month invoice unless the user explicitly requests it.

Read [references/invoice-workbook-contract.md](references/invoice-workbook-contract.md) before changing the month selector, export range, PDF checks, or invoice sheet discovery. Read [references/gmail-draft-policy.md](references/gmail-draft-policy.md) before changing recipient inference or Gmail draft behavior.

## Standard Workflow

Resolve the installed skill directory once, then use an absolute temporary PDF path:

```bash
invoice_skill_dir="${CODEX_HOME:-$HOME/.codex}/skills/prepare-invoice-email"
invoice_tmp_dir="$(mktemp -d)"
invoice_pdf="$invoice_tmp_dir/invoice-YYYY-MM.pdf"
```

Replace `YYYY-MM` consistently in every command.

1. Export the invoice. The script selects the `請求書` tab, records `U2`, switches it to the target month, exports only `A1:R34`, and restores the original `U2` value in `finally`.

   ```bash
   node "$invoice_skill_dir/scripts/invoice_email_draft.mjs" \
     --export --month YYYY-MM --pdf "$invoice_pdf"
   ```

   Require `EXPORT_OK` and, when the selector changed, `MONTH_RESTORED true`.

2. Run deterministic PDF verification:

   ```bash
   node "$invoice_skill_dir/scripts/invoice_email_draft.mjs" \
     --verify-pdf --month YYYY-MM --pdf "$invoice_pdf"
   ```

   Require `PDF_OK`. It proves one A4 page, an invoice title, and the target period. Also render the single page and visually confirm that it is the invoice—not `勤怠明細`—with no clipping, overlap, or mojibake before attaching it.

3. Create or resume the matching Gmail draft and attach the verified PDF:

   ```bash
   node "$invoice_skill_dir/scripts/invoice_email_draft.mjs" \
     --draft --month YYYY-MM --pdf "$invoice_pdf"
   ```

   The script takes the subject, body style, and primary recipient from the most recent sent invoice email, updates the billing period, saves and closes the compose window, and verifies the message under `in:drafts`. Require `DRAFT_OK ... sent=false`.

4. Inspect recipient normalization without changing the draft:

   ```bash
   node "$invoice_skill_dir/scripts/normalize_invoice_recipients.mjs" \
     --inspect --month YYYY-MM --pdf "$invoice_pdf"
   ```

5. Normalize the draft recipients:

   ```bash
   node "$invoice_skill_dir/scripts/normalize_invoice_recipients.mjs" \
     --month YYYY-MM --pdf "$invoice_pdf"
   ```

   Preserve the first external prior To recipient as the sole To recipient. Move additional prior To recipients and preserve prior CC recipients in CC. Require `DRAFT_UPDATED to=1 cc=<n> bcc=0 sent=false`.

6. Report the invoice month, PDF verification, To/CC/BCC counts, attachment name, draft verification, and `sent=false`. Delete the local temporary PDF and render only after the Gmail draft and attachment are verified.

Use `--config /absolute/path/config.json` with both scripts only for a non-default config.

## Stop Conditions

- Stop before drafting when the PDF verification fails or visual inspection is not clearly an invoice.
- Stop when no prior sent invoice email exists; do not invent recipients or organization-specific wording.
- Stop when the prior invoice contains BCC. The script intentionally refuses to infer a no-BCC draft from that state.
- Stop when Google authentication is missing. Do not automate Google password entry.
- If a run fails after opening a draft, search the exact target subject and attachment before retrying. The script reuses a matching draft, but do not create duplicates manually.
- Never click Send, press `Ctrl+Enter`, use Gmail scheduling, or call a mail-sending API. User authorization to create a draft is not authorization to send it.

## Validation

After changing either script or these instructions, run:

```bash
node skills/prepare-invoice-email/scripts/invoice_email_draft.mjs --self-test
node skills/prepare-invoice-email/scripts/normalize_invoice_recipients.mjs --self-test
node --check skills/prepare-invoice-email/scripts/invoice_email_draft.mjs
node --check skills/prepare-invoice-email/scripts/normalize_invoice_recipients.mjs
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/prepare-invoice-email
```
