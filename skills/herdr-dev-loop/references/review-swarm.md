# Review Swarm And Dual Review Contract

herdr-dev-loop 0.5.3 supports `single`, `swarm`, `dual`, and `dual-swarm`. A **review epoch** pins every required Reviewer and Gap execution, discovery lane, verifier, protocol capability, and capacity lease to one integration head SHA and immutable plan revision. A Coordinator owns the provider-native sub-agents; individual sub-agents do not write HLoop artifacts or close gates.

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

## Independent finding axes and follow-ups

The seven-axis disposition contract inherited from 0.5.2 remains canonical:

- `fact_status`: `confirmed`, `refuted`, or `insufficient_evidence`
- `origin`: `introduced`, `diff-expanded-pre-existing`, `unrelated-pre-existing`, or `unknown`
- `contract_relation`: `in_scope`, `outside_release`, or `ambiguous`
- `decision_requirement`: `none`, `spec`, or `user`
- `severity`: `P0`, `P1`, `P2`, or `P3`
- `disposition`: `fix_now`, `defer_follow_up`, `disable_feature`, `mark_experimental`, `user_decision`, `accepted_risk`, or `discard`
- `release_effect`: `blocking` or `non_blocking`

These axes are checked independently before a finding can become a task. A refuted finding must be discarded, and an introduced or diff-expanded in-scope regression cannot be hidden as a follow-up; in-scope P0/P1 regressions require immediate remediation, feature disablement, or a user decision. Non-blocking work is recorded with `hloop follow-up add`, whose `fu:v1:sha256:<64 hex>` key is derived from the stable semantic issue rather than the review title, target SHA, severity, or source line. Repeated discovery updates one follow-up instead of creating duplicates.

## Bounded convergence and manual final

For a new 0.5.3 loop, ordinary review waits for the current batch to close. The Manager creates an epoch plan, reserves shared capacity before each process start, records every required Reviewer and Gap execution, and closes the collection barrier before remediation. A same-SHA extra pass is a successor revision with inherited artifact digests; a changed target SHA starts a new epoch.

The default Reviewer protocol is `codex-review-multi-v2` with six externally planned lanes. Its pinned adapter must prove `externally-planned-v1`; a missing or mismatched companion record fails closed instead of spawning the companion's independent default Coordinator. Gap uses four requirement-audit lanes and one coverage challenge. The epoch-wide Agent budget includes Coordinators, lanes, verifiers, and Patch Reviewers, while an expired process remains quarantined until exit or forced abort is confirmed.

After collection, the Manager records all normalized candidates before approving one deterministic remediation batch. Classification conflicts stop approval. A convergence manifest that is incomplete, stale, or still contains verified actionable findings cannot pass; normal remediation and task-local Patch Review each have a two-round limit unless exact user authorization permits an additional round.

After convergence reaches zero verified actionable findings, the Manager prepares a separate manual-final plan and manifest at the same fixed target SHA. Both artifacts bind the configured independent/reuse policy, execution and source execution IDs, source artifact ref/digest, target, and pinned adapter. Independent mode must use an execution ID distinct from the complete pre-final source. Reuse mode must point to the exact complete fixed-target epoch Reviewer outcome; it cannot relabel a synthetic or duplicate source as independent work. Manual final recomputes those identities, lane completion, independent verification, shortfalls, release-scope snapshot, report presence, and the actionable-finding count. A self-reported zero count or a passing pre-final swarm alone is insufficient. `hloop review reopen --action <action> --user-input-id <id>` is the only supported recovery from exhausted convergence or incomplete/failed manual final; the atomic transition invalidates stale evidence and applies the selected remediation, scope, or abort policy.

Strict final review is armed only after the current batch closes, review triage is complete, no fix-task draft remains, and `hloop final-gates arm` pins the target SHA. Creating a new task disarms that record. This stability barrier prevents a full swarm and final gap audit from repeating after every incremental fix.
