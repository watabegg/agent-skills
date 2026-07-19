# Migration And Install Parity

herdr-dev-loop 0.5.3 uses `state_format_version: 3` and `schema_revision: 3`. New task and result artifacts use `contract_schema_revision: 3`. Revision-2 task/result artifacts remain historical evidence; migration labels them without inventing revision-3 invariant, regression, self-review, residual-risk, or unrun-check evidence.

## Schema 3.3 migration

Use the same runtime, repository, and namespace for every migration action:

```bash
$HLOOP version
$HLOOP migrate --dry-run
$HLOOP migrate --apply
$HLOOP status --raw-state
$HLOOP doctor
```

The dry run performs no namespace write. Apply prepares a transaction containing the source and planned artifact digests, writes the versioned archive and prepared marker durably, then replaces every planned artifact atomically before committing the marker. It preserves `run_id`, legacy task/result contents, release scope, follow-ups, accepted risk, user amendments, semantic ACK history, and handoff evidence.

Migration stops when a Worker, Reviewer, Gap Auditor, Advisor, Scout, Liaison, Patch Reviewer, merge, or remediation transaction is nonterminal, unharvested, live, dirty, or cleanup-failed. A terminal harvested non-Worker role is migratable only when its lifecycle provenance is canonical and no pane or dirty worktree remains. Unknown status, malformed provenance, mixed revision state, digest mismatch, or an ambiguous remediation history requires a decision instead of being normalized heuristically.

If the process stops after the prepared marker, resume the recorded transaction:

```bash
$HLOOP migrate --resume
```

Resume verifies the archive, marker, source digest, planned output digest, and every already-replaced artifact before continuing. It does not construct a new plan from partially migrated files.

Rollback has two distinct eligibility windows:

```bash
$HLOOP migrate --rollback
```

1. **Prepared/partial recovery rollback**: a canonical `prepared` or `running` marker may begin rollback while some artifacts still contain source bytes and others already contain their recorded planned bytes. The archive, marker, source digest, planned digest, and each observed artifact must all match the saved transaction. An interrupted rollback leaves `rollback-prepared`; run `$HLOOP migrate --resume` to continue restoring the same archive. This recovery path does not require a committed marker.
2. **Committed pre-first-mutation rollback**: a canonical `committed` marker may begin rollback only while both `first_v053_mutation_at` and `first_v053_mutation_command` are present as empty boundary fields, proving that no 0.5.3 material command has run. Once either boundary records the first 0.5.3 mutation, rollback is permanently closed.

An unmarked mixed tree, a digest mismatch, a malformed mutation boundary, or bytes that match neither the saved source nor planned output is blocked rather than guessed. After the first 0.5.3 mutation, use the current runtime to repair or complete the namespace; an older runtime must not mutate schema 3.3 state.

Legacy `.ai/loop` is a different artifact family and remains ignored. Do not copy it into a namespaced loop by hand.

## Pinned companion dependency

`release-dependencies.json` is the release authority for external protocol code. The `codex-review-multi-v2` entry fixes these values:

- distribution source and immutable distribution identity;
- exact companion version and minimum compatible version;
- `sha256-tree-v1` content digest;
- capability manifest path and required `externally-planned-v1` capability;
- Codex and Claude install destinations.

An unavailable record uses `release_ready: false`, `availability: unavailable`, and null identity fields. That state is a release blocker, not a wildcard. It must not be replaced with values inferred from a mutable installed copy. Publication becomes eligible only after an immutable compatible distribution supplies every identity field and the record changes atomically to `availability: available` and `release_ready: true`.

The capability manifest is an `external_review_protocol_adapter` record accepted by `hloop review epoch create --protocol-capability <path>`. The record binds `protocol`, `source`, `version`, `content_digest`, and `capabilities`. HLoop rejects a missing file, unknown field, unsupported protocol, unlabelled digest, or absent `externally-planned-v1`; it does not reduce lane count or launch a second Coordinator as an implicit fallback.

`sha256-tree-v1` hashes each regular distribution file in lexicographic relative-path order. For each file, the digest input is the UTF-8 relative path, one NUL byte, the decimal byte length, one NUL byte, and the raw file bytes. Generated cache files, backups, VCS metadata, and local evidence are not part of the distribution tree. A release producer computes the digest before installation and records the same labelled digest in both the dependency record and capability manifest.

## Repository, Codex, and Claude copies

The repository copy is the release source. Codex discovers `${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop`; Claude Code discovers `${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop`. The dependency record defines the corresponding companion destinations.

Stop active loops before replacing either installed runtime. Validate the repository copy, fix the release candidate SHA, and back up all four destinations before synchronization:

```bash
SKILL_DIR="skills/herdr-dev-loop"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop"
CODEX_COMPANION_DIR="${CODEX_HOME:-$HOME/.codex}/skills/codex-review-multi-v2"
CLAUDE_COMPANION_DIR="${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/codex-review-multi-v2"
CODEX_SKILL_BACKUP="${CODEX_SKILL_DIR}.backup-${STAMP}"
CLAUDE_SKILL_BACKUP="${CLAUDE_SKILL_DIR}.backup-${STAMP}"
CODEX_COMPANION_BACKUP="${CODEX_COMPANION_DIR}.backup-${STAMP}"
CLAUDE_COMPANION_BACKUP="${CLAUDE_COMPANION_DIR}.backup-${STAMP}"

PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/hloop" selftest
PYTHONDONTWRITEBYTECODE=1 python3 \
  "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" \
  "$SKILL_DIR"

test ! -e "$CODEX_SKILL_DIR" || cp -a "$CODEX_SKILL_DIR" "$CODEX_SKILL_BACKUP"
test ! -e "$CLAUDE_SKILL_DIR" || cp -a "$CLAUDE_SKILL_DIR" "$CLAUDE_SKILL_BACKUP"
test ! -e "$CODEX_COMPANION_DIR" || cp -a "$CODEX_COMPANION_DIR" "$CODEX_COMPANION_BACKUP"
test ! -e "$CLAUDE_COMPANION_DIR" || cp -a "$CLAUDE_COMPANION_DIR" "$CLAUDE_COMPANION_BACKUP"
mkdir -p \
  "$(dirname "$CODEX_SKILL_DIR")" \
  "$(dirname "$CLAUDE_SKILL_DIR")" \
  "$(dirname "$CODEX_COMPANION_DIR")" \
  "$(dirname "$CLAUDE_COMPANION_DIR")"
rsync -a --delete "$SKILL_DIR/" "$CODEX_SKILL_DIR/"
rsync -a --delete "$SKILL_DIR/" "$CLAUDE_SKILL_DIR/"
```

Do not run companion synchronization while the shipped record says `release_ready: false`. Once an external immutable distribution is supplied and the record atomically becomes ready, set `COMPANION_SOURCE` to that verified, unpacked distribution and synchronize the same bytes to both declared destinations:

```bash
COMPANION_SOURCE="<verified-immutable-codex-review-multi-v2-directory>"
test -f "$COMPANION_SOURCE/capabilities/externally-planned-v1.json"
rsync -a --delete "$COMPANION_SOURCE/" "$CODEX_COMPANION_DIR/"
rsync -a --delete "$COMPANION_SOURCE/" "$CLAUDE_COMPANION_DIR/"
```

Do not derive `COMPANION_SOURCE` or the release pin from an already installed mutable copy.

## Static and runtime parity

Static parity proves that the repository and installed HLoop distributions have the same files. It does not execute Python or either provider:

```bash
diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"
```

Run identity and selftest from both installed HLoop copies without creating cache files:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

Then compute `sha256-tree-v1` for both installed companion directories and compare each value with the pin. Read each installed capability manifest and require an exact match for protocol, source, version, content digest, and `externally-planned-v1`. A missing Claude copy, a different digest, or a manifest that merely names the skill without the capability is a failed gate.

## Fresh-session handshake

File parity and Python selftest do not prove provider discovery. Start replacement Codex and Claude sessions after synchronization. Each fresh session must:

1. discover `herdr-dev-loop` and report `0.5.3` before other work;
2. discover the pinned `codex-review-multi-v2` distribution from the declared destination;
3. read its machine-readable capability manifest;
4. report the exact version and `sha256-tree-v1` digest from `release-dependencies.json`;
5. confirm `externally-planned-v1` without spawning review lanes.

Record the replacement-session identity, provider, reported version, digest, capability, and result in local-only release evidence keyed by the candidate SHA. Reusing the release implementation session is not fresh-session evidence. Static parity, a skipped provider check, or one provider passing does not satisfy the two-provider gate.

## Rollback

Stop active loops and retain the exact `STAMP` and four backup variables from installation. Move each failed destination aside and restore each matching backup explicitly; never select a backup with an unordered wildcard:

```bash
test ! -e "$CODEX_SKILL_DIR" || mv "$CODEX_SKILL_DIR" "${CODEX_SKILL_DIR}.failed-${STAMP}"
test ! -e "$CLAUDE_SKILL_DIR" || mv "$CLAUDE_SKILL_DIR" "${CLAUDE_SKILL_DIR}.failed-${STAMP}"
test ! -e "$CODEX_COMPANION_DIR" || mv "$CODEX_COMPANION_DIR" "${CODEX_COMPANION_DIR}.failed-${STAMP}"
test ! -e "$CLAUDE_COMPANION_DIR" || mv "$CLAUDE_COMPANION_DIR" "${CLAUDE_COMPANION_DIR}.failed-${STAMP}"

test ! -e "$CODEX_SKILL_BACKUP" || cp -a "$CODEX_SKILL_BACKUP" "$CODEX_SKILL_DIR"
test ! -e "$CLAUDE_SKILL_BACKUP" || cp -a "$CLAUDE_SKILL_BACKUP" "$CLAUDE_SKILL_DIR"
test ! -e "$CODEX_COMPANION_BACKUP" || cp -a "$CODEX_COMPANION_BACKUP" "$CODEX_COMPANION_DIR"
test ! -e "$CLAUDE_COMPANION_BACKUP" || cp -a "$CLAUDE_COMPANION_BACKUP" "$CLAUDE_COMPANION_DIR"
```

If one destination did not exist before installation, its backup is intentionally absent; leave that destination absent after moving the failed copy aside. Run each restored HLoop `version`, `selftest`, and `doctor` command. Verify both restored companion digests and capability manifests before resuming review. Do not use a restored pre-0.5.3 runtime to mutate a namespace already migrated to schema 3.3.
