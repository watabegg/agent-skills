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

The shipped 0.5.3 record is available and release-ready. It pins the repository sibling `skills/codex-review-multi-v2` to this fork's immutable payload commit `de40e11747edde38684f2e75f94c773b6b086ccc`, HLoop adapter version `2.1.1`, and the exact payload digest. The fork derives from upstream commit `49fd08742f7666efa1fd4775989317d2da73f077` and adds the hardening recorded in `PROVENANCE.md`. Upstream does not publish semantic-version or HLoop capability metadata; the adapter version and capability manifest are HLoop-owned integration metadata. Do not derive any of these values from a mutable installed copy.

The capability manifest is an `external_review_protocol_adapter` record accepted by `hloop review epoch create --protocol-capability <path>`. `<path>` must resolve to the manifest inside the validated sibling distribution, `$(dirname "$SKILL_DIR")/codex-review-multi-v2/capabilities/externally-planned-v1.json`; a byte-identical copy elsewhere is rejected. The record binds `protocol`, `source`, `version`, `content_digest`, and `capabilities`. HLoop rejects a missing file, unknown field, unsupported protocol, unlabelled digest, or absent `externally-planned-v1`; it does not reduce lane count or launch a second Coordinator as an implicit fallback.

`sha256-tree-v1` hashes each regular payload file in lexicographic relative-path order. For each file, the digest input is the UTF-8 relative path, one NUL byte, the decimal byte length, one NUL byte, and the raw file bytes. The one capability manifest named by `release-dependencies.json` is excluded because it embeds the resulting digest; HLoop validates that manifest separately and requires its complete adapter record to equal the release pin. The runtime adapter source is the repository URL plus `#sha256-tree-v1=<digest>`; the separately stored Git commit is the fetchable distribution pin. This avoids a self-referential commit while keeping both the bytes and their repository snapshot exact. Generated Python cache files and VCS metadata are forbidden rather than ignored because Python may execute cached bytecode. Backups and local release evidence must live outside the installed skill directories. A cache, symlink, or other non-regular payload file fails validation.

## Repository, Codex, and Claude copies

The repository copy is the release source. Codex discovers `${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop`; Claude Code discovers `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/herdr-dev-loop`. The dependency record defines the corresponding companion destinations.

Stop active loops before replacing either installed runtime. Validate the repository copy, fix the release candidate SHA, and back up all four destinations before synchronization:

```bash
set -euo pipefail

SKILL_DIR="skills/herdr-dev-loop"
COMPANION_SOURCE="skills/codex-review-multi-v2"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
CLAUDE_SKILLS_ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
CODEX_SKILL_DIR="${CODEX_SKILLS_ROOT}/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_ROOT}/herdr-dev-loop"
CODEX_COMPANION_DIR="${CODEX_SKILLS_ROOT}/codex-review-multi-v2"
CLAUDE_COMPANION_DIR="${CLAUDE_SKILLS_ROOT}/codex-review-multi-v2"
CODEX_BACKUP_ROOT="$(dirname "$CODEX_SKILLS_ROOT")/skill-backups/codex/${STAMP}"
CLAUDE_BACKUP_ROOT="$(dirname "$CLAUDE_SKILLS_ROOT")/skill-backups/claude/${STAMP}"
CODEX_SKILL_BACKUP="${CODEX_BACKUP_ROOT}/herdr-dev-loop"
CLAUDE_SKILL_BACKUP="${CLAUDE_BACKUP_ROOT}/herdr-dev-loop"
CODEX_COMPANION_BACKUP="${CODEX_BACKUP_ROOT}/codex-review-multi-v2"
CLAUDE_COMPANION_BACKUP="${CLAUDE_BACKUP_ROOT}/codex-review-multi-v2"
CODEX_STAGE_ROOT="$(dirname "$CODEX_SKILLS_ROOT")/.hloop-install-stage-codex-${STAMP}"
CLAUDE_STAGE_ROOT="$(dirname "$CLAUDE_SKILLS_ROOT")/.hloop-install-stage-claude-${STAMP}"

PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("herdr-dev-loop 0.5.3 requires Python 3.11 or later")
import tomllib  # noqa: F401
PY
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/hloop" selftest
PYTHONDONTWRITEBYTECODE=1 python3 \
  "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" \
  "$SKILL_DIR"
PYTHONDONTWRITEBYTECODE=1 python3 -c 'import yaml; print(yaml.__version__)'
PYTHONDONTWRITEBYTECODE=1 python3 "$COMPANION_SOURCE/assets/validate_review.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$COMPANION_SOURCE/assets/render_review.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$COMPANION_SOURCE/assets/run.py" --selftest

PYTHONDONTWRITEBYTECODE=1 python3 - \
  "$SKILL_DIR" "$COMPANION_SOURCE" \
  "$CODEX_SKILLS_ROOT" "$CLAUDE_SKILLS_ROOT" \
  "$CODEX_BACKUP_ROOT" "$CLAUDE_BACKUP_ROOT" \
  "$CODEX_STAGE_ROOT" "$CLAUDE_STAGE_ROOT" \
  "$CODEX_SKILL_DIR" "$CLAUDE_SKILL_DIR" \
  "$CODEX_COMPANION_DIR" "$CLAUDE_COMPANION_DIR" <<'PY'
import os
import stat
import sys
from pathlib import Path

names = (
    "hloop_source", "companion_source",
    "codex_root", "claude_root",
    "codex_backup", "claude_backup",
    "codex_stage", "claude_stage",
    "codex_hloop", "claude_hloop",
    "codex_companion", "claude_companion",
)
paths = {name: Path(value).expanduser().absolute() for name, value in zip(names, sys.argv[1:])}

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

groups = (
    ("sources", ("hloop_source", "companion_source")),
    ("provider roots", ("codex_root", "claude_root")),
    ("backup roots", ("codex_backup", "claude_backup")),
    ("staging roots", ("codex_stage", "claude_stage")),
    ("destinations", ("codex_hloop", "claude_hloop", "codex_companion", "claude_companion")),
)
for label, members in groups:
    for index, left_name in enumerate(members):
        for right_name in members[index + 1:]:
            if overlaps(canonical[left_name], canonical[right_name]):
                raise SystemExit(f"unsafe {label} overlap: {left_name} / {right_name}")

for source_name in ("hloop_source", "companion_source"):
    for target_name in (
        "codex_root", "claude_root", "codex_backup", "claude_backup",
        "codex_stage", "claude_stage",
        "codex_hloop", "claude_hloop", "codex_companion", "claude_companion",
    ):
        if overlaps(canonical[source_name], canonical[target_name]):
            raise SystemExit(f"source/install overlap: {source_name} / {target_name}")
for backup_name in ("codex_backup", "claude_backup"):
    for root_name in ("codex_root", "claude_root"):
        if overlaps(canonical[backup_name], canonical[root_name]):
            raise SystemExit(f"backup/discovery overlap: {backup_name} / {root_name}")
for stage_name in ("codex_stage", "claude_stage"):
    for target_name in (
        "codex_root", "claude_root", "codex_backup", "claude_backup"
    ):
        if overlaps(canonical[stage_name], canonical[target_name]):
            raise SystemExit(f"staging/install overlap: {stage_name} / {target_name}")
PY

for SKILLS_ROOT in "$CODEX_SKILLS_ROOT" "$CLAUDE_SKILLS_ROOT"; do
  test ! -L "$SKILLS_ROOT"
  test ! -e "$SKILLS_ROOT" || test -d "$SKILLS_ROOT"
done
mkdir -p \
  "$CODEX_SKILLS_ROOT" "$CLAUDE_SKILLS_ROOT" \
  "$CODEX_BACKUP_ROOT" "$CLAUDE_BACKUP_ROOT"
for SOURCE in "$SKILL_DIR" "$COMPANION_SOURCE"; do
  test ! -L "$SOURCE"
  test -d "$SOURCE"
done
for DESTINATION in \
  "$CODEX_SKILL_DIR" "$CLAUDE_SKILL_DIR" \
  "$CODEX_COMPANION_DIR" "$CLAUDE_COMPANION_DIR"; do
  test ! -L "$DESTINATION"
  test ! -e "$DESTINATION" || test -d "$DESTINATION"
done
for BACKUP in \
  "$CODEX_SKILL_BACKUP" "$CLAUDE_SKILL_BACKUP" \
  "$CODEX_COMPANION_BACKUP" "$CLAUDE_COMPANION_BACKUP"; do
  test ! -e "$BACKUP"
done
test ! -e "$CODEX_STAGE_ROOT"
test ! -e "$CLAUDE_STAGE_ROOT"

mkdir -p \
  "$CODEX_STAGE_ROOT/new" "$CODEX_STAGE_ROOT/old" "$CODEX_STAGE_ROOT/failed" \
  "$CLAUDE_STAGE_ROOT/new" "$CLAUDE_STAGE_ROOT/old" "$CLAUDE_STAGE_ROOT/failed"
rsync -a --delete "$SKILL_DIR/" "$CODEX_STAGE_ROOT/new/herdr-dev-loop/"
rsync -a --delete "$COMPANION_SOURCE/" "$CODEX_STAGE_ROOT/new/codex-review-multi-v2/"
rsync -a --delete "$SKILL_DIR/" "$CLAUDE_STAGE_ROOT/new/herdr-dev-loop/"
rsync -a --delete "$COMPANION_SOURCE/" "$CLAUDE_STAGE_ROOT/new/codex-review-multi-v2/"

for STAGE_ROOT in "$CODEX_STAGE_ROOT" "$CLAUDE_STAGE_ROOT"; do
  diff -qr "$SKILL_DIR" "$STAGE_ROOT/new/herdr-dev-loop"
  diff -qr "$COMPANION_SOURCE" "$STAGE_ROOT/new/codex-review-multi-v2"
  PYTHONDONTWRITEBYTECODE=1 python3 \
    "$STAGE_ROOT/new/herdr-dev-loop/scripts/hloop" selftest
  PYTHONDONTWRITEBYTECODE=1 python3 \
    "$STAGE_ROOT/new/codex-review-multi-v2/assets/validate_review.py" --selftest
  PYTHONDONTWRITEBYTECODE=1 python3 \
    "$STAGE_ROOT/new/codex-review-multi-v2/assets/render_review.py" --selftest
  PYTHONDONTWRITEBYTECODE=1 python3 \
    "$STAGE_ROOT/new/codex-review-multi-v2/assets/run.py" --selftest
done

test ! -e "$CODEX_SKILL_DIR" || cp -a "$CODEX_SKILL_DIR" "$CODEX_SKILL_BACKUP"
test ! -e "$CLAUDE_SKILL_DIR" || cp -a "$CLAUDE_SKILL_DIR" "$CLAUDE_SKILL_BACKUP"
test ! -e "$CODEX_COMPANION_DIR" || cp -a "$CODEX_COMPANION_DIR" "$CODEX_COMPANION_BACKUP"
test ! -e "$CLAUDE_COMPANION_DIR" || cp -a "$CLAUDE_COMPANION_DIR" "$CLAUDE_COMPANION_BACKUP"

archive_legacy_discovery_backups() {
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
         -name 'codex-review-multi-v2.backup-*' -o \
         -name 'herdr-dev-loop.failed-*' -o \
         -name 'codex-review-multi-v2.failed-*' \) -print0
  )
}
archive_legacy_discovery_backups "$CODEX_SKILLS_ROOT" "$CODEX_BACKUP_ROOT"
archive_legacy_discovery_backups "$CLAUDE_SKILLS_ROOT" "$CLAUDE_BACKUP_ROOT"

DESTINATIONS=(
  "$CODEX_SKILL_DIR" "$CODEX_COMPANION_DIR"
  "$CLAUDE_SKILL_DIR" "$CLAUDE_COMPANION_DIR"
)
STAGED=(
  "$CODEX_STAGE_ROOT/new/herdr-dev-loop"
  "$CODEX_STAGE_ROOT/new/codex-review-multi-v2"
  "$CLAUDE_STAGE_ROOT/new/herdr-dev-loop"
  "$CLAUDE_STAGE_ROOT/new/codex-review-multi-v2"
)
OLD=(
  "$CODEX_STAGE_ROOT/old/herdr-dev-loop"
  "$CODEX_STAGE_ROOT/old/codex-review-multi-v2"
  "$CLAUDE_STAGE_ROOT/old/herdr-dev-loop"
  "$CLAUDE_STAGE_ROOT/old/codex-review-multi-v2"
)
FAILED=(
  "$CODEX_STAGE_ROOT/failed/herdr-dev-loop"
  "$CODEX_STAGE_ROOT/failed/codex-review-multi-v2"
  "$CLAUDE_STAGE_ROOT/failed/herdr-dev-loop"
  "$CLAUDE_STAGE_ROOT/failed/codex-review-multi-v2"
)
TOUCHED=(0 0 0 0)
HAD_ORIGINAL=(0 0 0 0)
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

test ! -L "$CODEX_SKILLS_ROOT"
test ! -L "$CLAUDE_SKILLS_ROOT"
for index in "${!DESTINATIONS[@]}"; do
  TOUCHED[$index]=1
  test ! -e "${DESTINATIONS[$index]}" || \
    mv "${DESTINATIONS[$index]}" "${OLD[$index]}"
  mv "${STAGED[$index]}" "${DESTINATIONS[$index]}"
done
trap - ERR INT TERM

mv "$CODEX_STAGE_ROOT" "$CODEX_BACKUP_ROOT/install-transaction"
mv "$CLAUDE_STAGE_ROOT" "$CLAUDE_BACKUP_ROOT/install-transaction"
```

Keeping backups below `skills/` causes Codex and Claude discovery to expose stale duplicate skills. The dedicated `skill-backups/<provider>/<STAMP>` roots are derived from the parent of each configured skills root, so they remain outside discovery even when `CLAUDE_CONFIG_DIR` is customized and cannot alias when both providers share a parent. Before any write, the recipe requires Python 3.11 or later and imports `tomllib` with the same `python3` executable used for HLoop. The Python path preflight rejects observed symlinks and all equality or ancestor/descendant collisions among sources, provider roots, backup roots, staging roots, and the four destinations. Payloads are synchronized and self-tested outside discovery first; live replacement uses same-filesystem directory renames and a failure trap that restores every touched original while retaining failed bytes as evidence. Do not run the recipe concurrently with another same-UID process that mutates provider roots; HLoop treats same-UID agents as trusted collaborators, not a hostile isolation boundary. The archive step moves the known legacy `*.backup-*` and `*.failed-*` HLoop/companion directory families and refuses a destination collision. Existing timestamp backup, staging, and non-directory destinations fail before replacement. Do not derive `COMPANION_SOURCE` or the release pin from an already installed mutable copy.

## Static and runtime parity

Static parity proves that the repository and installed HLoop distributions have the same files. It does not execute Python or either provider:

```bash
diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"
diff -qr "$COMPANION_SOURCE" "$CODEX_COMPANION_DIR"
diff -qr "$COMPANION_SOURCE" "$CLAUDE_COMPANION_DIR"
```

Run identity and selftest from both installed HLoop copies without creating cache files:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_COMPANION_DIR/assets/validate_review.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_COMPANION_DIR/assets/render_review.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_COMPANION_DIR/assets/run.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_COMPANION_DIR/assets/validate_review.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_COMPANION_DIR/assets/render_review.py" --selftest
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_COMPANION_DIR/assets/run.py" --selftest
```

Each installed HLoop selftest resolves its sibling companion, computes `sha256-tree-v1`, and requires the capability manifest to match protocol, immutable source, exact adapter version, payload digest, and `externally-planned-v1`. A missing Claude copy, a different digest, a symlink, or a manifest that merely names the skill without the capability is a failed gate.

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
set -euo pipefail

CODEX_FAILED_ROOT="${CODEX_BACKUP_ROOT}/failed"
CLAUDE_FAILED_ROOT="${CLAUDE_BACKUP_ROOT}/failed"
mkdir -p "$CODEX_FAILED_ROOT" "$CLAUDE_FAILED_ROOT"
test ! -e "$CODEX_SKILL_DIR" || mv "$CODEX_SKILL_DIR" "$CODEX_FAILED_ROOT/herdr-dev-loop"
test ! -e "$CLAUDE_SKILL_DIR" || mv "$CLAUDE_SKILL_DIR" "$CLAUDE_FAILED_ROOT/herdr-dev-loop"
test ! -e "$CODEX_COMPANION_DIR" || mv "$CODEX_COMPANION_DIR" "$CODEX_FAILED_ROOT/codex-review-multi-v2"
test ! -e "$CLAUDE_COMPANION_DIR" || mv "$CLAUDE_COMPANION_DIR" "$CLAUDE_FAILED_ROOT/codex-review-multi-v2"

test ! -e "$CODEX_SKILL_BACKUP" || cp -a "$CODEX_SKILL_BACKUP" "$CODEX_SKILL_DIR"
test ! -e "$CLAUDE_SKILL_BACKUP" || cp -a "$CLAUDE_SKILL_BACKUP" "$CLAUDE_SKILL_DIR"
test ! -e "$CODEX_COMPANION_BACKUP" || cp -a "$CODEX_COMPANION_BACKUP" "$CODEX_COMPANION_DIR"
test ! -e "$CLAUDE_COMPANION_BACKUP" || cp -a "$CLAUDE_COMPANION_BACKUP" "$CLAUDE_COMPANION_DIR"
```

If one destination did not exist before installation, its backup is intentionally absent; leave that destination absent after moving the failed copy aside. Run each restored HLoop `version`, `selftest`, and `doctor` command. Verify both restored companion digests and capability manifests before resuming review. Do not use a restored pre-0.5.3 runtime to mutate a namespace already migrated to schema 3.3.
