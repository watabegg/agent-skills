"""Hierarchical TOML config primitives for herdr-dev-loop 0.5.0.

Implements config file discovery, stdlib TOML loading, repo-default and
explicit cwd directory scopes with canonical symlink-safe matching,
precedence resolution with per-key source explanation, schema/type
validation, and Python capability checks.

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
SUPPORTED_MATCH_KINDS = ("repo", "cwd")
DEFAULT_MATCH_KIND = "repo"
_KNOWN_TOP_LEVEL_KEYS = ("version", "defaults", "scope")
_STR_ROLE_KEYS = ("provider", "model", "effort", "mode")
_INT_ROLE_KEYS = ("probe_count", "probes_per_provider")
_LIST_STR_ROLE_KEYS = ("providers",)


class ConfigError(Exception):
    """Base error for config discovery, parsing, or validation failures."""


class ConfigCapabilityError(ConfigError):
    """Raised when the running Python lacks required config capabilities."""


class ConfigValidationError(ConfigError):
    """Raised when parsed config content fails schema/type validation."""


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


def _validate_role_table(desc: str, table: Any, errors: list[str]) -> None:
    if not isinstance(table, Mapping):
        errors.append(f"{desc} must be a table")
        return
    for key in _STR_ROLE_KEYS:
        if key in table:
            _expect_type(desc, key, table[key], str, errors)
    for key in _INT_ROLE_KEYS:
        if key in table:
            _expect_type(desc, key, table[key], int, errors)
    for key in _LIST_STR_ROLE_KEYS:
        if key in table and key in table:
            value = table[key]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append(f"{desc}.{key} must be a list of strings")


def _validate_defaults_table(desc: str, table: Any, errors: list[str]) -> None:
    if not isinstance(table, Mapping):
        errors.append(f"{desc} must be a table")
        return
    if "max_workers" in table:
        _expect_type(desc, "max_workers", table["max_workers"], int, errors)
    if "session_cleanup" in table:
        _expect_type(desc, "session_cleanup", table["session_cleanup"], str, errors)
    for role_key in ("worker", "reviewer"):
        if role_key in table:
            _validate_role_table(f"{desc}.{role_key}", table[role_key], errors)


def _validate_scope_entry(desc: str, entry: Any, errors: list[str]) -> None:
    if not isinstance(entry, Mapping):
        errors.append(f"{desc} must be a table")
        return
    if "path" not in entry:
        errors.append(f"{desc}.path is required")
    elif not _expect_type(desc, "path", entry["path"], str, errors):
        pass
    if "match" in entry:
        match_kind = entry["match"]
        if not isinstance(match_kind, str) or match_kind not in SUPPORTED_MATCH_KINDS:
            errors.append(
                f"{desc}.match must be one of {SUPPORTED_MATCH_KINDS}, got {match_kind!r}"
            )
    for role_key in ("worker", "reviewer"):
        if role_key in entry:
            _validate_role_table(f"{desc}.{role_key}", entry[role_key], errors)


def _scope_dedupe_key(entry: Mapping, base: str | os.PathLike | None) -> tuple[str, Path] | None:
    path_value = entry.get("path")
    if not isinstance(path_value, str):
        return None
    match_kind = entry.get("match", DEFAULT_MATCH_KIND)
    if not isinstance(match_kind, str):
        return None
    return (match_kind, canonicalize_path(path_value, base))


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
        _expect_type("config", "version", data["version"], int, errors)

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
class ResolvedValue:
    value: Any
    source: str


class ConfigResolution:
    """Result of layered config merge: resolved values plus their source."""

    def __init__(self, entries: dict[tuple[str, ...], ResolvedValue]):
        self._entries = entries

    def get(self, *keys: str, default: Any = None) -> Any:
        entry = self._entries.get(tuple(keys))
        return entry.value if entry is not None else default

    def source_of(self, *keys: str) -> str | None:
        entry = self._entries.get(tuple(keys))
        return entry.source if entry is not None else None

    def explain(self) -> list[dict[str, Any]]:
        """Return a list of {key, value, source} rows, sorted by key path."""
        return [
            {"key": ".".join(path), "value": entry.value, "source": entry.source}
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
            entries[path] = ResolvedValue(value=value, source=source)


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


def resolve_config(
    built_in_defaults: Mapping,
    config_data: Mapping | None = None,
    *,
    target_dir: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    task_override: Mapping | None = None,
    start_override: Mapping | None = None,
) -> ConfigResolution:
    """Resolve config values across the full precedence chain.

    Order (lowest to highest precedence):
        built-in default
        < config.toml [defaults]
        < matching directory scopes, shallow to deep
        < task override
        < start command override

    Existing loop PROFILE.md/STATE.json snapshot precedence sits between
    scopes and task override in the full hloop CLI chain, but is outside
    this primitives module's responsibility.
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

    if task_override:
        layers.append(("task-override", task_override))

    if start_override:
        layers.append(("start-override", start_override))

    return deep_merge_with_source(layers)


def load_and_resolve(
    built_in_defaults: Mapping,
    *,
    target_dir: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    task_override: Mapping | None = None,
    start_override: Mapping | None = None,
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
        task_override=task_override,
        start_override=start_override,
    )
    return resolution, candidate
