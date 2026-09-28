# Anonymous historical repository repair replay

Period: 2026-09.

Two actual pre-fix repository repairs were replayed using GPT-6 Luna/high, max and
xhigh. Only generic observations are published: case A (multi-step save/retry) and
case B (stale revision rejection across callers). No source, contract or grader package
is provided; this study is summary-only.

| Effort | Case A checks / time | Case B checks / time | Two-task worker estimate USD |
| --- | --- | --- | ---: |
| high | 9/9 / 6m52s | 15/15 / 17m54s | 0.16378266 |
| max | 9/9 / 15m43s | 15/15 / 28m33s | 0.30631428 |
| xhigh | 9/9 / 10m17s | 15/15 / 38m19s | 0.30937020 |

These are six primary cells: original valid case A and clean replay case B. All completed
within 40 minutes. Initial case B trials lacked frontend dependencies in all candidate
directories; their times are exploratory, including a max timeout at 2408.408s. All arms
were rerun after candidate-directory preflight and warmup. Missing dependencies confound
timing, but are not proven to have caused the timeout. A graded deadline snapshot can
pass checks even when the worker did not finish in time.

Case A needed a post-submission grader adapter correction, applied uniformly to baseline,
reference and all submissions without changing behavioral assertions. This is disclosed
as a post-hoc limitation, not hidden behind the final scores.

Case A qualitative scores (maintainability / tests, each out of 10) were high 6/4 versus
max and xhigh 8/7. High duplicated update/cache responsibilities and had thinner flow
tests; conditional version/message concerns were not established runtime defects. Case B
scores were 8/7, 8/8 and 8/8, with no substantive contract gaps found. UI coverage was
partial and full live-service E2E was not run. Passing all checks is not equivalent quality.

The known experiment accounting includes all nine worker trials: about $1.59 for workers
and $98.44 for the parent roles. These are historical standard-API-equivalent estimates,
not bills. The subtotal excludes outer lead, additional audit and final processing tail.
Observed experiment wall time through the summary was about 4h07m42s, including
setup and reruns; it is not a single solver duration. See accounting.json for the role breakdown. Orchestration/monitoring is not separately
measured polling-only spend. Parent overhead and task-contract quality deserve attention
alongside worker effort; this single study does not settle a universal default.

## Records and limitations

- [Manifest](manifest.json): protocol, task catalog and limitations.
- [Trials](results.jsonl): one curated observation per line; unknown fields are null.
- [Shared methodology](../../methodology.md): metric and cost semantics.

- Summary only: original repository, source, task contracts, patches and graders are not published.
- One trial per task/effort; test counts are not independent task counts.
- Case A grader adapter repaired after submission, uniformly for baseline/reference/all candidates; behavioral assertions unchanged.
- Initial case B had missing frontend dependencies in all three candidate directories; graders had dependencies. Initial timings excluded from primary comparisons; all arms rerun after preflight and warmup.
- Missing dependencies confound timing; not proven to cause the initial timeout.
- UI coverage partial, including static audit; no full live-service E2E.
- Exact execution timestamps and source identifiers omitted.
