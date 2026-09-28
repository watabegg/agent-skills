# Initial workflow DAG delegation exercise

Period: 2026-08.

The three conditions all passed 13/13. Production line counts were 241 (vague),
113 (explicit) and 127 (parallel). This is an observation about the generated code,
not causal proof that a particular prompt improves quality. A separate 14-test
forward implementation is not included in this comparison. Timing/cost remain unknown.

The original temporary task directory is gone. Session reports retain the historical
results; no reusable package is shipped for this saturated exercise.

## Records and limitations

- [Manifest](manifest.json): protocol, task catalog and limitations.
- [Trials](results.jsonl): one curated observation per line; unknown fields are null.
- [Shared methodology](../../methodology.md): metric and cost semantics.

- One trial per condition; evaluator filesystem-accessible, not fully blind.
- Code line counts are not quality scores.
- Separate forward implementation had 14 passing tests; excluded from the 13-case comparison.
- Original temporary files missing; historical timing and cost not established.
