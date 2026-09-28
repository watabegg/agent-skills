# Serializable MVCC store

Use `SPEC.md` as the full contract. Copy `starter/mvcc.py` into a fresh worker
workspace and provide `tests/public/`. Keep `tests/evaluator/` outside that workspace.
The starter deliberately raises `NotImplementedError`; it is not a solution.

Run commands from the repository root using an absolute submission path; see
`task.json` for public and evaluator commands. Each suite uses standard-library
`unittest`. Invoke them in separate processes to avoid stale imports. The evaluator
contains 20 cases and the public smoke suite contains one test.

## Publication calibration

The specification, starter, smoke suite and evaluator assertions are byte-identical
to the retained synthetic sources. Only directory layout and evaluator filename
changed. During packaging, all 20 evaluator cases and the smoke test passed against
an archived passing submission; the placeholder starter failed as expected.
That submission is not published. This validates local packaging, not every Python
version, machine, historical result or future model. No new model trial was run.

A future comparison should freeze the public hashes in `task.json` and record its
own model/effort, environment, prompt and grading conditions. Public availability
means these evaluators cannot be treated as globally secret.
