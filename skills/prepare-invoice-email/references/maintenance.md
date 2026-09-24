# Maintenance

Read [invoice-workbook-contract.md](invoice-workbook-contract.md) before changing sheet discovery, month selection, export ranges or PDF checks. Read [gmail-draft-policy.md](gmail-draft-policy.md) before changing recipient inference or draft behavior.

The workflow uses `invoice_email_draft.mjs` modes `--export`, `--verify-pdf` and `--draft`. `normalize_invoice_recipients.mjs --inspect` diagnoses a matching draft; mutation now requires `--apply`. Both use `--month YYYY-MM --pdf /absolute/invoice.pdf` and optional `--config`.

Run each low-level script with `--self-test` and run `node --test /absolute/skill-dir/tests/workflow.test.mjs` after relevant changes. These are offline checks; the low-level draft self-test checks period/text formatting, not live export or Gmail UI. Routine operation does not need to rerun all self-tests.
