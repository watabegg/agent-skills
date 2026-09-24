---
name: prepare-invoice-email
description: Export and verify a monthly invoice PDF from the configured Google Sheets workbook, then create a Gmail draft with one primary To, remaining prior recipients in CC and no sending.
---

Use the requested target month. If it is missing and cannot be inferred, ask for it. Reuse attendance synchronization authorization when it was part of the same end-to-end request. The configuration is shared with `sync-teams-attendance`; do not print it.

Resolve `invoice_skill_dir` to the absolute directory containing this SKILL.md. Use an absolute PDF path in a unique temporary directory. Replace the month consistently:

```sh
node "$invoice_skill_dir/scripts/invoice_workflow.mjs" \
  --prepare --month YYYY-MM --pdf /tmp/invoice-run/invoice-YYYY-MM.pdf
```

The command exports `請求書!A1:R34`, restores U2, verifies one A4 page with the invoice title and target period, then renders a PNG. Its JSON result has `status: needs_input`, `reason: visual_review_required` and PDF/preview/log paths; exit 2 is the expected review checkpoint. Open the preview and confirm the invoice, target month and no clipping, overlap or mojibake. `PDF_OK` alone does not verify appearance or the business correctness of the amount. If only a PDF was requested, deliver it after this check.

When a draft was requested and the same PDF has passed visual review:

```sh
node "$invoice_skill_dir/scripts/invoice_workflow.mjs" \
  --draft --month YYYY-MM --pdf /tmp/invoice-run/invoice-YYYY-MM.pdf --visual-checked
```

This rechecks the PDF, creates/resumes a matching draft and normalizes recipients. `--visual-checked` records the agent's completed visual check; it is not another user approval. Do not use it for an unseen or subsequently changed PDF.

Exit 0 / `completed` / `draft_verified` means a draft was verified, with `sent: false`. Exit 1 / `failed` identifies the failing step and log. If `changed` is null, inspect the sheet or draft before retrying; do not assume the operation rolled back. Never click Send.

Keep one primary To from the previous invoice mail, the remaining prior To/CC recipients in CC, and BCC empty. The existing script rejects unexpected recipients. Preserve workbook formulas, rates, history and layout; do not use a fallback worksheet when the invoice cannot be identified. Keep PDFs, mail content, recipient data and browser state outside this public repository.

For repair or lower-level recipient inspection, read [maintenance.md](references/maintenance.md). Routine operation only needs the two commands and their artifacts.
