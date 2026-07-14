# herdr-dev-loop 0.5.0 release checklist

## Release identity

- Skill version: `0.5.0`
- State format: `3`
- Schema revision: `1`
- Minimum Python: `3.11`
- Legacy `.ai/loop`: ignored

## User-visible changes

- Hierarchical `config.toml` with repo and explicit cwd scopes, source explanation, and initialization snapshots
- Append-only attempt identity, merge transaction diagnostics, final-gate stability, done-target drift detection, and format 2 migration
- Redacted user input capture, accepted requirements, evidence-gated progress, scoped decisions, and requirement-oriented outcomes
- Structured `ack`, `milestone`, `attention`, and `completion` reports with durable broker inbox, fallback spool, and Manager wake lease
- `single`, `swarm`, `dual`, and `dual-swarm` review planning with semantic finding normalization and bounded independent verification
- Synthetic and provider E2E runners that emit machine-readable release evidence

## Required local validation

Run every command from the repository root and preserve the JSON output from both E2E runners.

```bash
python3 -m py_compile skills/herdr-dev-loop/scripts/hloop \
  skills/herdr-dev-loop/tests/run_synthetic_e2e.py \
  skills/herdr-dev-loop/tests/run_provider_e2e.py
python3 -m unittest discover -s skills/herdr-dev-loop/tests -v
python3 skills/herdr-dev-loop/scripts/hloop version --json
python3 skills/herdr-dev-loop/scripts/hloop selftest
python3 /home/watabegg/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/herdr-dev-loop
python3 skills/herdr-dev-loop/tests/run_synthetic_e2e.py --json
```

Run provider E2E with an authenticated disposable session:

```bash
python3 skills/herdr-dev-loop/tests/run_provider_e2e.py --provider codex --json
python3 skills/herdr-dev-loop/tests/run_provider_e2e.py --provider claude --json
```

When a provider credential or session is unavailable, record an explicit safe skip instead of treating the provider run as passed:

```bash
python3 skills/herdr-dev-loop/tests/run_provider_e2e.py \
  --provider codex --allow-skip \
  --skip-reason 'authenticated Codex session unavailable in release environment' \
  --json
```

A skipped provider scenario is evidence of non-execution, not release approval. Codex and Claude live runs remain separate release checklist items.

## Install parity

Follow [Migration And Install Parity](../references/migration-install.md) for backup, synchronization, discovery, and rollback. Record these checks independently:

- repository to Codex `diff -qr`
- repository to Claude Code `diff -qr`
- repository, Codex, and Claude `hloop version --json`
- repository, Codex, and Claude `selftest`
- Codex skill discovery in a fresh session
- Claude Code skill discovery in a fresh session

## Evidence table

| Gate | Evidence | Result |
|---|---|---|
| Version and Python compile | command output | pending |
| Unit tests | unittest output | pending |
| Skill selftest | command output | pending |
| Skill validator | command output | pending |
| Synthetic E2E | structured JSON | pending |
| Codex provider E2E | structured JSON, live or explicit skipped | pending |
| Claude provider E2E | structured JSON, live or explicit skipped | pending |
| Codex install parity and discovery | diff, version, selftest | pending |
| Claude install parity and discovery | diff, version, selftest | pending |

Do not replace `pending` with `passed` merely because the implementation exists. Each release environment records its own evidence.
