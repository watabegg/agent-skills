# Configuration Contract

herdr-dev-loop 0.5.2 requires Python 3.11 or later and uses the standard-library `tomllib` parser. A missing configuration file is valid and preserves the built-in defaults. Configuration files must not contain credentials, provider tokens, arbitrary setup commands, or other secrets.

## Discovery

`hloop config path --json` reports every candidate and the selected file. The first existing file in the following order is used; files are not merged across these locations.

1. `$HLOOP_CONFIG_HOME/config.toml`
2. `$XDG_CONFIG_HOME/herdr-dev-loop/config.toml`
3. `~/.config/herdr-dev-loop/config.toml`

Create a secret-free template with `hloop config init --json`. Use `--path <path>` for an explicit destination and `--force` only when intentionally replacing that file.

## Schema and scopes

The top-level `version` is currently `1`. `[defaults]` may set `max_workers`, `session_cleanup`, `[defaults.worker]`, and `[defaults.reviewer]`. Worker settings are `provider`, `model`, and `effort`. Reviewer settings add `mode`, `probe_count`, `providers`, and `probes_per_provider`.

Each `[[scope]]` requires an absolute path or a `~`-prefixed path. The default `match = "repo"` compares the canonical repository root. `match = "cwd"` must be explicit and compares the invocation directory. Paths are expanded, symlinks are resolved, and matching scopes are applied from the shallowest ancestor to the deepest ancestor. Defining the same canonical path and match kind twice is an error. Review policy values are snapshotted into `STATE.json` when a loop is initialized; changing global configuration later does not alter an existing loop.

The complete annotated example is [`examples/config.toml`](../examples/config.toml). Validate it or an installed copy before starting a loop:

```bash
hloop config validate --path skills/herdr-dev-loop/examples/config.toml --json
hloop config explain --repo /absolute/path/to/repo --json
```

## Precedence and snapshots

Values resolve from lowest to highest precedence in this order:

1. built-in defaults
2. configuration `[defaults]`
3. matching `[[scope]]` entries from shallow to deep
4. task override
5. explicit role-start override

`config explain` returns the final value and source for every leaf. `init` stores `config_source` and `resolved_config` in `STATE.json`; a later global configuration edit does not silently rewrite an existing loop. The 0.5.2 CLI does not expose an in-place `config apply` command. To change an active loop, use the supported task or role-specific update command, or initialize a new namespace after reviewing `config explain`.

## Review policy defaults

The review table accepts the following independent policy fields:

```toml
[defaults.review]
cadence = "batch"
pre_final_protocol = "codex-review-multi-v2"
manual_final_protocol = "codex-review-multi-v2"
max_fix_rounds = 2
scope_expansion_action = "follow_up"
final_required = "complete_zero_verified_actionable_findings"
lane_count = "auto"
```

`cadence = "batch"` defers ordinary review-gate opening until the current task batch is closed; it does not start a fixed-target Reviewer by itself. `merge-count` remains supported for explicitly configured or migrated legacy loops. `max_fix_rounds` is bounded from 0 through 2. Scope expansion may be routed to a follow-up, disable a feature, mark it experimental, or require a user decision; it cannot silently create a new in-scope fix task. `final_required` requires complete lanes, required independent verification, complete PLAN/MANIFEST evidence, and zero verified actionable findings for manual final certification.

## Review modes

`single` uses one provider and one discovery lane. `swarm` uses four to eight lanes on one provider. `dual` runs one lane for each of Codex and Claude. `dual-swarm` uses four to eight lanes per provider. Swarm counts are bounded by validation, and all providers in one review group are pinned to the same head SHA.

Provider, model, and effort remain separate values. A configured model is never replaced silently when capability probing returns unsupported or unknown; the recorded preflight and final argv explain the outcome.
