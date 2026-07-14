# Migration And Install Parity

herdr-dev-loop 0.5.0 uses `state_format_version: 3` and `schema_revision: 1`. The runtime reads format 2 and earlier format 3 revision 0 for migration, but mutating commands reject a future format or revision.

## Migrating an existing namespace

Use the same runtime and namespace for the dry run and apply steps:

```bash
$HLOOP version
$HLOOP migrate --dry-run
$HLOOP migrate --apply
$HLOOP status --raw-state
$HLOOP doctor
```

The dry run does not change the state. Apply creates a versioned backup below the namespace migration directory, preserves `run_id`, executes every declared revision in order, and updates the loop skill version. Migration refuses to run while a role or merge transaction is active or a role worktree is dirty. An unknown future revision remains readable through explicit inspection surfaces but cannot be mutated or downgraded.

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
python3 /home/watabegg/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$SKILL_DIR"

test ! -e "$CODEX_SKILL_DIR" || cp -a "$CODEX_SKILL_DIR" "${CODEX_SKILL_DIR}.backup-${STAMP}"
test ! -e "$CLAUDE_SKILL_DIR" || cp -a "$CLAUDE_SKILL_DIR" "${CLAUDE_SKILL_DIR}.backup-${STAMP}"

mkdir -p "$(dirname "$CODEX_SKILL_DIR")" "$(dirname "$CLAUDE_SKILL_DIR")"
rsync -a --delete "$SKILL_DIR/" "$CODEX_SKILL_DIR/"
rsync -a --delete "$SKILL_DIR/" "$CLAUDE_SKILL_DIR/"
```

Then verify byte parity, skill discovery, and runtime identity separately:

```bash
diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"
python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

Start a fresh Codex and Claude session after synchronization if an existing session cached skill discovery. In each client, invoke or list `herdr-dev-loop` and confirm that its first progress output reports 0.5.0.

## Rollback

Stop active loops before replacing an installed runtime. Move the failed installed directory aside, restore the matching timestamped backup, and run its `version`, `selftest`, and `doctor` commands. Do not use an older runtime to mutate a namespace already migrated beyond that runtime's supported schema revision.
