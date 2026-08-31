# Invoice workbook contract

The script operates on the `integrated-attendance-v1` workbook used by `sync-teams-attendance`.

## Owned invoice surface

- Activate the tab whose exact visible name is `請求書`.
- `請求書!U2` is the plain-text month selector in `YYYY-MM` form.
- Export only `請求書!A1:R34`.
- Use A4 portrait, fit to one page, with sheet names, titles, page numbers, gridlines, frozen-row repetition, and notes disabled.
- Do not include the spreadsheet-only controls in T:V.

The script may change only `U2`, and only while exporting. It records the original value and restores it in `finally`; restoration failure is a failed run even when a PDF was downloaded.

## Invoice-tab identity

Do not reuse the `勤怠明細` gid from `spreadsheet.url`. Activate the exact `請求書` tab first and derive its gid from the active URL or tab attributes. This prevents a valid-looking one-page attendance PDF from being attached as the invoice.

## PDF acceptance

Accept the PDF only when all are true:

- the file begins with a PDF signature;
- `pdfinfo` reports one A4 page;
- extracted text contains `請求書`, the target year, and the target month;
- it does not look like a `勤怠明細` export;
- a rendered page has no clipping, overlap, mojibake, or unexpected controls.

Keep generated PDFs and renders outside the repository. They may contain personal, bank, and billing information.
