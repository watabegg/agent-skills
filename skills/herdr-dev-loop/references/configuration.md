# Configuration Contract

herdr-dev-loop 0.5.3 requires Python 3.11 or later and uses the standard-library `tomllib` parser. A missing configuration file is valid and preserves the built-in defaults. Configuration files must not contain credentials, provider tokens, arbitrary setup commands, or other secrets.

## Discovery

`hloop config path --json` reports every candidate and the selected file. The first existing file in the following order is used; files are not merged across these locations.

1. `$HLOOP_CONFIG_HOME/config.toml`
2. `$XDG_CONFIG_HOME/herdr-dev-loop/config.toml`
3. `~/.config/herdr-dev-loop/config.toml`

Create a secret-free template with `hloop config init --json`. Use `--path <path>` for an explicit destination and `--force` only when intentionally replacing that file.

## Schema and scopes

The top-level `version` is currently `1`. `[defaults]` may set `max_workers`, `session_cleanup`, `specification_scout`, the role tables, `review`, and `audit`. Agent role settings use `provider`, `model`, and `effort`; Manager also accepts `identity_policy`. Reviewer additionally accepts `mode`, canonical `lane_count`, canonical `providers`, `protocol`, `required_capabilities`, and `coordinator`, `lane`, and `verifier` identity tables. `reviewer.providers` is retained as a provider-list leaf in the canonical resolved mapping. Gap uses the same coordinated identity tables with `mode` and `lane_count`. Only the legacy Reviewer count aliases `probe_count` and `probes_per_provider` are normalized to `reviewer.lane_count` within their source layer and omitted from the canonical resolved mapping.

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
4. loop snapshot
5. task override
6. explicit role-start override
7. participant override

`config explain` returns the final value and source for every leaf. `init` stores `config_source` and `resolved_config` in `STATE.json`; a later global configuration edit does not silently rewrite an existing loop. Use `config apply --dry-run` to preview changes, then `config apply --apply` to update an idle active loop's runtime-facing snapshot, including `review_policy`. A review-policy change invalidates review readiness, convergence, and manual-final evidence, which must be regenerated before finishing.

## Review policy defaults

The review table accepts the following independent policy fields:

```toml
[defaults.review]
cadence = "batch"
pre_final_protocol = "codex-review-multi-v2"
manual_final_protocol = "codex-review-multi-v2"
manual_final_execution = "independent"
max_fix_rounds = 2
scope_expansion_action = "follow_up"
final_required = "complete_zero_verified_actionable_findings"
```

`cadence = "batch"` defers ordinary review-gate opening until the current task batch is closed; it does not start a fixed-target Reviewer by itself. `merge-count` remains supported for explicitly configured or migrated legacy loops. `max_fix_rounds` is bounded from 0 through 2. Scope expansion may be routed to a follow-up, disable a feature, mark it experimental, or require a user decision; it cannot silently create a new in-scope fix task. `final_required` requires complete lanes, required independent verification, complete PLAN/MANIFEST evidence, and zero verified actionable findings for manual final certification. `manual_final_protocol` is intentionally narrower than the ordinary review protocol and currently accepts only the implemented `codex-review-multi-v2`; `native` is invalid for manual-final configuration and is never silently substituted. `manual_final_execution = "independent"` requires a separate final execution; `reuse_epoch_reviewer` is accepted only when the certification path can prove that the fixed-target epoch execution satisfies that policy.

## Review modes

`single` uses one provider and one discovery lane. `swarm` uses four to eight lanes on one provider. `dual` runs one lane for each of Codex and Claude. `dual-swarm` uses four to eight lanes per provider. Swarm counts are bounded by validation, and all providers in one review group are pinned to the same head SHA.

Provider, model, and effort remain separate values. A configured model is never replaced silently when capability probing returns unsupported or unknown; the recorded preflight and final argv explain the outcome.
