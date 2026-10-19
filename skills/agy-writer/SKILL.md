---
name: agy-writer
description: Have Gemini draft and edit substantial technical documents in any requested language, including design documents, implementation plans, reports, PR descriptions, quiz explanations, and long technical explanations. Use chapter writing and keep meaning checks in the same conversation. Short chat replies and progress updates stay with the lead agent.
---

# Technical documents via Gemini

Own the facts and design; let Gemini own the prose. The chosen workflow is chapter writing, a separate Gemini editor, and a continuing meaning review. Apply it to substantial technical writing regardless of language or whether the invoking skill mentions this writer. Do not write a lead-agent rough draft first or load other writing skills for this workflow.

1. Save concise material in `packet.md`: requested output language, reader/purpose, problem, decisions and reasons, conditions, unresolved points, evidence, and actual identifiers when needed. Distinguish current behavior, proposed changes, and tests actually run. Reuse investigation notes rather than rewriting them as prose. For a long document, group related packet sections into a few chapters using the small [chapter plan](references/chapters.md); the helper extracts their contents, so do not copy the material into separate prompts. Exclude irrelevant secrets.
2. Run:

   ```sh
   agent-write-ja chapters --language ja --packet packet.md --plan chapters.json --out draft.md
   ```

   Set `--language en` for English or specify another language name/code. The compatible `agent-write-ja` command supports all requested languages. Omit `--language` to follow the packet's requested language, defaulting to Japanese when none is specified. Repair inherits the original run's language unless explicitly overridden. For a document needing only one chapter, omit `--plan`. Each chapter gets a fresh Gemini writer and a fresh editor; chapters run concurrently and are assembled in plan order. The command returns compact JSON and saves evidence privately. Read `output`; `needs_review` is not a claim of semantic correctness. Do not read raw logs or Gemini instructions unless diagnosing a failure. For missing commands or runtime maintenance, read [setup](references/setup.md).
3. In the current Codex conversation, compare the entire assembled document with the original material in both directions: omissions, unsupported claims, changed conditions and stronger certainty. Check relationships across chapters too. Ordinary vocabulary, translations and structure belong to Gemini; a missing original term alone is not a finding.
4. For each real mismatch, provide the passage, supporting material and semantic difference. Use [targeted repair](references/repair.md); Gemini writes the replacement. Keep the reviewer in the **same conversation** for changed passages and related context. Do not start a fresh reviewer for each repair; if a reviewer was delegated, reuse its session. Preserve this continuity until the review is complete, then deliver within the task's existing authorization. Argon quota errors automatically fall back once per generation call to Gemini 3.8 Flash High; `--no-fallback` disables this for model comparisons. Other failures preserve the output; inspect the saved failure before retrying.
