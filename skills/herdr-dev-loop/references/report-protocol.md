# Agent Report And Manager Wake Contract

herdr-dev-loop 0.5.0 treats role progress as structured input to the Manager. Reports do not prove completion: harvest still verifies the artifact, target SHA, write scope, and validation evidence.

## Report types

- **`ack`**: confirms the understood goal, scope, acceptance criteria, and approach before material edits. It requires Manager attention.
- **`milestone`**: records a meaningful state change and current risks. It is inbox-only by default.
- **`attention`**: carries impact, attempted actions, options, recommendation, and blocked scope. It requires Manager attention and is never coalesced as an ordinary milestone.
- **`completion`**: points to the artifact, head SHA, validation results, residual risks, and handoff. It may wake the Manager, but it does not close a task or gate by itself.

Every report is bound to `run_id`, role and attempt identity, task contract digest, stage, summary, next action, evidence references, timestamp, and a client event UUID. The broker assigns the monotonic sequence. Reusing the same event ID with different content is rejected.

Every long-running Worker, Reviewer, Gap Auditor, and Advisor participant receives a registered report token in its rendered prompt. Reports from an unknown role, revoked role, stale attempt, mismatched contract digest, or wrong token fail closed. The fallback spool is local-only and carries the same private authentication envelope; replay reauthenticates it before accepting the event.

## Sending a semantic report

The role submits reports through the repository-local helper. The following ACK is the minimum material-edit barrier for a Worker:

```bash
$HLOOP agent report \
  --role-id T001 --attempt-id T001-A001 \
  --type ack --stage planning \
  --summary '契約と実装範囲を確認した' \
  --understood-goal '対象機能を契約どおり実装する' \
  --scope 'src/feature/**' \
  --acceptance '対象テストが通る' \
  --approach '既存の境界を保った最小変更' \
  --next 'material editを開始する'
```

After sending the initial ACK, the role enters the semantic ACK barrier and must not begin material work. The Manager approves or rejects the newest authenticated ACK with `hloop agent ack resolve <role-id> --decision approve|reject|timeout --reason <text>`. Reject and timeout remain blocking; approval then requires a newer corrected ACK event. The same rule applies when a later Manager message changes goal, scope, acceptance, or public behavior.

`hloop task update` applies a stronger form of the same rule to a running Worker. It hashes the updated task artifact, rebinds the active broker identity to that digest without rotating the attempt token, and replaces the prior barrier with a `task-contract` barrier. Only an authenticated ACK carrying the new digest and a sequence newer than the prior ACK can be approved. The canonical Manager state is authoritative for finalize, harvest, and merge, even when the Worker's local-only startup snapshot is older.

If the broker store is unavailable, the client atomically writes the event to the run-specific fallback spool. `hloop broker recover` replays valid events idempotently. Invalid, stale-run, or digest-conflicting events fail closed.

## Event-driven Manager

The Manager first drains durable input instead of repeatedly reading every pane:

```bash
$HLOOP inbox list
$HLOOP manager next
```

When no event needs action, the Manager registers a wake lease and waits:

```bash
$HLOOP manager sleep --ttl-seconds 3600
```

The lease is bound to the run, Manager session, pane, generation, and expiry. Registration and actionable-event inspection share the broker transaction, closing the lost-wake window between checking the inbox and sleeping. `manager sleep` is a foreground blocking operation and returns only for an ACK, attention or completion report, a Herdr fallback signal, or timeout. Those actionable reports signal the supervisor socket after the durable broker transaction commits; milestones remain inbox-only.

Wake delivery and inbox acknowledgement are separate. A stale-generation wake remains unprocessed, and an unacknowledged event is projected again when a fresh lease is registered. The Manager explicitly acknowledges the stable event ID exactly once:

```bash
$HLOOP inbox ack <event-id>
```

Duplicate wakes are harmless only when the Manager uses the event ID and lease generation. A role's free-form output must not be forwarded as a Manager prompt; wake messages contain fixed identifiers and the command needed to inspect the durable event.

Manager messages durably distinguish `delivered`, `acknowledged`, `applied`, `undelivered`, `unknown`, and `superseded`. Record a transport ACK or application result with `hloop message resolve <role-id> <message-id> --status acknowledged|applied`; `applied` requires `--result`. Resolve an ambiguous delivery explicitly instead of auto-resending it. `hloop message drain` retries only `undelivered` messages and never `unknown` messages.

## Recovery and privacy

`hloop broker status` reports event, inbox, spool, and owner counts. `hloop broker recover` is the supported recovery path after an interrupted broker. Broker databases, sockets, spool files, raw inputs, inbox records, tokens, and process metadata are local-only artifacts. They must not enter `branch-history` checkpoints, product commits, public test fixtures, or release bundles.

Herdr pane inspection remains a fallback for a role that exits, crashes, or never sends a report. It is not a substitute for the structured report protocol and must not become periodic progress polling.
