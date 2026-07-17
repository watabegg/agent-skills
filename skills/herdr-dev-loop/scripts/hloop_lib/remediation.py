"""Crash-safe, pure remediation-ledger contracts for HLoop 0.5.3.

This module deliberately contains no filesystem or ``STATE.json`` mutation.
It models the transaction that a later CLI integration must persist:

* audit sources register immutable candidate observations;
* observations sharing a semantic fingerprint are canonicalized, while
  conflicting policy axes stop the batch;
* one Manager approval consumes one remediation round and writes a complete,
  deterministic task materialization plan ahead of any task artifact;
* reconciliation distinguishes safely repairable omissions from conflicting
  persisted state and never guesses through the latter.

The release-scope authorization function remains the authority for whether a
canonical finding may become a task.  This module adds sequencing and crash
safety around that existing policy; it does not duplicate or weaken it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from hloop_lib.release_scope import (
    ReleaseScope,
    ReleaseScopeError,
    TaskAuthorizationError,
    authorize_task_creation,
)
from hloop_lib.review_policy import (
    NON_BLOCKING_DISPOSITIONS,
    FindingDisposition,
    ReviewPolicyError,
    validate_disposition,
)


REMEDIATION_LEDGER_SCHEMA_REVISION = 1
DEFAULT_MAX_FIX_ROUNDS = 2
MAX_FIX_ROUNDS = 2

SOURCE_KINDS = frozenset({"reviewer", "gap", "convergence", "manual-final"})
BATCH_STATUSES = frozenset(
    {
        "candidate_registered",
        "classification_conflict",
        "ready_to_triage",
        "materializing",
        "dispatched",
        "completed",
        "aborted",
    }
)
COMPLETION_OUTCOMES = frozenset({"product_changed", "no_change", "aborted"})
RECONCILE_STATUSES = frozenset(
    {"repair_required", "remediation_reconcile_required", "dispatched"}
)
POLICY_AXES = (
    "fact_status",
    "severity",
    "origin",
    "contract_relation",
    "decision_requirement",
    "disposition",
    "release_effect",
)
_CLASSIFICATION_FIELDS = frozenset(
    {
        "finding_id",
        "source_artifact",
        "source_candidate_id",
        "fingerprint",
        "target_sha",
        *POLICY_AXES,
        "requirement_refs",
        "why_fix_now",
        "remediation_round",
        "duplicate_of",
        "accepted_risk_decision_id",
        # Accepted input aliases normalized by FindingDisposition.from_record.
        "head_sha",
        "decision_id",
    }
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINGERPRINT_RE = _DIGEST_RE
_BATCH_ID_RE = re.compile(r"^RB[0-9]{3,}$")
_TASK_ID_RE = re.compile(r"^T[0-9]{3,}$")


class RemediationLedgerError(ValueError):
    """Base class for invalid remediation state or transitions."""


class CandidateClassificationConflict(RemediationLedgerError):
    """Raised when policy-axis conflicts have no Manager canonicalization."""


class RemediationApprovalConflict(RemediationLedgerError):
    """Raised when an approved batch is retried with different semantics."""


class RemediationRoundLimitExceeded(RemediationLedgerError):
    """Raised when another automatic round lacks explicit authorization."""


class RemediationReconcileRequired(RemediationLedgerError):
    """Raised by callers that require reconciliation to be immediately clean."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RemediationLedgerError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RemediationLedgerError(f"{field_name} must be a string")
    return value.strip()


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RemediationLedgerError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RemediationLedgerError(f"{field_name} must be a non-negative integer")
    return value


def _text_tuple(
    values: Sequence[Any] | None,
    field_name: str,
    *,
    sort: bool = False,
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise RemediationLedgerError(f"{field_name} must be an array of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if len(set(normalized)) != len(normalized):
        raise RemediationLedgerError(f"{field_name} must not contain duplicates")
    return tuple(sorted(normalized)) if sort else normalized


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RemediationLedgerError(f"{field_name} must be an object")
    return value


def _closed_record(
    value: Any,
    field_name: str,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> Mapping[str, Any]:
    record = _record(value, field_name)
    missing = [name for name in required if name not in record]
    if missing:
        raise RemediationLedgerError(
            f"{field_name} is missing required fields: " + ", ".join(missing)
        )
    unknown = sorted(set(record) - set(required) - set(optional))
    if unknown:
        raise RemediationLedgerError(
            f"{field_name} contains unknown fields: " + ", ".join(unknown)
        )
    return record


def _digest(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if _DIGEST_RE.fullmatch(text) is None:
        raise RemediationLedgerError(f"{field_name} must use sha256:<hex>")
    return text


def _optional_digest(value: Any, field_name: str) -> str:
    text = _optional_text(value, field_name)
    return _digest(text, field_name) if text else ""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _thaw_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RemediationLedgerError("value must be canonical JSON data") from exc


def canonical_digest(value: Any) -> str:
    """Return the version-independent digest used for ledger identities."""

    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _freeze_json(value: Any) -> Any:
    """Copy JSON data into immutable containers."""

    normalized = json.loads(_canonical_json(value))

    def freeze(item: Any) -> Any:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(normalized)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _policy_axes(disposition: FindingDisposition) -> dict[str, str]:
    return {field: getattr(disposition, field) for field in POLICY_AXES}


def _normalize_disposition(value: FindingDisposition | Mapping[str, Any]) -> FindingDisposition:
    try:
        if isinstance(value, Mapping):
            unknown = sorted(set(value) - _CLASSIFICATION_FIELDS)
            if unknown:
                raise RemediationLedgerError(
                    "finding classification contains unknown fields: "
                    + ", ".join(unknown)
                )
        disposition = (
            value
            if isinstance(value, FindingDisposition)
            else FindingDisposition.from_record(value)
        )
        return validate_disposition(
            disposition,
            # Candidate registration is nonterminal.  The exact Manager
            # authorization is resolved and persisted only after canonical
            # classification in ``mark_ready_to_triage``.
            accepted_risk_authorized=(
                disposition.disposition == "accepted_risk"
            ),
        )
    except ReviewPolicyError as exc:
        raise RemediationLedgerError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class AcceptedRiskAuthorization:
    """Manager authorization resolved against one canonical finding identity."""

    decision_id: str
    fingerprint: str
    target_sha: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", _required_text(self.decision_id, "decision_id")
        )
        object.__setattr__(
            self, "fingerprint", _digest(self.fingerprint, "fingerprint")
        )
        object.__setattr__(
            self, "target_sha", _required_text(self.target_sha, "target_sha")
        )

    def to_record(self) -> dict[str, str]:
        return {
            "decision_id": self.decision_id,
            "fingerprint": self.fingerprint,
            "target_sha": self.target_sha,
        }

    @classmethod
    def from_record(cls, value: Any) -> "AcceptedRiskAuthorization":
        return cls(
            **_closed_record(
                value,
                "accepted-risk authorization",
                required=("decision_id", "fingerprint", "target_sha"),
            )
        )


@dataclass(frozen=True, slots=True)
class ExtraRoundAuthorization:
    """Captured user authorization for an exact set of remediation batches."""

    input_id: str
    authorized_extra_rounds: int
    remediation_batch_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_id", _required_text(self.input_id, "input_id")
        )
        count = _positive_int(
            self.authorized_extra_rounds, "authorized_extra_rounds"
        )
        batch_ids = _text_tuple(
            self.remediation_batch_ids, "remediation_batch_ids", sort=True
        )
        if len(batch_ids) != count:
            raise RemediationLedgerError(
                "authorized_extra_rounds must exactly match remediation_batch_ids"
            )
        for batch_id in batch_ids:
            if _BATCH_ID_RE.fullmatch(batch_id) is None:
                raise RemediationLedgerError(
                    "remediation_batch_ids must use RB<digits>"
                )
        object.__setattr__(self, "authorized_extra_rounds", count)
        object.__setattr__(self, "remediation_batch_ids", batch_ids)

    def ref_for(self, batch_id: str) -> str:
        normalized = _required_text(batch_id, "batch_id")
        if normalized not in self.remediation_batch_ids:
            raise RemediationLedgerError(
                "extra-round authorization is bound to other remediation batches"
            )
        return f"{self.input_id}:{normalized}"

    def to_record(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "authorized_extra_rounds": self.authorized_extra_rounds,
            "remediation_batch_ids": list(self.remediation_batch_ids),
        }

    @classmethod
    def from_record(cls, value: Any) -> "ExtraRoundAuthorization":
        return cls(
            **_closed_record(
                value,
                "extra-round authorization",
                required=(
                    "input_id",
                    "authorized_extra_rounds",
                    "remediation_batch_ids",
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    """One source's immutable observation of a semantic finding."""

    observation_id: str
    source_kind: str
    source_ref: str
    source_execution_id: str
    source_candidate_id: str
    fingerprint: str
    target_sha: str
    classification: FindingDisposition
    requirement_refs: tuple[str, ...] = ()
    scope_refs: tuple[str, ...] = ()
    why_fix_now: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _required_text(self.observation_id, "observation_id")
        )
        source_kind = _required_text(self.source_kind, "source_kind")
        if source_kind == "manual_final":
            source_kind = "manual-final"
        if source_kind not in SOURCE_KINDS:
            raise RemediationLedgerError(f"unsupported source_kind: {source_kind}")
        object.__setattr__(self, "source_kind", source_kind)
        for name in ("source_ref", "source_execution_id", "source_candidate_id", "target_sha"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        fingerprint = _digest(self.fingerprint, "fingerprint")
        object.__setattr__(self, "fingerprint", fingerprint)
        classification = _normalize_disposition(self.classification)
        if classification.fingerprint and classification.fingerprint != fingerprint:
            raise RemediationLedgerError(
                "classification fingerprint does not match candidate fingerprint"
            )
        if classification.target_sha and classification.target_sha != self.target_sha:
            raise RemediationLedgerError(
                "classification target_sha does not match candidate target_sha"
            )
        object.__setattr__(self, "classification", classification)
        object.__setattr__(
            self,
            "requirement_refs",
            _text_tuple(self.requirement_refs, "requirement_refs", sort=True),
        )
        object.__setattr__(
            self, "scope_refs", _text_tuple(self.scope_refs, "scope_refs", sort=True)
        )
        object.__setattr__(
            self, "why_fix_now", _optional_text(self.why_fix_now, "why_fix_now")
        )

    @property
    def candidate_id(self) -> str:
        return self.observation_id

    @property
    def policy_axes(self) -> dict[str, str]:
        return _policy_axes(self.classification)

    def to_record(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "source_execution_id": self.source_execution_id,
            "source_candidate_id": self.source_candidate_id,
            "fingerprint": self.fingerprint,
            "target_sha": self.target_sha,
            "classification": self.classification.to_record(),
            "requirement_refs": list(self.requirement_refs),
            "scope_refs": list(self.scope_refs),
            "why_fix_now": self.why_fix_now,
        }

    @classmethod
    def from_record(cls, value: Any) -> "CandidateObservation":
        record = _closed_record(
            value,
            "candidate observation",
            required=(
                "observation_id",
                "source_kind",
                "source_ref",
                "source_execution_id",
                "source_candidate_id",
                "fingerprint",
                "target_sha",
                "classification",
                "requirement_refs",
                "scope_refs",
                "why_fix_now",
            ),
        )
        return cls(
            observation_id=record["observation_id"],
            source_kind=record["source_kind"],
            source_ref=record["source_ref"],
            source_execution_id=record["source_execution_id"],
            source_candidate_id=record["source_candidate_id"],
            fingerprint=record["fingerprint"],
            target_sha=record["target_sha"],
            classification=_normalize_disposition(record["classification"]),
            requirement_refs=record["requirement_refs"],
            scope_refs=record["scope_refs"],
            why_fix_now=record["why_fix_now"],
        )


# Shorter public name for callers that already use "candidate" terminology.
RemediationCandidate = CandidateObservation


@dataclass(frozen=True, slots=True)
class CanonicalCandidate:
    """Fingerprint-level candidate after Manager-visible classification."""

    fingerprint: str
    target_sha: str
    classification: FindingDisposition
    observation_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    source_execution_ids: tuple[str, ...]
    requirement_refs: tuple[str, ...]
    scope_refs: tuple[str, ...]
    why_fix_now: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", _digest(self.fingerprint, "fingerprint"))
        object.__setattr__(self, "target_sha", _required_text(self.target_sha, "target_sha"))
        classification = _normalize_disposition(self.classification)
        if classification.fingerprint and classification.fingerprint != self.fingerprint:
            raise RemediationLedgerError("canonical classification fingerprint mismatch")
        if classification.target_sha and classification.target_sha != self.target_sha:
            raise RemediationLedgerError("canonical classification target_sha mismatch")
        object.__setattr__(self, "classification", classification)
        for name in (
            "observation_ids",
            "source_refs",
            "source_execution_ids",
            "requirement_refs",
            "scope_refs",
        ):
            values = _text_tuple(getattr(self, name), name, sort=True)
            if name in {"observation_ids", "source_refs", "source_execution_ids"} and not values:
                raise RemediationLedgerError(f"{name} must not be empty")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "why_fix_now", _required_text(self.why_fix_now, "why_fix_now"))

    @property
    def policy_axes(self) -> dict[str, str]:
        return _policy_axes(self.classification)

    def to_record(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "target_sha": self.target_sha,
            "classification": self.classification.to_record(),
            "observation_ids": list(self.observation_ids),
            "source_refs": list(self.source_refs),
            "source_execution_ids": list(self.source_execution_ids),
            "requirement_refs": list(self.requirement_refs),
            "scope_refs": list(self.scope_refs),
            "why_fix_now": self.why_fix_now,
        }

    @classmethod
    def from_record(cls, value: Any) -> "CanonicalCandidate":
        record = _closed_record(
            value,
            "canonical candidate",
            required=(
                "fingerprint",
                "target_sha",
                "classification",
                "observation_ids",
                "source_refs",
                "source_execution_ids",
                "requirement_refs",
                "scope_refs",
                "why_fix_now",
            ),
        )
        return cls(
            fingerprint=record["fingerprint"],
            target_sha=record["target_sha"],
            classification=_normalize_disposition(record["classification"]),
            observation_ids=record["observation_ids"],
            source_refs=record["source_refs"],
            source_execution_ids=record["source_execution_ids"],
            requirement_refs=record["requirement_refs"],
            scope_refs=record["scope_refs"],
            why_fix_now=record["why_fix_now"],
        )


@dataclass(frozen=True, slots=True)
class PlannedRemediationTask:
    """One deterministic task entry stored before materialization starts."""

    task_id: str
    candidate_fingerprints: tuple[str, ...]
    task_contract: Mapping[str, Any]
    source_refs: tuple[str, ...]
    artifact_ref: str = ""
    task_contract_digest: str = ""

    def __post_init__(self) -> None:
        task_id = _required_text(self.task_id, "task_id")
        if _TASK_ID_RE.fullmatch(task_id) is None:
            raise RemediationLedgerError("task_id must use T<digits>")
        object.__setattr__(self, "task_id", task_id)
        fingerprints = _text_tuple(
            self.candidate_fingerprints, "candidate_fingerprints", sort=True
        )
        if not fingerprints:
            raise RemediationLedgerError("candidate_fingerprints must not be empty")
        for fingerprint in fingerprints:
            _digest(fingerprint, "candidate_fingerprints")
        object.__setattr__(self, "candidate_fingerprints", fingerprints)
        contract = _record(self.task_contract, "task_contract")
        if contract.get("id") != task_id:
            raise RemediationLedgerError("task contract id does not match planned task_id")
        frozen_contract = _freeze_json(contract)
        object.__setattr__(self, "task_contract", frozen_contract)
        expected_digest = canonical_digest(_thaw_json(frozen_contract))
        supplied_digest = _optional_digest(
            self.task_contract_digest, "task_contract_digest"
        )
        if supplied_digest and supplied_digest != expected_digest:
            raise RemediationLedgerError(
                "task_contract_digest does not match the canonical task contract"
            )
        object.__setattr__(self, "task_contract_digest", expected_digest)
        object.__setattr__(
            self, "source_refs", _text_tuple(self.source_refs, "source_refs", sort=True)
        )
        if not self.source_refs:
            raise RemediationLedgerError("source_refs must not be empty")
        artifact_ref = self.artifact_ref or f"tasks/{task_id}.md"
        artifact_ref = _required_text(artifact_ref, "artifact_ref")
        if artifact_ref != f"tasks/{task_id}.md":
            raise RemediationLedgerError(
                "artifact_ref must be the canonical tasks/<task_id>.md path"
            )
        object.__setattr__(self, "artifact_ref", artifact_ref)

    def to_record(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "candidate_fingerprints": list(self.candidate_fingerprints),
            "task_contract": _thaw_json(self.task_contract),
            "task_contract_digest": self.task_contract_digest,
            "source_refs": list(self.source_refs),
            "artifact_ref": self.artifact_ref,
        }

    @classmethod
    def from_record(cls, value: Any) -> "PlannedRemediationTask":
        record = _closed_record(
            value,
            "planned remediation task",
            required=(
                "task_id",
                "candidate_fingerprints",
                "task_contract",
                "task_contract_digest",
                "source_refs",
                "artifact_ref",
            ),
        )
        return cls(
            task_id=record["task_id"],
            candidate_fingerprints=record["candidate_fingerprints"],
            task_contract=record["task_contract"],
            task_contract_digest=record["task_contract_digest"],
            source_refs=record["source_refs"],
            artifact_ref=record["artifact_ref"],
        )


TaskMaterializationPlan = PlannedRemediationTask


@dataclass(frozen=True, slots=True)
class MaterializedTask:
    task_id: str
    state_contract_digest: str
    artifact_contract_digest: str
    artifact_digest: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        task_id = _required_text(self.task_id, "task_id")
        if _TASK_ID_RE.fullmatch(task_id) is None:
            raise RemediationLedgerError("task_id must use T<digits>")
        object.__setattr__(self, "task_id", task_id)
        for name in (
            "state_contract_digest",
            "artifact_contract_digest",
            "artifact_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        refs = _text_tuple(self.source_refs, "source_refs", sort=True)
        if not refs:
            raise RemediationLedgerError("source_refs must not be empty")
        object.__setattr__(self, "source_refs", refs)

    def to_record(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state_contract_digest": self.state_contract_digest,
            "artifact_contract_digest": self.artifact_contract_digest,
            "artifact_digest": self.artifact_digest,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_record(cls, value: Any) -> "MaterializedTask":
        record = _closed_record(
            value,
            "materialized task",
            required=(
                "task_id",
                "state_contract_digest",
                "artifact_contract_digest",
                "artifact_digest",
                "source_refs",
            ),
        )
        return cls(**record)


@dataclass(frozen=True, slots=True)
class TaskMaterializationObservation:
    """Observed durable projections used during crash reconciliation."""

    task_id: str
    state_task_contract: Mapping[str, Any] | None = None
    artifact_task_contract: Mapping[str, Any] | None = None
    artifact_digest: str = ""
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        task_id = _required_text(self.task_id, "task_id")
        if _TASK_ID_RE.fullmatch(task_id) is None:
            raise RemediationLedgerError("task_id must use T<digits>")
        object.__setattr__(self, "task_id", task_id)
        for name in ("state_task_contract", "artifact_task_contract"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze_json(_record(value, name)))
        object.__setattr__(
            self, "artifact_digest", _optional_digest(self.artifact_digest, "artifact_digest")
        )
        object.__setattr__(
            self, "source_refs", _text_tuple(self.source_refs, "source_refs", sort=True)
        )

    @classmethod
    def from_record(cls, value: Any) -> "TaskMaterializationObservation":
        record = _closed_record(
            value,
            "task materialization observation",
            required=(
                "task_id",
                "state_task_contract",
                "artifact_task_contract",
                "artifact_digest",
                "source_refs",
            ),
        )
        return cls(**record)


def _is_materializable_candidate(candidate: CanonicalCandidate) -> bool:
    classification = candidate.classification
    return bool(
        classification.fact_status == "confirmed"
        and classification.contract_relation == "in_scope"
        and classification.disposition == "fix_now"
        and classification.release_effect == "blocking"
    )


def _approval_payload(
    *,
    approval_ref: str,
    batch_id: str,
    candidate_set_digest: str,
    accepted_risk_authorizations: Sequence[AcceptedRiskAuthorization],
    extra_round_authorization_ref: str,
    extra_round_authorization: ExtraRoundAuthorization | None,
    materialization_plan: Sequence[PlannedRemediationTask],
    remediation_round: int,
    scope_digest: str,
    scope_revision: int,
) -> dict[str, Any]:
    """Return the one canonical Manager approval payload used on every path."""

    return {
        "approval_ref": approval_ref,
        "batch_id": batch_id,
        "candidate_set_digest": candidate_set_digest,
        "accepted_risk_authorizations": [
            item.to_record() for item in accepted_risk_authorizations
        ],
        "extra_round_authorization_ref": extra_round_authorization_ref,
        "extra_round_authorization": (
            extra_round_authorization.to_record()
            if extra_round_authorization is not None
            else None
        ),
        "materialization_plan": [item.to_record() for item in materialization_plan],
        "remediation_round": remediation_round,
        "scope_digest": scope_digest,
        "scope_revision": scope_revision,
    }


@dataclass(frozen=True, slots=True)
class RemediationBatch:
    batch_id: str
    epoch_id: str
    target_sha: str
    required_execution_ids: tuple[str, ...]
    terminal_execution_ids: tuple[str, ...] = ()
    status: str = "candidate_registered"
    observations: tuple[CandidateObservation, ...] = ()
    canonical_candidates: tuple[CanonicalCandidate, ...] = ()
    classification_conflicts: tuple[str, ...] = ()
    canonicalization_ref: str = ""
    candidate_set_digest: str = ""
    accepted_risk_authorizations: tuple[AcceptedRiskAuthorization, ...] = ()
    scope_digest: str = ""
    scope_revision: int = 0
    approval_ref: str = ""
    approval_digest: str = ""
    remediation_round: int = 0
    round_consumed: bool = False
    extra_round_authorization_ref: str = ""
    extra_round_authorization: ExtraRoundAuthorization | None = None
    materialization_plan: tuple[PlannedRemediationTask, ...] = ()
    materialized_tasks: tuple[MaterializedTask, ...] = ()
    completion_outcome: str = ""

    def __post_init__(self) -> None:
        batch_id = _required_text(self.batch_id, "batch_id")
        if _BATCH_ID_RE.fullmatch(batch_id) is None:
            raise RemediationLedgerError("batch_id must use RB<digits>")
        object.__setattr__(self, "batch_id", batch_id)
        object.__setattr__(self, "epoch_id", _required_text(self.epoch_id, "epoch_id"))
        object.__setattr__(self, "target_sha", _required_text(self.target_sha, "target_sha"))
        required_ids = _text_tuple(
            self.required_execution_ids, "required_execution_ids", sort=True
        )
        if not required_ids:
            raise RemediationLedgerError("required_execution_ids must not be empty")
        terminal_ids = _text_tuple(
            self.terminal_execution_ids, "terminal_execution_ids", sort=True
        )
        if not set(terminal_ids).issubset(required_ids):
            raise RemediationLedgerError(
                "terminal_execution_ids must be a subset of required_execution_ids"
            )
        object.__setattr__(self, "required_execution_ids", required_ids)
        object.__setattr__(self, "terminal_execution_ids", terminal_ids)
        status = _required_text(self.status, "status")
        if status not in BATCH_STATUSES:
            raise RemediationLedgerError(f"unsupported remediation batch status: {status}")
        object.__setattr__(self, "status", status)

        observations = tuple(sorted(self.observations, key=lambda item: item.observation_id))
        if len({item.observation_id for item in observations}) != len(observations):
            raise RemediationLedgerError("observation_id must be unique within a batch")
        if any(item.target_sha != self.target_sha for item in observations):
            raise RemediationLedgerError("candidate target_sha must match the batch target_sha")
        if any(item.source_execution_id not in required_ids for item in observations):
            raise RemediationLedgerError(
                "candidate source_execution_id must be required by the batch"
            )
        object.__setattr__(self, "observations", observations)

        canonical = tuple(
            sorted(self.canonical_candidates, key=lambda item: item.fingerprint)
        )
        if len({item.fingerprint for item in canonical}) != len(canonical):
            raise RemediationLedgerError("canonical candidate fingerprints must be unique")
        object.__setattr__(self, "canonical_candidates", canonical)
        conflicts = _text_tuple(
            self.classification_conflicts, "classification_conflicts", sort=True
        )
        for fingerprint in conflicts:
            _digest(fingerprint, "classification_conflicts")
        object.__setattr__(self, "classification_conflicts", conflicts)
        object.__setattr__(
            self,
            "canonicalization_ref",
            _optional_text(self.canonicalization_ref, "canonicalization_ref"),
        )
        object.__setattr__(
            self,
            "candidate_set_digest",
            _optional_digest(self.candidate_set_digest, "candidate_set_digest"),
        )
        accepted_risk_authorizations = tuple(self.accepted_risk_authorizations)
        if any(
            not isinstance(item, AcceptedRiskAuthorization)
            for item in accepted_risk_authorizations
        ):
            raise RemediationLedgerError(
                "accepted_risk_authorizations contain invalid records"
            )
        accepted_risk_authorizations = tuple(
            sorted(
                accepted_risk_authorizations,
                key=lambda item: (item.fingerprint, item.decision_id),
            )
        )
        if len(
            {
                (item.decision_id, item.fingerprint, item.target_sha)
                for item in accepted_risk_authorizations
            }
        ) != len(accepted_risk_authorizations):
            raise RemediationLedgerError(
                "accepted_risk_authorizations must not contain duplicates"
            )
        object.__setattr__(
            self,
            "accepted_risk_authorizations",
            accepted_risk_authorizations,
        )
        object.__setattr__(
            self, "scope_digest", _optional_digest(self.scope_digest, "scope_digest")
        )
        scope_revision = _nonnegative_int(self.scope_revision, "scope_revision")
        remediation_round = _nonnegative_int(self.remediation_round, "remediation_round")
        object.__setattr__(self, "scope_revision", scope_revision)
        object.__setattr__(self, "remediation_round", remediation_round)
        if not isinstance(self.round_consumed, bool):
            raise RemediationLedgerError("round_consumed must be boolean")
        for name in (
            "approval_ref",
            "extra_round_authorization_ref",
            "completion_outcome",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "approval_digest",
            _optional_digest(self.approval_digest, "approval_digest"),
        )
        extra_authorization = self.extra_round_authorization
        if extra_authorization is not None and not isinstance(
            extra_authorization, ExtraRoundAuthorization
        ):
            raise RemediationLedgerError(
                "extra_round_authorization must be an authorization record or null"
            )
        if bool(self.extra_round_authorization_ref) != bool(extra_authorization):
            raise RemediationLedgerError(
                "extra_round_authorization_ref and record must be stored together"
            )
        if extra_authorization is not None and (
            extra_authorization.ref_for(batch_id)
            != self.extra_round_authorization_ref
        ):
            raise RemediationLedgerError(
                "extra_round_authorization_ref does not match its captured input and batch"
            )
        plan = tuple(sorted(self.materialization_plan, key=lambda item: item.task_id))
        if len({item.task_id for item in plan}) != len(plan):
            raise RemediationLedgerError("planned task ids must be unique")
        materialized = tuple(sorted(self.materialized_tasks, key=lambda item: item.task_id))
        if len({item.task_id for item in materialized}) != len(materialized):
            raise RemediationLedgerError("materialized task ids must be unique")
        object.__setattr__(self, "materialization_plan", plan)
        object.__setattr__(self, "materialized_tasks", materialized)

        preapproval = {"candidate_registered", "classification_conflict", "ready_to_triage"}
        if status in preapproval:
            if any(
                (
                    self.scope_digest,
                    scope_revision,
                    self.approval_ref,
                    self.approval_digest,
                    remediation_round,
                    self.round_consumed,
                    self.extra_round_authorization_ref,
                    self.extra_round_authorization,
                    plan,
                    materialized,
                    self.completion_outcome,
                )
            ):
                raise RemediationLedgerError(
                    "pre-approval batch must not contain approval or materialization state"
                )
        if status == "classification_conflict":
            if not conflicts:
                raise RemediationLedgerError(
                    "classification_conflict status requires conflicting fingerprints"
                )
        elif conflicts:
            raise RemediationLedgerError(
                "classification conflicts require classification_conflict status"
            )

        ready_or_later = status in {
            "ready_to_triage",
            "materializing",
            "dispatched",
            "completed",
            "aborted",
        }
        if ready_or_later:
            if terminal_ids != required_ids:
                raise RemediationLedgerError(
                    "ready batch requires every required execution to be terminal"
                )
            if not canonical:
                raise RemediationLedgerError("ready batch requires canonical candidates")
            expected_candidate_digest = canonical_digest(
                [item.to_record() for item in canonical]
            )
            if self.candidate_set_digest != expected_candidate_digest:
                raise RemediationLedgerError(
                    "candidate_set_digest does not match canonical candidates"
                )
            _accepted_risk_authorizations(
                canonical, accepted_risk_authorizations
            )
        elif canonical or self.candidate_set_digest or self.canonicalization_ref:
            raise RemediationLedgerError(
                "unready batch must not contain canonical candidate state"
            )
        elif accepted_risk_authorizations:
            raise RemediationLedgerError(
                "unready batch must not consume accepted-risk authorizations"
            )

        approved_or_later = status in {
            "materializing",
            "dispatched",
            "completed",
            "aborted",
        }
        if approved_or_later:
            if not (
                self.scope_digest
                and scope_revision >= 1
                and self.approval_ref
                and self.approval_digest
                and remediation_round >= 1
                and self.round_consumed
                and plan
            ):
                raise RemediationLedgerError(
                    "approved batch requires scope, approval, round, and write-ahead plan"
                )
            actionable_fingerprints = {
                item.fingerprint
                for item in canonical
                if _is_materializable_candidate(item)
            }
            unresolved = tuple(
                item.fingerprint
                for item in canonical
                if item.fingerprint not in actionable_fingerprints
                and item.classification.disposition not in NON_BLOCKING_DISPOSITIONS
            )
            if unresolved:
                raise RemediationLedgerError(
                    "approved batch contains unresolved non-fix candidates: "
                    + ", ".join(unresolved)
                )
            planned_fingerprint_list = [
                fingerprint
                for item in plan
                for fingerprint in item.candidate_fingerprints
            ]
            planned_fingerprints = set(planned_fingerprint_list)
            if len(planned_fingerprint_list) != len(planned_fingerprints):
                raise RemediationLedgerError(
                    "actionable candidate fingerprints must be planned exactly once"
                )
            if planned_fingerprints != actionable_fingerprints:
                raise RemediationLedgerError(
                    "materialization plan must exactly cover actionable canonical candidates"
                )
            canonical_by_fingerprint = {
                item.fingerprint: item for item in canonical
            }
            for planned_task in plan:
                grouped_candidates = [
                    canonical_by_fingerprint[fingerprint]
                    for fingerprint in planned_task.candidate_fingerprints
                ]
                if len(
                    {
                        _canonical_json(item.policy_axes)
                        for item in grouped_candidates
                    }
                ) != 1:
                    raise RemediationLedgerError(
                        "one planned task cannot combine different canonical policy axes"
                    )
                expected_sources = tuple(
                    sorted(
                        {
                            source_ref
                            for candidate in grouped_candidates
                            for source_ref in candidate.source_refs
                        }
                    )
                )
                if planned_task.source_refs != expected_sources:
                    raise RemediationLedgerError(
                        "planned task source_refs do not match canonical candidates"
                    )
                contract = planned_task.task_contract
                if contract.get("remediation_round") != remediation_round:
                    raise RemediationLedgerError(
                        "planned task remediation_round does not match approved round"
                    )
                source_finding = _required_text(
                    contract.get("source_finding"), "task source_finding"
                )
                source_candidate = next(
                    (
                        candidate
                        for candidate in grouped_candidates
                        if source_finding
                        in {
                            candidate.fingerprint,
                            candidate.classification.finding_id,
                            *candidate.observation_ids,
                        }
                    ),
                    None,
                )
                if source_candidate is None:
                    raise RemediationLedgerError(
                        "planned task source_finding is not bound to its canonical candidates"
                    )
                for axis, expected in source_candidate.policy_axes.items():
                    if contract.get(axis) != expected:
                        raise RemediationLedgerError(
                            f"planned task {axis} does not match canonical classification"
                        )
            expected_approval_digest = canonical_digest(
                _approval_payload(
                    approval_ref=self.approval_ref,
                    batch_id=batch_id,
                    candidate_set_digest=self.candidate_set_digest,
                    accepted_risk_authorizations=accepted_risk_authorizations,
                    extra_round_authorization_ref=self.extra_round_authorization_ref,
                    extra_round_authorization=extra_authorization,
                    materialization_plan=plan,
                    remediation_round=remediation_round,
                    scope_digest=self.scope_digest,
                    scope_revision=scope_revision,
                )
            )
            if self.approval_digest != expected_approval_digest:
                raise RemediationLedgerError(
                    "approval_digest does not match canonical approval payload"
                )
        if status == "materializing" and materialized:
            raise RemediationLedgerError(
                "materializing batch records durable tasks only after reconcile completes"
            )
        if status in {"dispatched", "completed", "aborted"}:
            if tuple(item.task_id for item in materialized) != tuple(
                item.task_id for item in plan
            ):
                raise RemediationLedgerError(
                    "dispatched batch requires every planned task exactly once"
                )
        if status == "completed" and self.completion_outcome not in {
            "product_changed",
            "no_change",
        }:
            raise RemediationLedgerError(
                "completed batch requires product_changed or no_change outcome"
            )
        if status == "aborted" and self.completion_outcome != "aborted":
            raise RemediationLedgerError("aborted batch requires aborted outcome")
        if status not in {"completed", "aborted"} and self.completion_outcome:
            raise RemediationLedgerError(
                "nonterminal batch must not contain completion_outcome"
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "epoch_id": self.epoch_id,
            "target_sha": self.target_sha,
            "required_execution_ids": list(self.required_execution_ids),
            "terminal_execution_ids": list(self.terminal_execution_ids),
            "status": self.status,
            "observations": [item.to_record() for item in self.observations],
            "canonical_candidates": [item.to_record() for item in self.canonical_candidates],
            "classification_conflicts": list(self.classification_conflicts),
            "canonicalization_ref": self.canonicalization_ref,
            "candidate_set_digest": self.candidate_set_digest,
            "accepted_risk_authorizations": [
                item.to_record() for item in self.accepted_risk_authorizations
            ],
            "scope_digest": self.scope_digest,
            "scope_revision": self.scope_revision,
            "approval_ref": self.approval_ref,
            "approval_digest": self.approval_digest,
            "remediation_round": self.remediation_round,
            "round_consumed": self.round_consumed,
            "extra_round_authorization_ref": self.extra_round_authorization_ref,
            "extra_round_authorization": (
                self.extra_round_authorization.to_record()
                if self.extra_round_authorization is not None
                else None
            ),
            "materialization_plan": [item.to_record() for item in self.materialization_plan],
            "materialized_tasks": [item.to_record() for item in self.materialized_tasks],
            "completion_outcome": self.completion_outcome,
        }

    @classmethod
    def from_record(cls, value: Any) -> "RemediationBatch":
        fields = tuple(cls.__dataclass_fields__)
        record = _closed_record(value, "remediation batch", required=fields)
        return cls(
            **{
                **record,
                "observations": tuple(
                    CandidateObservation.from_record(item)
                    for item in record["observations"]
                ),
                "canonical_candidates": tuple(
                    CanonicalCandidate.from_record(item)
                    for item in record["canonical_candidates"]
                ),
                "accepted_risk_authorizations": tuple(
                    AcceptedRiskAuthorization.from_record(item)
                    for item in record["accepted_risk_authorizations"]
                ),
                "extra_round_authorization": (
                    ExtraRoundAuthorization.from_record(
                        record["extra_round_authorization"]
                    )
                    if record["extra_round_authorization"] is not None
                    else None
                ),
                "materialization_plan": tuple(
                    PlannedRemediationTask.from_record(item)
                    for item in record["materialization_plan"]
                ),
                "materialized_tasks": tuple(
                    MaterializedTask.from_record(item)
                    for item in record["materialized_tasks"]
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class RemediationLedger:
    max_fix_rounds: int = DEFAULT_MAX_FIX_ROUNDS
    consumed_rounds: int = 0
    consumed_extra_round_authorization_refs: tuple[str, ...] = ()
    batches: tuple[RemediationBatch, ...] = ()
    schema_revision: int = REMEDIATION_LEDGER_SCHEMA_REVISION

    def __post_init__(self) -> None:
        max_fix_rounds = _nonnegative_int(self.max_fix_rounds, "max_fix_rounds")
        if max_fix_rounds > MAX_FIX_ROUNDS:
            raise RemediationLedgerError(
                f"max_fix_rounds must not exceed {MAX_FIX_ROUNDS}"
            )
        consumed_rounds = _nonnegative_int(self.consumed_rounds, "consumed_rounds")
        refs = _text_tuple(
            self.consumed_extra_round_authorization_refs,
            "consumed_extra_round_authorization_refs",
            sort=True,
        )
        batches = tuple(sorted(self.batches, key=lambda item: item.batch_id))
        if len({item.batch_id for item in batches}) != len(batches):
            raise RemediationLedgerError("remediation batch ids must be unique")
        revision = _positive_int(self.schema_revision, "schema_revision")
        if revision != REMEDIATION_LEDGER_SCHEMA_REVISION:
            raise RemediationLedgerError(
                f"unsupported remediation ledger schema revision: {revision}"
            )
        consumed_batches = [item for item in batches if item.round_consumed]
        rounds = sorted(item.remediation_round for item in consumed_batches)
        if rounds != list(range(1, consumed_rounds + 1)):
            raise RemediationLedgerError(
                "consumed remediation rounds must be unique, contiguous, and match batches"
            )
        expected_refs = sorted(
            item.extra_round_authorization_ref
            for item in consumed_batches
            if item.extra_round_authorization_ref
        )
        if list(refs) != expected_refs:
            raise RemediationLedgerError(
                "consumed extra-round authorization refs must match approved batches"
            )
        authorizations_by_input: dict[str, ExtraRoundAuthorization] = {}
        consumed_batches_by_input: dict[str, set[str]] = {}
        for batch in consumed_batches:
            authorization = batch.extra_round_authorization
            if authorization is None:
                continue
            previous = authorizations_by_input.setdefault(
                authorization.input_id, authorization
            )
            if previous != authorization:
                raise RemediationLedgerError(
                    "one captured input must resolve to one exact extra-round authorization"
                )
            consumed_batches_by_input.setdefault(
                authorization.input_id, set()
            ).add(batch.batch_id)
        for input_id, authorization in authorizations_by_input.items():
            consumed = consumed_batches_by_input[input_id]
            if not consumed.issubset(set(authorization.remediation_batch_ids)):
                raise RemediationLedgerError(
                    "extra-round authorization was consumed by another batch"
                )
            if len(consumed) > authorization.authorized_extra_rounds:
                raise RemediationLedgerError(
                    "extra-round authorization was consumed over its count"
                )
        extra_round_count = max(0, consumed_rounds - max_fix_rounds)
        if len(refs) != extra_round_count:
            raise RemediationLedgerError(
                "every round beyond max_fix_rounds requires one unique authorization ref"
            )
        object.__setattr__(self, "max_fix_rounds", max_fix_rounds)
        object.__setattr__(self, "consumed_rounds", consumed_rounds)
        object.__setattr__(self, "consumed_extra_round_authorization_refs", refs)
        object.__setattr__(self, "batches", batches)
        object.__setattr__(self, "schema_revision", revision)

    @property
    def record_type(self) -> str:
        return "remediation_ledger"

    def batch(self, batch_id: str) -> RemediationBatch:
        normalized = _required_text(batch_id, "batch_id")
        for batch in self.batches:
            if batch.batch_id == normalized:
                return batch
        raise RemediationLedgerError(f"unknown remediation batch: {normalized}")

    def to_record(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "schema_revision": self.schema_revision,
            "max_fix_rounds": self.max_fix_rounds,
            "consumed_rounds": self.consumed_rounds,
            "consumed_extra_round_authorization_refs": list(
                self.consumed_extra_round_authorization_refs
            ),
            "batches": [item.to_record() for item in self.batches],
        }

    @classmethod
    def from_record(cls, value: Any) -> "RemediationLedger":
        record = _closed_record(
            value,
            "remediation ledger",
            required=(
                "record_type",
                "schema_revision",
                "max_fix_rounds",
                "consumed_rounds",
                "consumed_extra_round_authorization_refs",
                "batches",
            ),
        )
        if record["record_type"] != "remediation_ledger":
            raise RemediationLedgerError("invalid remediation ledger record_type")
        return cls(
            schema_revision=record["schema_revision"],
            max_fix_rounds=record["max_fix_rounds"],
            consumed_rounds=record["consumed_rounds"],
            consumed_extra_round_authorization_refs=record[
                "consumed_extra_round_authorization_refs"
            ],
            batches=tuple(RemediationBatch.from_record(item) for item in record["batches"]),
        )


@dataclass(frozen=True, slots=True)
class RepairAction:
    action: str
    task_id: str
    source_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _required_text(self.action, "action"))
        object.__setattr__(self, "task_id", _required_text(self.task_id, "task_id"))
        object.__setattr__(self, "source_ref", _optional_text(self.source_ref, "source_ref"))

    def to_record(self) -> dict[str, str]:
        return {"action": self.action, "task_id": self.task_id, "source_ref": self.source_ref}


@dataclass(frozen=True, slots=True)
class MaterializationReconcileResult:
    status: str
    ledger: RemediationLedger
    batch_id: str
    repair_actions: tuple[RepairAction, ...] = ()
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in RECONCILE_STATUSES:
            raise RemediationLedgerError(f"unsupported reconcile status: {self.status}")
        object.__setattr__(self, "batch_id", _required_text(self.batch_id, "batch_id"))
        object.__setattr__(
            self,
            "repair_actions",
            tuple(
                sorted(
                    self.repair_actions,
                    key=lambda item: (item.task_id, item.action, item.source_ref),
                )
            ),
        )
        object.__setattr__(self, "issues", tuple(sorted(set(self.issues))))
        if self.status == "repair_required" and not self.repair_actions:
            raise RemediationLedgerError("repair_required result needs repair actions")
        if self.status == "remediation_reconcile_required" and not self.issues:
            raise RemediationLedgerError("reconcile-required result needs issues")
        if self.status == "dispatched" and (self.repair_actions or self.issues):
            raise RemediationLedgerError("dispatched result must be clean")

    def require_dispatched(self) -> RemediationLedger:
        if self.status != "dispatched":
            detail = "; ".join(self.issues) or ", ".join(
                action.action for action in self.repair_actions
            )
            raise RemediationReconcileRequired(detail)
        return self.ledger


def _replace_batch(ledger: RemediationLedger, updated: RemediationBatch) -> RemediationLedger:
    if updated.batch_id not in {item.batch_id for item in ledger.batches}:
        raise RemediationLedgerError(f"unknown remediation batch: {updated.batch_id}")
    return replace(
        ledger,
        batches=tuple(
            updated if item.batch_id == updated.batch_id else item
            for item in ledger.batches
        ),
    )


def create_remediation_batch(
    ledger: RemediationLedger,
    *,
    batch_id: str,
    epoch_id: str,
    target_sha: str,
    required_execution_ids: Sequence[str],
) -> RemediationLedger:
    proposed = RemediationBatch(
        batch_id=batch_id,
        epoch_id=epoch_id,
        target_sha=target_sha,
        required_execution_ids=tuple(required_execution_ids),
    )
    for current in ledger.batches:
        if current.batch_id != proposed.batch_id:
            continue
        if current == proposed:
            return ledger
        raise RemediationLedgerError(
            f"remediation batch {batch_id} already exists with different identity"
        )
    return replace(ledger, batches=ledger.batches + (proposed,))


def _group_observations(
    observations: Sequence[CandidateObservation],
) -> dict[str, tuple[CandidateObservation, ...]]:
    groups: dict[str, list[CandidateObservation]] = {}
    for observation in observations:
        groups.setdefault(observation.fingerprint, []).append(observation)
    return {
        fingerprint: tuple(sorted(items, key=lambda item: item.observation_id))
        for fingerprint, items in groups.items()
    }


def classification_conflicts(
    observations: Sequence[CandidateObservation],
) -> tuple[str, ...]:
    conflicts = []
    for fingerprint, group in _group_observations(observations).items():
        axes = {_canonical_json(item.policy_axes) for item in group}
        if len(axes) > 1:
            conflicts.append(fingerprint)
    return tuple(sorted(conflicts))


def register_candidate(
    ledger: RemediationLedger,
    batch_id: str,
    candidate: CandidateObservation | Mapping[str, Any],
) -> RemediationLedger:
    batch = ledger.batch(batch_id)
    if batch.status not in {"candidate_registered", "classification_conflict"}:
        raise RemediationLedgerError(
            f"cannot register candidate while batch is {batch.status}"
        )
    normalized = (
        candidate
        if isinstance(candidate, CandidateObservation)
        else CandidateObservation.from_record(candidate)
    )
    if normalized.target_sha != batch.target_sha:
        raise RemediationLedgerError("candidate target_sha does not match batch target_sha")
    if normalized.source_execution_id not in batch.required_execution_ids:
        raise RemediationLedgerError("candidate source execution is not required by the batch")
    for existing in batch.observations:
        if existing.observation_id != normalized.observation_id:
            if (
                existing.source_execution_id == normalized.source_execution_id
                and existing.source_candidate_id == normalized.source_candidate_id
            ):
                raise RemediationLedgerError(
                    "source execution/candidate identity was registered under "
                    "a different observation_id"
                )
            continue
        if existing == normalized:
            return ledger
        raise RemediationLedgerError(
            f"candidate observation {normalized.observation_id} changed on retry"
        )
    observations = tuple(
        sorted(
            batch.observations + (normalized,),
            key=lambda item: item.observation_id,
        )
    )
    conflicts = classification_conflicts(observations)
    updated = replace(
        batch,
        status="classification_conflict" if conflicts else "candidate_registered",
        observations=observations,
        classification_conflicts=conflicts,
    )
    return _replace_batch(ledger, updated)


def _canonical_candidates(
    observations: Sequence[CandidateObservation],
    canonical_classifications: Mapping[
        str, FindingDisposition | Mapping[str, Any]
    ] | None,
) -> tuple[tuple[CanonicalCandidate, ...], tuple[str, ...]]:
    explicit = canonical_classifications or {}
    unknown = sorted(set(explicit) - set(_group_observations(observations)))
    if unknown:
        raise RemediationLedgerError(
            "canonical classifications reference unknown fingerprints: " + ", ".join(unknown)
        )
    canonical: list[CanonicalCandidate] = []
    unresolved: list[str] = []
    for fingerprint, group in sorted(_group_observations(observations).items()):
        axes = {_canonical_json(item.policy_axes) for item in group}
        if fingerprint in explicit:
            chosen = _normalize_disposition(explicit[fingerprint])
            if chosen.fingerprint and chosen.fingerprint != fingerprint:
                raise RemediationLedgerError("canonical classification fingerprint mismatch")
            if chosen.target_sha and chosen.target_sha != group[0].target_sha:
                raise RemediationLedgerError("canonical classification target_sha mismatch")
        elif len(axes) == 1:
            chosen = group[0].classification
        else:
            unresolved.append(fingerprint)
            continue
        why_fix_now = chosen.why_fix_now or next(
            (item.why_fix_now for item in group if item.why_fix_now), ""
        )
        if not why_fix_now:
            raise RemediationLedgerError(
                f"canonical candidate {fingerprint} requires why_fix_now evidence"
            )
        canonical.append(
            CanonicalCandidate(
                fingerprint=fingerprint,
                target_sha=group[0].target_sha,
                classification=chosen,
                observation_ids=tuple(item.observation_id for item in group),
                source_refs=tuple(sorted({item.source_ref for item in group})),
                source_execution_ids=tuple(
                    sorted({item.source_execution_id for item in group})
                ),
                requirement_refs=tuple(
                    sorted(
                        {
                            reference
                            for item in group
                            for reference in item.requirement_refs
                        }
                    )
                ),
                scope_refs=tuple(
                    sorted({reference for item in group for reference in item.scope_refs})
                ),
                why_fix_now=why_fix_now,
            )
        )
    return tuple(canonical), tuple(unresolved)


def _accepted_risk_authorizations(
    canonical_candidates: Sequence[CanonicalCandidate],
    authorizations: Sequence[AcceptedRiskAuthorization | Mapping[str, Any]],
) -> tuple[AcceptedRiskAuthorization, ...]:
    normalized = tuple(
        sorted(
            (
                item
                if isinstance(item, AcceptedRiskAuthorization)
                else AcceptedRiskAuthorization.from_record(item)
                for item in authorizations
            ),
            key=lambda item: (item.fingerprint, item.decision_id),
        )
    )
    risk_candidates = tuple(
        item
        for item in canonical_candidates
        if item.classification.disposition == "accepted_risk"
    )
    if len(normalized) != len(risk_candidates):
        raise RemediationLedgerError(
            "accepted-risk authorizations must exactly match decision, fingerprint, and target"
        )
    for candidate in risk_candidates:
        matches = tuple(
            authorization
            for authorization in normalized
            if authorization.fingerprint == candidate.fingerprint
            and authorization.target_sha == candidate.target_sha
        )
        expected_decision = candidate.classification.accepted_risk_decision_id
        if len(matches) != 1 or (
            expected_decision and matches[0].decision_id != expected_decision
        ):
            raise RemediationLedgerError(
                "accepted-risk authorizations must exactly match decision, fingerprint, and target"
            )
    return normalized


def mark_ready_to_triage(
    ledger: RemediationLedger,
    batch_id: str,
    *,
    terminal_execution_ids: Sequence[str],
    canonical_classifications: Mapping[
        str, FindingDisposition | Mapping[str, Any]
    ] | None = None,
    canonicalization_ref: str = "",
    accepted_risk_authorizations: Sequence[
        AcceptedRiskAuthorization | Mapping[str, Any]
    ] = (),
) -> RemediationLedger:
    batch = ledger.batch(batch_id)
    if batch.status == "ready_to_triage":
        # A replay must reproduce the complete canonical record, not merely the status.
        proposed, unresolved = _canonical_candidates(
            batch.observations, canonical_classifications
        )
        if unresolved:
            raise CandidateClassificationConflict(
                "unresolved classification conflicts: " + ", ".join(unresolved)
            )
        risk_authorizations = _accepted_risk_authorizations(
            proposed, accepted_risk_authorizations
        )
        if (
            tuple(sorted(terminal_execution_ids)) == batch.terminal_execution_ids
            and proposed == batch.canonical_candidates
            and _optional_text(canonicalization_ref, "canonicalization_ref")
            == batch.canonicalization_ref
            and risk_authorizations == batch.accepted_risk_authorizations
        ):
            return ledger
        raise RemediationLedgerError("ready-to-triage retry changed canonical batch state")
    if batch.status not in {"candidate_registered", "classification_conflict"}:
        raise RemediationLedgerError(f"cannot mark batch ready while it is {batch.status}")
    terminal = _text_tuple(
        terminal_execution_ids, "terminal_execution_ids", sort=True
    )
    if terminal != batch.required_execution_ids:
        missing = sorted(set(batch.required_execution_ids) - set(terminal))
        unknown = sorted(set(terminal) - set(batch.required_execution_ids))
        detail = []
        if missing:
            detail.append("missing terminal executions: " + ", ".join(missing))
        if unknown:
            detail.append("unknown terminal executions: " + ", ".join(unknown))
        raise RemediationLedgerError("; ".join(detail))
    if not batch.observations:
        raise RemediationLedgerError("cannot triage an empty remediation batch")
    canonical, unresolved = _canonical_candidates(
        batch.observations, canonical_classifications
    )
    if unresolved:
        raise CandidateClassificationConflict(
            "unresolved classification conflicts: " + ", ".join(unresolved)
        )
    risk_authorizations = _accepted_risk_authorizations(
        canonical, accepted_risk_authorizations
    )
    original_conflicts = classification_conflicts(batch.observations)
    grouped = _group_observations(batch.observations)
    classification_overridden = any(
        _canonical_json(item.policy_axes)
        not in {
            _canonical_json(observation.policy_axes)
            for observation in grouped[item.fingerprint]
        }
        for item in canonical
    )
    canonical_ref = _optional_text(canonicalization_ref, "canonicalization_ref")
    if (original_conflicts or classification_overridden) and not canonical_ref:
        raise RemediationLedgerError(
            "resolving or overriding canonical classification requires canonicalization_ref"
        )
    updated = replace(
        batch,
        status="ready_to_triage",
        terminal_execution_ids=terminal,
        canonical_candidates=canonical,
        classification_conflicts=(),
        canonicalization_ref=canonical_ref,
        candidate_set_digest=canonical_digest([item.to_record() for item in canonical]),
        accepted_risk_authorizations=risk_authorizations,
    )
    return _replace_batch(ledger, updated)


def deterministic_task_ids(
    candidate_groups: Sequence[Sequence[str]], *, first_task_number: int
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    first = _positive_int(first_task_number, "first_task_number")
    normalized_groups = []
    seen: set[str] = set()
    for group in candidate_groups:
        fingerprints = _text_tuple(group, "candidate_group", sort=True)
        if not fingerprints:
            raise RemediationLedgerError("candidate groups must not be empty")
        for fingerprint in fingerprints:
            _digest(fingerprint, "candidate_group")
            if fingerprint in seen:
                raise RemediationLedgerError(
                    "candidate fingerprint appears in more than one task group"
                )
            seen.add(fingerprint)
        normalized_groups.append(fingerprints)
    ordered = sorted(normalized_groups)
    return tuple(
        (f"T{first + offset:03d}", group)
        for offset, group in enumerate(ordered)
    )


def _candidate_inventory_record(
    candidates: Sequence[CanonicalCandidate], scope_revision: int
) -> dict[str, Any]:
    if not candidates:
        raise RemediationLedgerError("candidate inventory group must not be empty")
    policy_axes = {_canonical_json(candidate.policy_axes) for candidate in candidates}
    if len(policy_axes) != 1:
        raise RemediationLedgerError(
            "one remediation task cannot combine candidates with different policy axes"
        )
    target_shas = {candidate.target_sha for candidate in candidates}
    if len(target_shas) != 1:
        raise RemediationLedgerError(
            "one remediation task cannot combine candidates from different target SHAs"
        )
    return {
        "confirmed": True,
        "target_sha": candidates[0].target_sha,
        "scope_revision": scope_revision,
        "policy_axes_explicit": True,
        "policy_axes": candidates[0].policy_axes,
        "requirement_refs": sorted(
            {
                reference
                for candidate in candidates
                for reference in candidate.requirement_refs
            }
        ),
        "scope_refs": sorted(
            {
                reference
                for candidate in candidates
                for reference in candidate.scope_refs
            }
        ),
    }


def _plan_tasks(
    batch: RemediationBatch,
    *,
    task_contracts: Sequence[Mapping[str, Any]],
    candidate_groups: Sequence[Sequence[str]] | None,
    first_task_number: int,
    remediation_round: int,
    scope_revision: int,
    release_scope: ReleaseScope | Mapping[str, Any],
) -> tuple[PlannedRemediationTask, ...]:
    all_candidates = {
        item.fingerprint: item for item in batch.canonical_candidates
    }
    unresolved = tuple(
        candidate.fingerprint
        for candidate in all_candidates.values()
        if not _is_materializable_candidate(candidate)
        and candidate.classification.disposition not in NON_BLOCKING_DISPOSITIONS
    )
    if unresolved:
        raise RemediationLedgerError(
            "only terminal non-blocking outcomes may be excluded from materialization: "
            + ", ".join(unresolved)
        )
    candidates = {
        fingerprint: candidate
        for fingerprint, candidate in all_candidates.items()
        if _is_materializable_candidate(candidate)
    }
    if not candidates:
        raise RemediationLedgerError(
            "remediation approval requires at least one actionable fix_now candidate"
        )
    for candidate in all_candidates.values():
        if (
            not _is_materializable_candidate(candidate)
            and candidate.classification.release_effect != "non_blocking"
        ):
            raise RemediationLedgerError(
                "non-materialized remediation candidates must be terminal and non-blocking"
            )
    groups = candidate_groups or tuple((fingerprint,) for fingerprint in candidates)
    allocations = deterministic_task_ids(groups, first_task_number=first_task_number)
    grouped_fingerprints = {fingerprint for _, group in allocations for fingerprint in group}
    if grouped_fingerprints != set(candidates):
        missing = sorted(set(candidates) - grouped_fingerprints)
        unknown = sorted(grouped_fingerprints - set(candidates))
        parts = []
        if missing:
            parts.append("unplanned candidates: " + ", ".join(missing))
        if unknown:
            parts.append("unknown planned candidates: " + ", ".join(unknown))
        raise RemediationLedgerError("; ".join(parts))
    contracts_by_id: dict[str, Mapping[str, Any]] = {}
    for contract in task_contracts:
        normalized = _record(contract, "task_contract")
        task_id = _required_text(normalized.get("id"), "task contract id")
        if task_id in contracts_by_id:
            raise RemediationLedgerError(f"duplicate task contract id: {task_id}")
        contracts_by_id[task_id] = normalized
    expected_ids = {task_id for task_id, _ in allocations}
    if set(contracts_by_id) != expected_ids:
        raise RemediationLedgerError(
            "task contracts must exactly match deterministic task ids: "
            + ", ".join(sorted(expected_ids))
        )

    planned = []
    for task_id, fingerprints in allocations:
        contract = contracts_by_id[task_id]
        if contract.get("remediation_round") != remediation_round:
            raise RemediationLedgerError(
                f"task {task_id} remediation_round does not match the approved round"
            )
        source_finding = _required_text(
            contract.get("source_finding"), "task source_finding"
        )
        grouped = [candidates[fingerprint] for fingerprint in fingerprints]
        accepted_finding_ids = {
            identifier
            for candidate in grouped
            for identifier in (
                candidate.fingerprint,
                candidate.classification.finding_id,
                *candidate.observation_ids,
            )
            if identifier
        }
        if source_finding not in accepted_finding_ids:
            raise RemediationLedgerError(
                f"task {task_id} source_finding is not in its canonical candidate group"
            )
        source_candidate = next(
            candidate
            for candidate in grouped
            if source_finding
            in {
                candidate.fingerprint,
                candidate.classification.finding_id,
                *candidate.observation_ids,
            }
        )
        inventory_record = _candidate_inventory_record(grouped, scope_revision)
        try:
            authorize_task_creation(
                contract,
                release_scope,
                finding_records={source_finding: inventory_record},
                current_target_sha=batch.target_sha,
            )
        except TaskAuthorizationError as exc:
            raise RemediationLedgerError(
                f"task {task_id} failed release-scope authorization: {exc}"
            ) from exc
        for axis, expected in source_candidate.policy_axes.items():
            if contract.get(axis) != expected:
                raise RemediationLedgerError(
                    f"task {task_id} {axis} does not match canonical classification"
                )
        source_refs = tuple(
            sorted({source for candidate in grouped for source in candidate.source_refs})
        )
        planned.append(
            PlannedRemediationTask(
                task_id=task_id,
                candidate_fingerprints=fingerprints,
                task_contract=contract,
                source_refs=source_refs,
            )
        )
    return tuple(planned)


def approve_remediation_batch(
    ledger: RemediationLedger,
    batch_id: str,
    *,
    approval_ref: str,
    scope_digest: str,
    scope_revision: int,
    task_contracts: Sequence[Mapping[str, Any]],
    first_task_number: int,
    release_scope: ReleaseScope | Mapping[str, Any],
    candidate_groups: Sequence[Sequence[str]] | None = None,
    extra_round_authorization_ref: str = "",
    extra_round_authorization: ExtraRoundAuthorization | Mapping[str, Any] | None = None,
    captured_input_ids: Sequence[str] = (),
) -> RemediationLedger:
    batch = ledger.batch(batch_id)
    approval_ref = _required_text(approval_ref, "approval_ref")
    scope_digest = _digest(scope_digest, "scope_digest")
    scope_revision = _positive_int(scope_revision, "scope_revision")
    extra_ref = _optional_text(
        extra_round_authorization_ref, "extra_round_authorization_ref"
    )
    if extra_round_authorization is None:
        extra_authorization = None
    elif isinstance(extra_round_authorization, ExtraRoundAuthorization):
        extra_authorization = extra_round_authorization
    else:
        extra_authorization = ExtraRoundAuthorization.from_record(
            extra_round_authorization
        )
    captured_inputs = _text_tuple(
        captured_input_ids, "captured_input_ids", sort=True
    )
    if bool(extra_ref) != bool(extra_authorization):
        raise RemediationLedgerError(
            "extra-round authorization ref and record must be supplied together"
        )
    if extra_authorization is not None:
        if extra_authorization.ref_for(batch.batch_id) != extra_ref:
            raise RemediationLedgerError(
                "extra-round authorization ref is not exact for this batch"
            )
        if extra_authorization.input_id not in captured_inputs:
            raise RemediationLedgerError(
                "extra-round authorization input is not a captured input"
            )
        for approved_batch in ledger.batches:
            approved_authorization = approved_batch.extra_round_authorization
            if (
                approved_authorization is not None
                and approved_authorization.input_id == extra_authorization.input_id
                and approved_authorization != extra_authorization
            ):
                raise RemediationLedgerError(
                    "captured extra-round authorization changed across batches"
                )

    if isinstance(release_scope, ReleaseScope):
        normalized_scope = release_scope
    else:
        try:
            normalized_scope = ReleaseScope.from_record(release_scope)
        except ReleaseScopeError as exc:
            raise RemediationLedgerError(f"invalid release scope: {exc}") from exc
    if normalized_scope.status != "locked":
        raise RemediationLedgerError("approval requires a locked release scope")
    if normalized_scope.scope_digest != scope_digest:
        raise RemediationLedgerError("approval scope_digest is stale")
    if normalized_scope.scope_revision != scope_revision:
        raise RemediationLedgerError("approval scope_revision is stale")

    retry = batch.status in {"materializing", "dispatched", "completed", "aborted"}
    if batch.status != "ready_to_triage" and not retry:
        raise RemediationLedgerError(f"cannot approve batch while it is {batch.status}")
    round_number = batch.remediation_round if retry else ledger.consumed_rounds + 1
    if not retry:
        if ledger.consumed_rounds >= ledger.max_fix_rounds:
            if not extra_ref:
                unresolved = ", ".join(
                    item.fingerprint for item in batch.canonical_candidates
                )
                raise RemediationRoundLimitExceeded(
                    "max_fix_rounds reached; unresolved candidates: " + unresolved
                )
            if extra_ref in ledger.consumed_extra_round_authorization_refs:
                raise RemediationRoundLimitExceeded(
                    "extra-round authorization ref was already consumed"
                )
        elif extra_ref:
            raise RemediationLedgerError(
                "extra-round authorization cannot be consumed before the automatic limit"
            )

    plan = _plan_tasks(
        batch,
        task_contracts=task_contracts,
        candidate_groups=candidate_groups,
        first_task_number=first_task_number,
        remediation_round=round_number,
        scope_revision=scope_revision,
        release_scope=normalized_scope,
    )
    approval_payload = _approval_payload(
        approval_ref=approval_ref,
        batch_id=batch.batch_id,
        candidate_set_digest=batch.candidate_set_digest,
        accepted_risk_authorizations=batch.accepted_risk_authorizations,
        extra_round_authorization_ref=extra_ref,
        extra_round_authorization=extra_authorization,
        materialization_plan=plan,
        remediation_round=round_number,
        scope_digest=scope_digest,
        scope_revision=scope_revision,
    )
    approval_digest = canonical_digest(approval_payload)
    if retry:
        if (
            batch.approval_ref == approval_ref
            and batch.approval_digest == approval_digest
            and batch.scope_digest == scope_digest
            and batch.scope_revision == scope_revision
            and batch.remediation_round == round_number
            and batch.extra_round_authorization_ref == extra_ref
            and batch.extra_round_authorization == extra_authorization
            and batch.materialization_plan == plan
        ):
            return ledger
        raise RemediationApprovalConflict(
            "approved remediation batch was retried with different semantics"
        )

    updated = replace(
        batch,
        status="materializing",
        scope_digest=scope_digest,
        scope_revision=scope_revision,
        approval_ref=approval_ref,
        approval_digest=approval_digest,
        remediation_round=round_number,
        round_consumed=True,
        extra_round_authorization_ref=extra_ref,
        extra_round_authorization=extra_authorization,
        materialization_plan=plan,
    )
    refs = ledger.consumed_extra_round_authorization_refs
    if extra_ref:
        refs = refs + (extra_ref,)
    # Approval is one atomic pure transition: constructing an intermediate
    # ledger with a consumed batch but the old counter would itself be an
    # invalid persisted state.
    updated_batches = tuple(
        updated if item.batch_id == updated.batch_id else item
        for item in ledger.batches
    )
    return replace(
        ledger,
        batches=updated_batches,
        consumed_rounds=round_number,
        consumed_extra_round_authorization_refs=refs,
    )


def reconcile_materialization(
    ledger: RemediationLedger,
    batch_id: str,
    observations: Sequence[TaskMaterializationObservation | Mapping[str, Any]],
) -> MaterializationReconcileResult:
    batch = ledger.batch(batch_id)
    # Rehydrate through the strict parser on every recovery entry.  This
    # recomputes candidate, authorization, plan, path, and approval bindings
    # instead of trusting an object retained across a crash boundary.
    restored_batch = RemediationBatch.from_record(batch.to_record())
    if restored_batch != batch:
        raise RemediationLedgerError(
            "persisted remediation batch changed during approval revalidation"
        )
    if batch.status not in {"materializing", "dispatched"}:
        raise RemediationLedgerError(
            f"cannot reconcile materialization while batch is {batch.status}"
        )
    normalized = tuple(
        item
        if isinstance(item, TaskMaterializationObservation)
        else TaskMaterializationObservation.from_record(item)
        for item in observations
    )
    by_id: dict[str, TaskMaterializationObservation] = {}
    for item in normalized:
        if item.task_id in by_id:
            raise RemediationLedgerError(
                f"duplicate materialization observation: {item.task_id}"
            )
        by_id[item.task_id] = item
    expected_ids = {item.task_id for item in batch.materialization_plan}
    issues = [
        f"unexpected task {task_id} is associated with remediation batch {batch_id}"
        for task_id in sorted(set(by_id) - expected_ids)
    ]
    repairs: list[RepairAction] = []
    materialized: list[MaterializedTask] = []
    for planned in batch.materialization_plan:
        observed = by_id.get(planned.task_id)
        if observed is None:
            repairs.extend(
                (
                    RepairAction("write_state_projection", planned.task_id),
                    RepairAction("write_task_artifact", planned.task_id),
                    *(
                        RepairAction("link_source_created_fix_task", planned.task_id, source)
                        for source in planned.source_refs
                    ),
                )
            )
            continue
        state_digest = ""
        if observed.state_task_contract is None:
            repairs.append(RepairAction("write_state_projection", planned.task_id))
        else:
            state_digest = canonical_digest(_thaw_json(observed.state_task_contract))
            if state_digest != planned.task_contract_digest:
                issues.append(
                    f"task {planned.task_id} STATE projection conflicts with write-ahead contract"
                )
        artifact_contract_digest = ""
        if observed.artifact_task_contract is None:
            if observed.artifact_digest:
                issues.append(
                    f"task {planned.task_id} has artifact digest without a readable task contract"
                )
            else:
                repairs.append(RepairAction("write_task_artifact", planned.task_id))
        else:
            artifact_contract_digest = canonical_digest(
                _thaw_json(observed.artifact_task_contract)
            )
            if artifact_contract_digest != planned.task_contract_digest:
                issues.append(
                    f"task {planned.task_id} artifact conflicts with write-ahead contract"
                )
            if not observed.artifact_digest:
                repairs.append(RepairAction("record_task_artifact_digest", planned.task_id))
        expected_sources = set(planned.source_refs)
        observed_sources = set(observed.source_refs)
        for source in sorted(expected_sources - observed_sources):
            repairs.append(
                RepairAction("link_source_created_fix_task", planned.task_id, source)
            )
        for source in sorted(observed_sources - expected_sources):
            issues.append(
                f"task {planned.task_id} has unexpected source created_fix_tasks link {source}"
            )
        if (
            state_digest == planned.task_contract_digest
            and artifact_contract_digest == planned.task_contract_digest
            and observed.artifact_digest
            and observed_sources == expected_sources
        ):
            materialized.append(
                MaterializedTask(
                    task_id=planned.task_id,
                    state_contract_digest=state_digest,
                    artifact_contract_digest=artifact_contract_digest,
                    artifact_digest=observed.artifact_digest,
                    source_refs=observed.source_refs,
                )
            )

    if batch.status == "dispatched" and repairs:
        issues.extend(
            f"dispatched task {action.task_id} lost durable {action.action} evidence"
            for action in repairs
        )
        repairs = []
    if batch.status == "dispatched" and not issues:
        expected_materialized = tuple(batch.materialized_tasks)
        observed_materialized = tuple(
            sorted(materialized, key=lambda item: item.task_id)
        )
        if observed_materialized != expected_materialized:
            issues.append(
                "dispatched materialized-task evidence differs from the persisted ledger"
            )
    if issues:
        return MaterializationReconcileResult(
            status="remediation_reconcile_required",
            ledger=ledger,
            batch_id=batch_id,
            issues=tuple(issues),
        )
    if repairs:
        return MaterializationReconcileResult(
            status="repair_required",
            ledger=ledger,
            batch_id=batch_id,
            repair_actions=tuple(repairs),
        )
    updated = replace(
        batch,
        status="dispatched",
        materialized_tasks=tuple(materialized),
    )
    reconciled = _replace_batch(ledger, updated)
    return MaterializationReconcileResult(
        status="dispatched", ledger=reconciled, batch_id=batch_id
    )


def complete_remediation_batch(
    ledger: RemediationLedger, batch_id: str, *, outcome: str
) -> RemediationLedger:
    batch = ledger.batch(batch_id)
    normalized = _required_text(outcome, "outcome")
    if normalized not in COMPLETION_OUTCOMES:
        raise RemediationLedgerError(f"unsupported completion outcome: {normalized}")
    target_status = "aborted" if normalized == "aborted" else "completed"
    if batch.status in {"completed", "aborted"}:
        if batch.status == target_status and batch.completion_outcome == normalized:
            return ledger
        raise RemediationLedgerError("terminal remediation outcome changed on retry")
    if batch.status != "dispatched":
        raise RemediationLedgerError(
            f"cannot complete remediation batch while it is {batch.status}"
        )
    # The round remains consumed for no_change and aborted outcomes by policy.
    return _replace_batch(
        ledger,
        replace(batch, status=target_status, completion_outcome=normalized),
    )


# Descriptive aliases for the later CLI integration.
seal_candidates_for_triage = mark_ready_to_triage
materialize_or_reconcile = reconcile_materialization


__all__ = [
    "AcceptedRiskAuthorization",
    "BATCH_STATUSES",
    "COMPLETION_OUTCOMES",
    "CandidateClassificationConflict",
    "CandidateObservation",
    "CanonicalCandidate",
    "DEFAULT_MAX_FIX_ROUNDS",
    "ExtraRoundAuthorization",
    "MAX_FIX_ROUNDS",
    "MaterializationReconcileResult",
    "MaterializedTask",
    "POLICY_AXES",
    "PlannedRemediationTask",
    "REMEDIATION_LEDGER_SCHEMA_REVISION",
    "RECONCILE_STATUSES",
    "RemediationApprovalConflict",
    "RemediationBatch",
    "RemediationCandidate",
    "RemediationLedger",
    "RemediationLedgerError",
    "RemediationReconcileRequired",
    "RemediationRoundLimitExceeded",
    "RepairAction",
    "SOURCE_KINDS",
    "TaskMaterializationObservation",
    "TaskMaterializationPlan",
    "approve_remediation_batch",
    "canonical_digest",
    "classification_conflicts",
    "complete_remediation_batch",
    "create_remediation_batch",
    "deterministic_task_ids",
    "mark_ready_to_triage",
    "materialize_or_reconcile",
    "reconcile_materialization",
    "register_candidate",
    "seal_candidates_for_triage",
]
