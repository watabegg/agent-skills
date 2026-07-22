"""Integrated synthetic, failure, and migration crash evidence for HLoop 0.5.3."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest import mock


SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "hloop"
MATRIX_PATH = Path(__file__).parent / "fixtures" / "v053" / "scenario-matrix.json"
sys.path.insert(0, str(SKILL_ROOT.parents[1]))
sys.path.insert(0, str(SCRIPT.parent))

loader = importlib.machinery.SourceFileLoader("hloop_v053_e2e_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
if spec is None:
    raise RuntimeError("could not load hloop runtime")
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)

runner_loader = importlib.machinery.SourceFileLoader(
    "hloop_v053_synthetic_runner",
    str(Path(__file__).parent / "run_synthetic_e2e.py"),
)
runner_spec = importlib.util.spec_from_loader(runner_loader.name, runner_loader)
if runner_spec is None:
    raise RuntimeError("could not load synthetic E2E runner")
synthetic_runner = importlib.util.module_from_spec(runner_spec)
runner_loader.exec_module(synthetic_runner)

from hloop_lib.remediation import (  # noqa: E402
    CandidateClassificationConflict,
    RemediationLedger,
    TaskMaterializationObservation,
    approve_remediation_batch,
    complete_remediation_batch,
    create_remediation_batch,
    mark_ready_to_triage,
    reconcile_materialization,
    register_candidate,
)
from hloop_lib.config import project_agent_identity  # noqa: E402
from hloop_lib.review_epoch import (  # noqa: E402
    EpochExecutionOutcome,
    ReviewEpochCollection,
    ReviewEpochError,
    ReviewEpochPlan,
    create_successor_revision,
    validate_successor_revision,
)
from hloop_lib.worker_candidate import (  # noqa: E402
    DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK,
    evaluate_patch_review_rounds,
)


epoch_fixtures = __import__(
    "skills.herdr-dev-loop.tests.test_review_epoch_v053",
    fromlist=["epoch_plan", "reviewer_execution", "gap_execution"],
)
remediation_fixtures = __import__(
    "skills.herdr-dev-loop.tests.test_remediation_v053",
    fromlist=["candidate", "candidate_batch", "approve"],
)
candidate_fixtures = __import__(
    "skills.herdr-dev-loop.tests.test_worker_candidate_v053",
    fromlist=["candidate", "sealed", "review", "patch_finding"],
)
migration_fixtures = __import__(
    "skills.herdr-dev-loop.tests.test_migration_v053",
    fromlist=["legacy_state", "legacy_task"],
)


class ScenarioFailure(RuntimeError):
    """Raised when a required release invariant is not observed."""


class InjectedCrash(RuntimeError):
    """Deterministic crash injected immediately after one durable rename."""


FIXTURE_OBSERVED_PROCESS_IDENTITY = {
    "provider": "codex",
    "model": "gpt-5.6-sol",
    "effort": "xhigh",
}
FIXTURE_ATTESTED_PROCESS_IDENTITY = {
    "provider": "codex",
    "model": "gpt-5.6-sol",
    "effort": "xhigh",
}


def _fixture_process_identities(execution: Any) -> tuple[dict[str, Any], ...]:
    """Build canonical evidence from separate synthetic runtime observations."""

    return tuple(
        {
            "process_id": process.process_id,
            "agent_identity": project_agent_identity(
                {
                    "provider": process.provider,
                    "model": process.model,
                    "effort": process.effort,
                },
                observed=dict(FIXTURE_OBSERVED_PROCESS_IDENTITY),
                attested=dict(FIXTURE_ATTESTED_PROCESS_IDENTITY),
            ).as_dict(),
        }
        for process in execution.processes
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


def _expect_error(
    expected: type[BaseException] | tuple[type[BaseException], ...],
    operation: Callable[[], Any],
    label: str,
) -> str:
    try:
        operation()
    except expected as exc:
        return str(exc)
    raise ScenarioFailure(f"{label} did not fail closed")


def _complete_epoch(plan: ReviewEpochPlan) -> ReviewEpochCollection:
    collection = ReviewEpochCollection.create(plan)
    for execution in plan.required_executions:
        collection = _record_epoch_outcome(
            collection,
            execution.execution_id,
            artifact_complete=True,
            status="succeeded",
        )
    require(collection.status == "ready_to_triage", "epoch did not reach triage")
    return collection


def _record_epoch_outcome(
    collection: ReviewEpochCollection,
    execution_id: str,
    *,
    artifact_complete: bool,
    status: str,
) -> ReviewEpochCollection:
    plan = collection.plan
    execution = plan.execution(execution_id)
    process_ids = tuple(process.process_id for process in execution.processes)
    lease_id = f"lease-{execution_id}"
    capacity = (
        collection.capacity.reserve(
            plan,
            lease_id=lease_id,
            execution_id=execution_id,
            process_ids=process_ids,
            expires_at="2026-07-17T08:00:00Z",
        )
        .mark_running(lease_id)
        .mark_terminal(
            lease_id,
            reason="synthetic process tree exited",
            process_exit_confirmed=True,
        )
    )
    return collection.with_capacity(capacity).record_outcome(
        EpochExecutionOutcome.for_plan(
            plan,
            execution_id,
            artifact_digest=(
                epoch_fixtures.digest(
                    f"artifact-{plan.epoch_id}-{execution_id}"
                )
                if artifact_complete
                else ""
            ),
            artifact_complete=artifact_complete,
            completed_process_ids=process_ids,
            process_identities=(
                _fixture_process_identities(execution)
                if status == "succeeded"
                else ()
            ),
            status=status,
            terminal_at="2026-07-17T08:01:00Z",
        )
    )


def _candidate_for_outcome(
    plan: ReviewEpochPlan,
    outcome: EpochExecutionOutcome,
    *,
    observation_id: str,
    semantic_fingerprint: str,
) -> Any:
    candidate = remediation_fixtures.candidate(
        observation_id,
        source_kind=outcome.source_kind,
        source_execution_id=outcome.execution_id,
        semantic_fingerprint=semantic_fingerprint,
    )
    return replace(
        candidate,
        source_ref=outcome.artifact_ref,
        target_sha=plan.target_sha,
        classification=replace(
            candidate.classification,
            target_sha=plan.target_sha,
        ),
    )


def _collect_triage_candidates(
    collection: ReviewEpochCollection,
    artifact_candidates: dict[str, tuple[Any, ...]],
) -> tuple[Any, ...]:
    require(
        collection.status == "ready_to_triage",
        "candidate collection requires a triage-ready epoch",
    )
    expected_refs = {
        outcome.artifact_ref for outcome in collection.execution_outcomes
    }
    require(
        set(artifact_candidates) == expected_refs,
        "candidate evidence does not cover the collected epoch artifacts",
    )
    collected: list[Any] = []
    for outcome in collection.execution_outcomes:
        require(
            outcome.successful,
            f"candidate evidence used incomplete artifact {outcome.artifact_ref}",
        )
        for candidate in artifact_candidates[outcome.artifact_ref]:
            require(
                candidate.source_ref == outcome.artifact_ref
                and candidate.source_execution_id == outcome.execution_id
                and candidate.target_sha == collection.plan.target_sha,
                "candidate lineage does not match its collected artifact",
            )
            collected.append(candidate)
    return tuple(collected)


def scenario_v053_convergence() -> dict[str, Any]:
    initial_batch = {
        "batch_id": "B001",
        "status": "closed",
        "tasks": {"T001": "merged", "T002": "merged", "T003": "merged"},
    }
    require(
        initial_batch["status"] == "closed"
        and set(initial_batch["tasks"].values()) == {"merged"},
        "initial implementation batch was not closed and merged",
    )
    with tempfile.TemporaryDirectory(prefix="hloop-v053-convergence-e2e-") as directory:
        repo = Path(directory)

        def git(*args: str, commit_at: str = "") -> str:
            env = os.environ.copy()
            if commit_at:
                env.update(
                    {
                        "GIT_AUTHOR_NAME": "HLoop v0.5.3 E2E",
                        "GIT_AUTHOR_EMAIL": "hloop-v053-e2e@example.invalid",
                        "GIT_AUTHOR_DATE": commit_at,
                        "GIT_COMMITTER_NAME": "HLoop v0.5.3 E2E",
                        "GIT_COMMITTER_EMAIL": "hloop-v053-e2e@example.invalid",
                        "GIT_COMMITTER_DATE": commit_at,
                    }
                )
            completed = subprocess.run(
                ["git", *args],
                cwd=repo,
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            require(
                completed.returncode == 0,
                f"synthetic git {' '.join(args)} failed: {completed.stderr.strip()}",
            )
            return completed.stdout.strip()

        git("init", "--initial-branch=main")
        git("config", "user.email", "hloop-v053-e2e@example.invalid")
        git("config", "user.name", "HLoop v0.5.3 E2E")
        product_path = repo / "product" / "release-state.txt"
        product_path.parent.mkdir(parents=True)
        product_path.write_text("status=baseline\n", encoding="utf-8")
        git("add", "product/release-state.txt")
        git(
            "commit",
            "-m",
            "synthetic product baseline",
            commit_at="2026-07-17T07:00:00+00:00",
        )
        initial_base_sha = git("rev-parse", "HEAD")
        product_path.write_text(
            "status=initial_batch\nbatch=B001\n",
            encoding="utf-8",
        )
        git("add", "product/release-state.txt")
        git(
            "commit",
            "-m",
            "synthetic initial implementation batch",
            commit_at="2026-07-17T07:01:00+00:00",
        )
        initial = replace(
            epoch_fixtures.epoch_plan(),
            base_sha=initial_base_sha,
            target_sha=git("rev-parse", "HEAD"),
        )
        collected = _complete_epoch(initial)
        require(
            {outcome.execution_id for outcome in collected.execution_outcomes}
            == {"R001", "G001"},
            "same-SHA Reviewer and Gap were not both collected",
        )

        semantic = remediation_fixtures.fingerprint("e2e-causal-remediation")
        candidates_by_artifact: dict[str, tuple[Any, ...]] = {}
        for outcome in collected.execution_outcomes:
            candidates_by_artifact[outcome.artifact_ref] = (
                _candidate_for_outcome(
                    initial,
                    outcome,
                    observation_id=f"{outcome.execution_id}:F001",
                    semantic_fingerprint=semantic,
                ),
            )
        collected_candidates = _collect_triage_candidates(
            collected,
            candidates_by_artifact,
        )
        ledger = create_remediation_batch(
            RemediationLedger(),
            batch_id="RB001",
            epoch_id=initial.epoch_id,
            target_sha=initial.target_sha,
            required_execution_ids=tuple(
                execution.execution_id for execution in initial.required_executions
            ),
        )
        for candidate in collected_candidates:
            ledger = register_candidate(ledger, "RB001", candidate)
        ledger = mark_ready_to_triage(
            ledger,
            "RB001",
            terminal_execution_ids=tuple(
                outcome.execution_id for outcome in collected.execution_outcomes
            ),
        )
        ready_batch = ledger.batch("RB001")
        remediation_contract = remediation_fixtures.remediation_task(
            "T020",
            ready_batch.canonical_candidates[0].observation_ids[0],
            1,
        )
        remediation_contract["write_allow"] = ["product/release-state.txt"]
        release_scope = remediation_fixtures.locked_scope()
        approved = approve_remediation_batch(
            ledger,
            "RB001",
            approval_ref="manager-approval:RB001",
            scope_digest=release_scope.scope_digest,
            scope_revision=release_scope.scope_revision,
            task_contracts=(remediation_contract,),
            first_task_number=20,
            release_scope=release_scope,
        )
        with _configured_namespace("v053-convergence-e2e"):
            durable_state = {
                "state_format_version": 3,
                "schema_revision": 3,
                "namespace": hloop.LOOP_NAMESPACE,
                "run_id": "synthetic-v053-convergence",
                "skill_version": "0.5.3",
                "goal_id": "v053-convergence",
                "phase": "reviewing",
                "base_branch": "main",
                "integration_branch": "main",
                "persistence": "local-only",
                "branch_strategy": "integration",
                "worker_protocol": "native",
                "worker_qa_profile": "repo-default",
                "worker_agent_provider": "codex",
                "worker_agent_model": "gpt-5.6-sol",
                "review_policy": {"cadence": "batch", "max_fix_rounds": 2},
                "tasks": {},
                "batches": {},
                "reviews": {"R001": {}},
                "gaps": {"G001": {}},
                "advice": {},
                "decisions": {},
                "remediation_source_links": {},
                "inputs_index": {},
            }
            hloop.store_remediation_ledger(durable_state, approved)
            hloop.loop_path(repo).mkdir(parents=True)
            for planned in approved.batch("RB001").materialization_plan:
                hloop.materialize_planned_task(repo, durable_state, planned)
            hloop.save_state(repo, durable_state)

            persisted_state = hloop.load_state(repo)
            persisted_ledger = hloop.remediation_ledger_from_state(persisted_state)
            persisted_batch = persisted_ledger.batch("RB001")
            persisted_observations = hloop.materialization_observations(
                repo,
                persisted_state,
                persisted_batch,
            )
            materialized = reconcile_materialization(
                persisted_ledger,
                "RB001",
                persisted_observations,
            )
            require(
                materialized.status == "dispatched",
                "durable remediation materialization did not reconcile: "
                + "; ".join(
                    materialized.issues
                    or tuple(
                        f"{item.action}:{item.task_id}"
                        for item in materialized.repair_actions
                    )
                ),
            )
            hloop.store_remediation_ledger(persisted_state, materialized.ledger)
            hloop.save_state(repo, persisted_state)

            reloaded_state = hloop.load_state(repo)
            reloaded_ledger = hloop.remediation_ledger_from_state(reloaded_state)
            reloaded_observations = hloop.materialization_observations(
                repo,
                reloaded_state,
                reloaded_ledger.batch("RB001"),
            )
            reloaded_materialized = reconcile_materialization(
                reloaded_ledger,
                "RB001",
                reloaded_observations,
            )
            require(
                reloaded_materialized.status == "dispatched",
                "persisted materialization ledger did not reconcile after reload",
            )
            materialized = reloaded_materialized

        remediation_task_ids = tuple(
            item.task_id
            for item in materialized.ledger.batch("RB001").materialized_tasks
        )
        require(remediation_task_ids, "remediation did not dispatch a product task")
        persisted_observations_by_task = {
            item.task_id: item for item in reloaded_observations
        }
        materialized_allowed_paths = tuple(
            sorted(
                {
                    path
                    for task_id in remediation_task_ids
                    for path in (
                        persisted_observations_by_task[task_id].state_task_contract
                        or {}
                    ).get("write_allow", ())
                }
            )
        )
        artifact_allowed_paths = tuple(
            sorted(
                {
                    path
                    for task_id in remediation_task_ids
                    for path in (
                        persisted_observations_by_task[task_id].artifact_task_contract
                        or {}
                    ).get("write_allow", ())
                }
            )
        )
        require(
            materialized_allowed_paths == artifact_allowed_paths,
            "durable STATE/task artifact authorization paths disagree",
        )
        require(
            materialized_allowed_paths == ("product/release-state.txt",),
            "synthetic remediation contract did not authorize the exact product path",
        )
        product_path.write_text(
            "\n".join(
                (
                    "status=remediated",
                    "remediation_batch=RB001",
                    f"semantic_fingerprint={semantic}",
                    f"materialized_tasks={','.join(remediation_task_ids)}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        git("add", "product/release-state.txt")
        git(
            "commit",
            "-m",
            "apply synthetic remediation RB001",
            commit_at="2026-07-17T07:02:00+00:00",
        )
        remediation_head_sha = git("rev-parse", "HEAD")
        remediation_parent_sha = git("rev-parse", "HEAD^")
        remediation_changed_paths = tuple(
            git("diff", "--name-only", initial.target_sha, remediation_head_sha).splitlines()
        )
        committed_product = git("show", "HEAD:product/release-state.txt")
        require(
            remediation_parent_sha == initial.target_sha,
            "remediation commit is not based on the reviewed E001 target",
        )
        require(
            remediation_changed_paths == ("product/release-state.txt",),
            "remediation commit did not contain the deterministic product-only change",
        )
        require(
            set(remediation_changed_paths).issubset(materialized_allowed_paths),
            "remediation commit contains a path outside the materialized task contract",
        )
        require(
            all(task_id in committed_product for task_id in remediation_task_ids),
            "remediation commit is not bound to every materialized task",
        )
        completed = complete_remediation_batch(
            materialized.ledger,
            "RB001",
            outcome="product_changed",
        )
        completed_batch = completed.batch("RB001")
        require(completed.consumed_rounds == 1, "remediation consumed the wrong round count")
        require(
            completed_batch.completion_outcome == "product_changed"
            and git("rev-parse", "HEAD") == remediation_head_sha,
            "remediation completion is not bound to the committed product HEAD",
        )

        clean = ReviewEpochPlan(
            epoch_id="E002",
            epoch_revision=1,
            base_sha=initial.target_sha,
            target_sha=remediation_head_sha,
            scope_revision=initial.scope_revision,
            source_snapshot_revision=initial.source_snapshot_revision,
            scope_digest=initial.scope_digest,
            source_refs=initial.source_refs,
            policy_digest=initial.policy_digest,
            validation_identity=initial.validation_identity,
            audit_agent_budget=initial.audit_agent_budget,
            required_executions=(
                epoch_fixtures.reviewer_execution("R002"),
                epoch_fixtures.gap_execution("G002"),
            ),
        )
        clean_collection = _complete_epoch(clean)
        require(clean.target_sha != initial.target_sha, "clean epoch reused the old SHA")
        require(
            clean.target_sha == git("rev-parse", "HEAD"),
            "clean epoch target is not the synthetic checkout HEAD",
        )
        clean_candidates = _collect_triage_candidates(
            clean_collection,
            {
                outcome.artifact_ref: ()
                for outcome in clean_collection.execution_outcomes
            },
        )

        return {
            "initial_batch": initial_batch["batch_id"],
            "initial_batch_tasks": sorted(initial_batch["tasks"]),
            "initial_epoch": initial.epoch_id,
            "initial_target_sha": initial.target_sha,
            "same_sha_sources": sorted(
                outcome.execution_id for outcome in collected.execution_outcomes
            ),
            "candidate_lineage": [
                {
                    "observation_id": candidate.observation_id,
                    "source_ref": candidate.source_ref,
                    "source_execution_id": candidate.source_execution_id,
                    "target_sha": candidate.target_sha,
                }
                for candidate in collected_candidates
            ],
            "remediation_batches": 1,
            "remediation_rounds_consumed": completed.consumed_rounds,
            "remediation_task_ids": [
                item.task_id for item in completed.batch("RB001").materialized_tasks
            ],
            "remediation_product_commit": {
                "head_sha": remediation_head_sha,
                "parent_sha": remediation_parent_sha,
                "changed_paths": list(remediation_changed_paths),
                "authorized_paths": list(materialized_allowed_paths),
                "materialized_task_ids": list(remediation_task_ids),
                "completion_outcome": completed_batch.completion_outcome,
            },
            "clean_epoch": clean.epoch_id,
            "clean_target_sha": clean.target_sha,
            "clean_epoch_status": clean_collection.status,
            "clean_candidate_artifact_refs": sorted(
                outcome.artifact_ref for outcome in clean_collection.execution_outcomes
            ),
            "clean_candidate_count": len(clean_candidates),
        }


def _classification_conflict() -> dict[str, Any]:
    semantic = remediation_fixtures.fingerprint("e2e-classification-conflict")
    ledger = create_remediation_batch(
        RemediationLedger(),
        batch_id="RB001",
        epoch_id="E001",
        target_sha=remediation_fixtures.TARGET_SHA,
        required_execution_ids=("R001", "G001"),
    )
    ledger = register_candidate(
        ledger,
        "RB001",
        remediation_fixtures.candidate(
            "review:F001",
            source_kind="reviewer",
            source_execution_id="R001",
            semantic_fingerprint=semantic,
            severity="P1",
        ),
    )
    ledger = register_candidate(
        ledger,
        "RB001",
        remediation_fixtures.candidate(
            "gap:F001",
            source_kind="gap",
            source_execution_id="G001",
            semantic_fingerprint=semantic,
            severity="P2",
        ),
    )
    error = _expect_error(
        CandidateClassificationConflict,
        lambda: mark_ready_to_triage(
            ledger,
            "RB001",
            terminal_execution_ids=("R001", "G001"),
        ),
        "classification conflict",
    )
    return {"status": ledger.batch("RB001").status, "error": error}


def _incomplete_lane() -> dict[str, Any]:
    plan = epoch_fixtures.epoch_plan()
    collection = ReviewEpochCollection.create(plan)
    collection = _record_epoch_outcome(
        collection,
        "G001",
        artifact_complete=True,
        status="succeeded",
    )
    collection = _record_epoch_outcome(
        collection,
        "R001",
        artifact_complete=False,
        status="artifact_incomplete",
    )
    error = _expect_error(
        ReviewEpochError,
        lambda: collection.close(reason="unsafe clean close"),
        "incomplete lane",
    )
    outcomes = {outcome.execution_id: outcome for outcome in collection.execution_outcomes}
    require(set(outcomes) == {"R001", "G001"}, "not every execution was terminal")
    require(
        not outcomes["R001"].artifact_complete
        and outcomes["R001"].status == "artifact_incomplete",
        "R001 did not retain the incomplete artifact state",
    )
    return {
        "epoch_status": collection.status,
        "terminal_execution_ids": sorted(outcomes),
        "incomplete_artifact_execution_id": "R001",
        "other_execution_status": outcomes["G001"].status,
        "error": error,
    }


def _successor_drift() -> dict[str, Any]:
    parent = epoch_fixtures.epoch_plan()
    successor = create_successor_revision(
        parent,
        additional_executions=(epoch_fixtures.reviewer_execution("R002"),),
    )
    error = _expect_error(
        ReviewEpochError,
        lambda: validate_successor_revision(
            parent,
            replace(successor, target_sha="drifted-target"),
        ),
        "successor drift",
    )
    return {"parent_revision": 1, "successor_revision": 2, "error": error}


def _budget_exhaustion() -> dict[str, Any]:
    plan = epoch_fixtures.epoch_plan(budget=5)
    reviewer_ids = tuple(
        process.process_id for process in plan.execution("R001").processes
    )
    gap_ids = tuple(process.process_id for process in plan.execution("G001").processes)
    ledger = plan.capacity_ledger().reserve(
        plan,
        lease_id="lease-reviewer",
        execution_id="R001",
        process_ids=reviewer_ids,
        expires_at="2026-07-17T08:00:00Z",
    )
    error = _expect_error(
        ReviewEpochError,
        lambda: ledger.reserve(
            plan,
            lease_id="lease-gap",
            execution_id="G001",
            process_ids=gap_ids,
            expires_at="2026-07-17T08:00:00Z",
        ),
        "audit budget exhaustion",
    )
    return {"budget": 5, "reserved": ledger.reserved_slots, "error": error}


def _quarantined_orphan_lease() -> dict[str, Any]:
    plan = epoch_fixtures.epoch_plan(budget=6)
    reviewer_ids = tuple(
        process.process_id for process in plan.execution("R001").processes
    )
    ledger = (
        plan.capacity_ledger()
        .reserve(
            plan,
            lease_id="lease-orphan",
            execution_id="R001",
            process_ids=reviewer_ids,
            expires_at="2026-07-17T08:00:00Z",
        )
        .mark_running("lease-orphan")
        .mark_expired_quarantined(
            "lease-orphan",
            now="2026-07-17T08:00:00Z",
        )
    )
    error = _expect_error(
        ReviewEpochError,
        lambda: ledger.reserve(
            plan,
            lease_id="lease-gap",
            execution_id="G001",
            process_ids=("G001-coordinator",),
            expires_at="2026-07-17T09:00:00Z",
        ),
        "quarantined orphan lease",
    )
    return {
        "lease_status": ledger.lease("lease-orphan").status,
        "credential_revoked": ledger.lease("lease-orphan").credential_revoked,
        "capacity_held": ledger.reserved_slots,
        "error": error,
    }


def _patch_review_limit() -> dict[str, Any]:
    first = candidate_fixtures.candidate()
    first_seal = candidate_fixtures.sealed(first)
    first_review = candidate_fixtures.review(
        first_seal,
        verdict="fix_required",
        unresolved=(candidate_fixtures.patch_finding("first-regression"),),
    )
    second = candidate_fixtures.candidate(revision=2, tree="d" * 40)
    second_seal = candidate_fixtures.sealed(second, commit="e" * 40)
    second_review = candidate_fixtures.review(
        second_seal,
        attempt="PR-T006-A002",
        round_number=2,
        verdict="fix_required",
        unresolved=(candidate_fixtures.patch_finding("second-regression"),),
    )
    decision = evaluate_patch_review_rounds(
        second_seal,
        (first_review, second_review),
        current_task_contract_digest=second.task_contract_digest,
        max_rounds=DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK,
    )
    require(decision.requires_user_decision, "Patch Review limit did not stop")
    return {
        "action": decision.action,
        "rounds_used": decision.rounds_used,
        "last_candidate_sha": decision.last_candidate_sha,
        "automatic_task_ids": list(decision.automatic_task_ids),
    }


def _materialization_crash() -> dict[str, Any]:
    approved = remediation_fixtures.approve(
        remediation_fixtures.candidate_batch(),
        "RB001",
        task_number=20,
        remediation_round=1,
    )
    plan = approved.batch("RB001").materialization_plan[0]
    contract = plan.to_record()["task_contract"]
    partial = reconcile_materialization(
        approved,
        "RB001",
        (
            TaskMaterializationObservation(
                task_id=plan.task_id,
                state_task_contract=contract,
            ),
        ),
    )
    conflicting = dict(contract)
    conflicting["acceptance"] = ["tampered after write-ahead"]
    blocked = reconcile_materialization(
        approved,
        "RB001",
        (
            TaskMaterializationObservation(
                task_id=plan.task_id,
                state_task_contract=conflicting,
                artifact_task_contract=contract,
                artifact_digest=remediation_fixtures.canonical_digest(
                    {"artifact": plan.task_id}
                ),
                source_refs=plan.source_refs,
            ),
        ),
    )
    require(partial.status == "repair_required", "partial crash was not repairable")
    require(
        blocked.status == "remediation_reconcile_required",
        "conflicting crash state was guessed through",
    )
    require(blocked.ledger.consumed_rounds == 1, "crash replay consumed another round")
    return {
        "partial_status": partial.status,
        "repair_actions": sorted(action.action for action in partial.repair_actions),
        "conflict_status": blocked.status,
        "rounds_consumed": blocked.ledger.consumed_rounds,
    }


def _migration_decision_required() -> dict[str, Any]:
    state = migration_fixtures.legacy_state(
        tasks={
            "T009": migration_fixtures.legacy_task(
                "merged",
                id="T009",
                task_origin="finding",
                remediation_round=1,
            ),
            "T010": migration_fixtures.legacy_task(
                "merged",
                id="T010",
                task_origin="finding",
                remediation_round=1,
            ),
        }
    )
    plan = migration_fixtures.plan_format_three_revision_three(state)
    error = _expect_error(
        migration_fixtures.MigrationDecisionRequired,
        lambda: migration_fixtures.migrate_schema(
            state,
            target=migration_fixtures.V053_STATE_SCHEMA_VERSION,
            steps=(migration_fixtures.FORMAT_3_REVISION_3_MIGRATION,),
        ),
        "migration decision",
    )
    require(plan.remediation.decision_required, "migration ambiguity was not retained")
    return {
        "status": plan.remediation.status,
        "decision_candidates": list(plan.remediation.decision_candidates),
        "error": error,
    }


def scenario_v053_fail_closed_matrix() -> dict[str, Any]:
    cases = {
        "classification-conflict": _classification_conflict(),
        "incomplete-lane": _incomplete_lane(),
        "successor-drift": _successor_drift(),
        "budget-exhaustion": _budget_exhaustion(),
        "quarantined-orphan-lease": _quarantined_orphan_lease(),
        "patch-review-limit": _patch_review_limit(),
        "materialization-crash": _materialization_crash(),
        "migration-decision-required": _migration_decision_required(),
    }
    return {"case_count": len(cases), "cases": cases}


def _migrate_args(repo: Path, mode: str) -> argparse.Namespace:
    return argparse.Namespace(
        repo=str(repo),
        apply=mode == "apply",
        resume=mode == "resume",
        rollback=mode == "rollback",
        dry_run=mode == "dry-run",
    )


@contextmanager
def _configured_namespace(namespace: str) -> Iterator[None]:
    previous = hloop.LOOP_NAMESPACE
    try:
        hloop.configure_loop_namespace(namespace)
        yield
    finally:
        hloop.configure_loop_namespace(previous)


@contextmanager
def _migration_repo() -> Iterator[Path]:
    with (
        tempfile.TemporaryDirectory(prefix="hloop-v053-migration-e2e-") as directory,
        _configured_namespace("v053-migration-e2e"),
    ):
        repo = Path(directory)
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        hloop.loop_path(repo).mkdir(parents=True)
        state = {
            "state_format_version": 3,
            "schema_revision": 2,
            "namespace": "v053-migration-e2e",
            "run_id": "run-v052-e2e",
            "skill_version": "0.5.2",
            "tasks": {
                "T001": {
                    "id": "T001",
                    "status": "queued",
                    "kind": "implementation",
                    "task_origin": "planned",
                    "remediation_round": 0,
                    "source_finding": "",
                }
            },
            "batches": {},
            "reviews": {},
            "gaps": {},
            "review_protocol": "native",
            "review_policy": {
                "cadence": "batch",
                "pre_final_protocol": "native",
                "manual_final_protocol": "codex-review-multi-v2",
                "max_fix_rounds": 2,
                "scope_expansion_action": "follow_up",
                "final_required": "complete_zero_verified_actionable_findings",
                "lane_count": "auto",
            },
            "review_convergence": {
                "status": "not-started",
                "fix_round": 0,
                "authorized_extra_rounds": 0,
                "extra_round_authorization_refs": [],
            },
            "manual_final_review": {"status": "not-started"},
            "execution_metrics": {"review_fix_rounds": 0},
        }
        hloop.state_path(repo).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        task = hloop.task_file(repo, "T001")
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text(
            hloop.frontmatter({"id": "T001", "status": "queued"})
            + "\n\n# Legacy task\n",
            encoding="utf-8",
        )
        yield repo


def _invoke_migrate(repo: Path, mode: str) -> int:
    output = io.StringIO()
    with redirect_stdout(output):
        return_code = hloop.cmd_migrate(_migrate_args(repo, mode))
    require(
        return_code == 0,
        f"cmd_migrate --{mode} returned nonzero status {return_code}",
    )
    return return_code


def _crash_after_write(
    trigger: Callable[[Path, bytes], bool],
    operation: Callable[[], Any],
) -> None:
    original = hloop.write_bytes_durable
    crashed = False

    def injected(path: Path, payload: bytes) -> None:
        nonlocal crashed
        original(path, payload)
        if not crashed and trigger(path, payload):
            crashed = True
            raise InjectedCrash(f"crash after durable rename: {path}")

    with mock.patch.object(hloop, "write_bytes_durable", side_effect=injected):
        _expect_error(InjectedCrash, operation, "migration crash injection")
    require(crashed, "crash trigger did not match a durable rename")


def _migration_pairs(
    repo: Path,
) -> tuple[Any, dict[str, tuple[bytes, bytes]]]:
    state = json.loads(hloop.state_path(repo).read_text(encoding="utf-8"))
    plan, pairs, _steps = hloop.prepare_migration_transaction(repo, state)
    return plan, pairs


def _assert_recoverable_bytes(
    repo: Path,
    pairs: dict[str, tuple[bytes, bytes]],
) -> None:
    marker = hloop.load_migration_marker(repo)
    require(marker is not None, "partial tree has no recovery marker")
    for relative, pair in pairs.items():
        observed = hloop.migration_repository_path(repo, relative).read_bytes()
        require(observed in pair, f"noncanonical mixed bytes at {relative}")


def _assert_exact_tree(
    repo: Path,
    pairs: dict[str, tuple[bytes, bytes]],
    output_index: int,
) -> None:
    for relative, pair in pairs.items():
        observed = hloop.migration_repository_path(repo, relative).read_bytes()
        require(observed == pair[output_index], f"wrong recovered bytes at {relative}")


def _loop_bytes(repo: Path) -> dict[str, bytes]:
    root = hloop.loop_path(repo)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_recovery_refused_without_writes(repo: Path) -> dict[str, str]:
    before = _loop_bytes(repo)
    errors: dict[str, str] = {}
    for mode in ("resume", "rollback"):
        errors[mode] = _expect_error(
            hloop.HLoopError,
            lambda mode=mode: _invoke_migrate(repo, mode),
            f"migration {mode} refusal",
        )
        require(
            _loop_bytes(repo) == before,
            f"migration --{mode} changed bytes before refusing recovery",
        )
    return errors


def _corrupted_archive_identity() -> dict[str, Any]:
    with _migration_repo() as repo:
        plan, pairs = _migration_pairs(repo)
        hloop.persist_prepared_migration(repo, plan, pairs)
        relative = sorted(pairs)[0]
        archive = (
            hloop.migration_generation_root(repo, plan.migration_generation)
            / "archive"
            / relative
        )
        archive.write_bytes(archive.read_bytes() + b"\ncorrupted-archive-identity")
        errors = _assert_recovery_refused_without_writes(repo)
        require(
            all("archive identity" in error for error in errors.values()),
            "corrupted archive refusal did not identify persisted identity drift",
        )
        return {
            "corrupted_artifact": relative,
            "refused_modes": sorted(errors),
            "exact_bytes_preserved": True,
        }


def _unknown_partial_live_bytes() -> dict[str, Any]:
    with _migration_repo() as repo:
        plan, pairs = _migration_pairs(repo)
        hloop.persist_prepared_migration(repo, plan, pairs)
        state_relative = hloop.state_path(repo).relative_to(repo).as_posix()
        relative = next(path for path in sorted(pairs) if path != state_relative)
        live = hloop.migration_repository_path(repo, relative)
        live.write_bytes(b"unknown-partial-live-bytes")
        errors = _assert_recovery_refused_without_writes(repo)
        require(
            all("neither archived source" in error for error in errors.values()),
            "unknown live bytes did not reach the digest recovery refusal",
        )
        return {
            "corrupted_artifact": relative,
            "refused_modes": sorted(errors),
            "exact_bytes_preserved": True,
        }


def _marker_status(payload: bytes) -> str:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    return str(value.get("status") or "") if isinstance(value, dict) else ""


def _marker_boundary(status: str, mode: str, output_index: int) -> dict[str, Any]:
    with _migration_repo() as repo:
        active = hloop.migration_active_marker_path(repo).resolve()
        if mode.startswith("rollback"):
            _invoke_migrate(repo, "apply")
        _crash_after_write(
            lambda path, payload: path.resolve() == active
            and _marker_status(payload) == status,
            lambda: _invoke_migrate(
                repo,
                "rollback" if mode.startswith("rollback") else "apply",
            ),
        )
        marker = hloop.load_migration_marker(repo)
        require(marker is not None, f"{status} marker was not durable")
        plan, pairs = hloop.load_prepared_migration(repo, marker)
        _assert_recoverable_bytes(repo, pairs)
        _invoke_migrate(
            repo,
            "rollback" if mode.startswith("rollback") else "resume",
        )
        _assert_exact_tree(repo, pairs, output_index)
        return {
            "status_after_crash": status,
            "generation": plan.migration_generation,
            "artifact_count": len(pairs),
        }


def _target_rename_boundaries(*, rollback: bool) -> list[str]:
    with _migration_repo() as template_repo:
        _plan, template_pairs = _migration_pairs(template_repo)
        relative_paths = sorted(
            template_pairs,
            key=lambda path: (
                path == hloop.state_path(template_repo).relative_to(template_repo).as_posix(),
                path,
            ),
        )

    observed_boundaries: list[str] = []
    for relative in relative_paths:
        with _migration_repo() as repo:
            plan, pairs = _migration_pairs(repo)
            if rollback:
                hloop.persist_prepared_migration(repo, plan, pairs)
                _invoke_migrate(repo, "resume")
                operation = lambda: _invoke_migrate(repo, "rollback")
                recovery_mode = "rollback"
                output_index = 0
            else:
                hloop.persist_prepared_migration(repo, plan, pairs)
                operation = lambda: _invoke_migrate(repo, "resume")
                recovery_mode = "resume"
                output_index = 1
            target = hloop.migration_repository_path(repo, relative).resolve()
            _crash_after_write(
                lambda path, _payload, target=target: path.resolve() == target,
                operation,
            )
            _assert_recoverable_bytes(repo, pairs)
            _invoke_migrate(repo, recovery_mode)
            _assert_exact_tree(repo, pairs, output_index)
            observed_boundaries.append(relative)
    return observed_boundaries


def _first_mutation_boundary() -> dict[str, Any]:
    with _migration_repo() as repo:
        _invoke_migrate(repo, "apply")
        args = argparse.Namespace(command="task", task_command="update", dry_run=False)
        with mock.patch.object(
            hloop,
            "save_state",
            side_effect=InjectedCrash("crash before state mutation projection"),
        ):
            _expect_error(
                InjectedCrash,
                lambda: hloop.record_first_v053_mutation(repo, args),
                "first mutation marker",
            )
        marker = hloop.load_migration_marker(repo)
        require(marker is not None, "first mutation marker disappeared")
        require(marker["first_v053_mutation_at"], "mutation marker was not durable first")
        state_before = json.loads(hloop.state_path(repo).read_text(encoding="utf-8"))
        require(not state_before["first_v053_mutation_at"], "state was written before crash")

        hloop.record_first_v053_mutation(repo, args)
        repaired = json.loads(hloop.state_path(repo).read_text(encoding="utf-8"))
        require(
            repaired["first_v053_mutation_at"] == marker["first_v053_mutation_at"],
            "first mutation state did not repair from marker",
        )
        error = _expect_error(
            hloop.HLoopError,
            lambda: _invoke_migrate(repo, "rollback"),
            "rollback after first mutation",
        )
        return {
            "marker_first": True,
            "state_repaired": True,
            "rollback_rejected": "rollback is forbidden" in error,
        }


def scenario_v053_migration_crash_matrix() -> dict[str, Any]:
    prepared = _marker_boundary("prepared", "apply", 1)
    apply_renames = _target_rename_boundaries(rollback=False)
    committed = _marker_boundary("committed", "apply", 1)
    corrupted_archive = _corrupted_archive_identity()
    unknown_live_bytes = _unknown_partial_live_bytes()

    with _migration_repo() as repo:
        _invoke_migrate(repo, "apply")
        marker = hloop.load_migration_marker(repo)
        require(marker is not None, "committed marker missing")
        plan, _pairs = hloop.load_prepared_migration(repo, marker)
        rollback_prepared_status = str(plan.rollback_prepared_marker["status"])
        rolled_back_status = str(plan.rolled_back_marker["status"])
    rollback_prepared = _marker_boundary(rollback_prepared_status, "rollback", 0)
    rollback_renames = _target_rename_boundaries(rollback=True)
    rolled_back = _marker_boundary(rolled_back_status, "rollback", 0)
    first_mutation = _first_mutation_boundary()

    require(apply_renames == rollback_renames, "apply/rollback target order drifted")
    return {
        "prepared": prepared,
        "apply_target_renames": apply_renames,
        "committed": committed,
        "corrupted_archive_identity": corrupted_archive,
        "unknown_partial_live_bytes": unknown_live_bytes,
        "rollback_prepared": rollback_prepared,
        "rollback_target_renames": rollback_renames,
        "rolled_back": rolled_back,
        "first_mutation": first_mutation,
        "zero_return_codes_required": ["apply", "resume", "rollback"],
        "mixed_schema_after_recovery": False,
    }


SCENARIOS: dict[str, Callable[[], dict[str, Any]]] = {
    "v053-convergence": scenario_v053_convergence,
    "v053-fail-closed-matrix": scenario_v053_fail_closed_matrix,
    "v053-migration-crash-matrix": scenario_v053_migration_crash_matrix,
}


def run_scenario(name: str) -> dict[str, Any]:
    try:
        scenario = SCENARIOS[name]
    except KeyError as exc:
        raise ScenarioFailure(f"unknown v0.5.3 scenario: {name}") from exc
    return scenario()


class HLoopV053E2ETests(unittest.TestCase):
    def test_scenario_fixture_matches_executable_matrix(self):
        fixture = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(fixture["scenarios"]), set(SCENARIOS))
        self.assertEqual(
            fixture["scenarios"]["v053-fail-closed-matrix"]["evidence"],
            [
                "classification-conflict",
                "incomplete-lane",
                "successor-drift",
                "budget-exhaustion",
                "quarantined-orphan-lease",
                "patch-review-limit",
                "materialization-crash",
                "migration-decision-required",
            ],
        )

    def test_converges_after_one_remediation_batch_on_a_new_sha(self):
        evidence = scenario_v053_convergence()
        repeated = scenario_v053_convergence()
        self.assertEqual(evidence["remediation_batches"], 1)
        self.assertNotEqual(evidence["initial_target_sha"], evidence["clean_target_sha"])
        product_commit = evidence["remediation_product_commit"]
        self.assertEqual(product_commit["head_sha"], evidence["clean_target_sha"])
        self.assertEqual(product_commit["head_sha"], repeated["clean_target_sha"])
        self.assertEqual(product_commit["parent_sha"], evidence["initial_target_sha"])
        self.assertEqual(product_commit["changed_paths"], ["product/release-state.txt"])
        self.assertEqual(product_commit["authorized_paths"], product_commit["changed_paths"])
        self.assertEqual(product_commit["completion_outcome"], "product_changed")
        self.assertEqual(
            product_commit["materialized_task_ids"],
            evidence["remediation_task_ids"],
        )
        self.assertEqual(evidence["clean_candidate_count"], 0)
        self.assertEqual(len(evidence["candidate_lineage"]), 2)
        self.assertEqual(
            {item["target_sha"] for item in evidence["candidate_lineage"]},
            {evidence["initial_target_sha"]},
        )

    def test_convergence_requires_production_materialization_evidence(self):
        with self.subTest("materializer bypass"):
            with mock.patch.object(
                hloop,
                "materialize_planned_task",
                return_value=None,
            ):
                with self.assertRaisesRegex(
                    ScenarioFailure,
                    "durable remediation materialization did not reconcile",
                ):
                    scenario_v053_convergence()

        original_observer = hloop.materialization_observations

        def falsified_observations(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
            observations = list(original_observer(*args, **kwargs))
            state_contract = dict(observations[0].state_task_contract or {})
            state_contract["write_allow"] = ["product/not-authorized.txt"]
            observations[0] = replace(
                observations[0],
                state_task_contract=state_contract,
            )
            return tuple(observations)

        with self.subTest("durable state observation falsified"):
            with mock.patch.object(
                hloop,
                "materialization_observations",
                side_effect=falsified_observations,
            ):
                with self.assertRaisesRegex(
                    ScenarioFailure,
                    "durable remediation materialization did not reconcile",
                ):
                    scenario_v053_convergence()

    def test_failure_matrix_stops_all_eight_unsafe_transitions(self):
        evidence = scenario_v053_fail_closed_matrix()
        self.assertEqual(evidence["case_count"], 8)
        self.assertEqual(
            evidence["cases"]["patch-review-limit"]["action"],
            "user_decision_required",
        )
        self.assertEqual(
            evidence["cases"]["materialization-crash"]["conflict_status"],
            "remediation_reconcile_required",
        )
        incomplete = evidence["cases"]["incomplete-lane"]
        self.assertEqual(incomplete["terminal_execution_ids"], ["G001", "R001"])
        self.assertEqual(incomplete["incomplete_artifact_execution_id"], "R001")
        self.assertEqual(incomplete["other_execution_status"], "succeeded")

    def test_migration_crash_matrix_recovers_every_durable_boundary(self):
        evidence = scenario_v053_migration_crash_matrix()
        self.assertEqual(
            evidence["apply_target_renames"],
            evidence["rollback_target_renames"],
        )
        self.assertGreaterEqual(len(evidence["apply_target_renames"]), 2)
        self.assertFalse(evidence["mixed_schema_after_recovery"])
        self.assertTrue(evidence["first_mutation"]["rollback_rejected"])
        self.assertTrue(
            evidence["corrupted_archive_identity"]["exact_bytes_preserved"]
        )
        self.assertEqual(
            evidence["corrupted_archive_identity"]["refused_modes"],
            ["resume", "rollback"],
        )
        self.assertTrue(
            evidence["unknown_partial_live_bytes"]["exact_bytes_preserved"]
        )
        self.assertEqual(
            evidence["unknown_partial_live_bytes"]["refused_modes"],
            ["resume", "rollback"],
        )

    def test_migration_wrapper_rejects_nonzero_return_status(self):
        with _migration_repo() as repo:
            with mock.patch.object(hloop, "cmd_migrate", return_value=7):
                with self.assertRaisesRegex(
                    ScenarioFailure,
                    r"cmd_migrate --apply returned nonzero status 7",
                ):
                    _invoke_migrate(repo, "apply")

    def test_migration_fixture_restores_namespace_after_setup_exception(self):
        previous = hloop.LOOP_NAMESPACE
        with mock.patch.object(
            hloop,
            "loop_path",
            side_effect=RuntimeError("synthetic setup failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic setup failure"):
                with _migration_repo():
                    self.fail("fixture yielded after setup failure")
        self.assertEqual(hloop.LOOP_NAMESPACE, previous)

    def test_synthetic_runner_rejects_checkout_identity_mismatch(self):
        mismatch = "0" * 40
        if subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SKILL_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip() == mismatch:
            mismatch = "f" * 40
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "run_synthetic_e2e.py"),
                "--json",
                "--expected-integration-sha",
                mismatch,
            ],
            cwd=SKILL_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["scenario_count"], 0)
        self.assertEqual(
            evidence["checkout_identity"]["expected_integration_sha"],
            mismatch,
        )
        self.assertFalse(evidence["checkout_identity"]["verified"])

    def test_synthetic_runner_rejects_dirty_skill_source_identity(self):
        def git(repo: Path, *args: str) -> str:
            completed = subprocess.run(
                ["git", *args],
                cwd=repo,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout.strip()

        for mutation in ("unstaged", "staged", "untracked"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                skill_root = repo / "skills" / "herdr-dev-loop"
                skill_root.mkdir(parents=True)
                tracked = skill_root / "README.md"
                tracked.write_text("baseline\n", encoding="utf-8")
                git(repo, "init", "--initial-branch=main")
                git(repo, "config", "user.email", "hloop-e2e@example.invalid")
                git(repo, "config", "user.name", "HLoop E2E")
                git(repo, "add", "skills/herdr-dev-loop/README.md")
                git(repo, "commit", "-m", "baseline")
                expected = git(repo, "rev-parse", "HEAD")

                clean = synthetic_runner.checkout_identity_record(
                    expected,
                    skill_root=skill_root,
                )
                self.assertTrue(clean["verified"])
                self.assertTrue(clean["skill_subtree_clean"])
                self.assertEqual(clean["dirty_paths"], [])
                with mock.patch.dict(
                    os.environ,
                    {
                        "HLOOP_SYNTHETIC_PRIVATE_SNAPSHOT_SHA": expected,
                        "HLOOP_SYNTHETIC_SOURCE_SKILL_ROOT": str(skill_root),
                    },
                    clear=False,
                ):
                    spoofed = synthetic_runner.checkout_identity_record(
                        expected,
                        skill_root=skill_root,
                        require_private_snapshot=True,
                    )
                self.assertFalse(spoofed["verified"])
                self.assertFalse(spoofed["private_snapshot_verified"])
                self.assertEqual(spoofed["execution_source"], "mutable-checkout")

                if mutation == "untracked":
                    (skill_root / "UNTRACKED.md").write_text(
                        "not in the release commit\n",
                        encoding="utf-8",
                    )
                else:
                    tracked.write_text("dirty\n", encoding="utf-8")
                    if mutation == "staged":
                        git(repo, "add", "skills/herdr-dev-loop/README.md")

                dirty = synthetic_runner.checkout_identity_record(
                    expected,
                    skill_root=skill_root,
                )
                self.assertFalse(dirty["verified"])
                self.assertFalse(dirty["skill_subtree_clean"])
                self.assertTrue(dirty["dirty_paths"])
                self.assertIn("source changes", dirty["error"])

    def test_snapshot_output_path_uses_argparse_last_value(self):
        with mock.patch.object(
            sys,
            "argv",
            [
                "run_synthetic_e2e.py",
                "--output",
                "/tmp/first.json",
                "--output=/tmp/second.json",
                "--expected-integration-sha",
                "a" * 40,
                "--expected-integration-sha=" + "b" * 40,
            ],
        ):
            self.assertEqual(
                synthetic_runner._raw_output_path(), Path("/tmp/second.json")
            )
            self.assertEqual(
                synthetic_runner._raw_expected_integration_sha(), "b" * 40
            )

    def test_parent_cleanup_attestation_validates_child_identity(self):
        expected = "a" * 40
        child = {
            "status": "passed",
            "checkout_identity": {
                "expected_integration_sha": expected,
                "resolved_head_sha": expected,
                "private_snapshot_sha": expected,
                "skill_subtree_clean": True,
                "private_snapshot_verified": True,
                "execution_verified": True,
                "verified": False,
                "parent_cleanup_attested": False,
                "error": "parent cleanup attestation is pending",
            },
        }
        attested = json.loads(
            synthetic_runner._mark_snapshot_cleanup_passed(json.dumps(child))
        )
        self.assertTrue(attested["checkout_identity"]["verified"])
        self.assertTrue(
            attested["checkout_identity"]["parent_cleanup_attested"]
        )
        self.assertEqual(attested["checkout_identity"]["error"], "")
        self.assertEqual(
            attested["snapshot_cleanup"], {"status": "passed", "error": ""}
        )

        child["checkout_identity"]["execution_verified"] = False
        rejected = json.loads(
            synthetic_runner._mark_snapshot_cleanup_passed(json.dumps(child))
        )
        self.assertEqual(rejected["status"], "failed")
        self.assertFalse(rejected["checkout_identity"]["verified"])
        self.assertFalse(
            rejected["checkout_identity"]["parent_cleanup_attested"]
        )

    def test_parent_extracts_final_json_after_incidental_stdout(self):
        prefix = "sent manager message\n{\n  \"diagnostic\": true\n}\n"
        final = {"status": "failed", "checkout_identity": {"verified": False}}
        split = synthetic_runner._trailing_json_payload(
            prefix + json.dumps(final, indent=2) + "\n"
        )

        self.assertIsNotNone(split)
        incidental, payload = split
        self.assertEqual(incidental, prefix)
        self.assertEqual(json.loads(payload), final)

    def test_synthetic_runner_main_fails_before_scenarios_on_dirty_skill_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            copied_skill = repo / "skills" / "herdr-dev-loop"
            shutil.copytree(
                SKILL_ROOT,
                copied_skill,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            def git(*args: str) -> str:
                completed = subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return completed.stdout.strip()

            git("init", "--initial-branch=main")
            git("config", "user.email", "hloop-e2e@example.invalid")
            git("config", "user.name", "HLoop E2E")
            git("add", "skills/herdr-dev-loop")
            git("commit", "-m", "synthetic release candidate")
            expected = git("rev-parse", "HEAD")
            clean_completed = subprocess.run(
                [
                    sys.executable,
                    str(copied_skill / "tests" / "run_synthetic_e2e.py"),
                    "--json",
                    "--scenario",
                    "user-stop-freeze",
                    "--expected-integration-sha",
                    expected,
                ],
                cwd=repo,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(clean_completed.returncode, 0, clean_completed.stderr)
            clean_evidence = json.loads(clean_completed.stdout)
            self.assertEqual(clean_evidence["status"], "passed")
            self.assertEqual(clean_evidence["scenario_count"], 1)
            self.assertTrue(clean_evidence["checkout_identity"]["verified"])
            self.assertTrue(
                clean_evidence["checkout_identity"]["parent_cleanup_attested"]
            )
            self.assertTrue(
                clean_evidence["checkout_identity"]["execution_verified"]
            )
            self.assertEqual(
                clean_evidence["checkout_identity"]["execution_source"],
                "private-detached-worktree",
            )
            self.assertEqual(
                clean_evidence["checkout_identity"]["private_snapshot_sha"],
                expected,
            )
            self.assertTrue(
                clean_evidence["checkout_identity"]["private_snapshot_verified"]
            )
            self.assertEqual(
                clean_evidence["snapshot_cleanup"],
                {"status": "passed", "error": ""},
            )
            self.assertEqual(git("status", "--porcelain"), "")

            forbidden_output = copied_skill / "release-evidence.json"
            output_completed = subprocess.run(
                [
                    sys.executable,
                    str(copied_skill / "tests" / "run_synthetic_e2e.py"),
                    "--json",
                    "--scenario",
                    "user-stop-freeze",
                    "--expected-integration-sha",
                    expected,
                    "--output",
                    str(forbidden_output),
                ],
                cwd=repo,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(output_completed.returncode, 1, output_completed.stderr)
            output_evidence = json.loads(output_completed.stdout)
            self.assertEqual(output_evidence["scenario_count"], 0)
            self.assertFalse(output_evidence["checkout_identity"]["verified"])
            self.assertIn(
                "output must be outside",
                output_evidence["checkout_identity"]["error"],
            )
            self.assertFalse(forbidden_output.exists())
            self.assertEqual(git("status", "--porcelain"), "")

            with (copied_skill / "README.md").open("a", encoding="utf-8") as handle:
                handle.write("dirty overlay\n")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(copied_skill / "tests" / "run_synthetic_e2e.py"),
                    "--json",
                    "--scenario",
                    "user-stop-freeze",
                    "--expected-integration-sha",
                    expected,
                ],
                cwd=repo,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            evidence = json.loads(completed.stdout)
            self.assertEqual(evidence["status"], "failed")
            self.assertEqual(evidence["scenario_count"], 0)
            identity = evidence["checkout_identity"]
            self.assertFalse(identity["verified"])
            self.assertFalse(identity["skill_subtree_clean"])
            self.assertIn("README.md", identity["error"])

    def test_synthetic_runner_requires_external_checkout_identity(self):
        env = os.environ.copy()
        env.pop("HLOOP_EXPECTED_INTEGRATION_SHA", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "run_synthetic_e2e.py"),
                "--json",
            ],
            cwd=SKILL_ROOT,
            check=False,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["scenario_count"], 0)
        self.assertEqual(
            evidence["checkout_identity"]["expected_integration_sha"],
            "",
        )
        self.assertFalse(evidence["checkout_identity"]["verified"])
        self.assertIn("is required", evidence["checkout_identity"]["error"])


if __name__ == "__main__":
    unittest.main()
