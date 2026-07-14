# Review Swarm And Dual Review Contract

herdr-dev-loop 0.5.0 supports `single`, `swarm`, `dual`, and `dual-swarm`. A **review group** pins every discovery lane and verifier to one integration head SHA. A Coordinator owns the provider-native sub-agents and returns one manifest; individual sub-agents do not write HLoop artifacts or close gates.

## Discovery topology

- `single`: one Codex discovery lane unless the provider is explicitly overridden.
- `swarm`: four to eight independent lanes on one provider; the default is six.
- `dual`: one Codex lane and one Claude lane.
- `dual-swarm`: four to eight lanes per provider; the default is four per provider.

Lane purposes cover integration contract, product correctness, security and privacy, data integrity and concurrency, failure recovery and observability, validation evidence, UX and accessibility, and repository-specific risks. Starting a review with `hloop reviewer start --mode <mode>` records the selected mode. A started review group never changes topology because a later global configuration edit changed.

## Finding normalization

Findings carry provider, discovering agent, severity, confidence, file and symbol, trigger, product impact, origin, proposed fix, and specification-decision flag. The fingerprint derives from the semantic location, trigger, impact, and fix. Similar titles alone do not produce consensus.

The normalizer marks a fingerprint reported by Codex and Claude on the same SHA as `consensus`; a finding from only one provider remains `unique`. Both categories proceed to verification. Cross-model agreement is evidence, not an automatic merge decision.

## Bounded verification

P0, P1, and specification-decision candidates require two independent verification passes. In dual modes, critical candidates require verifier coverage from both providers. P2 and P3 normally require one pass. A discovering agent cannot verify its own finding.

The review budget bounds parallel verifiers, total verifications, provider-specific usage, and elapsed time. When the budget or independent verifier pool is exhausted, the manifest retains the candidate as `insufficient_evidence`; it must not silently discard or confirm it. Only fact status `confirmed` can become a fix-task candidate.

## Manifest gate

The final manifest records the expected and completed lanes, normalized findings, verifier assignments, verification results, provider usage, shortfalls, and completeness issues. The gate remains open when a lane is missing, a finding count drifts, an independent pass is missing, or a budget shortfall leaves evidence incomplete.

The Manager triages confirmed findings into a fix task, user decision, accepted-risk candidate, or discard with evidence. Reviewer output cannot merge code, create tasks without Manager approval, or decide user-visible specifications.

Strict final review is armed only after the current batch closes, review triage is complete, no fix-task draft remains, and `hloop final-gates arm` pins the target SHA. Creating a new task disarms that record. This stability barrier prevents a full swarm and final gap audit from repeating after every incremental fix.
