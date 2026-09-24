---
name: pencil-pencli
description: Inspect, edit, validate and export Pencil .pen/.pencli designs through the headless Pencil CLI, including persistence checks and schema-guided operations.
---

Use `pencil interactive` for design contents. Do not read, grep, copy, parse or patch `.pen`/`.pencli` with ordinary filesystem tools or custom scripts. They are encrypted/editor-owned artifacts. Normal repository tools remain appropriate for app code.

Start a new design with `pencil interactive --out output.pen`; edit one with `pencil interactive --in input.pen --out output.pen`. Always provide `--out`. In **every** session, including verification after reopening, the first Pencil operation is:

```text
get_editor_state({ include_schema: true })
```

Follow the returned schema. Inspect the requested nodes with `batch_get` and `snapshot_layout`; use guidelines, existing components and variables when relevant. Edit with `batch_design({ input: "..." })` and use the full operation names the schema provides. Capture returned node IDs instead of guessing them.

After meaningful edits, verify the affected nodes with `snapshot_layout`, plus a screenshot/export when visual correctness matters. `OK` proves command acceptance, not a changed design. Call `save()` before `exit()`, reopen the saved file in a fresh session, fetch schema again and verify the changed nodes. Export assets when requested or needed downstream. File size or `git hash-object` may check persistence without reading design contents.

Read [operations.md](references/operations.md) when an API example, batch-design diagnostic or CLI fallback is needed; do not read the whole reference for routine inspection.

Use direct Pencil MCP only when the user asks to operate on the live app/editor, or when the CLI is unavailable and the fallback is stated. A live editor edit still needs to be saved and reopened to establish persistence. If paths, target nodes or the available interface are unresolved, report the specific missing information before editing.
