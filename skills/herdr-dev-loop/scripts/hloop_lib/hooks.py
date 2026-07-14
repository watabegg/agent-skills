"""Ownership-safe provider hook rendering for HLoop 0.5.

Hooks are an optional guard, never the source of truth for role progress or
completion.  These helpers only render and merge a Stop guard after an explicit
opt-in.  Existing user hooks are preserved, and uninstall removes handlers only
when their argv contains HLoop's exact owner marker and guard command shape.
"""

from __future__ import annotations

import copy
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROVIDERS = frozenset({"codex", "claude"})
CAPABILITY_STATUSES = frozenset(
    {"supported", "unsupported", "unknown", "unavailable"}
)
OWNER_MARKER = "herdr-dev-loop:manager-sleep-guard:v1"
FIXED_SLEEP_CONTEXT = (
    "Active HLoop roles remain and no valid wake lease is registered. "
    "Run `hloop manager sleep --wake-on report` before ending this turn."
)


class HookSettingsError(ValueError):
    """Raised for malformed settings or unsafe hook configuration."""


@dataclass(frozen=True)
class HookPlan:
    provider: str
    enabled: bool
    installable: bool
    reason: str
    handler: dict[str, Any] | None
    fallback: str = "herdr"
    trust_required: bool = False
    reload_required: bool = False


@dataclass(frozen=True)
class HookSettingsChange:
    settings: dict[str, Any]
    changed: bool
    owned_handlers: int


def plan_stop_hook(
    *,
    provider: str,
    helper_path: Path,
    namespace: str,
    enabled: bool,
    codex_continuation_capability: str = "unknown",
    timeout_seconds: int = 10,
) -> HookPlan:
    """Return an opt-in provider plan while retaining Herdr fallback.

    Codex Stop continuation must be proven by a version-specific runtime probe.
    An ``unknown`` or ``unsupported`` result deliberately produces a hookless
    plan rather than pretending a warning can force the Manager to continue.
    """

    provider = _provider(provider)
    helper = Path(helper_path).expanduser()
    if not helper.is_absolute():
        raise HookSettingsError("helper_path must be absolute")
    namespace = _single_line(namespace, "namespace")
    codex_continuation_capability = _single_line(
        codex_continuation_capability, "codex_continuation_capability"
    )
    if codex_continuation_capability not in CAPABILITY_STATUSES:
        raise HookSettingsError(
            "codex_continuation_capability must be one of: "
            + ", ".join(sorted(CAPABILITY_STATUSES))
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise HookSettingsError("timeout_seconds must be an integer")
    if not 1 <= timeout_seconds <= 600:
        raise HookSettingsError("timeout_seconds must be between 1 and 600")
    if not enabled:
        return HookPlan(
            provider=provider,
            enabled=False,
            installable=False,
            reason="provider hook installation was not explicitly enabled",
            handler=None,
        )

    if provider == "codex" and codex_continuation_capability != "supported":
        return HookPlan(
            provider=provider,
            enabled=True,
            installable=False,
            reason=(
                "Codex Stop continuation capability is "
                f"{codex_continuation_capability}; use Herdr fallback"
            ),
            handler=None,
            trust_required=True,
        )

    command = [
        "python3",
        str(helper),
        "--namespace",
        namespace,
        "hooks",
        "guard",
        "--provider",
        provider,
        "--hloop-hook-owner",
        OWNER_MARKER,
    ]
    common: dict[str, Any] = {
        "type": "command",
        "timeout": timeout_seconds,
        "statusMessage": "Checking HLoop wake lease",
    }
    if provider == "claude":
        handler = {**common, "command": command[0], "args": command[1:]}
    else:
        handler = {**common, "command": shlex.join(command)}
    return HookPlan(
        provider=provider,
        enabled=True,
        installable=True,
        reason="Stop guard rendered; Herdr remains the hookless wake fallback",
        handler=handler,
        trust_required=provider == "codex",
        reload_required=provider == "claude",
    )


def merge_stop_hook(
    settings: Mapping[str, Any], plan: HookPlan
) -> HookSettingsChange:
    """Idempotently merge one HLoop-owned Stop handler.

    A non-installable plan is a no-op.  This makes opt-in and capability gating
    explicit at the call site and keeps hookless operation fully supported.
    """

    normalized = _validated_settings_copy(settings)
    if not plan.installable:
        return HookSettingsChange(normalized, False, 0)
    if plan.handler is None:
        raise HookSettingsError("installable HookPlan is missing its handler")

    stripped, _ = _remove_owned(normalized, _provider(plan.provider))
    hooks = stripped.setdefault("hooks", {})
    stop_groups = hooks.setdefault("Stop", [])
    stop_groups.append({"hooks": [copy.deepcopy(plan.handler)]})
    return HookSettingsChange(stripped, stripped != normalized, 1)


def uninstall_stop_hook(
    settings: Mapping[str, Any], *, provider: str
) -> HookSettingsChange:
    """Remove only HLoop-owned Stop handlers and preserve every user entry."""

    normalized = _validated_settings_copy(settings)
    stripped, removed = _remove_owned(normalized, _provider(provider))
    return HookSettingsChange(stripped, removed > 0, 0)


def owned_stop_hook_count(settings: Mapping[str, Any], *, provider: str) -> int:
    normalized = _validated_settings_copy(settings)
    hooks = normalized.get("hooks", {})
    return sum(
        1
        for group in hooks.get("Stop", [])
        for handler in group["hooks"]
        if _is_owned_handler(handler, _provider(provider))
    )


def render_stop_guard_response(
    *,
    provider: str,
    active_roles: bool,
    valid_wake_lease: bool,
) -> dict[str, Any]:
    """Render fixed, non-agent-derived Stop feedback when sleep is required."""

    provider = _provider(provider)
    if not active_roles or valid_wake_lease:
        return {}
    if provider == "claude":
        return {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": FIXED_SLEEP_CONTEXT,
            }
        }
    return {
        "continue": True,
        "systemMessage": FIXED_SLEEP_CONTEXT,
    }


def _remove_owned(
    settings: dict[str, Any], provider: str
) -> tuple[dict[str, Any], int]:
    result = copy.deepcopy(settings)
    hooks = result.get("hooks")
    if hooks is None:
        return result, 0
    groups = hooks.get("Stop")
    if groups is None:
        return result, 0

    removed = 0
    remaining_groups: list[dict[str, Any]] = []
    for group in groups:
        handlers = []
        for handler in group["hooks"]:
            if _is_owned_handler(handler, provider):
                removed += 1
            else:
                handlers.append(handler)
        if handlers:
            retained = copy.deepcopy(group)
            retained["hooks"] = handlers
            remaining_groups.append(retained)
        elif set(group) - {"hooks", "matcher"}:
            retained = copy.deepcopy(group)
            retained["hooks"] = []
            remaining_groups.append(retained)
    if remaining_groups:
        hooks["Stop"] = remaining_groups
    else:
        hooks.pop("Stop", None)
    if not hooks:
        result.pop("hooks", None)
    return result, removed


def _is_owned_handler(handler: Mapping[str, Any], provider: str) -> bool:
    tokens = _handler_tokens(handler)
    return (
        _contains_sequence(tokens, ("hooks", "guard"))
        and _contains_pair(tokens, "--provider", provider)
        and _contains_pair(tokens, "--hloop-hook-owner", OWNER_MARKER)
    )


def _handler_tokens(handler: Mapping[str, Any]) -> tuple[str, ...]:
    command = handler.get("command")
    if not isinstance(command, str) or not command:
        return ()
    args = handler.get("args")
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            return ()
        return (command, *args)
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return ()


def _contains_pair(tokens: tuple[str, ...], flag: str, value: str) -> bool:
    return any(
        tokens[index] == flag and tokens[index + 1] == value
        for index in range(len(tokens) - 1)
    )


def _contains_sequence(tokens: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    width = len(expected)
    return any(tokens[index : index + width] == expected for index in range(len(tokens)))


def _validated_settings_copy(settings: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise HookSettingsError("settings must be a JSON object")
    result = copy.deepcopy(dict(settings))
    hooks = result.get("hooks")
    if hooks is None:
        return result
    if not isinstance(hooks, dict):
        raise HookSettingsError("settings.hooks must be a JSON object")
    groups = hooks.get("Stop")
    if groups is None:
        return result
    if not isinstance(groups, list):
        raise HookSettingsError("settings.hooks.Stop must be a JSON array")
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise HookSettingsError(f"Stop[{index}] must be a JSON object")
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            raise HookSettingsError(f"Stop[{index}].hooks must be a JSON array")
        if not all(isinstance(handler, dict) for handler in handlers):
            raise HookSettingsError(
                f"Stop[{index}].hooks entries must be JSON objects"
            )
    return result


def _provider(value: str) -> str:
    normalized = _single_line(value, "provider").lower()
    if normalized not in PROVIDERS:
        raise HookSettingsError(
            f"provider must be one of: {', '.join(sorted(PROVIDERS))}"
        )
    return normalized


def _single_line(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise HookSettingsError(f"{field} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise HookSettingsError(f"{field} must be a single-line string")
    return value
