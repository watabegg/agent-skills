# Agent Report And Manager Wake Contract

herdr-dev-loop 0.5.3 treats role progress as structured input to the Manager. Reports do not prove completion: candidate sealing, Patch Review, harvest, review-epoch collection, and final certification still verify the durable artifact, exact SHA, write scope, and validation evidence. A report cannot substitute for a complete manifest or fixed-target evidence.

## Report types

- **`ack`**: confirms the understood goal, scope, acceptance criteria, and approach before material edits. It requires Manager attention.
- **`milestone`**: records a meaningful state change and current risks. It is inbox-only by default.
- **`attention`**: carries impact, attempted actions, options, recommendation, and blocked scope. It requires Manager attention and is never coalesced as an ordinary milestone.
- **`completion`**: points to the artifact, head SHA, validation results, residual risks, and handoff. It may wake the Manager, but it does not close a task or gate by itself.

Every report is bound to `run_id`, role and attempt identity, task contract digest, stage, summary, next action, evidence references, timestamp, and a client event UUID. The broker assigns the monotonic sequence. Reusing the same event ID with different content is rejected.

`hloop agent report` accepts the report body as a schema-validated JSON object via `--file <path>` or `--stdin`, or as the individual `--type`/`--stage`/`--summary`/`...` flags kept as compatibility input; the two input styles are mutually exclusive per call. Before ever reaching the broker or the fallback spool, the client atomically persists the complete normalized client event in a `0600` role-local outbox keyed by run/role/attempt. For each new logical report, the caller generates a new shell-safe opaque key and passes it as `--invocation-id`. The key starts with an ASCII alphanumeric character and then contains only ASCII alphanumerics or `.`, `_`, `:`, `/`, `-`. Every retry of that same report reuses the same key, including when the prior process may have stopped after broker commit, after outbox confirmation, or immediately before the CLI success response. The first retained complete envelope for that invocation ID is reused with its original event ID, timestamp, and digest whether it is pending or confirmed. Reusing the key with different semantic content is a local idempotency conflict; a different key with identical content is a distinct event. `--invocation-id` and `--event-id` are mutually exclusive.

The outbox retains at most the newest 64 entries. Invocation idempotency therefore applies only while the original entry remains retained. Retry an uncertain report with the same key before sending a newer logical report; after retention eviction, the client can no longer distinguish that invocation and may create a new event. This is a bounded client retry contract, not a general exactly-once framework.

Compatibility behavior remains unchanged when `--invocation-id` is omitted. An implicit invocation without either ID reuses only an unconfirmed pending delivery with matching semantic fields. Broker acceptance or successful fallback spooling marks that exact envelope confirmed, so a later legacy implicit invocation with identical content receives a distinct event ID. An explicit `--event-id` retry reuses its retained envelope even after confirmation when the semantic digest matches. A pre-0.5.1 visible-ASCII invocation key remains retryable while its pending or confirmed entry is retained and the semantic content matches; it does not authorize creating a new grammar-invalid entry after eviction. Legacy entries do not block a different new shell-safe key. Confirmation and compaction preserve retained legacy keys verbatim.

Every long-running Worker, Reviewer, Gap Auditor, Advisor participant, Specification Scout, and Decision Liaison receives an attempt-scoped credential file path in its rendered prompt. Scout uses role ID `S001`; a Liaison for `DNNN` uses `L-DNNN`. The token itself is stored under the repository Git common directory in a local-only file owned by the current user with mode `0600`; it never appears in a repository prompt, checkpoint, state diagnostic, or provider command. Reports from an unknown role, revoked role, stale attempt, mismatched contract digest, unreadable or over-permissive credential file, or wrong token fail closed. The fallback spool is local-only and carries the same attempt-bound authentication envelope; replay reauthenticates it before accepting the event.

## Trust boundary

HLoop treats agents running under the same OS UID as trusted collaborators. The report credential prevents accidental misdelivery, stale-attempt reuse, and role-identity mix-ups. Mode `0600` protects the token from other OS users and unintended publication; it does not separate secrets from another process running as the same UID. HLoop does not provide cryptographic Manager authentication, malicious same-UID state-tamper resistance, OS-level write isolation, or a strong sandbox boundary.

Every subordinate launch command inherits `HLOOP_ROLE_CONTEXT=1`, `HLOOP_ROLE_ID`, `HLOOP_ROLE_ATTEMPT_ID`, and `HLOOP_MANAGER_REPO`. When that best-effort context is present, the Manager consumer commands `hloop inbox list|show|ack` and `hloop manager next|sleep` refuse to run. The preflight classifies a wrong role or stale attempt and tries to append the rejection to the existing Manager `JOURNAL.md`; rejection remains in force if that audit write fails. A same-UID process can unset or replace the environment marker, so this guard reduces accidental subordinate misuse and is not a security boundary.

A Decision Liaison's approved semantic ACK authorizes it to present the question; it is not user consent. The presentation turn must not send a `completion` report. The Liaison waits in the same pane for a later explicit user option selection or free-text answer and may report completion only after writing the provenance-valid response artifact. Recommendation text, silence, system or Manager messages, and ACK approval do not satisfy this report boundary. A premature completion report remains only a wake signal and cannot make harvest accept a response without explicit subsequent-user provenance.

## Sending a semantic report

The role submits reports through the repository-local helper. The blocking
exchange is the standard minimum material-edit barrier for a Worker:

```bash
$HLOOP agent ack exchange T001 \
  --attempt-id T001-A001 --run-id <run-id> \
  --task-contract-digest <sha256> \
  --report-credential-file /private/local-only/credential.json \
  --invocation-id T001-A001-ack-0001 \
  --stage planning \
  --summary '契約と実装範囲を確認した' \
  --understood-goal '対象機能を契約どおり実装する' \
  --scope 'src/feature/**' \
  --acceptance '対象テストが通る' \
  --approach '既存の境界を保った最小変更' \
  --next 'Manager decisionを待つ' \
  --timeout-seconds 900 --json
```

`exchange` appends the authenticated ACK, releases the repository lock while
waiting, and checks the exact run, role, attempt, contract digest, barrier
message, ACK event, approval availability, and completion-mode probe. On exact
approval it appends an idempotent authenticated application event, requires a
positive broker sequence, and remains blocked until the Manager consumer
applies that exact event ID, payload digest, attempt, contract digest, and ACK
binding to `approval_application`. Fallback-spool-only evidence never opens the
barrier. Reject, Manager timeout, supersede, wait timeout, or any identity drift
returns non-zero and never authorizes material work. Retrying one interrupted
exchange reuses its invocation ID; a corrected ACK after reject or timeout uses
a new invocation ID.

The Manager resolves the newest authenticated ACK with `hloop agent ack resolve
<role-id> --decision approve|reject|timeout --reason <text>`. Resolution durably
records `semantic_decision`, `approval_availability`, `approval_application`,
and `pane_notification` as separate projections. The default sends no pane
message. `--notify-pane` is an explicit advisory/debug option whose delivery
status cannot change decision, availability, or application state. Lifecycle
commands remain blocked after decision approval until the exact Manager-owned
application binding is `applied`. The same
barrier applies when a later Manager message changes goal, scope, acceptance,
or public behavior. This is an integration gate: finalize, harvest, and merge
still verify approved work, but the barrier is not an OS sandbox.

`agent report --type ack` and `agent ack status --apply` remain compatibility
surfaces. New role prompts use `agent ack exchange` so ACK and resume occur in
one provider process turn without pane input.

Use `hloop agent message S001 ... --contract-changing` or `hloop agent message L-DNNN ... --contract-changing` for Scout/Liaison contract changes. The common `agent ack resolve`, `agent abort`, `agent requeue`, inbox, message resolution, credential revocation, and Manager sleep paths recognize both role ID forms. Harvest and user-response cleanup remain blocked until the active barrier has an exact Manager-applied application binding.

`hloop task update` applies a stronger form of the same rule to a running Worker. It hashes the updated task artifact, rebinds the active broker identity to that digest without rotating the attempt token, and replaces the prior barrier with a `task-contract` barrier. Only an authenticated ACK carrying the new digest and a sequence newer than the prior ACK can be approved. The canonical Manager state is authoritative for finalize, harvest, and merge, even when the Worker's local-only startup snapshot is older.

If the broker store or SQLite storage is temporarily unavailable, the client atomically writes the event to the run-specific fallback spool. `hloop broker recover` replays valid events idempotently. Idempotency conflicts, authentication failures, invalid schemas, unsupported storage schemas, and other permanent semantic/integrity errors return non-zero and are never reported as successful fallback spooling.

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

The lease is bound to the run, Manager session, pane, generation, and expiry. Registration and actionable-event inspection share the broker transaction, closing the lost-wake window between checking the inbox and sleeping. `manager sleep` is a foreground blocking operation and returns only for an ACK, attention or completion report, a Herdr fallback signal, or timeout. It does not hold the repository transaction lock while blocked; after returning it reacquires that lock, reloads and schema-checks the latest `STATE.json`, and merges only the sleep-owned lease/result fields. Those actionable reports signal the supervisor socket after the durable broker transaction commits; milestones remain inbox-only.

Unread `milestone` reports that share the same run, role, attempt, stage, and summary as a later unread `milestone` are coalesced to that later report in the inbox projection Managers read (`inbox list`, `manager next`, and the pre-sleep unread check); the underlying append-only event log and inbox table are never mutated or pruned, and every `attention` report is always delivered individually, even when its content repeats an earlier one. Once any wake-eligible report (`ack`, `attention`, `completion`) is seen, `manager sleep` holds the return open for a short bounded window so a cross-role burst landing moments later is delivered as one batch instead of forcing a separate wake per event; every event that arrives is preserved either way, including across a Manager crash and restart of `manager sleep` itself.

Wake delivery and inbox acknowledgement are separate. A stale-generation wake remains unprocessed, and an unacknowledged event is projected again when a fresh lease is registered. The Manager explicitly acknowledges the stable event ID exactly once:

```bash
$HLOOP inbox ack <event-id>
```

Duplicate wakes are harmless only when the Manager uses the event ID and lease generation. A role's free-form output must not be forwarded as a Manager prompt; wake messages contain fixed identifiers and the command needed to inspect the durable event.

Manager messages durably distinguish `delivered`, `acknowledged`, `applied`, `undelivered`, `unknown`, and `superseded`. Record a transport ACK or application result with `hloop message resolve <role-id> <message-id> --status acknowledged|applied`; `applied` requires `--result`. Resolve an ambiguous delivery explicitly instead of auto-resending it. `hloop message drain` retries only `undelivered` messages and never `unknown` messages.

## Recovery and privacy

`hloop broker status` reports event, inbox, spool, and owner counts. `hloop broker recover` is the supported recovery path after an interrupted broker. The foreground supervisor accepts a runtime socket parent only when descriptor and `lstat` checks agree that it is a real current-user directory secured to mode `0700`; symlinks, ownership mismatches, replacement races, and permission-repair failures fail closed. Broker databases, sockets, spool files, credential files, raw inputs, inbox records, tokens, and process metadata are local-only artifacts. Credential files must remain `0600`; diagnostics may identify a credential path or permission problem but must not print the token. This permission protects against other OS users and unintended publication, not a malicious same-UID process. These artifacts must not enter `branch-history` checkpoints, product commits, public test fixtures, or release bundles.

Herdr pane inspection remains a fallback for a role that exits, crashes, or never sends a report. It is not a substitute for the structured report protocol and must not become periodic progress polling.

Scout and Liaison panes are included in the same fallback watches as the other long-running roles. `idle`, `blocked`, `done`, or `unknown` pane state wakes `manager sleep` when no authenticated report arrived; the Manager still inspects durable state and the role artifact before taking a lifecycle action.
