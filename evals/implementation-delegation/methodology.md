# Evaluation protocol and record semantics

This archive preserves historical observations and a small reusable task suite.
It is not a model leaderboard. Original runs were not repeated for publication.
The archived studies differ in prompts, model configuration, grading and environment;
do not pool their test counts into a single accuracy percentage.

## Choose a task by use case

| Task | Purpose | Recommended use |
| --- | --- | --- |
| [Lease queue](tasks/lease-queue/) | State transitions, expiry/retry and exact API contracts | Compare delegation prompts and ownership arrangements |
| [Build graph](tasks/build-graph/) | Dependency propagation, invalidation and deterministic ordering | Medium implementation task |
| [MVCC](tasks/mvcc/) | Serializable transactions, conflicts, savepoints and rollback | Hard consistency task |

Difficulty is an author estimate. These bounded tasks do not measure navigation of a
large unfamiliar repository, visual design or architectural judgment. The workflow
and three-function pilot saturated in their historical comparisons. The exact
scheduler emphasizes algorithmic performance and is retained as history, not as a
packaged default. Private repository tasks are summary-only; generic replacements
would be new tasks with new IDs and no inherited historical scores.

## Running a new comparison

1. Select one task **version** for all comparison arms. Record model, effort,
   skill revision, prompt variant, repetitions, budget and intervention policy before
   running. Change the task version if the contract or evaluator behavior changes.
2. Read the task's `task.json` and recovery notes. Only packages marked `runnable`
   are eligible. Provide each worker a fresh directory containing only `SPEC.md`,
   `starter/` and `tests/public/`. Do not expose this archive, study results, evaluator,
   other submissions or repository history to the worker. Public evaluators cannot
   remain globally secret; disclose whether isolation is enforced or instruction-only.
3. Perform dependency/toolchain preflight inside the **actual worker directory**.
   Run every arm under equivalent resource limits. Record setup separately from solver
   time. Preserve the submitted snapshot at the deadline, even if incomplete.
4. Use the evaluator command in `task.json`, with the submitted module on
   `PYTHONPATH`, from a separate grader directory/process. Run public and evaluator
   suites separately. Starters intentionally fail; they are not reference solutions.
   A test harness successfully discovering tests is not proof of a working solver.
5. Record requested and observed model/effort separately. A request label alone is
   not attestation; use null when evidence is unavailable. Session configuration
   evidence is not proof of an immutable backend model snapshot.
6. Grade correctness objectively. Report maintainability and submitted-test quality
   separately with a rubric; use a fresh reviewer and anonymous submissions where
   feasible. Never infer equivalent quality from equal test pass counts.
7. Preserve failures, timeouts and invalid setups. A repaired setup requires all
   comparable arms to rerun. Preserve the original designation and explain exclusions.
   Grader changes after submissions require uniform calibration on baseline/reference/
   candidates and explicit disclosure. Do not overwrite frozen scores with favorable
   supplemental timing repeats.

No model launches are included in the archive's validation commands. Example offline
commands, from the repository root:

```sh
python3 evals/implementation-delegation/scripts/validate_records.py .
PYTHONPATH=/path/to/submission python3 -m unittest discover -s evals/implementation-delegation/tasks/build-graph/tests/public
PYTHONPATH=/path/to/submission python3 -m unittest discover -s evals/implementation-delegation/tasks/build-graph/tests/evaluator
```

Use an isolated working directory and an absolute submission path to avoid accidental
imports. The retained synthetic tasks use Python's standard library. Individual task
notes describe their local calibration and portability limits.

Historical contracts and graders are not guaranteed to cover every behavior. Do not
silently add hidden requirements that workers were never given. Record any clarified
contract as an adaptation or a new version, and distinguish its results from old trials.

## Records

Each `studies/<id>/manifest.json` describes the experiment and limitations;
`results.jsonl` has one historical trial per line. `task_catalog` includes unselected
historical tasks without pretending they have runnable public packages. `task_version:
"historical"` deliberately does not attest byte-for-byte equivalence to a newly
packaged task version. Package origin/adaptations and artifact hashes document the
published material, not private source fingerprints.

- `objective_passed/total` counts checks, not independent tasks. Own tests are separate.
- `subjective_scores` is separate from objective correctness. The four-task study's
  total is correctness / 80 + maintainability / 10 + test quality / 10.
- `timing_checks` preserves performance criteria separately; a timed case can still
  affect the original correctness score.
- `solver_seconds` must be read with `time_scope`: an old orchestration run may include
  parent and worker time. It is not always the sum of child processing times.
- `evidence_level` distinguishes retained artifacts from recovered session reports.
  `public_reproducibility` describes what is publicly available, a separate dimension.
  `task_package_available` permits new trials; it does not mean historical submissions,
  environments or numeric scores can all be independently reproduced from this repo.
- Missing values are null, never invented zeros. `skill_revision` remains null when
  an exact revision-to-run mapping was not established.
- Templates are field guides, not historical observations. New runs should also record
  public artifact hashes, grader revision, environment/toolchain, blinding, seeds,
  intervention policy, setup/grading/end-to-end timings and actual usage evidence.

## Costs and tokens

All archived dollar amounts are **historical API-equivalent estimates**, not current
prices or actual subscription charges. Preserve the historical rates and scope.
For each applicable rate band, compute:

```
((input_tokens - cached_input_tokens) * uncached_input_rate
 + cached_input_tokens * cached_input_rate
 + cache_write_input_tokens * cache_write_rate
 + output_tokens * output_rate) / 1_000_000
```

Cached input is a subset of input, and reasoning output is a subset of output; do
not add either twice. Apply rate-band/context/tier adjustments only when evidenced.
Older rounded estimates with only total token counts cannot be recalculated from
these records. Do not infer uncached token counts from the reported total alone.

Keep worker, parent, setup and review scopes distinct. The repository-replay
`accounting.json` includes exploratory runs and superseded review; it excludes outer
lead/audit/final-tail work. Its orchestration category is not a measured polling-only
cost. Small worker costs do not imply the whole orchestration is inexpensive.
