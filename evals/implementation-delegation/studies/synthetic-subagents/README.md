# Four synthetic tasks with Luna subagents

Period: 2026-09.

Four bespoke tasks (two medium, two hard) were each attempted once by new Luna/max,
new Luna/high and old Luna/max. Fresh Astra/low graders assessed anonymous submissions.
The 100-point score combines correctness (80), maintainability (10) and own tests (10).

| Condition | Mean score | Mean worker seconds | Passed evaluator cases | Output tokens |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Luna/max | 97.25 | 452.54875 | 79/80 | 82,921 |
| GPT-6 Luna/high | 95.75 | 279.235 | 80/80 | 27,214 |
| GPT-5.6 Luna/max | 97.75 | 446.318 | 80/80 | 83,325 |

The exact scheduler's new-max submission lost one timed case (4 correctness points):
3.664s against a 3.5s grader limit. Five later repeats took 3.186–3.329s, passing that
limit but still missing the specified 2s target. Keep supplemental measurements separate
from the frozen score. Its total 93 also includes subjective deductions; the timed case
alone does not explain every lost point. Do not dismiss runtime performance as service
latency or conclude general model superiority from these few trials.

Only build-graph and mvcc are packaged for reuse. Revisioned-ledger and exact-scheduler
remain historical entries. Packaged tests make new evaluations possible; original
candidate submissions and raw grading evidence are not published.

## Records and limitations

- [Manifest](manifest.json): protocol, task catalog and limitations.
- [Trials](results.jsonl): one curated observation per line; unknown fields are null.
- [Shared methodology](../../methodology.md): metric and cost semantics.

- One trial per task/condition; difficulty labels are author estimates.
- Subjective maintainability and submitted-test scores are not objective correctness.
- One timing-sensitive frozen failure; supplemental reruns never replace the original score.
- Small score differences do not establish a general model ranking.
- Published graders are no longer globally secret.
