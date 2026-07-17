"""Pure schema and transaction-planning primitives for HLoop state.

The CLI owns repository locks, fsync, temporary files, and atomic renames.  This
module owns deterministic in-memory migration, artifact digest manifests, and
recovery decisions.  Dry-run, apply, resume, and rollback can therefore share
one fail-closed model without this module reading or mutating filesystem state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from .config import REVIEW_POLICY_DEFAULTS
from .task_contract import (
    LEGACY_CONTRACT_SCHEMA_REVISION,
    V053_CONTRACT_SCHEMA_REVISION,
    ContractValidationError,
    LegacyTaskMigration,
    migrate_legacy_task_contract,
)


State = Mapping[str, Any]
MigrationTransform = Callable[[dict[str, Any]], Mapping[str, Any]]


class MigrationError(ValueError):
    """Base error for invalid schemas, migration definitions, or paths."""


class FutureSchemaError(MigrationError):
    """Raised when state is newer than the runtime target."""


class MissingMigrationError(MigrationError):
    """Raised when no contiguous migration path reaches the target."""


class MigrationDecisionRequired(MigrationError):
    """Raised when legacy evidence cannot be mapped to one safe 0.5.3 state."""

    def __init__(self, plan: "V053StateMigrationPlan"):
        self.plan = plan
        super().__init__(
            "; ".join(plan.blocking_reasons)
            or "migration requires an explicit Manager decision"
        )


@dataclass(frozen=True, order=True, slots=True)
class SchemaVersion:
    """A state format and its monotonically increasing in-format revision."""

    format_version: int
    revision: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.format_version, bool)
            or not isinstance(self.format_version, int)
            or self.format_version < 1
        ):
            raise MigrationError("format_version must be a positive integer")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise MigrationError("revision must be a non-negative integer")

    def label(self) -> str:
        """Return a stable label suitable for diagnostics and audit output."""

        return f"format-{self.format_version}.revision-{self.revision}"


# Format 3 remains the persisted state format for 0.5.2.  The revision bump is
# intentionally explicit so callers can append this step to their runtime
# migration chain without changing the meaning of an older format.
V052_STATE_SCHEMA_VERSION = SchemaVersion(3, 2)
FORMAT_3_REVISION_2 = V052_STATE_SCHEMA_VERSION
V053_STATE_SCHEMA_VERSION = SchemaVersion(3, 3)
FORMAT_3_REVISION_3 = V053_STATE_SCHEMA_VERSION

LEGACY_REVIEW_POLICY = {
    **REVIEW_POLICY_DEFAULTS,
    "cadence": "merge-count",
}


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """One declared edge in the schema revision graph.

    A step must advance exactly one revision within a format, or enter the next
    format at revision zero.  The transform changes content only; the migration
    engine owns ``state_format_version`` and ``schema_revision`` markers.
    """

    source: SchemaVersion
    target: SchemaVersion
    name: str
    transform: MigrationTransform = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, SchemaVersion) or not isinstance(
            self.target, SchemaVersion
        ):
            raise MigrationError("migration source and target must be SchemaVersion values")
        if not isinstance(self.name, str) or not self.name.strip():
            raise MigrationError("migration step name must not be empty")
        same_format_increment = (
            self.target.format_version == self.source.format_version
            and self.target.revision == self.source.revision + 1
        )
        next_format = (
            self.target.format_version == self.source.format_version + 1
            and self.target.revision == 0
        )
        if not (same_format_increment or next_format):
            raise MigrationError(
                "migration steps must advance one revision or enter the next "
                "format at revision zero"
            )
        if not callable(self.transform):
            raise MigrationError("migration transform must be callable")


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A validated, ordered migration path with no state mutation."""

    source: SchemaVersion
    target: SchemaVersion
    steps: tuple[MigrationStep, ...]

    @property
    def changed(self) -> bool:
        return self.source != self.target

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """The migrated copy and the exact plan used to produce it."""

    state: Mapping[str, Any]
    plan: MigrationPlan

    @property
    def changed(self) -> bool:
        return self.plan.changed

    @property
    def applied_steps(self) -> tuple[str, ...]:
        return self.plan.step_names


@dataclass(frozen=True, slots=True)
class RemediationBatchRecovery:
    """One deterministically reconstructed legacy remediation batch."""

    recovery_key: str
    task_ids: tuple[str, ...]
    source_refs: tuple[str, ...] = ()
    batch_id: str = ""
    remediation_round: int | None = None
    target_shas: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "recovery_key": self.recovery_key,
            "task_ids": list(self.task_ids),
            "source_refs": list(self.source_refs),
            "batch_id": self.batch_id,
            "remediation_round": self.remediation_round,
            "target_shas": list(self.target_shas),
        }


@dataclass(frozen=True, slots=True)
class RemediationHistoryRecovery:
    """Dry-run result for mapping 0.5.2 fix history to consumed batches."""

    status: str
    consumed_rounds: int | None
    batches: tuple[RemediationBatchRecovery, ...] = ()
    extra_round_authorization_refs: tuple[str, ...] = ()
    decision_candidates: tuple[int, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def decision_required(self) -> bool:
        return self.status == "migration_decision_required"

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "consumed_rounds": self.consumed_rounds,
            "batches": [batch.to_record() for batch in self.batches],
            "extra_round_authorization_refs": list(
                self.extra_round_authorization_refs
            ),
            "decision_candidates": list(self.decision_candidates),
            "issues": list(self.issues),
        }


@dataclass(frozen=True, slots=True)
class V053StateMigrationPlan:
    """Side-effect-free state projection and any decision-required blockers."""

    state: Mapping[str, Any]
    task_migrations: Mapping[str, LegacyTaskMigration]
    remediation: RemediationHistoryRecovery
    blocking_reasons: tuple[str, ...] = ()

    @property
    def applicable(self) -> bool:
        return not self.blocking_reasons

    def require_applicable(self) -> None:
        if self.blocking_reasons:
            raise MigrationDecisionRequired(self)

    def to_record(self) -> dict[str, Any]:
        return {
            "source_schema": _schema_record(V052_STATE_SCHEMA_VERSION),
            "target_schema": _schema_record(V053_STATE_SCHEMA_VERSION),
            "applicable": self.applicable,
            "task_migrations": {
                task_id: migration.to_record()
                for task_id, migration in sorted(self.task_migrations.items())
            },
            "remediation": self.remediation.to_record(),
            "blocking_reasons": list(self.blocking_reasons),
        }


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MIGRATION_MARKER_SCHEMA_REVISION = 1
MIGRATION_MARKER_STATUSES = frozenset(
    {"prepared", "committed", "rollback-prepared", "rolled-back"}
)
MIGRATION_RECOVERY_ACTIONS = frozenset(
    {
        "prepare",
        "resume-apply",
        "write-committed-marker",
        "complete",
        "begin-rollback",
        "resume-rollback",
        "write-rolled-back-marker",
        "record-first-v053-mutation",
        "fail-closed",
    }
)


def sha256_digest(data: bytes) -> str:
    """Return the canonical digest label used by migration marker records."""

    if not isinstance(data, bytes):
        raise MigrationError("digest input must be bytes")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _schema_record(version: SchemaVersion) -> dict[str, int]:
    return {
        "state_format_version": version.format_version,
        "schema_revision": version.revision,
    }


def _artifact_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MigrationError("migration artifact path must be a non-empty string")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise MigrationError("migration artifact path must be repository-relative")
    normalized = path.as_posix()
    if normalized != value.strip() or "\\" in normalized:
        raise MigrationError("migration artifact path must be normalized POSIX text")
    return normalized


def _digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MigrationError(f"{field_name} must be a sha256:<hex> digest")
    return value


@dataclass(frozen=True, slots=True)
class MigrationArtifactPlan:
    """Source/archive and planned-output identity for one namespaced file."""

    path: str
    archive_digest: str
    output_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _artifact_path(self.path))
        object.__setattr__(
            self,
            "archive_digest",
            _digest(self.archive_digest, "archive_digest"),
        )
        object.__setattr__(
            self,
            "output_digest",
            _digest(self.output_digest, "output_digest"),
        )

    @classmethod
    def from_bytes(
        cls, path: str, archive_bytes: bytes, output_bytes: bytes
    ) -> "MigrationArtifactPlan":
        return cls(
            path=path,
            archive_digest=sha256_digest(archive_bytes),
            output_digest=sha256_digest(output_bytes),
        )

    @property
    def changed(self) -> bool:
        return self.archive_digest != self.output_digest

    def to_record(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "archive_digest": self.archive_digest,
            "output_digest": self.output_digest,
            "changed": self.changed,
        }


def _manifest_digest(
    artifacts: Sequence[MigrationArtifactPlan], digest_field: str
) -> str:
    manifest = [
        {"path": artifact.path, "digest": getattr(artifact, digest_field)}
        for artifact in artifacts
    ]
    return sha256_digest(_canonical_json_bytes(manifest))


@dataclass(frozen=True, slots=True)
class MigrationTransactionPlan:
    """Immutable identity shared by prepare, commit, resume, and rollback."""

    migration_generation: int
    source: SchemaVersion
    target: SchemaVersion
    artifacts: tuple[MigrationArtifactPlan, ...]
    archive_digest: str
    output_digest: str

    def __post_init__(self) -> None:
        generation = self.migration_generation
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise MigrationError("migration_generation must be a positive integer")
        if not isinstance(self.source, SchemaVersion) or not isinstance(
            self.target, SchemaVersion
        ):
            raise MigrationError("migration transaction versions must be SchemaVersion values")
        if self.source >= self.target:
            raise MigrationError("migration transaction target must be newer than source")
        artifacts = tuple(self.artifacts)
        if not artifacts:
            raise MigrationError("migration transaction must contain at least one artifact")
        if any(not isinstance(artifact, MigrationArtifactPlan) for artifact in artifacts):
            raise MigrationError("migration transaction artifacts are invalid")
        sorted_artifacts = tuple(sorted(artifacts, key=lambda artifact: artifact.path))
        paths = tuple(artifact.path for artifact in sorted_artifacts)
        if len(set(paths)) != len(paths):
            raise MigrationError("migration transaction artifact paths must be unique")
        object.__setattr__(self, "artifacts", sorted_artifacts)
        expected_archive = _manifest_digest(sorted_artifacts, "archive_digest")
        expected_output = _manifest_digest(sorted_artifacts, "output_digest")
        if self.archive_digest != expected_archive:
            raise MigrationError("aggregate archive_digest does not match artifact manifest")
        if self.output_digest != expected_output:
            raise MigrationError("aggregate output_digest does not match artifact manifest")

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(artifact.path for artifact in self.artifacts if artifact.changed)

    @property
    def prepared_marker(self) -> dict[str, Any]:
        return self._marker("prepared")

    @property
    def prepared_marker_digest(self) -> str:
        return sha256_digest(_canonical_json_bytes(self.prepared_marker))

    @property
    def committed_marker(self) -> dict[str, Any]:
        marker = self._marker("committed")
        marker["prepared_marker_digest"] = self.prepared_marker_digest
        marker["first_v053_mutation_at"] = ""
        marker["first_v053_mutation_command"] = ""
        return marker

    @property
    def rollback_prepared_marker(self) -> dict[str, Any]:
        marker = self._marker("rollback-prepared")
        marker["prepared_marker_digest"] = self.prepared_marker_digest
        return marker

    @property
    def rolled_back_marker(self) -> dict[str, Any]:
        marker = self._marker("rolled-back")
        marker["prepared_marker_digest"] = self.prepared_marker_digest
        return marker

    def _marker(self, status: str) -> dict[str, Any]:
        if status not in MIGRATION_MARKER_STATUSES:
            raise MigrationError(f"unsupported migration marker status: {status}")
        return {
            "marker_schema_revision": MIGRATION_MARKER_SCHEMA_REVISION,
            "status": status,
            "migration_generation": self.migration_generation,
            "source_schema": _schema_record(self.source),
            "target_schema": _schema_record(self.target),
            "archive_digest": self.archive_digest,
            "output_digest": self.output_digest,
            "artifacts": [artifact.to_record() for artifact in self.artifacts],
        }

    def to_record(self) -> dict[str, Any]:
        return {
            "migration_generation": self.migration_generation,
            "source_schema": _schema_record(self.source),
            "target_schema": _schema_record(self.target),
            "archive_digest": self.archive_digest,
            "output_digest": self.output_digest,
            "changed_paths": list(self.changed_paths),
            "prepared_marker": self.prepared_marker,
            "committed_marker": self.committed_marker,
            "rollback_eligible_after_commit": True,
            "first_v053_mutation_eligible_after_commit": True,
        }


@dataclass(frozen=True, slots=True)
class MigrationRecoveryDecision:
    """Deterministic next action for one observed transaction state."""

    action: str
    rollback_eligible: bool = False
    recovery_rollback_eligible: bool = False
    first_v053_mutation_eligible: bool = False
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.action not in MIGRATION_RECOVERY_ACTIONS:
            raise MigrationError(f"unsupported migration recovery action: {self.action}")

    @property
    def blocked(self) -> bool:
        return self.action == "fail-closed"

    def to_record(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "rollback_eligible": self.rollback_eligible,
            "recovery_rollback_eligible": self.recovery_rollback_eligible,
            "first_v053_mutation_eligible": self.first_v053_mutation_eligible,
            "issues": list(self.issues),
        }


def plan_migration_transaction(
    *,
    migration_generation: int,
    source: SchemaVersion,
    target: SchemaVersion,
    artifacts: Mapping[str, tuple[bytes, bytes]]
    | Sequence[MigrationArtifactPlan],
) -> MigrationTransactionPlan:
    """Build aggregate archive/output digests without reading or writing files."""

    if isinstance(artifacts, Mapping):
        planned: list[MigrationArtifactPlan] = []
        for path, pair in artifacts.items():
            if (
                isinstance(pair, (str, bytes))
                or not isinstance(pair, Sequence)
                or len(pair) != 2
            ):
                raise MigrationError(
                    "artifact byte mapping values must be (archive_bytes, output_bytes)"
                )
            archive_bytes, output_bytes = pair
            if not isinstance(archive_bytes, bytes) or not isinstance(output_bytes, bytes):
                raise MigrationError("artifact archive/output values must be bytes")
            planned.append(
                MigrationArtifactPlan.from_bytes(path, archive_bytes, output_bytes)
            )
    else:
        planned = list(artifacts)
    ordered = tuple(sorted(planned, key=lambda artifact: artifact.path))
    return MigrationTransactionPlan(
        migration_generation=migration_generation,
        source=source,
        target=target,
        artifacts=ordered,
        archive_digest=_manifest_digest(ordered, "archive_digest"),
        output_digest=_manifest_digest(ordered, "output_digest"),
    )


def artifact_digests_from_bytes(artifacts: Mapping[str, bytes]) -> dict[str, str]:
    """Convert caller-observed bytes to the digest input used by recovery."""

    return {
        _artifact_path(path): sha256_digest(content)
        for path, content in artifacts.items()
    }


def _source_fix_records(state: State) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for collection_name in ("reviews", "gaps"):
        collection = state.get(collection_name)
        if not isinstance(collection, Mapping):
            continue
        for source_id, record in sorted(collection.items(), key=lambda item: str(item[0])):
            if isinstance(record, Mapping):
                yield f"{collection_name}/{source_id}", record
    for field_name in ("review_convergence", "manual_final_review"):
        record = state.get(field_name)
        if isinstance(record, Mapping):
            yield field_name, record


def _string_sequence(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return tuple(item.strip() for item in value)


def _positive_round(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def recover_legacy_remediation_history(state: State) -> RemediationHistoryRecovery:
    """Reconstruct consumed remediation batches without guessing downward.

    Explicit batch membership and each source's ``created_fix_tasks`` list form
    connected components.  Positive task rounds and the legacy aggregate
    counters must agree with that grouping.  Any conflicting grouping, missing
    task, or unexplained counter becomes ``migration_decision_required`` and
    reports the observed candidate counts instead of choosing the smaller one.
    """

    if not isinstance(state, Mapping):
        raise MigrationError("state must be a mapping")
    tasks_value = state.get("tasks", {})
    if not isinstance(tasks_value, Mapping):
        return RemediationHistoryRecovery(
            status="migration_decision_required",
            consumed_rounds=None,
            issues=("tasks must be an object before remediation history recovery",),
        )
    tasks: dict[str, Any] = {}
    for task_id, task in tasks_value.items():
        normalized_id = str(task_id)
        if normalized_id in tasks:
            return RemediationHistoryRecovery(
                status="migration_decision_required",
                consumed_rounds=None,
                issues=(
                    f"task IDs collide after string normalization: {normalized_id}",
                ),
            )
        tasks[normalized_id] = task
    issues: list[str] = []
    source_tasks: dict[str, tuple[str, ...]] = {}
    source_targets: dict[str, tuple[str, ...]] = {}
    referenced: set[str] = set()

    for source_ref, record in _source_fix_records(state):
        if "created_fix_tasks" not in record:
            continue
        task_ids = _string_sequence(record.get("created_fix_tasks"))
        if task_ids is None:
            issues.append(f"{source_ref}.created_fix_tasks must be an array of task IDs")
            continue
        if len(set(task_ids)) != len(task_ids):
            issues.append(f"{source_ref}.created_fix_tasks contains duplicates")
        source_tasks[source_ref] = tuple(dict.fromkeys(task_ids))
        referenced.update(task_ids)
        targets = tuple(
            sorted(
                {
                    str(record.get(field_name) or "").strip()
                    for field_name in (
                        "target_sha",
                        "head_sha",
                        "closed_head_sha",
                    )
                    if str(record.get(field_name) or "").strip()
                }
            )
        )
        source_targets[source_ref] = targets
        for task_id in task_ids:
            if task_id not in tasks:
                issues.append(f"{source_ref} references unknown remediation task {task_id}")

    relevant: set[str] = set(referenced & set(tasks))
    for task_id, record in tasks.items():
        if not isinstance(record, Mapping):
            if task_id in referenced:
                issues.append(f"remediation task {task_id} must be an object")
            continue
        if (
            record.get("task_origin") == "finding"
            or _positive_round(record.get("remediation_round")) is not None
            or bool(str(record.get("source_finding") or "").strip())
        ):
            relevant.add(task_id)

    parent = {task_id: task_id for task_id in relevant}

    def find(task_id: str) -> str:
        root = task_id
        while parent[root] != root:
            root = parent[root]
        while parent[task_id] != task_id:
            next_id = parent[task_id]
            parent[task_id] = root
            task_id = next_id
        return root

    def union(task_ids: Iterable[str]) -> None:
        known = sorted({task_id for task_id in task_ids if task_id in parent})
        if not known:
            return
        root = find(known[0])
        for task_id in known[1:]:
            other = find(task_id)
            if other != root:
                parent[other] = root

    for task_ids in source_tasks.values():
        union(task_ids)

    explicit_batches: dict[str, set[str]] = defaultdict(set)
    for task_id in relevant:
        record = tasks.get(task_id)
        if not isinstance(record, Mapping):
            continue
        batch_id = str(record.get("batch_id") or "").strip()
        if batch_id:
            explicit_batches[batch_id].add(task_id)
    batches_value = state.get("batches")
    if isinstance(batches_value, Mapping):
        for batch_id, record in batches_value.items():
            if not isinstance(record, Mapping) or "task_ids" not in record:
                continue
            task_ids = _string_sequence(record.get("task_ids"))
            if task_ids is None:
                issues.append(f"batches/{batch_id}.task_ids must be an array of task IDs")
                continue
            explicit_batches[str(batch_id)].update(set(task_ids) & relevant)
    for task_ids in explicit_batches.values():
        union(task_ids)

    components: dict[str, set[str]] = defaultdict(set)
    for task_id in sorted(relevant):
        components[find(task_id)].add(task_id)

    recovered_batches: list[RemediationBatchRecovery] = []
    unbatched_identity_components: dict[
        tuple[int | None, tuple[str, ...]], list[tuple[str, ...]]
    ] = defaultdict(list)
    for task_ids_set in components.values():
        task_ids = tuple(sorted(task_ids_set))
        batch_ids = {
            str(tasks[task_id].get("batch_id") or "").strip()
            for task_id in task_ids
            if isinstance(tasks.get(task_id), Mapping)
            and str(tasks[task_id].get("batch_id") or "").strip()
        }
        for batch_id, members in explicit_batches.items():
            if members & set(task_ids):
                batch_ids.add(batch_id)
        source_refs = tuple(
            sorted(
                source_ref
                for source_ref, members in source_tasks.items()
                if set(members) & set(task_ids)
            )
        )
        rounds = {
            round_value
            for task_id in task_ids
            if isinstance(tasks.get(task_id), Mapping)
            for round_value in (_positive_round(tasks[task_id].get("remediation_round")),)
            if round_value is not None
        }
        target_shas = tuple(
            sorted(
                {
                    target
                    for source_ref in source_refs
                    for target in source_targets.get(source_ref, ())
                }
            )
        )
        if len(batch_ids) > 1:
            issues.append(
                f"tasks {', '.join(task_ids)} map to multiple legacy batches: "
                + ", ".join(sorted(batch_ids))
            )
        if len(rounds) > 1:
            issues.append(
                f"tasks {', '.join(task_ids)} carry conflicting remediation rounds: "
                + ", ".join(str(value) for value in sorted(rounds))
            )
        if len(target_shas) > 1 and not batch_ids:
            issues.append(
                f"tasks {', '.join(task_ids)} are linked across multiple target SHAs "
                "without an explicit batch"
            )
        batch_id = next(iter(batch_ids)) if len(batch_ids) == 1 else ""
        remediation_round = next(iter(rounds)) if len(rounds) == 1 else None
        identity = {
            "task_ids": task_ids,
            "source_refs": source_refs,
            "batch_id": batch_id,
            "remediation_round": remediation_round,
            "target_shas": target_shas,
        }
        recovery_digest = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
        recovery_key = f"legacy:sha256:{recovery_digest}"
        recovered_batches.append(
            RemediationBatchRecovery(
                recovery_key=recovery_key,
                task_ids=task_ids,
                source_refs=source_refs,
                batch_id=batch_id,
                remediation_round=remediation_round,
                target_shas=target_shas,
            )
        )
        if not batch_id:
            unbatched_identity_components[
                (remediation_round, target_shas)
            ].append(task_ids)

    decision_candidates: set[int] = set()
    if recovered_batches:
        decision_candidates.add(len(recovered_batches))
    for (
        round_value,
        target_shas,
    ), unbatched_components in unbatched_identity_components.items():
        if len(unbatched_components) > 1:
            issues.append(
                "legacy remediation components without explicit batch evidence "
                f"cannot be grouped uniquely for round {round_value!r} and targets "
                f"{target_shas!r}"
            )
            decision_candidates.add(
                len(recovered_batches) - len(unbatched_components) + 1
            )

    counter_values: list[tuple[str, int]] = []
    convergence = state.get("review_convergence")
    if isinstance(convergence, Mapping):
        fix_round = convergence.get("fix_round")
        if isinstance(fix_round, bool) or not isinstance(fix_round, int) or fix_round < 0:
            issues.append("review_convergence.fix_round must be a non-negative integer")
        elif fix_round:
            counter_values.append(("review_convergence.fix_round", fix_round))
    metrics = state.get("execution_metrics")
    if isinstance(metrics, Mapping):
        review_fix_rounds = metrics.get("review_fix_rounds")
        if review_fix_rounds is not None:
            if (
                isinstance(review_fix_rounds, bool)
                or not isinstance(review_fix_rounds, int)
                or review_fix_rounds < 0
            ):
                issues.append(
                    "execution_metrics.review_fix_rounds must be a non-negative integer"
                )
            elif review_fix_rounds:
                counter_values.append(
                    ("execution_metrics.review_fix_rounds", review_fix_rounds)
                )
    distinct_counters = {value for _, value in counter_values}
    decision_candidates.update(distinct_counters)
    if len(distinct_counters) > 1:
        issues.append(
            "legacy remediation counters disagree: "
            + ", ".join(f"{name}={value}" for name, value in counter_values)
        )
    group_count = len(recovered_batches)
    if distinct_counters and not group_count:
        issues.append(
            "positive legacy remediation counter has no reconstructible remediation "
            "provenance from a task, source, or batch"
        )
    if distinct_counters and group_count and distinct_counters != {group_count}:
        issues.append(
            f"recovered remediation batch count {group_count} disagrees with legacy "
            f"counter {next(iter(distinct_counters))}"
        )

    authorization_refs: list[str] = []
    for source_ref, record in _source_fix_records(state):
        if "extra_round_authorization_refs" not in record:
            continue
        refs = _string_sequence(record.get("extra_round_authorization_refs"))
        if refs is None:
            issues.append(
                f"{source_ref}.extra_round_authorization_refs must be an array of strings"
            )
            continue
        authorization_refs.extend(refs)
    authorization_refs = list(dict.fromkeys(authorization_refs))

    recovered_batches.sort(key=lambda batch: batch.recovery_key)
    if not decision_candidates:
        decision_candidates.add(0)
    if issues:
        return RemediationHistoryRecovery(
            status="migration_decision_required",
            consumed_rounds=None,
            batches=tuple(recovered_batches),
            extra_round_authorization_refs=tuple(authorization_refs),
            decision_candidates=tuple(sorted(decision_candidates)),
            issues=tuple(dict.fromkeys(issues)),
        )
    consumed_rounds = (
        next(iter(distinct_counters)) if distinct_counters else group_count
    )
    return RemediationHistoryRecovery(
        status="recovered",
        consumed_rounds=consumed_rounds,
        batches=tuple(recovered_batches),
        extra_round_authorization_refs=tuple(authorization_refs),
        decision_candidates=(consumed_rounds,),
    )


def plan_format_three_revision_three(state: State) -> V053StateMigrationPlan:
    """Plan the 3.2 -> 3.3 state projection without mutating input state."""

    if schema_version_of(state) != V052_STATE_SCHEMA_VERSION:
        raise MigrationError(
            "0.5.3 state migration planning requires format-3.revision-2 input"
        )
    result = deepcopy(dict(state))
    mutation_fields = (
        "first_v053_mutation_at",
        "first_v053_mutation_command",
    )
    if any(field_name in result for field_name in mutation_fields):
        if not all(field_name in result for field_name in mutation_fields):
            raise MigrationError(
                "revision-2 state contains a partial first-v0.5.3 mutation marker"
            )
        mutation_at = result["first_v053_mutation_at"]
        mutation_command = result["first_v053_mutation_command"]
        if not isinstance(mutation_at, str) or not isinstance(
            mutation_command, str
        ):
            raise MigrationError(
                "revision-2 first-v0.5.3 mutation fields must be strings"
            )
        if mutation_at or mutation_command:
            raise MigrationError(
                "revision-2 state contains a partial first-v0.5.3 mutation marker"
            )
    tasks = result.get("tasks", {})
    if not isinstance(tasks, Mapping):
        raise MigrationError("tasks must be an object")
    migrated_tasks: dict[str, Any] = {}
    task_migrations: dict[str, LegacyTaskMigration] = {}
    for task_id, task in sorted(tasks.items(), key=lambda item: str(item[0])):
        if not isinstance(task, Mapping):
            raise MigrationError(f"task {task_id} must be an object")
        normalized_id = str(task_id)
        if normalized_id in migrated_tasks:
            raise MigrationError(
                f"task IDs collide after string normalization: {normalized_id}"
            )
        artifact_id = str(task.get("id") or "").strip()
        if artifact_id and artifact_id != normalized_id:
            raise MigrationError(
                f"task key {normalized_id} disagrees with embedded id {artifact_id}"
            )
        try:
            migration = migrate_legacy_task_contract(task)
        except ContractValidationError as exc:
            raise MigrationError(f"cannot migrate task {task_id}: {exc}") from exc
        migrated_tasks[normalized_id] = deepcopy(dict(migration.record))
        task_migrations[normalized_id] = migration
    result["tasks"] = migrated_tasks

    remediation = recover_legacy_remediation_history(state)
    blocking_reasons = list(remediation.issues) if remediation.decision_required else []
    active_statuses = {
        "starting",
        "running",
        "waiting",
        "waiting-agent",
        "reported",
        "prepared",
    }
    for collection_name in ("reviews", "gaps"):
        collection = state.get(collection_name)
        if not isinstance(collection, Mapping):
            continue
        for role_id, record in sorted(collection.items(), key=lambda item: str(item[0])):
            if not isinstance(record, Mapping):
                continue
            status = str(record.get("status") or record.get("gate_status") or "")
            if status in active_statuses:
                blocking_reasons.append(
                    f"active legacy {collection_name[:-1]} {role_id} must be harvested or aborted before migration"
                )
    for field_name in ("review_convergence", "manual_final_review"):
        record = state.get(field_name)
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or "")
        if status in {"prepared", "running"}:
            blocking_reasons.append(
                f"active legacy {field_name} must be closed or aborted before migration"
            )
    blocking_reasons = list(dict.fromkeys(blocking_reasons))
    result["contract_schema_compatibility"] = {
        "state_schema_revision": V053_STATE_SCHEMA_VERSION.revision,
        "legacy_contract_schema_revision": LEGACY_CONTRACT_SCHEMA_REVISION,
        "current_contract_schema_revision": V053_CONTRACT_SCHEMA_REVISION,
    }
    result["first_v053_mutation_at"] = ""
    result["first_v053_mutation_command"] = ""
    result["migration_v053"] = {
        "status": (
            "migration_decision_required"
            if blocking_reasons
            else "ready-to-commit"
        ),
        "task_contracts": {
            task_id: migration.to_record()
            for task_id, migration in sorted(task_migrations.items())
        },
        "remediation_history": remediation.to_record(),
        "blocking_reasons": list(blocking_reasons),
    }
    return V053StateMigrationPlan(
        state=result,
        task_migrations=task_migrations,
        remediation=remediation,
        blocking_reasons=tuple(blocking_reasons),
    )


def _failed_recovery(*issues: str) -> MigrationRecoveryDecision:
    return MigrationRecoveryDecision(
        action="fail-closed",
        issues=tuple(issue for issue in issues if issue),
    )


def _marker_identity_issues(
    plan: MigrationTransactionPlan, marker: Mapping[str, Any]
) -> list[str]:
    status = marker.get("status")
    issues: list[str] = []
    if marker.get("marker_schema_revision") != MIGRATION_MARKER_SCHEMA_REVISION:
        issues.append("migration marker schema revision mismatch")
    if status not in MIGRATION_MARKER_STATUSES:
        issues.append(f"unsupported migration marker status: {status!r}")
    expected = plan._marker(str(status)) if status in MIGRATION_MARKER_STATUSES else {}
    for field_name in (
        "migration_generation",
        "source_schema",
        "target_schema",
        "archive_digest",
        "output_digest",
        "artifacts",
    ):
        if marker.get(field_name) != expected.get(field_name):
            issues.append(f"migration marker {field_name} does not match the plan")
    if status in {"committed", "rollback-prepared", "rolled-back"} and marker.get(
        "prepared_marker_digest"
    ) != plan.prepared_marker_digest:
        issues.append("migration marker prepared digest does not match the plan")
    mutation_fields = (
        "first_v053_mutation_at",
        "first_v053_mutation_command",
    )
    if status == "committed":
        missing_mutation_fields = tuple(
            field_name
            for field_name in mutation_fields
            if field_name not in marker
        )
        if missing_mutation_fields:
            issues.append(
                "committed migration marker is missing mutation boundary fields: "
                + ", ".join(missing_mutation_fields)
            )
        else:
            marker_at = marker["first_v053_mutation_at"]
            marker_command = marker["first_v053_mutation_command"]
            if not isinstance(marker_at, str) or not isinstance(marker_command, str):
                issues.append(
                    "first-v0.5.3 mutation boundary fields must be strings"
                )
            elif (
                marker_at != marker_at.strip()
                or marker_command != marker_command.strip()
                or bool(marker_at) != bool(marker_command)
            ):
                issues.append(
                    "first-v0.5.3 mutation boundary must be a canonical paired string state"
                )
    elif any(field_name in marker for field_name in mutation_fields):
        issues.append(
            "first-v0.5.3 mutation marker is only valid after migration commit"
        )
    return issues


def decide_migration_recovery(
    plan: MigrationTransactionPlan,
    *,
    marker: Mapping[str, Any] | None,
    archive_digest: str = "",
    current_artifact_digests: Mapping[str, str],
    requested_action: str = "auto",
    requested_command: str = "",
    first_v053_mutation_at: str = "",
    first_v053_mutation_command: str = "",
) -> MigrationRecoveryDecision:
    """Choose one recovery action from caller-observed marker and file digests.

    ``requested_action`` is ``auto``, ``resume``, ``rollback``, or
    ``first-mutation``.  Unknown bytes, marker identity drift, archive mismatch,
    a committed mixed tree, or an inconsistent first-mutation marker always
    return ``fail-closed``.
    """

    if not isinstance(plan, MigrationTransactionPlan):
        raise MigrationError("plan must be a MigrationTransactionPlan")
    if requested_action not in {"auto", "resume", "rollback", "first-mutation"}:
        raise MigrationError(f"unsupported requested migration action: {requested_action}")
    expected_paths = {artifact.path for artifact in plan.artifacts}
    observed_paths = set(current_artifact_digests)
    if observed_paths != expected_paths:
        return _failed_recovery(
            "observed artifact paths do not exactly match the migration plan"
        )
    try:
        observed = {
            _artifact_path(path): _digest(value, f"artifact digest for {path}")
            for path, value in current_artifact_digests.items()
        }
    except MigrationError as exc:
        return _failed_recovery(str(exc))
    source_by_path = {
        artifact.path: artifact.archive_digest for artifact in plan.artifacts
    }
    output_by_path = {
        artifact.path: artifact.output_digest for artifact in plan.artifacts
    }
    # Once the first 0.5.3 material mutation is durably recorded, migration
    # rollback is permanently closed and ordinary runtime writes may change
    # STATE/task bytes beyond the original planned output.  Validate the
    # immutable marker identity and paired mutation evidence before classifying
    # those post-migration bytes as an interrupted mixed tree.
    if isinstance(marker, Mapping) and marker.get("status") == "committed":
        marker_issues = _marker_identity_issues(plan, marker)
        marker_at = marker.get("first_v053_mutation_at", "")
        marker_command = marker.get("first_v053_mutation_command", "")
        if (
            not marker_issues
            and archive_digest == plan.archive_digest
            and isinstance(marker_at, str)
            and isinstance(marker_command, str)
            and marker_at
            and marker_command
        ):
            if first_v053_mutation_at and first_v053_mutation_at != marker_at:
                return _failed_recovery(
                    "first-v0.5.3 mutation timestamp observations disagree"
                )
            if (
                first_v053_mutation_command
                and first_v053_mutation_command != marker_command
            ):
                return _failed_recovery(
                    "first-v0.5.3 mutation command observations disagree"
                )
            if requested_action == "rollback":
                return _failed_recovery(
                    "rollback is forbidden after first-v0.5.3 mutation was recorded"
                )
            return MigrationRecoveryDecision(action="complete")
    unknown_paths = tuple(
        sorted(
            path
            for path, digest in observed.items()
            if digest not in {source_by_path[path], output_by_path[path]}
        )
    )
    if unknown_paths:
        return _failed_recovery(
            "artifact digest is neither archived source nor planned output: "
            + ", ".join(unknown_paths)
        )
    all_source = all(observed[path] == source_by_path[path] for path in expected_paths)
    all_output = all(observed[path] == output_by_path[path] for path in expected_paths)

    if marker is None:
        if not all_source:
            return _failed_recovery(
                "migration bytes changed before a prepared marker was recorded"
            )
        if requested_action in {"rollback", "first-mutation"}:
            return _failed_recovery(
                f"{requested_action} requires a prepared and committed migration"
            )
        return MigrationRecoveryDecision(action="prepare")
    if not isinstance(marker, Mapping):
        return _failed_recovery("migration marker must be an object")
    marker_issues = _marker_identity_issues(plan, marker)
    if marker_issues:
        return _failed_recovery(*marker_issues)
    if archive_digest != plan.archive_digest:
        return _failed_recovery("archive digest does not match the prepared migration")

    marker_at = marker.get("first_v053_mutation_at", "")
    marker_command = marker.get("first_v053_mutation_command", "")
    if not isinstance(first_v053_mutation_at, str) or not isinstance(
        first_v053_mutation_command, str
    ):
        return _failed_recovery(
            "first-v0.5.3 mutation observations must be strings"
        )
    explicit_at = first_v053_mutation_at
    explicit_command = first_v053_mutation_command
    if (
        explicit_at != explicit_at.strip()
        or explicit_command != explicit_command.strip()
        or bool(explicit_at) != bool(explicit_command)
    ):
        return _failed_recovery(
            "first-v0.5.3 mutation observations must be a canonical paired string state"
        )
    if explicit_at and marker_at and explicit_at != marker_at:
        return _failed_recovery(
            "first-v0.5.3 mutation timestamp observations disagree"
        )
    if explicit_command and marker_command and explicit_command != marker_command:
        return _failed_recovery(
            "first-v0.5.3 mutation command observations disagree"
        )
    observed_at = explicit_at or marker_at
    observed_command = explicit_command or marker_command
    if bool(observed_at) != bool(observed_command):
        return _failed_recovery(
            "first-v0.5.3 mutation timestamp and command identity must be recorded together"
        )
    status = str(marker.get("status"))
    if status != "committed" and observed_at:
        return _failed_recovery(
            "first-v0.5.3 mutation marker is only valid after migration commit"
        )
    if status == "prepared":
        if requested_action == "first-mutation":
            return _failed_recovery(
                "first-v0.5.3 mutation cannot start before committed marker"
            )
        if requested_action == "rollback":
            return MigrationRecoveryDecision(
                action="begin-rollback",
                recovery_rollback_eligible=True,
            )
        if all_output:
            return MigrationRecoveryDecision(
                action="write-committed-marker",
                recovery_rollback_eligible=True,
            )
        return MigrationRecoveryDecision(
            action="resume-apply",
            recovery_rollback_eligible=True,
        )
    if status == "rollback-prepared":
        if requested_action == "first-mutation":
            return _failed_recovery("rollback is already in progress")
        if all_source:
            return MigrationRecoveryDecision(action="write-rolled-back-marker")
        return MigrationRecoveryDecision(
            action="resume-rollback",
            recovery_rollback_eligible=True,
        )
    if status == "rolled-back":
        if not all_source:
            return _failed_recovery(
                "rolled-back marker requires every artifact to match the archive"
            )
        if requested_action == "first-mutation":
            return _failed_recovery("rolled-back migration cannot record a mutation")
        return MigrationRecoveryDecision(action="complete")
    if status != "committed":
        return _failed_recovery(f"unhandled migration marker status: {status}")
    if not all_output:
        return _failed_recovery(
            "committed marker requires every artifact to match planned output"
        )
    rollback_eligible = not observed_at
    first_mutation_eligible = not observed_at
    if requested_action == "rollback":
        if not rollback_eligible:
            return _failed_recovery(
                "rollback is forbidden after first-v0.5.3 mutation was recorded"
            )
        return MigrationRecoveryDecision(
            action="begin-rollback",
            rollback_eligible=True,
            first_v053_mutation_eligible=True,
        )
    if requested_action == "first-mutation":
        if not requested_command.strip():
            return _failed_recovery(
                "first-v0.5.3 mutation requires a non-empty command identity"
            )
        if first_mutation_eligible:
            return MigrationRecoveryDecision(
                action="record-first-v053-mutation",
                rollback_eligible=True,
                first_v053_mutation_eligible=True,
            )
        return MigrationRecoveryDecision(action="complete")
    return MigrationRecoveryDecision(
        action="complete",
        rollback_eligible=rollback_eligible,
        first_v053_mutation_eligible=first_mutation_eligible,
    )


def _integer_marker(state: State, key: str, default: int) -> int:
    value = state.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MigrationError(f"{key} must be an integer")
    return value


def schema_version_of(state: State) -> SchemaVersion:
    """Read a schema version without modifying ``state``.

    Pre-revision states are revision zero.  This keeps format-2 fixtures and
    early format-3 development snapshots representable by the same chain.
    """

    if not isinstance(state, Mapping):
        raise MigrationError("state must be a mapping")
    format_version = _integer_marker(state, "state_format_version", 1)
    revision = _integer_marker(state, "schema_revision", 0)
    return SchemaVersion(format_version, revision)


def _step_index(steps: Sequence[MigrationStep]) -> dict[SchemaVersion, MigrationStep]:
    index: dict[SchemaVersion, MigrationStep] = {}
    names: set[str] = set()
    for step in steps:
        if step.source in index:
            raise MigrationError(f"duplicate migration source: {step.source.label()}")
        if step.name in names:
            raise MigrationError(f"duplicate migration step name: {step.name}")
        index[step.source] = step
        names.add(step.name)
    return index


def plan_schema_migration(
    state: State,
    *,
    target: SchemaVersion,
    steps: Sequence[MigrationStep],
) -> MigrationPlan:
    """Return the unique contiguous path from ``state`` to ``target``.

    Future formats/revisions are rejected rather than silently downgraded.
    Missing or overshooting edges are also rejected, so a caller cannot skip an
    intermediate development revision by accident.
    """

    source = schema_version_of(state)
    if source > target:
        raise FutureSchemaError(
            f"state {source.label()} is newer than runtime {target.label()}"
        )
    index = _step_index(steps)
    current = source
    path: list[MigrationStep] = []
    visited: set[SchemaVersion] = set()
    while current != target:
        if current in visited:
            raise MissingMigrationError(f"migration cycle at {current.label()}")
        visited.add(current)
        step = index.get(current)
        if step is None:
            raise MissingMigrationError(
                f"missing migration from {current.label()} to {target.label()}"
            )
        if step.target > target:
            raise MissingMigrationError(
                f"migration {step.name} overshoots target {target.label()}"
            )
        path.append(step)
        current = step.target
    return MigrationPlan(source=source, target=target, steps=tuple(path))


def migrate_schema(
    state: State,
    *,
    target: SchemaVersion,
    steps: Sequence[MigrationStep],
) -> MigrationResult:
    """Apply a validated revision chain to a deep copy of ``state``.

    The input remains byte-for-byte serializable to its original value even if
    a transform mutates its argument before returning it.  A transform may not
    forge schema markers; those markers are written after each successful step.
    """

    plan = plan_schema_migration(state, target=target, steps=steps)
    working: dict[str, Any] = deepcopy(dict(state))
    for step in plan.steps:
        candidate_input = deepcopy(working)
        candidate = step.transform(candidate_input)
        if not isinstance(candidate, Mapping):
            raise MigrationError(f"migration {step.name} did not return a mapping")
        migrated = deepcopy(dict(candidate))
        declared = schema_version_of(migrated)
        if declared not in {step.source, step.target}:
            raise MigrationError(
                f"migration {step.name} forged schema marker {declared.label()}"
            )
        migrated["state_format_version"] = step.target.format_version
        migrated["schema_revision"] = step.target.revision
        working = migrated
    return MigrationResult(state=working, plan=plan)


def migrate_format_three_revision_two(state: State) -> dict[str, Any]:
    """Add the 0.5.2 state policy while preserving legacy loop behavior.

    A format-3 revision-1 loop predates release-scope locking, convergence
    review, and manual final certification.  It must retain its existing
    ``review_after_merges`` value and must not acquire a new finish gate merely
    because its state is migrated.  The transform therefore records the new
    state blocks with legacy-safe statuses and marks every existing task as
    ``legacy-unclassified``.  The migration engine, rather than this transform,
    owns the schema revision markers.
    """
    if not isinstance(state, Mapping):
        raise MigrationError("state must be a mapping")

    result = deepcopy(dict(state))

    # Existing state keeps its top-level review_after_merges value untouched;
    # only the new policy snapshot is added.  The final requirement remains the
    # current policy for new runs, while the legacy status below disables that
    # gate for this migrated run.
    result["review_policy"] = deepcopy(LEGACY_REVIEW_POLICY)
    result["release_scope"] = {
        "status": "legacy-unlocked",
        "locked_at": "",
        "source_refs": [],
        "source_digests": {},
        "scope_revision": 0,
        "source_snapshot_revision": 0,
        "last_user_input_id": "",
        "amendment_refs": [],
    }
    result["dispatch_freeze"] = {
        "status": "inactive",
        "reason": "",
        "frozen_at": "",
        "source_input_id": "",
        "allowed_running_role_ids": [],
    }
    result["review_convergence"] = {
        "status": "not-started",
        "target_sha": "",
        "fix_round": 0,
        "authorized_extra_rounds": 0,
        "extra_round_authorization_refs": [],
        "verified_actionable_findings": None,
        "artifact_refs": [],
    }
    result["manual_final_review"] = {
        "status": "not-required-for-legacy-run",
        "certification_id": "",
        "target_sha": "",
        "prepared_plan": "",
        "prepared_plan_digest": "",
        "manifest": "",
        "report": "",
        "manifest_complete": None,
        "verified_actionable_findings": None,
        "attempt_history": [],
    }
    result["follow_ups"] = {
        "next_id": 1,
        "open_count": 0,
        "artifact_refs": [],
        "issue_keys": {},
        "issue_key_aliases": {},
    }

    manager_invocation = result.get("manager_invocation")
    if not isinstance(manager_invocation, Mapping):
        manager_invocation = {}
    result["manager_invocation"] = {
        "provider": str(manager_invocation.get("provider") or ""),
        "model": str(manager_invocation.get("model") or ""),
        "reasoning_effort": str(manager_invocation.get("reasoning_effort") or ""),
        "recorded_at": str(manager_invocation.get("recorded_at") or ""),
    }

    execution_metrics = result.get("execution_metrics")
    if not isinstance(execution_metrics, Mapping):
        execution_metrics = {}
    metric_defaults = {
        "planned_task_count": 0,
        "remediation_task_count": 0,
        "task_origin_counts": {},
        "review_fix_rounds": 0,
        "finding_disposition_counts": {},
        "stale_review_count": 0,
        "aborted_review_count": 0,
        "stale_gap_count": 0,
        "aborted_gap_count": 0,
        "scope_expansion_started_at": "",
        "effective_parallelism": None,
    }
    result["execution_metrics"] = {
        key: deepcopy(execution_metrics.get(key, default))
        for key, default in metric_defaults.items()
    }

    legacy_task_defaults = {
        "release_scope_revision": 0,
        "plan_item_refs": [],
        "requirement_refs": [],
        "scope_refs": [],
        "source_finding": "",
        "authorization_input_id": "",
        "why_fix_now": "",
        "operational_reason": "",
        "origin": "",
        "contract_relation": "",
        "release_effect": "",
        "remediation_round": 0,
        "fact_status": "",
        "disposition": "",
    }
    tasks = result.get("tasks")
    if isinstance(tasks, Mapping):
        migrated_tasks: dict[str, Any] = {}
        for task_id, task in tasks.items():
            if not isinstance(task, Mapping):
                migrated_tasks[str(task_id)] = deepcopy(task)
                continue
            migrated_task = deepcopy(dict(task))
            migrated_task["task_origin"] = "legacy-unclassified"
            for key, default in legacy_task_defaults.items():
                migrated_task.setdefault(key, deepcopy(default))
            migrated_tasks[str(task_id)] = migrated_task
        result["tasks"] = migrated_tasks
    else:
        result["tasks"] = {}

    return result


def migrate_format_three_revision_three(state: State) -> dict[str, Any]:
    """Apply the decision-free portion of the 0.5.3 state projection.

    Ambiguous remediation history raises :class:`MigrationDecisionRequired`;
    callers use :func:`plan_format_three_revision_three` to show candidates and
    stopping reasons during dry-run.  The migration engine writes the schema
    markers only after this transform returns successfully.
    """

    plan = plan_format_three_revision_three(state)
    plan.require_applicable()
    return deepcopy(dict(plan.state))


FORMAT_3_REVISION_2_MIGRATION = MigrationStep(
    SchemaVersion(3, 1),
    V052_STATE_SCHEMA_VERSION,
    "format-3-revision-2",
    migrate_format_three_revision_two,
)
FORMAT_3_REVISION_3_MIGRATION = MigrationStep(
    V052_STATE_SCHEMA_VERSION,
    V053_STATE_SCHEMA_VERSION,
    "format-3-revision-3",
    migrate_format_three_revision_three,
)
FORMAT_THREE_MIGRATION_STEPS = (
    FORMAT_3_REVISION_2_MIGRATION,
    FORMAT_3_REVISION_3_MIGRATION,
)
