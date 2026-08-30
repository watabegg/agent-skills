# Luna task contract

Use the contract to remove decisions from the worker prompt, not to prescribe implementation details that the repository already makes obvious.

## Write-task template

```text
You are working in a shared workspace with other agents. Do not revert their
changes. Accommodate concurrent changes that are inside your declared scope.
Do not edit outside your ownership and do not delegate this task.

Working directory: <absolute repository/worktree path>

Objective:
<one sentence describing one observable result>

Context and fixed decisions:
- <relevant files, existing pattern, API/schema decision>
- <invariants that must remain true>
- <exact public names, success return values including None behavior, boundary conditions, ordering, failure/idempotency, and persistence semantics when applicable>

Interface contract:
- <exact input/output shape, types, errors, ordering, or "none">

Ownership:
- Write allow: <exact files or narrow directories>
- Write deny: <neighboring or manager-owned paths>

Dependencies and inputs:
- <completed prerequisite, interface supplied by another task, or none>

Acceptance criteria:
- <externally observable behavior>
- <edge/error behavior>
- <compatibility or non-regression requirement>

Verification:
- <exact targeted command or concrete manual evidence>
- <broader command only when proportional to the slice>

Stop and report instead of guessing when:
- a required decision is not fixed by this contract or the existing code;
- the requested behavior requires an out-of-scope edit;
- the verification environment or prerequisite is unavailable.

Return:
- changed files;
- acceptance criteria mapped to the implementation;
- commands run and exact results;
- assumptions, unrun checks, and residual risks.
```

Acceptance criteria describe behavior, not an implementation wish list. Include failure cases, ordering, idempotency, authorization, compatibility, or concurrency only when relevant.

## Read-only investigation template

```text
Investigate <one concrete question> in <path>. Do not edit files and do not
delegate. Inspect <named likely sources>. Return relevant files and line
numbers, observed facts, constraints those facts impose, commands run, and any
remaining uncertainty. Separate facts from recommendations.
```

## Decomposition checks

Before dispatching a batch, verify:

- every task has a stable ID and a single owner;
- dependencies form an acyclic graph;
- tasks in the same batch have disjoint write scopes;
- shared interfaces are fixed exactly before dependent tasks start; separate files alone do not prove independence;
- every public callable has an explicit success return contract, including mutating methods that may otherwise be assumed to return `None`;
- tightly coupled producers and consumers remain together unless a stable interface makes the split genuinely concurrent;
- generated artifacts have one owner and one regeneration command;
- each task can fail without corrupting another task's workspace;
- integration order and final cross-slice checks are known;
- every handoff buys concurrency, subsystem isolation, independent verification, or a bounded investigation;
- the longest dependency chain is not inflated by file-oriented decomposition.

A task is still too broad when it asks the worker to “finish the feature,” “make it robust,” “follow best practices,” or “decide the best design” without converting those phrases into observable criteria and fixed decisions.

A task is too fragmented when its worker cannot verify an externally meaningful result without waiting for the next worker, or when the split follows file boundaries while the files jointly implement one stateful contract. Prefer a cohesive vertical slice in that case.

For implementation-independent tests, give the test owner the fixed public contract and fixture only. Do not provide implementation notes, suspected defects, or another worker's report. Keep test ownership disjoint from production files.
