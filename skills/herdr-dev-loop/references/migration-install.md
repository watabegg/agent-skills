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

The dry run performs no namespace write. Apply prepares a transaction containing the source and planned artifact digests, writes the versioned archive and prepared marker durably, then replaces every planned artifact atomically before committing the marker. It preserves `run_id`, legacy task/result contents, review protocol and certificate identity, release scope, follow-ups, accepted risk, user amendments, semantic ACK history, and handoff evidence. Migration never relabels an external-review plan or certificate as native.

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

## Optional external-review protocol

The shipped HLoop release has dependencies: []. It does not bundle or install a sibling review companion. Fresh ordinary review, pre-final, and manual-final use the HLoop Native Review Protocol with the canonical six-lane Reviewer topology. Native execution requires no companion.

external-review remains an explicit compatibility option. Configure it only when the exact distribution source, version, payload digest, and externally-planned-v1 capability can be pinned and verified. Install and maintain that adapter separately under its own distribution instructions; it is not part of the default HLoop install below. A missing or drifted pin blocks an external execution. Saved external plans and certificates remain bound to their protocol and identity: never fall back to native, relabel the plan, or reuse a certificate under another protocol.

The release dependency record is empty. Release install evidence covers HLoop parity and fresh-session discovery for Codex and Claude only; it does not require an adapter handshake.

## HLoop-only install

The repository copy is the release source. Codex discovers ${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop; Claude Code discovers ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/herdr-dev-loop. Backups and staging are placed outside both skills discovery roots. The recipe backs up only these two HLoop destinations.

Stop active loops before replacing an installed runtime. Set SKILL_DIR to the maintained source, then run:

```bash
set -euo pipefail

SKILL_DIR="skills/herdr-dev-loop"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_CONFIG_ROOT="${CODEX_HOME:-$HOME/.codex}"
CLAUDE_CONFIG_ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CODEX_SKILLS_ROOT="${CODEX_CONFIG_ROOT}/skills"
CLAUDE_SKILLS_ROOT="${CLAUDE_CONFIG_ROOT}/skills"
CODEX_SKILL_DIR="${CODEX_SKILLS_ROOT}/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_ROOT}/herdr-dev-loop"
CODEX_BACKUP_ROOT="${CODEX_CONFIG_ROOT}/skill-backups/codex/${STAMP}"
CLAUDE_BACKUP_ROOT="${CLAUDE_CONFIG_ROOT}/skill-backups/claude/${STAMP}"
CODEX_SKILL_BACKUP="${CODEX_BACKUP_ROOT}/herdr-dev-loop"
CLAUDE_SKILL_BACKUP="${CLAUDE_BACKUP_ROOT}/herdr-dev-loop"
CODEX_STAGE_ROOT="${CODEX_CONFIG_ROOT}/.hloop-install-stage-${STAMP}"
CLAUDE_STAGE_ROOT="${CLAUDE_CONFIG_ROOT}/.hloop-install-stage-${STAMP}"

PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("herdr-dev-loop 0.5.3 requires Python 3.11 or later")
import tomllib
PY
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/hloop" selftest

PYTHONDONTWRITEBYTECODE=1 python3 - \
  "$SKILL_DIR" "$CODEX_SKILLS_ROOT" "$CLAUDE_SKILLS_ROOT" \
  "$CODEX_BACKUP_ROOT" "$CLAUDE_BACKUP_ROOT" \
  "$CODEX_STAGE_ROOT" "$CLAUDE_STAGE_ROOT" \
  "$CODEX_SKILL_DIR" "$CLAUDE_SKILL_DIR" <<'PY'
import os
import stat
import sys
from pathlib import Path

names = (
    "hloop_source", "codex_root", "claude_root",
    "codex_backup", "claude_backup", "codex_stage", "claude_stage",
    "codex_destination", "claude_destination",
)
paths = {
    name: Path(value).expanduser().absolute()
    for name, value in zip(names, sys.argv[1:])
}

def reject_symlink_components(name, path):
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise SystemExit(f"unsafe symlink component for {name}: {current}")

for name, path in paths.items():
    reject_symlink_components(name, path)
canonical = {name: path.resolve(strict=False) for name, path in paths.items()}

def overlaps(left, right):
    return left == right or left in right.parents or right in left.parents

def require_disjoint(label, members):
    for index, left_name in enumerate(members):
        for right_name in members[index + 1:]:
            if overlaps(canonical[left_name], canonical[right_name]):
                raise SystemExit(f"unsafe {label} overlap: {left_name} / {right_name}")

# Roots, backups, and staging must be distinct and outside both discovery roots.
require_disjoint(
    "provider, backup, or staging paths",
    ("codex_root", "claude_root", "codex_backup", "claude_backup",
     "codex_stage", "claude_stage"),
)
require_disjoint("destinations", ("codex_destination", "claude_destination"))

if canonical["codex_destination"] != canonical["codex_root"] / "herdr-dev-loop":
    raise SystemExit("Codex destination does not match its skills root")
if canonical["claude_destination"] != canonical["claude_root"] / "herdr-dev-loop":
    raise SystemExit("Claude destination does not match its skills root")

for target_name in (
    "codex_root", "claude_root", "codex_backup", "claude_backup",
    "codex_stage", "claude_stage", "codex_destination", "claude_destination",
):
    if overlaps(canonical["hloop_source"], canonical[target_name]):
        raise SystemExit(f"source/install overlap: hloop_source / {target_name}")
PY

test ! -L "$SKILL_DIR"
test -d "$SKILL_DIR"
for SKILLS_ROOT in "$CODEX_SKILLS_ROOT" "$CLAUDE_SKILLS_ROOT"; do
  test ! -L "$SKILLS_ROOT"
  test ! -e "$SKILLS_ROOT" || test -d "$SKILLS_ROOT"
done
for DESTINATION in "$CODEX_SKILL_DIR" "$CLAUDE_SKILL_DIR"; do
  test ! -L "$DESTINATION"
  test ! -e "$DESTINATION" || test -d "$DESTINATION"
done
for PATH_TO_CREATE in \
  "$CODEX_BACKUP_ROOT" "$CLAUDE_BACKUP_ROOT" \
  "$CODEX_STAGE_ROOT" "$CLAUDE_STAGE_ROOT"; do
  test ! -e "$PATH_TO_CREATE"
done

mkdir -p "$CODEX_SKILLS_ROOT" "$CLAUDE_SKILLS_ROOT"
mkdir -p "$CODEX_BACKUP_ROOT" "$CLAUDE_BACKUP_ROOT"
mkdir -p \
  "$CODEX_STAGE_ROOT/new" "$CODEX_STAGE_ROOT/old" "$CODEX_STAGE_ROOT/failed" \
  "$CLAUDE_STAGE_ROOT/new" "$CLAUDE_STAGE_ROOT/old" "$CLAUDE_STAGE_ROOT/failed"

# Directory renames during installation must stay on the destination filesystem.
PYTHONDONTWRITEBYTECODE=1 python3 - \
  "$CODEX_SKILLS_ROOT" "$CODEX_CONFIG_ROOT" \
  "$CLAUDE_SKILLS_ROOT" "$CLAUDE_CONFIG_ROOT" <<'PY'
import os
import sys

for skills_root, config_root in zip(sys.argv[1::2], sys.argv[2::2]):
    if os.stat(skills_root).st_dev != os.stat(config_root).st_dev:
        raise SystemExit(f"staging and destination are on different filesystems: {skills_root}")
PY

rsync -a --delete "$SKILL_DIR/" "$CODEX_STAGE_ROOT/new/herdr-dev-loop/"
rsync -a --delete "$SKILL_DIR/" "$CLAUDE_STAGE_ROOT/new/herdr-dev-loop/"
for STAGE_ROOT in "$CODEX_STAGE_ROOT" "$CLAUDE_STAGE_ROOT"; do
  diff -qr "$SKILL_DIR" "$STAGE_ROOT/new/herdr-dev-loop"
  PYTHONDONTWRITEBYTECODE=1 python3 \
    "$STAGE_ROOT/new/herdr-dev-loop/scripts/hloop" selftest
done

test ! -e "$CODEX_SKILL_BACKUP"
test ! -e "$CLAUDE_SKILL_BACKUP"
test ! -e "$CODEX_BACKUP_ROOT/install-transaction"
test ! -e "$CLAUDE_BACKUP_ROOT/install-transaction"
test ! -e "$CODEX_SKILL_DIR" || cp -a "$CODEX_SKILL_DIR" "$CODEX_SKILL_BACKUP"
test ! -e "$CLAUDE_SKILL_DIR" || cp -a "$CLAUDE_SKILL_DIR" "$CLAUDE_SKILL_BACKUP"

archive_legacy_hloop_discovery_backups() {
  local skills_root="$1"
  local archive_root="$2/legacy-discovery"
  local legacy target
  mkdir -p "$archive_root"
  while IFS= read -r -d '' legacy; do
    target="$archive_root/$(basename "$legacy")"
    test ! -e "$target"
    mv "$legacy" "$target"
  done < <(
    find "$skills_root" -mindepth 1 -maxdepth 1 -type d \
      \( -name 'herdr-dev-loop.backup-*' -o \
         -name 'herdr-dev-loop.failed-*' \) -print0
  )
}
archive_legacy_hloop_discovery_backups "$CODEX_SKILLS_ROOT" "$CODEX_BACKUP_ROOT"
archive_legacy_hloop_discovery_backups "$CLAUDE_SKILLS_ROOT" "$CLAUDE_BACKUP_ROOT"

DESTINATIONS=("$CODEX_SKILL_DIR" "$CLAUDE_SKILL_DIR")
STAGED=(
  "$CODEX_STAGE_ROOT/new/herdr-dev-loop"
  "$CLAUDE_STAGE_ROOT/new/herdr-dev-loop"
)
OLD=(
  "$CODEX_STAGE_ROOT/old/herdr-dev-loop"
  "$CLAUDE_STAGE_ROOT/old/herdr-dev-loop"
)
FAILED=(
  "$CODEX_STAGE_ROOT/failed/herdr-dev-loop"
  "$CLAUDE_STAGE_ROOT/failed/herdr-dev-loop"
)
TOUCHED=(0 0)
HAD_ORIGINAL=(0 0)
for index in "${!DESTINATIONS[@]}"; do
  test ! -e "${DESTINATIONS[$index]}" || HAD_ORIGINAL[$index]=1
done

rollback_partial_install() {
  local status="${1:-1}"
  local index
  trap - ERR INT TERM
  set +e
  for ((index=${#DESTINATIONS[@]} - 1; index >= 0; index--)); do
    test "${TOUCHED[$index]}" = 1 || continue
    if test "${HAD_ORIGINAL[$index]}" = 1; then
      if test -e "${OLD[$index]}"; then
        test ! -e "${DESTINATIONS[$index]}" || \
          mv "${DESTINATIONS[$index]}" "${FAILED[$index]}"
        mv "${OLD[$index]}" "${DESTINATIONS[$index]}"
      fi
    else
      test ! -e "${DESTINATIONS[$index]}" || \
        mv "${DESTINATIONS[$index]}" "${FAILED[$index]}"
    fi
  done
  echo "install failed; original destinations restored; staged evidence retained" >&2
  exit "$status"
}
trap 'rollback_partial_install $?' ERR
trap 'rollback_partial_install 130' INT
trap 'rollback_partial_install 143' TERM

for index in "${!DESTINATIONS[@]}"; do
  TOUCHED[$index]=1
  test ! -e "${DESTINATIONS[$index]}" || \
    mv "${DESTINATIONS[$index]}" "${OLD[$index]}"
  mv "${STAGED[$index]}" "${DESTINATIONS[$index]}"
done
trap - ERR INT TERM

# Keep staged transaction copies below the outside-root backups.
mv "$CODEX_STAGE_ROOT" "$CODEX_BACKUP_ROOT/install-transaction"
mv "$CLAUDE_STAGE_ROOT" "$CLAUDE_BACKUP_ROOT/install-transaction"
```

Backups are below each provider configuration root, outside its skills discovery directory. Before replacement, the recipe moves only the known HLoop `herdr-dev-loop.backup-*` and `herdr-dev-loop.failed-*` directories into a timestamped `legacy-discovery` archive, keeping stale HLoop copies out of discovery. It does not touch other skill families. The Python preflight rejects symlink components, identical or overlapping provider roots, backup/staging collisions, and source/install overlap. Staging is checked to be on the same filesystem as each skills root so destination replacement uses directory renames. Both staged copies must match the source and pass hloop selftest before either installed copy is changed. If replacement fails, the trap restores every touched original and retains failed/staged bytes for inspection. The explicit backups and transaction directories are keyed by STAMP; do not run two installs with the same timestamp or concurrently mutate these provider roots.

## Static and runtime parity

Static parity compares only the maintained HLoop directory with its two installed copies:

```bash
diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
test ! -e "$CODEX_SKILL_DIR" || \
  PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
test ! -e "$CLAUDE_SKILL_DIR" || \
  PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

Static parity and selftest do not prove provider discovery. Start fresh Codex and Claude sessions after synchronization. In each session, verify that the provider discovers herdr-dev-loop and that hloop version reports the expected skill version before other work. Record the candidate SHA, provider, fresh session identity, reported version, parity result, and selftest result in local-only release evidence. The HLoop release gate requires both provider parities and both fresh-session handshakes; it does not require companion installation or adapter evidence.

## Rollback

Retain the exact STAMP and backup variables from installation. Move each current destination aside, then restore only its matching backup. If no backup exists, that destination did not exist before installation and should remain absent:

```bash
set -euo pipefail

CODEX_FAILED_ROOT="$CODEX_BACKUP_ROOT/failed"
CLAUDE_FAILED_ROOT="$CLAUDE_BACKUP_ROOT/failed"
mkdir -p "$CODEX_FAILED_ROOT" "$CLAUDE_FAILED_ROOT"

test ! -e "$CODEX_SKILL_DIR" || mv "$CODEX_SKILL_DIR" "$CODEX_FAILED_ROOT/herdr-dev-loop"
test ! -e "$CLAUDE_SKILL_DIR" || mv "$CLAUDE_SKILL_DIR" "$CLAUDE_FAILED_ROOT/herdr-dev-loop"
test ! -e "$CODEX_SKILL_BACKUP" || cp -a "$CODEX_SKILL_BACKUP" "$CODEX_SKILL_DIR"
test ! -e "$CLAUDE_SKILL_BACKUP" || cp -a "$CLAUDE_SKILL_BACKUP" "$CLAUDE_SKILL_DIR"

PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

A pre-0.5.3 runtime must not mutate a namespace already migrated to schema 3.3.
