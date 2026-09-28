# Dependency-aware lease queue

This package recovers the synthetic lease-queue exercise used to study detailed
contracts and implementation delegation. It is runnable for new evaluations;
the historical experiments themselves are not fully reproducible from this repo.

## Use

Provide `SPEC.md`, the contents of `starter/`, and `tests/public/` in a fresh
solver workspace. Keep evaluator code and study results outside that workspace.
The starter methods deliberately raise `NotImplementedError`.

Run the commands in `task.json` from the repository root, with an absolute path
to the directory containing the submitted `leasequeue/` package. The correct
public command targets `tests/public`; this supersedes the historical starter
README's `-s tests` command for this archive layout. The evaluator is an executable
`evaluate.py` (43 unittest cases), not a default `test_*.py` discovery file. It
prints `BLIND_SCORE` as a historical output label; public availability means the
suite is not globally secret.

## Recovery and local calibration

- Original starter files, five visible tests and the 43-case evaluator were
  recovered from retained tool payloads. Assertions were not rewritten.
- `SPEC.md` is an authored reconstruction from fixed-contract material and
  evaluator assertions, not a byte-identical historical prompt. It explicitly
  states that heartbeat returns the renewed Lease. The revised historical prompt
  omitted that return requirement; comparisons must preserve this distinction.
- A candidate reconstructed privately from retained implementation/integration
  patches passed the five visible tests and reproduced **42/43**, failing only
  because heartbeat returned None where the evaluator expected a Lease.
- A separate private calibration copy changed only that return to the renewed
  lease. It passed **43/43** and the five visible tests. This is packaging
  calibration, not a new model run or a correction of the historical score.
- The unimplemented starter failed the visible tests as expected. Reference
  candidates and reconstruction logs are not shipped in this package.

The source directories no longer exist; patch reconstruction is not attestation of
an untouched original filesystem snapshot. Local calibration does not guarantee
all edge cases, environments or Python versions. Keep the historical **42/43**
record unchanged and label future trials with this package's version and hashes.
