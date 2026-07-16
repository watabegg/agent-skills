# Migration And Install Parity

herdr-dev-loop 0.5.2 uses `state_format_version: 3` and `schema_revision: 2`. The runtime reads format 1/2 and format 3 revision 0/1 for migration, but mutating commands reject a future format or revision.

## Migrating an existing namespace

Use the same runtime and namespace for the dry run and apply steps:

```bash
$HLOOP version
$HLOOP migrate --dry-run
$HLOOP migrate --apply
$HLOOP status --raw-state
$HLOOP doctor
```

The dry run does not change the state. Apply creates a versioned backup below the namespace migration directory, preserves `run_id`, executes every declared revision in order, and updates the loop skill version. Migration refuses to run while a role or merge transaction is active or a role worktree is dirty. An unknown future revision remains readable through explicit inspection surfaces but cannot be mutated or downgraded. A migrated legacy loop keeps its stored merge-count cadence, marks pre-existing tasks `legacy-unclassified`, and does not acquire the new manual-final requirement implicitly.

Legacy `.ai/loop` is a different artifact family and remains ignored. Do not copy it into the namespaced format by hand.

## Repository, Codex, and Claude copies

The repository copy is the release source. Codex discovers the installed skill at `${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop`. Claude Code discovers the personal skill at `${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop`.

Before synchronization, validate the repository copy and back up each existing destination. The timestamped backup makes rollback independent of Git state.

```bash
SKILL_DIR="skills/herdr-dev-loop"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop"

python3 "$SKILL_DIR/scripts/hloop" selftest
QUICK_VALIDATE="$(find "${CODEX_HOME:-$HOME/.codex}" "$HOME/.claude" -iname quick_validate.py 2>/dev/null | head -n1)"
test -n "$QUICK_VALIDATE" || { echo "quick_validate.py not found under the Codex or Claude skill-creator install" >&2; exit 1; }
python3 "$QUICK_VALIDATE" "$SKILL_DIR"

test ! -e "$CODEX_SKILL_DIR" || cp -a "$CODEX_SKILL_DIR" "${CODEX_SKILL_DIR}.backup-${STAMP}"
test ! -e "$CLAUDE_SKILL_DIR" || cp -a "$CLAUDE_SKILL_DIR" "${CLAUDE_SKILL_DIR}.backup-${STAMP}"

mkdir -p "$(dirname "$CODEX_SKILL_DIR")" "$(dirname "$CLAUDE_SKILL_DIR")"
rsync -a --delete "$SKILL_DIR/" "$CODEX_SKILL_DIR/"
rsync -a --delete "$SKILL_DIR/" "$CLAUDE_SKILL_DIR/"
```

First verify static byte parity. This proves that the distributed files match; it does not execute a provider or the Python runtime:

```bash
diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"
```

Then verify runtime identity and Python selftest for each installed copy. These checks execute the skill's Python code, but they are not Codex or Claude live-provider E2E:

```bash
python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

For an ordinary distribution, start fresh Codex and Claude sessions after synchronization if an existing session cached skill discovery, then confirm that both clients report 0.5.2. Fresh provider discovery is a separate live-provider check; do not infer success from file parity or Python selftest. The 0.5.2 repository task does not synchronize installed copies; perform that step only in the later distribution/release operation after the candidate SHA is fixed.

## Rollback

Stop active loops before replacing an installed runtime. Move the failed installed directory aside, restore the matching timestamped backup, and run its `version`, `selftest`, and `doctor` commands. Do not use an older runtime to mutate a namespace already migrated beyond that runtime's supported schema revision.
