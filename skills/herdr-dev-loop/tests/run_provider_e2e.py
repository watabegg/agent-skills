#!/usr/bin/env python3
"""Run or explicitly skip one read-only live-provider marker probe.

The legacy filename is retained for compatibility.  This runner proves only
provider CLI availability, an exact marker response, and fixture Git
immutability; it does not exercise an HLoop role, Herdr, prompts, or reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SUCCESS_MARKER = "HLOOP_PROVIDER_MARKER_PROBE_OK"
UNAVAILABLE_PATTERNS = {
    "credentials-unavailable": (
        "not logged in",
        "authentication",
        "authenticate",
        "api key",
        "credential",
        "login required",
        "please log in",
    ),
    "session-unavailable": (
        "session unavailable",
        "session expired",
        "no active session",
    ),
    "provider-capacity-unavailable": (
        "rate limit",
        "quota",
        "overloaded",
        "capacity",
    ),
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def command_version(executable: str) -> dict[str, Any]:
    proc = subprocess.run(
        [executable, "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    text = (proc.stdout or proc.stderr).strip().splitlines()
    return {
        "returncode": proc.returncode,
        "value": text[0][:300] if text else "",
    }


def classify_unavailable(stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".lower()
    for classification, patterns in UNAVAILABLE_PATTERNS.items():
        if any(pattern in text for pattern in patterns):
            return classification
    return ""


def diagnostic_record(stdout: str, stderr: str) -> dict[str, Any]:
    combined = f"stdout:\n{stdout}\nstderr:\n{stderr}".encode("utf-8", errors="replace")
    return {
        "sha256": hashlib.sha256(combined).hexdigest(),
        "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
        "success_marker_present": SUCCESS_MARKER in stdout,
    }


def normalize_timeout_output(output: str | bytes | None) -> str:
    """Return TimeoutExpired output as text regardless of subprocess behavior."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def provider_message(provider: str, stdout: str, last_message: str) -> str:
    if provider == "codex":
        return last_message or stdout
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return payload["result"]
    return stdout


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    commands = (
        ["git", "init", "--initial-branch=master"],
        ["git", "config", "user.email", "hloop-provider-probe@example.invalid"],
        ["git", "config", "user.name", "HLoop Provider Probe"],
    )
    for command in commands:
        subprocess.run(command, cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "README.md").write_text("provider marker probe fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "provider marker probe fixture"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return repo


def provider_command(
    provider: str,
    executable: str,
    repo: Path,
    output_path: Path,
    model: str | None,
) -> list[str]:
    prompt = (
        "This is a read-only herdr-dev-loop provider probe. Do not use tools and do not "
        f"edit files. Return exactly {SUCCESS_MARKER} and nothing else."
    )
    if provider == "codex":
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--ignore-rules",
            "--output-last-message",
            str(output_path),
            "-C",
            str(repo),
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    command = [
        executable,
        "--print",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--model")
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="return a structured skipped result for an unavailable binary, credential, or session",
    )
    parser.add_argument(
        "--skip-reason",
        help="explicitly skip without invoking the provider; requires --allow-skip",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep-workdir", action="store_true")
    return parser.parse_args()


def emit(result: dict[str, Any], args: argparse.Namespace) -> int:
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json:
        sys.stdout.write(payload)
    else:
        print(
            f"provider marker probe ({result['provider']}): {result['status']}"
            + (f" - {result['skip_reason']}" if result["skip_reason"] else "")
        )
    return 0 if result["status"] in {"passed", "skipped"} else 1


def main() -> int:
    args = parse_args()
    if args.skip_reason and not args.allow_skip:
        raise SystemExit("--skip-reason requires --allow-skip")
    if args.timeout_seconds < 1:
        raise SystemExit("--timeout-seconds must be positive")

    started = now()
    runtime_version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    executable = shutil.which(args.provider)
    version = command_version(executable) if executable else {"returncode": 127, "value": ""}
    base: dict[str, Any] = {
        "schema_version": 1,
        "runner": "herdr-dev-loop-provider-marker-probe",
        "probe_kind": "live-provider-availability-read-only-marker",
        "coverage": [
            "provider-cli-availability",
            "exact-marker-response",
            "fixture-git-immutability",
        ],
        "excluded_coverage": [
            "hloop-role-launch",
            "herdr-integration",
            "rendered-role-prompt",
            "agent-report-path",
        ],
        "runtime_version": runtime_version,
        "provider": args.provider,
        "model": args.model or "provider-default",
        "provider_binary": executable,
        "provider_version": version,
        "herdr_env": os.environ.get("HERDR_ENV") == "1",
        "started_at": started,
        "finished_at": now(),
        "live_execution": False,
        "safe_skip": False,
        "skip_reason": "",
        "skip_classification": "",
        "workspace": None,
        "workspace_retained": False,
        "git_unchanged": None,
        "diagnostic": {},
    }

    if args.skip_reason:
        base.update(
            status="skipped",
            safe_skip=True,
            skip_reason=args.skip_reason,
            skip_classification="explicit-release-environment-skip",
            finished_at=now(),
        )
        return emit(base, args)

    if executable is None:
        if args.allow_skip:
            base.update(
                status="skipped",
                safe_skip=True,
                skip_reason=f"{args.provider} executable is unavailable",
                skip_classification="provider-binary-unavailable",
                finished_at=now(),
            )
            return emit(base, args)
        base.update(status="failed", skip_reason=f"{args.provider} executable is unavailable")
        return emit(base, args)

    root = Path(tempfile.mkdtemp(prefix=f"hloop-provider-probe-{args.provider}-"))
    repo = make_repo(root)
    output_path = root / "provider-last-message.txt"
    before = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    command = provider_command(args.provider, executable, repo, output_path, args.model)
    try:
        proc = subprocess.run(
            command,
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=args.timeout_seconds,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(
            command,
            124,
            stdout=normalize_timeout_output(exc.stdout),
            stderr=normalize_timeout_output(exc.stderr) or "provider probe timed out",
        )
        timed_out = True

    after = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    last_message = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    combined_stdout = f"{proc.stdout}\n{last_message}"
    classification = classify_unavailable(combined_stdout, proc.stderr)
    message = provider_message(args.provider, proc.stdout, last_message)
    marker_ok = bool(re.fullmatch(rf"\s*{SUCCESS_MARKER}\s*", message))
    git_unchanged = before == after == ""
    retained = args.keep_workdir
    base.update(
        live_execution=True,
        workspace=str(root) if retained else None,
        workspace_retained=retained,
        git_unchanged=git_unchanged,
        diagnostic={
            **diagnostic_record(combined_stdout, proc.stderr),
            "returncode": proc.returncode,
            "timed_out": timed_out,
        },
    )
    if proc.returncode == 0 and marker_ok and git_unchanged:
        base.update(status="passed")
    elif classification and args.allow_skip and git_unchanged:
        base.update(
            status="skipped",
            safe_skip=True,
            skip_reason=f"live {args.provider} probe could not authenticate or acquire a provider session",
            skip_classification=classification,
        )
    else:
        reasons = []
        if proc.returncode != 0:
            reasons.append(f"provider exit code {proc.returncode}")
        if not marker_ok:
            reasons.append("success marker missing or not exact")
        if not git_unchanged:
            reasons.append("provider modified the fixture repository")
        base.update(status="failed", skip_reason="; ".join(reasons))
    base["finished_at"] = now()
    if not retained:
        shutil.rmtree(root, ignore_errors=True)
    return emit(base, args)


if __name__ == "__main__":
    raise SystemExit(main())
