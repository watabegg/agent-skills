# Lease queue contract and orchestration study

Period: 2026-08.

The original GPT-5.6 Luna/max vague arm passed 24/43; the detailed-contract arm
passed 43/43. Sol/high alone with the vague prompt passed 23/43. These numbers partly
measure agreement with unspecified contract details, not just implementation ability.

The initial Sol/high + five Luna/max orchestration passed 43/43 in 36m33s, with a
reported total API-equivalent estimate of $1.586. The revised fresh parent with two
workers passed 42/43 in 16m36s at $1.759. The observed elapsed reduction is 54.6%, while
cost increased 10.9%; one run and a changed prompt do not isolate the skill's effect.

The revised prompt omitted the successful heartbeat return value. The implementation
returned None where the grader expected Lease; the state transition was correct.
Preserve 42/43 and the omission. Do not claim an identical-prompt regression or silently
correct the historical score. The reusable package uses the explicit contract; consult
its recovery notes and task.json rather than assuming the entire old environment exists.

This history motivated cohesive implementation ownership, fewer sequential handoffs,
explicit successful return values and independent tests. No skill behavior is changed
by publishing this archive.

## Records and limitations

- [Manifest](manifest.json): protocol, task catalog and limitations.
- [Trials](results.jsonl): one curated observation per line; unknown fields are null.
- [Shared methodology](../../methodology.md): metric and cost semantics.

- One trial per condition; neither randomization nor double blinding.
- Vague arms omitted details required by the detailed-contract grader; not a pure capability comparison.
- Revised prompt omitted heartbeat success return; the 42/43 result is not an identical-prompt regression.
- Grader authored after implementations by a condition-aware author; no OS isolation.
- Reported historical cost estimates retained; detailed usage breakdown is unavailable in these curated records.
