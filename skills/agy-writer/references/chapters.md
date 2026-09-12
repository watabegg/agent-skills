# Chapter plans

Group related material without copying it into new prompts. A plan refers to the exact names of the packet's `##` headings. Those headings organize the material; Gemini chooses the wording and subheadings in the manuscript.

For example, a packet with sections named `読者と制約`, `現状`, `課題`, `変更方針`, `導入`, and `検証` can use:

```json
{
  "title": "設定の更新手順",
  "shared": ["読者と制約"],
  "chapters": [
    {"title": "背景と課題", "sections": ["現状", "課題"]},
    {"title": "変更すること", "sections": ["変更方針"]},
    {"title": "導入と確認", "sections": ["導入", "検証"]}
  ]
}
```

- Every level-two section must appear exactly once, either in `shared` or in a chapter. Unknown, duplicate and unassigned names fail before any model call. Heading-like text in fenced code is not a section.
- Material before the first `##` heading and the optional `shared` sections go to every chapter as context. Use them for the reader, overall scope and shared constraints; avoid assigning the whole design as shared context.
- Each chapter has a title hint and one or more sections, in the desired reading order. An optional positive `target_chars` is a writing target, not a truncation limit; required conditions must remain.
- `--plan` is optional. Without it, the entire packet receives one writer/editor pair, suitable for a short PR description. An explicit edit request may use an existing document as material; do not prepare a new Codex prose draft for Gemini.

```sh
agent-write-ja chapters --packet packet.md --plan chapters.json --out draft.md
```

`--jobs` controls concurrent chapters (default 3). Each chapter uses two independent Gemini conversations: writing, then editing against that chapter's original material. The editor checks missing conditions and unsupported additions while retaining natural Japanese. The helper assembles the edited chapters in plan order and saves the full original packet for the subsequent Codex review and repairs.

The return value includes `output`, `run_dir`, elapsed seconds, the chapter count and `review_mode: "continue"`. Inspect the whole assembled document against the original packet, then keep corrections with that same Codex reviewer. Gemini's editor and the helper's section-coverage checks do not replace this meaning review.

`--profile path.md` adds request-specific style preferences to both Gemini phases. Raw prompts, responses, chapter drafts and phase timings stay in the private run directory. A failed chapter or an output modified during generation leaves the destination unchanged. Diagnose the named phase before another attempt; successful sibling chapters remain in the saved evidence.

The low-level `draft` command remains available for explicit single-pass requests and compatibility. Normal document creation uses `chapters`, including its one-chapter form.
