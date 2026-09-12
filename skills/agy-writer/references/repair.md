# Repair a semantic mismatch

Write a JSON array in `findings.json`. Each entry contains:

- `before`: an exact, unique passage from the saved draft; include enough context to disambiguate it.
- `evidence`: the relevant material, quoted or precisely referenced.
- `issue`: what was added, omitted, or changed in meaning, including conditions to retain. Do not prescribe replacement wording.

```json
[
  {
    "before": "The exact paragraph to replace.",
    "evidence": "The packet says a nullable reference is added; its storage mechanism is undecided.",
    "issue": "This paragraph commits to a foreign-key constraint although that mechanism is undecided."
  }
]
```

```sh
agent-write-ja repair --run <run_dir-from-the-last-result> --findings findings.json
```

The command reads the saved material and draft, asks Gemini for replacements, and updates the output only at the specified non-overlapping passages. Unrelated bytes stay unchanged. Use the returned `run_dir` for a subsequent repair. If the document changed externally, create a new draft from that document and the original material instead of repairing an obsolete version.

Use the top-level run returned by `chapters`, not a chapter's writer/editor subdirectory: the top-level run contains the entire original material and assembled manuscript. Continue the same Codex review conversation after each repair, using `changes` and the related context. If the reviewer was delegated, retain and resume that session rather than launching another one. If that context is lost, recover the original material and prior review before checking; a patch alone does not establish what the document was required to say.

Exit 0 / `needs_review`: read `changes` and check the replacements in context. Exit 2 / `needs_input`: Gemini needs missing material; investigate before asking the user. Exit 1 / `failed`: inspect the named run's logs. No automatic retry or publication occurs. These statuses do not certify semantic correctness.
