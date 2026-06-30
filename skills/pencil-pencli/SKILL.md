---
name: pencil-pencli
description: Safely inspect, edit, validate, and export Pencil design files through the Pencil headless CLI. Use whenever Codex needs to work with Pencil, PenCLI, design.pen, *.pen, *.pencli, encrypted Pencil design files, or any request to read/change/export a Pencil canvas or design file. Prefer `pencil interactive` headless mode over direct MCP tool calls; never inspect encrypted Pencil files with shell reads, grep, cat, sed, Python, or normal filesystem tools.
---

# Pencil / PenCLI

## Overview

Use the Pencil CLI, especially `pencil interactive` in headless mode, as the standard operating surface for `.pen` / `.pencli` design artifacts. Pencil files are encrypted or editor-owned artifacts, so filesystem reads are not valid evidence and can be unsafe.

The CLI interactive shell exposes the same design operations used by Pencil, but keeps the file path and save lifecycle explicit. This is the preferred path because direct Codex MCP calls are easy to misroute or apply against the wrong editor state.

## Hard Rules

- Do not read, grep, `cat`, `sed`, parse, copy, or patch `.pen` / `.pencli` files directly.
- Do not use local scripts or Python to inspect or modify Pencil design files.
- Do not call Pencil MCP tools directly from Codex as the default workflow.
- Use `pencil interactive` for design file content, structure, screenshots, exports, variables, and edits.
- In headless mode, always provide `--out`; provide `--in` when editing an existing design.
- Always start each interactive session with `get_editor_state({ include_schema: true })` before any other Pencil operation.
- Follow the schema returned by `get_editor_state` exactly when calling later interactive tools.
- Always call `save()` before exiting after edits.
- Use direct Pencil MCP tools only when the user explicitly asks to operate on an already-open Pencil app/editor, or when the headless CLI is unavailable and the fallback is clearly stated.

## Standard Workflow

1. Confirm the target input and output paths.
2. Start the headless interactive shell:
   - New design: `pencil interactive --out output.pen`
   - Edit existing design: `pencil interactive --in input.pen --out output.pen`
   - Optional preview: add `--enable-preview --preview-output /tmp/pencil-preview.png`
3. In the shell, call `get_editor_state({ include_schema: true })`.
4. Inspect only the relevant nodes using `batch_get`, `snapshot_layout`, `get_screenshot`, `get_variables`, or `get_guidelines`.
5. Make edits with `batch_design({ input: "..." })`; use `set_variables` only for design variables.
6. Validate edits immediately with `snapshot_layout` and, when visual correctness matters, `get_screenshot` or `export_nodes`.
7. Export through `export_nodes` only when the user asks for exported assets or the downstream task needs them.
8. Call `save()` and then `exit()`.
9. Reopen the saved `.pen` with `pencil interactive --in saved.pen --out /tmp/verify.pen` and validate the changed node again. For Git-backed files, checking file size or `git hash-object` is acceptable as a persistence check, but do not inspect `.pen` contents directly.

## CLI Commands

- `pencil interactive --out <output.pen>`: create or edit a new headless design session.
- `pencil interactive --in <input.pen> --out <output.pen>`: edit an existing design in headless mode.
- `pencil interactive --app <name> --in <file.pen>`: connect to a running Pencil app only when the user specifically wants the live app/editor.
- `pencil --in <input.pen> --out <output.pen> --prompt "<task>" --agent claude`: simple one-shot edit; prefer interactive mode for precise inspection and controlled changes.
- `pencil --out <output.pen> --prompt "<task>" --agent claude`: simple one-shot generation; prefer interactive mode when layout validation is needed.

## Interactive Tool Selection

Run these inside the `pencil interactive` shell, not as direct Codex MCP calls unless using an explicit fallback.

- `get_editor_state({ include_schema: true })`: required first step; confirms active file, selection, schema, and available operations.
- `get_guidelines()`: list available guides and styles. Load relevant guidelines before design changes when visual/design-system consistency matters.
- `batch_get()`: inspect top-level nodes or specific node data after identifying node IDs.
- `snapshot_layout({ ... })`: verify node positions, dimensions, hierarchy, clipping, and overflow.
- `get_screenshot({ nodeId: "..." })`: visually inspect the canvas or changed region; use sparingly.
- `get_variables()` / `set_variables({ ... })`: inspect or update design tokens/variables.
- `batch_design({ input: "..." })`: create, update, delete, move, or restyle nodes. Use the full function names returned by the schema, such as `Insert`, `Update`, `Replace`, `Move`, `Delete`, and `FindEmptySpace`.
- `export_nodes({ nodeIds: [...], outputDir: "..." })`: export selected nodes/assets after the design is correct.
- `save()`: persist the current design to the `--out` file.

## Batch Design Notes

- Prefer `batch_design({ input: "..." })` in `pencil interactive`. Some older examples or generated docs may show `operations`; if `operations` returns `OK` but `snapshot_layout` does not show the edit, retry with `input`.
- Treat `OK` as "the command was accepted", not as proof that the design changed. Always verify the target node or top-level count with `snapshot_layout` after a meaningful edit.
- For a new root frame, capture the ID returned in "Created nodes by name" and validate that ID directly, for example `snapshot_layout({ parentId: "newFrameId", maxDepth: 5, problemsOnly: true })`.
- If you are unsure whether the CLI argument shape is working, insert a tiny temporary frame with `batch_design({ input: 'tmp=Insert(document,{type:"frame",name:"TMP_VERIFY",x:0,y:0,width:10,height:10})' })`, confirm the returned ID appears in `snapshot_layout`, then delete it with `batch_design({ input: 'Delete("returnedId")' })`.
- After `save()`, reopen the file in a fresh interactive session and re-run layout validation on the changed node. This catches edits that were visible in a live editor or MCP session but were not persisted to the `.pen` file.

## Example Sessions

### Editing an existing file

```text
$ pencil interactive --in input.pen --out output.pen --enable-preview --preview-output /tmp/pencil-preview.png
pencil > get_editor_state({ include_schema: true })
pencil > batch_get()
pencil > batch_get({ nodeIds: ["frameId"], readDepth: 3 })
pencil > snapshot_layout({ parentId: "frameId", maxDepth: 3 })
pencil > batch_design({ input: 'Update("frameId/title",{content:"Updated title"})' })
pencil > snapshot_layout({ parentId: "frameId", maxDepth: 5, problemsOnly: true })
pencil > export_nodes({ nodeIds: ["frameId"], outputDir: "/tmp", format: "png" })
pencil > save()
pencil > exit()
$ pencil interactive --in output.pen --out /tmp/verify.pen
pencil > snapshot_layout({ parentId: "frameId", maxDepth: 5, problemsOnly: true })
pencil > exit()
```

### Creating a new file

```text
$ pencil interactive --out output.pen
pencil > get_editor_state({ include_schema: true })
pencil > get_guidelines()
pencil > batch_design({ input: 'const pos=FindEmptySpace({width:1440,height:900,padding:80});frame=Insert(document,{type:"frame",name:"Main",x:pos.x,y:pos.y,width:1440,height:900,fill:"#FFFFFF",placeholder:true});Update(frame,{placeholder:false})' })
pencil > snapshot_layout({ maxDepth: 2 })
pencil > save()
pencil > exit()
```

## Editing Guidance

- Keep edits scoped to the user's requested nodes, frames, page, or selection.
- Prefer existing variables, components, and guidelines over ad hoc styling.
- When node identity is ambiguous, inspect nearby hierarchy and screenshots before editing.
- When a user asks to connect Pencil output to app code, use `pencil interactive` for the design artifact and normal repo tools for code files.
- If the CLI fails, rerun with `--verbose-mcp` when more tool error detail is needed.
- If a direct Pencil MCP fallback was used, remember that the live editor state may not be saved to disk. Finish by reproducing or saving the edit through `pencil interactive`, then reopen the saved file to verify persistence.
- State clearly when a requested operation cannot be completed because the Pencil CLI is unavailable, the input file cannot be opened, the active app is unavailable, or required nodes cannot be found.

## Expected Trigger Phrases

- `design.pen を編集して`
- `Pencil CLIで確認して`
- `pencil interactive で編集して`
- `pencliファイルを操作して`
- `この .pen のスクリーンショットを見て`
- `Pencilのノードをexportして`
- `Pencil上の選択中フレームを修正して`
