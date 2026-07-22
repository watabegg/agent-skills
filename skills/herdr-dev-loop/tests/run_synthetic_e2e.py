#!/usr/bin/env python3
"""Run the HLoop 0.5.3 release scenarios without live provider calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


sys.dont_write_bytecode = True

_SNAPSHOT_ACTIVE_ENV = "HLOOP_SYNTHETIC_PRIVATE_SNAPSHOT_SHA"
_SNAPSHOT_SOURCE_SKILL_ENV = "HLOOP_SYNTHETIC_SOURCE_SKILL_ROOT"
_SNAPSHOT_BOOTSTRAP_ERROR_ENV = "HLOOP_SYNTHETIC_SNAPSHOT_BOOTSTRAP_ERROR"


def _raw_expected_integration_sha() -> str:
    """Read the release identity before any repository-local module import."""

    expected = ""
    for index, value in enumerate(sys.argv[1:]):
        if value == "--expected-integration-sha":
            argument_index = index + 2
            expected = (
                sys.argv[argument_index] if argument_index < len(sys.argv) else ""
            )
        elif value.startswith("--expected-integration-sha="):
            expected = value.split("=", 1)[1]
    return expected or str(
        os.environ.get("HLOOP_EXPECTED_INTEGRATION_SHA") or ""
    ).strip()


def _raw_output_path() -> Path | None:
    output_path: Path | None = None
    for index, value in enumerate(sys.argv[1:]):
        if value == "--output":
            argument_index = index + 2
            output_path = (
                Path(sys.argv[argument_index])
                if argument_index < len(sys.argv)
                else None
            )
        elif value.startswith("--output="):
            output_path = Path(value.split("=", 1)[1])
    return output_path


def _mark_snapshot_cleanup_failed(payload: str, detail: str) -> str:
    """Turn child JSON evidence into a fail-closed cleanup result."""

    try:
        result = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return payload
    if not isinstance(result, dict):
        return payload
    result["status"] = "failed"
    result["snapshot_cleanup"] = {"status": "failed", "error": detail}
    identity = result.get("checkout_identity")
    if isinstance(identity, dict):
        identity["verified"] = False
        identity["parent_cleanup_attested"] = False
        identity["error"] = detail
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _mark_snapshot_cleanup_passed(payload: str) -> str:
    try:
        result = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return payload
    if not isinstance(result, dict):
        return payload
    result["snapshot_cleanup"] = {"status": "passed", "error": ""}
    identity = result.get("checkout_identity")
    identity_error = ""
    if not isinstance(identity, dict):
        identity_error = "child evidence has no checkout identity"
    elif identity.get("execution_verified") is not True:
        identity_error = str(identity.get("error") or "") or (
            "child execution identity was not verified"
        )
    elif identity.get("private_snapshot_verified") is not True:
        identity_error = "child did not execute in the verified private snapshot"
    elif identity.get("skill_subtree_clean") is not True:
        identity_error = "child private snapshot skill subtree was not clean"
    elif not str(identity.get("expected_integration_sha") or ""):
        identity_error = "child expected integration SHA is missing"
    elif len(
        {
            str(identity.get("expected_integration_sha") or ""),
            str(identity.get("resolved_head_sha") or ""),
            str(identity.get("private_snapshot_sha") or ""),
        }
    ) != 1:
        identity_error = "child private snapshot SHA identity is inconsistent"
    if identity_error:
        result["status"] = "failed"
        if isinstance(identity, dict):
            identity["verified"] = False
            identity["parent_cleanup_attested"] = False
            identity["error"] = identity_error
    else:
        identity["verified"] = True
        identity["parent_cleanup_attested"] = True
        identity["error"] = ""
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _snapshot_parent_attestation_present(payload: str) -> bool:
    try:
        result = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(result, dict):
        return False
    identity = result.get("checkout_identity")
    cleanup = result.get("snapshot_cleanup")
    return bool(
        isinstance(identity, dict)
        and identity.get("verified") is True
        and identity.get("parent_cleanup_attested") is True
        and isinstance(cleanup, dict)
        and cleanup.get("status") == "passed"
    )


def _trailing_json_payload(payload: str) -> tuple[str, str] | None:
    """Split incidental stdout from the final pretty-printed JSON object."""

    starts = [match.start() for match in re.finditer(r"(?m)^\{", payload)]
    for start in reversed(starts):
        candidate = payload[start:].strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return payload[:start], candidate + "\n"
    return None


def _private_snapshot_context_matches(expected: str) -> bool:
    """Verify the private snapshot from Git topology, not self-reported env."""

    if str(os.environ.get(_SNAPSHOT_ACTIVE_ENV) or "").strip() != expected:
        return False
    source_value = str(os.environ.get(_SNAPSHOT_SOURCE_SKILL_ENV) or "").strip()
    if not source_value:
        return False
    snapshot_skill = Path(__file__).resolve().parents[1]
    source_skill = Path(source_value).resolve()
    if snapshot_skill == source_skill or not source_skill.is_dir():
        return False

    def git_text(skill: Path, *args: str) -> tuple[int, str]:
        completed = subprocess.run(
            ["git", "-C", str(skill), *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return completed.returncode, completed.stdout.strip()

    snapshot_top_rc, snapshot_top_text = git_text(
        snapshot_skill, "rev-parse", "--show-toplevel"
    )
    source_top_rc, source_top_text = git_text(
        source_skill, "rev-parse", "--show-toplevel"
    )
    snapshot_common_rc, snapshot_common_text = git_text(
        snapshot_skill,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    )
    source_common_rc, source_common_text = git_text(
        source_skill,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    )
    snapshot_head_rc, snapshot_head = git_text(snapshot_skill, "rev-parse", "HEAD")
    detached = subprocess.run(
        ["git", "-C", str(snapshot_skill), "symbolic-ref", "-q", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if any(
        code != 0
        for code in (
            snapshot_top_rc,
            source_top_rc,
            snapshot_common_rc,
            source_common_rc,
            snapshot_head_rc,
        )
    ):
        return False
    snapshot_top = Path(snapshot_top_text).resolve()
    source_top = Path(source_top_text).resolve()
    try:
        same_relative_skill = (
            snapshot_skill.relative_to(snapshot_top)
            == source_skill.relative_to(source_top)
        )
    except ValueError:
        return False
    return bool(
        same_relative_skill
        and snapshot_head == expected
        and detached.returncode == 1
        and (snapshot_top / ".git").is_file()
        and Path(snapshot_common_text).resolve()
        == Path(source_common_text).resolve()
    )


def _bootstrap_private_release_snapshot() -> None:
    """Re-exec pinned release runs from a private detached worktree.

    The bootstrap runs before importing ``hloop`` or ``hloop_lib``. A clean
    mutable checkout is used only to locate the repository and expected
    commit; every release scenario then executes the committed bytes from a
    disposable detached worktree. Dirty or mismatched checkouts fall through
    to ``main`` solely to emit the normal structured fail-closed result.
    """

    expected = _raw_expected_integration_sha()
    if _private_snapshot_context_matches(expected):
        return
    # A caller-provided marker is not evidence. Clear it and build a real
    # snapshot when the current checkout is otherwise eligible.
    os.environ.pop(_SNAPSHOT_ACTIVE_ENV, None)
    os.environ.pop(_SNAPSHOT_SOURCE_SKILL_ENV, None)
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected) is None:
        return
    source_skill = Path(__file__).resolve().parents[1]
    head = subprocess.run(
        ["git", "-C", str(source_skill), "rev-parse", "HEAD"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    status = subprocess.run(
        [
            "git",
            "-C",
            str(source_skill),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--",
            ".",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if (
        head.returncode != 0
        or head.stdout.strip() != expected
        or status.returncode != 0
        or bool(status.stdout)
    ):
        return
    top = subprocess.run(
        ["git", "-C", str(source_skill), "rev-parse", "--show-toplevel"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if top.returncode != 0 or not top.stdout.strip():
        os.environ[_SNAPSHOT_BOOTSTRAP_ERROR_ENV] = (
            "could not resolve repository root for private release snapshot"
        )
        return
    source_repo = Path(top.stdout.strip()).resolve()
    relative_runner = Path(__file__).resolve().relative_to(source_repo)
    temporary_root = Path(tempfile.mkdtemp(prefix="hloop-release-snapshot-"))
    snapshot_repo = temporary_root / "checkout"
    try:
        added = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "worktree",
                "add",
                "--detach",
                str(snapshot_repo),
                expected,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        shutil.rmtree(temporary_root, ignore_errors=True)
        os.environ[_SNAPSHOT_BOOTSTRAP_ERROR_ENV] = (
            f"could not create private release snapshot: {exc}"
        )
        return
    if added.returncode != 0:
        subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "worktree",
                "remove",
                "--force",
                str(snapshot_repo),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "-C", str(source_repo), "worktree", "prune"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        shutil.rmtree(temporary_root, ignore_errors=True)
        os.environ[_SNAPSHOT_BOOTSTRAP_ERROR_ENV] = (
            "could not create private release snapshot: "
            + (added.stderr.strip() or "git worktree add failed")
        )
        return
    child_env = os.environ.copy()
    child_env[_SNAPSHOT_ACTIVE_ENV] = expected
    child_env[_SNAPSHOT_SOURCE_SKILL_ENV] = str(source_skill)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed: subprocess.CompletedProcess[str] | None = None
    child_error = ""
    cleanup_errors: list[str] = []
    try:
        completed = subprocess.run(
            [sys.executable, str(snapshot_repo / relative_runner), *sys.argv[1:]],
            check=False,
            env=child_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except BaseException as exc:
        child_error = f"private release snapshot child failed to run: {exc}"
    finally:
        try:
            removed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_repo),
                    "worktree",
                    "remove",
                    "--force",
                    str(snapshot_repo),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            cleanup_errors.append(f"git worktree remove could not run: {exc}")
        else:
            if removed.returncode != 0:
                cleanup_errors.append(
                    "git worktree remove failed: "
                    + (removed.stderr.strip() or f"exit {removed.returncode}")
                )
        try:
            shutil.rmtree(temporary_root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_errors.append(f"temporary snapshot removal failed: {exc}")
        if snapshot_repo.exists() or temporary_root.exists():
            cleanup_errors.append("private snapshot path still exists after cleanup")

    if completed is None:
        detail = "; ".join([item for item in (child_error, *cleanup_errors) if item])
        os.environ[_SNAPSHOT_BOOTSTRAP_ERROR_ENV] = (
            detail or "private release snapshot child did not produce a result"
        )
        return

    if completed.stderr:
        sys.stderr.write(completed.stderr)
    json_requested = any(value == "--json" for value in sys.argv[1:])
    structured_stdout = completed.stdout
    if json_requested:
        split_stdout = _trailing_json_payload(completed.stdout)
        if split_stdout is not None:
            incidental_stdout, structured_stdout = split_stdout
            if incidental_stdout:
                sys.stderr.write(incidental_stdout)
        else:
            output_path = _raw_output_path()
            if output_path is not None and output_path.exists():
                try:
                    structured_stdout = output_path.read_text(encoding="utf-8")
                except OSError:
                    pass
            if completed.stdout:
                sys.stderr.write(completed.stdout)
    if cleanup_errors:
        detail = "; ".join(cleanup_errors)
        output = _mark_snapshot_cleanup_failed(structured_stdout, detail)
        sys.stdout.write(output)
        output_path = _raw_output_path()
        if output_path is not None and output_path.exists():
            try:
                original = output_path.read_text(encoding="utf-8")
                output_path.write_text(
                    _mark_snapshot_cleanup_failed(original, detail),
                    encoding="utf-8",
                )
            except OSError as exc:
                detail += f"; could not update output evidence: {exc}"
        print(f"synthetic release snapshot cleanup failed: {detail}", file=sys.stderr)
        raise SystemExit(1)
    output = _mark_snapshot_cleanup_passed(structured_stdout)
    output_path = _raw_output_path()
    if output_path is not None and output_path.exists():
        try:
            original = output_path.read_text(encoding="utf-8")
            output_path.write_text(
                _mark_snapshot_cleanup_passed(original), encoding="utf-8"
            )
        except OSError as exc:
            detail = f"could not attest cleanup in output evidence: {exc}"
            sys.stdout.write(
                _mark_snapshot_cleanup_failed(structured_stdout, detail)
            )
            print(
                f"synthetic release evidence cleanup attestation write failed: {exc}",
                file=sys.stderr,
            )
            raise SystemExit(1)
    sys.stdout.write(output)
    if json_requested and not _snapshot_parent_attestation_present(output):
        print(
            "synthetic release evidence lacks a valid parent cleanup attestation",
            file=sys.stderr,
        )
        raise SystemExit(1)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    _bootstrap_private_release_snapshot()


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "hloop"
sys.path.insert(0, str(SKILL_ROOT.parents[1]))
sys.path.insert(0, str(SCRIPT.parent))

from hloop_lib.broker import spool_client_event  # noqa: E402
from hloop_lib.events import prepare_client_event, utc_now  # noqa: E402
from hloop_lib import review as hloop_review  # noqa: E402
from hloop_lib.certification import (  # noqa: E402
    CertificationPlan,
    FinalReviewManifest,
    FinalReviewProcessIdentity,
)
from hloop_lib.config import project_agent_identity  # noqa: E402
from hloop_lib.lifecycle import (  # noqa: E402
    AttemptIdentity,
    MERGE_ACTIVE,
    MERGE_COMPLETED,
    MERGE_CONTENT_CONFLICT,
    MergeTransaction,
    validate_attempt_copies,
    validate_merge_transaction,
)
from hloop_lib.review import (  # noqa: E402
    FindingCandidate,
    normalize_findings,
    plan_review_group,
    plan_verification,
)


loader = importlib.machinery.SourceFileLoader("hloop_synthetic_e2e_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
if spec is None:
    raise RuntimeError("could not load hloop runtime")
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)

class ScenarioFailure(RuntimeError):
    """Raised when a synthetic release invariant is not observed."""


FIXTURE_OBSERVED_FINAL_IDENTITIES = {
    "manual-final-coordinator": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "max",
    },
    "review-process": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
    },
}
FIXTURE_ATTESTED_FINAL_IDENTITIES = {
    "manual-final-coordinator": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "max",
    },
    "review-process": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
    },
}


def _v053_e2e_module():
    """Load v0.5.3-only scenarios lazily for copied 0.5.2 fixtures."""

    return __import__(
        "skills.herdr-dev-loop.tests.test_hloop_v053_e2e",
        fromlist=["run_scenario"],
    )


def _with_fixture_process_identities(
    plan: CertificationPlan, manifest: FinalReviewManifest
) -> FinalReviewManifest:
    identities = []
    for process in plan.process_plan:
        fixture_key = (
            "manual-final-coordinator"
            if process.process_id == "manual-final-coordinator"
            else "review-process"
        )
        identities.append(
            FinalReviewProcessIdentity(
                process_id=process.process_id,
                agent_identity=project_agent_identity(
                    {
                        "provider": process.provider,
                        "model": process.model,
                        "effort": process.effort,
                    },
                    observed=dict(FIXTURE_OBSERVED_FINAL_IDENTITIES[fixture_key]),
                    attested=dict(FIXTURE_ATTESTED_FINAL_IDENTITIES[fixture_key]),
                ).as_dict(),
            )
        )
    return replace(manifest, process_identities=tuple(identities))


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expected: int | set[int] = 0,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    expected_codes = {expected} if isinstance(expected, int) else expected
    if proc.returncode not in expected_codes:
        command = " ".join(argv)
        raise ScenarioFailure(
            f"command failed ({proc.returncode}): {command}\n"
            f"stdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
        )
    return proc


def git(repo: Path, *args: str, expected: int | set[int] = 0) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, env=os.environ.copy(), expected=expected)


def make_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir()
    run(
        ["git", "init", "--initial-branch=master"],
        cwd=repo,
        env=os.environ.copy(),
    )
    git(repo, "config", "user.email", "hloop-e2e@example.invalid")
    git(repo, "config", "user.name", "HLoop Synthetic E2E")
    (repo / "README.md").write_text("synthetic fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "synthetic fixture")
    return repo


def hloop_command(repo: Path, namespace: str, *args: str) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT),
        "--repo",
        str(repo),
        "--namespace",
        namespace,
        *args,
    ]


def state_path(repo: Path, namespace: str) -> Path:
    return repo / ".ai" / "herdr-dev-loop" / "loops" / namespace / "STATE.json"


def scenario_config_migration(ctx: dict[str, Any]) -> dict[str, Any]:
    root: Path = ctx["root"]
    repo: Path = ctx["repo"]
    env: dict[str, str] = ctx["env"]
    namespace: str = ctx["namespace"]
    config_path = Path(env["HLOOP_CONFIG_HOME"]) / "config.toml"

    created = run(
        [sys.executable, str(SCRIPT), "config", "init", "--json"],
        cwd=repo,
        env=env,
    )
    require(Path(json.loads(created.stdout)["created"]) == config_path, "wrong config path")
    config_path.write_text(
        """version = 1

[defaults]
max_workers = 2
session_cleanup = "archive"
specification_scout = "always"

[defaults.worker]
provider = "codex"
model = "auto"
effort = "auto"

[defaults.reviewer]
mode = "dual-swarm"
providers = ["codex", "claude"]
probes_per_provider = 4
""",
        encoding="utf-8",
    )
    run(
        [sys.executable, str(SCRIPT), "config", "validate", "--json"],
        cwd=repo,
        env=env,
    )
    explained = run(
        [
            sys.executable,
            str(SCRIPT),
            "config",
            "explain",
            "--repo",
            str(repo),
            "--json",
        ],
        cwd=repo,
        env=env,
    )
    require(json.loads(explained.stdout)["resolved"]["max_workers"] == 2, "config not resolved")
    run(
        hloop_command(
            repo,
            namespace,
            "init",
            "--goal",
            "synthetic release gate",
            "--integration",
            "master",
            "--persistence",
            "local-only",
        ),
        cwd=root,
        env=env,
    )

    path = state_path(repo, namespace)
    state = json.loads(path.read_text(encoding="utf-8"))
    require(state["state_format_version"] == 3, "init did not create format 3")
    require(
        state["schema_revision"] == hloop.STATE_SCHEMA_REVISION,
        "init did not create the current schema revision",
    )
    require(state["skill_version"] == ctx["runtime_version"], "state version mismatch")
    require(state["resolved_config"]["max_workers"] == 2, "config snapshot missing")
    require(state["specification_scout"] == "always", "Scout policy missing")

    config_text = config_path.read_text(encoding="utf-8").replace(
        "max_workers = 2", "max_workers = 4"
    )
    config_path.write_text(config_text, encoding="utf-8")
    apply_preview = run(
        hloop_command(repo, namespace, "config", "apply", "--dry-run"),
        cwd=root,
        env=env,
    )
    preview_payload = json.loads(apply_preview.stdout)
    require(preview_payload["changed"], "config apply dry-run missed a changed value")
    unchanged = json.loads(path.read_text(encoding="utf-8"))
    require(unchanged["max_workers"] == 2, "config apply dry-run mutated state")
    run(
        hloop_command(repo, namespace, "config", "apply", "--apply"),
        cwd=root,
        env=env,
    )
    applied = json.loads(path.read_text(encoding="utf-8"))
    require(applied["max_workers"] == 4, "explicit config apply did not update state")
    state = applied

    state["state_format_version"] = 2
    state.pop("schema_revision", None)
    state["skill_version"] = "0.4.0"
    for v053_field in (
        "config_identity_projection",
        "first_v053_mutation_at",
        "first_v053_mutation_command",
        "manager_identity",
        "migration_v053",
        "remediation_ledger",
        "remediation_source_links",
        "review_epochs",
        "review_protocol_selection",
    ):
        state.pop(v053_field, None)
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    dry_run = run(
        hloop_command(repo, namespace, "migrate", "--dry-run"),
        cwd=root,
        env=env,
    )
    migration_plan = json.loads(dry_run.stdout)
    require(migration_plan["to_format"] == 3, "migration target format mismatch")
    require(
        migration_plan["to_revision"] == hloop.STATE_SCHEMA_REVISION,
        "migration target revision mismatch",
    )
    run(
        hloop_command(repo, namespace, "migrate", "--apply"),
        cwd=root,
        env=env,
    )
    # Close rollback through the real first-material-command boundary before
    # later synthetic scenarios make deliberate direct STATE projections.
    run(
        hloop_command(repo, namespace, "config", "apply", "--apply"),
        cwd=root,
        env=env,
    )
    migrated = json.loads(path.read_text(encoding="utf-8"))
    marker_path = path.parent / "migration" / "v053" / "ACTIVE.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    generation_root = (
        marker_path.parent
        / "generations"
        / f"{marker['migration_generation']:06d}"
    )
    archived_state = generation_root / "archive" / path.relative_to(repo)
    require(
        (migrated["state_format_version"], migrated["schema_revision"])
        == (hloop.STATE_FORMAT_VERSION, hloop.STATE_SCHEMA_REVISION),
        "migration failed",
    )
    require(marker["status"] == "committed", "migration commit marker missing")
    require(marker["first_v053_mutation_at"], "first mutation boundary missing")
    require(archived_state.is_file(), "migration archive missing")
    return {
        "config_source": migrated["config_source"]["kind"],
        "resolved_max_workers": migrated["resolved_config"]["max_workers"],
        "explicit_apply_max_workers": migrated["max_workers"],
        "migration_steps": migration_plan["applied_steps"],
        "migration_generation": marker["migration_generation"],
        "archive_count": 1,
    }


def scenario_attempt_and_merge(ctx: dict[str, Any]) -> dict[str, Any]:
    root: Path = ctx["root"]
    repo = make_repo(root, "merge-repo")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "switch", "-c", "worker")
    (repo / "README.md").write_text("worker change\n", encoding="utf-8")
    git(repo, "commit", "-am", "worker change")
    worker_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "switch", "master")
    (repo / "README.md").write_text("manager change\n", encoding="utf-8")
    git(repo, "commit", "-am", "manager change")
    manager_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    conflict = git(repo, "merge", "worker", expected={1})
    require("CONFLICT" in conflict.stdout or "CONFLICT" in conflict.stderr, "expected merge conflict")
    git(repo, "merge", "--abort")
    require(not git(repo, "status", "--porcelain").stdout.strip(), "merge abort left dirty state")

    identity = AttemptIdentity(
        run_id="run-e2e",
        role_id="T001",
        attempt_id="T001-A001",
        base_sha=base,
        branch="worker",
        worktree=str(repo),
        task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
    )
    diverged = replace(identity, base_sha=manager_head)
    copy_result = validate_attempt_copies(identity, diverged)
    require(not copy_result.ok, "attempt mutation was accepted")
    require(copy_result.issues[0].code == "attempt-state-diverged", "wrong attempt diagnostic")

    active = MergeTransaction(
        transaction_id="merge-e2e",
        task_id="T001",
        attempt_id="T001-A001",
        branch="worker",
        pre_merge_head=manager_head,
        worker_head=worker_head,
        result_head=worker_head,
        index_state="clean",
        changed_paths=("README.md",),
        status=MERGE_ACTIVE,
    )
    content_conflict = replace(active, status=MERGE_CONTENT_CONFLICT)
    completed = replace(content_conflict, status=MERGE_COMPLETED)
    require(validate_merge_transaction(active, content_conflict).ok, "conflict transition rejected")
    require(validate_merge_transaction(content_conflict, completed).ok, "continue transition rejected")
    manual = replace(content_conflict, worker_head=manager_head)
    manual_result = validate_merge_transaction(content_conflict, manual)
    require(not manual_result.ok, "manual integration trace was accepted")
    require(manual_result.issues[0].code == "manual-integration-trace", "wrong merge diagnostic")
    return {
        "actual_git_conflict": True,
        "merge_abort_clean": True,
        "attempt_diagnostic": copy_result.issues[0].code,
        "manual_merge_diagnostic": manual_result.issues[0].code,
    }


def scenario_merge_conflict_recovery(ctx: dict[str, Any]) -> dict[str, Any]:
    """Drive `hloop merge` through a real Git conflict, not the primitives directly."""

    root: Path = ctx["root"]
    env: dict[str, str] = ctx["env"]
    repo = make_repo(root, "merge-conflict-repo")
    namespace = "merge-conflict-e2e"
    run(
        hloop_command(
            repo,
            namespace,
            "init",
            "--goal",
            "synthetic merge conflict recovery",
            "--integration",
            "master",
            "--persistence",
            "local-only",
        ),
        cwd=root,
        env=env,
    )
    path = state_path(repo, namespace)
    task_id = "T900"
    branch = "synthetic/merge-conflict-task"

    base_sha = git(repo, "rev-parse", "master").stdout.strip()
    git(repo, "switch", "-c", branch)
    (repo / "README.md").write_text("worker change\n", encoding="utf-8")
    git(repo, "commit", "-am", "worker change")
    result_dir = repo / ".ai" / "herdr-dev-loop" / "loops" / namespace / "results" / task_id
    result_dir.mkdir(parents=True, exist_ok=True)
    result_meta = {
        "task_id": task_id,
        "run_id": json.loads(path.read_text(encoding="utf-8"))["run_id"],
        "skill_version": ctx["runtime_version"],
        "contract_schema_revision": 2,
        "attempt_id": f"{task_id}-A001",
        "status": "done",
        "merge_ready": True,
        "branch": branch,
        "head_sha": "HEAD",
        "base_sha": base_sha,
        "changed_files": ["README.md"],
        "validation_recorded": True,
        "validation_commands": ["synthetic worker validation"],
        "validation_results": ["passed"],
        "validation_summary": "synthetic validation passed",
        "blocking_questions": [],
        "handoff": False,
    }
    result_text = hloop.frontmatter(result_meta) + "\n\n# Result\n"
    (result_dir / "result.md").write_text(result_text, encoding="utf-8")
    git(repo, "add", "-f", str(result_dir.relative_to(repo)))
    git(repo, "commit", "-m", "worker result")
    worker_head = git(repo, "rev-parse", branch).stdout.strip()
    git(repo, "switch", "master")
    (repo / "README.md").write_text("manager change\n", encoding="utf-8")
    git(repo, "commit", "-am", "manager change")

    def write_task() -> None:
        state = json.loads(path.read_text(encoding="utf-8"))
        task_path = path.parent / "tasks" / f"{task_id}.md"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            hloop.frontmatter(
                {
                    "id": task_id,
                    "status": "result_reported",
                    "contract_schema_revision": 2,
                }
            )
            + "\n\n# Synthetic legacy task\n",
            encoding="utf-8",
        )
        result_path = path.parent / "results" / task_id / "result.md"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(result_text, encoding="utf-8")
        state["tasks"][task_id] = {
            "status": "result_reported",
            "branch": branch,
            "worker_base_sha": base_sha,
            "base_sha": base_sha,
            "head_sha": worker_head,
            "result_status": "done",
            "merge_ready": True,
            "validation_recorded": True,
            "validation_commands": ["synthetic worker validation"],
            "validation_results": ["passed"],
            "write_allow": ["README.md"],
            "write_deny": [],
            "active_attempt_id": f"{task_id}-A001",
            "attempt_id": f"{task_id}-A001",
            "attempt_no": 1,
            "contract_schema_revision": 2,
            "blocking_questions": [],
            "result_path": f"{worker_head}:{result_dir.relative_to(repo).as_posix()}/result.md",
            "committed_result_path": f"{worker_head}:{result_dir.relative_to(repo).as_posix()}/result.md",
            "harvested_at": now(),
            "artifact_digest": hloop._sha256_labelled(result_path.read_bytes()),
        }
        state["tasks"][task_id]["legacy_result_acceptance"] = {
            "run_id": state["run_id"],
            "task_id": task_id,
            "attempt_id": f"{task_id}-A001",
            "task_contract_digest": hloop._labelled_contract_digest(
                hashlib.sha256(task_path.read_bytes()).hexdigest()
            ),
            "result_artifact_digest": hloop._sha256_labelled(
                result_path.read_bytes()
            ),
            "head_sha": worker_head,
            "reason": "synthetic migration compatibility fixture",
            "accepted_at": now(),
        }
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    write_task()
    result_rel = (result_dir / "result.md").relative_to(repo).as_posix()
    git(repo, "restore", "--source", branch, "--", result_rel)

    conflict = run(
        hloop_command(repo, namespace, "merge", task_id, "--mode", "squash"),
        cwd=root,
        env=env,
        expected={1},
    )
    require(
        "conflict" in (conflict.stdout + conflict.stderr).lower(),
        "expected hloop merge to report a real content conflict",
    )
    after_conflict = json.loads(path.read_text(encoding="utf-8"))
    require(
        after_conflict.get("active_merge", {}).get("status") == "content-conflict",
        "merge transaction did not record content-conflict status",
    )
    require(
        after_conflict["tasks"][task_id]["status"] == "blocked_merge_conflict",
        "task status was not marked blocked on conflict",
    )

    run(hloop_command(repo, namespace, "merge", task_id, "--abort"), cwd=root, env=env)
    after_abort = json.loads(path.read_text(encoding="utf-8"))
    require(not after_abort.get("active_merge"), "abort did not clear the merge transaction")
    require(
        after_abort["tasks"][task_id]["status"] == "result_reported",
        "abort did not restore the task to result_reported",
    )
    require(
        not git(repo, "status", "--porcelain", "--", "README.md").stdout.strip(),
        "abort left the product file dirty",
    )

    git(repo, "restore", "--source", branch, "--", result_rel)
    run(
        hloop_command(repo, namespace, "merge", task_id, "--mode", "squash"),
        cwd=root,
        env=env,
        expected={1},
    )
    (repo / "README.md").write_text("resolved by manager\n", encoding="utf-8")
    git(repo, "add", "README.md")
    run(hloop_command(repo, namespace, "merge", task_id, "--continue"), cwd=root, env=env)
    completed = json.loads(path.read_text(encoding="utf-8"))
    require(completed["tasks"][task_id]["status"] == "merged", "continue did not complete the merge")
    require(not completed.get("active_merge"), "active_merge still present after continue")
    require(
        (repo / "README.md").read_text(encoding="utf-8") == "resolved by manager\n",
        "resolved content missing from integration branch after continue",
    )

    stale_abort = run(
        hloop_command(repo, namespace, "merge", task_id, "--abort"),
        cwd=root,
        env=env,
        expected={2},
    )
    require(
        "no active merge transaction" in (stale_abort.stdout + stale_abort.stderr),
        "abort without an active transaction did not fail closed",
    )

    return {
        "conflict_status": after_conflict.get("active_merge", {}).get("status"),
        "task_status_after_conflict": after_conflict["tasks"][task_id]["status"],
        "task_status_after_abort": after_abort["tasks"][task_id]["status"],
        "task_status_after_continue": completed["tasks"][task_id]["status"],
    }


def scenario_cleanup_gate_resolution(ctx: dict[str, Any]) -> dict[str, Any]:
    """Drive `hloop cleanup resolve` for a worker worktree cleanup failure."""

    root: Path = ctx["root"]
    env: dict[str, str] = ctx["env"]
    repo = make_repo(root, "cleanup-gate-repo")
    namespace = "cleanup-gate-e2e"
    run(
        hloop_command(
            repo,
            namespace,
            "init",
            "--goal",
            "synthetic cleanup gate",
            "--integration",
            "master",
            "--persistence",
            "local-only",
        ),
        cwd=root,
        env=env,
    )
    path = state_path(repo, namespace)
    task_id = "T901"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["tasks"][task_id] = {
        "status": "merged",
        "branch": "synthetic/cleanup-gate-task",
        "worktree": str(root / "does-not-exist" / task_id),
        "cleanup_pending": False,
        "cleanup_done": False,
        "worktree_cleanup_status": "failed",
        "worktree_cleanup_error": "synthetic: git worktree remove failed",
        "worktree_cleanup_error_fingerprint": "synthetic-fingerprint",
        "attempt_id": f"{task_id}-A001",
    }
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    finish_probe = run(
        hloop_command(repo, namespace, "finish"),
        cwd=root,
        env=env,
        expected={2},
    )
    require(
        "cleanup is unresolved" in (finish_probe.stdout + finish_probe.stderr),
        "finish did not block on the unresolved Worker cleanup failure",
    )

    accepted = run(
        hloop_command(
            repo,
            namespace,
            "cleanup",
            "resolve",
            task_id,
            "--status",
            "accepted-risk",
            "--reason",
            "synthetic: worktree already removed manually by Manager",
        ),
        cwd=root,
        env=env,
    )
    require("resolved cleanup" in accepted.stdout, "cleanup resolve did not confirm the resolution")
    resolved_state = json.loads(path.read_text(encoding="utf-8"))
    resolutions = resolved_state["tasks"][task_id].get("cleanup_resolutions") or {}
    require("worktree" in resolutions, "accepted-risk resolution was not recorded on the task")
    require(resolutions["worktree"]["status"] == "accepted-risk", "resolution status mismatch")
    require(
        len(resolved_state.get("cleanup_history") or []) >= 1,
        "cleanup resolution was not appended to cleanup_history",
    )

    return {
        "blocked_finish_status": finish_probe.returncode,
        "resolution_status": resolutions["worktree"]["status"],
        "cleanup_history_count": len(resolved_state.get("cleanup_history") or []),
    }


def scenario_report_broker(ctx: dict[str, Any]) -> dict[str, Any]:
    root: Path = ctx["root"]
    repo: Path = ctx["repo"]
    env: dict[str, str] = ctx["env"]
    namespace: str = ctx["namespace"]
    path = state_path(repo, namespace)
    state = json.loads(path.read_text(encoding="utf-8"))

    t001_digest = hashlib.sha256(b"T001").hexdigest()
    t002_digest = hashlib.sha256(b"T002").hexdigest()
    previous_namespace = hloop.LOOP_NAMESPACE
    hloop.configure_loop_namespace(namespace)
    try:
        t001_credential, _ = hloop.register_role_report_identity_and_ack_floor(
            repo,
            state,
            role_id="T001",
            attempt_id="T001-A001",
            task_contract_digest=t001_digest,
        )
        t001_token = json.loads(t001_credential.read_text(encoding="utf-8"))["token"]
        require(
            stat.S_IMODE(t001_credential.stat().st_mode) == 0o600,
            "role report credential is not mode 0600",
        )
        report_contract = hloop.report_contract_text(
            "T001",
            "T001-A001",
            state,
            report_credential_file=str(t001_credential),
            task_contract_digest=t001_digest,
            manager_repo=str(repo.resolve()),
        )
        require(t001_token not in report_contract, "report token leaked into role prompt")
        store = hloop._open_broker_store(repo)
        with store.transaction() as transaction:
            store.register_active_role(
                transaction,
                run_id=state["run_id"],
                role_id="T002",
                attempt_id="T002-A001",
                task_contract_digest=t002_digest,
                token="synthetic-t002-token",
            )
    finally:
        hloop.configure_loop_namespace(previous_namespace)
    event_id = str(uuid.uuid4())
    run(
        hloop_command(
            repo,
            namespace,
            "agent",
            "report",
            "--role-id",
            "T001",
            "--attempt-id",
            "T001-A001",
            "--run-id",
            state["run_id"],
            "--task-contract-digest",
            t001_digest,
            "--report-credential-file",
            str(t001_credential),
            "--event-id",
            event_id,
            "--type",
            "ack",
            "--stage",
            "planning",
            "--summary",
            "contract understood",
            "--understood-goal",
            "exercise semantic ACK",
            "--scope",
            "README.md",
            "--acceptance",
            "broker wake is durable",
            "--approach",
            "submit one structured ACK",
            "--next",
            "wait for Manager",
        ),
        cwd=root,
        env=env,
    )
    sleep_result = run(
        hloop_command(
            repo,
            namespace,
            "manager",
            "sleep",
            "--ttl-seconds",
            "1",
            "--manager-session-id",
            "synthetic-manager",
            "--pane-id",
            "synthetic-pane",
        ),
        cwd=root,
        env=env,
    )
    require("manager sleep returned: report" in sleep_result.stdout, "Manager sleep did not surface the pending ACK")
    pending = run(
        hloop_command(repo, namespace, "manager", "next"),
        cwd=root,
        env=env,
    )
    require(event_id in pending.stdout, "ACK did not wake Manager")
    run(
        hloop_command(repo, namespace, "inbox", "ack", event_id),
        cwd=root,
        env=env,
    )
    empty = run(
        hloop_command(repo, namespace, "manager", "next"),
        cwd=root,
        env=env,
    )
    require("no pending wakes" in empty.stdout, "wake was not consumed")

    spooled_id = str(uuid.uuid4())
    milestone = prepare_client_event(
        {
            "run_id": state["run_id"],
            "role_id": "T002",
            "attempt_id": "T002-A001",
            "task_contract_digest": t002_digest,
            "type": "milestone",
            "stage": "testing",
            "summary": "broker recovery fixture",
            "risks": ["synthetic broker outage already recovered"],
            "next": "recover the spool",
            "needs_manager": False,
            "evidence_refs": ["synthetic:e2e"],
            "created_at": utc_now(),
        },
        event_id=spooled_id,
    )
    previous_namespace = hloop.LOOP_NAMESPACE
    hloop.configure_loop_namespace(namespace)
    try:
        spool_dir = hloop.broker_spool_dir(repo)
    finally:
        hloop.configure_loop_namespace(previous_namespace)
    spool_client_event(
        spool_dir,
        milestone,
        authentication={
            "run_id": state["run_id"],
            "role_id": "T002",
            "attempt_id": "T002-A001",
            "task_contract_digest": t002_digest,
            "token": "synthetic-t002-token",
        },
    )
    poison_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    (spool_dir / f"{poison_id}.json").write_text(
        json.dumps({"not": "an event"}), encoding="utf-8"
    )
    recovered = run(
        hloop_command(repo, namespace, "broker", "recover"),
        cwd=root,
        env=env,
    )
    require(
        "replayed 1 spooled report" in recovered.stdout,
        "spool recovery did not replay the valid entry alongside a poison entry",
    )
    require(
        "quarantined 1 poison entrie" in recovered.stdout,
        "spool recovery did not report the quarantined poison entry",
    )
    quarantined_files = [
        path
        for path in (spool_dir / "quarantine").glob("*.json")
        if not path.name.endswith(".audit.json")
    ]
    require(
        [path.name for path in quarantined_files] == [f"{poison_id}.json"],
        "poison spool entry was not quarantined by itself",
    )
    broker_status = run(
        hloop_command(repo, namespace, "broker", "status"),
        cwd=root,
        env=env,
    )
    status = json.loads(broker_status.stdout)
    require(status["spooled"] == 0, "recovered spool was not cleared")
    require(status["spool_quarantined"] == 1, "quarantined spool count is stale")
    require(status["events"] == 2, "broker event count mismatch")
    return {
        "semantic_ack_event": event_id,
        "wake_consumed": True,
        "recovered_event": spooled_id,
        "quarantined_event": poison_id,
        "broker_counts": status,
    }


def scenario_scout_liaison_reports(ctx: dict[str, Any]) -> dict[str, Any]:
    root: Path = ctx["root"]
    repo: Path = ctx["repo"]
    env: dict[str, str] = ctx["env"]
    namespace: str = ctx["namespace"]
    path = state_path(repo, namespace)
    state = json.loads(path.read_text(encoding="utf-8"))
    previous_namespace = hloop.LOOP_NAMESPACE
    hloop.configure_loop_namespace(namespace)
    try:
        identities: dict[str, tuple[str, str, Path]] = {}
        state.setdefault("decision_liaisons", {})
        for role_id, attempt_id in (
            ("S001", "S001-A001"),
            ("L-D001", "L-D001-A001"),
        ):
            digest = hashlib.sha256(role_id.encode("utf-8")).hexdigest()
            credential, floor = hloop.register_role_report_identity_and_ack_floor(
                repo,
                state,
                role_id=role_id,
                attempt_id=attempt_id,
                task_contract_digest=digest,
            )
            require(
                stat.S_IMODE(credential.stat().st_mode) == 0o600,
                f"{role_id} credential is not mode 0600",
            )
            role_state = {
                "role_id": role_id,
                "status": "running",
                "gate_status": "running",
                "attempt_no": 1,
                "attempt_id": attempt_id,
                "skill_version": ctx["runtime_version"],
                "task_contract_digest": digest,
            }
            hloop.arm_initial_semantic_ack_barrier(
                role_state,
                attempt_id=attempt_id,
                contract_digest=digest,
                required_reack_after_sequence=floor,
            )
            if role_id == "S001":
                state["specification_scout_run"] = role_state
            else:
                state["decision_liaisons"]["D001"] = {
                    **role_state,
                    "decision_id": "D001",
                }
            identities[role_id] = (attempt_id, digest, credential)
        hloop.save_state(repo, state)

        def send_report(role_id: str, report_type: str, label: str) -> str:
            attempt_id, digest, credential = identities[role_id]
            event_id = str(uuid.uuid4())
            completion = report_type == "completion"
            attention = report_type == "attention"
            command = hloop_command(
                repo,
                namespace,
                "agent",
                "report",
                "--role-id",
                role_id,
                "--attempt-id",
                attempt_id,
                "--run-id",
                state["run_id"],
                "--task-contract-digest",
                digest,
                "--report-credential-file",
                str(credential),
                "--event-id",
                event_id,
                "--type",
                report_type,
                "--stage",
                "completed" if completion else ("blocked" if attention else "planning"),
                "--summary",
                label,
                "--next",
                "Manager handoff" if completion else "wait for Manager",
                "--evidence-ref",
                "skills/herdr-dev-loop/scripts/hloop:1",
            )
            if report_type == "ack":
                command.extend(
                    [
                        "--understood-goal",
                        f"perform {role_id}",
                        "--scope",
                        "decision artifact only",
                        "--acceptance",
                        "Manager approves before material work",
                        "--approach",
                        "use the shared report contract",
                    ]
                )
            elif attention:
                command.extend(
                    [
                        "--impact",
                        "Manager must inspect the decision role",
                        "--attempted",
                        "persisted role evidence",
                        "--option-text",
                        "inspect the durable report",
                        "--recommendation",
                        "inspect and respond",
                        "--blocked-scope",
                        "decision role",
                    ]
                )
            else:
                command.extend(
                    [
                        "--artifact",
                        "decisions/artifact.md",
                        "--head-sha",
                        "a" * 40,
                        "--validation-result-ref",
                        "synthetic role validation",
                        "--residual-risk",
                        "none",
                        "--handoff",
                        "Manager may harvest",
                    ]
                )
            run(command, cwd=root, env=env)
            return event_id

        send_report("S001", "ack", "Scout contract understood")
        run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "S001",
                "--decision",
                "approve",
                "--reason",
                "Scout scope confirmed",
            ),
            cwd=root,
            env=env,
        )

        send_report("L-D001", "ack", "Liaison first ACK")
        run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "L-D001",
                "--decision",
                "reject",
                "--reason",
                "question wording incomplete",
            ),
            cwd=root,
            env=env,
        )
        stale = run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "L-D001",
                "--decision",
                "approve",
                "--reason",
                "stale ACK must fail",
            ),
            cwd=root,
            env=env,
            expected={1, 2},
        )
        require("corrected semantic ACK" in stale.stderr, "reject did not require re-ACK")
        send_report("L-D001", "ack", "Liaison corrected ACK")
        run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "L-D001",
                "--decision",
                "approve",
                "--reason",
                "corrected ACK accepted",
            ),
            cwd=root,
            env=env,
        )

        state = json.loads(path.read_text(encoding="utf-8"))
        liaison = state["decision_liaisons"]["D001"]
        liaison["pane_id"] = "synthetic-liaison-pane"
        hloop.save_state(repo, state)
        original_preflight = hloop.preflight_loop
        original_send = hloop.send_agent_tui_message
        try:
            hloop.preflight_loop = lambda *_args, **_kwargs: state
            hloop.send_agent_tui_message = lambda *_args, **_kwargs: None
            hloop.cmd_agent_message(
                SimpleNamespace(
                    repo=str(repo),
                    agent_id="L-D001",
                    message="Use the revised public wording",
                    file=None,
                    timeout_ms=10,
                    input_settle_ms=0,
                    submit_verify_ms=1,
                    submit_attempts=1,
                    contract_changing=True,
                )
            )
        finally:
            hloop.preflight_loop = original_preflight
            hloop.send_agent_tui_message = original_send
        state = json.loads(path.read_text(encoding="utf-8"))
        liaison = state["decision_liaisons"]["D001"]
        rebound_digest = str(liaison.get("active_report_contract_digest") or "")
        barrier = liaison.get("semantic_ack_barrier") or {}
        require(
            str(barrier.get("report_identity_status") or "") == "bound"
            and rebound_digest == str(barrier.get("digest") or "")
            and rebound_digest == str(barrier.get("rendered_exchange_digest") or ""),
            "Liaison contract-changing message did not complete identity rebinding",
        )
        attempt_id, _, credential = identities["L-D001"]
        identities["L-D001"] = (attempt_id, rebound_digest, credential)
        liaison.pop("pane_id", None)
        hloop.save_state(repo, state)
        run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "L-D001",
                "--decision",
                "timeout",
                "--reason",
                "Manager approval timed out",
            ),
            cwd=root,
            env=env,
        )
        timed_out = run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "L-D001",
                "--decision",
                "approve",
                "--reason",
                "pre-timeout ACK must fail",
            ),
            cwd=root,
            env=env,
            expected={1, 2},
        )
        require("corrected semantic ACK" in timed_out.stderr, "timeout did not require re-ACK")
        send_report("L-D001", "ack", "Liaison ACK after timeout")
        run(
            hloop_command(
                repo,
                namespace,
                "agent",
                "ack",
                "resolve",
                "L-D001",
                "--decision",
                "approve",
                "--reason",
                "fresh ACK after timeout",
            ),
            cwd=root,
            env=env,
        )

        # Regression for the live failure: selecting the recommendation in an
        # artifact immediately after presentation has no subsequent-user
        # provenance and must fail closed. Free text from a later direct user
        # turn remains valid without defaulting selected_option.
        premature_recommendation = {
            "responded_by": "liaison",
            "responded_at": "2026-07-15T12:00:00+00:00",
            "selected_option": "opt_1",
        }
        require(
            "response_source" in hloop.decision_liaison_response_provenance_error(
                premature_recommendation
            ),
            "recommendation-only Liaison response did not fail closed",
        )
        explicit_free_text = {
            **premature_recommendation,
            "responded_at": "2026-07-15T12:00:01+00:00",
            "response_source": "explicit-user-input",
            "response_channel": "same-pane",
            "response_turn": "after-question",
            "user_input_received_at": "2026-07-15T12:00:00+00:00",
            "user_input_kind": "free-text",
        }
        explicit_free_text.pop("selected_option")
        require(
            hloop.decision_liaison_response_provenance_error(explicit_free_text) == "",
            "explicit later free-text Liaison response was rejected",
        )

        attention_ids = {
            role_id: send_report(role_id, "attention", f"{role_id} needs attention")
            for role_id in identities
        }
        completion_ids = {
            role_id: send_report(role_id, "completion", f"{role_id} completed")
            for role_id in identities
        }
        pending = run(
            hloop_command(repo, namespace, "manager", "next"), cwd=root, env=env
        )
        require("S001" in pending.stdout, "Scout completion did not reach Manager inbox")
        require("L-D001" in pending.stdout, "Liaison completion did not reach Manager inbox")

        state = json.loads(path.read_text(encoding="utf-8"))
        state["specification_scout_run"].update(
            {"attempt_id": "S001-A002", "pane_id": "synthetic-scout", "status": "running"}
        )
        state["decision_liaisons"]["D001"].update(
            {
                "attempt_id": "L-D001-A002",
                "pane_id": "synthetic-liaison",
                "status": "running",
            }
        )
        hloop.save_state(repo, state)
        captured: dict[str, Any] = {}

        class FakeSleepSupervisor:
            def __init__(self, *_args, **_kwargs):
                pass

            def sleep(self, *, timeout_seconds, fallback_watches):
                captured["watches"] = list(fallback_watches)
                return SimpleNamespace(
                    lease_generation=1,
                    reason="fallback",
                    event_ids=(),
                    drained_reports=0,
                    fallback=SimpleNamespace(
                        pane_id="synthetic-scout", status="done", returncode=0
                    ),
                )

        original_supervisor = hloop.hloop_supervisor.ManagerSleepSupervisor
        try:
            hloop.hloop_supervisor.ManagerSleepSupervisor = FakeSleepSupervisor
            hloop.cmd_manager_sleep(
                SimpleNamespace(
                    repo=str(repo),
                    ttl_seconds=1,
                    manager_session_id="synthetic-manager",
                    pane_id="synthetic-manager-pane",
                )
            )
        finally:
            hloop.hloop_supervisor.ManagerSleepSupervisor = original_supervisor
        watches = {(item.pane_id, item.status) for item in captured["watches"]}
        for pane_id in ("synthetic-scout", "synthetic-liaison"):
            require(
                {status for pane, status in watches if pane == pane_id}
                == set(hloop.hloop_supervisor.FALLBACK_STATUSES),
                f"no-report fallback watches missing for {pane_id}",
            )
        state = json.loads(path.read_text(encoding="utf-8"))
        for role_id in ("S001", "L-D001"):
            hloop.revoke_active_role_report_identity(repo, state, role_id)
        state["specification_scout_run"].update(
            {"status": "aborted", "gate_status": "aborted"}
        )
        state["specification_scout_run"].pop("pane_id", None)
        state["decision_liaisons"].pop("D001", None)
        hloop.save_state(repo, state)
        return {
            "scout_ack_approved": True,
            "liaison_reject_reack": True,
            "liaison_timeout_reack": True,
            "liaison_recommendation_is_not_consent": True,
            "liaison_explicit_free_text_without_default_option": True,
            "contract_changing_message": True,
            "attention_events": attention_ids,
            "completion_events": completion_ids,
            "no_report_fallback_roles": ["S001", "L-D001"],
        }
    finally:
        hloop.configure_loop_namespace(previous_namespace)


def scenario_requirements_decisions(ctx: dict[str, Any]) -> dict[str, Any]:
    root: Path = ctx["root"]
    repo: Path = ctx["repo"]
    env: dict[str, str] = ctx["env"]
    namespace: str = ctx["namespace"]
    path = state_path(repo, namespace)
    no_herdr_env = dict(env)
    no_herdr_env.pop("HERDR_ENV", None)
    secret = "synthetic-secret-abcdefghijklmnop"
    run(
        hloop_command(
            repo,
            namespace,
            "input",
            "record",
            "--source",
            "synthetic-user",
            "--text",
            f"add release evidence token={secret}",
        ),
        cwd=root,
        env=env,
    )
    state = json.loads(path.read_text(encoding="utf-8"))
    require(secret not in json.dumps(state), "raw credential leaked into STATE")
    input_file = root / "synthetic-user-input.txt"
    input_file.write_text("preserve compatibility in the public API\n", encoding="utf-8")
    run(
        hloop_command(
            repo,
            namespace,
            "input",
            "record",
            "--source",
            "synthetic-file",
            "--file",
            str(input_file),
        ),
        cwd=root,
        env=env,
    )
    run(
        hloop_command(
            repo,
            namespace,
            "requirements",
            "extract",
            "--input",
            "U0001",
            "--input",
            "U0002",
            "--acceptance",
            "structured release evidence exists",
            "--priority",
            "P1",
        ),
        cwd=root,
        env=env,
    )
    draft_state = json.loads(path.read_text(encoding="utf-8"))
    require(not draft_state.get("requirements"), "draft silently entered accepted ledger")
    require(
        draft_state["requirement_drafts"]["DRQ-001"]["confirmation_required"],
        "draft confirmation boundary missing",
    )
    run(
        hloop_command(
            repo,
            namespace,
            "requirements",
            "accept",
            "--draft",
            "DRQ-001",
            "--id",
            "REQ-001",
        ),
        cwd=root,
        env=env,
    )
    for status in ("in_progress", "implemented_unverified"):
        args = hloop_command(
            repo,
            namespace,
            "progress",
            "record",
            "--requirement-id",
            "REQ-001",
            "--status",
            status,
        )
        if status == "in_progress":
            args.extend(["--task-id", "T001", "--remaining-work", "run release gates"])
        run(args, cwd=root, env=env)
    run(
        hloop_command(
            repo,
            namespace,
            "context",
            "update",
            "--source",
            "U0001",
            "--text",
            "Preserve structured release evidence for the final report.",
        ),
        cwd=root,
        env=env,
    )
    outcome = run(
        hloop_command(repo, namespace, "outcome", "show", "--requirement-id", "REQ-001"),
        cwd=root,
        env=env,
    )
    projected = json.loads(outcome.stdout)
    require(projected["progress"]["status"] == "implemented_unverified", "wrong progress projection")

    run(
        hloop_command(
            repo,
            namespace,
            "decision",
            "new",
            "--id",
            "D001",
            "--title",
            "retain compatibility",
            "--class",
            "blocking-user",
            "--affects",
            "T004",
            "--option",
            "retain",
            "--option",
            "break",
            "--recommend-option",
            "opt_1",
            "--recommend-rationale",
            "safer migration",
        ),
        cwd=root,
        env=no_herdr_env,
    )
    question_path = path.parent / "decisions" / "D001" / "QUESTION.md"
    require(question_path.is_file(), "decision question artifact missing")
    require("# 判断のお願い" in question_path.read_text(encoding="utf-8"), "liaison question is not plain Japanese")
    attention_state = json.loads(path.read_text(encoding="utf-8"))
    require(
        attention_state["decision_attention"]["D001"]["status"] == "manager-fallback",
        "decision attention fallback was not recorded",
    )
    require(
        len(attention_state.get("decision_attention_events") or []) == 1,
        "decision attention was not emitted exactly once",
    )
    run(
        hloop_command(
            repo,
            namespace,
            "specification-scout",
            "start",
            "--force",
        ),
        cwd=root,
        env=no_herdr_env,
    )
    fallback_state = json.loads(path.read_text(encoding="utf-8"))
    require(
        fallback_state["specification_scout_run"]["mode"] == "manager-fallback",
        "Scout fallback missing",
    )
    run(
        hloop_command(
            repo,
            namespace,
            "specification-scout",
            "close",
            "--verdict",
            "no-decision",
            "--reason",
            "synthetic Manager review found no extra decision",
        ),
        cwd=root,
        env=env,
    )
    liaison = run(
        hloop_command(
            repo,
            namespace,
            "decision",
            "liaison",
            "start",
            "--id",
            "D001",
        ),
        cwd=root,
        env=no_herdr_env,
    )
    require("# 判断のお願い" in liaison.stdout, "Liaison fallback did not present question")
    require("T004" not in liaison.stdout, "Liaison user text leaked an internal task id")
    run(
        hloop_command(
            repo,
            namespace,
            "decision",
            "respond",
            "--id",
            "D001",
            "--text",
            "retain compatibility",
            "--option",
            "opt_1",
        ),
        cwd=root,
        env=env,
    )
    run(
        hloop_command(
            repo,
            namespace,
            "decision",
            "resolve",
            "--id",
            "D001",
            "--outcome",
            "accepted",
            "--selected-option",
            "opt_1",
            "--note",
            "synthetic resolution",
            "--resolved-by",
            "synthetic-manager",
        ),
        cwd=root,
        env=env,
    )
    final_state = json.loads(path.read_text(encoding="utf-8"))
    require(final_state["decisions"]["D001"]["status"] == "accepted", "decision not resolved")
    require(
        len(final_state.get("decision_attention_events") or []) == 1,
        "decision attention was duplicated by response or resolution refresh",
    )
    artifact_paths = [
        path.parent / "requirements" / "REQUIREMENTS.md",
        path.parent / "requirements" / "STATUS.md",
        path.parent / "context" / "MANAGER_CONTEXT.md",
        path.parent / "progress" / "LATEST.md",
        path.parent / "decisions" / "D001" / "RESPONSE.md",
    ]
    require(all(item.is_file() for item in artifact_paths), "requirement/context/progress/decision artifact missing")
    return {
        "input_redacted_from_state": True,
        "input_file_and_text": True,
        "confirmation_boundary": True,
        "requirement_status": projected["progress"]["status"],
        "decision_status": final_state["decisions"]["D001"]["status"],
        "scout_status": final_state["specification_scout_run"]["status"],
        "liaison_mode": final_state["decision_liaisons"]["D001"]["mode"],
        "decision_attention_idempotent": True,
    }


def finding(finding_id: str, *, path: str, symbol: str) -> FindingCandidate:
    return FindingCandidate(
        finding_id=finding_id,
        provider="codex",
        head_sha="a" * 40,
        discovering_agent="codex-discovery-01",
        severity="P2",
        confidence=0.9,
        title="synthetic finding",
        file_path=path,
        line=1,
        symbol=symbol,
        trigger="synthetic trigger",
        product_impact="synthetic impact",
        origin="introduced",
        proposed_fix=f"fix {symbol}",
        requires_spec_decision=False,
    )


def _fixture_state(fixture: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
        state_path(fixture["repo"], fixture["namespace"]).read_text(encoding="utf-8")
    )


def _save_fixture_state(fixture: dict[str, Any], state: dict[str, Any]) -> None:
    state_path(fixture["repo"], fixture["namespace"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fixture_cli(
    fixture: dict[str, Any], *args: str, expected: int | set[int] = 0
) -> subprocess.CompletedProcess[str]:
    return run(
        hloop_command(fixture["repo"], fixture["namespace"], *args),
        cwd=fixture["root"],
        env=fixture["env"],
        expected=expected,
    )


def _validation_log_count(fixture: dict[str, Any]) -> int:
    journal_path = state_path(fixture["repo"], fixture["namespace"]).parent / "JOURNAL.md"
    if not journal_path.is_file():
        return 0
    return journal_path.read_text(encoding="utf-8").count("Validation results:")


def _mark_tasks_merged(fixture: dict[str, Any], task_ids: tuple[str, ...] | None = None) -> None:
    state = _fixture_state(fixture)
    target = git(fixture["repo"], "rev-parse", "master").stdout.strip()
    selected = task_ids or tuple(state.get("tasks", {}).keys())
    for task_id in selected:
        task = state.get("tasks", {}).get(task_id)
        require(isinstance(task, dict), f"missing synthetic task {task_id}")
        task.update(
            {
                "status": "merged",
                "result_status": "done",
                "merge_ready": True,
                "cleanup_done": True,
                "cleanup_pending": False,
                "head_sha": target,
                "reported_head_sha": target,
                "worker_base_sha": target,
                "validation_recorded": True,
                "validation_commands": ["true"],
                "validation_results": ["passed"],
            }
        )
    state.update(
        {
            "integration_head_sha": target,
            "completion_target_sha": target,
            "phase": "dispatching",
            "needs_review": False,
            "needs_gap_check": False,
        }
    )
    _save_fixture_state(fixture, state)


def _run_fixture_validation(fixture: dict[str, Any]) -> int:
    before = _validation_log_count(fixture)
    _fixture_cli(fixture, "validate", "--command", "true", "--no-cleanup")
    after = _validation_log_count(fixture)
    state = _fixture_state(fixture)
    reused = bool((state.get("last_validation") or {}).get("reused"))
    require(
        after > before or reused,
        "validation command did not create or reuse a validation event",
    )
    return after


def _new_synthetic_fixture(
    ctx: dict[str, Any],
    label: str,
    *,
    task_count: int = 1,
    merge_tasks: bool = True,
) -> dict[str, Any]:
    root: Path = ctx["root"]
    env = dict(ctx["env"])
    env["HLOOP_CONFIG_HOME"] = str(root / f"config-home-{label}")
    env["XDG_CONFIG_HOME"] = str(root / f"xdg-{label}")
    repo = make_repo(root, f"{label}-repo")
    namespace = f"synthetic-{label}"
    _run = lambda *args, expected=0: run(
        hloop_command(repo, namespace, *args),
        cwd=root,
        env=env,
        expected=expected,
    )
    _run(
        "init",
        "--goal",
        f"bounded convergence fixture: {label}",
        "--integration",
        "master",
        "--max-reviewers",
        "0",
        "--max-gap-auditors",
        "0",
        "--review-after-merges",
        "999",
        "--gap-after-merges",
        "999",
        "--validation",
        "true",
        "--worker-runner",
        "exec",
        "--reviewer-runner",
        "exec",
        "--gap-runner",
        "exec",
        "--specification-scout",
        "off",
        "--manager-qa-profile",
        "none",
    )
    loop = repo / ".ai" / "herdr-dev-loop" / "loops" / namespace
    plan_path = loop / "PLAN.md"
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8")
        + "\n- P005: synthetic bounded-convergence implementation item\n",
        encoding="utf-8",
    )
    source_args = (
        "--source-ref",
        f".ai/herdr-dev-loop/loops/{namespace}/MISSION.md",
        "--source-ref",
        f".ai/herdr-dev-loop/loops/{namespace}/PLAN.md",
        "--source-ref",
        f".ai/herdr-dev-loop/loops/{namespace}/PROFILE.md",
        "--source-ref",
        f".ai/herdr-dev-loop/loops/{namespace}/DECISIONS.md",
    )
    _run(
        "release-scope",
        "lock",
        *source_args,
        "--plan-item-ref",
        "P005",
        "--scope-ref",
        "release-contract",
    )
    _run("batch", "start", f"initial batch for {label}", "--id", "B001")
    for index in range(1, task_count + 1):
        task_id = f"T{index:03d}"
        _run(
            "task",
            "new",
            f"synthetic {label} task {task_id}",
            "--id",
            task_id,
            "--kind",
            "implementation",
            "--write-allow",
            f"synthetic/{label}/{task_id}.txt",
            "--task-origin",
            "planned",
            "--plan-item-ref",
            "P005",
            "--acceptance",
            "synthetic task is represented in the fixture",
            "--preserved-invariant",
            "preserve synthetic fixture behavior",
            "--regression-check",
            "run the synthetic fixture regression",
            "--risk-class",
            "normal",
            "--required-gate",
            "patch_review",
            "--required-gate",
            "full_suite",
        )
    _run("batch", "close", "B001", "--summary", f"closed initial batch for {label}")
    fixture = {
        "root": root,
        "repo": repo,
        "env": env,
        "namespace": namespace,
        "label": label,
    }
    if merge_tasks:
        _mark_tasks_merged(fixture)
        _run_fixture_validation(fixture)
    return fixture


def _prepare_convergence(fixture: dict[str, Any]) -> dict[str, Any]:
    readiness = _fixture_cli(fixture, "review", "readiness", "--json")
    readiness_payload = json.loads(readiness.stdout)
    require(readiness_payload["status"] == "ready", "synthetic fixture is not review-ready")
    prepared = _fixture_cli(fixture, "review", "convergence", "prepare", "--json")
    payload = json.loads(prepared.stdout)
    require(not payload["automatic_reviewer_started"], "convergence started a Reviewer")
    plan_path = state_path(fixture["repo"], fixture["namespace"]).parent / "reviews" / "convergence" / "PLAN.json"
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _candidate_for_lane(
    head_sha: str,
    lane: Any,
    *,
    finding_id: str,
    severity: str,
    title: str,
    fact_status: str = "confirmed",
    origin: str = "introduced",
    contract_relation: str = "in_scope",
    decision_requirement: str = "none",
    disposition: str = "fix_now",
    release_effect: str = "blocking",
) -> FindingCandidate:
    return FindingCandidate(
        finding_id=finding_id,
        provider=lane.provider,
        head_sha=head_sha,
        discovering_agent=lane.agent_label,
        severity=severity,
        confidence=0.95,
        title=title,
        file_path=f"src/{finding_id.lower()}.py",
        line=1,
        symbol="synthetic_gate",
        trigger="synthetic bounded-convergence trigger",
        product_impact="the synthetic release gate must account for this finding",
        origin=origin,
        proposed_fix="apply the bounded synthetic remediation",
        requires_spec_decision=False,
        fact_status=fact_status,
        contract_relation=contract_relation,
        decision_requirement=decision_requirement,
        disposition=disposition,
        release_effect=release_effect,
    )


def _write_review_manifest(
    fixture: dict[str, Any],
    plan: dict[str, Any],
    *,
    candidates: tuple[FindingCandidate, ...] = (),
    recommended_action: str = "fix_task",
    timeout_lane: bool = False,
) -> tuple[hloop_review.ReviewManifest, tuple[hloop_review.NormalizedFinding, ...]]:
    loop = state_path(fixture["repo"], fixture["namespace"]).parent
    group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
    normalized = normalize_findings(candidates)
    verification_plan = plan_verification(group, normalized)
    ignore_status = (
        "must_not_ignore"
        if recommended_action in {"fix_task", "ask_user"}
        else "may_defer"
    )
    by_fingerprint = {item.fingerprint: item for item in normalized}
    verifications = tuple(
        hloop_review.VerificationRecord.from_assignment(
            assignment,
            fact_status=by_fingerprint[assignment.fingerprint].fact_status,
            ignore_status=ignore_status,
            decision_status="none",
            progress_without_decision="yes",
            severity=by_fingerprint[assignment.fingerprint].severity,
            recommended_action=recommended_action,
        )
        for assignment in verification_plan.assignments
    )
    lane_results = []
    for index, lane in enumerate(group.expected_lanes):
        finding_count = sum(
            1
            for item in normalized
            for candidate in item.candidates
            if candidate.discovering_agent == lane.agent_label
        )
        lane_results.append(
            lane.result(
                status="timeout" if timeout_lane and index == 0 else "completed",
                finding_count=finding_count,
            )
        )
    manifest = hloop_review.ReviewManifest(
        review_id="R001",
        plan=group,
        lane_results=tuple(lane_results),
        findings=normalized,
        verification_plan=verification_plan,
        verifications=verifications,
    )
    manifest_path = loop / "reviews" / "convergence" / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest.to_record(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest, normalized


def _record_convergence(fixture: dict[str, Any], fix_round: int) -> dict[str, Any]:
    recorded = _fixture_cli(
        fixture,
        "review",
        "convergence",
        "record",
        "--fix-round",
        str(fix_round),
        "--json",
        expected={0, 2},
    )
    return json.loads(recorded.stdout)


def _prepare_final_review(fixture: dict[str, Any]) -> None:
    _fixture_cli(
        fixture,
        "final-review",
        "prepare",
        "--protocol-capability",
        str(
            SKILL_ROOT.parent
            / "codex-review-multi-v2"
            / "capabilities"
            / "externally-planned-v1.json"
        ),
        "--json",
    )


def _write_final_manifest(
    fixture: dict[str, Any],
    *,
    finding_spec: tuple[str, str] | None = None,
    timeout_lane: bool = False,
    patch_verdict: str = "passed",
    disposition: str = "fix_now",
    release_effect: str = "blocking",
    origin: str = "introduced",
    contract_relation: str = "in_scope",
    decision_requirement: str = "none",
) -> FinalReviewManifest:
    loop = state_path(fixture["repo"], fixture["namespace"]).parent
    plan = CertificationPlan.from_record(
        json.loads((loop / "reviews" / "final" / "PLAN.json").read_text(encoding="utf-8"))
    )
    current_record = json.loads((loop / "reviews" / "final" / "MANIFEST.json").read_text(encoding="utf-8"))
    group = hloop_review.ReviewGroupPlan.from_record(current_record)
    candidates: tuple[FindingCandidate, ...] = ()
    if finding_spec:
        finding_id, severity = finding_spec
        candidates = (
            _candidate_for_lane(
                plan.target_sha,
                group.expected_lanes[0],
                finding_id=finding_id,
                severity=severity,
                title="synthetic final certification finding",
                origin=origin,
                contract_relation=contract_relation,
                decision_requirement=decision_requirement,
                disposition=disposition,
                release_effect=release_effect,
            ),
        )
    normalized = normalize_findings(candidates)
    verification_plan = plan_verification(group, normalized)
    by_fingerprint = {item.fingerprint: item for item in normalized}
    verifications = tuple(
        hloop_review.VerificationRecord.from_assignment(
            assignment,
            fact_status=by_fingerprint[assignment.fingerprint].fact_status,
            ignore_status="must_not_ignore",
            decision_status="none",
            progress_without_decision="yes",
            severity=by_fingerprint[assignment.fingerprint].severity,
            recommended_action="fix_task",
        )
        for assignment in verification_plan.assignments
    )
    lane_results = tuple(
        lane.result(
            status="timeout" if timeout_lane and index == 0 else "completed",
            finding_count=sum(
                1
                for item in normalized
                for candidate in item.candidates
                if candidate.discovering_agent == lane.agent_label
            ),
        )
        for index, lane in enumerate(group.expected_lanes)
    )
    review_manifest = hloop_review.ReviewManifest(
        review_id=plan.execution.execution_id,
        plan=group,
        lane_results=lane_results,
        findings=normalized,
        verification_plan=verification_plan,
        verifications=verifications,
    )
    manifest = _with_fixture_process_identities(
        plan,
        FinalReviewManifest.from_review_manifest(
            plan,
            review_manifest,
            verified_actionable_findings=len(
                FinalReviewManifest.from_review_manifest(
                    plan, review_manifest, verified_actionable_findings=0
                ).recomputed_verified_actionable_fingerprints
            ),
            patch_verdict=patch_verdict,
        ),
    )
    (loop / "reviews" / "final" / "MANIFEST.json").write_text(
        json.dumps(manifest.to_record(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    inventory = hloop._changed_file_inventory(fixture["repo"], plan.base_sha, plan.target_sha)
    report = {
        "protocol": plan.protocol,
        "certification_id": plan.certification_id,
        "prepared_plan_digest": plan.digest,
        "base_sha": plan.base_sha,
        "target_sha": plan.target_sha,
        "scope_revision": plan.scope_revision,
        "source_snapshot_revision": plan.source_snapshot_revision,
        "source_digest": plan.source_digest,
        "lane_count": len(plan.lane_plan),
        "lane_names": [
            f"{lane.provider}:{lane.lane_id}" for lane in plan.lane_plan
        ],
        "lane_outcomes": [
            f"{result.provider}:{result.lane_id}:{result.status}"
            for result in lane_results
        ],
        "coordinator_session_id": "synthetic-coordinator",
        "diff_inventory": inventory or ["(no changed files)"],
        "verification_records": [
            record.fingerprint for record in verifications
        ],
        "verification_shortfall": len(
            review_manifest.verification_plan.shortfalls
        ),
        "incomplete_findings": list(manifest.completeness.incomplete_findings),
        "manifest_complete": manifest.manifest_complete,
        "verified_actionable_findings": manifest.recomputed_verified_actionable_count,
        "findings": [
            finding.fingerprint for finding in review_manifest.findings
        ],
        "residual_risks": [],
        "follow_up_refs": [],
        "patch_verdict": manifest.patch_verdict,
        "completed_at": now(),
    }
    (loop / "reviews" / "final" / "FINAL.md").write_text(
        hloop.frontmatter(report) + "\n# Synthetic Manual Final Review\n",
        encoding="utf-8",
    )
    return manifest


def _record_final_review(fixture: dict[str, Any]) -> dict[str, Any]:
    recorded = _fixture_cli(
        fixture, "final-review", "record", "--json", expected={0, 2}
    )
    return json.loads(recorded.stdout)


def _create_finding_task(
    fixture: dict[str, Any],
    *,
    task_id: str,
    source_finding: str,
    remediation_round: int,
) -> None:
    _fixture_cli(fixture, "batch", "start", "remediation batch", "--id", "B002")
    _fixture_cli(
        fixture,
        "task",
        "new",
        "synthetic remediation task",
        "--id",
        task_id,
        "--kind",
        "fix",
        "--write-allow",
        f"synthetic/remediation/{task_id}.txt",
        "--task-origin",
        "finding",
        "--source-finding",
        source_finding,
        "--scope-ref",
        "release-contract",
        "--origin",
        "introduced",
        "--contract-relation",
        "in_scope",
        "--release-effect",
        "blocking",
        "--fact-status",
        "confirmed",
        "--disposition",
        "fix_now",
        "--why-fix-now",
        "the confirmed synthetic finding blocks certification",
        "--remediation-round",
        str(remediation_round),
        "--acceptance",
        "the confirmed synthetic finding is remediated",
        "--preserved-invariant",
        "preserve bounded synthetic remediation",
        "--regression-check",
        "run the remediation convergence scenario",
        "--risk-class",
        "high",
        "--required-gate",
        "patch_review",
        "--required-gate",
        "full_suite",
    )
    _fixture_cli(
        fixture,
        "batch",
        "close",
        "B002",
        "--summary",
        "closed one coalesced remediation batch",
    )
    _mark_tasks_merged(fixture)
    _run_fixture_validation(fixture)


def _seed_running_worker_with_review_wait(
    fixture: dict[str, Any], task_id: str = "T001"
) -> None:
    repo = fixture["repo"]
    state = _fixture_state(fixture)
    task = state["tasks"][task_id]
    task_path = state_path(repo, fixture["namespace"]).parent / "tasks" / f"{task_id}.md"
    hloop.replace_frontmatter(task_path, {"contract_schema_revision": 2})
    task["contract_schema_revision"] = 2
    task["task_contract_digest"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    worktree = fixture["root"] / "safe-harvest-worker"
    branch = str(task["branch"])
    git(repo, "worktree", "add", "-b", branch, str(worktree), "master")
    attempt_id = f"{task_id}-A001"
    result_path = (
        worktree
        / ".ai"
        / "herdr-dev-loop"
        / "loops"
        / fixture["namespace"]
        / "results"
        / task_id
        / "result.md"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        hloop.frontmatter(
            {
                "task_id": task_id,
                "run_id": state["run_id"],
                "skill_version": state["skill_version"],
                "contract_schema_revision": 2,
                "attempt_id": attempt_id,
                "status": "done",
                "merge_ready": True,
                "branch": branch,
                "head_sha": "HEAD",
                "base_sha": task["base_sha"],
                "changed_files": [result_path.relative_to(worktree).as_posix()],
                "validation_recorded": True,
                "validation_commands": ["true"],
                "validation_results": ["passed"],
                "validation_summary": "synthetic safe harvest validation",
                "blocking_questions": [],
            }
        )
        + "\n# Synthetic safe-harvest result\n",
        encoding="utf-8",
    )
    git(worktree, "add", "-f", result_path.relative_to(worktree).as_posix())
    git(worktree, "commit", "-m", "synthetic safe harvest result")
    head = git(worktree, "rev-parse", "HEAD").stdout.strip()
    task.update(
        {
            "status": "running",
            "worktree": str(worktree),
            "active_attempt_id": attempt_id,
            "attempt_id": attempt_id,
            "worker_base_sha": task["base_sha"],
            "head_sha": head,
            "reported_head_sha": head,
        }
    )
    state["reviews"] = {
        "R001": {"status": "running", "gate_status": "running", "head_sha": head}
    }
    state["needs_review"] = True
    state["phase"] = "waiting_review"
    _save_fixture_state(fixture, state)


def scenario_remediation_convergence(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(
        ctx, "remediation-convergence", task_count=3, merge_tasks=False
    )
    dispatch = _fixture_cli(
        fixture, "tick", "--once", "--dry-run", "--max-workers", "3"
    )
    worker_starts = dispatch.stdout.count("DRY RUN start worker")
    require(worker_starts == 3, "three non-conflicting workers were not dispatchable together")
    _mark_tasks_merged(fixture)
    first_validation = _run_fixture_validation(fixture)

    plan = _prepare_convergence(fixture)
    group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
    candidate = _candidate_for_lane(
        plan["target_sha"],
        group.expected_lanes[0],
        finding_id="F001",
        severity="P1",
        title="synthetic remediation finding",
    )
    _write_review_manifest(fixture, plan, candidates=(candidate,))
    first_round = _record_convergence(fixture, 0)
    require(first_round["status"] == "pending", "first remediation round did not remain pending")
    reopened = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "remediate",
        "--user-input-id",
        "U0001",
        "--json",
    )
    require(json.loads(reopened.stdout)["accepted"], "remediation reopen was rejected")
    reopened_state = _fixture_state(fixture)
    require(reopened_state["review_convergence"]["fix_round"] == 1, "authorized remediation did not start a fresh bounded round")
    _create_finding_task(
        fixture,
        task_id="T004",
        source_finding=candidate.fingerprint,
        remediation_round=1,
    )
    second_state = _fixture_state(fixture)
    second_validation = _validation_log_count(fixture)
    require(
        second_validation > first_validation
        or bool((second_state.get("last_validation") or {}).get("reused")),
        "remediation batch did not receive or reuse validation",
    )
    next_plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, next_plan)
    converged = _record_convergence(fixture, 1)
    require(converged["status"] == "converged", "remediation did not converge")
    _prepare_final_review(fixture)
    _write_final_manifest(fixture)
    final = _record_final_review(fixture)
    require(final["status"] == "passed", "remediation final certification did not pass")
    require(plan["target_sha"] == final["target_sha"], "remediation changed the fixed review target")
    return {
        "parallel_workers": worker_starts,
        "batch_count": 2,
        "merge_events": 4,
        "review_events": 2,
        "review_per_merge": False,
        "validation_events": 2,
        "validation_scales_by_batch": (
            second_validation - first_validation >= 1
            or bool((second_state.get("last_validation") or {}).get("reused"))
        ),
        "remediation_batch_count": 1,
        "remediation_findings_coalesced": True,
        "fix_round": 1,
        "final_status": final["status"],
    }


def scenario_batch_review_cadence(ctx: dict[str, Any]) -> dict[str, Any]:
    """Exercise intermediate and final review openings across two batches."""

    fixture = _new_synthetic_fixture(
        ctx, "batch-review-cadence", task_count=1, merge_tasks=False
    )
    state = _fixture_state(fixture)
    state["max_reviewers"] = 1
    state["review_policy"]["cadence"] = "batch"
    state["review_policy"]["lane_count"] = "auto"
    state["unreviewed_merge_count"] = 1
    state["needs_review"] = False
    _save_fixture_state(fixture, state)
    _mark_tasks_merged(fixture, ("T001",))
    target_one = git(fixture["repo"], "rev-parse", "master").stdout.strip()

    # A future queued task is deliberately outside the closed batch. It must
    # not prevent a review for B001 from opening at the current head.
    state = _fixture_state(fixture)
    state["tasks"]["T002"] = {"status": "queued"}
    state["unreviewed_merge_count"] = 1
    state["needs_review"] = False
    state["current_batch_id"] = ""
    _save_fixture_state(fixture, state)
    _run_fixture_validation(fixture)
    first_review_state = _fixture_state(fixture)
    require(first_review_state["needs_review"], "closed batch review did not open")
    require(
        first_review_state["last_validation"]["head_sha"] == target_one,
        "first review did not pin the current integration head",
    )

    # Simulate the Manager consuming the first gate, then leave B002 open. A
    # current open batch must keep the next review gate closed.
    state = first_review_state
    state["needs_review"] = False
    state["unreviewed_merge_count"] = 0
    state["batches"]["B002"] = {
        "status": "active",
        "task_ids": ["T002"],
        "created_at": now(),
        "started_at": now(),
    }
    state["tasks"]["T002"]["batch_id"] = "B002"
    state["current_batch_id"] = "B002"
    _save_fixture_state(fixture, state)
    _run_fixture_validation(fixture)
    open_batch_state = _fixture_state(fixture)
    require(
        not open_batch_state["needs_review"],
        "review opened while the current batch was still open",
    )

    # Close B002 and advance the integration head before its validation. The
    # second review must pin the new head rather than reuse target_one.
    (fixture["repo"] / "README.md").write_text(
        "synthetic batch review advance\n", encoding="utf-8"
    )
    git(fixture["repo"], "add", "README.md")
    git(fixture["repo"], "commit", "-m", "advance second batch")
    target_two = git(fixture["repo"], "rev-parse", "master").stdout.strip()
    state = _fixture_state(fixture)
    state["batches"]["B002"].update(
        {"status": "closed", "closed_at": now(), "summary": "second batch merged"}
    )
    state["current_batch_id"] = ""
    state["tasks"]["T002"].update({"status": "merged", "head_sha": target_two})
    state["integration_head_sha"] = target_two
    state["completion_target_sha"] = target_two
    state["unreviewed_merge_count"] = 1
    state["needs_review"] = False
    _save_fixture_state(fixture, state)
    _run_fixture_validation(fixture)
    second_review_state = _fixture_state(fixture)
    require(second_review_state["needs_review"], "second closed batch review did not open")
    require(
        second_review_state["last_validation"]["head_sha"] == target_two,
        "second review did not pin the advanced integration head",
    )
    require(target_one != target_two, "batch review targets did not advance")
    return {
        "review_events": 2,
        "first_review_target_sha": target_one,
        "second_review_target_sha": target_two,
        "targets_advanced": True,
        "future_queued_tasks_did_not_block": True,
        "open_batch_kept_closed": True,
    }


def scenario_batch_performance_validation_reuse(ctx: dict[str, Any]) -> dict[str, Any]:
    """Exercise GAP8 batch metrics, validation identity, and scope conflicts."""

    fixture = _new_synthetic_fixture(
        ctx, "batch-performance-validation-reuse", task_count=2, merge_tasks=False
    )
    state = _fixture_state(fixture)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    batch_started = timestamp - timedelta(seconds=12)
    task_times = (
        (batch_started + timedelta(seconds=1), batch_started + timedelta(seconds=8)),
        (batch_started + timedelta(seconds=2), batch_started + timedelta(seconds=10)),
    )
    state["current_batch_id"] = "B001"
    state["batches"]["B001"].update(
        {
            "status": "active",
            "started_at": batch_started.isoformat(),
            "task_ids": ["T001", "T002"],
        }
    )
    for task_id, (started_at, merged_at) in zip(("T001", "T002"), task_times):
        state["tasks"][task_id].update(
            {
                "status": "merged",
                "batch_id": "B001",
                "write_allow": ["src/core.py"],
                "started_at": started_at.isoformat(),
                "merged_at": merged_at.isoformat(),
            }
        )
    state["phase"] = "dispatching"
    state["needs_validation"] = False
    _save_fixture_state(fixture, state)
    _fixture_cli(fixture, "batch", "close", "B001", "--summary", "record GAP8 metrics")

    state = _fixture_state(fixture)
    performance = state["batches"]["B001"].get("performance") or {}
    require(performance.get("worker_count") == 2, "batch worker count was not persisted")
    require(performance.get("worker_runtime_seconds", 0) > 0, "Worker runtime was not persisted")
    require(performance.get("wall_time_seconds", 0) > 0, "batch wall time was not persisted")
    require(
        performance.get("effective_parallelism") is not None
        and performance["effective_parallelism"] < 1.5,
        "synthetic batch did not produce low effective parallelism",
    )
    require(
        performance.get("longest_worker_seconds") == 8.0,
        "longest Worker metric was not calculated",
    )
    require(
        "effective-parallelism-low:" in " ".join(performance.get("warnings") or []),
        "low-parallelism warning was not recorded",
    )
    require(
        state.get("next_batch_replan", {}).get("required") is True,
        "low-parallelism replan requirement was not recorded",
    )
    progress_path = state_path(fixture["repo"], fixture["namespace"]).parent / "progress" / "LATEST.md"
    progress_text = progress_path.read_text(encoding="utf-8")
    require("Effective parallelism:" in progress_text, "progress projection lacks batch metrics")
    graph = state["batches"]["B001"].get("write_scope_conflict_graph") or {}
    require(graph.get("digest"), "batch conflict graph digest was not persisted")
    require(
        graph.get("conflicts", {}).get("T001") == ["T002"],
        "batch conflict graph did not record the overlapping core file",
    )

    # The scheduler must avoid the overlap even when both tasks are queued.
    for task in state["tasks"].values():
        task["status"] = "queued"
    state["phase"] = "dispatching"
    _save_fixture_state(fixture, state)
    dry_tick = _fixture_cli(
        fixture, "tick", "--once", "--dry-run", "--max-workers", "2"
    )
    require(
        dry_tick.stdout.count("DRY RUN start worker") == 1,
        "dispatch planning did not avoid overlapping write scopes",
    )

    # Direct Worker starts consume the same conflict projection after the
    # independent planning gate succeeds.  Exercise that shared guard here;
    # planning artifacts are intentionally outside this performance fixture.
    state = _fixture_state(fixture)
    state["tasks"]["T001"]["status"] = "running"
    state["tasks"]["T002"]["status"] = "queued"
    _save_fixture_state(fixture, state)
    require(
        hloop.active_write_scope_conflicts(state, "T002") == ["T001"],
        "direct Worker start guard did not fail closed on scope overlap",
    )

    state = _fixture_state(fixture)
    for task in state["tasks"].values():
        task["status"] = "merged"
    state["needs_validation"] = False
    state["phase"] = "dispatching"
    _save_fixture_state(fixture, state)
    first = _fixture_cli(fixture, "validate", "--command", "true", "--no-cleanup")
    first_state = _fixture_state(fixture)
    first_record = first_state.get("last_validation") or {}
    first_journal_count = _validation_log_count(fixture)
    post_validation_performance = first_state["batches"]["B001"].get("performance") or {}
    require(
        post_validation_performance.get("validation_time_seconds", 0) > 0,
        "batch validation time was not updated after validation",
    )
    refreshed_progress = progress_path.read_text(encoding="utf-8")
    require(
        "Validation time:" in refreshed_progress,
        "progress projection was not refreshed with validation time",
    )
    identity = first_record.get("validation_identity") or {}
    require(identity.get("target_sha"), "validation identity target SHA is missing")
    require(identity.get("commands") == ["true"], "validation identity command set is missing")
    require(identity.get("dependency_identity"), "dependency identity is missing")
    require(first_record.get("reused") is False, "first validation was incorrectly reused")

    second = _fixture_cli(fixture, "validate", "--command", "true", "--no-cleanup")
    second_state = _fixture_state(fixture)
    require(
        second_state["last_validation"].get("reused") is True,
        "same-target validation evidence was not reused",
    )
    require(
        _validation_log_count(fixture) == first_journal_count,
        "validation reuse unexpectedly ran a new validation event",
    )

    second_state["validation_commands"] = ["true", "true"]
    _save_fixture_state(fixture, second_state)
    _fixture_cli(fixture, "validate", "--no-cleanup")
    third_state = _fixture_state(fixture)
    require(
        third_state["last_validation"].get("reused") is False,
        "command-set change did not invalidate validation evidence",
    )
    require(
        _validation_log_count(fixture) > first_journal_count,
        "invalidated validation did not record a new validation event",
    )
    third_state["resolved_config"] = {
        **(third_state.get("resolved_config") or {}),
        "synthetic_revision": "changed",
    }
    _save_fixture_state(fixture, third_state)
    _fixture_cli(fixture, "validate", "--no-cleanup")
    fourth_state = _fixture_state(fixture)
    require(
        fourth_state["last_validation"].get("reused") is False,
        "resolved-config change did not invalidate validation evidence",
    )
    _fixture_cli(fixture, "batch", "start", "follow-up", "--id", "B002")
    follow_up_state = _fixture_state(fixture)
    require(
        follow_up_state["batches"]["B002"].get("replan_requirement", {}).get(
            "source_batch_id"
        )
        == "B001",
        "next batch did not acknowledge the low-parallelism replan",
    )
    require(
        follow_up_state.get("next_batch_replan", {}).get("required") is False,
        "next batch replan requirement was not acknowledged",
    )
    return {
        "batch_id": "B001",
        "effective_parallelism": performance["effective_parallelism"],
        "low_parallelism_warning": True,
        "replan_required": True,
        "conflict_graph_digest": graph["digest"],
        "scheduler_avoided_overlap": True,
        "direct_start_blocked": True,
        "validation_reused": True,
        "validation_invalidated_by_command_set": True,
        "validation_invalidated_by_resolved_config": True,
        "validation_identity_version": identity.get("version"),
        "validation_event_count": _validation_log_count(fixture),
    }


def scenario_scope_expansion_follow_up(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(ctx, "scope-expansion-follow-up")
    plan = _prepare_convergence(fixture)
    group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
    candidate = _candidate_for_lane(
        plan["target_sha"],
        group.expected_lanes[0],
        finding_id="F001",
        severity="P2",
        title="synthetic scope expansion",
        contract_relation="outside_release",
        disposition="defer_follow_up",
        release_effect="non_blocking",
    )
    _write_review_manifest(
        fixture,
        plan,
        candidates=(candidate,),
        recommended_action="accepted_risk_candidate",
    )
    blocked = _fixture_cli(
        fixture,
        "review",
        "convergence",
        "record",
        "--fix-round",
        "0",
        "--json",
        expected=2,
    )
    require(
        "first-class follow-up artifacts" in blocked.stderr,
        "scope expansion convergence did not fail closed before follow-up",
    )
    state_before_follow_up = _fixture_state(fixture)
    require(
        state_before_follow_up["review_convergence"]["status"] == "prepared",
        "blocked convergence mutated the convergence gate",
    )
    target_sha = state_before_follow_up["review_convergence"]["target_sha"]
    follow_up = _fixture_cli(
        fixture,
        "follow-up",
        "add",
        "--title",
        "synthetic scope expansion follow-up",
        "--component",
        "integration",
        "--trigger-class",
        "review-follow-up",
        "--product-impact",
        "scope expansion is tracked outside this release",
        "--root-cause",
        "review found a non-blocking scope expansion",
        "--source-review-fingerprint",
        candidate.fingerprint,
        "--discovered-head",
        plan["target_sha"],
        "--evidence",
        "synthetic final review evidence",
        "--impact",
        "no current release gate impact",
        "--affected-path",
        "src/follow-up.py",
        "--fact-status",
        "confirmed",
        "--severity",
        "P2",
        "--origin",
        "introduced",
        "--contract-relation",
        "outside_release",
        "--decision-requirement",
        "none",
        "--release-effect",
        "non_blocking",
        "--disposition",
        "defer_follow_up",
        "--recommended-action",
        "defer_follow_up",
        "--deferred-reason",
        "out of scope for this release",
        "--reconsider-condition",
        "the next release scope review includes this component",
        "--json",
    )
    convergence = _record_convergence(fixture, 0)
    require(convergence["status"] == "converged", "follow-up did not permit convergence")
    require(
        _fixture_state(fixture)["review_convergence"]["target_sha"] == target_sha,
        "follow-up changed the fixed review target",
    )
    follow_up_payload = json.loads(follow_up.stdout)
    follow_up_id = str(follow_up_payload["follow_up"]["id"])
    require(follow_up_id == "F001", "follow-up artifact id is not deterministic")
    follow_up_path = state_path(fixture["repo"], fixture["namespace"]).parent / "follow-ups" / "F001.md"
    require(follow_up_path.is_file(), "scope-expanding follow-up artifact is missing")
    state_after_follow_up = _fixture_state(fixture)
    require(state_after_follow_up["review_convergence"]["status"] == "converged", "follow-up invalidated convergence gate")
    _prepare_final_review(fixture)
    _write_final_manifest(fixture)
    final = _record_final_review(fixture)
    require(final["status"] == "passed", "scope follow-up final certification did not pass")
    return {
        "follow_up_id": follow_up_id,
        "follow_up_artifact": str(follow_up_path.relative_to(fixture["repo"])),
        "created_tasks": 0,
        "fixture_task_count": len(_fixture_state(fixture)["tasks"]),
        "gate_invalidated": False,
        "final_status": final["status"],
    }


def scenario_two_round_exhaustion(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(ctx, "two-round-exhaustion")
    plan = _prepare_convergence(fixture)
    group = hloop_review.ReviewGroupPlan.from_record(plan["review_plan"])
    candidate = _candidate_for_lane(
        plan["target_sha"],
        group.expected_lanes[0],
        finding_id="F001",
        severity="P1",
        title="synthetic exhausting finding",
    )
    _write_review_manifest(fixture, plan, candidates=(candidate,))
    first = _record_convergence(fixture, 0)
    require(first["status"] == "pending", "round zero did not remain pending")
    reopened_one = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "remediate",
        "--user-input-id",
        "U0001",
        "--json",
    )
    require(json.loads(reopened_one.stdout)["accepted"], "round-one remediation reopen was rejected")
    plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, plan, candidates=(candidate,))
    second = _record_convergence(fixture, 1)
    require(second["status"] == "pending", "round one did not remain pending")
    reopened_two = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "remediate",
        "--user-input-id",
        "U0002",
        "--json",
    )
    require(json.loads(reopened_two.stdout)["accepted"], "round-two remediation reopen was rejected")
    plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, plan, candidates=(candidate,))
    exhausted = _record_convergence(fixture, 2)
    require(exhausted["status"] == "exhausted", "convergence did not stop at two rounds")
    exhausted_state = _fixture_state(fixture)
    third = _fixture_cli(
        fixture,
        "review",
        "convergence",
        "prepare",
        "--json",
        expected=2,
    )
    require("exhausted" in (third.stdout + third.stderr).lower(), "third automatic round was not rejected")
    unauthorized = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "remediate",
        "--user-input-id",
        "U0003",
        "--json",
        expected=2,
    )
    require(
        "authorized-extra-rounds-required" in unauthorized.stdout,
        "exhausted remediation reopened without extra-round authorization",
    )
    authorized = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "remediate",
        "--user-input-id",
        "U0003",
        "--authorized-extra-rounds",
        "1",
        "--authorization-input-id",
        "U0003",
        "--json",
    )
    require(json.loads(authorized.stdout)["accepted"], "authorized extra remediation reopen was rejected")
    state = _fixture_state(fixture)
    require(state["review_convergence"]["fix_round"] == 3, "authorized reopen did not advance canonical round")
    return {
        "configured_max_fix_rounds": exhausted["max_fix_rounds"],
        "recorded_fix_round": exhausted["fix_round"],
        "automatic_third_round": False,
        "dispatch_frozen": exhausted_state["dispatch_freeze"]["status"] == "active",
        "canonical_fix_round_after_authorized_reopen": state["review_convergence"]["fix_round"],
        "phase": state["phase"],
    }


def scenario_user_stop_freeze(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(
        ctx, "user-stop-freeze", task_count=1, merge_tasks=False
    )
    _seed_running_worker_with_review_wait(fixture)
    frozen = _fixture_cli(
        fixture,
        "dispatch",
        "freeze",
        "--reason",
        "user requested stop",
        "--user-input-id",
        "U0001",
        "--allowed-running-role-id",
        "T001",
        "--json",
    )
    require(json.loads(frozen.stdout)["status"] == "active", "user stop did not freeze dispatch")
    blocked_task = _fixture_cli(
        fixture,
        "task",
        "new",
        "blocked task after user stop",
        "--id",
        "T002",
        "--kind",
        "implementation",
        "--write-allow",
        "blocked.txt",
        "--task-origin",
        "planned",
        "--plan-item-ref",
        "P005",
        "--acceptance",
        "must not dispatch",
        expected=2,
    )
    require("frozen" in (blocked_task.stdout + blocked_task.stderr).lower(), "frozen dispatch allowed a new task")
    for command in (("reviewer", "start", "--dry-run"), ("gap", "start", "--dry-run")):
        blocked = _fixture_cli(fixture, *command, expected=2)
        require("frozen" in (blocked.stdout + blocked.stderr).lower(), f"frozen dispatch allowed {command[0]} start")
    harvest = _fixture_cli(
        fixture, "tick", "--once", "--dry-run", "--max-workers", "3"
    )
    require(
        "DRY RUN harvest worker T001" in harvest.stdout,
        "safe worker harvest was not offered while review was waiting",
    )
    dashboard = _fixture_cli(fixture, "dashboard", "--json", "--no-pane-probe")
    dashboard_payload = json.loads(dashboard.stdout)
    require(
        dashboard_payload.get("loop", {}).get("dispatch_freeze", {}).get("status") == "active",
        "dashboard lost the user stop freeze",
    )
    _fixture_cli(fixture, "report")
    report_path = state_path(fixture["repo"], fixture["namespace"]).parent / "reports" / "DRAFT.md"
    require(report_path.is_file(), "user-stop draft report is missing")
    state = _fixture_state(fixture)
    require(set(state["tasks"]) == {"T001"}, "user stop created an unexpected task")
    require(set(state["reviews"]) == {"R001"}, "user stop created an unexpected Reviewer")
    return {
        "freeze_status": state["dispatch_freeze"]["status"],
        "allowed_running_role_ids": state["dispatch_freeze"]["allowed_running_role_ids"],
        "safe_harvest_while_review_waits": True,
        "new_task_events": 0,
        "new_reviewer_events": 0,
        "new_gap_events": 0,
        "report": str(report_path.relative_to(fixture["repo"])),
    }


def scenario_manual_final_retry_same_sha(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(ctx, "manual-final-retry-same-sha")
    plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, plan)
    require(_record_convergence(fixture, 0)["status"] == "converged", "retry fixture did not converge")
    _prepare_final_review(fixture)
    target = plan["target_sha"]
    _write_final_manifest(fixture, timeout_lane=True)
    incomplete = _record_final_review(fixture)
    require(incomplete["status"] == "incomplete", "incomplete final lane unexpectedly passed")
    reopened = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "retry-certification",
        "--user-input-id",
        "U0001",
        "--json",
    )
    require(json.loads(reopened.stdout)["accepted"], "same-SHA certification retry was rejected")
    after_reopen = _fixture_state(fixture)
    require(after_reopen["dispatch_freeze"]["status"] == "active", "certification retry unexpectedly unfroze dispatch")
    loop_plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, loop_plan)
    require(_record_convergence(fixture, 0)["status"] == "converged", "retry convergence evidence was not restored")
    _prepare_final_review(fixture)
    _write_final_manifest(fixture)
    final = _record_final_review(fixture)
    require(final["status"] == "passed", "same-SHA certification retry did not pass")
    state = _fixture_state(fixture)
    statuses = [item.get("status") for item in state["manual_final_review"]["attempt_history"]]
    require("incomplete" in statuses and "passed" in statuses, "retry attempt history is incomplete")
    require(target == final["target_sha"], "certification retry changed target SHA")
    return {
        "initial_status": incomplete["status"],
        "retry_status": final["status"],
        "same_target_sha": True,
        "attempt_history_statuses": statuses,
        "dispatch_frozen_during_retry": after_reopen["dispatch_freeze"]["status"] == "active",
    }


def scenario_manual_final_policy_fail_closed(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(ctx, "manual-final-policy-fail-closed")
    plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, plan)
    require(
        _record_convergence(fixture, 0)["status"] == "converged",
        "policy fixture did not converge",
    )
    _prepare_final_review(fixture)
    _write_final_manifest(
        fixture,
        finding_spec=("F001", "P1"),
        disposition="defer_follow_up",
        release_effect="non_blocking",
    )
    before = _fixture_state(fixture)
    rejected = _fixture_cli(
        fixture,
        "final-review",
        "record",
        "--json",
        expected=2,
    )
    require(rejected.stdout == "", "unsafe final policy produced a success payload")
    require(
        "disposition policy" in rejected.stderr,
        "unsafe final policy rejection did not identify disposition policy",
    )
    require(
        _fixture_state(fixture) == before,
        "unsafe final policy mutated manual-final state",
    )
    return {
        "rejected": True,
        "state_unchanged": True,
        "policy": "confirmed introduced in_scope P1 defer_follow_up",
    }


def scenario_manual_final_user_authorized_reopen(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(ctx, "manual-final-authorized-reopen")
    plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, plan)
    require(_record_convergence(fixture, 0)["status"] == "converged", "reopen fixture did not converge")
    _prepare_final_review(fixture)
    failed_manifest = _write_final_manifest(
        fixture, finding_spec=("F001", "P1"), patch_verdict="failed"
    )
    failed = _record_final_review(fixture)
    require(failed["status"] == "failed", "final actionable finding did not fail certification")
    source_finding = str(failed_manifest.review_manifest.findings[0].fingerprint)
    reopened = _fixture_cli(
        fixture,
        "review",
        "reopen",
        "--action",
        "remediate",
        "--user-input-id",
        "U0001",
        "--json",
    )
    require(json.loads(reopened.stdout)["accepted"], "user-authorized remediation reopen was rejected")
    state_after_reopen = _fixture_state(fixture)
    require(state_after_reopen["review_convergence"]["fix_round"] == 1, "reopen did not start remediation round one")
    _create_finding_task(
        fixture,
        task_id="T002",
        source_finding=source_finding,
        remediation_round=1,
    )
    next_plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, next_plan)
    require(_record_convergence(fixture, 1)["status"] == "converged", "authorized remediation did not converge")
    _prepare_final_review(fixture)
    _write_final_manifest(fixture)
    final = _record_final_review(fixture)
    require(final["status"] == "passed", "authorized remediation certification did not pass")
    state = _fixture_state(fixture)
    statuses = [item.get("status") for item in state["manual_final_review"]["attempt_history"]]
    require("failed" in statuses and "passed" in statuses, "reopen attempt history is incomplete")
    return {
        "initial_status": failed["status"],
        "reopened_with_user_input": True,
        "remediation_round": state["review_convergence"]["fix_round"],
        "final_status": final["status"],
        "attempt_history_statuses": statuses,
    }


def scenario_review_budget(ctx: dict[str, Any]) -> dict[str, Any]:
    dual = plan_review_group(
        "dual-swarm",
        head_sha="a" * 40,
        probes_per_provider=4,
    )
    require(dual.providers == ("codex", "claude"), "dual providers missing")
    require([len(item.lanes) for item in dual.provider_plans] == [4, 4], "dual swarm topology mismatch")

    bounded = plan_review_group(
        "single",
        head_sha="a" * 40,
        max_verifications=1,
    )
    findings = normalize_findings(
        (
            finding("C-F001", path="src/one.py", symbol="one"),
            finding("C-F002", path="src/two.py", symbol="two"),
        )
    )
    verification = plan_verification(bounded, findings)
    require(verification.budget_exhausted, "review budget exhaustion was not recorded")
    require(len(verification.shortfalls) == 1, "review shortfall was dropped")
    require(verification.shortfalls[0].reason == "budget-exhausted", "wrong budget reason")
    return {
        "mode": dual.mode,
        "providers": list(dual.providers),
        "lanes_per_provider": [len(item.lanes) for item in dual.provider_plans],
        "budget_exhausted": verification.budget_exhausted,
        "retained_shortfalls": len(verification.shortfalls),
    }


def scenario_finish(ctx: dict[str, Any]) -> dict[str, Any]:
    fixture = _new_synthetic_fixture(ctx, "final-gate-and-finish")
    root: Path = fixture["root"]
    repo: Path = fixture["repo"]
    env: dict[str, str] = fixture["env"]
    namespace: str = fixture["namespace"]
    path = state_path(repo, namespace)
    state = json.loads(path.read_text(encoding="utf-8"))
    target = git(repo, "rev-parse", "master").stdout.strip()
    final_plan = _prepare_convergence(fixture)
    _write_review_manifest(fixture, final_plan)
    require(
        _record_convergence(fixture, 0)["status"] == "converged",
        "finish fixture did not converge",
    )
    _prepare_final_review(fixture)
    _write_final_manifest(fixture)
    require(
        _record_final_review(fixture)["status"] == "passed",
        "finish fixture final certification did not pass",
    )
    captured_input = run(
        hloop_command(
            repo,
            namespace,
            "input",
            "record",
            "--source",
            "synthetic-final-gate",
            "--text",
            "the synthetic final-gate requirement",
        ),
        cwd=root,
        env=env,
    )
    source_input = captured_input.stdout.strip().split()[-1]
    run(
        hloop_command(
            repo,
            namespace,
            "requirement",
            "new",
            "--id",
            "REQ-001",
            "--source-input",
            source_input,
            "--acceptance",
            "synthetic final-gate evidence is recorded",
            "--priority",
            "P1",
        ),
        cwd=root,
        env=env,
    )
    run(
        hloop_command(
            repo,
            namespace,
            "progress",
            "record",
            "--requirement-id",
            "REQ-001",
            "--status",
            "in_progress",
        ),
        cwd=root,
        env=env,
    )
    run(
        hloop_command(
            repo,
            namespace,
            "progress",
            "record",
            "--requirement-id",
            "REQ-001",
            "--status",
            "implemented_unverified",
            "--evidence-kind",
            "artifact",
            "--evidence-ref",
            "reports/release-evidence.json",
            "--verified-by",
            "hloop",
            "--head-sha",
            target,
            "--remaining-work",
            "run final validation",
        ),
        cwd=root,
        env=env,
    )
    run(
        hloop_command(
            repo,
            namespace,
            "progress",
            "record",
            "--requirement-id",
            "REQ-001",
            "--status",
            "verified",
            "--evidence-kind",
            "test",
            "--evidence-ref",
            "synthetic validation",
            "--verified-by",
            "hloop",
            "--head-sha",
            target,
            "--result",
            "passed",
            "--remaining-work",
            "",
        ),
        cwd=root,
        env=env,
    )
    state = json.loads(path.read_text(encoding="utf-8"))
    state["tasks"] = {
        "T999": {
            "status": "merged",
            "branch": "synthetic/already-merged",
            "title": "Synthetic confirmed fix",
            "task_origin": "planned",
            "release_scope_revision": 1,
            "cleanup_done": True,
        }
    }
    worker_result = path.parent / "results" / "T999" / "result.md"
    worker_result.parent.mkdir(parents=True, exist_ok=True)
    worker_result.write_text("# Synthetic Worker Result\n", encoding="utf-8")
    state["tasks"]["T999"]["result_path"] = str(worker_result)
    state["tasks"]["T999"]["harvested_at"] = now()
    review_path = path.parent / "reviews" / "R999.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("# Synthetic confirmed review\n", encoding="utf-8")
    state["reviews"] = {
        "R999": {
            "status": "triaged",
            "gate_status": "triaged",
            "head_sha": target,
            "closed_head_sha": target,
            "review_path": str(review_path),
            "confirmed_finding_fingerprints": ["sha256:" + "f" * 64],
            "created_fix_tasks": ["T999"],
            "verdict": "accepted-risk",
            "triage_reason": "synthetic residual compatibility risk",
            "harvested_at": now(),
        }
    }
    state["batches"] = {"B001": {"status": "closed", "title": "synthetic batch"}}
    state["current_batch_id"] = ""
    state["completion_target_sha"] = target
    state["integration_head_sha"] = target
    state["max_reviewers"] = 0
    state["max_gap_auditors"] = 0
    state["needs_review"] = False
    state["needs_gap_check"] = False
    state["manager_qa_profile"] = "none"
    state["manager_qa_status"] = "not-required"
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    run(
        hloop_command(repo, namespace, "validate", "--level", "L3", "--no-cleanup"),
        cwd=root,
        env=env,
    )

    run(
        hloop_command(repo, namespace, "final-gates", "arm", "--armed-by", "synthetic-manager"),
        cwd=root,
        env=env,
    )
    run(
        hloop_command(repo, namespace, "finish"),
        cwd=root,
        env=env,
    )
    finished = json.loads(path.read_text(encoding="utf-8"))
    report_path = path.parent / "reports" / "FINAL.md"
    require(finished["phase"] == "done", "finish did not reach done")
    require(finished["final_target_sha"] == target, "finish target mismatch")
    require(report_path.is_file(), "finish report missing")
    report_text = report_path.read_text(encoding="utf-8")
    require("# Final Outcome" in report_text, "FINAL is not OutcomeReport-rendered")
    require("sha256:" + "f" * 64 in report_text, "confirmed finding missing from FINAL")
    require("T999" in report_text, "confirmed fix missing from FINAL")
    require("synthetic residual compatibility risk" in report_text, "accepted risk missing from FINAL")
    require("Manager invocation unavailable reason:" in report_text, "manager invocation projection missing from FINAL")
    require("Task origins:" in report_text, "task metrics projection missing from FINAL")
    require("Timings (seconds):" in report_text, "timing metrics projection missing from FINAL")
    finished_metrics = finished.get("execution_metrics") or {}
    require(
        finished_metrics.get("remediation_task_count") == 0
        and finished_metrics.get("planned_task_count") == 1,
        "finish did not refresh task-origin metrics",
    )
    return {
        "phase": finished["phase"],
        "target_sha": target,
        "final_gate_generation": finished["final_gate"]["generation"],
        "report": str(report_path.relative_to(repo)),
        "metrics_projected": True,
        "fixture_note": "prepared an already-merged task and passing validation before invoking real final-gates and finish commands",
    }


def scenario_v053_convergence(_ctx: dict[str, Any]) -> dict[str, Any]:
    return _v053_e2e_module().run_scenario("v053-convergence")


def scenario_v053_fail_closed_matrix(_ctx: dict[str, Any]) -> dict[str, Any]:
    return _v053_e2e_module().run_scenario("v053-fail-closed-matrix")


def scenario_v053_migration_crash_matrix(_ctx: dict[str, Any]) -> dict[str, Any]:
    return _v053_e2e_module().run_scenario("v053-migration-crash-matrix")


SCENARIOS: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("config-and-migration", scenario_config_migration),
    ("attempt-and-merge-transaction", scenario_attempt_and_merge),
    ("merge-conflict-recovery", scenario_merge_conflict_recovery),
    ("cleanup-gate-resolution", scenario_cleanup_gate_resolution),
    ("report-broker-sleep-wake-recovery", scenario_report_broker),
    ("scout-liaison-report-ack-attention", scenario_scout_liaison_reports),
    ("requirements-decisions-outcomes", scenario_requirements_decisions),
    ("remediation-convergence", scenario_remediation_convergence),
    ("batch-review-cadence", scenario_batch_review_cadence),
    ("batch-performance-validation-reuse", scenario_batch_performance_validation_reuse),
    ("scope-expansion-follow-up", scenario_scope_expansion_follow_up),
    ("two-round-exhaustion", scenario_two_round_exhaustion),
    ("user-stop-freeze", scenario_user_stop_freeze),
    ("manual-final-retry-same-sha", scenario_manual_final_retry_same_sha),
    ("manual-final-policy-fail-closed", scenario_manual_final_policy_fail_closed),
    ("manual-final-authorized-reopen", scenario_manual_final_user_authorized_reopen),
    ("dual-review-and-budget", scenario_review_budget),
    ("final-gate-and-finish", scenario_finish),
    ("v053-convergence", scenario_v053_convergence),
    ("v053-fail-closed-matrix", scenario_v053_fail_closed_matrix),
    ("v053-migration-crash-matrix", scenario_v053_migration_crash_matrix),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the structured result as JSON")
    parser.add_argument("--output", type=Path, help="also write the structured result to this path")
    parser.add_argument("--keep-workdir", action="store_true", help="retain the temporary repositories")
    parser.add_argument(
        "--expected-integration-sha",
        help=(
            "fail closed unless the resolved checkout HEAD equals this integration SHA "
            "and the skill subtree has no staged, unstaged, or untracked source changes; "
            "defaults to HLOOP_EXPECTED_INTEGRATION_SHA and is otherwise required"
        ),
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="run only this named scenario; may be repeated",
    )
    return parser.parse_args()


def resolved_checkout_head(skill_root: Path = SKILL_ROOT) -> str:
    completed = subprocess.run(
        ["git", "-C", str(skill_root), "rev-parse", "HEAD"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        # Targeted compatibility fixtures copy the skill without Git metadata.
        # ``main`` already makes missing identity release-blocking for an
        # aggregate run, while a named scenario is intentionally portable.
        return ""
    head = completed.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) is None:
        raise ScenarioFailure(f"resolved checkout HEAD is not a canonical Git SHA: {head!r}")
    return head


def checkout_skill_source_status(
    skill_root: Path = SKILL_ROOT,
) -> tuple[bool | None, list[str], str]:
    """Return whether the checked-out skill subtree exactly matches ``HEAD``.

    Release evidence is tied to the bytes below ``SKILL_ROOT``, not merely to
    the commit named by ``HEAD``.  Staged, unstaged, and untracked skill files
    therefore all make the checkout ineligible for exact-SHA certification.
    Loop evidence outside the skill subtree (for example repository-local
    ``.ai/`` state) is intentionally outside this source-identity check.

    ``None`` means the status could not be resolved, which preserves portable
    named-scenario fixtures without Git metadata while still failing closed for
    aggregate or explicitly pinned release runs.
    """

    completed = subprocess.run(
        [
            "git",
            "-C",
            str(skill_root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--",
            ".",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        return None, [], detail or "git status failed for the skill subtree"
    dirty_paths: list[str] = []
    for raw_record in completed.stdout.split(b"\0"):
        if not raw_record:
            continue
        if len(raw_record) < 4 or raw_record[2:3] != b" ":
            return None, [], "git status returned a malformed porcelain record"
        path = raw_record[3:].decode("utf-8", errors="surrogateescape")
        if not path:
            return None, [], "git status returned an empty dirty path"
        dirty_paths.append(path)
    return not dirty_paths, dirty_paths, ""


def checkout_identity_record(
    expected_integration_sha: str,
    skill_root: Path = SKILL_ROOT,
    *,
    require_private_snapshot: bool = False,
) -> dict[str, Any]:
    """Resolve one fail-closed release source-identity snapshot."""

    checkout_head = resolved_checkout_head(skill_root)
    checkout_clean, dirty_paths, status_error = checkout_skill_source_status(skill_root)
    expected_is_canonical = bool(
        re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_integration_sha)
    )
    private_snapshot_sha = str(os.environ.get(_SNAPSHOT_ACTIVE_ENV) or "").strip()
    private_snapshot_verified = bool(
        _private_snapshot_context_matches(expected_integration_sha)
        and private_snapshot_sha == expected_integration_sha
        and checkout_head == expected_integration_sha
        and checkout_clean is True
    )
    execution_verified = bool(
        expected_is_canonical
        and checkout_head == expected_integration_sha
        and checkout_clean is True
        and (not require_private_snapshot or private_snapshot_verified)
    )
    # A private child may verify that it is executing the requested bytes, but
    # only the source-checkout parent can attest that the disposable worktree
    # was removed afterwards.  The parent rewrites ``verified`` only after that
    # cleanup succeeds.
    verified = bool(execution_verified and not require_private_snapshot)
    error = ""
    if not expected_integration_sha:
        error = (
            "expected integration SHA is required via --expected-integration-sha "
            "or HLOOP_EXPECTED_INTEGRATION_SHA"
        )
    elif not expected_is_canonical:
        error = (
            "expected integration SHA is not a canonical Git SHA: "
            f"{expected_integration_sha!r}"
        )
    elif checkout_head != expected_integration_sha:
        error = (
            "resolved checkout HEAD does not match expected integration SHA: "
            f"head={checkout_head} expected={expected_integration_sha}"
        )
    elif checkout_clean is None:
        error = "skill subtree cleanliness could not be verified: " + status_error
    elif dirty_paths:
        preview = ", ".join(dirty_paths[:20])
        suffix = "" if len(dirty_paths) <= 20 else f" (+{len(dirty_paths) - 20} more)"
        error = (
            "skill subtree has staged, unstaged, or untracked source changes: "
            f"{preview}{suffix}"
        )
    elif require_private_snapshot:
        if not private_snapshot_verified:
            error = (
                "release scenarios must execute from a private detached snapshot "
                "of the expected integration SHA"
            )
        else:
            error = "parent cleanup attestation is pending"
    return {
        "resolved_head_sha": checkout_head,
        "expected_integration_sha": expected_integration_sha,
        "skill_subtree_clean": checkout_clean,
        "dirty_paths": dirty_paths,
        "execution_source": (
            "private-detached-worktree"
            if private_snapshot_verified
            else "mutable-checkout"
        ),
        "private_snapshot_sha": private_snapshot_sha,
        "private_snapshot_verified": private_snapshot_verified,
        "execution_verified": execution_verified,
        "parent_cleanup_attested": False,
        "verified": verified,
        "error": error,
    }


def release_output_path_error(output_path: Path | None) -> str:
    """Reject evidence writes that would dirty either release source tree."""

    if output_path is None:
        return ""
    resolved = output_path.expanduser().resolve()
    roots = [SKILL_ROOT.resolve()]
    source_skill = str(os.environ.get(_SNAPSHOT_SOURCE_SKILL_ENV) or "").strip()
    if source_skill:
        roots.append(Path(source_skill).resolve())
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return (
            "release evidence output must be outside the skill source subtree: "
            f"{resolved}"
        )
    return ""


def emit_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json:
        sys.stdout.write(payload)
    else:
        records = result["scenarios"]
        print(
            f"synthetic E2E: {result['status']} "
            f"({len(records)}/{len(SCENARIOS)} scenarios)"
        )
        for record in records:
            print(f"- {record['name']}: {record['status']}")
            if record["error"]:
                print(f"  {record['error']}")
        result_identity = result.get("checkout_identity", {})
        identity_error = str(result_identity.get("error") or "")
        if identity_error and not result_identity.get("execution_verified"):
            print(f"- checkout-identity: failed\n  {identity_error}")


def main() -> int:
    args = parse_args()
    started = now()
    runtime_version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    expected_integration_sha = str(
        args.expected_integration_sha
        or os.environ.get("HLOOP_EXPECTED_INTEGRATION_SHA")
        or ""
    ).strip()
    identity_is_release_blocking = bool(expected_integration_sha) or not args.scenarios
    checkout_identity = checkout_identity_record(
        expected_integration_sha,
        require_private_snapshot=identity_is_release_blocking,
    )
    bootstrap_error = str(
        os.environ.get(_SNAPSHOT_BOOTSTRAP_ERROR_ENV) or ""
    ).strip()
    output_error = (
        release_output_path_error(args.output)
        if identity_is_release_blocking
        else ""
    )
    if output_error:
        # Preserve stdout evidence while refusing the source-tree write itself.
        args.output = None
    if bootstrap_error or output_error:
        checkout_identity["verified"] = False
        checkout_identity["execution_verified"] = False
        checkout_identity["parent_cleanup_attested"] = False
        checkout_identity["error"] = bootstrap_error or output_error
    checkout_head = str(checkout_identity.get("resolved_head_sha") or "")
    if identity_is_release_blocking and not checkout_identity.get(
        "execution_verified"
    ):
        result = {
            "schema_version": 1,
            "runner": "herdr-dev-loop-synthetic-e2e",
            "runtime_version": runtime_version,
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "checkout_identity": checkout_identity,
            "status": "failed",
            "started_at": started,
            "finished_at": now(),
            "workspace": None,
            "workspace_retained": False,
            "scenario_count": 0,
            "scenarios": [],
        }
        emit_result(args, result)
        return 1

    root = Path(tempfile.mkdtemp(prefix="hloop-synthetic-e2e-"))
    repo = make_repo(root)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(root / "home"),
            "HLOOP_CONFIG_HOME": str(root / "config-home"),
            "XDG_CONFIG_HOME": str(root / "xdg"),
            "HERDR_ENV": "1",
        }
    )
    namespace = "synthetic-e2e"
    context: dict[str, Any] = {
        "root": root,
        "repo": repo,
        "env": env,
        "namespace": namespace,
        "runtime_version": runtime_version,
        "checkout_head_sha": checkout_head,
        "expected_integration_sha": expected_integration_sha,
    }
    records: list[dict[str, Any]] = []
    overall = "passed"
    selected = SCENARIOS
    if args.scenarios:
        known = {name for name, _ in SCENARIOS}
        unknown = sorted(set(args.scenarios) - known)
        if unknown:
            raise SystemExit("unknown synthetic scenario(s): " + ", ".join(unknown))
        selected = tuple(item for item in SCENARIOS if item[0] in set(args.scenarios))
    for name, scenario in selected:
        scenario_started = time.monotonic()
        try:
            evidence = scenario(context)
            status = "passed"
            error = ""
        except Exception as exc:  # structured release evidence must survive one failed scenario
            status = "failed"
            evidence = {}
            error = f"{type(exc).__name__}: {exc}"
            overall = "failed"
        records.append(
            {
                "name": name,
                "status": status,
                "duration_ms": round((time.monotonic() - scenario_started) * 1000),
                "evidence": evidence,
                "error": error,
            }
        )
        if status == "failed":
            break

    retained = args.keep_workdir
    final_checkout_identity = checkout_identity_record(
        expected_integration_sha,
        require_private_snapshot=identity_is_release_blocking,
    )
    if identity_is_release_blocking and not final_checkout_identity[
        "execution_verified"
    ]:
        overall = "failed"
    result = {
        "schema_version": 1,
        "runner": "herdr-dev-loop-synthetic-e2e",
        "runtime_version": runtime_version,
        "state_format_version": hloop.STATE_FORMAT_VERSION,
        "schema_revision": hloop.STATE_SCHEMA_REVISION,
        "checkout_identity": final_checkout_identity,
        "status": overall,
        "started_at": started,
        "finished_at": now(),
        "workspace": str(root) if retained else None,
        "workspace_retained": retained,
        "scenario_count": len(records),
        "scenarios": records,
    }
    emit_result(args, result)
    if not retained:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if overall == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
