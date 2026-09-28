# Small migration pilot: one module, three functions

Period: 2026-09.

Four conditions, two trials each, generated one Python module containing three
functions. All eight passed 283/283 correlated checks. Mean seconds: old Luna/max
76.925; new Luna/max 54.905; new Luna/high 29.745; new Luna/max with changed stopping
guidance 68.960. This does not establish a benefit from the prompt change.

This was tool-free CLI generation, not a full luna-impl subagent workflow. Only requested
model/effort labels are established here. The checks are not 283 independent tasks and
the three functions are not three independent trials. No reusable package is selected.

## Records and limitations

- [Manifest](manifest.json): protocol, task catalog and limitations.
- [Trials](results.jsonl): one curated observation per line; unknown fields are null.
- [Shared methodology](../../methodology.md): metric and cost semantics.

- 283 correlated checks do not represent 283 independent tasks.
- Tools forbidden: not the full luna-impl workflow.
- Two trials per condition; all pass, so this suite is saturated.
- Requested model/effort only; no backend identity attestation.
- Context configuration differed across model generations.
