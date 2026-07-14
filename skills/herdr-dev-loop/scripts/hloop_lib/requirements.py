"""Pure input, requirement, and progress primitives for HLoop 0.5.

The CLI owns clocks, files, and state transactions.  This module only turns
caller-supplied observations into validated immutable records.  In particular,
captured user input is redacted before it enters a record, accepted
requirements are append-only/superseded rather than silently rewritten, and a
requirement cannot become ``verified`` from an agent assertion alone.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


INPUT_ARTIFACT_CLASS = "local-sensitive"
REQUIREMENT_STATUSES = frozenset({"accepted", "superseded"})
PROGRESS_STATUSES = frozenset(
    {
        "not_started",
        "in_progress",
        "implemented_unverified",
        "verified",
        "blocked",
        "deferred",
        "superseded",
    }
)
EVIDENCE_KINDS = frozenset(
    {"artifact", "sha", "test", "qa", "review", "decision", "agent-report"}
)
VERIFICATION_AUTHORITIES = frozenset({"manager", "hloop"})
PASSING_EVIDENCE_RESULTS = frozenset({"passed", "confirmed", "accepted"})

_INPUT_ID_RE = re.compile(r"^U[0-9]{4}$")
_REQUIREMENT_ID_RE = re.compile(r"^REQ-[0-9]{3}$")
_PROGRESS_ID_RE = re.compile(r"^P[0-9]{4}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class RequirementModelError(ValueError):
    """Raised when an input, requirement, or progress record is invalid."""


class CheckpointPolicyError(RequirementModelError):
    """Raised when local-sensitive material is selected for a checkpoint."""


class ProgressEvidenceError(RequirementModelError):
    """Raised when a claimed progress state is not supported by evidence."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequirementModelError(f"{field_name} must be a non-empty string")
    return value.strip()


def _unique_texts(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RequirementModelError(f"{field_name} must be a sequence of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if len(set(normalized)) != len(normalized):
        raise RequirementModelError(f"{field_name} must not contain duplicates")
    return normalized


def _rfc3339(value: str, field_name: str) -> str:
    text = _required_text(value, field_name)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise RequirementModelError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RequirementModelError(f"{field_name} must include a timezone")
    return text


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION_RE = re.compile(
    r"(?im)(\bAuthorization\s*:\s*)(?:Bearer|Basic)\s+[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_CREDENTIAL_URI_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://[^/@\s:]+:)([^/@\s]+)(@)"
)
_ASSIGNMENT_RE = re.compile(
    r"(?im)(\b(?:api[-_ ]?key|access[-_ ]?token|auth[-_ ]?token|token|password|"
    r"passwd|client[-_ ]?secret|secret(?:[-_ ]?key)?)\b\s*[:=]\s*)"
    r"(?!\[REDACTED(?: [A-Z ]+)?\])(?:\"[^\"\n]+\"|'[^'\n]+'|[^\s,;]+)"
)
_ENVIRONMENT_CREDENTIAL_RE = re.compile(
    r"(?im)(\b(?![a-z0-9_]*public_key\b)[a-z][a-z0-9_]*"
    r"(?:_token|_secret|_password|_passwd|_key)\b[ \t]*=[ \t]*)"
    r"(?!\[REDACTED(?: [A-Z ]+)?\])(?:\"[^\"\n]+\"|'[^'\n]+'|[^\s,;]+)"
)
_GITLAB_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"
)
_KNOWN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"sk-[A-Za-z0-9_-]{12,}|"
    r"AKIA[0-9A-Z]{16}"
    r")(?![A-Za-z0-9])"
)


def redact_sensitive_text_with_kinds(text: str) -> tuple[str, tuple[str, ...]]:
    """Redact common credential forms and return stable redaction labels.

    The original text is never returned as part of the metadata.  Its digest
    can be calculated separately to correlate the local record with the user
    prompt without committing the prompt itself.
    """

    if not isinstance(text, str):
        raise RequirementModelError("raw_input must be a string")
    redacted = text
    kinds: list[str] = []
    patterns = (
        ("private-key", _PRIVATE_KEY_RE, "[REDACTED PRIVATE KEY]"),
        ("authorization", _AUTHORIZATION_RE, r"\1[REDACTED]"),
        ("bearer-token", _BEARER_RE, r"\1 [REDACTED]"),
        ("credential-uri", _CREDENTIAL_URI_RE, r"\1[REDACTED]\3"),
        ("credential-assignment", _ASSIGNMENT_RE, r"\1[REDACTED]"),
        (
            "environment-credential",
            _ENVIRONMENT_CREDENTIAL_RE,
            r"\1[REDACTED]",
        ),
        ("gitlab-token", _GITLAB_TOKEN_RE, "[REDACTED TOKEN]"),
        ("known-token", _KNOWN_TOKEN_RE, "[REDACTED TOKEN]"),
    )
    for kind, pattern, replacement in patterns:
        if pattern.search(redacted):
            redacted = pattern.sub(replacement, redacted)
            kinds.append(kind)
    return redacted, tuple(kinds)


def redact_sensitive_text(text: str) -> str:
    """Return only the redacted form of ``text`` for simple callers."""

    return redact_sensitive_text_with_kinds(text)[0]


def prompt_digest(text: str) -> str:
    """Return the SHA-256 digest used to identify an input prompt."""

    if not isinstance(text, str):
        raise RequirementModelError("raw_input must be a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InputRecord:
    """A redacted, explicitly local-sensitive user input record."""

    input_id: str
    received_at: str
    source: str
    prompt_digest: str
    raw_input: str
    redactions: tuple[str, ...] = ()
    artifact_class: str = INPUT_ARTIFACT_CLASS
    checkpoint_included: bool = False
    product_commit_included: bool = False

    def __post_init__(self) -> None:
        if not _INPUT_ID_RE.fullmatch(self.input_id):
            raise RequirementModelError("input_id must match U0001")
        _rfc3339(self.received_at, "received_at")
        _required_text(self.source, "source")
        if not _DIGEST_RE.fullmatch(self.prompt_digest):
            raise RequirementModelError("prompt_digest must be a lowercase SHA-256 digest")
        if not isinstance(self.raw_input, str):
            raise RequirementModelError("raw_input must be a string")
        if redact_sensitive_text(self.raw_input) != self.raw_input:
            raise RequirementModelError("raw_input contains an unredacted credential")
        object.__setattr__(
            self, "redactions", _unique_texts(self.redactions, "redactions")
        )
        if self.artifact_class != INPUT_ARTIFACT_CLASS:
            raise RequirementModelError("raw input must use local-sensitive artifact class")
        if self.checkpoint_included is not False or self.product_commit_included is not False:
            raise CheckpointPolicyError(
                "raw input cannot be included in a checkpoint or product commit"
            )

    @classmethod
    def capture(
        cls,
        *,
        input_id: str,
        received_at: str,
        source: str,
        raw_input: str,
    ) -> "InputRecord":
        """Capture a prompt without retaining its unredacted credential values."""

        redacted, kinds = redact_sensitive_text_with_kinds(raw_input)
        return cls(
            input_id=input_id,
            received_at=received_at,
            source=source,
            prompt_digest=prompt_digest(raw_input),
            raw_input=redacted,
            redactions=kinds,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.input_id,
            "received_at": self.received_at,
            "source": self.source,
            "prompt_digest": self.prompt_digest,
            "raw_input": self.raw_input,
            "redactions": list(self.redactions),
            "artifact_class": self.artifact_class,
            "checkpoint_included": self.checkpoint_included,
            "product_commit_included": self.product_commit_included,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "InputRecord":
        return cls(
            input_id=str(record.get("id") or record.get("input_id") or ""),
            received_at=str(record.get("received_at") or ""),
            source=str(record.get("source") or ""),
            prompt_digest=str(record.get("prompt_digest") or ""),
            raw_input=record.get("raw_input") if isinstance(record.get("raw_input"), str) else "",
            redactions=tuple(record.get("redactions") or ()),
            artifact_class=str(record.get("artifact_class") or ""),
            checkpoint_included=record.get("checkpoint_included", False),
            product_commit_included=record.get("product_commit_included", False),
        )


def is_local_sensitive_path(path: str) -> bool:
    """Return whether a namespaced path is excluded from durable Git history."""

    if not isinstance(path, str) or not path.strip():
        raise CheckpointPolicyError("checkpoint path must be a non-empty string")
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return "inputs" in parts or "inbox" in parts


def checkpoint_inclusion_allowed(
    path: str, *, artifact_class: str | None = None
) -> bool:
    """Classify checkpoint inclusion without reading the filesystem."""

    return artifact_class != INPUT_ARTIFACT_CLASS and not is_local_sensitive_path(path)


def assert_checkpoint_inclusion_allowed(
    paths: Sequence[str], *, artifact_classes: Mapping[str, str] | None = None
) -> None:
    """Reject a checkpoint path set containing raw input or another inbox item."""

    classes = artifact_classes or {}
    rejected = [
        path
        for path in paths
        if not checkpoint_inclusion_allowed(path, artifact_class=classes.get(path))
    ]
    if rejected:
        raise CheckpointPolicyError(
            "local-sensitive paths cannot be checkpointed: " + ", ".join(rejected)
        )


@dataclass(frozen=True, slots=True)
class Requirement:
    """One Manager-accepted requirement with traceable input provenance."""

    requirement_id: str
    source_inputs: tuple[str, ...]
    acceptance: tuple[str, ...]
    priority: str
    dependencies: tuple[str, ...] = ()
    accepted_at: str = ""
    status: str = "accepted"
    supersedes: tuple[str, ...] = ()
    superseded_by: str = ""

    def __post_init__(self) -> None:
        if not _REQUIREMENT_ID_RE.fullmatch(self.requirement_id):
            raise RequirementModelError("requirement_id must match REQ-001")
        source_inputs = _unique_texts(self.source_inputs, "source_inputs")
        if not source_inputs or any(not _INPUT_ID_RE.fullmatch(item) for item in source_inputs):
            raise RequirementModelError("source_inputs must contain U0001-style ids")
        acceptance = _unique_texts(self.acceptance, "acceptance")
        if not acceptance:
            raise RequirementModelError("acceptance must not be empty")
        if self.priority not in {"P0", "P1", "P2", "P3"}:
            raise RequirementModelError("priority must be P0, P1, P2, or P3")
        dependencies = _unique_texts(self.dependencies, "dependencies")
        supersedes = _unique_texts(self.supersedes, "supersedes")
        for field_name, values in (("dependencies", dependencies), ("supersedes", supersedes)):
            if any(not _REQUIREMENT_ID_RE.fullmatch(item) for item in values):
                raise RequirementModelError(f"{field_name} must contain REQ-001-style ids")
            if self.requirement_id in values:
                raise RequirementModelError(f"a requirement cannot list itself in {field_name}")
        if self.accepted_at:
            _rfc3339(self.accepted_at, "accepted_at")
        if self.status not in REQUIREMENT_STATUSES:
            raise RequirementModelError(f"unknown requirement status: {self.status}")
        if self.status == "superseded":
            if not _REQUIREMENT_ID_RE.fullmatch(self.superseded_by):
                raise RequirementModelError("superseded requirements require superseded_by")
        elif self.superseded_by:
            raise RequirementModelError("accepted requirements cannot set superseded_by")
        object.__setattr__(self, "source_inputs", source_inputs)
        object.__setattr__(self, "acceptance", acceptance)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "supersedes", supersedes)

    def to_record(self) -> dict[str, Any]:
        record = {
            "id": self.requirement_id,
            "source_inputs": list(self.source_inputs),
            "acceptance": list(self.acceptance),
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
        }
        if self.accepted_at:
            record["accepted_at"] = self.accepted_at
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Requirement":
        return cls(
            requirement_id=str(record.get("id") or record.get("requirement_id") or ""),
            source_inputs=tuple(record.get("source_inputs") or ()),
            acceptance=tuple(record.get("acceptance") or ()),
            priority=str(record.get("priority") or ""),
            dependencies=tuple(record.get("dependencies") or ()),
            accepted_at=str(record.get("accepted_at") or ""),
            status=str(record.get("status") or ""),
            supersedes=tuple(record.get("supersedes") or ()),
            superseded_by=str(record.get("superseded_by") or ""),
        )


@dataclass(frozen=True, slots=True)
class RequirementLedger:
    """An immutable accepted-requirement ledger."""

    requirements: tuple[Requirement, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(item, Requirement) for item in self.requirements):
            raise RequirementModelError("ledger entries must be Requirement values")
        ids = [item.requirement_id for item in self.requirements]
        if len(set(ids)) != len(ids):
            raise RequirementModelError("requirement ids are append-only and must be unique")

    def get(self, requirement_id: str) -> Requirement:
        for requirement in self.requirements:
            if requirement.requirement_id == requirement_id:
                return requirement
        raise RequirementModelError(f"unknown requirement: {requirement_id}")

    def accept(self, requirement: Requirement) -> "RequirementLedger":
        if requirement.status != "accepted":
            raise RequirementModelError("only accepted requirements can be added")
        if any(item.requirement_id == requirement.requirement_id for item in self.requirements):
            raise RequirementModelError(
                "an accepted requirement cannot silently rewrite an existing id"
            )
        known = {item.requirement_id for item in self.requirements}
        missing_dependencies = set(requirement.dependencies) - known
        missing_supersedes = set(requirement.supersedes) - known
        if missing_dependencies:
            raise RequirementModelError(
                "unknown requirement dependencies: " + ", ".join(sorted(missing_dependencies))
            )
        if missing_supersedes:
            raise RequirementModelError(
                "unknown superseded requirements: " + ", ".join(sorted(missing_supersedes))
            )
        entries = list(self.requirements)
        for old_id in requirement.supersedes:
            index = next(i for i, item in enumerate(entries) if item.requirement_id == old_id)
            old = entries[index]
            if old.status == "superseded":
                raise RequirementModelError(f"requirement is already superseded: {old_id}")
            entries[index] = replace(
                old, status="superseded", superseded_by=requirement.requirement_id
            )
        entries.append(requirement)
        return RequirementLedger(tuple(entries))

    def supersede(
        self, superseded_ids: Sequence[str], replacement: Requirement
    ) -> "RequirementLedger":
        ids = _unique_texts(superseded_ids, "superseded_ids")
        if not ids:
            raise RequirementModelError("superseded_ids must not be empty")
        if replacement.supersedes and replacement.supersedes != ids:
            raise RequirementModelError("replacement supersedes relation disagrees with request")
        return self.accept(replace(replacement, supersedes=ids))


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Evidence checked by Manager/HLoop, or a non-authoritative agent report."""

    kind: str
    reference: str
    verified_by: str = ""
    head_sha: str = ""
    result: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ProgressEvidenceError(f"unknown evidence kind: {self.kind}")
        _required_text(self.reference, "evidence reference")
        if self.verified_by and self.verified_by not in VERIFICATION_AUTHORITIES:
            raise ProgressEvidenceError("verified_by must be manager or hloop")
        if self.kind in {"test", "qa"} and self.verified_by and not self.result:
            raise ProgressEvidenceError("verified test/QA evidence requires a result")
        if not isinstance(self.head_sha, str) or not isinstance(self.result, str):
            raise ProgressEvidenceError("evidence head_sha and result must be strings")
        if self.head_sha and not _HEAD_SHA_RE.fullmatch(self.head_sha):
            raise ProgressEvidenceError(
                "evidence head_sha must be a 40- or 64-character hex digest"
            )

    @property
    def qualifies_for_verified(self) -> bool:
        """Return whether this item may participate in a verified bundle."""

        if self.kind == "agent-report" or self.verified_by not in VERIFICATION_AUTHORITIES:
            return False
        if not self.head_sha:
            return False
        if self.kind in {"test", "qa"}:
            return self.result in PASSING_EVIDENCE_RESULTS
        return self.result in PASSING_EVIDENCE_RESULTS or not self.result

    def to_record(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "reference": self.reference,
            "verified_by": self.verified_by,
            "head_sha": self.head_sha,
            "result": self.result,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "EvidenceRef":
        return cls(
            kind=str(record.get("kind") or ""),
            reference=str(record.get("reference") or ""),
            verified_by=str(record.get("verified_by") or ""),
            head_sha=str(record.get("head_sha") or ""),
            result=str(record.get("result") or ""),
        )


_PROGRESS_TRANSITIONS = {
    "not_started": {"in_progress", "blocked", "deferred", "superseded"},
    "in_progress": {
        "implemented_unverified",
        "blocked",
        "deferred",
        "superseded",
    },
    "implemented_unverified": {"in_progress", "verified", "blocked", "superseded"},
    "verified": {"in_progress", "superseded"},
    "blocked": {"in_progress", "deferred", "superseded"},
    "deferred": {"not_started", "in_progress", "superseded"},
    "superseded": set(),
}


@dataclass(frozen=True, slots=True)
class RequirementProgress:
    """Current evidence-derived state of one accepted requirement."""

    requirement_id: str
    status: str = "not_started"
    task_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    remaining_work: str = ""
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _REQUIREMENT_ID_RE.fullmatch(self.requirement_id):
            raise RequirementModelError("requirement_id must match REQ-001")
        if self.status not in PROGRESS_STATUSES:
            raise RequirementModelError(f"unknown progress status: {self.status}")
        object.__setattr__(self, "task_ids", _unique_texts(self.task_ids, "task_ids"))
        if any(not isinstance(item, EvidenceRef) for item in self.evidence):
            raise ProgressEvidenceError("evidence must contain EvidenceRef values")
        if not isinstance(self.remaining_work, str):
            raise RequirementModelError("remaining_work must be a string")
        blockers = _unique_texts(self.blockers, "blockers")
        object.__setattr__(self, "blockers", blockers)
        if self.status == "verified":
            artifacts = tuple(
                item
                for item in self.evidence
                if item.kind == "artifact"
                and item.verified_by in VERIFICATION_AUTHORITIES
            )
            if not artifacts or any(not item.head_sha for item in artifacts):
                raise ProgressEvidenceError(
                    "verified status requires Manager/HLoop-verified artifact evidence "
                    "bound to a head SHA"
                )
            validations = tuple(
                item
                for item in self.evidence
                if item.kind in {"test", "qa"}
                and item.verified_by in VERIFICATION_AUTHORITIES
                and item.result in PASSING_EVIDENCE_RESULTS
            )
            if not validations or any(not item.head_sha for item in validations):
                raise ProgressEvidenceError(
                    "verified status requires passing test/QA evidence bound to a head SHA"
                )
            target_shas = {item.head_sha for item in (*artifacts, *validations)}
            if len(target_shas) != 1:
                raise ProgressEvidenceError(
                    "verified artifact and passing test/QA evidence must use the same "
                    "target head SHA"
                )
            if blockers:
                raise ProgressEvidenceError("verified progress cannot retain blockers")
            if self.remaining_work.strip():
                raise ProgressEvidenceError("verified progress cannot retain remaining work")
        if self.status == "blocked" and not blockers:
            raise RequirementModelError("blocked progress requires at least one blocker")

    def to_record(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "status": self.status,
            "task_ids": list(self.task_ids),
            "evidence": [item.to_record() for item in self.evidence],
            "remaining_work": self.remaining_work,
            "blockers": list(self.blockers),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "RequirementProgress":
        evidence = record.get("evidence") or ()
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
            raise ProgressEvidenceError("evidence must be a sequence")
        return cls(
            requirement_id=str(record.get("requirement_id") or ""),
            status=str(record.get("status") or ""),
            task_ids=tuple(record.get("task_ids") or ()),
            evidence=tuple(
                item if isinstance(item, EvidenceRef) else EvidenceRef.from_record(item)
                for item in evidence
            ),
            remaining_work=record.get("remaining_work") or "",
            blockers=tuple(record.get("blockers") or ()),
        )


_UNSET = object()


def transition_progress(
    current: RequirementProgress,
    status: str,
    *,
    task_ids: Sequence[str] | object = _UNSET,
    evidence: Sequence[EvidenceRef] | object = _UNSET,
    remaining_work: str | object = _UNSET,
    blockers: Sequence[str] | object = _UNSET,
) -> RequirementProgress:
    """Apply one explicit progress transition and revalidate its evidence."""

    if not isinstance(current, RequirementProgress):
        raise RequirementModelError("current must be RequirementProgress")
    if status not in PROGRESS_STATUSES:
        raise RequirementModelError(f"unknown progress status: {status}")
    if status != current.status and status not in _PROGRESS_TRANSITIONS[current.status]:
        raise RequirementModelError(
            f"illegal requirement progress transition: {current.status} -> {status}"
        )
    return RequirementProgress(
        requirement_id=current.requirement_id,
        status=status,
        task_ids=current.task_ids if task_ids is _UNSET else tuple(task_ids),
        evidence=current.evidence if evidence is _UNSET else tuple(evidence),
        remaining_work=(
            current.remaining_work if remaining_work is _UNSET else str(remaining_work)
        ),
        blockers=current.blockers if blockers is _UNSET else tuple(blockers),
    )


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """A point-in-time, requirement-oriented progress projection."""

    progress_id: str
    created_at: str
    requirements: tuple[RequirementProgress, ...]

    def __post_init__(self) -> None:
        if not _PROGRESS_ID_RE.fullmatch(self.progress_id):
            raise RequirementModelError("progress_id must match P0001")
        _rfc3339(self.created_at, "created_at")
        if any(not isinstance(item, RequirementProgress) for item in self.requirements):
            raise RequirementModelError(
                "progress snapshot requirements must be RequirementProgress values"
            )
        ids = [item.requirement_id for item in self.requirements]
        if len(set(ids)) != len(ids):
            raise RequirementModelError("progress snapshot requirement ids must be unique")

    def counts(self) -> dict[str, int]:
        return {
            status: sum(item.status == status for item in self.requirements)
            for status in sorted(PROGRESS_STATUSES)
        }

    def summary(self) -> str:
        counts = self.counts()
        return (
            f"{len(self.requirements)}要件中{counts['verified']}件を検証済み、"
            f"{counts['implemented_unverified']}件は実装済み・未検証、"
            f"{counts['blocked']}件はブロック中"
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.progress_id,
            "created_at": self.created_at,
            "summary": self.summary(),
            "requirements": [item.to_record() for item in self.requirements],
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ProgressSnapshot":
        requirements = record.get("requirements") or ()
        if isinstance(requirements, (str, bytes)) or not isinstance(
            requirements, Sequence
        ):
            raise RequirementModelError("requirements must be a sequence")
        return cls(
            progress_id=str(record.get("id") or record.get("progress_id") or ""),
            created_at=str(record.get("created_at") or ""),
            requirements=tuple(
                item
                if isinstance(item, RequirementProgress)
                else RequirementProgress.from_record(item)
                for item in requirements
            ),
        )
