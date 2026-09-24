# Maintenance

Read these only when changing or diagnosing the implementation:

- [Workbook format](workbook-format.md): ownership, columns, formulas and layout checks.
- [Teams message parsing](teams-message-parsing.md): edited messages, timezone and punch pairing.
- [Sheet adaptation](sheet-adaptation.md): intentional workbook format changes.

Run `node /absolute/skill-dir/scripts/sync_teams_attendance.mjs --self-test` after changing parser, workbook or auth logic. The self-test is offline and does not validate current browser UI. Use `--inspect-sheet` only when separately diagnosing the sheet; every normal run already inspects it. Inspect the script when repairing it, not before routine execution.
