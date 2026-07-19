"""Hierarchical TOML config primitives for herdr-dev-loop 0.5.x.

Implements config file discovery, stdlib TOML loading, repo-default and
explicit cwd directory scopes with canonical symlink-safe matching,
precedence resolution with per-key provenance, layer-local legacy alias
normalization, schema/type validation, and Python capability checks.

This module is a pure-function primitives library. It does not know about
the hloop CLI, STATE.json, or PROFILE.md, and it performs no I/O beyond
reading the discovered config file.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CONFIG_FILENAME = "config.toml"
MIN_PYTHON = (3, 11)
SUPPORTED_CONFIG_VERSIONS = (1,)
SUPPORTED_MATCH_KINDS = ("repo", "cwd")
DEFAULT_MATCH_KIND = "repo"
SUPPORTED_AGENT_PROVIDERS = ("codex", "claude")
SUPPORTED_SESSION_CLEANUP_MODES = ("archive", "none", "delete")
SUPPORTED_REVIEW_MODES = ("single", "swarm", "dual", "dual-swarm")
SUPPORTED_SPECIFICATION_SCOUT_MODES = ("auto", "always", "off")
SUPPORTED_REVIEW_CADENCES = ("batch", "merge-count")
SUPPORTED_REVIEW_PROTOCOLS = ("native", "codex-review-multi-v2")
# The manual-final command currently has only one implemented protocol. Keep
# this separate from the ordinary review protocol set so an unsupported
# ``native`` value cannot be accepted and silently routed elsewhere.
SUPPORTED_MANUAL_FINAL_PROTOCOLS = ("codex-review-multi-v2",)
SUPPORTED_MANUAL_FINAL_EXECUTIONS = ("independent", "reuse_epoch_reviewer")
SUPPORTED_MANAGER_IDENTITY_POLICIES = ("strict", "warn-unavailable")
AGENT_IDENTITY_FIELDS = ("provider", "model", "effort")
AGENT_IDENTITY_STATUSES = (
    "attested",
    "requested-only",
    "unavailable",
    "mismatch",
)
SUPPORTED_SCOPE_EXPANSION_ACTIONS = (
    "follow_up",
    "disable_feature",
    "mark_experimental",
    "user_decision",
)
SUPPORTED_FINAL_REQUIREMENTS = ("complete_zero_verified_actionable_findings",)
MIN_REVIEW_PROBES = 4
MAX_REVIEW_PROBES = 8
MIN_REVIEW_LANES = 4
MAX_REVIEW_LANES = 8
MAX_REVIEW_FIX_ROUNDS = 2
MAX_PATCH_REVIEW_ROUNDS = 2
_REVIEW_POLICY_KEYS = (
    "cadence",
    "pre_final_protocol",
    "manual_final_protocol",
    "manual_final_execution",
    "max_fix_rounds",
    "scope_expansion_action",
    "final_required",
    # 0.5.2 compatibility alias.  Layer normalization moves this to
    # ``reviewer.lane_count`` before hierarchical merge.
    "lane_count",
)
REVIEW_POLICY_DEFAULTS = {
    "cadence": "batch",
    "pre_final_protocol": "codex-review-multi-v2",
    "manual_final_protocol": "codex-review-multi-v2",
    "max_fix_rounds": 2,
    "scope_expansion_action": "follow_up",
    "final_required": "complete_zero_verified_actionable_findings",
    "lane_count": "auto",
}
# 0.5.2 callers and the shipped 0.5.2 example compare
# ``REVIEW_POLICY_DEFAULTS`` byte-for-byte.  Keep that compatibility value
# stable while exposing the complete 0.5.3 defaults for new callers.
V053_REVIEW_POLICY_DEFAULTS = {
    key: value for key, value in REVIEW_POLICY_DEFAULTS.items() if key != "lane_count"
}
V053_REVIEW_POLICY_DEFAULTS["manual_final_execution"] = "independent"
# Public alias for callers that use the older constant naming convention.
DEFAULT_REVIEW_POLICY = REVIEW_POLICY_DEFAULTS
_KNOWN_TOP_LEVEL_KEYS = ("version", "defaults", "scope")
CONFIG_ROLE_NAMES = (
    "manager",
    "worker",
    "reviewer",
    "gap",
    "plan_gap",
    "patch_reviewer",
    "final_coordinator",
    "advisor",
)
COORDINATED_ROLE_NAMES = ("reviewer", "gap")
COORDINATOR_COMPONENT_NAMES = ("coordinator", "lane", "verifier")
_DEFAULT_KEYS = (
    "max_workers",
    "session_cleanup",
    "specification_scout",
    *CONFIG_ROLE_NAMES,
    "review",
    "audit",
)
_SCOPE_KEYS = ("path", "match", *_DEFAULT_KEYS)
_AGENT_IDENTITY_KEYS = ("provider", "model", "effort")
_MANAGER_ROLE_KEYS = (*_AGENT_IDENTITY_KEYS, "identity_policy")
_REVIEWER_ROLE_KEYS = (
    *_AGENT_IDENTITY_KEYS,
    "mode",
    "lane_count",
    "protocol",
    "required_capabilities",
    "providers",
    # 0.5.0--0.5.2 compatibility count aliases.  They are never retained in a
    # canonical resolved mapping.
    "probe_count",
    "probes_per_provider",
    *COORDINATOR_COMPONENT_NAMES,
)
_GAP_ROLE_KEYS = (
    *_AGENT_IDENTITY_KEYS,
    "mode",
    "lane_count",
    *COORDINATOR_COMPONENT_NAMES,
)
_AUDIT_KEYS = ("agent_budget", "max_patch_review_rounds_per_task")

# All config layers use this order.  ``participant-override`` is the
# participant-specific member of the highest start-override tier; when both
# are supplied its more specific value wins deterministically.
CONFIG_PRECEDENCE = (
    "built-in-default",
    "config-defaults",
    "matching-scope",
    "loop-snapshot",
    "task-override",
    "start-override",
    "participant-override",
)
_RUNTIME_OVERRIDE_SOURCES = frozenset(
    {"task-override", "start-override", "participant-override"}
)


def _identity_defaults(provider: str, model: str, effort: str) -> dict[str, str]:
    return {"provider": provider, "model": model, "effort": effort}


# Pure 0.5.3 defaults.  Runtime adoption and legacy migration are intentionally
# owned by the later central-CLI integration task; this constant lets those
# callers share one canonical shape without duplicating role defaults.
V053_BUILT_IN_CONFIG_DEFAULTS = {
    "max_workers": 3,
    "session_cleanup": "archive",
    "specification_scout": "auto",
    "manager": {
        **_identity_defaults("codex", "gpt-5.6-sol", "max"),
        "identity_policy": "warn-unavailable",
    },
    "worker": _identity_defaults("codex", "gpt-5.6-terra", "max"),
    "reviewer": {
        **_identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
        "mode": "swarm",
        "lane_count": 6,
        "protocol": "codex-review-multi-v2",
        "required_capabilities": ["externally-planned-v1"],
        "coordinator": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
        "lane": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
        "verifier": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
    },
    "review": dict(V053_REVIEW_POLICY_DEFAULTS),
    "gap": {
        **_identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
        "mode": "swarm",
        "lane_count": 4,
        "coordinator": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
        "lane": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
        "verifier": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
    },
    "plan_gap": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
    "patch_reviewer": _identity_defaults("codex", "gpt-5.6-sol", "xhigh"),
    "final_coordinator": _identity_defaults("codex", "gpt-5.6-sol", "max"),
    "advisor": _identity_defaults("codex", "gpt-5.6-luna", "max"),
    "audit": {
        "agent_budget": 12,
        "max_patch_review_rounds_per_task": 2,
    },
}


def _coordinator_leaf_paths(role: str) -> set[str]:
    return {
        f"{role}.{component}.{key}"
        for component in COORDINATOR_COMPONENT_NAMES
        for key in _AGENT_IDENTITY_KEYS
    }


def _canonical_config_leaf_paths() -> set[str]:
    """Derive resolved leaves from the validator's canonical key sets."""

    leaves = {"max_workers", "session_cleanup", "specification_scout"}
    simple_roles = set(CONFIG_ROLE_NAMES) - {"manager", "reviewer", "gap"}
    leaves.update(
        f"{role}.{key}" for role in simple_roles for key in _AGENT_IDENTITY_KEYS
    )
    leaves.update(f"manager.{key}" for key in _MANAGER_ROLE_KEYS)
    leaves.update(
        f"reviewer.{key}"
        for key in _REVIEWER_ROLE_KEYS
        if key not in {"probe_count", "probes_per_provider", *COORDINATOR_COMPONENT_NAMES}
    )
    leaves.update(
        f"gap.{key}" for key in _GAP_ROLE_KEYS if key not in COORDINATOR_COMPONENT_NAMES
    )
    leaves.update(
        f"review.{key}" for key in _REVIEW_POLICY_KEYS if key != "lane_count"
    )
    leaves.update(f"audit.{key}" for key in _AUDIT_KEYS)
    leaves.update(_coordinator_leaf_paths("reviewer"))
    leaves.update(_coordinator_leaf_paths("gap"))
    return leaves


# Canonical resolved-config schema leaves.  Structural TOML fields
# (``version``, ``scope.path``, and ``scope.match``) and migration aliases are
# deliberately excluded: they select layers but never appear in the resolved
# config snapshot classified by validation_identity.py.
CANONICAL_CONFIG_LEAF_PATHS = frozenset(_canonical_config_leaf_paths())
LEGACY_CONFIG_ALIAS_PATHS = frozenset(
    {
        "review.lane_count",
        "reviewer.probe_count",
        "reviewer.probes_per_provider",
    }
)


class ConfigError(Exception):
    """Base error for config discovery, parsing, or validation failures."""


class ConfigCapabilityError(ConfigError):
    """Raised when the running Python lacks required config capabilities."""


class ConfigValidationError(ConfigError):
    """Raised when parsed config content fails schema/type validation."""


@dataclasses.dataclass(frozen=True)
class AgentIdentityProjection:
    """Requested, observed, and attested identity without invented evidence.

    Provider launch configuration is a request, not an observation.  Callers
    must pass independently observed or provider-attested values explicitly;
    omitted values remain ``unavailable`` instead of being copied from the
    requested mapping.
    """

    requested: Mapping[str, str]
    observed: Mapping[str, str]
    attested: Mapping[str, str]
    status: str
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in AGENT_IDENTITY_STATUSES:
            raise ConfigValidationError(
                f"unsupported agent identity status: {self.status!r}"
            )

    @property
    def verified(self) -> bool:
        return self.status == "attested" and not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": dict(self.requested),
            "observed": dict(self.observed),
            "attested": dict(self.attested),
            "status": self.status,
            "verified": self.verified,
            "issues": list(self.issues),
        }


def _identity_mapping(value: Mapping[str, Any] | None, *, label: str) -> dict[str, str]:
    """Normalize only identity values actually supplied by one evidence source."""

    if value is None:
        return {field: "unavailable" for field in AGENT_IDENTITY_FIELDS}
    if not isinstance(value, Mapping):
        raise ConfigValidationError(f"{label} identity must be a mapping")
    unknown = sorted(set(value) - set(AGENT_IDENTITY_FIELDS))
    if unknown:
        raise ConfigValidationError(
            f"{label} identity has unknown field(s): {', '.join(unknown)}"
        )
    normalized: dict[str, str] = {}
    for field in AGENT_IDENTITY_FIELDS:
        raw = value.get(field)
        if raw is None or str(raw).strip() == "":
            normalized[field] = "unavailable"
            continue
        text = str(raw).strip()
        if "\n" in text or "\r" in text or "\0" in text:
            raise ConfigValidationError(
                f"{label} identity {field} must be a single-line string"
            )
        normalized[field] = text
    return normalized


def project_agent_identity(
    requested: Mapping[str, Any],
    *,
    observed: Mapping[str, Any] | None = None,
    attested: Mapping[str, Any] | None = None,
) -> AgentIdentityProjection:
    """Separate requested/observed/attested model identity fail-closed.

    An attestation is verified only when every attested field agrees with both
    the requested value and an independently observed value.  Partial or
    absent evidence stays explicit and can never be mistaken for a match.
    """

    requested_identity = _identity_mapping(requested, label="requested")
    if any(value == "unavailable" for value in requested_identity.values()):
        raise ConfigValidationError(
            "requested identity requires provider, model, and effort"
        )
    observed_identity = _identity_mapping(observed, label="observed")
    attested_identity = _identity_mapping(attested, label="attested")
    issues: list[str] = []
    for field in AGENT_IDENTITY_FIELDS:
        observed_value = observed_identity[field]
        attested_value = attested_identity[field]
        requested_value = requested_identity[field]
        request_constrains_value = requested_value != "auto"
        if (
            request_constrains_value
            and observed_value != "unavailable"
            and observed_value != requested_value
        ):
            issues.append(
                f"observed-{field}-mismatch:requested={requested_value!r}:"
                f"observed={observed_value!r}"
            )
        if (
            request_constrains_value
            and attested_value != "unavailable"
            and attested_value != requested_value
        ):
            issues.append(
                f"attested-{field}-mismatch:requested={requested_value!r}:"
                f"attested={attested_value!r}"
            )
        if (
            observed_value != "unavailable"
            and attested_value != "unavailable"
            and observed_value != attested_value
        ):
            issues.append(
                f"observed-attested-{field}-mismatch:observed={observed_value!r}:"
                f"attested={attested_value!r}"
            )
    if issues:
        status = "mismatch"
    elif all(value != "unavailable" for value in attested_identity.values()) and all(
        value != "unavailable" for value in observed_identity.values()
    ):
        status = "attested"
    elif all(value == "unavailable" for value in observed_identity.values()) and all(
        value == "unavailable" for value in attested_identity.values()
    ):
        status = "requested-only"
    else:
        status = "unavailable"
    return AgentIdentityProjection(
        requested=requested_identity,
        observed=observed_identity,
        attested=attested_identity,
        status=status,
        issues=tuple(issues),
    )


# Concise compatibility spelling for central runtime callers.
agent_identity_projection = project_agent_identity


def check_python_capability(version_info: Any = None) -> None:
    """Raise ConfigCapabilityError unless stdlib tomllib is usable.

    ``version_info`` may be injected for testing; it defaults to the
    running interpreter's ``sys.version_info``.
    """
    version_info = version_info if version_info is not None else sys.version_info
    if (version_info.major, version_info.minor) < MIN_PYTHON:
        raise ConfigCapabilityError(
            "herdr-dev-loop config requires Python "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ for stdlib tomllib; "
            f"running {version_info.major}.{version_info.minor}"
        )
    try:
        import tomllib  # noqa: F401
    except ImportError as exc:
        raise ConfigCapabilityError("stdlib tomllib module is unavailable") from exc


def _load_tomllib():
    check_python_capability()
    import tomllib

    return tomllib


@dataclasses.dataclass(frozen=True)
class ConfigCandidate:
    """A candidate config file location, in discovery priority order."""

    source: str
    path: Path


def discover_config_candidates(env: Mapping[str, str] | None = None) -> list[ConfigCandidate]:
    """Return candidate config paths in priority order (highest first).

    Order: $HLOOP_CONFIG_HOME/config.toml, then
    $XDG_CONFIG_HOME/herdr-dev-loop/config.toml, then
    ~/.config/herdr-dev-loop/config.toml.
    """
    env = env if env is not None else os.environ
    candidates: list[ConfigCandidate] = []

    hloop_home = env.get("HLOOP_CONFIG_HOME")
    if hloop_home:
        candidates.append(ConfigCandidate("HLOOP_CONFIG_HOME", Path(hloop_home).expanduser() / CONFIG_FILENAME))

    xdg_home = env.get("XDG_CONFIG_HOME")
    if xdg_home:
        candidates.append(
            ConfigCandidate(
                "XDG_CONFIG_HOME", Path(xdg_home).expanduser() / "herdr-dev-loop" / CONFIG_FILENAME
            )
        )

    home = Path(env.get("HOME") or Path.home())
    candidates.append(ConfigCandidate("default", home / ".config" / "herdr-dev-loop" / CONFIG_FILENAME))

    return candidates


def find_config_file(env: Mapping[str, str] | None = None) -> ConfigCandidate | None:
    """Return the first candidate that exists as a file, or None.

    Only one config file is ever read; candidates are not merged.
    """
    for candidate in discover_config_candidates(env):
        if candidate.path.is_file():
            return candidate
    return None


def load_config_file(path: Path) -> dict:
    """Parse a TOML config file with stdlib tomllib.

    Raises ConfigCapabilityError if tomllib is unavailable, or ConfigError
    if the file cannot be read or parsed.
    """
    tomllib = _load_tomllib()
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"failed to parse TOML config at {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"failed to read config file at {path}: {exc}") from exc


def canonicalize_path(path: str | os.PathLike, base: str | os.PathLike | None = None) -> Path:
    """Expand ~, resolve relative to base (default cwd), and resolve symlinks."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (Path(base) if base is not None else Path.cwd()) / candidate
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def find_repo_root(start: str | os.PathLike | None = None) -> Path | None:
    """Walk up from start (default cwd) looking for a `.git` entry."""
    current = canonicalize_path(start if start is not None else Path.cwd())
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _is_ancestor_or_equal(ancestor: Path, target: Path) -> bool:
    return ancestor == target or ancestor in target.parents


def _expect_type(desc: str, key: str, value: Any, expected: type | tuple[type, ...], errors: list[str]) -> bool:
    if not isinstance(value, expected) or isinstance(value, bool) and expected is not bool:
        type_names = "/".join(t.__name__ for t in (expected if isinstance(expected, tuple) else (expected,)))
        errors.append(f"{desc}.{key} must be {type_names}, got {type(value).__name__}")
        return False
    return True


def _reject_unknown_keys(desc: str, table: Mapping, allowed: Sequence[str], errors: list[str]) -> None:
    unknown_keys = sorted(set(table) - set(allowed))
    if unknown_keys:
        errors.append(f"{desc} has unknown or forbidden key(s): {', '.join(unknown_keys)}")


def _validate_enum(desc: str, key: str, value: Any, allowed: Sequence[str], errors: list[str]) -> None:
    if _expect_type(desc, key, value, str, errors) and value not in allowed:
        errors.append(f"{desc}.{key} must be one of {tuple(allowed)}, got {value!r}")


def _validate_non_empty_string(desc: str, key: str, value: Any, errors: list[str]) -> None:
    if _expect_type(desc, key, value, str, errors) and not value.strip():
        errors.append(f"{desc}.{key} must not be empty")


def _validate_int_range(
    desc: str,
    key: str,
    value: Any,
    errors: list[str],
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    if not _expect_type(desc, key, value, int, errors):
        return
    if value < minimum or maximum is not None and value > maximum:
        expected = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        errors.append(f"{desc}.{key} must be {expected}, got {value}")


def _validate_agent_identity_fields(desc: str, table: Mapping, errors: list[str]) -> None:
    """Validate the provider/model/effort fields shared by every role."""

    if "provider" in table:
        _validate_enum(desc, "provider", table["provider"], SUPPORTED_AGENT_PROVIDERS, errors)
    for key in ("model", "effort"):
        if key in table:
            _validate_non_empty_string(desc, key, table[key], errors)


def _validate_lane_count(desc: str, key: str, value: Any, errors: list[str]) -> None:
    """Validate a canonical or legacy lane-count value.

    ``auto`` participates in the same per-layer alias normalization as an
    explicit integer.  Treating it as absence would incorrectly expose a
    lower-precedence legacy probe count.
    """

    if isinstance(value, str):
        if value != "auto":
            errors.append(
                f"{desc}.{key} must be 'auto' or an integer between "
                f"{MIN_REVIEW_LANES} and {MAX_REVIEW_LANES}, got {value!r}"
            )
        return
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(
            f"{desc}.{key} must be 'auto' or an integer between "
            f"{MIN_REVIEW_LANES} and {MAX_REVIEW_LANES}, got {type(value).__name__}"
        )
        return
    if value < MIN_REVIEW_LANES or value > MAX_REVIEW_LANES:
        errors.append(
            f"{desc}.{key} must be 'auto' or an integer between "
            f"{MIN_REVIEW_LANES} and {MAX_REVIEW_LANES}, got {value}"
        )


def _validate_string_list(
    desc: str,
    key: str,
    value: Any,
    errors: list[str],
    *,
    allowed: Sequence[str] | None = None,
    allow_empty: bool = False,
) -> None:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        qualifier = "a list" if allow_empty else "a non-empty list"
        errors.append(f"{desc}.{key} must be {qualifier} of non-empty strings")
        return
    if len(value) != len(set(value)):
        errors.append(f"{desc}.{key} must not contain duplicate entries")
    if allowed is not None:
        invalid = sorted(set(value) - set(allowed))
        if invalid:
            errors.append(
                f"{desc}.{key} entries must be one of {tuple(allowed)}, "
                f"got {', '.join(invalid)}"
            )


def _validate_role_table(desc: str, table: Any, errors: list[str], *, role: str) -> None:
    if not isinstance(table, Mapping):
        errors.append(f"{desc} must be a table")
        return
    if role == "manager":
        allowed_keys = _MANAGER_ROLE_KEYS
    elif role == "reviewer":
        allowed_keys = _REVIEWER_ROLE_KEYS
    elif role == "gap":
        allowed_keys = _GAP_ROLE_KEYS
    else:
        allowed_keys = _AGENT_IDENTITY_KEYS
    _reject_unknown_keys(desc, table, allowed_keys, errors)
    _validate_agent_identity_fields(desc, table, errors)

    if role == "manager" and "identity_policy" in table:
        _validate_enum(
            desc,
            "identity_policy",
            table["identity_policy"],
            SUPPORTED_MANAGER_IDENTITY_POLICIES,
            errors,
        )

    if role not in COORDINATED_ROLE_NAMES:
        return
    if "mode" in table:
        _validate_enum(desc, "mode", table["mode"], SUPPORTED_REVIEW_MODES, errors)
    lane_keys = ("lane_count",)
    if role == "reviewer":
        lane_keys = (*lane_keys, "probe_count", "probes_per_provider")
    for key in lane_keys:
        if key in table:
            _validate_lane_count(desc, key, table[key], errors)

    for component in COORDINATOR_COMPONENT_NAMES:
        if component not in table:
            continue
        component_table = table[component]
        if not isinstance(component_table, Mapping):
            errors.append(f"{desc}.{component} must be a table")
            continue
        _reject_unknown_keys(
            f"{desc}.{component}", component_table, _AGENT_IDENTITY_KEYS, errors
        )
        _validate_agent_identity_fields(f"{desc}.{component}", component_table, errors)

    if role != "reviewer":
        return
    if "protocol" in table:
        _validate_enum(
            desc, "protocol", table["protocol"], SUPPORTED_REVIEW_PROTOCOLS, errors
        )
    if "providers" in table:
        _validate_string_list(
            desc,
            "providers",
            table["providers"],
            errors,
            allowed=SUPPORTED_AGENT_PROVIDERS,
        )
    if "required_capabilities" in table:
        _validate_string_list(
            desc,
            "required_capabilities",
            table["required_capabilities"],
            errors,
            allow_empty=True,
        )


def _validate_review_policy_table(desc: str, table: Any, errors: list[str]) -> None:
    """Validate the review policy fields shared by defaults and scopes.

    The safety-critical rules remain in the review/convergence state machine;
    this table only accepts the bounded policy knobs approved for configuration.
    ``lane_count`` deliberately accepts ``"auto"`` as well as the explicit
    4--8 lane range used by the review plan.
    """
    if not isinstance(table, Mapping):
        errors.append(f"{desc} must be a table")
        return
    _reject_unknown_keys(desc, table, _REVIEW_POLICY_KEYS, errors)

    if "cadence" in table:
        _validate_enum(desc, "cadence", table["cadence"], SUPPORTED_REVIEW_CADENCES, errors)
    if "pre_final_protocol" in table:
        _validate_enum(
            desc,
            "pre_final_protocol",
            table["pre_final_protocol"],
            SUPPORTED_REVIEW_PROTOCOLS,
            errors,
        )
    if "manual_final_protocol" in table:
        _validate_enum(
            desc,
            "manual_final_protocol",
            table["manual_final_protocol"],
            SUPPORTED_MANUAL_FINAL_PROTOCOLS,
            errors,
        )
    if "manual_final_execution" in table:
        _validate_enum(
            desc,
            "manual_final_execution",
            table["manual_final_execution"],
            SUPPORTED_MANUAL_FINAL_EXECUTIONS,
            errors,
        )
    if "max_fix_rounds" in table:
        _validate_int_range(
            desc,
            "max_fix_rounds",
            table["max_fix_rounds"],
            errors,
            minimum=0,
            maximum=MAX_REVIEW_FIX_ROUNDS,
        )
    if "scope_expansion_action" in table:
        _validate_enum(
            desc,
            "scope_expansion_action",
            table["scope_expansion_action"],
            SUPPORTED_SCOPE_EXPANSION_ACTIONS,
            errors,
        )
    if "final_required" in table:
        _validate_enum(
            desc,
            "final_required",
            table["final_required"],
            SUPPORTED_FINAL_REQUIREMENTS,
            errors,
        )
    if "lane_count" in table:
        _validate_lane_count(desc, "lane_count", table["lane_count"], errors)


def _lane_alias_values(table: Mapping) -> list[tuple[str, Any]]:
    """Return every reviewer lane spelling present in one config layer."""

    aliases: list[tuple[str, Any]] = []
    reviewer = table.get("reviewer")
    if isinstance(reviewer, Mapping):
        for key in ("lane_count", "probe_count", "probes_per_provider"):
            if key in reviewer:
                aliases.append((f"reviewer.{key}", reviewer[key]))
    review = table.get("review")
    if isinstance(review, Mapping) and "lane_count" in review:
        aliases.append(("review.lane_count", review["lane_count"]))
    return aliases


def _resolved_layer_lane_alias(
    aliases: Sequence[tuple[str, Any]],
) -> tuple[str, Any] | None:
    """Resolve equal aliases and the legacy ``auto`` fallback in one layer.

    One explicit legacy value paired with ``auto`` is the 0.5.2 fallback
    shape and resolves to that explicit value.  A layer containing only
    ``auto`` keeps ``auto`` as a real higher-precedence override.  More than
    one distinct explicit value is a conflict.
    """

    if not aliases:
        return None
    explicit = [(key, value) for key, value in aliases if value != "auto"]
    if not explicit:
        return aliases[0]
    first = explicit[0]
    if all(value == first[1] and type(value) is type(first[1]) for _, value in explicit[1:]):
        return first
    return None


def _validate_layer_alias_conflicts(desc: str, table: Mapping, errors: list[str]) -> None:
    """Reject only differing reviewer lane aliases in the same layer."""

    aliases = _lane_alias_values(table)
    if len(aliases) < 2:
        return
    if _resolved_layer_lane_alias(aliases) is not None:
        return
    keys = [key for key, _ in aliases]
    if keys == ["reviewer.probe_count", "reviewer.probes_per_provider"] or keys == [
        "reviewer.probes_per_provider",
        "reviewer.probe_count",
    ]:
        errors.append(
            f"{desc}.reviewer must not set both probe_count and probes_per_provider "
            "to different values in the same layer"
        )
        return
    rendered = ", ".join(f"{key}={value!r}" for key, value in aliases)
    errors.append(
        f"{desc} has conflicting reviewer lane aliases in the same layer: {rendered}"
    )


def _validate_audit_table(desc: str, table: Any, errors: list[str]) -> None:
    if not isinstance(table, Mapping):
        errors.append(f"{desc} must be a table")
        return
    _reject_unknown_keys(desc, table, _AUDIT_KEYS, errors)
    if "agent_budget" in table:
        _validate_int_range(desc, "agent_budget", table["agent_budget"], errors, minimum=1)
    if "max_patch_review_rounds_per_task" in table:
        _validate_int_range(
            desc,
            "max_patch_review_rounds_per_task",
            table["max_patch_review_rounds_per_task"],
            errors,
            minimum=0,
            maximum=MAX_PATCH_REVIEW_ROUNDS,
        )


def _validate_defaults_table(desc: str, table: Any, errors: list[str]) -> None:
    if not isinstance(table, Mapping):
        errors.append(f"{desc} must be a table")
        return
    _reject_unknown_keys(desc, table, _DEFAULT_KEYS, errors)
    if "max_workers" in table:
        _validate_int_range(desc, "max_workers", table["max_workers"], errors, minimum=1)
    if "session_cleanup" in table:
        _validate_enum(
            desc,
            "session_cleanup",
            table["session_cleanup"],
            SUPPORTED_SESSION_CLEANUP_MODES,
            errors,
        )
    if "specification_scout" in table:
        _validate_enum(
            desc,
            "specification_scout",
            table["specification_scout"],
            SUPPORTED_SPECIFICATION_SCOUT_MODES,
            errors,
        )
    for role_key in CONFIG_ROLE_NAMES:
        if role_key in table:
            _validate_role_table(f"{desc}.{role_key}", table[role_key], errors, role=role_key)
    if "review" in table:
        _validate_review_policy_table(f"{desc}.review", table["review"], errors)
    if "audit" in table:
        _validate_audit_table(f"{desc}.audit", table["audit"], errors)
    _validate_layer_alias_conflicts(desc, table, errors)


def _validate_scope_entry(desc: str, entry: Any, errors: list[str]) -> None:
    if not isinstance(entry, Mapping):
        errors.append(f"{desc} must be a table")
        return
    _reject_unknown_keys(desc, entry, _SCOPE_KEYS, errors)
    if "path" not in entry:
        errors.append(f"{desc}.path is required")
    elif _expect_type(desc, "path", entry["path"], str, errors):
        try:
            expanded_path = Path(entry["path"]).expanduser()
        except RuntimeError:
            errors.append(f"{desc}.path cannot expand user home: {entry['path']!r}")
        else:
            if not expanded_path.is_absolute():
                errors.append(
                    f"{desc}.path must be absolute (or start with '~'); relative paths are cwd-dependent"
                )
    if "match" in entry:
        match_kind = entry["match"]
        if not isinstance(match_kind, str) or match_kind not in SUPPORTED_MATCH_KINDS:
            errors.append(
                f"{desc}.match must be one of {SUPPORTED_MATCH_KINDS}, got {match_kind!r}"
            )
    if "max_workers" in entry:
        _validate_int_range(desc, "max_workers", entry["max_workers"], errors, minimum=1)
    if "session_cleanup" in entry:
        _validate_enum(
            desc,
            "session_cleanup",
            entry["session_cleanup"],
            SUPPORTED_SESSION_CLEANUP_MODES,
            errors,
        )
    if "specification_scout" in entry:
        _validate_enum(
            desc,
            "specification_scout",
            entry["specification_scout"],
            SUPPORTED_SPECIFICATION_SCOUT_MODES,
            errors,
        )
    for role_key in CONFIG_ROLE_NAMES:
        if role_key in entry:
            _validate_role_table(f"{desc}.{role_key}", entry[role_key], errors, role=role_key)
    if "review" in entry:
        _validate_review_policy_table(f"{desc}.review", entry["review"], errors)
    if "audit" in entry:
        _validate_audit_table(f"{desc}.audit", entry["audit"], errors)
    _validate_layer_alias_conflicts(desc, entry, errors)


def _scope_dedupe_key(entry: Mapping, _base: str | os.PathLike | None) -> tuple[str, Path] | None:
    path_value = entry.get("path")
    if not isinstance(path_value, str):
        return None
    try:
        expanded_path = Path(path_value).expanduser()
    except RuntimeError:
        return None
    if not expanded_path.is_absolute():
        return None
    match_kind = entry.get("match", DEFAULT_MATCH_KIND)
    if not isinstance(match_kind, str):
        return None
    return (match_kind, canonicalize_path(expanded_path))


def validate_config(data: Mapping, *, base: str | os.PathLike | None = None) -> None:
    """Validate parsed TOML content against the config.toml schema.

    Collects every issue found and raises a single ConfigValidationError
    listing them, rather than failing on the first problem. Raises nothing
    when ``data`` is valid.
    """
    if not isinstance(data, Mapping):
        raise ConfigValidationError("config root must be a table")

    errors: list[str] = []

    unknown_keys = sorted(set(data.keys()) - set(_KNOWN_TOP_LEVEL_KEYS))
    if unknown_keys:
        errors.append(f"unknown top-level key(s): {', '.join(unknown_keys)}")

    if "version" not in data:
        errors.append("version is required")
    else:
        version = data["version"]
        if _expect_type("config", "version", version, int, errors) and version not in SUPPORTED_CONFIG_VERSIONS:
            errors.append(
                f"config.version must be one of {SUPPORTED_CONFIG_VERSIONS}, got {version}"
            )

    if "defaults" in data:
        _validate_defaults_table("defaults", data["defaults"], errors)

    scope_list: Sequence = ()
    if "scope" in data:
        if not isinstance(data["scope"], list):
            errors.append("scope must be an array of tables")
        else:
            scope_list = data["scope"]
            for index, entry in enumerate(scope_list):
                _validate_scope_entry(f"scope[{index}]", entry, errors)

    seen_scopes: dict[tuple[str, Path], int] = {}
    for index, entry in enumerate(scope_list):
        if not isinstance(entry, Mapping):
            continue
        key = _scope_dedupe_key(entry, base)
        if key is None:
            continue
        if key in seen_scopes:
            match_kind, canonical_path = key
            errors.append(
                f"duplicate scope definition for match={match_kind!r} path={canonical_path} "
                f"(scope[{seen_scopes[key]}] and scope[{index}])"
            )
        else:
            seen_scopes[key] = index

    if errors:
        raise ConfigValidationError("; ".join(errors))


@dataclasses.dataclass(frozen=True)
class MatchedScope:
    """A [[scope]] entry that matched the current resolution target."""

    index: int
    match_kind: str
    canonical_path: Path
    entry: Mapping


def match_scopes(
    scope_list: Sequence[Mapping] | None,
    *,
    repo_root: Path | None,
    cwd: Path,
    base: str | os.PathLike | None = None,
) -> list[MatchedScope]:
    """Return scopes whose canonical path is an ancestor of the match target.

    Results are ordered shallow-to-deep so callers can apply them in
    override order (deeper/more-specific scopes win).
    """
    matched: list[MatchedScope] = []
    for index, entry in enumerate(scope_list or ()):
        if not isinstance(entry, Mapping) or "path" not in entry:
            continue
        match_kind = entry.get("match", DEFAULT_MATCH_KIND)
        if match_kind not in SUPPORTED_MATCH_KINDS:
            continue
        target = repo_root if match_kind == "repo" else cwd
        if target is None:
            continue
        canonical_path = canonicalize_path(entry["path"], base)
        if _is_ancestor_or_equal(canonical_path, target):
            matched.append(MatchedScope(index=index, match_kind=match_kind, canonical_path=canonical_path, entry=entry))

    matched.sort(key=lambda scope: len(scope.canonical_path.parts))
    return matched


@dataclasses.dataclass(frozen=True)
class ConfigAssignment:
    """One leaf assignment retained in low-to-high precedence order."""

    source: str
    input_key: str
    value: Any

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "input_key": self.input_key, "value": self.value}


@dataclasses.dataclass(frozen=True)
class ResolvedValue:
    value: Any
    source: str
    history: tuple[ConfigAssignment, ...] = ()


class ConfigResolution:
    """Result of layered config merge: canonical values and provenance."""

    def __init__(self, entries: dict[tuple[str, ...], ResolvedValue]):
        self._entries = entries

    def _entry_for_lookup(self, keys: tuple[str, ...]) -> ResolvedValue | None:
        entry = self._entries.get(keys)
        if entry is not None:
            return entry
        canonical = _LEGACY_ALIAS_TO_CANONICAL.get(keys)
        if canonical is None:
            return None
        entry = self._entries.get(canonical)
        if entry is None or not entry.history:
            return None
        # Keep read compatibility for the legacy spelling that actually won
        # this resolution without reintroducing aliases into ``as_dict``.
        return entry if entry.history[-1].input_key == ".".join(keys) else None

    def get(self, *keys: str, default: Any = None) -> Any:
        entry = self._entry_for_lookup(tuple(keys))
        return entry.value if entry is not None else default

    def source_of(self, *keys: str) -> str | None:
        entry = self._entry_for_lookup(tuple(keys))
        return entry.source if entry is not None else None

    def explain(self) -> list[dict[str, Any]]:
        """Return a list of {key, value, source} rows, sorted by key path."""
        return [
            {"key": ".".join(path), "value": entry.value, "source": entry.source}
            for path, entry in sorted(self._entries.items())
        ]

    def explain_provenance(self) -> list[dict[str, Any]]:
        """Explain the winner and every lower-precedence assignment.

        Alias inputs retain their original dotted spelling in ``input_key``;
        ``key`` is always the canonical resolved path.
        """

        return [
            {
                "key": ".".join(path),
                "value": entry.value,
                "source": entry.source,
                "provenance": [assignment.as_dict() for assignment in entry.history],
            }
            for path, entry in sorted(self._entries.items())
        ]

    def as_dict(self) -> dict:
        result: dict = {}
        for path, entry in self._entries.items():
            cursor = result
            for part in path[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[path[-1]] = entry.value
        return result


# Keys that form a single coherent reviewer topology unit: setting one in a
# layer must clear any sibling inherited from a lower-priority layer, or the
# two can both resolve simultaneously even though only one was ever chosen
# at any single layer (e.g. `probe_count` from `[defaults.reviewer]` plus
# `probes_per_provider` from a deeper `[[scope]]` entry).
_EXCLUSIVE_KEY_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("reviewer",), ("probe_count", "probes_per_provider")),
)


def _clear_exclusive_siblings(
    entries: dict[tuple[str, ...], ResolvedValue], parent: tuple[str, ...], key: str
) -> None:
    for group_parent, group_keys in _EXCLUSIVE_KEY_GROUPS:
        if parent == group_parent and key in group_keys:
            for sibling in group_keys:
                if sibling != key:
                    entries.pop(parent + (sibling,), None)


def _merge_layer(
    entries: dict[tuple[str, ...], ResolvedValue],
    mapping: Mapping,
    source: str,
    prefix: tuple[str, ...] = (),
) -> None:
    for key, value in mapping.items():
        path = prefix + (key,)
        if isinstance(value, Mapping):
            _merge_layer(entries, value, source, path)
        else:
            _clear_exclusive_siblings(entries, prefix, key)
            previous = entries.get(path)
            assignment = ConfigAssignment(source=source, input_key=".".join(path), value=value)
            history = (*previous.history, assignment) if previous is not None else (assignment,)
            entries[path] = ResolvedValue(value=value, source=source, history=history)


def deep_merge_with_source(layers: Iterable[tuple[str, Mapping | None]]) -> ConfigResolution:
    """Merge layered mappings, later layers overriding earlier ones per-key.

    Each leaf value records the source label of the layer that set it,
    enabling `config explain`-style output.
    """
    entries: dict[tuple[str, ...], ResolvedValue] = {}
    for source, mapping in layers:
        if mapping:
            _merge_layer(entries, mapping, source)
    return ConfigResolution(entries)


_LEGACY_ALIAS_TO_CANONICAL = {
    ("review", "lane_count"): ("reviewer", "lane_count"),
    ("reviewer", "probe_count"): ("reviewer", "lane_count"),
    ("reviewer", "probes_per_provider"): ("reviewer", "lane_count"),
}


def _copy_config_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_config_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_config_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_config_value(item) for item in value)
    return value


def _normalize_config_layer_with_origins(
    mapping: Mapping, *, source: str
) -> tuple[dict, dict[tuple[str, ...], str]]:
    """Canonicalize aliases in one layer without consulting other layers."""

    if not isinstance(mapping, Mapping):
        raise ConfigValidationError(f"{source} config layer must be a table")
    errors: list[str] = []
    for input_key, value in _lane_alias_values(mapping):
        _validate_lane_count(source, input_key, value, errors)
    _validate_layer_alias_conflicts(source, mapping, errors)
    if errors:
        raise ConfigValidationError("; ".join(errors))

    normalized = _copy_config_value(mapping)
    origins: dict[tuple[str, ...], str] = {}
    aliases = _lane_alias_values(mapping)
    if not aliases:
        return normalized, origins

    selected = _resolved_layer_lane_alias(aliases)
    if selected is None:
        # ``_validate_layer_alias_conflicts`` above guarantees the detailed
        # message, so this is defensive only.
        raise ConfigValidationError(f"{source} has conflicting reviewer lane aliases")
    input_key, value = selected
    reviewer = normalized.setdefault("reviewer", {})
    if not isinstance(reviewer, dict):
        # Config-file validation reports this before resolution.  Override
        # layers are also fail-closed instead of failing with an opaque
        # attribute error here.
        raise ConfigValidationError(f"{source}.reviewer must be a table")
    for key in ("lane_count", "probe_count", "probes_per_provider"):
        reviewer.pop(key, None)
    reviewer["lane_count"] = value

    review = normalized.get("review")
    if isinstance(review, dict):
        review.pop("lane_count", None)
    origins[("reviewer", "lane_count")] = input_key
    return normalized, origins


def normalize_config_layer(mapping: Mapping, *, source: str = "config-layer") -> dict:
    """Return a detached layer containing canonical config keys only."""

    normalized, _ = _normalize_config_layer_with_origins(mapping, source=source)
    return normalized


def _merge_canonical_layer(
    entries: dict[tuple[str, ...], ResolvedValue],
    mapping: Mapping,
    source: str,
    origins: Mapping[tuple[str, ...], str],
    prefix: tuple[str, ...] = (),
) -> None:
    for key, value in mapping.items():
        path = (*prefix, key)
        if isinstance(value, Mapping):
            _merge_canonical_layer(entries, value, source, origins, path)
            continue
        previous = entries.get(path)
        assignment = ConfigAssignment(
            source=source,
            input_key=origins.get(path, ".".join(path)),
            value=value,
        )
        history = (*previous.history, assignment) if previous is not None else (assignment,)
        entries[path] = ResolvedValue(value=value, source=source, history=history)


def merge_config_layers(
    layers: Iterable[tuple[str, Mapping | None]],
) -> ConfigResolution:
    """Validate, normalize, then merge layers with assignment history.

    Config-file defaults and scopes already pass through ``validate_config``.
    Task, start, and participant overrides do not, so those runtime-only
    layers independently enforce the same canonical keys and value bounds
    before their higher precedence can take effect. Other sources retain the
    0.5.2 compatibility behavior of accepting prevalidated snapshots/defaults.
    """

    entries: dict[tuple[str, ...], ResolvedValue] = {}
    for source, mapping in layers:
        if not mapping:
            continue
        if source in _RUNTIME_OVERRIDE_SOURCES:
            errors: list[str] = []
            _validate_defaults_table(source, mapping, errors)
            if errors:
                raise ConfigValidationError("; ".join(errors))
        normalized, origins = _normalize_config_layer_with_origins(mapping, source=source)
        _merge_canonical_layer(entries, normalized, source, origins)
    return ConfigResolution(entries)


def resolve_config(
    built_in_defaults: Mapping,
    config_data: Mapping | None = None,
    *,
    target_dir: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    loop_snapshot: Mapping | None = None,
    task_override: Mapping | None = None,
    start_override: Mapping | None = None,
    participant_override: Mapping | None = None,
) -> ConfigResolution:
    """Resolve config values across the full precedence chain.

    Order (lowest to highest precedence):
        built-in default
        < config.toml [defaults]
        < matching directory scopes, shallow to deep
        < loop snapshot
        < task override
        < start command override
        < participant override (participant-specific start tier)

    Every layer is alias-normalized independently before merge.  Therefore a
    higher-layer ``auto`` or legacy spelling overrides a lower layer normally,
    while only contradictory spellings inside one layer are rejected.
    """
    target_dir = Path(target_dir) if target_dir is not None else Path.cwd()
    cwd = canonicalize_path(target_dir)
    repo_root = find_repo_root(cwd)

    layers: list[tuple[str, Mapping | None]] = [("built-in-default", built_in_defaults)]

    if config_data:
        validate_config(config_data, base=cwd)
        layers.append(("config-defaults", config_data.get("defaults", {})))

        matched = match_scopes(config_data.get("scope"), repo_root=repo_root, cwd=cwd, base=cwd)
        for scope in matched:
            scope_values = {k: v for k, v in scope.entry.items() if k not in ("path", "match")}
            source = f"scope:{scope.match_kind}:{scope.canonical_path}"
            layers.append((source, scope_values))

    if loop_snapshot:
        layers.append(("loop-snapshot", loop_snapshot))

    if task_override:
        layers.append(("task-override", task_override))

    if start_override:
        layers.append(("start-override", start_override))

    if participant_override:
        layers.append(("participant-override", participant_override))

    return merge_config_layers(layers)


def load_and_resolve(
    built_in_defaults: Mapping,
    *,
    target_dir: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    loop_snapshot: Mapping | None = None,
    task_override: Mapping | None = None,
    start_override: Mapping | None = None,
    participant_override: Mapping | None = None,
) -> tuple[ConfigResolution, ConfigCandidate | None]:
    """Discover, load, validate, and resolve config in one call.

    Returns (resolution, candidate). ``candidate`` is None when no config
    file was found; built-in defaults (plus any overrides) still resolve.
    """
    candidate = find_config_file(env)
    config_data = load_config_file(candidate.path) if candidate is not None else None
    resolution = resolve_config(
        built_in_defaults,
        config_data,
        target_dir=target_dir,
        env=env,
        loop_snapshot=loop_snapshot,
        task_override=task_override,
        start_override=start_override,
        participant_override=participant_override,
    )
    return resolution, candidate


@dataclasses.dataclass(frozen=True)
class ProtocolSelection:
    """Canonical protocol selection for one review execution kind."""

    execution_kind: str
    key: str
    protocol: Any
    source: str | None


_PROTOCOL_PATHS = {
    "ordinary": ("reviewer", "protocol"),
    "pre-final": ("review", "pre_final_protocol"),
    "manual-final": ("review", "manual_final_protocol"),
}


def select_review_protocol(
    resolved: ConfigResolution | Mapping, execution_kind: str
) -> ProtocolSelection:
    """Read only the canonical protocol key for ``execution_kind``.

    Missing values remain missing; this function intentionally never falls
    back to another execution kind's protocol.
    """

    try:
        path = _PROTOCOL_PATHS[execution_kind]
    except KeyError as exc:
        raise ConfigValidationError(
            f"unsupported review execution kind: {execution_kind!r}"
        ) from exc
    if isinstance(resolved, ConfigResolution):
        protocol = resolved.get(*path)
        source = resolved.source_of(*path)
    else:
        cursor: Any = resolved
        for key in path:
            cursor = cursor.get(key) if isinstance(cursor, Mapping) else None
        protocol = cursor
        source = None
    return ProtocolSelection(
        execution_kind=execution_kind,
        key=".".join(path),
        protocol=protocol,
        source=source,
    )
