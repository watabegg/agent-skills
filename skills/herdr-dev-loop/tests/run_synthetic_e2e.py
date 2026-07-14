#!/usr/bin/env python3
"""Run the HLoop 0.5.0 release scenarios without live provider calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))

from hloop_lib.broker import spool_client_event  # noqa: E402
from hloop_lib.events import prepare_client_event, utc_now  # noqa: E402
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
    require(state["schema_revision"] == 1, "init did not create revision 1")
    require(state["skill_version"] == ctx["runtime_version"], "state version mismatch")
    require(state["resolved_config"]["max_workers"] == 2, "config snapshot missing")

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
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    dry_run = run(
        hloop_command(repo, namespace, "migrate", "--dry-run"),
        cwd=root,
        env=env,
    )
    migration_plan = json.loads(dry_run.stdout)
    require(migration_plan["to_format"] == 3, "migration target format mismatch")
    require(migration_plan["to_revision"] == 1, "migration target revision mismatch")
    run(
        hloop_command(repo, namespace, "migrate", "--apply"),
        cwd=root,
        env=env,
    )
    migrated = json.loads(path.read_text(encoding="utf-8"))
    backups = list((path.parent / "migration").glob("STATE.v2.r0.*.json"))
    require((migrated["state_format_version"], migrated["schema_revision"]) == (3, 1), "migration failed")
    require(len(backups) == 1, "migration backup missing")
    return {
        "config_source": migrated["config_source"]["kind"],
        "resolved_max_workers": migrated["resolved_config"]["max_workers"],
        "explicit_apply_max_workers": migrated["max_workers"],
        "migration_steps": migration_plan["applied_steps"],
        "backup_count": len(backups),
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
    (result_dir / "result.md").write_text("---\nstatus: done\n---\n\n# Result\n", encoding="utf-8")
    git(repo, "add", "-f", str(result_dir.relative_to(repo)))
    git(repo, "commit", "-m", "worker result")
    worker_head = git(repo, "rev-parse", branch).stdout.strip()
    git(repo, "switch", "master")
    (repo / "README.md").write_text("manager change\n", encoding="utf-8")
    git(repo, "commit", "-am", "manager change")

    def write_task() -> None:
        state = json.loads(path.read_text(encoding="utf-8"))
        state["tasks"][task_id] = {
            "status": "result_reported",
            "branch": branch,
            "worker_base_sha": base_sha,
            "base_sha": base_sha,
            "head_sha": worker_head,
            "result_status": "done",
            "merge_ready": True,
            "validation_recorded": True,
            "write_allow": ["README.md"],
            "write_deny": [],
            "active_attempt_id": f"{task_id}-A001",
            "attempt_id": f"{task_id}-A001",
            "attempt_no": 1,
        }
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    write_task()

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

    run(
        hloop_command(
            repo,
            namespace,
            "manager",
            "sleep",
            "--ttl-seconds",
            "300",
            "--manager-session-id",
            "synthetic-manager",
            "--pane-id",
            "synthetic-pane",
        ),
        cwd=root,
        env=env,
    )
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
            "task_contract_digest": hashlib.sha256(b"T002").hexdigest(),
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
    spool_client_event(spool_dir, milestone)
    recovered = run(
        hloop_command(repo, namespace, "broker", "recover"),
        cwd=root,
        env=env,
    )
    require("replayed 1 spooled report" in recovered.stdout, "spool recovery did not replay")
    broker_status = run(
        hloop_command(repo, namespace, "broker", "status"),
        cwd=root,
        env=env,
    )
    status = json.loads(broker_status.stdout)
    require(status["spooled"] == 0, "recovered spool was not cleared")
    require(status["events"] == 2, "broker event count mismatch")
    return {
        "semantic_ack_event": event_id,
        "wake_consumed": True,
        "recovered_event": spooled_id,
        "broker_counts": status,
    }


def scenario_requirements_decisions(ctx: dict[str, Any]) -> dict[str, Any]:
    root: Path = ctx["root"]
    repo: Path = ctx["repo"]
    env: dict[str, str] = ctx["env"]
    namespace: str = ctx["namespace"]
    path = state_path(repo, namespace)
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
    run(
        hloop_command(
            repo,
            namespace,
            "requirement",
            "new",
            "--id",
            "REQ-001",
            "--source-input",
            "U0001",
            "--acceptance",
            "structured release evidence exists",
            "--priority",
            "P1",
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
        env=env,
    )
    question_path = path.parent / "decisions" / "D001" / "QUESTION.md"
    require(question_path.is_file(), "decision question artifact missing")
    require("# 判断のお願い" in question_path.read_text(encoding="utf-8"), "liaison question is not plain Japanese")
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
        "requirement_status": projected["progress"]["status"],
        "decision_status": final_state["decisions"]["D001"]["status"],
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
    root: Path = ctx["root"]
    repo: Path = ctx["repo"]
    env: dict[str, str] = ctx["env"]
    namespace: str = ctx["namespace"]
    path = state_path(repo, namespace)
    state = json.loads(path.read_text(encoding="utf-8"))
    target = git(repo, "rev-parse", "master").stdout.strip()
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
            "cleanup_done": True,
        }
    }
    state["batches"] = {"B001": {"status": "closed", "title": "synthetic batch"}}
    state["current_batch_id"] = ""
    state["last_validation"] = {
        "head_sha": target,
        "results": [{"command": "synthetic validation", "result": "passed"}],
    }
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
    require("# Final Outcome" in report_path.read_text(encoding="utf-8"), "FINAL is not OutcomeReport-rendered")
    return {
        "phase": finished["phase"],
        "target_sha": target,
        "final_gate_generation": finished["final_gate"]["generation"],
        "report": str(report_path.relative_to(repo)),
        "fixture_note": "prepared an already-merged task and passing validation before invoking real final-gates and finish commands",
    }


SCENARIOS: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("config-and-migration", scenario_config_migration),
    ("attempt-and-merge-transaction", scenario_attempt_and_merge),
    ("merge-conflict-recovery", scenario_merge_conflict_recovery),
    ("cleanup-gate-resolution", scenario_cleanup_gate_resolution),
    ("report-broker-sleep-wake-recovery", scenario_report_broker),
    ("requirements-decisions-outcomes", scenario_requirements_decisions),
    ("dual-review-and-budget", scenario_review_budget),
    ("final-gate-and-finish", scenario_finish),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the structured result as JSON")
    parser.add_argument("--output", type=Path, help="also write the structured result to this path")
    parser.add_argument("--keep-workdir", action="store_true", help="retain the temporary repositories")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = now()
    runtime_version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
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
    }
    records: list[dict[str, Any]] = []
    overall = "passed"
    for name, scenario in SCENARIOS:
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
    result = {
        "schema_version": 1,
        "runner": "herdr-dev-loop-synthetic-e2e",
        "runtime_version": runtime_version,
        "state_format_version": hloop.STATE_FORMAT_VERSION,
        "schema_revision": hloop.STATE_SCHEMA_REVISION,
        "status": overall,
        "started_at": started,
        "finished_at": now(),
        "workspace": str(root) if retained else None,
        "workspace_retained": retained,
        "scenario_count": len(records),
        "scenarios": records,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json:
        sys.stdout.write(payload)
    else:
        print(f"synthetic E2E: {overall} ({len(records)}/{len(SCENARIOS)} scenarios)")
        for record in records:
            print(f"- {record['name']}: {record['status']}")
            if record["error"]:
                print(f"  {record['error']}")
    if not retained:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if overall == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
