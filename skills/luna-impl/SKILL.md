---
name: luna-impl
description: Orchestrate non-trivial implementation work that benefits from subagents by converting it into bounded, cohesive, objective, independently verifiable tasks for GPT-5.6 Luna/max workers. Use by default when delegating implementation, investigation, tests, migrations, or verification to the Codex luna-worker role or to Luna agents in Herdr. Keep architectural, product, visual, creative, and ambiguity-resolving judgment with the lead agent.
---

# Luna Implementation Orchestrator

Use Luna as an execution worker after the lead agent has made the decisions that require broad context or creative judgment. The lead owns requirements, architecture, decomposition, integration, and final verification.

Do not delegate merely to create activity. Handle a tiny change locally. Keep a task with the lead when its main difficulty is deciding what should be built, reconciling ambiguous requirements, selecting architecture, designing a novel interface, or integrating overlapping changes.

## Fix the contract before choosing the task graph

Inspect the relevant code, configuration, tests, and repository instructions before delegating. Resolve material ambiguities and choose the implementation shape locally. Derive public behavior from evidence. Do not turn words such as “robust,” “clean,” or “best” into a stricter API, new validation policy, or compatibility break. If no precedent answers a material public-contract choice, ask the user or keep the narrowest compatibility-preserving interpretation and disclose it.

Write down the externally observable contract before choosing worker boundaries. Fix exact public names and shapes, success return values (including whether mutating operations return a value or `None`), ordering, boundary conditions, failure behavior, idempotency, and persistence or migration semantics when they apply. A longer complete contract is preferable to a short prompt that leaves Luna to invent public behavior.

Represent the remaining work as a dependency graph. A Luna-ready task must have all of these properties:

- one observable outcome;
- enough concrete context to work without rediscovering the architecture;
- explicit acceptance criteria and a feasible verification method;
- one exclusive write scope, or a read-only scope;
- no unresolved product or architecture choice;
- a result that the lead can review independently.

If any property is missing, refine the task or keep it local. Dispatch only the currently unblocked tasks whose write scopes do not overlap. Read-only investigations may run together. Sequence tasks that share generated files, schemas, central registries, migrations, snapshots, or other merge-sensitive state.

Separate files are not automatically independent. When one slice produces data, types, errors, APIs, events, or generated output consumed by another, the lead must define that interface exactly before parallel dispatch. Otherwise sequence the producer before the consumer.

## Minimize handoffs, not task size

Use the fewest worker handoffs that still create real concurrency, isolation, or independent verification. “Small” means bounded by a complete contract and a coherent ownership surface; it does not mean one file or one function per worker.

Prefer one Luna worker for a cohesive vertical slice when it can implement and verify the result without an unresolved decision. Keep tightly coupled producers and consumers together, especially a state model and the state machine that consumes it. Split them only when their interface is already stable and the split shortens the critical path.

Before adding a task, name the concrete benefit of that handoff:

- it can run concurrently without waiting for or editing another slice;
- it isolates a genuinely separate subsystem or generated artifact;
- it provides implementation-independent verification; or
- it removes a bounded investigation from the implementation path.

If none applies, merge it into the nearest cohesive task. Avoid dependency graphs that are mostly serial. Estimate the longest dependency chain and prefer the simpler graph when extra nodes add review and context-replay cost without reducing that chain.

For a medium, strongly coupled feature, start from one implementation owner plus an optional independent contract-test owner. Expand only when repository evidence shows multiple independent ownership surfaces. An independent test worker may receive the fixed public contract and fixture, but not the implementation rationale, suspected defects, or another worker's report.

Read [references/task-contract.md](references/task-contract.md) before writing Luna prompts. Use its full contract for write tasks; the compact form is sufficient for narrow read-only investigation.

## Direct Codex subagents

Use the `luna-worker` agent role. That role selects GPT-5.6 Luna with `max` reasoning; do not replace it with a generic worker and assume the same configuration. Use `fork_turns: "none"` or a bounded positive history so the role-specific configuration applies, and make the prompt self-contained when history is not inherited.

Every write delegation must name exact ownership and say that other agents share the workspace, that the worker must not revert others' edits, and that it must accommodate concurrent in-scope changes. Prefer disjoint tasks that can complete in one focused turn. The worker must not redesign the surrounding system or delegate again.

Start with the smallest useful conflict-free batch. Increase parallelism only when another task has a distinct ownership surface and does not lengthen the critical path through an avoidable handoff. Keep the critical path local when waiting for it would stall all other work.

## Herdr execution

When the user explicitly requests Herdr, read [references/herdr.md](references/herdr.md) and use the existing `$herdr` workflow for ordinary panes. This skill shapes task contracts; it does not bypass Herdr preflight, lifecycle, pane, worktree, or agent-state rules.

## Accept results as evidence, not proof

For every completed task, the lead must:

1. inspect the changed paths and reject or separately review scope drift;
2. review the diff against the acceptance criteria and repository conventions;
3. rerun the smallest decisive verification, then broader checks appropriate to the integrated change;
4. integrate results in dependency order and resolve conflicts locally;
5. record residual risks, unrun checks, and any assumption the worker made.

Send one focused follow-up when the implementation has a local defect and the contract is still valid. If the result exposes a missing requirement or design decision, do not broaden the Luna prompt; the lead must decide and issue a new contract. Repeated failure on the same bounded task is a signal to take the task back or use a stronger model, not to add vague encouragement.

If `luna-worker` or the requested Herdr model configuration is unavailable, state the actual limitation. Do not silently substitute a different model while claiming Luna/max was used.
