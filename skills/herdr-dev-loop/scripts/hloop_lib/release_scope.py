"""Release-scope and task-authorization primitives for HLoop 0.5.2.

The stable CLI owns files, clocks, and state transactions.  This module keeps
the policy decisions that are useful to every task-creation path in one small,
stdlib-only unit.  Records are immutable, source snapshots are content
addressed, and authorization is fail-closed when a new-policy loop does not
carry the references needed to explain why a task exists.

The module deliberately does not import the CLI.  That makes the policy
testable before it is wired into ``task new``, triage, or role dispatch, and it
also prevents a direct CLI path from growing a second, weaker implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Sequence


AMENDMENT_KINDS = frozenset({"editorial", "clarification", "scope-change"})
SCOPE_STATUSES = frozenset({"unlocked", "locked", "legacy-unlocked"})
TASK_ORIGINS = frozenset(
    {"planned", "finding", "user-amendment", "operational", "legacy-unclassified"}
)
FINDING_ORIGINS = frozenset(
    {"introduced", "diff-expanded-pre-existing", "unrelated-pre-existing", "unknown"}
)
CONTRACT_RELATIONS = frozenset({"in_scope", "outside_release", "ambiguous"})
RELEASE_EFFECTS = frozenset({"blocking", "non_blocking"})
FACT_STATUSES = frozenset({"confirmed", "refuted", "insufficient_evidence"})
TASK_DISPOSITIONS = frozenset(
    {
        "fix_now",
        "defer_follow_up",
        "disable_feature",
        "mark_experimental",
        "user_decision",
        "accepted_risk",
        "discard",
    }
)

_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_INPUT_ID_RE = re.compile(r"^U[0-9]{4}$")
_AMENDMENT_ID_RE = re.compile(r"^A[0-9]{3}$")
_TASK_ID_RE = re.compile(r"^T[0-9]{3}$")


class ReleaseScopeError(ValueError):
    """Base error for malformed scope or authorization records."""


class ScopeValidationError(ReleaseScopeError):
    """Raised when a release-scope lock or amendment is invalid."""


class ScopeDriftError(ScopeValidationError):
    """Raised when the current source snapshot differs from the locked one."""


class AmendmentValidationError(ScopeValidationError):
    """Raised when an amendment is not a valid transition from a lock."""


class TaskAuthorizationError(ReleaseScopeError):
    """Raised when task provenance cannot authorize task creation."""


class ProvenanceMutationError(TaskAuthorizationError):
    """Raised when an existing task attempts to rewrite its provenance."""


TaskProvenanceError = TaskAuthorizationError


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseScopeError(f"{field_name} must be a non-empty string")
    return value.strip()


def _unique_texts(values: Sequence[str] | None, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReleaseScopeError(f"{field_name} must be a sequence of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ReleaseScopeError(f"{field_name} must not contain duplicates")
    return normalized


def _revision(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReleaseScopeError(
            f"{field_name} must be an integer greater than or equal to {minimum}"
        )
    return value


def _timestamp(value: str, field_name: str) -> str:
    text = _required_text(value, field_name)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ReleaseScopeError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseScopeError(f"{field_name} must include a timezone")
    return text


def _optional_timestamp(value: Any, field_name: str) -> str:
    if value in (None, ""):
        return ""
    return _timestamp(value, field_name)


def _input_id(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _INPUT_ID_RE.fullmatch(text):
        raise ReleaseScopeError(f"{field_name} must match U0001-style input ids")
    return text


def _optional_input_id(value: Any, field_name: str) -> str:
    if value in (None, ""):
        return ""
    return _input_id(value, field_name)


def _digest(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name).lower()
    if not _DIGEST_RE.fullmatch(text):
        raise ReleaseScopeError(
            f"{field_name} must be a lowercase SHA-256 digest (sha256:<64 hex chars>)"
        )
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _freeze_digests(
    values: Mapping[str, str] | None, field_name: str = "source_digests"
) -> Mapping[str, str]:
    if values is None:
        values = {}
    if not isinstance(values, Mapping):
        raise ReleaseScopeError(f"{field_name} must be an object")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = _required_text(raw_key, f"{field_name} key")
        normalized[key] = _digest(raw_value, f"{field_name}[{key!r}]")
    return MappingProxyType(dict(sorted(normalized.items())))


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseScopeError(f"{field_name} must be an object")
    return value


def _as_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReleaseScopeError(f"{field_name} must be an array")
    return tuple(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_digest(value: str | bytes) -> str:
    """Return the canonical ``sha256:<hex>`` digest for text or bytes."""

    if isinstance(value, str):
        payload = value.encode("utf-8")
    elif isinstance(value, bytes):
        payload = value
    else:
        raise ReleaseScopeError("digest input must be text or bytes")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def digest_text(value: str) -> str:
    """Digest one source document using UTF-8 without newline rewriting."""

    if not isinstance(value, str):
        raise ReleaseScopeError("source content must be a string")
    return sha256_digest(value)


def compute_source_digests(sources: Mapping[str, str]) -> dict[str, str]:
    """Compute deterministic per-path digests for a source-content mapping."""

    if not isinstance(sources, Mapping):
        raise ReleaseScopeError("sources must be an object mapping paths to text")
    normalized: dict[str, str] = {}
    for raw_path, content in sorted(sources.items(), key=lambda item: str(item[0])):
        path = _required_text(raw_path, "source path")
        if not isinstance(content, str):
            raise ReleaseScopeError(f"source content for {path!r} must be a string")
        normalized[path] = digest_text(content)
    return normalized


def source_digests(sources: Mapping[str, str]) -> dict[str, str]:
    """Compatibility alias for :func:`compute_source_digests`."""

    return compute_source_digests(sources)


def aggregate_source_digest(digests: Mapping[str, str]) -> str:
    """Digest a sorted path-to-digest map to identify a whole source snapshot."""

    normalized = _freeze_digests(digests)
    return sha256_digest(_canonical_json(dict(normalized)))


def scope_digest(digests: Mapping[str, str]) -> str:
    """Compatibility alias for the aggregate release-scope digest."""

    return aggregate_source_digest(digests)


# Singular/plural spellings are kept as small aliases because source-digest
# helpers are often called from state and migration code with either form.
source_digest = digest_text
compute_scope_digest = aggregate_source_digest
calculate_source_digest = aggregate_source_digest


def _mapping_to_dict(value: Mapping[str, str]) -> dict[str, str]:
    return dict(value.items())


@dataclass(frozen=True, slots=True)
class ReleaseScope:
    """Immutable release-scope lock and its source snapshot identity."""

    status: str = "unlocked"
    source_refs: tuple[str, ...] = ()
    source_digests: Mapping[str, str] = field(default_factory=dict)
    scope_revision: int = 0
    source_snapshot_revision: int = 0
    scope_digest: str = ""
    locked_at: str = ""
    last_user_input_id: str = ""
    amendment_refs: tuple[str, ...] = ()
    plan_item_refs: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    release_scope_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in SCOPE_STATUSES:
            raise ScopeValidationError(f"unknown release-scope status: {self.status}")

        source_refs = _unique_texts(self.source_refs, "source_refs")
        source_digests = _freeze_digests(self.source_digests)
        if self.status == "locked":
            if not source_refs:
                raise ScopeValidationError("locked release scope requires source_refs")
            if set(source_refs) != set(source_digests):
                missing = sorted(set(source_refs) - set(source_digests))
                extra = sorted(set(source_digests) - set(source_refs))
                details = []
                if missing:
                    details.append("missing locked references: " + ", ".join(missing))
                if extra:
                    details.append("unlocked source digests: " + ", ".join(extra))
                raise ScopeValidationError("; ".join(details))
            scope_revision = _revision(self.scope_revision, "scope_revision", minimum=1)
            snapshot_revision = _revision(
                self.source_snapshot_revision, "source_snapshot_revision", minimum=1
            )
            locked_at = _timestamp(self.locked_at, "locked_at") if self.locked_at else ""
        else:
            scope_revision = _revision(self.scope_revision, "scope_revision")
            snapshot_revision = _revision(
                self.source_snapshot_revision, "source_snapshot_revision"
            )
            locked_at = _optional_timestamp(self.locked_at, "locked_at")
            if self.status == "legacy-unlocked":
                if scope_revision != 0 or snapshot_revision != 0:
                    raise ScopeValidationError(
                        "legacy-unlocked release scope must use revision 0"
                    )

        scope_digest = self.scope_digest
        if scope_digest:
            scope_digest = _digest(scope_digest, "scope_digest")
            calculated = aggregate_source_digest(source_digests)
            if scope_digest != calculated:
                raise ScopeValidationError("scope_digest does not match source_digests")
        elif source_digests:
            scope_digest = aggregate_source_digest(source_digests)

        amendment_refs = _unique_texts(self.amendment_refs, "amendment_refs")
        for amendment_ref in amendment_refs:
            if not _AMENDMENT_ID_RE.fullmatch(amendment_ref):
                raise ScopeValidationError("amendment_refs must contain A001-style ids")

        plan_item_refs = _unique_texts(self.plan_item_refs, "plan_item_refs")
        requirement_refs = _unique_texts(self.requirement_refs, "requirement_refs")
        release_scope_refs = _unique_texts(
            self.release_scope_refs, "release_scope_refs"
        )
        last_user_input_id = _optional_input_id(
            self.last_user_input_id, "last_user_input_id"
        )

        object.__setattr__(self, "source_refs", source_refs)
        object.__setattr__(self, "source_digests", source_digests)
        object.__setattr__(self, "scope_revision", scope_revision)
        object.__setattr__(self, "source_snapshot_revision", snapshot_revision)
        object.__setattr__(self, "scope_digest", scope_digest)
        object.__setattr__(self, "locked_at", locked_at)
        object.__setattr__(self, "last_user_input_id", last_user_input_id)
        object.__setattr__(self, "amendment_refs", amendment_refs)
        object.__setattr__(self, "plan_item_refs", plan_item_refs)
        object.__setattr__(self, "requirement_refs", requirement_refs)
        object.__setattr__(self, "release_scope_refs", release_scope_refs)

    @classmethod
    def lock(
        cls,
        *,
        source_refs: Sequence[str] | None = None,
        source_digests: Mapping[str, str] | None = None,
        source_contents: Mapping[str, str] | None = None,
        scope_revision: int = 1,
        source_snapshot_revision: int = 1,
        locked_at: str = "",
        plan_item_refs: Sequence[str] = (),
        requirement_refs: Sequence[str] = (),
        release_scope_refs: Sequence[str] = (),
    ) -> "ReleaseScope":
        """Create a new lock from contents or already computed digests."""

        if source_contents is not None:
            computed = compute_source_digests(source_contents)
            if source_digests is not None and _freeze_digests(source_digests) != computed:
                raise ScopeValidationError(
                    "source_digests do not match supplied source_contents"
                )
            source_digests = computed
            if source_refs is None:
                source_refs = tuple(computed)
        if source_refs is None and source_digests is not None:
            source_refs = tuple(source_digests)
        return cls(
            status="locked",
            source_refs=tuple(source_refs or ()),
            source_digests=source_digests or {},
            scope_revision=scope_revision,
            source_snapshot_revision=source_snapshot_revision,
            locked_at=locked_at,
            plan_item_refs=tuple(plan_item_refs),
            requirement_refs=tuple(requirement_refs),
            release_scope_refs=tuple(release_scope_refs),
        )

    @classmethod
    def from_sources(cls, **kwargs: Any) -> "ReleaseScope":
        """Named constructor alias for callers that build a lock from files."""

        return cls.lock(**kwargs)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ReleaseScope":
        value = _record(record, "release_scope")
        return cls(
            status=str(value.get("status") or "unlocked"),
            source_refs=tuple(value.get("source_refs") or ()),
            source_digests=value.get("source_digests") or {},
            scope_revision=value.get("scope_revision", 0),
            source_snapshot_revision=value.get("source_snapshot_revision", 0),
            scope_digest=str(value.get("scope_digest") or ""),
            locked_at=str(value.get("locked_at") or ""),
            last_user_input_id=str(value.get("last_user_input_id") or ""),
            amendment_refs=tuple(value.get("amendment_refs") or ()),
            plan_item_refs=tuple(value.get("plan_item_refs") or ()),
            requirement_refs=tuple(value.get("requirement_refs") or ()),
            release_scope_refs=tuple(
                value.get("release_scope_refs")
                or value.get("scope_refs")
                or ()
            ),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_refs": list(self.source_refs),
            "source_digests": _mapping_to_dict(self.source_digests),
            "scope_revision": self.scope_revision,
            "source_snapshot_revision": self.source_snapshot_revision,
            "scope_digest": self.scope_digest,
            "locked_at": self.locked_at,
            "last_user_input_id": self.last_user_input_id,
            "amendment_refs": list(self.amendment_refs),
            "plan_item_refs": list(self.plan_item_refs),
            "requirement_refs": list(self.requirement_refs),
            "release_scope_refs": list(self.release_scope_refs),
        }

    @property
    def scope_refs(self) -> tuple[str, ...]:
        """Alias used by task records when they cite the locked contract."""

        return self.release_scope_refs

    def current_source_digests(
        self, sources: Mapping[str, str] | Mapping[str, Any]
    ) -> dict[str, str]:
        """Return normalized current digests from contents or digest values."""

        if not isinstance(sources, Mapping):
            raise ScopeDriftError("current sources must be an object")
        if all(isinstance(value, str) and _DIGEST_RE.fullmatch(value.lower()) for value in sources.values()):
            return dict(_freeze_digests(sources))
        return compute_source_digests(sources)  # type: ignore[arg-type]

    def source_drift(
        self, sources: Mapping[str, str] | Mapping[str, Any]
    ) -> tuple[str, ...]:
        current = self.current_source_digests(sources)
        changed = set(self.source_digests) ^ set(current)
        changed.update(
            path
            for path in set(self.source_digests) & set(current)
            if self.source_digests[path] != current[path]
        )
        return tuple(sorted(changed))

    def assert_source_snapshot(
        self, sources: Mapping[str, str] | Mapping[str, Any]
    ) -> None:
        drift = self.source_drift(sources)
        if drift:
            raise ScopeDriftError(
                "release-scope source snapshot drift detected: " + ", ".join(drift)
            )

    def apply_amendment(self, amendment: "ScopeAmendment") -> "ReleaseScope":
        validate_amendment(self, amendment)
        return replace(
            self,
            source_refs=tuple(amendment.new_source_digests),
            source_digests=amendment.new_source_digests,
            scope_revision=amendment.new_scope_revision,
            source_snapshot_revision=amendment.new_source_snapshot_revision,
            scope_digest=amendment.new_scope_digest,
            last_user_input_id=amendment.user_input_id or self.last_user_input_id,
            amendment_refs=self.amendment_refs + (amendment.amendment_id,),
        )


# Names used by callers that treat the lock as a distinct concept.
ReleaseScopeLock = ReleaseScope
ScopeLock = ReleaseScope


@dataclass(frozen=True, slots=True)
class ScopeAmendment:
    """Immutable record of one source-snapshot or semantic-scope transition."""

    amendment_id: str
    kind: str
    previous_scope_revision: int
    new_scope_revision: int
    previous_source_snapshot_revision: int
    new_source_snapshot_revision: int
    previous_source_digests: Mapping[str, str]
    new_source_digests: Mapping[str, str]
    reason: str
    basis_refs: tuple[str, ...] = ()
    user_input_id: str = ""
    affected_task_ids: tuple[str, ...] = ()
    created_at: str = ""
    previous_scope_digest: str = ""
    new_scope_digest: str = ""
    semantic_equivalence: bool | None = None

    def __post_init__(self) -> None:
        amendment_id = _required_text(self.amendment_id, "amendment_id")
        if not _AMENDMENT_ID_RE.fullmatch(amendment_id):
            raise AmendmentValidationError("amendment_id must match A001-style ids")
        if self.kind not in AMENDMENT_KINDS:
            raise AmendmentValidationError(f"unknown amendment kind: {self.kind}")

        old_scope = _revision(self.previous_scope_revision, "previous_scope_revision")
        new_scope = _revision(self.new_scope_revision, "new_scope_revision")
        old_snapshot = _revision(
            self.previous_source_snapshot_revision,
            "previous_source_snapshot_revision",
        )
        new_snapshot = _revision(
            self.new_source_snapshot_revision,
            "new_source_snapshot_revision",
        )
        old_digests = _freeze_digests(self.previous_source_digests)
        new_digests = _freeze_digests(self.new_source_digests)
        if old_digests == new_digests:
            raise AmendmentValidationError("amendment must change the source snapshot")

        if self.kind in {"editorial", "clarification"}:
            if new_scope != old_scope:
                raise AmendmentValidationError(
                    f"{self.kind} amendment cannot change scope_revision"
                )
            if new_snapshot != old_snapshot + 1:
                raise AmendmentValidationError(
                    f"{self.kind} amendment must increment source_snapshot_revision only"
                )
            if set(old_digests) != set(new_digests):
                raise AmendmentValidationError(
                    f"{self.kind} amendment cannot add or remove locked references"
                )
            if self.kind == "clarification" and not self.basis_refs:
                raise AmendmentValidationError(
                    "clarification amendment requires basis_refs"
                )
        else:
            if new_scope != old_scope + 1:
                raise AmendmentValidationError(
                    "scope-change amendment must increment scope_revision"
                )
            if new_snapshot != old_snapshot + 1:
                raise AmendmentValidationError(
                    "scope-change amendment must increment source_snapshot_revision"
                )
            if not self.user_input_id:
                raise AmendmentValidationError(
                    "scope-change amendment requires user_input_id"
                )

        reason = _required_text(self.reason, "reason")
        basis_refs = _unique_texts(self.basis_refs, "basis_refs")
        user_input_id = _optional_input_id(self.user_input_id, "user_input_id")
        affected_task_ids = _unique_texts(self.affected_task_ids, "affected_task_ids")
        for task_id in affected_task_ids:
            if not _TASK_ID_RE.fullmatch(task_id):
                raise AmendmentValidationError(
                    "affected_task_ids must contain T001-style ids"
                )
        created_at = _optional_timestamp(self.created_at, "created_at")

        old_scope_digest = self.previous_scope_digest or aggregate_source_digest(old_digests)
        new_scope_digest = self.new_scope_digest or aggregate_source_digest(new_digests)
        if _digest(old_scope_digest, "previous_scope_digest") != aggregate_source_digest(old_digests):
            raise AmendmentValidationError("previous_scope_digest does not match sources")
        if _digest(new_scope_digest, "new_scope_digest") != aggregate_source_digest(new_digests):
            raise AmendmentValidationError("new_scope_digest does not match sources")
        semantic_equivalence = self.semantic_equivalence
        if semantic_equivalence is None:
            semantic_equivalence = self.kind != "scope-change"
        if not isinstance(semantic_equivalence, bool):
            raise AmendmentValidationError("semantic_equivalence must be boolean")
        if self.kind != "scope-change" and not semantic_equivalence:
            raise AmendmentValidationError(
                f"{self.kind} amendment must preserve semantic equivalence"
            )
        if self.kind == "scope-change" and semantic_equivalence:
            raise AmendmentValidationError(
                "scope-change amendment cannot claim semantic equivalence"
            )

        object.__setattr__(self, "amendment_id", amendment_id)
        object.__setattr__(self, "previous_scope_revision", old_scope)
        object.__setattr__(self, "new_scope_revision", new_scope)
        object.__setattr__(self, "previous_source_snapshot_revision", old_snapshot)
        object.__setattr__(self, "new_source_snapshot_revision", new_snapshot)
        object.__setattr__(self, "previous_source_digests", old_digests)
        object.__setattr__(self, "new_source_digests", new_digests)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "basis_refs", basis_refs)
        object.__setattr__(self, "user_input_id", user_input_id)
        object.__setattr__(self, "affected_task_ids", affected_task_ids)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "previous_scope_digest", aggregate_source_digest(old_digests))
        object.__setattr__(self, "new_scope_digest", aggregate_source_digest(new_digests))
        object.__setattr__(self, "semantic_equivalence", semantic_equivalence)

    @property
    def old_scope_revision(self) -> int:
        return self.previous_scope_revision

    @property
    def old_source_snapshot_revision(self) -> int:
        return self.previous_source_snapshot_revision

    @property
    def old_source_digests(self) -> Mapping[str, str]:
        return self.previous_source_digests

    @property
    def authorization_input_id(self) -> str:
        return self.user_input_id

    @property
    def amendment_kind(self) -> str:
        return self.kind

    def to_record(self) -> dict[str, Any]:
        return {
            "amendment_id": self.amendment_id,
            "kind": self.kind,
            "previous_scope_revision": self.previous_scope_revision,
            "new_scope_revision": self.new_scope_revision,
            "previous_source_snapshot_revision": self.previous_source_snapshot_revision,
            "new_source_snapshot_revision": self.new_source_snapshot_revision,
            "previous_source_digests": _mapping_to_dict(self.previous_source_digests),
            "new_source_digests": _mapping_to_dict(self.new_source_digests),
            "previous_scope_digest": self.previous_scope_digest,
            "new_scope_digest": self.new_scope_digest,
            "reason": self.reason,
            "basis_refs": list(self.basis_refs),
            "user_input_id": self.user_input_id,
            "affected_task_ids": list(self.affected_task_ids),
            "created_at": self.created_at,
            "semantic_equivalence": self.semantic_equivalence,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ScopeAmendment":
        value = _record(record, "amendment")
        return cls(
            amendment_id=str(value.get("amendment_id") or value.get("id") or ""),
            kind=str(value.get("kind") or value.get("amendment_kind") or ""),
            previous_scope_revision=value.get(
                "previous_scope_revision", value.get("old_scope_revision", 0)
            ),
            new_scope_revision=value.get("new_scope_revision", 0),
            previous_source_snapshot_revision=value.get(
                "previous_source_snapshot_revision",
                value.get("old_source_snapshot_revision", 0),
            ),
            new_source_snapshot_revision=value.get("new_source_snapshot_revision", 0),
            previous_source_digests=value.get(
                "previous_source_digests", value.get("old_source_digests", {})
            ),
            new_source_digests=value.get("new_source_digests", {}),
            previous_scope_digest=str(value.get("previous_scope_digest") or ""),
            new_scope_digest=str(value.get("new_scope_digest") or ""),
            reason=str(value.get("reason") or ""),
            basis_refs=tuple(value.get("basis_refs") or ()),
            user_input_id=str(
                value.get("user_input_id")
                or value.get("authorization_input_id")
                or ""
            ),
            affected_task_ids=tuple(value.get("affected_task_ids") or ()),
            created_at=str(value.get("created_at") or ""),
            semantic_equivalence=value.get("semantic_equivalence"),
        )


ReleaseScopeAmendment = ScopeAmendment
AmendmentRecord = ScopeAmendment


def validate_amendment(scope: ReleaseScope, amendment: ScopeAmendment) -> None:
    """Validate that an immutable amendment starts at exactly ``scope``."""

    if not isinstance(scope, ReleaseScope):
        scope = ReleaseScope.from_record(scope)  # type: ignore[arg-type]
    if not isinstance(amendment, ScopeAmendment):
        amendment = ScopeAmendment.from_record(amendment)  # type: ignore[arg-type]
    if scope.status != "locked":
        raise AmendmentValidationError("amendments require a locked release scope")
    if amendment.amendment_id in scope.amendment_refs:
        raise AmendmentValidationError("amendment_id has already been applied")
    if amendment.previous_scope_revision != scope.scope_revision:
        raise AmendmentValidationError("amendment starts from a different scope_revision")
    if (
        amendment.previous_source_snapshot_revision
        != scope.source_snapshot_revision
    ):
        raise AmendmentValidationError(
            "amendment starts from a different source_snapshot_revision"
        )
    if amendment.previous_source_digests != scope.source_digests:
        raise AmendmentValidationError("amendment starts from a different source snapshot")
    if (
        amendment.kind == "scope-change"
        and scope.last_user_input_id
        and amendment.user_input_id == scope.last_user_input_id
    ):
        raise AmendmentValidationError(
            "scope-change user_input_id has already authorized an earlier amendment"
        )
    if amendment.kind in {"editorial", "clarification"} and amendment.user_input_id:
        raise AmendmentValidationError(
            f"{amendment.kind} amendment cannot use a scope-change authorization input"
        )


def create_amendment(
    scope: ReleaseScope,
    *,
    amendment_id: str,
    kind: str,
    reason: str,
    new_source_digests: Mapping[str, str] | None = None,
    new_source_contents: Mapping[str, str] | None = None,
    basis_refs: Sequence[str] = (),
    user_input_id: str = "",
    affected_task_ids: Sequence[str] = (),
    created_at: str = "",
    semantic_equivalence: bool | None = None,
) -> ScopeAmendment:
    """Build an amendment whose ``previous_*`` fields come from ``scope``."""

    if not isinstance(scope, ReleaseScope):
        scope = ReleaseScope.from_record(scope)  # type: ignore[arg-type]
    if new_source_contents is not None:
        computed = compute_source_digests(new_source_contents)
        if new_source_digests is not None and _freeze_digests(new_source_digests) != computed:
            raise AmendmentValidationError(
                "new_source_digests do not match supplied new_source_contents"
            )
        new_source_digests = computed
    if new_source_digests is None:
        raise AmendmentValidationError("new source digests or contents are required")
    if semantic_equivalence is None:
        semantic_equivalence = kind != "scope-change"
    return ScopeAmendment(
        amendment_id=amendment_id,
        kind=kind,
        previous_scope_revision=scope.scope_revision,
        new_scope_revision=(scope.scope_revision + (1 if kind == "scope-change" else 0)),
        previous_source_snapshot_revision=scope.source_snapshot_revision,
        new_source_snapshot_revision=scope.source_snapshot_revision + 1,
        previous_source_digests=scope.source_digests,
        new_source_digests=new_source_digests,
        reason=reason,
        basis_refs=tuple(basis_refs),
        user_input_id=user_input_id,
        affected_task_ids=tuple(affected_task_ids),
        created_at=created_at,
        previous_scope_digest=scope.scope_digest,
        semantic_equivalence=semantic_equivalence,
    )


def amend_scope(*args: Any, **kwargs: Any) -> tuple[ReleaseScope, ScopeAmendment]:
    """Create and apply one amendment, returning ``(new_scope, record)``."""

    scope = args[0] if args else kwargs.pop("scope")
    amendment = create_amendment(scope, **kwargs)
    return scope.apply_amendment(amendment), amendment


amend_release_scope = amend_scope


def apply_amendment(scope: ReleaseScope, amendment: ScopeAmendment) -> ReleaseScope:
    return scope.apply_amendment(amendment)


def validate_locked_scope(
    scope: ReleaseScope | Mapping[str, Any],
    *,
    current_sources: Mapping[str, str] | Mapping[str, Any] | None = None,
) -> ReleaseScope:
    """Validate a lock and optionally its current source snapshot."""

    normalized = scope if isinstance(scope, ReleaseScope) else ReleaseScope.from_record(scope)
    if normalized.status != "locked":
        raise ScopeValidationError("release scope is not locked")
    if current_sources is not None:
        normalized.assert_source_snapshot(current_sources)
    return normalized


def check_source_drift(
    scope: ReleaseScope | Mapping[str, Any],
    current_sources: Mapping[str, str] | Mapping[str, Any],
) -> ReleaseScope:
    return validate_locked_scope(scope, current_sources=current_sources)


def detect_source_drift(
    scope: ReleaseScope | Mapping[str, Any],
    current_sources: Mapping[str, str] | Mapping[str, Any],
) -> tuple[str, ...]:
    normalized = scope if isinstance(scope, ReleaseScope) else ReleaseScope.from_record(scope)
    return normalized.source_drift(current_sources)


@dataclass(frozen=True, slots=True)
class TaskProvenance:
    """Immutable task origin and evidence references."""

    task_origin: str
    release_scope_revision: int
    plan_item_refs: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    scope_refs: tuple[str, ...] = ()
    source_finding: str = ""
    authorization_input_id: str = ""
    why_fix_now: str = ""
    operational_reason: str = ""
    origin: str = ""
    contract_relation: str = ""
    release_effect: str = ""
    remediation_round: int = 0
    fact_status: str = ""
    disposition: str = ""

    def __post_init__(self) -> None:
        if self.task_origin not in TASK_ORIGINS:
            raise TaskAuthorizationError(f"unknown task_origin: {self.task_origin}")
        scope_revision = _revision(
            self.release_scope_revision, "release_scope_revision"
        )
        plan_item_refs = _unique_texts(self.plan_item_refs, "plan_item_refs")
        requirement_refs = _unique_texts(self.requirement_refs, "requirement_refs")
        scope_refs = _unique_texts(self.scope_refs, "scope_refs")
        source_finding = self.source_finding.strip() if isinstance(self.source_finding, str) else ""
        authorization_input_id = _optional_input_id(
            self.authorization_input_id, "authorization_input_id"
        )
        why_fix_now = self.why_fix_now.strip() if isinstance(self.why_fix_now, str) else ""
        operational_reason = (
            self.operational_reason.strip()
            if isinstance(self.operational_reason, str)
            else ""
        )
        origin = self.origin.strip() if isinstance(self.origin, str) else ""
        if origin and origin not in FINDING_ORIGINS:
            raise TaskAuthorizationError(f"unknown finding origin: {origin}")
        contract_relation = (
            self.contract_relation.strip() if isinstance(self.contract_relation, str) else ""
        )
        if contract_relation and contract_relation not in CONTRACT_RELATIONS:
            raise TaskAuthorizationError(
                f"unknown contract_relation: {contract_relation}"
            )
        release_effect = (
            self.release_effect.strip() if isinstance(self.release_effect, str) else ""
        )
        if release_effect and release_effect not in RELEASE_EFFECTS:
            raise TaskAuthorizationError(f"unknown release_effect: {release_effect}")
        remediation_round = _revision(
            self.remediation_round, "remediation_round"
        )
        fact_status = self.fact_status.strip() if isinstance(self.fact_status, str) else ""
        if fact_status and fact_status not in FACT_STATUSES:
            raise TaskAuthorizationError(f"unknown fact_status: {fact_status}")
        disposition = self.disposition.strip() if isinstance(self.disposition, str) else ""
        if disposition and disposition not in TASK_DISPOSITIONS:
            raise TaskAuthorizationError(f"unknown disposition: {disposition}")

        object.__setattr__(self, "release_scope_revision", scope_revision)
        object.__setattr__(self, "plan_item_refs", plan_item_refs)
        object.__setattr__(self, "requirement_refs", requirement_refs)
        object.__setattr__(self, "scope_refs", scope_refs)
        object.__setattr__(self, "source_finding", source_finding)
        object.__setattr__(self, "authorization_input_id", authorization_input_id)
        object.__setattr__(self, "why_fix_now", why_fix_now)
        object.__setattr__(self, "operational_reason", operational_reason)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "contract_relation", contract_relation)
        object.__setattr__(self, "release_effect", release_effect)
        object.__setattr__(self, "remediation_round", remediation_round)
        object.__setattr__(self, "fact_status", fact_status)
        object.__setattr__(self, "disposition", disposition)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "TaskProvenance":
        value = _record(record, "task provenance")
        nested = value.get("provenance")
        if isinstance(nested, Mapping):
            merged = dict(value)
            merged.update(nested)
            value = merged
        return cls(
            task_origin=str(value.get("task_origin") or ""),
            release_scope_revision=value.get("release_scope_revision", 0),
            plan_item_refs=tuple(value.get("plan_item_refs") or ()),
            requirement_refs=tuple(value.get("requirement_refs") or ()),
            scope_refs=tuple(
                value.get("scope_refs")
                or value.get("release_scope_refs")
                or value.get("release_scope_reference")
                and (value.get("release_scope_reference"),)
                or ()
            ),
            source_finding=str(value.get("source_finding") or ""),
            authorization_input_id=str(
                value.get("authorization_input_id")
                or value.get("user_input_id")
                or ""
            ),
            why_fix_now=str(value.get("why_fix_now") or ""),
            operational_reason=str(value.get("operational_reason") or ""),
            origin=str(value.get("origin") or ""),
            contract_relation=str(value.get("contract_relation") or ""),
            release_effect=str(value.get("release_effect") or ""),
            remediation_round=value.get("remediation_round", 0),
            fact_status=str(value.get("fact_status") or ""),
            disposition=str(value.get("disposition") or ""),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "task_origin": self.task_origin,
            "release_scope_revision": self.release_scope_revision,
            "plan_item_refs": list(self.plan_item_refs),
            "requirement_refs": list(self.requirement_refs),
            "scope_refs": list(self.scope_refs),
            "source_finding": self.source_finding,
            "authorization_input_id": self.authorization_input_id,
            "why_fix_now": self.why_fix_now,
            "operational_reason": self.operational_reason,
            "origin": self.origin,
            "contract_relation": self.contract_relation,
            "release_effect": self.release_effect,
            "remediation_round": self.remediation_round,
            "fact_status": self.fact_status,
            "disposition": self.disposition,
        }


TaskProvenanceRecord = TaskProvenance


def _scope_from_any(scope: ReleaseScope | Mapping[str, Any] | None) -> ReleaseScope:
    if scope is None:
        raise TaskAuthorizationError("a locked release scope is required")
    if isinstance(scope, ReleaseScope):
        return scope
    return ReleaseScope.from_record(scope)


def _task_record(task: Mapping[str, Any] | TaskProvenance) -> Mapping[str, Any]:
    if isinstance(task, TaskProvenance):
        return task.to_record()
    return _record(task, "task")


def _available_refs(
    scope_values: Sequence[str], supplied_values: Sequence[str] | None, field_name: str
) -> set[str]:
    values = set(scope_values)
    if supplied_values:
        values.update(_unique_texts(supplied_values, field_name))
    return values


def _value_bool(record: Mapping[str, Any], *names: str) -> bool:
    for name in names:
        value = record.get(name)
        if isinstance(value, bool):
            if value:
                return True
        elif value not in (None, "", 0, False):
            raise TaskAuthorizationError(f"{name} must be boolean")
    return False


def _path_changes_product_or_release(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized.startswith((".ai/", "reports/", "qa/", "validation/", "results/", "follow-ups/")):
        return False
    if normalized.startswith(("tests/", "test/")):
        return False
    if normalized.startswith(("src/", "lib/", "app/", "apps/", "packages/", "scripts/")):
        return True
    if normalized.startswith(("dist/", "build/", "artifacts/", "release/", "package/")):
        return True
    if normalized.startswith((".github/", ".gitlab/", "docker/", "deploy/")):
        return True
    if normalized in {"VERSION", "README.md", "pyproject.toml", "package.json", "setup.py"}:
        return True
    if normalized.startswith(("docs/", "references/", "skills/")):
        return True
    return False


def _operational_changes_product_or_release(record: Mapping[str, Any]) -> bool:
    if _value_bool(
        record,
        "changes_product",
        "changes_product_behavior",
        "product_changes",
        "changes_release_artifact",
        "changes_release_artifacts",
        "release_artifact_changes",
    ):
        return True
    for field_name in ("write_allow", "changed_files", "product_paths", "release_artifact_paths"):
        value = record.get(field_name)
        if value is None:
            continue
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TaskAuthorizationError(f"{field_name} must be an array")
        if any(_path_changes_product_or_release(str(path)) for path in value):
            return True
    return False


def _merge_task_and_provenance(record: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    nested = record.get("provenance")
    if isinstance(nested, Mapping):
        nested_values = dict(nested)
        for key, value in nested_values.items():
            merged.setdefault(key, value)
    return merged


def validate_task_provenance(
    task: Mapping[str, Any] | TaskProvenance,
    scope: ReleaseScope | Mapping[str, Any] | None = None,
    *,
    release_scope: ReleaseScope | Mapping[str, Any] | None = None,
    locked_plan_item_refs: Sequence[str] = (),
    locked_requirement_refs: Sequence[str] = (),
    locked_finding_ids: Sequence[str] = (),
    plan_item_refs: Sequence[str] | None = None,
    requirement_refs: Sequence[str] | None = None,
    finding_ids: Sequence[str] | None = None,
    legacy_mode: bool = False,
    allow_legacy_unclassified: bool = False,
    **_: Any,
) -> TaskProvenance:
    """Validate provenance against a locked scope without creating a task.

    ``locked_*`` arguments let the caller supply the current PLAN,
    requirement, and finding indexes before those indexes are wired into the
    main CLI.  Scope records may carry the same references directly.
    """

    normalized_scope = _scope_from_any(scope or release_scope)
    record = _merge_task_and_provenance(_task_record(task))
    try:
        provenance = TaskProvenance.from_record(record)
    except ReleaseScopeError as exc:
        raise TaskAuthorizationError(str(exc)) from exc

    if normalized_scope.status == "legacy-unlocked":
        if provenance.task_origin != "legacy-unclassified":
            raise TaskAuthorizationError(
                "legacy-unlocked scope only accepts legacy-unclassified task provenance"
            )
        if provenance.release_scope_revision != 0:
            raise TaskAuthorizationError(
                "legacy-unclassified task must use release_scope_revision 0"
            )
        return provenance

    if normalized_scope.status != "locked":
        raise TaskAuthorizationError("task creation requires a locked release scope")
    if provenance.task_origin == "legacy-unclassified":
        if not (legacy_mode and allow_legacy_unclassified):
            raise TaskAuthorizationError(
                "legacy-unclassified tasks cannot be created in a new-policy loop"
            )
    if provenance.release_scope_revision != normalized_scope.scope_revision:
        raise TaskAuthorizationError(
            "task release_scope_revision does not match the locked scope revision"
        )

    available_plan = _available_refs(
        normalized_scope.plan_item_refs,
        locked_plan_item_refs or plan_item_refs,
        "locked_plan_item_refs",
    )
    available_requirements = _available_refs(
        normalized_scope.requirement_refs,
        locked_requirement_refs or requirement_refs,
        "locked_requirement_refs",
    )
    available_findings = set(
        _unique_texts(locked_finding_ids or finding_ids, "locked_finding_ids")
    )
    available_scope_refs = set(normalized_scope.release_scope_refs)

    for reference in provenance.plan_item_refs:
        if reference not in available_plan:
            raise TaskAuthorizationError(
                f"missing locked PLAN reference: {reference}"
            )
    for reference in provenance.requirement_refs:
        if reference not in available_requirements:
            raise TaskAuthorizationError(
                f"missing locked requirement reference: {reference}"
            )
    if provenance.source_finding and available_findings and provenance.source_finding not in available_findings:
        raise TaskAuthorizationError(
            f"missing locked finding reference: {provenance.source_finding}"
        )

    if provenance.task_origin == "planned":
        if not provenance.plan_item_refs and not provenance.requirement_refs:
            raise TaskAuthorizationError(
                "planned task requires plan_item_refs or requirement_refs"
            )
        if _scope_expansion_requested(record):
            raise TaskAuthorizationError(
                "planned task cannot expand the locked release scope"
            )

    elif provenance.task_origin == "finding":
        _validate_finding_task(
            provenance, record, available_requirements, available_scope_refs
        )

    elif provenance.task_origin == "user-amendment":
        if not provenance.authorization_input_id:
            raise TaskAuthorizationError(
                "user-amendment task requires authorization_input_id"
            )
        if normalized_scope.last_user_input_id != provenance.authorization_input_id:
            raise TaskAuthorizationError(
                "user-amendment authorization_input_id is not the latest locked input"
            )
        if not normalized_scope.amendment_refs:
            raise TaskAuthorizationError(
                "user-amendment task requires a recorded scope amendment reference"
            )

    elif provenance.task_origin == "operational":
        if not provenance.operational_reason:
            raise TaskAuthorizationError(
                "operational task requires operational_reason"
            )
        if _operational_changes_product_or_release(record):
            raise TaskAuthorizationError(
                "operational task cannot change product or release artifacts"
            )

    elif provenance.task_origin == "legacy-unclassified":
        if not (legacy_mode and allow_legacy_unclassified):
            raise TaskAuthorizationError(
                "legacy-unclassified task creation requires explicit legacy mode"
            )

    if _scope_expansion_requested(record) and provenance.task_origin != "user-amendment":
        raise TaskAuthorizationError(
            "scope expansion requires a user-amendment task with authorization"
        )
    return provenance


def _scope_expansion_requested(record: Mapping[str, Any]) -> bool:
    for name in (
        "scope_expanding",
        "expands_scope",
        "scope_expansion",
        "release_scope_expansion",
    ):
        value = record.get(name)
        if isinstance(value, bool) and value:
            return True
        if value not in (None, "", False, 0) and not isinstance(value, bool):
            raise TaskAuthorizationError(f"{name} must be boolean")
    relation = record.get("contract_relation")
    return relation == "outside_release" or record.get("release_effect") == "scope_expanding"


def _validate_finding_task(
    provenance: TaskProvenance,
    record: Mapping[str, Any],
    available_requirements: set[str],
    available_scope_refs: set[str],
) -> None:
    if not provenance.source_finding:
        raise TaskAuthorizationError("finding task requires source_finding")
    if not provenance.requirement_refs and not provenance.scope_refs:
        raise TaskAuthorizationError(
            "finding task requires requirement_refs or an explicit release scope reference"
        )
    if not provenance.requirement_refs and provenance.scope_refs:
        locked_scope_refs = set(available_scope_refs)
        if locked_scope_refs and not set(provenance.scope_refs).intersection(locked_scope_refs):
            raise TaskAuthorizationError(
                "finding task does not reference the locked release scope"
            )
    if provenance.contract_relation != "in_scope":
        raise TaskAuthorizationError(
            "only in_scope findings may create remediation tasks"
        )
    if provenance.origin not in {"introduced", "diff-expanded-pre-existing"}:
        raise TaskAuthorizationError(
            "finding task origin must be introduced or diff-expanded-pre-existing"
        )
    if provenance.fact_status != "confirmed":
        raise TaskAuthorizationError(
            "finding task requires fact_status confirmed"
        )
    if provenance.disposition != "fix_now":
        raise TaskAuthorizationError(
            "finding task requires disposition fix_now"
        )
    if not provenance.why_fix_now:
        raise TaskAuthorizationError("finding task requires why_fix_now")
    if provenance.release_effect not in RELEASE_EFFECTS:
        raise TaskAuthorizationError(
            "finding task requires release_effect blocking or non_blocking"
        )
    if provenance.remediation_round < 1:
        raise TaskAuthorizationError(
            "finding task requires a positive remediation_round"
        )
    if available_requirements and any(
        reference not in available_requirements for reference in provenance.requirement_refs
    ):
        missing = sorted(
            reference
            for reference in provenance.requirement_refs
            if reference not in available_requirements
        )
        raise TaskAuthorizationError(
            "missing locked requirement reference: " + ", ".join(missing)
        )
    if record.get("contract_relation") == "outside_release":
        raise TaskAuthorizationError(
            "outside_release finding cannot be promoted to a task without a scope amendment"
        )


def authorize_task_creation(
    task: Mapping[str, Any] | TaskProvenance,
    scope: ReleaseScope | Mapping[str, Any] | None = None,
    *,
    release_scope: ReleaseScope | Mapping[str, Any] | None = None,
    existing_task: Mapping[str, Any] | TaskProvenance | None = None,
    **kwargs: Any,
) -> TaskProvenance:
    """Central creation preflight used by every future task path.

    The function returns the normalized immutable provenance record.  Callers
    that only need a yes/no answer can use :func:`is_task_creation_authorized`.
    ``existing_task`` is intentionally accepted here so a direct update path
    cannot bypass the same immutable-provenance rule.
    """

    provenance = validate_task_provenance(
        task,
        scope,
        release_scope=release_scope,
        **kwargs,
    )
    if existing_task is not None:
        assert_provenance_unchanged(existing_task, task)
    return provenance


def validate_provenance_update(
    existing_task: Mapping[str, Any] | TaskProvenance,
    updated_task: Mapping[str, Any] | TaskProvenance,
    scope: ReleaseScope | Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> TaskProvenance:
    """Reject deletion or rewriting of task provenance during task update."""

    old = TaskProvenance.from_record(_task_record(existing_task))
    new = TaskProvenance.from_record(_task_record(updated_task))
    if old != new:
        raise ProvenanceMutationError(
            "task provenance is immutable; close the old task and create a new one"
        )
    if scope is None and kwargs.get("release_scope") is None:
        return new
    return validate_task_provenance(new, scope, **kwargs)


def assert_provenance_unchanged(
    existing_task: Mapping[str, Any] | TaskProvenance,
    updated_task: Mapping[str, Any] | TaskProvenance,
) -> None:
    """Raise when any provenance field is removed or changed."""

    old = TaskProvenance.from_record(_task_record(existing_task))
    new = TaskProvenance.from_record(_task_record(updated_task))
    if old != new:
        raise ProvenanceMutationError(
            "task provenance is immutable; close the old task and create a new one"
        )


def is_task_creation_authorized(
    task: Mapping[str, Any] | TaskProvenance,
    scope: ReleaseScope | Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> bool:
    """Boolean convenience wrapper around :func:`authorize_task_creation`."""

    try:
        authorize_task_creation(task, scope, **kwargs)
    except ReleaseScopeError:
        return False
    return True


def make_task_provenance(**kwargs: Any) -> TaskProvenance:
    """Construct a normalized provenance record before authorization."""

    return TaskProvenance(**kwargs)


__all__ = [
    "AMENDMENT_KINDS",
    "AmendmentRecord",
    "AmendmentValidationError",
    "CONTRACT_RELATIONS",
    "FINDING_ORIGINS",
    "ReleaseScope",
    "ReleaseScopeAmendment",
    "ReleaseScopeError",
    "ReleaseScopeLock",
    "ScopeAmendment",
    "ScopeDriftError",
    "ScopeLock",
    "SCOPE_STATUSES",
    "ScopeValidationError",
    "TaskAuthorizationError",
    "TaskProvenanceError",
    "TASK_ORIGINS",
    "TaskOrigins",
    "TaskProvenance",
    "TaskProvenanceRecord",
    "ProvenanceMutationError",
    "aggregate_source_digest",
    "amend_release_scope",
    "amend_scope",
    "apply_amendment",
    "assert_provenance_unchanged",
    "authorize_task_creation",
    "check_source_drift",
    "compute_source_digests",
    "compute_scope_digest",
    "calculate_source_digest",
    "create_amendment",
    "digest_text",
    "detect_source_drift",
    "is_task_creation_authorized",
    "make_task_provenance",
    "scope_digest",
    "sha256_digest",
    "source_digest",
    "source_digests",
    "validate_amendment",
    "validate_locked_scope",
    "validate_provenance_update",
    "validate_task_provenance",
]


# Keep the public spelling useful to callers while retaining the more explicit
# ``TASK_ORIGINS`` constant above.
TaskOrigins = TASK_ORIGINS
