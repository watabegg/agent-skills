"""Immutable review-epoch plans and fail-closed audit-capacity leases.

The command-line runtime owns clocks, provider processes, credentials, and
STATE transactions.  This module keeps the corresponding 0.5.3 contracts
pure: an epoch revision has a canonical immutable identity, a supplemental
pass creates a successor revision, and capacity is released only after a
process-exit confirmation or a forced-abort acknowledgement.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any, Mapping, Sequence

from .review import SUPPORTED_PROVIDERS


DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
AUDIT_PROCESS_KINDS = frozenset(
    {"coordinator", "discovery", "verifier", "challenge"}
)
EPOCH_SOURCE_KINDS = frozenset(
    {"reviewer", "gap", "convergence", "pre_final", "manual_final"}
)
LEASE_STATUSES = frozenset(
    {"starting", "running", "expired_quarantined", "terminal"}
)

_DIGEST_RE = re.compile(DIGEST_PATTERN)
_EPOCH_ID_RE = re.compile(r"^E[0-9]{3,}$")


class ReviewEpochError(ValueError):
    """Raised when an epoch identity or capacity transition is invalid."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewEpochError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str:
    if value in (None, ""):
        return ""
    return _required_text(value, field_name)


def _items(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReviewEpochError(f"{field_name} must be an array")
    return tuple(value)


def _text_tuple(
    value: Sequence[Any], field_name: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    normalized = tuple(
        _required_text(item, field_name) for item in _items(value, field_name)
    )
    if not allow_empty and not normalized:
        raise ReviewEpochError(f"{field_name} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ReviewEpochError(f"{field_name} must not contain duplicates")
    return normalized


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewEpochError(f"{field_name} must be an object")
    return value


def _required_fields(
    record: Mapping[str, Any], field_name: str, fields: Sequence[str]
) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise ReviewEpochError(
            f"{field_name} is missing required fields: {', '.join(missing)}"
        )


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewEpochError(f"{field_name} must be a non-negative integer")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    result = _non_negative_int(value, field_name)
    if result < 1:
        raise ReviewEpochError(f"{field_name} must be a positive integer")
    return result


def _digest(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _DIGEST_RE.fullmatch(text):
        raise ReviewEpochError(
            f"{field_name} must be a lowercase labelled SHA-256 digest"
        )
    return text


def canonical_json(value: Any) -> str:
    """Serialize identity input deterministically and reject non-JSON values."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReviewEpochError(
            f"value is not canonically JSON serializable: {exc}"
        ) from exc


def canonical_digest(value: Any) -> str:
    """Return a stable labelled digest for canonical JSON identity data."""

    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    text = _required_text(value, field_name)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        instant = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ReviewEpochError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ReviewEpochError(f"{field_name} must include a UTC offset")
    return instant.astimezone(timezone.utc)


def _timestamp(value: Any, field_name: str) -> str:
    return _parse_timestamp(value, field_name).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AuditProcessPlan:
    """One planned process that consumes one aggregate audit-budget slot."""

    process_id: str
    process_kind: str
    agent_label: str
    provider: str
    model: str
    effort: str
    parent_process_id: str = ""
    lane_id: str = ""

    def __post_init__(self) -> None:
        for field_name in ("process_id", "agent_label", "model", "effort"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if self.process_kind not in AUDIT_PROCESS_KINDS:
            raise ReviewEpochError(
                f"unsupported audit process kind: {self.process_kind!r}"
            )
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ReviewEpochError(f"unsupported audit provider: {self.provider!r}")
        object.__setattr__(
            self,
            "parent_process_id",
            _optional_text(self.parent_process_id, "parent_process_id"),
        )
        object.__setattr__(
            self, "lane_id", _optional_text(self.lane_id, "lane_id")
        )

        if self.process_kind == "coordinator":
            if self.parent_process_id:
                raise ReviewEpochError("coordinator processes cannot have a parent")
            if self.lane_id:
                raise ReviewEpochError("coordinator processes cannot have a lane_id")
        else:
            if not self.parent_process_id:
                raise ReviewEpochError(
                    f"{self.process_kind} processes require parent_process_id"
                )
            if self.process_kind in {"discovery", "challenge"} and not self.lane_id:
                raise ReviewEpochError(
                    f"{self.process_kind} processes require lane_id"
                )

    def to_record(self) -> dict[str, str]:
        return {
            "process_id": self.process_id,
            "process_kind": self.process_kind,
            "agent_label": self.agent_label,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "parent_process_id": self.parent_process_id,
            "lane_id": self.lane_id,
        }

    @classmethod
    def from_record(cls, value: Any) -> "AuditProcessPlan":
        if isinstance(value, cls):
            return value
        record = _record(value, "audit process plan")
        _required_fields(
            record,
            "audit process plan",
            (
                "process_id",
                "process_kind",
                "agent_label",
                "provider",
                "model",
                "effort",
            ),
        )
        return cls(
            process_id=record["process_id"],
            process_kind=record["process_kind"],
            agent_label=record["agent_label"],
            provider=record["provider"],
            model=record["model"],
            effort=record["effort"],
            parent_process_id=record.get("parent_process_id", ""),
            lane_id=record.get("lane_id", ""),
        )


@dataclass(frozen=True, slots=True)
class EpochExecutionPlan:
    """A required Reviewer, Gap, convergence, or manual-final execution."""

    execution_id: str
    attempt_id: str
    source_kind: str
    protocol: str
    independence_key: str
    artifact_ref: str
    processes: tuple[AuditProcessPlan, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "execution_id",
            "attempt_id",
            "protocol",
            "independence_key",
            "artifact_ref",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if self.source_kind not in EPOCH_SOURCE_KINDS:
            raise ReviewEpochError(
                f"unsupported epoch source_kind: {self.source_kind!r}"
            )

        processes = tuple(AuditProcessPlan.from_record(item) for item in self.processes)
        if not processes:
            raise ReviewEpochError("epoch executions require at least one process")
        process_ids = [process.process_id for process in processes]
        if len(set(process_ids)) != len(process_ids):
            raise ReviewEpochError("process_id must be unique within an execution")
        labels = [process.agent_label for process in processes]
        if len(set(labels)) != len(labels):
            raise ReviewEpochError("agent_label must be unique within an execution")

        coordinators = [
            process for process in processes if process.process_kind == "coordinator"
        ]
        if len(coordinators) != 1:
            raise ReviewEpochError("each execution requires exactly one coordinator")
        coordinator_id = coordinators[0].process_id
        for process in processes:
            if (
                process.process_kind != "coordinator"
                and process.parent_process_id != coordinator_id
            ):
                raise ReviewEpochError(
                    "audit lane, verifier, and challenge processes must be direct "
                    "children of the execution coordinator"
                )
        object.__setattr__(self, "processes", processes)

    def process(self, process_id: str) -> AuditProcessPlan:
        for process in self.processes:
            if process.process_id == process_id:
                return process
        raise ReviewEpochError(
            f"process is not planned for execution {self.execution_id}: {process_id}"
        )

    def identity_record(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "source_kind": self.source_kind,
            "protocol": self.protocol,
            "independence_key": self.independence_key,
            "artifact_ref": self.artifact_ref,
            "processes": [process.to_record() for process in self.processes],
        }

    @property
    def execution_digest(self) -> str:
        return canonical_digest(self.identity_record())

    def to_record(self) -> dict[str, Any]:
        return {**self.identity_record(), "execution_digest": self.execution_digest}

    @classmethod
    def from_record(cls, value: Any) -> "EpochExecutionPlan":
        if isinstance(value, cls):
            return value
        record = _record(value, "epoch execution plan")
        _required_fields(
            record,
            "epoch execution plan",
            (
                "execution_id",
                "attempt_id",
                "source_kind",
                "protocol",
                "independence_key",
                "artifact_ref",
                "processes",
                "execution_digest",
            ),
        )
        execution = cls(
            execution_id=record["execution_id"],
            attempt_id=record["attempt_id"],
            source_kind=record["source_kind"],
            protocol=record["protocol"],
            independence_key=record["independence_key"],
            artifact_ref=record["artifact_ref"],
            processes=tuple(
                AuditProcessPlan.from_record(item)
                for item in _items(record["processes"], "execution processes")
            ),
        )
        if not hmac.compare_digest(
            _digest(record["execution_digest"], "execution_digest"),
            execution.execution_digest,
        ):
            raise ReviewEpochError(
                "execution_digest does not match canonical execution identity"
            )
        return execution


@dataclass(frozen=True, slots=True)
class InheritedArtifact:
    """An explicitly digest-bound artifact inherited by a successor revision."""

    execution_id: str
    execution_digest: str
    artifact_ref: str
    artifact_digest: str
    source_plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "execution_id", _required_text(self.execution_id, "execution_id")
        )
        object.__setattr__(
            self,
            "execution_digest",
            _digest(self.execution_digest, "execution_digest"),
        )
        object.__setattr__(
            self, "artifact_ref", _required_text(self.artifact_ref, "artifact_ref")
        )
        object.__setattr__(
            self,
            "artifact_digest",
            _digest(self.artifact_digest, "artifact_digest"),
        )
        object.__setattr__(
            self,
            "source_plan_digest",
            _digest(self.source_plan_digest, "source_plan_digest"),
        )

    def to_record(self) -> dict[str, str]:
        return {
            "execution_id": self.execution_id,
            "execution_digest": self.execution_digest,
            "artifact_ref": self.artifact_ref,
            "artifact_digest": self.artifact_digest,
            "source_plan_digest": self.source_plan_digest,
        }

    @classmethod
    def from_record(cls, value: Any) -> "InheritedArtifact":
        if isinstance(value, cls):
            return value
        record = _record(value, "inherited artifact")
        _required_fields(
            record,
            "inherited artifact",
            (
                "execution_id",
                "execution_digest",
                "artifact_ref",
                "artifact_digest",
                "source_plan_digest",
            ),
        )
        return cls(**{field: record[field] for field in (
            "execution_id",
            "execution_digest",
            "artifact_ref",
            "artifact_digest",
            "source_plan_digest",
        )})


@dataclass(frozen=True, slots=True)
class EpochArtifactIdentity:
    """The immutable epoch identity every execution artifact must carry."""

    epoch_id: str
    epoch_revision: int
    execution_id: str
    attempt_id: str
    plan_digest: str
    execution_digest: str
    source_kind: str
    protocol: str
    independence_key: str
    artifact_ref: str

    def to_record(self) -> dict[str, Any]:
        return {
            "epoch_id": self.epoch_id,
            "epoch_revision": self.epoch_revision,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "plan_digest": self.plan_digest,
            "execution_digest": self.execution_digest,
            "source_kind": self.source_kind,
            "protocol": self.protocol,
            "independence_key": self.independence_key,
            "artifact_ref": self.artifact_ref,
        }


@dataclass(frozen=True, slots=True)
class ReviewEpochPlan:
    """One immutable epoch revision pinned to a single target SHA."""

    epoch_id: str
    epoch_revision: int
    base_sha: str
    target_sha: str
    scope_revision: int
    source_snapshot_revision: int
    scope_digest: str
    source_refs: tuple[str, ...]
    policy_digest: str
    validation_identity: str
    audit_agent_budget: int
    required_executions: tuple[EpochExecutionPlan, ...]
    parent_plan_digest: str = ""
    additional_execution_ids: tuple[str, ...] = ()
    inherited_artifacts: tuple[InheritedArtifact, ...] = ()

    def __post_init__(self) -> None:
        epoch_id = _required_text(self.epoch_id, "epoch_id")
        if not _EPOCH_ID_RE.fullmatch(epoch_id):
            raise ReviewEpochError("epoch_id must match E followed by at least 3 digits")
        object.__setattr__(self, "epoch_id", epoch_id)
        revision = _positive_int(self.epoch_revision, "epoch_revision")
        object.__setattr__(self, "epoch_revision", revision)
        for field_name in ("base_sha", "target_sha"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "scope_revision",
            _non_negative_int(self.scope_revision, "scope_revision"),
        )
        object.__setattr__(
            self,
            "source_snapshot_revision",
            _non_negative_int(
                self.source_snapshot_revision, "source_snapshot_revision"
            ),
        )
        for field_name in ("scope_digest", "policy_digest", "validation_identity"):
            object.__setattr__(
                self, field_name, _digest(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "source_refs",
            _text_tuple(self.source_refs, "source_refs"),
        )
        object.__setattr__(
            self,
            "audit_agent_budget",
            _positive_int(self.audit_agent_budget, "audit_agent_budget"),
        )

        executions = tuple(
            EpochExecutionPlan.from_record(item) for item in self.required_executions
        )
        if not executions:
            raise ReviewEpochError("required_executions must not be empty")
        for field_name, values in (
            ("execution_id", [item.execution_id for item in executions]),
            ("independence_key", [item.independence_key for item in executions]),
            ("artifact_ref", [item.artifact_ref for item in executions]),
        ):
            if len(set(values)) != len(values):
                raise ReviewEpochError(
                    f"required executions contain duplicate {field_name}"
                )
        process_ids = [
            process.process_id for execution in executions for process in execution.processes
        ]
        if len(set(process_ids)) != len(process_ids):
            raise ReviewEpochError("process_id must be unique across an epoch revision")
        object.__setattr__(self, "required_executions", executions)

        parent = _optional_text(self.parent_plan_digest, "parent_plan_digest")
        if parent:
            parent = _digest(parent, "parent_plan_digest")
        object.__setattr__(self, "parent_plan_digest", parent)
        additional = _text_tuple(
            self.additional_execution_ids,
            "additional_execution_ids",
            allow_empty=True,
        )
        execution_ids = {execution.execution_id for execution in executions}
        if not set(additional).issubset(execution_ids):
            raise ReviewEpochError(
                "additional_execution_ids must name required executions"
            )
        object.__setattr__(self, "additional_execution_ids", additional)

        inherited = tuple(
            InheritedArtifact.from_record(item) for item in self.inherited_artifacts
        )
        inherited_keys = [(item.execution_id, item.artifact_ref) for item in inherited]
        if len(set(inherited_keys)) != len(inherited_keys):
            raise ReviewEpochError("inherited artifacts must not contain duplicates")
        object.__setattr__(self, "inherited_artifacts", inherited)

        if revision == 1:
            if parent or additional or inherited:
                raise ReviewEpochError(
                    "initial epoch revision cannot have parent or successor fields"
                )
        elif not parent or not additional:
            raise ReviewEpochError(
                "successor epoch revisions require parent_plan_digest and "
                "additional_execution_ids"
            )

    def execution(self, execution_id: str) -> EpochExecutionPlan:
        for execution in self.required_executions:
            if execution.execution_id == execution_id:
                return execution
        raise ReviewEpochError(
            f"execution is not required by epoch revision: {execution_id}"
        )

    @property
    def topology_digest(self) -> str:
        return canonical_digest(
            [
                {
                    "execution_id": execution.execution_id,
                    "source_kind": execution.source_kind,
                    "processes": [
                        process.to_record() for process in execution.processes
                    ],
                }
                for execution in self.required_executions
            ]
        )

    def identity_record(self) -> dict[str, Any]:
        return {
            "record_type": "review_epoch_plan",
            "epoch_id": self.epoch_id,
            "epoch_revision": self.epoch_revision,
            "base_sha": self.base_sha,
            "target_sha": self.target_sha,
            "scope_revision": self.scope_revision,
            "source_snapshot_revision": self.source_snapshot_revision,
            "scope_digest": self.scope_digest,
            "source_refs": list(self.source_refs),
            "policy_digest": self.policy_digest,
            "topology_digest": self.topology_digest,
            "validation_identity": self.validation_identity,
            "audit_agent_budget": self.audit_agent_budget,
            "required_executions": [
                execution.to_record() for execution in self.required_executions
            ],
            "parent_plan_digest": self.parent_plan_digest,
            "additional_execution_ids": list(self.additional_execution_ids),
            "inherited_artifacts": [
                artifact.to_record() for artifact in self.inherited_artifacts
            ],
        }

    @property
    def plan_digest(self) -> str:
        return canonical_digest(self.identity_record())

    @property
    def digest(self) -> str:
        return self.plan_digest

    def to_record(self) -> dict[str, Any]:
        return {**self.identity_record(), "plan_digest": self.plan_digest}

    def artifact_identity(self, execution_id: str) -> EpochArtifactIdentity:
        execution = self.execution(execution_id)
        return EpochArtifactIdentity(
            epoch_id=self.epoch_id,
            epoch_revision=self.epoch_revision,
            execution_id=execution.execution_id,
            attempt_id=execution.attempt_id,
            plan_digest=self.plan_digest,
            execution_digest=execution.execution_digest,
            source_kind=execution.source_kind,
            protocol=execution.protocol,
            independence_key=execution.independence_key,
            artifact_ref=execution.artifact_ref,
        )

    def capacity_ledger(
        self,
        predecessor: "EpochCapacityLedger | Mapping[str, Any] | None" = None,
    ) -> "EpochCapacityLedger":
        """Create the epoch-wide capacity ledger for this revision.

        A successor must advance the predecessor ledger rather than creating an
        independent empty ledger.  This preserves every capacity-holding lease
        in the revision lineage, including quarantined leases that block starts.
        """

        if self.epoch_revision == 1:
            if predecessor is not None:
                raise ReviewEpochError(
                    "initial epoch capacity ledger cannot have a predecessor"
                )
            return EpochCapacityLedger(
                epoch_id=self.epoch_id,
                epoch_revision=self.epoch_revision,
                plan_digest=self.plan_digest,
                audit_agent_budget=self.audit_agent_budget,
            )

        if predecessor is None:
            raise ReviewEpochError(
                "successor epoch capacity ledger requires predecessor ledger"
            )
        previous = EpochCapacityLedger.from_record(predecessor)
        if (
            previous.epoch_id != self.epoch_id
            or previous.epoch_revision != self.epoch_revision - 1
            or not hmac.compare_digest(
                previous.plan_digest, self.parent_plan_digest
            )
            or previous.audit_agent_budget != self.audit_agent_budget
        ):
            raise ReviewEpochError(
                "predecessor capacity ledger does not match successor lineage"
            )
        ledger = EpochCapacityLedger(
            epoch_id=self.epoch_id,
            epoch_revision=self.epoch_revision,
            plan_digest=self.plan_digest,
            ancestor_plan_digests=(
                *previous.ancestor_plan_digests,
                previous.plan_digest,
            ),
            audit_agent_budget=self.audit_agent_budget,
            leases=previous.leases,
        )
        ledger.validate_for_plan(self)
        return ledger

    @classmethod
    def from_record(cls, value: Any) -> "ReviewEpochPlan":
        if isinstance(value, cls):
            return value
        record = _record(value, "review epoch plan")
        if record.get("record_type", "review_epoch_plan") != "review_epoch_plan":
            raise ReviewEpochError("review epoch plan record_type is invalid")
        _required_fields(
            record,
            "review epoch plan",
            (
                "epoch_id",
                "epoch_revision",
                "base_sha",
                "target_sha",
                "scope_revision",
                "source_snapshot_revision",
                "scope_digest",
                "source_refs",
                "policy_digest",
                "topology_digest",
                "validation_identity",
                "audit_agent_budget",
                "required_executions",
                "plan_digest",
            ),
        )
        plan = cls(
            epoch_id=record["epoch_id"],
            epoch_revision=record["epoch_revision"],
            base_sha=record["base_sha"],
            target_sha=record["target_sha"],
            scope_revision=record["scope_revision"],
            source_snapshot_revision=record["source_snapshot_revision"],
            scope_digest=record["scope_digest"],
            source_refs=tuple(_items(record["source_refs"], "source_refs")),
            policy_digest=record["policy_digest"],
            validation_identity=record["validation_identity"],
            audit_agent_budget=record["audit_agent_budget"],
            required_executions=tuple(
                EpochExecutionPlan.from_record(item)
                for item in _items(
                    record["required_executions"], "required_executions"
                )
            ),
            parent_plan_digest=record.get("parent_plan_digest", ""),
            additional_execution_ids=tuple(
                _items(
                    record.get("additional_execution_ids", ()),
                    "additional_execution_ids",
                )
            ),
            inherited_artifacts=tuple(
                InheritedArtifact.from_record(item)
                for item in _items(
                    record.get("inherited_artifacts", ()), "inherited_artifacts"
                )
            ),
        )
        if not hmac.compare_digest(
            _digest(record["topology_digest"], "topology_digest"),
            plan.topology_digest,
        ):
            raise ReviewEpochError(
                "topology_digest does not match required execution topology"
            )
        if not hmac.compare_digest(
            _digest(record["plan_digest"], "plan_digest"), plan.plan_digest
        ):
            raise ReviewEpochError(
                "plan_digest does not match canonical epoch identity"
            )
        return plan


def inherit_artifact(
    parent: ReviewEpochPlan | Mapping[str, Any],
    execution_id: str,
    *,
    artifact_digest: str,
) -> InheritedArtifact:
    """Bind one parent artifact to the exact parent execution identity."""

    parent_plan = ReviewEpochPlan.from_record(parent)
    execution = parent_plan.execution(execution_id)
    return InheritedArtifact(
        execution_id=execution.execution_id,
        execution_digest=execution.execution_digest,
        artifact_ref=execution.artifact_ref,
        artifact_digest=artifact_digest,
        source_plan_digest=parent_plan.plan_digest,
    )


def validate_successor_revision(
    parent: ReviewEpochPlan | Mapping[str, Any],
    successor: ReviewEpochPlan | Mapping[str, Any],
) -> None:
    """Fail closed unless ``successor`` is an append-only same-SHA revision."""

    parent_plan = ReviewEpochPlan.from_record(parent)
    successor_plan = ReviewEpochPlan.from_record(successor)
    if successor_plan.epoch_id != parent_plan.epoch_id:
        raise ReviewEpochError("successor must keep the parent epoch_id")
    if successor_plan.epoch_revision != parent_plan.epoch_revision + 1:
        raise ReviewEpochError("successor epoch_revision must increase by exactly one")
    if not hmac.compare_digest(
        successor_plan.parent_plan_digest, parent_plan.plan_digest
    ):
        raise ReviewEpochError("successor parent_plan_digest does not match parent")

    frozen_fields = (
        "base_sha",
        "target_sha",
        "scope_revision",
        "source_snapshot_revision",
        "scope_digest",
        "source_refs",
        "policy_digest",
        "validation_identity",
        "audit_agent_budget",
    )
    for field_name in frozen_fields:
        if getattr(successor_plan, field_name) != getattr(parent_plan, field_name):
            raise ReviewEpochError(
                f"successor changed frozen parent field: {field_name}"
            )

    parent_executions = parent_plan.required_executions
    successor_prefix = successor_plan.required_executions[: len(parent_executions)]
    if successor_prefix != parent_executions:
        raise ReviewEpochError(
            "successor must preserve every parent execution in original order"
        )
    added = successor_plan.required_executions[len(parent_executions) :]
    if tuple(item.execution_id for item in added) != (
        successor_plan.additional_execution_ids
    ):
        raise ReviewEpochError(
            "additional_execution_ids must exactly identify appended executions"
        )

    parent_by_id = {item.execution_id: item for item in parent_executions}
    for artifact in successor_plan.inherited_artifacts:
        execution = parent_by_id.get(artifact.execution_id)
        if execution is None:
            raise ReviewEpochError(
                "inherited artifact must reference a parent required execution"
            )
        if not hmac.compare_digest(
            artifact.source_plan_digest, parent_plan.plan_digest
        ):
            raise ReviewEpochError(
                "inherited artifact source_plan_digest does not match parent"
            )
        if not hmac.compare_digest(
            artifact.execution_digest, execution.execution_digest
        ):
            raise ReviewEpochError(
                "inherited artifact execution identity does not match parent"
            )
        if artifact.artifact_ref != execution.artifact_ref:
            raise ReviewEpochError(
                "inherited artifact_ref does not match parent execution"
            )


def create_successor_revision(
    parent: ReviewEpochPlan | Mapping[str, Any],
    *,
    additional_executions: Sequence[EpochExecutionPlan | Mapping[str, Any]],
    inherited_artifacts: Sequence[InheritedArtifact | Mapping[str, Any]] = (),
) -> ReviewEpochPlan:
    """Create and validate an append-only supplemental revision."""

    parent_plan = ReviewEpochPlan.from_record(parent)
    additions = tuple(
        EpochExecutionPlan.from_record(item) for item in additional_executions
    )
    if not additions:
        raise ReviewEpochError("a successor revision requires additional executions")
    successor = ReviewEpochPlan(
        epoch_id=parent_plan.epoch_id,
        epoch_revision=parent_plan.epoch_revision + 1,
        base_sha=parent_plan.base_sha,
        target_sha=parent_plan.target_sha,
        scope_revision=parent_plan.scope_revision,
        source_snapshot_revision=parent_plan.source_snapshot_revision,
        scope_digest=parent_plan.scope_digest,
        source_refs=parent_plan.source_refs,
        policy_digest=parent_plan.policy_digest,
        validation_identity=parent_plan.validation_identity,
        audit_agent_budget=parent_plan.audit_agent_budget,
        required_executions=(*parent_plan.required_executions, *additions),
        parent_plan_digest=parent_plan.plan_digest,
        additional_execution_ids=tuple(item.execution_id for item in additions),
        inherited_artifacts=tuple(
            InheritedArtifact.from_record(item) for item in inherited_artifacts
        ),
    )
    validate_successor_revision(parent_plan, successor)
    return successor


@dataclass(frozen=True, slots=True)
class LeaseReservation:
    """One explicitly planned audit process held by a capacity lease."""

    process_id: str
    process_kind: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "process_id", _required_text(self.process_id, "process_id")
        )
        if self.process_kind not in AUDIT_PROCESS_KINDS:
            raise ReviewEpochError(
                f"unsupported audit process kind: {self.process_kind!r}"
            )

    def to_record(self) -> dict[str, str]:
        return {
            "process_id": self.process_id,
            "process_kind": self.process_kind,
        }

    @classmethod
    def from_record(cls, value: Any) -> "LeaseReservation":
        if isinstance(value, cls):
            return value
        record = _record(value, "lease reservation")
        _required_fields(
            record, "lease reservation", ("process_id", "process_kind")
        )
        return cls(
            process_id=record["process_id"], process_kind=record["process_kind"]
        )


@dataclass(frozen=True, slots=True)
class CapacityLease:
    """Attempt-bound reservation whose timeout quarantines rather than frees."""

    lease_id: str
    epoch_id: str
    epoch_revision: int
    execution_id: str
    attempt_id: str
    plan_digest: str
    reservations: tuple[LeaseReservation, ...]
    expires_at: str
    status: str = "starting"
    credential_revoked: bool = False
    process_exit_confirmed: bool = False
    forced_abort_acknowledged: bool = False
    terminal_reason: str = ""

    def __post_init__(self) -> None:
        for field_name in ("lease_id", "epoch_id", "execution_id", "attempt_id"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if not _EPOCH_ID_RE.fullmatch(self.epoch_id):
            raise ReviewEpochError("lease epoch_id is invalid")
        object.__setattr__(
            self,
            "epoch_revision",
            _positive_int(self.epoch_revision, "epoch_revision"),
        )
        object.__setattr__(
            self, "plan_digest", _digest(self.plan_digest, "plan_digest")
        )
        reservations = tuple(
            LeaseReservation.from_record(item) for item in self.reservations
        )
        if not reservations:
            raise ReviewEpochError("capacity leases must reserve at least one process")
        process_ids = [item.process_id for item in reservations]
        if len(set(process_ids)) != len(process_ids):
            raise ReviewEpochError("lease process reservations must be unique")
        object.__setattr__(self, "reservations", reservations)
        object.__setattr__(
            self, "expires_at", _timestamp(self.expires_at, "expires_at")
        )
        if self.status not in LEASE_STATUSES:
            raise ReviewEpochError(f"unsupported capacity lease status: {self.status}")
        for field_name in (
            "credential_revoked",
            "process_exit_confirmed",
            "forced_abort_acknowledged",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ReviewEpochError(f"{field_name} must be boolean")
        object.__setattr__(
            self,
            "terminal_reason",
            _optional_text(self.terminal_reason, "terminal_reason"),
        )

        if self.status in {"starting", "running"}:
            if (
                self.credential_revoked
                or self.process_exit_confirmed
                or self.forced_abort_acknowledged
                or self.terminal_reason
            ):
                raise ReviewEpochError(
                    f"{self.status} leases cannot carry terminal or quarantine fields"
                )
        elif self.status == "expired_quarantined":
            if not self.credential_revoked:
                raise ReviewEpochError(
                    "expired_quarantined leases require credential revocation"
                )
            if (
                self.process_exit_confirmed
                or self.forced_abort_acknowledged
                or self.terminal_reason
            ):
                raise ReviewEpochError(
                    "expired_quarantined leases remain nonterminal"
                )
        else:
            if not (
                self.process_exit_confirmed or self.forced_abort_acknowledged
            ):
                raise ReviewEpochError(
                    "terminal leases require process-exit confirmation or "
                    "forced-abort acknowledgement"
                )
            if not self.terminal_reason:
                raise ReviewEpochError("terminal leases require terminal_reason")
            if self.forced_abort_acknowledged and not self.credential_revoked:
                raise ReviewEpochError(
                    "forced-abort terminal leases require credential revocation"
                )

    @property
    def reserved_slots(self) -> int:
        return len(self.reservations)

    @property
    def capacity_held(self) -> bool:
        return self.status != "terminal"

    @property
    def live_slots(self) -> int:
        return (
            self.reserved_slots
            if self.status in {"running", "expired_quarantined"}
            else 0
        )

    @property
    def process_ids(self) -> tuple[str, ...]:
        return tuple(item.process_id for item in self.reservations)

    def mark_running(self) -> "CapacityLease":
        if self.status != "starting":
            raise ReviewEpochError("only starting leases can become running")
        return replace(self, status="running")

    def mark_expired_quarantined(self, *, now: str) -> "CapacityLease":
        if self.status not in {"starting", "running"}:
            raise ReviewEpochError(
                "only starting or running leases can become expired_quarantined"
            )
        if _parse_timestamp(now, "now") < _parse_timestamp(
            self.expires_at, "expires_at"
        ):
            raise ReviewEpochError("capacity lease has not expired")
        return replace(
            self, status="expired_quarantined", credential_revoked=True
        )

    def mark_terminal(
        self,
        *,
        reason: str,
        process_exit_confirmed: bool = False,
        forced_abort_acknowledged: bool = False,
    ) -> "CapacityLease":
        if self.status == "terminal":
            raise ReviewEpochError("terminal capacity leases cannot transition again")
        if not isinstance(process_exit_confirmed, bool) or not isinstance(
            forced_abort_acknowledged, bool
        ):
            raise ReviewEpochError("terminal confirmations must be boolean")
        if not (process_exit_confirmed or forced_abort_acknowledged):
            raise ReviewEpochError(
                "capacity remains held until process exit or forced-abort acknowledgement"
            )
        return replace(
            self,
            status="terminal",
            credential_revoked=(
                self.credential_revoked or forced_abort_acknowledged
            ),
            process_exit_confirmed=process_exit_confirmed,
            forced_abort_acknowledged=forced_abort_acknowledged,
            terminal_reason=_required_text(reason, "terminal reason"),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "epoch_id": self.epoch_id,
            "epoch_revision": self.epoch_revision,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "plan_digest": self.plan_digest,
            "reservations": [item.to_record() for item in self.reservations],
            "reserved_slots": self.reserved_slots,
            "expires_at": self.expires_at,
            "status": self.status,
            "credential_revoked": self.credential_revoked,
            "process_exit_confirmed": self.process_exit_confirmed,
            "forced_abort_acknowledged": self.forced_abort_acknowledged,
            "terminal_reason": self.terminal_reason,
        }

    @classmethod
    def from_record(cls, value: Any) -> "CapacityLease":
        if isinstance(value, cls):
            return value
        record = _record(value, "capacity lease")
        _required_fields(
            record,
            "capacity lease",
            (
                "lease_id",
                "epoch_id",
                "epoch_revision",
                "execution_id",
                "attempt_id",
                "plan_digest",
                "reservations",
                "expires_at",
                "status",
                "credential_revoked",
                "process_exit_confirmed",
                "forced_abort_acknowledged",
                "terminal_reason",
            ),
        )
        lease = cls(
            lease_id=record["lease_id"],
            epoch_id=record["epoch_id"],
            epoch_revision=record["epoch_revision"],
            execution_id=record["execution_id"],
            attempt_id=record["attempt_id"],
            plan_digest=record["plan_digest"],
            reservations=tuple(
                LeaseReservation.from_record(item)
                for item in _items(record["reservations"], "reservations")
            ),
            expires_at=record["expires_at"],
            status=record["status"],
            credential_revoked=record["credential_revoked"],
            process_exit_confirmed=record["process_exit_confirmed"],
            forced_abort_acknowledged=record["forced_abort_acknowledged"],
            terminal_reason=record["terminal_reason"],
        )
        supplied_slots = record.get("reserved_slots")
        if supplied_slots is not None and _non_negative_int(
            supplied_slots, "reserved_slots"
        ) != lease.reserved_slots:
            raise ReviewEpochError(
                "reserved_slots does not match explicit process reservations"
            )
        return lease


@dataclass(frozen=True, slots=True)
class EpochCapacityLedger:
    """Aggregate capacity ledger shared by an epoch's full revision lineage."""

    epoch_id: str
    epoch_revision: int
    plan_digest: str
    audit_agent_budget: int
    leases: tuple[CapacityLease, ...] = ()
    ancestor_plan_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        epoch_id = _required_text(self.epoch_id, "epoch_id")
        if not _EPOCH_ID_RE.fullmatch(epoch_id):
            raise ReviewEpochError("ledger epoch_id is invalid")
        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(
            self,
            "epoch_revision",
            _positive_int(self.epoch_revision, "epoch_revision"),
        )
        object.__setattr__(
            self, "plan_digest", _digest(self.plan_digest, "plan_digest")
        )
        ancestor_plan_digests = tuple(
            _digest(item, "ancestor_plan_digests")
            for item in _items(
                self.ancestor_plan_digests, "ancestor_plan_digests"
            )
        )
        if len(ancestor_plan_digests) != self.epoch_revision - 1:
            raise ReviewEpochError(
                "ancestor_plan_digests must identify every prior epoch revision"
            )
        lineage_plan_digests = (*ancestor_plan_digests, self.plan_digest)
        if len(set(lineage_plan_digests)) != len(lineage_plan_digests):
            raise ReviewEpochError(
                "epoch lineage plan digests must be unique by revision"
            )
        object.__setattr__(
            self, "ancestor_plan_digests", ancestor_plan_digests
        )
        object.__setattr__(
            self,
            "audit_agent_budget",
            _positive_int(self.audit_agent_budget, "audit_agent_budget"),
        )
        leases = tuple(CapacityLease.from_record(item) for item in self.leases)
        lease_ids = [lease.lease_id for lease in leases]
        if len(set(lease_ids)) != len(lease_ids):
            raise ReviewEpochError("capacity lease_id must be unique")
        for lease in leases:
            if (
                lease.epoch_id != self.epoch_id
                or lease.epoch_revision > self.epoch_revision
                or not hmac.compare_digest(
                    lease.plan_digest,
                    lineage_plan_digests[lease.epoch_revision - 1],
                )
            ):
                raise ReviewEpochError(
                    "capacity lease identity does not match epoch lineage"
                )
        active_process_ids = [
            process_id
            for lease in leases
            if lease.capacity_held
            for process_id in lease.process_ids
        ]
        if len(set(active_process_ids)) != len(active_process_ids):
            raise ReviewEpochError(
                "a planned process cannot be held by multiple active leases"
            )
        object.__setattr__(self, "leases", leases)
        if self.reserved_slots > self.audit_agent_budget:
            raise ReviewEpochError("aggregate reserved slots exceed audit_agent_budget")
        if self.live_slots > self.audit_agent_budget:
            raise ReviewEpochError("aggregate live slots exceed audit_agent_budget")

    @property
    def reserved_slots(self) -> int:
        return sum(
            lease.reserved_slots for lease in self.leases if lease.capacity_held
        )

    @property
    def live_slots(self) -> int:
        return sum(lease.live_slots for lease in self.leases)

    @property
    def available_slots(self) -> int:
        return self.audit_agent_budget - self.reserved_slots

    @property
    def blocks_new_starts(self) -> bool:
        return any(lease.status == "expired_quarantined" for lease in self.leases)

    def lease(self, lease_id: str) -> CapacityLease:
        for lease in self.leases:
            if lease.lease_id == lease_id:
                return lease
        raise ReviewEpochError(f"unknown capacity lease: {lease_id}")

    def validate_for_plan(self, plan: ReviewEpochPlan | Mapping[str, Any]) -> None:
        epoch = ReviewEpochPlan.from_record(plan)
        if (
            epoch.epoch_id != self.epoch_id
            or epoch.epoch_revision != self.epoch_revision
            or not hmac.compare_digest(epoch.plan_digest, self.plan_digest)
            or epoch.audit_agent_budget != self.audit_agent_budget
        ):
            raise ReviewEpochError("capacity ledger does not match epoch plan")
        if epoch.epoch_revision > 1 and not hmac.compare_digest(
            epoch.parent_plan_digest, self.ancestor_plan_digests[-1]
        ):
            raise ReviewEpochError(
                "capacity ledger predecessor does not match epoch plan"
            )
        for lease in self.leases:
            execution = epoch.execution(lease.execution_id)
            if lease.attempt_id != execution.attempt_id:
                raise ReviewEpochError("capacity lease attempt_id is stale")
            for reservation in lease.reservations:
                process = execution.process(reservation.process_id)
                if process.process_kind != reservation.process_kind:
                    raise ReviewEpochError(
                        "capacity reservation process_kind does not match plan"
                    )

    def reserve(
        self,
        plan: ReviewEpochPlan | Mapping[str, Any],
        *,
        lease_id: str,
        execution_id: str,
        process_ids: Sequence[str],
        expires_at: str,
    ) -> "EpochCapacityLedger":
        epoch = ReviewEpochPlan.from_record(plan)
        self.validate_for_plan(epoch)
        if self.blocks_new_starts:
            raise ReviewEpochError(
                "expired_quarantined lease blocks every new start in the epoch"
            )
        execution = epoch.execution(execution_id)
        selected = _text_tuple(process_ids, "process_ids")
        active = {
            process_id
            for lease in self.leases
            if lease.capacity_held
            for process_id in lease.process_ids
        }
        if active.intersection(selected):
            raise ReviewEpochError("planned process already has an active lease")
        reservations = tuple(
            LeaseReservation(
                process_id=process.process_id,
                process_kind=process.process_kind,
            )
            for process in (execution.process(process_id) for process_id in selected)
        )
        if self.reserved_slots + len(reservations) > self.audit_agent_budget:
            raise ReviewEpochError(
                "aggregate audit_agent_budget cannot satisfy capacity reservation"
            )
        lease = CapacityLease(
            lease_id=lease_id,
            epoch_id=self.epoch_id,
            epoch_revision=self.epoch_revision,
            execution_id=execution.execution_id,
            attempt_id=execution.attempt_id,
            plan_digest=self.plan_digest,
            reservations=reservations,
            expires_at=expires_at,
        )
        return replace(self, leases=(*self.leases, lease))

    def _replace_lease(self, replacement: CapacityLease) -> "EpochCapacityLedger":
        found = False
        leases: list[CapacityLease] = []
        for lease in self.leases:
            if lease.lease_id == replacement.lease_id:
                leases.append(replacement)
                found = True
            else:
                leases.append(lease)
        if not found:
            raise ReviewEpochError(f"unknown capacity lease: {replacement.lease_id}")
        return replace(self, leases=tuple(leases))

    def mark_running(self, lease_id: str) -> "EpochCapacityLedger":
        return self._replace_lease(self.lease(lease_id).mark_running())

    def mark_expired_quarantined(
        self, lease_id: str, *, now: str
    ) -> "EpochCapacityLedger":
        return self._replace_lease(
            self.lease(lease_id).mark_expired_quarantined(now=now)
        )

    def mark_terminal(
        self,
        lease_id: str,
        *,
        reason: str,
        process_exit_confirmed: bool = False,
        forced_abort_acknowledged: bool = False,
    ) -> "EpochCapacityLedger":
        return self._replace_lease(
            self.lease(lease_id).mark_terminal(
                reason=reason,
                process_exit_confirmed=process_exit_confirmed,
                forced_abort_acknowledged=forced_abort_acknowledged,
            )
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "record_type": "review_epoch_capacity",
            "epoch_id": self.epoch_id,
            "epoch_revision": self.epoch_revision,
            "plan_digest": self.plan_digest,
            "ancestor_plan_digests": list(self.ancestor_plan_digests),
            "audit_agent_budget": self.audit_agent_budget,
            "leases": [lease.to_record() for lease in self.leases],
            "reserved_slots": self.reserved_slots,
            "live_slots": self.live_slots,
            "available_slots": self.available_slots,
            "blocks_new_starts": self.blocks_new_starts,
        }

    @classmethod
    def from_record(cls, value: Any) -> "EpochCapacityLedger":
        if isinstance(value, cls):
            return value
        record = _record(value, "epoch capacity ledger")
        if (
            record.get("record_type", "review_epoch_capacity")
            != "review_epoch_capacity"
        ):
            raise ReviewEpochError("epoch capacity ledger record_type is invalid")
        _required_fields(
            record,
            "epoch capacity ledger",
            (
                "epoch_id",
                "epoch_revision",
                "plan_digest",
                "ancestor_plan_digests",
                "audit_agent_budget",
                "leases",
            ),
        )
        ledger = cls(
            epoch_id=record["epoch_id"],
            epoch_revision=record["epoch_revision"],
            plan_digest=record["plan_digest"],
            ancestor_plan_digests=tuple(
                _items(
                    record["ancestor_plan_digests"],
                    "ancestor_plan_digests",
                )
            ),
            audit_agent_budget=record["audit_agent_budget"],
            leases=tuple(
                CapacityLease.from_record(item)
                for item in _items(record["leases"], "leases")
            ),
        )
        derived = {
            "reserved_slots": ledger.reserved_slots,
            "live_slots": ledger.live_slots,
            "available_slots": ledger.available_slots,
            "blocks_new_starts": ledger.blocks_new_starts,
        }
        for field_name, expected in derived.items():
            if field_name in record and record[field_name] != expected:
                raise ReviewEpochError(
                    f"{field_name} does not match capacity lease state"
                )
        return ledger


# Descriptive aliases keep later CLI integration readable without weakening the
# single canonical implementation above.
ReviewEpochRevision = ReviewEpochPlan
AuditCapacityLease = CapacityLease
