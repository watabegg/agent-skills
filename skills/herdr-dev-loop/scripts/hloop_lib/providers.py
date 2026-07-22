"""Provider command and capability primitives for HLoop 0.5.

The CLI integration owns process startup and STATE.json mutation.  This module
keeps the provider-specific argument construction and capability evidence small,
deterministic, and independently testable.  In particular, an explicit model is
never treated as supported merely because a provider accepts ``--model``: when
the installed CLI has no safe model-validation command, the result stays
``unknown`` and the exact launch argv is still recorded.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


PROVIDERS = frozenset({"codex", "claude"})
RUNNERS = frozenset({"tui", "exec"})
CAPABILITY_STATUSES = frozenset(
    {"supported", "unsupported", "unknown", "unavailable"}
)

CapabilityStatus = Literal["supported", "unsupported", "unknown", "unavailable"]


class ProviderError(ValueError):
    """Raised when provider configuration cannot form a safe invocation."""


@dataclass(frozen=True)
class ProviderInvocation:
    """A provider launch description without shell redirection.

    ``stdin_path`` is recorded separately for non-interactive runners so prompt
    contents do not need to appear in argv or persisted capability evidence.
    Interactive callers append the prompt text at launch time; ``prompt_path``
    remains the durable, non-secret description of that source.
    """

    provider: str
    runner: str
    model: str
    effort: str
    permission_mode: str
    sandbox: str
    argv: tuple[str, ...]
    prompt_path: str
    stdin_path: str | None
    stdout_path: str | None

    def as_record(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "runner": self.runner,
            "model": self.model,
            "effort": self.effort,
            "permission_mode": self.permission_mode,
            "sandbox": self.sandbox,
            "argv": list(self.argv),
            "prompt_path": self.prompt_path,
            "stdin_path": self.stdin_path,
            "stdout_path": self.stdout_path,
        }


@dataclass(frozen=True)
class ModelProbeResult:
    """Result supplied by a provider-specific safe model validator."""

    status: CapabilityStatus
    reason: str
    argv: tuple[str, ...] = ()
    returncode: int | None = None

    def __post_init__(self) -> None:
        if self.status not in CAPABILITY_STATUSES:
            raise ProviderError(f"invalid capability status: {self.status}")
        _single_line(self.reason, "reason")


@dataclass(frozen=True)
class ProviderCapabilityResult:
    """Serializable preflight evidence for one resolved provider launch."""

    provider: str
    capability: CapabilityStatus
    reason: str
    invocation: ProviderInvocation
    probe_argv: tuple[str, ...]
    probe_returncode: int | None
    model_probe: ModelProbeResult | None = None

    @property
    def launch_allowed(self) -> bool:
        """Unknown is explicit evidence, not an implicit provider fallback."""

        return self.capability in {"supported", "unknown"}

    def as_record(self) -> dict[str, Any]:
        record = self.invocation.as_record()
        record.update(
            {
                "capability": self.capability,
                "reason": self.reason,
                "launch_allowed": self.launch_allowed,
                "probe_argv": list(self.probe_argv),
                "probe_returncode": self.probe_returncode,
            }
        )
        if self.model_probe is not None:
            record["model_probe"] = {
                "capability": self.model_probe.status,
                "reason": self.model_probe.reason,
                "argv": list(self.model_probe.argv),
                "returncode": self.model_probe.returncode,
            }
        return record


def build_provider_invocation(
    *,
    provider: str,
    runner: str,
    sandbox: str,
    prompt_path: Path,
    model: str = "auto",
    effort: str = "auto",
    permission_mode: str = "auto",
    output_path: Path | None = None,
    writable_dirs: Sequence[Path] = (),
) -> ProviderInvocation:
    """Build the final provider argv without invoking a shell.

    For ``exec`` runners the prompt is passed on stdin.  For ``tui`` runners the
    caller reads ``prompt_path`` and appends the text as the final positional
    argument immediately before launch.  Keeping the prompt out of this record
    prevents a persisted argv snapshot from duplicating user/task content.
    """

    provider = _choice(provider, "provider", PROVIDERS)
    runner = _choice(runner, "runner", RUNNERS)
    sandbox = _single_line(sandbox, "sandbox")
    model = _single_line(model, "model")
    effort = _single_line(effort, "effort")
    permission_mode = _single_line(permission_mode, "permission_mode")
    prompt = _absolute_path(prompt_path, "prompt_path")
    output = _absolute_path(output_path, "output_path") if output_path else None
    writable = tuple(_absolute_path(path, "writable_dir") for path in writable_dirs)

    if provider == "codex":
        if permission_mode not in {"auto", "never"}:
            raise ProviderError(
                "Codex permission_mode must be auto or never; use sandbox for access"
            )
        permission_mode = "never"
        if runner == "exec":
            argv = ["codex", "exec", "--sandbox", sandbox]
        else:
            argv = [
                "codex",
                "--sandbox",
                sandbox,
                "--ask-for-approval",
                "never",
                "--no-alt-screen",
            ]
        argv.extend(["-c", "sandbox_workspace_write.writable_roots=[]"])
        if model != "auto":
            argv.extend(["--model", model])
        if effort != "auto":
            argv.extend(["-c", f"model_reasoning_effort={effort}"])
        for directory in writable:
            argv.extend(["--add-dir", directory])
        if runner == "exec" and output is not None:
            argv.extend(["--output-last-message", output])
        if runner == "exec":
            argv.append("-")
    else:
        if sandbox not in {"workspace-write", "read-only"}:
            raise ProviderError(
                "Claude sandbox must be workspace-write or read-only; "
                "permission_mode controls provider access"
            )
        argv = ["claude"]
        if runner == "exec":
            argv.append("--print")
        else:
            argv.append("--ax-screen-reader")
        argv.extend(["--permission-mode", permission_mode])
        if model != "auto":
            argv.extend(["--model", model])
        if effort != "auto":
            argv.extend(["--effort", effort])
        for directory in writable:
            argv.extend(["--add-dir", directory])
        if runner == "exec" and output is not None:
            raise ProviderError(
                "Claude output redirection is a launcher concern, not provider argv"
            )

    return ProviderInvocation(
        provider=provider,
        runner=runner,
        model=model,
        effort=effort,
        permission_mode=permission_mode,
        sandbox=sandbox,
        argv=tuple(argv),
        prompt_path=prompt,
        stdin_path=prompt if runner == "exec" else None,
        stdout_path=output,
    )


def probe_provider_capability(
    invocation: ProviderInvocation,
    *,
    executable_finder: Callable[[str], str | None] = shutil.which,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    model_validator: Callable[[ProviderInvocation], ModelProbeResult] | None = None,
    timeout_seconds: float = 10.0,
    env: Mapping[str, str] | None = None,
) -> ProviderCapabilityResult:
    """Probe installed CLI flags and, when available, the configured model.

    The built-in probe is deliberately local and side-effect free: it invokes
    only ``<provider> --help``.  An explicit model therefore remains ``unknown``
    unless the caller supplies a safe provider/version-specific validator.
    """

    executable = executable_finder(invocation.provider)
    help_argv = _help_probe_argv(executable or invocation.provider, invocation)
    if executable is None:
        return ProviderCapabilityResult(
            provider=invocation.provider,
            capability="unavailable",
            reason=f"{invocation.provider} command not found",
            invocation=invocation,
            probe_argv=help_argv,
            probe_returncode=None,
        )

    try:
        completed = command_runner(
            list(help_argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProviderCapabilityResult(
            provider=invocation.provider,
            capability="unavailable",
            reason=f"provider help probe failed: {type(exc).__name__}: {exc}",
            invocation=invocation,
            probe_argv=help_argv,
            probe_returncode=None,
        )

    if completed.returncode != 0:
        return ProviderCapabilityResult(
            provider=invocation.provider,
            capability="unavailable",
            reason="provider help probe returned non-zero",
            invocation=invocation,
            probe_argv=help_argv,
            probe_returncode=completed.returncode,
        )

    help_text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    missing_flags = _missing_required_flags(invocation, help_text)
    if missing_flags:
        return ProviderCapabilityResult(
            provider=invocation.provider,
            capability="unsupported",
            reason="installed provider lacks required flags: "
            + ", ".join(missing_flags),
            invocation=invocation,
            probe_argv=help_argv,
            probe_returncode=completed.returncode,
        )

    if invocation.model == "auto":
        return ProviderCapabilityResult(
            provider=invocation.provider,
            capability="supported",
            reason="provider executable and required launch flags are available",
            invocation=invocation,
            probe_argv=help_argv,
            probe_returncode=completed.returncode,
        )

    if model_validator is None:
        return ProviderCapabilityResult(
            provider=invocation.provider,
            capability="unknown",
            reason=(
                "installed provider exposes --model but no safe model validation "
                "probe was supplied; no fallback was selected"
            ),
            invocation=invocation,
            probe_argv=help_argv,
            probe_returncode=completed.returncode,
        )

    try:
        model_result = model_validator(invocation)
    except (OSError, subprocess.SubprocessError, ProviderError) as exc:
        model_result = ModelProbeResult(
            "unknown", f"model validation probe failed: {type(exc).__name__}: {exc}"
        )
    if not isinstance(model_result, ModelProbeResult):
        raise ProviderError("model_validator must return ModelProbeResult")
    return ProviderCapabilityResult(
        provider=invocation.provider,
        capability=model_result.status,
        reason=model_result.reason,
        invocation=invocation,
        probe_argv=help_argv,
        probe_returncode=completed.returncode,
        model_probe=model_result,
    )


DEFAULT_REVIEW_CAPACITY = {"codex": 10, "claude": 10}
"""Default per-provider ceiling on concurrent review sub-agents (coordinator +
discovery lanes + verifier pool) an installed CLI is trusted to host safely.
Overridable per-namespace via STATE ``review_capacity_limits``."""


@dataclass(frozen=True)
class ReviewCapacityResult:
    """Per-provider capacity evidence checked before any review pane exists."""

    provider: str
    required: int
    ceiling: int
    capacity_probe: CapabilityStatus = "unknown"
    capacity_probe_reason: str = "no capacity probe supplied"

    @property
    def ok(self) -> bool:
        return self.required <= self.ceiling and self.capacity_probe in {
            "supported",
            "unknown",
        }

    def as_record(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "required": self.required,
            "ceiling": self.ceiling,
            "capacity_probe": self.capacity_probe,
            "capacity_probe_reason": self.capacity_probe_reason,
            "ok": self.ok,
        }


def review_capacity_ceiling(provider: str, *, limits: Mapping[str, int] | None = None) -> int:
    if limits and provider in limits:
        return int(limits[provider])
    return DEFAULT_REVIEW_CAPACITY.get(provider, 8)


def probe_provider_review_capacity(
    provider: str,
    *,
    executable_finder: Callable[[str], str | None] = shutil.which,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: float = 10.0,
) -> tuple[CapabilityStatus, str]:
    """Best-effort, side-effect-free evidence for a swarm concurrency ceiling.

    Codex hosts review sub-agents via process-local ``-c agents.max_threads=N``
    / ``-c agents.max_depth=1`` overrides; Claude Code hosts them via
    session-local sub-agent (Task tool) support. Neither can be safely probed
    by actually spawning agents before a Coordinator pane exists, so this only
    inspects ``--help`` text for the documented control surface: a provider
    that is installed but does not document it stays ``unknown`` (explicit,
    unverified evidence), never an implicit ``supported`` fallback.
    """

    provider = _choice(provider, "provider", PROVIDERS)
    executable = executable_finder(provider)
    if executable is None:
        return "unavailable", f"{provider} command not found"
    try:
        completed = command_runner(
            [executable, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unavailable", f"provider help probe failed: {type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return "unavailable", "provider help probe returned non-zero"
    help_text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if provider == "codex":
        if "-c" in help_text or "--config" in help_text:
            return (
                "supported",
                "codex documents -c for process-local agents.max_threads/agents.max_depth overrides",
            )
        return (
            "unknown",
            "codex --help does not document a config override flag; swarm concurrency ceiling is unverified",
        )
    if "agent" in help_text.lower():
        return "supported", "claude documents sub-agent (Task tool) support"
    return (
        "unknown",
        "claude --help does not document sub-agent capacity controls; swarm concurrency ceiling is unverified",
    )


def check_review_capacity(
    required: Mapping[str, int],
    *,
    limits: Mapping[str, int] | None = None,
    capability: Mapping[str, tuple[CapabilityStatus, str]] | None = None,
) -> list[ReviewCapacityResult]:
    """Evaluate every provider's required-vs-ceiling capacity.

    Callers must check ``.ok`` before creating a Coordinator pane: a plan that
    requests more concurrent sub-agents than the provider is trusted to host,
    or whose swarm concurrency controls a verifiable probe reports as
    ``unsupported``/``unavailable``, must fail closed rather than spawn a
    partially-staffed swarm. ``capability`` should carry real
    ``probe_provider_review_capacity`` evidence per provider; providers absent
    from it stay ``unknown`` (unverified, not blocking) rather than an
    implicit pass.
    """

    capability = capability or {}
    results = [
        ReviewCapacityResult(
            provider=provider,
            required=count,
            ceiling=review_capacity_ceiling(provider, limits=limits),
            **(
                {
                    "capacity_probe": capability[provider][0],
                    "capacity_probe_reason": capability[provider][1],
                }
                if provider in capability
                else {}
            ),
        )
        for provider, count in required.items()
    ]
    exceeded = [result for result in results if not result.ok]
    if exceeded:
        detail = "; ".join(
            f"{result.provider}: requires {result.required}, ceiling is {result.ceiling}"
            if result.required > result.ceiling
            else f"{result.provider}: capacity probe is {result.capacity_probe} ({result.capacity_probe_reason})"
            for result in exceeded
        )
        raise ProviderError(f"review swarm capacity exceeded: {detail}")
    return results


def _missing_required_flags(
    invocation: ProviderInvocation, help_text: str
) -> list[str]:
    required: list[str] = []
    if invocation.model != "auto":
        required.append("--model")
    if invocation.effort != "auto":
        required.append("-c" if invocation.provider == "codex" else "--effort")
    if invocation.provider == "codex":
        required.append("--sandbox")
        if invocation.runner == "exec" and invocation.stdout_path is not None:
            required.append("--output-last-message")
    else:
        required.append("--permission-mode")
        if invocation.runner == "exec":
            required.append("--print")
    return [flag for flag in required if flag not in help_text]


def _help_probe_argv(
    executable: str, invocation: ProviderInvocation
) -> tuple[str, ...]:
    if invocation.provider == "codex" and invocation.runner == "exec":
        return (executable, "exec", "--help")
    return (executable, "--help")


def _choice(value: Any, field: str, choices: frozenset[str]) -> str:
    normalized = _single_line(value, field).lower()
    if normalized not in choices:
        raise ProviderError(
            f"{field} must be one of: {', '.join(sorted(choices))}"
        )
    return normalized


def _single_line(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ProviderError(f"{field} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise ProviderError(f"{field} must be a single-line string")
    return value


def _absolute_path(path: Path, field: str) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ProviderError(f"{field} must be absolute")
    return str(candidate)
