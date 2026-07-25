# Sheet adaptation and verification

This reference describes the semantic safety layer inside the fixed workbook contract. Read [workbook-format.md](workbook-format.md) first. Semantic discovery must not be used to accept a layout that violates `integrated-attendance-v1`.

## Contents

- Header discovery
- Append position
- Deduplication
- Writes
- Verification and failure handling

## Header discovery

Parse the exported CSV and inspect candidate header rows rather than reading canvas coordinates. Normalize case, whitespace, ASCII punctuation, and Japanese full-width punctuation before matching aliases.

Require one unambiguous column for each semantic field:

- `date`
- `start`
- `end`

Treat duration, monthly totals, compensation, notes, and formulas as out of scope. If several columns match the same semantic field or several header rows tie with incompatible mappings, stop before writing. Under `integrated-attendance-v1`, the unique result must then match row 1 and A/B/C; aliases do not authorize a different physical mapping.

## Append position

Find the last row below the detected header containing at least one of date, start, or end, then use the following row. Do not use the sheet's overall last non-empty cell because D contains formulas and F:H contain hidden legacy summaries.

Write date, start, and end as separate one-column ranges. This preserves formulas and arbitrary columns between the semantic fields. Do not fill a duration column, even when it is detected visually.

## Deduplication

Normalize existing and proposed rows to:

```text
M/D|HH:mm|HH:mm
```

Use the configured timezone and visible minute precision. Scan Teams with an overlap before the latest parseable sheet date so a partially synchronized day can be completed without duplicating existing rows.

When the sheet has no parseable attendance row, require `--since YYYY-MM-DD` or use the explicit configured lookback. Never infer a long historical import from unrelated summary cells.

## Writes

Use the Google Sheets name box to select exact target ranges and paste newline-separated values through the browser clipboard. Never use mouse coordinates for cells.

Before applying, require all of the following:

- The sheet is editable in the configured Google account.
- Header mapping is unique.
- Candidate intervals are closed and chronologically valid.
- No candidate key already exists.
- The target ranges start immediately after the detected attendance data.

## Verification and failure handling

After writing, export CSV again with a cache-busting query parameter and rediscover the schema. Confirm every proposed key exists in the new export.

If verification fails, report that a partial write may have occurred and provide the exact target ranges, but do not retry automatically. A blind retry can duplicate rows when the first write succeeded but the read-back was stale.

If formulas or totals do not update, report that separately. Do not repair or copy formulas unless the user explicitly requests formula maintenance after inspecting the new layout.
