"""Pure Worker candidate, seal, and Patch Review contracts for HLoop 0.5.3.

The runtime owns Git, files, panes, credentials, and state transactions.  This
module deliberately owns none of them.  It validates the immutable records at
the boundary between Worker implementation, candidate sealing, task-local
Patch Review, and final result projection.

An implementation candidate records an exact Git tree rather than its own
commit SHA: a JSON artifact cannot safely contain the SHA of the commit that
also contains that artifact.  After commit-mode or handoff-mode sealing, a
``CandidateSeal`` binds the artifact bytes and tree to the exact candidate
commit.  Patch Review is always bound to that seal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Any

from .task_contract import V053_CONTRACT_SCHEMA_REVISION, validate_result_contract


DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
GIT_OBJECT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
TASK_ID_PATTERN = r"^T[0-9]{3}$"
ATTEMPT_ID_PATTERN = r"^T[0-9]{3}-A[0-9]{3}$"
REVIEW_ATTEMPT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"

COMPLETION_MODES = frozenset({"commit", "handoff"})
PATCH_REVIEW_VERDICTS = frozenset({"passed", "fix_required"})
PATCH_REVIEW_ACTIONS = frozenset(
    {
        "patch_review_pending",
        "patch_fix_running",
        "finalize_allowed",
        "candidate_resubmission_required",
        "user_decision_required",
    }
)
DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK = 2
MAX_PATCH_REVIEW_ROUNDS_PER_TASK = DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK

_DIGEST_RE = re.compile(DIGEST_PATTERN)
_GIT_OBJECT_RE = re.compile(GIT_OBJECT_PATTERN)
_TASK_ID_RE = re.compile(TASK_ID_PATTERN)
_ATTEMPT_ID_RE = re.compile(ATTEMPT_ID_PATTERN)
_REVIEW_ATTEMPT_ID_RE = re.compile(REVIEW_ATTEMPT_ID_PATTERN)

IMPLEMENTATION_CANDIDATE_FIELDS = frozenset(
    {
        "record_type",
        "run_id",
        "skill_version",
        "contract_schema_revision",
        "task_id",
        "attempt_id",
        "task_contract_digest",
        "semantic_ack_event_id",
        "base_sha",
        "candidate_revision",
        "completion_mode",
        "lifecycle_status",
        "candidate_tree_sha",
        "candidate_artifact_ref",
        "changed_files",
        "validation_commands",
        "validation_results",
        "validation_summary",
        "invariant_evidence",
        "regression_evidence",
        "self_review_summary",
        "residual_risks",
        "unrun_checks",
        "merge_ready",
        "terminal_result_emitted",
        "completion_sentinel_emitted",
    }
)
CANDIDATE_SEAL_FIELDS = frozenset(
    {
        "record_type",
        "run_id",
        "skill_version",
        "contract_schema_revision",
        "task_id",
        "attempt_id",
        "task_contract_digest",
        "semantic_ack_event_id",
        "base_sha",
        "candidate_revision",
        "completion_mode",
        "lifecycle_status",
        "candidate_tree_sha",
        "candidate_sha",
        "candidate_artifact_ref",
        "candidate_artifact_digest",
        "merge_ready",
        "terminal_result_emitted",
        "completion_sentinel_emitted",
        "manager_validation_recorded",
    }
)
PATCH_REVIEW_FIELDS = frozenset(
    {
        "record_type",
        "run_id",
        "skill_version",
        "contract_schema_revision",
        "task_id",
        "attempt_id",
        "task_contract_digest",
        "semantic_ack_event_id",
        "base_sha",
        "candidate_revision",
        "candidate_sha",
        "candidate_artifact_ref",
        "candidate_artifact_digest",
        "review_attempt_id",
        "review_round",
        "reviewer_provider",
        "reviewer_model",
        "reviewer_effort",
        "verdict",
        "unresolved_finding_fingerprints",
        "follow_up_finding_fingerprints",
        "automatic_task_ids",
    }
)


class WorkerCandidateError(ValueError):
    """Raised when candidate or Patch Review evidence fails closed."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerCandidateError(f"{field_name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkerCandidateError(f"{field_name} must be a positive integer")
    return value


def _patch_review_round_limit(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_PATCH_REVIEW_ROUNDS_PER_TASK
    ):
        raise WorkerCandidateError(
            "max_rounds must be an integer between 0 and "
            f"{MAX_PATCH_REVIEW_ROUNDS_PER_TASK}"
        )
    return value


def _items(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkerCandidateError(f"{field_name} must be an array")
    return tuple(value)


def _text_tuple(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool,
    unique: bool = True,
) -> tuple[str, ...]:
    normalized = tuple(
        _required_text(item, field_name) for item in _items(value, field_name)
    )
    if not allow_empty and not normalized:
        raise WorkerCandidateError(f"{field_name} must not be empty")
    if unique and len(set(normalized)) != len(normalized):
        raise WorkerCandidateError(f"{field_name} must not contain duplicates")
    return normalized


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerCandidateError(f"{field_name} must be an object")
    return value


def _exact_fields(
    record: Mapping[str, Any], fields: frozenset[str], field_name: str
) -> None:
    missing = sorted(fields - set(record))
    unknown = sorted(
        (field for field in record if field not in fields), key=lambda item: str(item)
    )
    if missing:
        raise WorkerCandidateError(
            f"{field_name} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise WorkerCandidateError(
            f"{field_name} contains unknown fields: {', '.join(unknown)}"
        )


def _digest(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _DIGEST_RE.fullmatch(text):
        raise WorkerCandidateError(
            f"{field_name} must be a lowercase labelled SHA-256 digest"
        )
    return text


def _git_object(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _GIT_OBJECT_RE.fullmatch(text):
        raise WorkerCandidateError(
            f"{field_name} must be an exact lowercase Git object ID"
        )
    return text


def _task_attempt(task_id: Any, attempt_id: Any) -> tuple[str, str]:
    task = _required_text(task_id, "task_id")
    attempt = _required_text(attempt_id, "attempt_id")
    if not _TASK_ID_RE.fullmatch(task):
        raise WorkerCandidateError("task_id must match T followed by exactly 3 digits")
    if not _ATTEMPT_ID_RE.fullmatch(attempt) or not attempt.startswith(f"{task}-A"):
        raise WorkerCandidateError("attempt_id must belong to task_id")
    return task, attempt


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
        raise WorkerCandidateError(
            f"value is not canonically JSON serializable: {exc}"
        ) from exc


def canonical_digest(value: Any) -> str:
    """Return a stable digest for a canonical JSON value."""

    return digest_bytes(canonical_json(value).encode("utf-8"))


def digest_bytes(payload: bytes) -> str:
    """Digest exact artifact bytes without performing filesystem I/O."""

    if not isinstance(payload, bytes):
        raise WorkerCandidateError("artifact payload must be bytes")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ImplementationCandidate:
    """A nonterminal Worker submission pinned to an exact Git tree."""

    run_id: str
    skill_version: str
    task_id: str
    attempt_id: str
    task_contract_digest: str
    semantic_ack_event_id: str
    base_sha: str
    candidate_revision: int
    completion_mode: str
    candidate_tree_sha: str
    candidate_artifact_ref: str
    changed_files: tuple[str, ...]
    validation_commands: tuple[str, ...]
    validation_results: tuple[str, ...]
    validation_summary: str
    invariant_evidence: tuple[str, ...]
    regression_evidence: tuple[str, ...]
    self_review_summary: str
    residual_risks: tuple[str, ...] = ()
    unrun_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("run_id", "skill_version", "semantic_ack_event_id"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        task_id, attempt_id = _task_attempt(self.task_id, self.attempt_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(
            self,
            "task_contract_digest",
            _digest(self.task_contract_digest, "task_contract_digest"),
        )
        object.__setattr__(self, "base_sha", _git_object(self.base_sha, "base_sha"))
        object.__setattr__(
            self,
            "candidate_revision",
            _positive_int(self.candidate_revision, "candidate_revision"),
        )
        if self.completion_mode not in COMPLETION_MODES:
            raise WorkerCandidateError(
                f"unsupported completion_mode: {self.completion_mode!r}"
            )
        object.__setattr__(
            self,
            "candidate_tree_sha",
            _git_object(self.candidate_tree_sha, "candidate_tree_sha"),
        )
        expected_ref = (
            f"implementation-candidates/{task_id}/{attempt_id}/"
            f"{self.candidate_revision}.json"
        )
        artifact_ref = _required_text(
            self.candidate_artifact_ref, "candidate_artifact_ref"
        )
        if artifact_ref != expected_ref:
            raise WorkerCandidateError(
                "candidate_artifact_ref does not match task, attempt, and revision"
            )
        object.__setattr__(self, "candidate_artifact_ref", artifact_ref)
        for field_name, allow_empty, unique in (
            ("changed_files", False, True),
            ("validation_commands", False, True),
            ("validation_results", False, False),
            ("invariant_evidence", False, True),
            ("regression_evidence", False, True),
            ("residual_risks", True, True),
            ("unrun_checks", True, True),
        ):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(
                    getattr(self, field_name),
                    field_name,
                    allow_empty=allow_empty,
                    unique=unique,
                ),
            )
        if len(self.validation_commands) != len(self.validation_results):
            raise WorkerCandidateError(
                "validation_commands and validation_results must have equal lengths"
            )
        if any(result != "passed" for result in self.validation_results):
            raise WorkerCandidateError(
                "implementation candidates require every recorded validation to pass"
            )
        object.__setattr__(
            self,
            "validation_summary",
            _required_text(self.validation_summary, "validation_summary"),
        )
        object.__setattr__(
            self,
            "self_review_summary",
            _required_text(self.self_review_summary, "self_review_summary"),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "record_type": "implementation_candidate",
            "run_id": self.run_id,
            "skill_version": self.skill_version,
            "contract_schema_revision": V053_CONTRACT_SCHEMA_REVISION,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "task_contract_digest": self.task_contract_digest,
            "semantic_ack_event_id": self.semantic_ack_event_id,
            "base_sha": self.base_sha,
            "candidate_revision": self.candidate_revision,
            "completion_mode": self.completion_mode,
            "lifecycle_status": "implementation_complete",
            "candidate_tree_sha": self.candidate_tree_sha,
            "candidate_artifact_ref": self.candidate_artifact_ref,
            "changed_files": list(self.changed_files),
            "validation_commands": list(self.validation_commands),
            "validation_results": list(self.validation_results),
            "validation_summary": self.validation_summary,
            "invariant_evidence": list(self.invariant_evidence),
            "regression_evidence": list(self.regression_evidence),
            "self_review_summary": self.self_review_summary,
            "residual_risks": list(self.residual_risks),
            "unrun_checks": list(self.unrun_checks),
            "merge_ready": False,
            "terminal_result_emitted": False,
            "completion_sentinel_emitted": False,
        }

    @property
    def canonical_artifact_digest(self) -> str:
        """Digest the canonical candidate record for deterministic storage."""

        return canonical_digest(self.to_record())

    @classmethod
    def from_record(cls, value: Any) -> "ImplementationCandidate":
        record = _record(value, "implementation candidate")
        _exact_fields(record, IMPLEMENTATION_CANDIDATE_FIELDS, "implementation candidate")
        for field_name, expected in (
            ("record_type", "implementation_candidate"),
            ("contract_schema_revision", V053_CONTRACT_SCHEMA_REVISION),
            ("lifecycle_status", "implementation_complete"),
            ("merge_ready", False),
            ("terminal_result_emitted", False),
            ("completion_sentinel_emitted", False),
        ):
            if record[field_name] != expected:
                raise WorkerCandidateError(
                    f"implementation candidate {field_name} must be {expected!r}"
                )
        return cls(
            **{
                field_name: record[field_name]
                for field_name in (
                    "run_id",
                    "skill_version",
                    "task_id",
                    "attempt_id",
                    "task_contract_digest",
                    "semantic_ack_event_id",
                    "base_sha",
                    "candidate_revision",
                    "completion_mode",
                    "candidate_tree_sha",
                    "candidate_artifact_ref",
                    "changed_files",
                    "validation_commands",
                    "validation_results",
                    "validation_summary",
                    "invariant_evidence",
                    "regression_evidence",
                    "self_review_summary",
                    "residual_risks",
                    "unrun_checks",
                )
            }
        )


@dataclass(frozen=True, slots=True)
class CandidateSeal:
    """Exact commit identity shared by commit and handoff candidate modes."""

    run_id: str
    skill_version: str
    task_id: str
    attempt_id: str
    task_contract_digest: str
    semantic_ack_event_id: str
    base_sha: str
    candidate_revision: int
    completion_mode: str
    candidate_tree_sha: str
    candidate_sha: str
    candidate_artifact_ref: str
    candidate_artifact_digest: str

    def __post_init__(self) -> None:
        for field_name in ("run_id", "skill_version", "semantic_ack_event_id"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        task_id, attempt_id = _task_attempt(self.task_id, self.attempt_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(
            self,
            "task_contract_digest",
            _digest(self.task_contract_digest, "task_contract_digest"),
        )
        object.__setattr__(self, "base_sha", _git_object(self.base_sha, "base_sha"))
        object.__setattr__(
            self,
            "candidate_revision",
            _positive_int(self.candidate_revision, "candidate_revision"),
        )
        if self.completion_mode not in COMPLETION_MODES:
            raise WorkerCandidateError(
                f"unsupported completion_mode: {self.completion_mode!r}"
            )
        for field_name in ("candidate_tree_sha", "candidate_sha"):
            object.__setattr__(
                self,
                field_name,
                _git_object(getattr(self, field_name), field_name),
            )
        expected_ref = (
            f"implementation-candidates/{task_id}/{attempt_id}/"
            f"{self.candidate_revision}.json"
        )
        artifact_ref = _required_text(
            self.candidate_artifact_ref, "candidate_artifact_ref"
        )
        if artifact_ref != expected_ref:
            raise WorkerCandidateError(
                "candidate_artifact_ref does not match task, attempt, and revision"
            )
        object.__setattr__(self, "candidate_artifact_ref", artifact_ref)
        object.__setattr__(
            self,
            "candidate_artifact_digest",
            _digest(self.candidate_artifact_digest, "candidate_artifact_digest"),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "record_type": "candidate_seal",
            "run_id": self.run_id,
            "skill_version": self.skill_version,
            "contract_schema_revision": V053_CONTRACT_SCHEMA_REVISION,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "task_contract_digest": self.task_contract_digest,
            "semantic_ack_event_id": self.semantic_ack_event_id,
            "base_sha": self.base_sha,
            "candidate_revision": self.candidate_revision,
            "completion_mode": self.completion_mode,
            "lifecycle_status": "candidate_sealed",
            "candidate_tree_sha": self.candidate_tree_sha,
            "candidate_sha": self.candidate_sha,
            "candidate_artifact_ref": self.candidate_artifact_ref,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "merge_ready": False,
            "terminal_result_emitted": False,
            "completion_sentinel_emitted": False,
            "manager_validation_recorded": False,
        }

    @classmethod
    def from_record(cls, value: Any) -> "CandidateSeal":
        record = _record(value, "candidate seal")
        _exact_fields(record, CANDIDATE_SEAL_FIELDS, "candidate seal")
        for field_name, expected in (
            ("record_type", "candidate_seal"),
            ("contract_schema_revision", V053_CONTRACT_SCHEMA_REVISION),
            ("lifecycle_status", "candidate_sealed"),
            ("merge_ready", False),
            ("terminal_result_emitted", False),
            ("completion_sentinel_emitted", False),
            ("manager_validation_recorded", False),
        ):
            if record[field_name] != expected:
                raise WorkerCandidateError(
                    f"candidate seal {field_name} must be {expected!r}"
                )
        return cls(
            **{
                field_name: record[field_name]
                for field_name in (
                    "run_id",
                    "skill_version",
                    "task_id",
                    "attempt_id",
                    "task_contract_digest",
                    "semantic_ack_event_id",
                    "base_sha",
                    "candidate_revision",
                    "completion_mode",
                    "candidate_tree_sha",
                    "candidate_sha",
                    "candidate_artifact_ref",
                    "candidate_artifact_digest",
                )
            }
        )


def seal_candidate(
    candidate: ImplementationCandidate | Mapping[str, Any],
    *,
    candidate_sha: str,
    candidate_artifact_digest: str,
    observed_tree_sha: str,
    active_attempt_id: str,
    active_task_contract_digest: str,
    approved_ack_event_id: str,
) -> CandidateSeal:
    """Seal exact artifact bytes and Git identity without changing the attempt.

    The caller must read ``observed_tree_sha`` from ``candidate_sha``.  Requiring
    all active identities here prevents a stale candidate, ACK, or contract from
    being sealed merely because its artifact is structurally valid.
    """

    current = (
        candidate
        if isinstance(candidate, ImplementationCandidate)
        else ImplementationCandidate.from_record(candidate)
    )
    active_attempt = _required_text(active_attempt_id, "active_attempt_id")
    active_contract = _digest(
        active_task_contract_digest, "active_task_contract_digest"
    )
    approved_ack = _required_text(approved_ack_event_id, "approved_ack_event_id")
    observed_tree = _git_object(observed_tree_sha, "observed_tree_sha")
    if current.attempt_id != active_attempt:
        raise WorkerCandidateError("candidate attempt is not the active attempt")
    if not hmac.compare_digest(current.task_contract_digest, active_contract):
        raise WorkerCandidateError("candidate task contract is stale")
    if current.semantic_ack_event_id != approved_ack:
        raise WorkerCandidateError("candidate semantic ACK is not the approved ACK")
    if not hmac.compare_digest(current.candidate_tree_sha, observed_tree):
        raise WorkerCandidateError("candidate tree does not match the sealed commit")

    return CandidateSeal(
        run_id=current.run_id,
        skill_version=current.skill_version,
        task_id=current.task_id,
        attempt_id=current.attempt_id,
        task_contract_digest=current.task_contract_digest,
        semantic_ack_event_id=current.semantic_ack_event_id,
        base_sha=current.base_sha,
        candidate_revision=current.candidate_revision,
        completion_mode=current.completion_mode,
        candidate_tree_sha=current.candidate_tree_sha,
        candidate_sha=candidate_sha,
        candidate_artifact_ref=current.candidate_artifact_ref,
        candidate_artifact_digest=candidate_artifact_digest,
    )


def validate_candidate_seal(
    candidate: ImplementationCandidate | Mapping[str, Any],
    seal: CandidateSeal | Mapping[str, Any],
    *,
    candidate_artifact_digest: str,
) -> None:
    """Verify that a seal is an exact projection of one candidate artifact."""

    current = (
        candidate
        if isinstance(candidate, ImplementationCandidate)
        else ImplementationCandidate.from_record(candidate)
    )
    sealed = seal if isinstance(seal, CandidateSeal) else CandidateSeal.from_record(seal)
    expected_digest = _digest(candidate_artifact_digest, "candidate_artifact_digest")
    axes = (
        "run_id",
        "skill_version",
        "task_id",
        "attempt_id",
        "task_contract_digest",
        "semantic_ack_event_id",
        "base_sha",
        "candidate_revision",
        "completion_mode",
        "candidate_tree_sha",
        "candidate_artifact_ref",
    )
    for field_name in axes:
        if getattr(current, field_name) != getattr(sealed, field_name):
            raise WorkerCandidateError(
                f"candidate seal mismatches candidate field: {field_name}"
            )
    if not hmac.compare_digest(sealed.candidate_artifact_digest, expected_digest):
        raise WorkerCandidateError("candidate seal mismatches candidate artifact digest")


@dataclass(frozen=True, slots=True)
class PatchReview:
    """One task-local review round bound to an exact candidate seal."""

    run_id: str
    skill_version: str
    task_id: str
    attempt_id: str
    task_contract_digest: str
    semantic_ack_event_id: str
    base_sha: str
    candidate_revision: int
    candidate_sha: str
    candidate_artifact_ref: str
    candidate_artifact_digest: str
    review_attempt_id: str
    review_round: int
    reviewer_provider: str
    reviewer_model: str
    reviewer_effort: str
    verdict: str
    unresolved_finding_fingerprints: tuple[str, ...] = ()
    follow_up_finding_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "skill_version",
            "semantic_ack_event_id",
            "reviewer_model",
            "reviewer_effort",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        review_attempt_id = _required_text(
            self.review_attempt_id, "review_attempt_id"
        )
        if not _REVIEW_ATTEMPT_ID_RE.fullmatch(review_attempt_id):
            raise WorkerCandidateError(
                "review_attempt_id must be safe for an artifact filename"
            )
        object.__setattr__(self, "review_attempt_id", review_attempt_id)
        task_id, attempt_id = _task_attempt(self.task_id, self.attempt_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(
            self,
            "task_contract_digest",
            _digest(self.task_contract_digest, "task_contract_digest"),
        )
        object.__setattr__(self, "base_sha", _git_object(self.base_sha, "base_sha"))
        object.__setattr__(
            self,
            "candidate_revision",
            _positive_int(self.candidate_revision, "candidate_revision"),
        )
        object.__setattr__(
            self,
            "candidate_sha",
            _git_object(self.candidate_sha, "candidate_sha"),
        )
        object.__setattr__(
            self,
            "candidate_artifact_ref",
            _required_text(self.candidate_artifact_ref, "candidate_artifact_ref"),
        )
        object.__setattr__(
            self,
            "candidate_artifact_digest",
            _digest(self.candidate_artifact_digest, "candidate_artifact_digest"),
        )
        object.__setattr__(
            self,
            "review_round",
            _positive_int(self.review_round, "review_round"),
        )
        if self.reviewer_provider not in {"codex", "claude"}:
            raise WorkerCandidateError(
                f"unsupported reviewer_provider: {self.reviewer_provider!r}"
            )
        if self.verdict not in PATCH_REVIEW_VERDICTS:
            raise WorkerCandidateError(
                f"unsupported Patch Review verdict: {self.verdict!r}"
            )
        for field_name in (
            "unresolved_finding_fingerprints",
            "follow_up_finding_fingerprints",
        ):
            values = _text_tuple(
                getattr(self, field_name), field_name, allow_empty=True
            )
            for value in values:
                _digest(value, field_name)
            object.__setattr__(self, field_name, values)
        if set(self.unresolved_finding_fingerprints) & set(
            self.follow_up_finding_fingerprints
        ):
            raise WorkerCandidateError(
                "a Patch Review finding cannot be both unresolved and follow-up"
            )
        if self.verdict == "passed" and self.unresolved_finding_fingerprints:
            raise WorkerCandidateError(
                "passed Patch Review cannot retain unresolved task findings"
            )
        if self.verdict == "fix_required" and not self.unresolved_finding_fingerprints:
            raise WorkerCandidateError(
                "fix_required Patch Review requires unresolved task findings"
            )

    @property
    def review_artifact_ref(self) -> str:
        return (
            f"patch-reviews/{self.task_id}/{self.attempt_id}/"
            f"{self.review_attempt_id}.json"
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "record_type": "patch_review",
            "run_id": self.run_id,
            "skill_version": self.skill_version,
            "contract_schema_revision": V053_CONTRACT_SCHEMA_REVISION,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "task_contract_digest": self.task_contract_digest,
            "semantic_ack_event_id": self.semantic_ack_event_id,
            "base_sha": self.base_sha,
            "candidate_revision": self.candidate_revision,
            "candidate_sha": self.candidate_sha,
            "candidate_artifact_ref": self.candidate_artifact_ref,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "review_attempt_id": self.review_attempt_id,
            "review_round": self.review_round,
            "reviewer_provider": self.reviewer_provider,
            "reviewer_model": self.reviewer_model,
            "reviewer_effort": self.reviewer_effort,
            "verdict": self.verdict,
            "unresolved_finding_fingerprints": list(
                self.unresolved_finding_fingerprints
            ),
            "follow_up_finding_fingerprints": list(
                self.follow_up_finding_fingerprints
            ),
            "automatic_task_ids": [],
        }

    @classmethod
    def from_record(cls, value: Any) -> "PatchReview":
        record = _record(value, "Patch Review")
        _exact_fields(record, PATCH_REVIEW_FIELDS, "Patch Review")
        if record["record_type"] != "patch_review":
            raise WorkerCandidateError("Patch Review record_type must be patch_review")
        if record["contract_schema_revision"] != V053_CONTRACT_SCHEMA_REVISION:
            raise WorkerCandidateError("Patch Review contract revision must be 3")
        if record["automatic_task_ids"] != []:
            raise WorkerCandidateError(
                "Patch Review cannot automatically create scope-expanding tasks"
            )
        return cls(
            **{
                field_name: record[field_name]
                for field_name in (
                    "run_id",
                    "skill_version",
                    "task_id",
                    "attempt_id",
                    "task_contract_digest",
                    "semantic_ack_event_id",
                    "base_sha",
                    "candidate_revision",
                    "candidate_sha",
                    "candidate_artifact_ref",
                    "candidate_artifact_digest",
                    "review_attempt_id",
                    "review_round",
                    "reviewer_provider",
                    "reviewer_model",
                    "reviewer_effort",
                    "verdict",
                    "unresolved_finding_fingerprints",
                    "follow_up_finding_fingerprints",
                )
            }
        )


def record_patch_review(
    seal: CandidateSeal | Mapping[str, Any],
    *,
    review_attempt_id: str,
    review_round: int,
    reviewer_provider: str,
    reviewer_model: str,
    reviewer_effort: str,
    verdict: str,
    unresolved_finding_fingerprints: Sequence[str] = (),
    follow_up_finding_fingerprints: Sequence[str] = (),
) -> PatchReview:
    """Create a Patch Review bound to every immutable candidate axis."""

    current = seal if isinstance(seal, CandidateSeal) else CandidateSeal.from_record(seal)
    return PatchReview(
        run_id=current.run_id,
        skill_version=current.skill_version,
        task_id=current.task_id,
        attempt_id=current.attempt_id,
        task_contract_digest=current.task_contract_digest,
        semantic_ack_event_id=current.semantic_ack_event_id,
        base_sha=current.base_sha,
        candidate_revision=current.candidate_revision,
        candidate_sha=current.candidate_sha,
        candidate_artifact_ref=current.candidate_artifact_ref,
        candidate_artifact_digest=current.candidate_artifact_digest,
        review_attempt_id=review_attempt_id,
        review_round=review_round,
        reviewer_provider=reviewer_provider,
        reviewer_model=reviewer_model,
        reviewer_effort=reviewer_effort,
        verdict=verdict,
        unresolved_finding_fingerprints=tuple(unresolved_finding_fingerprints),
        follow_up_finding_fingerprints=tuple(follow_up_finding_fingerprints),
    )


def patch_review_staleness(
    review: PatchReview | Mapping[str, Any],
    seal: CandidateSeal | Mapping[str, Any],
    *,
    current_task_contract_digest: str,
) -> tuple[str, ...]:
    """Return stable reasons why review evidence is not fresh for a candidate."""

    recorded = review if isinstance(review, PatchReview) else PatchReview.from_record(review)
    current = seal if isinstance(seal, CandidateSeal) else CandidateSeal.from_record(seal)
    active_contract = _digest(
        current_task_contract_digest, "current_task_contract_digest"
    )
    reasons: list[str] = []
    for field_name in (
        "run_id",
        "task_id",
        "attempt_id",
        "task_contract_digest",
        "semantic_ack_event_id",
        "base_sha",
        "candidate_revision",
        "candidate_sha",
        "candidate_artifact_ref",
        "candidate_artifact_digest",
    ):
        if getattr(recorded, field_name) != getattr(current, field_name):
            reasons.append(f"{field_name}-changed")
    if current.task_contract_digest != active_contract:
        reasons.append("active-task-contract-changed")
    return tuple(reasons)


def require_fresh_patch_review(
    review: PatchReview | Mapping[str, Any],
    seal: CandidateSeal | Mapping[str, Any],
    *,
    current_task_contract_digest: str,
) -> PatchReview:
    """Return a passed fresh review or reject finalization."""

    recorded = review if isinstance(review, PatchReview) else PatchReview.from_record(review)
    reasons = patch_review_staleness(
        recorded,
        seal,
        current_task_contract_digest=current_task_contract_digest,
    )
    if reasons:
        raise WorkerCandidateError(
            "Patch Review is stale: " + ", ".join(reasons)
        )
    if recorded.verdict != "passed":
        raise WorkerCandidateError("Patch Review has unresolved task findings")
    return recorded


@dataclass(frozen=True, slots=True)
class PatchReviewDecision:
    """Fail-closed next action for one task's bounded Patch Review history."""

    action: str
    task_id: str
    attempt_id: str
    semantic_ack_event_id: str
    rounds_used: int
    max_rounds: int
    last_candidate_sha: str
    unresolved_finding_fingerprints: tuple[str, ...]
    follow_up_finding_fingerprints: tuple[str, ...]
    counted_review_attempt_ids: tuple[str, ...]
    stale_review_attempt_ids: tuple[str, ...]
    automatic_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.action not in PATCH_REVIEW_ACTIONS:
            raise WorkerCandidateError(
                f"unsupported Patch Review action: {self.action!r}"
            )
        if self.automatic_task_ids:
            raise WorkerCandidateError(
                "Patch Review decisions cannot automatically create tasks"
            )

    @property
    def requires_user_decision(self) -> bool:
        return self.action == "user_decision_required"

    def to_record(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "semantic_ack_event_id": self.semantic_ack_event_id,
            "rounds_used": self.rounds_used,
            "max_rounds": self.max_rounds,
            "last_candidate_sha": self.last_candidate_sha,
            "unresolved_finding_fingerprints": list(
                self.unresolved_finding_fingerprints
            ),
            "follow_up_finding_fingerprints": list(
                self.follow_up_finding_fingerprints
            ),
            "counted_review_attempt_ids": list(self.counted_review_attempt_ids),
            "stale_review_attempt_ids": list(self.stale_review_attempt_ids),
            "automatic_task_ids": [],
        }


def _deduplicate_review_attempts(
    reviews: Sequence[PatchReview | Mapping[str, Any]],
    *,
    run_id: str,
    task_id: str,
) -> tuple[PatchReview, ...]:
    by_attempt: dict[str, PatchReview] = {}
    for value in reviews:
        review = value if isinstance(value, PatchReview) else PatchReview.from_record(value)
        if review.run_id != run_id:
            raise WorkerCandidateError("Patch Review history contains another run")
        if review.task_id != task_id:
            raise WorkerCandidateError("Patch Review history contains another task")
        existing = by_attempt.get(review.review_attempt_id)
        if existing is not None:
            if existing.to_record() != review.to_record():
                raise WorkerCandidateError(
                    "review_attempt_id was reused with different Patch Review content"
                )
            continue
        by_attempt[review.review_attempt_id] = review
    ordered = tuple(
        sorted(by_attempt.values(), key=lambda item: (item.review_round, item.review_attempt_id))
    )
    rounds = [item.review_round for item in ordered]
    if len(set(rounds)) != len(rounds):
        raise WorkerCandidateError(
            "distinct Patch Review attempts cannot claim the same review_round"
        )
    if rounds and rounds != list(range(1, len(rounds) + 1)):
        raise WorkerCandidateError("Patch Review rounds must be contiguous from 1")
    return ordered


def evaluate_patch_review_rounds(
    seal: CandidateSeal | Mapping[str, Any],
    reviews: Sequence[PatchReview | Mapping[str, Any]],
    *,
    current_task_contract_digest: str,
    max_rounds: int = DEFAULT_MAX_PATCH_REVIEW_ROUNDS_PER_TASK,
) -> PatchReviewDecision:
    """Evaluate fresh evidence and the independent per-task review budget.

    Idempotent replay of an identical ``review_attempt_id`` does not consume an
    extra round.  A different payload under the same ID is an integrity error.
    Stale reviews cannot authorize finalization, but their completed rounds
    still count against the task-local limit.
    """

    current = seal if isinstance(seal, CandidateSeal) else CandidateSeal.from_record(seal)
    active_contract = _digest(
        current_task_contract_digest, "current_task_contract_digest"
    )
    limit = _patch_review_round_limit(max_rounds)
    history = _deduplicate_review_attempts(
        reviews, run_id=current.run_id, task_id=current.task_id
    )
    if len(history) > limit:
        raise WorkerCandidateError("Patch Review history exceeds the per-task round limit")

    fresh: list[PatchReview] = []
    stale_ids: list[str] = []
    for review in history:
        if patch_review_staleness(
            review,
            current,
            current_task_contract_digest=active_contract,
        ):
            stale_ids.append(review.review_attempt_id)
        else:
            fresh.append(review)

    latest = history[-1] if history else None
    latest_fresh = fresh[-1] if fresh else None
    follow_ups = tuple(
        dict.fromkeys(
            fingerprint
            for review in history
            for fingerprint in review.follow_up_finding_fingerprints
        )
    )
    action = "patch_review_pending"
    unresolved: tuple[str, ...] = ()
    last_candidate_sha = current.candidate_sha

    if (
        latest_fresh is not None
        and latest_fresh is latest
        and latest_fresh.verdict == "passed"
    ):
        action = "finalize_allowed"
    elif len(history) >= limit:
        action = "user_decision_required"
        if latest is not None:
            unresolved = latest.unresolved_finding_fingerprints
            last_candidate_sha = latest.candidate_sha
    elif current.task_contract_digest != active_contract:
        action = "candidate_resubmission_required"
    elif latest_fresh is not None and latest_fresh is latest:
        unresolved = latest_fresh.unresolved_finding_fingerprints
        last_candidate_sha = latest_fresh.candidate_sha
        action = "patch_fix_running"
    return PatchReviewDecision(
        action=action,
        task_id=current.task_id,
        attempt_id=current.attempt_id,
        semantic_ack_event_id=current.semantic_ack_event_id,
        rounds_used=len(history),
        max_rounds=limit,
        last_candidate_sha=last_candidate_sha,
        unresolved_finding_fingerprints=unresolved,
        follow_up_finding_fingerprints=follow_ups,
        counted_review_attempt_ids=tuple(
            review.review_attempt_id for review in history
        ),
        stale_review_attempt_ids=tuple(stale_ids),
    )


def validate_final_result_authenticity(
    candidate: ImplementationCandidate | Mapping[str, Any],
    seal: CandidateSeal | Mapping[str, Any],
    review: PatchReview | Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    candidate_artifact_digest: str,
    current_task_contract_digest: str,
    result_parent_candidate_sha: str,
) -> None:
    """Recheck candidate, review, and final result before harvest or merge."""

    current = (
        candidate
        if isinstance(candidate, ImplementationCandidate)
        else ImplementationCandidate.from_record(candidate)
    )
    sealed = seal if isinstance(seal, CandidateSeal) else CandidateSeal.from_record(seal)
    validate_candidate_seal(
        current,
        sealed,
        candidate_artifact_digest=candidate_artifact_digest,
    )
    require_fresh_patch_review(
        review,
        sealed,
        current_task_contract_digest=current_task_contract_digest,
    )
    parent_candidate_sha = _git_object(
        result_parent_candidate_sha, "result_parent_candidate_sha"
    )
    if not hmac.compare_digest(parent_candidate_sha, sealed.candidate_sha):
        raise WorkerCandidateError(
            "final result commit is not based on the reviewed candidate SHA"
        )
    validation = validate_result_contract(result)
    if not validation.ok:
        raise WorkerCandidateError(
            "final result contract is invalid: "
            + "; ".join(issue.message for issue in validation.issues)
        )
    expected_scalars = {
        "contract_schema_revision": V053_CONTRACT_SCHEMA_REVISION,
        "task_id": current.task_id,
        "run_id": current.run_id,
        "skill_version": current.skill_version,
        "attempt_id": current.attempt_id,
        "status": "done",
        "merge_ready": True,
        "base_sha": current.base_sha,
        "self_review_summary": current.self_review_summary,
    }
    for field_name, expected in expected_scalars.items():
        if result.get(field_name) != expected:
            raise WorkerCandidateError(
                f"final result does not match candidate field: {field_name}"
            )
    _git_object(result.get("head_sha"), "final result head_sha")
    if tuple(result.get("changed_files", ())) != current.changed_files:
        raise WorkerCandidateError(
            "final result does not preserve candidate changed_files"
        )
    if result.get("handoff") != (current.completion_mode == "handoff"):
        raise WorkerCandidateError(
            "final result handoff mode does not match the sealed candidate"
        )
    for field_name in (
        "invariant_evidence",
        "regression_evidence",
        "residual_risks",
        "unrun_checks",
    ):
        if tuple(result.get(field_name, ())) != getattr(current, field_name):
            raise WorkerCandidateError(
                f"final result does not preserve candidate evidence: {field_name}"
            )
    result_pairs = tuple(
        zip(result.get("validation_commands", ()), result.get("validation_results", ()))
    )
    candidate_pairs = tuple(zip(current.validation_commands, current.validation_results))
    if any(pair not in result_pairs for pair in candidate_pairs):
        raise WorkerCandidateError(
            "final result does not preserve candidate validation evidence"
        )
