# Integrated attendance workbook contract

## Contract identifier

Use `integrated-attendance-v1` for the current workbook. Keep the spreadsheet ID, account details, issuer information, and bank information in the external config or workbook; never place them in the skill repository.

## Tab ownership

| Tab | Contract | Normal sync access |
| --- | --- | --- |
| `勤怠明細` | Row 1 headers: A=`日付`, B=`出勤`, C=`退勤`, D=`労働時間`. F:H are hidden legacy summary columns. | Append A:C only |
| `設定` | Yellow cells are user inputs. `B13` mirrors the invoice month selector. Rate history begins at row 16. `Z1:Z100` is a formula-owned internal text list for the month dropdown. | None |
| `月次集計` | A:H are formula-owned monthly results. A contains `yyyy-mm`. J:K show the selected-month summary. | None |
| `請求書` | A1:R34 is the PDF invoice. T:V is the spreadsheet-only control panel. U2 is the plain-text month dropdown; V2 is unused visual padding. T13:V14 is the PDF download link. | None |
| `請求履歴` | Manual snapshot and delivery/payment tracking table. | None |

## Attendance invariants

- Treat `spreadsheet.url` as the `勤怠明細` tab URL.
- Require the fixed row-1 A:D contract before writing.
- Infer semantic headers first, then require the inferred mapping to equal A/B/C. This catches accidental moves and duplicate headers.
- Find the append row from A/B/C only. Formula-filled D rows and hidden F:H cells are not attendance data.
- Paste date, start, and end as three separate single-column ranges.
- Never paste an entire row, fill D, unhide F:H, sort the tab, or create a filter during synchronization.
- Re-export the same gid and verify every appended `M/D|HH:mm|HH:mm` key.

## Downstream behavior

- `月次集計` calculates from raw attendance minutes and the effective-rate history. Do not round displayed hours before calculating compensation.
- Keep `請求書!U2` as plain text in `yyyy-mm` form. Do not merge U2 with V2 or apply a date number format.
- Generate the dropdown choices in `設定!Z2:Z100` as text from `月次集計!A4:A100`, and source U2's single validation rule from that helper range. This avoids the date-versus-text mismatch while still adding new months automatically.
- Treat `設定!Z1:Z100` as formula-owned implementation data, not user configuration. Do not expose it in the visible control panel.
- The PDF link must export only A1:R34, use A4 portrait, fit to one page, suppress gridlines and sheet chrome, and exclude T:V controls.
- When testing a month switch, switch to another existing month, verify amount and invoice date, then restore the original month in `finally`-style cleanup.
- When changing invoice layout or PDF parameters, generate the actual PDF, confirm it is one A4 page, visually inspect it, and delete temporary files containing personal or bank information.

## Intentional format changes

Only change this contract when the user explicitly asks for workbook maintenance. Update all of the following together:

1. This reference.
2. The `layoutContract` example config.
3. Script contract validation and self-test fixtures.
4. `SKILL.md` ownership and safety rules when responsibilities change.

Afterward, run `--inspect-sheet`, `--self-test`, the skill validator, and a real PDF check if the invoice surface changed.
