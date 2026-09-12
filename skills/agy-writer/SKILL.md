---
name: agy-writer
description: Have Gemini write or revise Japanese design documents and PR descriptions from verified material, then check their meaning. Short chat replies and progress updates stay with the lead agent.
---

# Japanese documents via Gemini

Own the facts and design; let Gemini own the prose. Do not write a polished draft first or load other Japanese writing skills for this workflow.

1. Save concise material in `packet.md`: reader/purpose, problem, decisions and reasons, conditions, unresolved points, evidence, and actual identifiers when needed. Distinguish current behavior, proposed changes, and tests actually run. Existing material can be reused; no fixed template is required. For an existing document, use it as the packet and state the requested edit. Exclude irrelevant secrets.
2. Run:

   ```sh
   agent-write-ja draft --packet packet.md --out draft.md
   ```

   The command invokes `agy-writer` with Gemini 3.8 Flash High, saves inputs/results privately, and returns compact JSON. Read `output`; `needs_review` means generation succeeded, not that the meaning was verified. Do not read raw logs or Gemini instructions unless diagnosing a failure. If the commands are missing or runtime settings need maintenance, read [setup](references/setup.md).
3. Compare material → document for omissions and document → material for unsupported claims, changed conditions or stronger certainty. Preserve actual code names where they identify code. Ordinary vocabulary, translations, explanations, and structure belong to Gemini; a missing original term alone is not a finding.
4. For each real mismatch, provide the passage, supporting material, and the semantic difference. Use [targeted repair](references/repair.md); do not supply replacement prose. Check the changed passages and related context, then deliver within the task's existing authorization. A failed command leaves the output unchanged; diagnose it without claiming completion or repeatedly retrying it.
