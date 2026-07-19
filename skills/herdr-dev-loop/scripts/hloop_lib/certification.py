"""Pure primitives for the manual final-review certification gate.

The command layer owns files, Git observations, user-input records, and state
transactions.  This module deliberately stays in memory: it models the
immutable plan used to prepare a certification, recomputes the completeness of
the structured manifest, and applies a reopen request to a deep copy only
after all of its invariants have passed.

The existing :mod:`hloop_lib.review` module remains the source of truth for
lane, finding, and verification semantics.  The types below add the final
review identity around that model instead of trusting a chat transcript or a
self-reported finding count.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

from . import config as hloop_config
from . import release_scope as hloop_release_scope
from .review import (
    CRITICAL_SEVERITIES,
    ExternalReviewProtocolAdapter,
    ManifestCompleteness,
    ReviewManifest,
    ReviewModelError,
    SUPPORTED_PROVIDERS,
    review_manifest_policy_counts,
    validate_manifest_policy,
)


MANUAL_FINAL_PROTOCOL = "codex-review-multi-v2"
CERTIFICATION_STATUSES = frozenset({"passed", "incomplete", "failed"})
PATCH_VERDICTS = frozenset({"passed", "failed", "incomplete"})
REOPENABLE_PHASES = frozenset(
    {
        "review_convergence",
        "review_convergence_exhausted",
        "manual_final_review_failed",
        "manual_final_review_incomplete",
    }
)
REOPEN_ACTIONS = frozenset(
    {
        "remediate",
        "disable-feature",
        "mark-experimental",
        "scope-amend",
        "retry-certification",
        "abort",
    }
)
SCOPE_AMENDMENT_KINDS = frozenset({"editorial", "clarification", "scope-change"})
_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_INPUT_ID_RE = re.compile(r"^U[0-9]{4}$")
_REVIEW_EXECUTION_ID_RE = re.compile(r"^R[0-9]{3}$")
MANUAL_FINAL_EXECUTION_POLICIES = frozenset(
    {"independent", "reuse_epoch_reviewer"}
)
FINAL_PROCESS_KINDS = frozenset({"coordinator", "discovery", "verifier"})


class CertificationModelError(ValueError):
    """Raised when a plan, manifest, or reopen request is malformed."""


# A descriptive alias keeps callers that use the shorter name compatible with
# the other pure HLoop modules, while the longer name is clearer in tracebacks.
CertificationError = CertificationModelError


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CertificationModelError(f"{field_name} must be a non-empty string")
    return value.strip()


def _text_tuple(
    values: Sequence[Any], field_name: str, *, unique: bool = True
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise CertificationModelError(f"{field_name} must be an array of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if unique and len(set(normalized)) != len(normalized):
        raise CertificationModelError(f"{field_name} must not contain duplicates")
    return normalized


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CertificationModelError(f"{field_name} must be an object")
    return value


def _items(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CertificationModelError(f"{field_name} must be an array")
    return tuple(value)


def _required_fields(
    record: Mapping[str, Any], field_name: str, fields: Sequence[str]
) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise CertificationModelError(
            f"{field_name} is missing required fields: {', '.join(missing)}"
        )


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CertificationModelError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _positive_int(value: Any, field_name: str) -> int:
    result = _non_negative_int(value, field_name)
    if result < 1:
        raise CertificationModelError(f"{field_name} must be a positive integer")
    return result


def _digest(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _DIGEST_RE.fullmatch(text):
        raise CertificationModelError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return text


def canonical_json(value: Any) -> str:
    """Serialize JSON identity data deterministically for hashing."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CertificationModelError(
            f"value is not canonically JSON serializable: {exc}"
        ) from exc


def canonical_digest(value: Any) -> str:
    """Return the stable, explicitly labelled SHA-256 digest of ``value``."""

    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _coerce_int(value: Any, field_name: str, *, default: int) -> int:
    return default if value is None else _non_negative_int(value, field_name)


def _canonical_agent_identity(
    value: Mapping[str, Any] | None,
    *,
    requested: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    if not value:
        return hloop_config.project_agent_identity(requested).as_dict()
    record = _record(value, field_name)
    if set(record) != {
        "requested",
        "observed",
        "attested",
        "status",
        "verified",
        "issues",
    }:
        raise CertificationModelError(f"{field_name} fields are not canonical")
    try:
        projected = hloop_config.project_agent_identity(
            requested,
            observed=_record(record["observed"], f"{field_name}.observed"),
            attested=_record(record["attested"], f"{field_name}.attested"),
        ).as_dict()
    except hloop_config.ConfigValidationError as exc:
        raise CertificationModelError(f"{field_name} is invalid: {exc}") from exc
    if dict(record) != projected:
        raise CertificationModelError(
            f"{field_name} does not match requested/observed/attested evidence"
        )
    return projected


@dataclass(frozen=True, slots=True)
class FinalReviewProcessPlan:
    """One manual-final coordinator, discovery lane, or verifier identity."""

    process_id: str
    process_kind: str
    agent_label: str
    provider: str
    model: str
    effort: str
    config_sources: Mapping[str, str]
    config_provenance: Mapping[str, Any]
    agent_identity: Mapping[str, Any] = field(default_factory=dict)
    attestation_required: bool = True

    def __post_init__(self) -> None:
        for field_name in ("process_id", "agent_label", "model", "effort"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if self.process_kind not in FINAL_PROCESS_KINDS:
            raise CertificationModelError(
                f"unsupported final review process kind: {self.process_kind}"
            )
        if self.provider not in SUPPORTED_PROVIDERS:
            raise CertificationModelError(
                f"unsupported final review process provider: {self.provider}"
            )
        if not isinstance(self.attestation_required, bool):
            raise CertificationModelError("attestation_required must be boolean")
        if not self.attestation_required:
            raise CertificationModelError(
                "manual final process plans require identity attestation"
            )
        expected_config_fields = {"provider", "model", "effort"}
        sources = _record(self.config_sources, "final review process config_sources")
        provenance = _record(
            self.config_provenance, "final review process config_provenance"
        )
        if set(sources) != expected_config_fields or set(provenance) != expected_config_fields:
            raise CertificationModelError(
                "manual final process config sources and provenance must cover "
                "provider/model/effort exactly"
            )
        canonical_sources: dict[str, str] = {}
        canonical_provenance: dict[str, list[dict[str, Any]]] = {}
        for field_name in sorted(expected_config_fields):
            canonical_sources[field_name] = _required_text(
                sources[field_name], f"config_sources.{field_name}"
            )
            history = provenance[field_name]
            if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
                raise CertificationModelError(
                    f"config_provenance.{field_name} must be an array"
                )
            canonical_provenance[field_name] = [
                json.loads(canonical_json(_record(item, f"config_provenance.{field_name}")))
                for item in history
            ]
        object.__setattr__(self, "config_sources", canonical_sources)
        object.__setattr__(self, "config_provenance", canonical_provenance)
        requested = {
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
        }
        identity = _canonical_agent_identity(
            self.agent_identity,
            requested=requested,
            field_name="final review process agent_identity",
        )
        if identity["requested"] != requested:
            raise CertificationModelError(
                "final review process identity does not match planned provider/model/effort"
            )
        if identity["status"] != "requested-only" or identity["verified"]:
            raise CertificationModelError(
                "final review process plans must preserve requested-only identity"
            )
        object.__setattr__(self, "agent_identity", identity)

    def to_record(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "process_kind": self.process_kind,
            "agent_label": self.agent_label,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "config_sources": dict(self.config_sources),
            "config_provenance": dict(self.config_provenance),
            "agent_identity": dict(self.agent_identity),
            "attestation_required": self.attestation_required,
        }

    @classmethod
    def from_record(cls, value: Any) -> "FinalReviewProcessPlan":
        if isinstance(value, cls):
            return value
        record = _record(value, "final review process plan")
        expected = {
            "process_id",
            "process_kind",
            "agent_label",
            "provider",
            "model",
            "effort",
            "config_sources",
            "config_provenance",
            "agent_identity",
            "attestation_required",
        }
        if set(record) != expected:
            raise CertificationModelError(
                "final review process plan fields are not canonical"
            )
        return cls(**{key: record[key] for key in expected})


@dataclass(frozen=True, slots=True)
class FinalReviewProcessIdentity:
    """Observed and provider-attested identity for one manual-final process."""

    process_id: str
    agent_identity: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "process_id", _required_text(self.process_id, "process_id")
        )
        record = _record(self.agent_identity, "final review process identity")
        requested = _record(
            record.get("requested"), "final review process identity.requested"
        )
        object.__setattr__(
            self,
            "agent_identity",
            _canonical_agent_identity(
                record,
                requested=requested,
                field_name="final review process identity",
            ),
        )

    @property
    def verified(self) -> bool:
        return bool(self.agent_identity.get("verified")) and str(
            self.agent_identity.get("status") or ""
        ) == "attested"

    def to_record(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "agent_identity": dict(self.agent_identity),
        }

    @classmethod
    def from_record(cls, value: Any) -> "FinalReviewProcessIdentity":
        if isinstance(value, cls):
            return value
        record = _record(value, "final review process identity")
        if set(record) != {"process_id", "agent_identity"}:
            raise CertificationModelError(
                "final review process identity fields are not canonical"
            )
        return cls(
            process_id=record["process_id"],
            agent_identity=record["agent_identity"],
        )


@dataclass(frozen=True, slots=True)
class FinalReviewLane:
    """One lane frozen into a manual final-review plan."""

    provider: str
    lane_id: str
    purpose: str
    agent_label: str

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise CertificationModelError(f"unsupported lane provider: {self.provider}")
        for field_name in ("lane_id", "purpose", "agent_label"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.lane_id

    def to_record(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "lane_id": self.lane_id,
            "purpose": self.purpose,
            "agent_label": self.agent_label,
        }

    @classmethod
    def from_record(cls, value: Any) -> "FinalReviewLane":
        record = _record(value, "final review lane")
        _required_fields(
            record,
            "final review lane",
            ("provider", "lane_id", "purpose", "agent_label"),
        )
        return cls(
            provider=record["provider"],
            lane_id=record["lane_id"],
            purpose=record["purpose"],
            agent_label=record["agent_label"],
        )


def _coerce_lane(value: Any, index: int) -> FinalReviewLane:
    if isinstance(value, FinalReviewLane):
        return value
    if isinstance(value, Mapping):
        return FinalReviewLane.from_record(value)
    # Accept the existing review lane type without importing its concrete
    # class.  This keeps the boundary useful to callers that already have a
    # ReviewGroupPlan and avoids a second conversion API.
    if all(hasattr(value, name) for name in ("provider", "lane_id", "purpose", "agent_label")):
        return FinalReviewLane(
            provider=value.provider,
            lane_id=value.lane_id,
            purpose=value.purpose,
            agent_label=value.agent_label,
        )
    if isinstance(value, str):
        lane_id = _required_text(value, f"lane_plan[{index}]")
        return FinalReviewLane(
            provider="codex",
            lane_id=lane_id,
            purpose=lane_id,
            agent_label=lane_id,
        )
    raise CertificationModelError(
        f"lane_plan[{index}] must be a FinalReviewLane, object, or lane id"
    )


@dataclass(frozen=True, slots=True)
class ManualFinalExecutionProvenance:
    """Fixed-target execution and source identity for manual-final evidence."""

    execution_policy: str
    execution_id: str
    source_kind: str
    source_execution_id: str
    source_artifact_ref: str
    source_artifact_digest: str
    target_sha: str
    protocol_adapter: ExternalReviewProtocolAdapter

    def __post_init__(self) -> None:
        if self.execution_policy not in MANUAL_FINAL_EXECUTION_POLICIES:
            raise CertificationModelError(
                f"unsupported manual-final execution_policy: {self.execution_policy}"
            )
        for field_name in (
            "execution_id",
            "source_kind",
            "source_execution_id",
            "source_artifact_ref",
            "target_sha",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if not _REVIEW_EXECUTION_ID_RE.fullmatch(self.execution_id):
            raise CertificationModelError("execution_id must match RNNN")
        if not _REVIEW_EXECUTION_ID_RE.fullmatch(self.source_execution_id):
            raise CertificationModelError("source_execution_id must match RNNN")
        artifact_ref = PurePosixPath(self.source_artifact_ref)
        if artifact_ref.is_absolute() or ".." in artifact_ref.parts:
            raise CertificationModelError(
                "source_artifact_ref must be a safe relative path"
            )
        object.__setattr__(
            self,
            "source_artifact_digest",
            _digest(self.source_artifact_digest, "source_artifact_digest"),
        )
        adapter = self.protocol_adapter
        if not isinstance(adapter, ExternalReviewProtocolAdapter):
            try:
                adapter = ExternalReviewProtocolAdapter.from_record(adapter)
            except (TypeError, ValueError, ReviewModelError) as exc:
                raise CertificationModelError(
                    f"protocol_adapter is invalid: {exc}"
                ) from exc
            object.__setattr__(self, "protocol_adapter", adapter)
        if adapter.protocol != MANUAL_FINAL_PROTOCOL:
            raise CertificationModelError(
                "manual-final protocol_adapter does not match the protocol"
            )
        if self.execution_policy == "independent":
            if self.source_kind != "pre-final-review":
                raise CertificationModelError(
                    "independent manual-final source_kind must be pre-final-review"
                )
            if self.execution_id == self.source_execution_id:
                raise CertificationModelError(
                    "independent manual-final execution must differ from its source execution"
                )
        else:
            if self.source_kind != "review-epoch-reviewer":
                raise CertificationModelError(
                    "reuse_epoch_reviewer source_kind must be review-epoch-reviewer"
                )
            if self.execution_id != self.source_execution_id:
                raise CertificationModelError(
                    "reuse_epoch_reviewer must reuse the exact source execution"
                )

    def to_record(self) -> dict[str, Any]:
        return {
            "execution_policy": self.execution_policy,
            "execution_id": self.execution_id,
            "source_kind": self.source_kind,
            "source_execution_id": self.source_execution_id,
            "source_artifact_ref": self.source_artifact_ref,
            "source_artifact_digest": self.source_artifact_digest,
            "target_sha": self.target_sha,
            "protocol_adapter": self.protocol_adapter.to_record(),
        }

    @classmethod
    def from_record(cls, value: Any) -> "ManualFinalExecutionProvenance":
        if isinstance(value, cls):
            return value
        record = _record(value, "manual-final execution provenance")
        expected = {
            "execution_policy",
            "execution_id",
            "source_kind",
            "source_execution_id",
            "source_artifact_ref",
            "source_artifact_digest",
            "target_sha",
            "protocol_adapter",
        }
        if set(record) != expected:
            raise CertificationModelError(
                "manual-final execution provenance fields are not canonical"
            )
        try:
            adapter = ExternalReviewProtocolAdapter.from_record(
                record["protocol_adapter"]
            )
        except (TypeError, ValueError, ReviewModelError) as exc:
            raise CertificationModelError(
                f"protocol_adapter is invalid: {exc}"
            ) from exc
        return cls(
            execution_policy=record["execution_policy"],
            execution_id=record["execution_id"],
            source_kind=record["source_kind"],
            source_execution_id=record["source_execution_id"],
            source_artifact_ref=record["source_artifact_ref"],
            source_artifact_digest=record["source_artifact_digest"],
            target_sha=record["target_sha"],
            protocol_adapter=adapter,
        )


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Verification rules included in the plan identity."""

    required_passes: int = 1
    critical_passes: int = 2
    critical_severities: tuple[str, ...] = tuple(sorted(CRITICAL_SEVERITIES))
    require_independent: bool = True
    require_cross_provider_for_critical: bool = False
    max_parallel_verifiers: int = 2
    max_verifications: int = 64
    time_limit_seconds: int = 1800
    provider_limits: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        required = _positive_int(self.required_passes, "required_passes")
        critical = _positive_int(self.critical_passes, "critical_passes")
        if required > 2 or critical > 2:
            raise CertificationModelError(
                "verification pass counts must be between 1 and 2"
            )
        if critical < required:
            raise CertificationModelError(
                "critical_passes must be at least required_passes"
            )
        severities = _text_tuple(
            self.critical_severities, "critical_severities"
        )
        if any(severity not in {"P0", "P1", "P2", "P3"} for severity in severities):
            raise CertificationModelError("critical_severities contains an invalid severity")
        for field_name in (
            "require_independent",
            "require_cross_provider_for_critical",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise CertificationModelError(f"{field_name} must be boolean")
        max_parallel = _positive_int(
            self.max_parallel_verifiers, "max_parallel_verifiers"
        )
        if max_parallel > 8:
            raise CertificationModelError("max_parallel_verifiers must be at most 8")
        max_verifications = _positive_int(
            self.max_verifications, "max_verifications"
        )
        time_limit = _positive_int(self.time_limit_seconds, "time_limit_seconds")

        raw_limits = self.provider_limits
        if isinstance(raw_limits, Mapping):
            raw_limits = tuple(raw_limits.items())
        limits: list[tuple[str, int]] = []
        seen: set[str] = set()
        for provider, limit in raw_limits:
            provider = _required_text(provider, "provider_limits provider")
            if provider not in SUPPORTED_PROVIDERS:
                raise CertificationModelError(
                    f"unsupported provider limit: {provider}"
                )
            if provider in seen:
                raise CertificationModelError(
                    f"provider_limits contains duplicate provider: {provider}"
                )
            limits.append((provider, _non_negative_int(limit, "provider limit")))
            seen.add(provider)
        limits.sort(key=lambda item: SUPPORTED_PROVIDERS.index(item[0]))

        object.__setattr__(self, "required_passes", required)
        object.__setattr__(self, "critical_passes", critical)
        object.__setattr__(self, "critical_severities", severities)
        object.__setattr__(self, "max_parallel_verifiers", max_parallel)
        object.__setattr__(self, "max_verifications", max_verifications)
        object.__setattr__(self, "time_limit_seconds", time_limit)
        object.__setattr__(self, "provider_limits", tuple(limits))

    def to_record(self) -> dict[str, Any]:
        return {
            "required_passes": self.required_passes,
            "critical_passes": self.critical_passes,
            "critical_severities": list(self.critical_severities),
            "require_independent": self.require_independent,
            "require_cross_provider_for_critical": self.require_cross_provider_for_critical,
            "max_parallel_verifiers": self.max_parallel_verifiers,
            "max_verifications": self.max_verifications,
            "time_limit_seconds": self.time_limit_seconds,
            "provider_limits": dict(self.provider_limits),
        }

    @classmethod
    def from_record(cls, value: Any) -> "VerificationPolicy":
        if isinstance(value, cls):
            return value
        record = _record(value, "verification_policy")
        limits = record.get("provider_limits", {})
        if isinstance(limits, Mapping):
            limits = tuple(limits.items())
        else:
            limits = tuple(_items(limits, "verification_policy.provider_limits"))
        return cls(
            required_passes=_coerce_int(
                record.get("required_passes"), "required_passes", default=1
            ),
            critical_passes=_coerce_int(
                record.get("critical_passes"), "critical_passes", default=2
            ),
            critical_severities=tuple(
                record.get("critical_severities", tuple(sorted(CRITICAL_SEVERITIES)))
            ),
            require_independent=record.get("require_independent", True),
            require_cross_provider_for_critical=record.get(
                "require_cross_provider_for_critical", False
            ),
            max_parallel_verifiers=_coerce_int(
                record.get("max_parallel_verifiers"),
                "max_parallel_verifiers",
                default=2,
            ),
            max_verifications=_coerce_int(
                record.get("max_verifications"), "max_verifications", default=64
            ),
            time_limit_seconds=_coerce_int(
                record.get("time_limit_seconds"),
                "time_limit_seconds",
                default=1800,
            ),
            provider_limits=tuple(limits),
        )

    def required_passes_for(self, severity: str) -> int:
        return (
            self.critical_passes
            if severity in self.critical_severities
            else self.required_passes
        )


@dataclass(frozen=True, slots=True)
class CertificationPlan:
    """Immutable identity for one prepared manual final review."""

    certification_id: str
    base_sha: str
    target_sha: str
    scope_revision: int
    source_snapshot_revision: int
    source_digest: str
    protocol: str
    lane_plan: tuple[FinalReviewLane, ...]
    verification_policy: VerificationPolicy = field(default_factory=VerificationPolicy)
    base_ref: str = ""
    target_ref: str = ""
    scope_source: tuple[str, ...] = ()
    execution: ManualFinalExecutionProvenance | None = None
    execution_kind: str = ""
    protocol_key: str = ""
    process_plan: tuple[FinalReviewProcessPlan, ...] = ()
    final_coordinator_config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "certification_id",
            "base_sha",
            "target_sha",
            "protocol",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.protocol != MANUAL_FINAL_PROTOCOL:
            raise CertificationModelError(
                f"manual final protocol must be {MANUAL_FINAL_PROTOCOL}"
            )
        if bool(self.execution_kind) != bool(self.protocol_key):
            raise CertificationModelError(
                "manual final plan requires both execution_kind and protocol_key"
            )
        if self.execution_kind and (
            self.execution_kind != "manual-final"
            or self.protocol_key != "review.manual_final_protocol"
        ):
            raise CertificationModelError(
                "manual final plan protocol identity must use "
                "manual-final/review.manual_final_protocol"
            )
        object.__setattr__(
            self,
            "base_ref",
            "" if self.base_ref is None else _required_text(self.base_ref, "base_ref"),
        )
        object.__setattr__(
            self,
            "target_ref",
            ""
            if self.target_ref is None
            else _required_text(self.target_ref, "target_ref"),
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
        object.__setattr__(
            self, "source_digest", _digest(self.source_digest, "source_digest")
        )
        lanes = tuple(_coerce_lane(value, index) for index, value in enumerate(self.lane_plan))
        if not lanes:
            raise CertificationModelError("lane_plan must contain at least one lane")
        if len({lane.key for lane in lanes}) != len(lanes):
            raise CertificationModelError("lane_plan must not contain duplicate lanes")
        object.__setattr__(self, "lane_plan", lanes)
        object.__setattr__(
            self,
            "verification_policy",
            VerificationPolicy.from_record(self.verification_policy),
        )
        object.__setattr__(
            self,
            "scope_source",
            _text_tuple(self.scope_source, "scope_source"),
        )
        if self.execution is not None:
            execution = ManualFinalExecutionProvenance.from_record(self.execution)
            if execution.target_sha != self.target_sha:
                raise CertificationModelError(
                    "manual-final execution target_sha must match certification target_sha"
                )
            object.__setattr__(self, "execution", execution)
        processes = tuple(
            FinalReviewProcessPlan.from_record(item) for item in self.process_plan
        )
        if len({item.process_id for item in processes}) != len(processes):
            raise CertificationModelError(
                "manual final process_plan must not contain duplicate process_id values"
            )
        if len({item.agent_label for item in processes}) != len(processes):
            raise CertificationModelError(
                "manual final process_plan must not contain duplicate agent_label values"
            )
        object.__setattr__(self, "process_plan", processes)
        coordinator_config = _record(
            self.final_coordinator_config, "final_coordinator_config"
        )
        if coordinator_config:
            expected_fields = {"provider", "model", "effort", "sources", "provenance"}
            if set(coordinator_config) != expected_fields:
                raise CertificationModelError(
                    "final_coordinator_config fields are not canonical"
                )
            for field_name in ("provider", "model", "effort"):
                _required_text(
                    coordinator_config[field_name],
                    f"final_coordinator_config.{field_name}",
                )
            if not isinstance(coordinator_config["sources"], Mapping) or not isinstance(
                coordinator_config["provenance"], Mapping
            ):
                raise CertificationModelError(
                    "final_coordinator_config sources and provenance must be objects"
                )
            coordinator_config = json.loads(canonical_json(coordinator_config))
        object.__setattr__(self, "final_coordinator_config", coordinator_config)
        if processes:
            lane_processes = {
                (item.provider, item.agent_label)
                for item in processes
                if item.process_kind == "discovery"
            }
            expected_lanes = {
                (item.provider, item.agent_label) for item in self.lane_plan
            }
            if lane_processes != expected_lanes:
                raise CertificationModelError(
                    "manual final discovery process identities must exactly match lane_plan"
                )
        if coordinator_config:
            coordinators = tuple(
                item
                for item in processes
                if item.process_id == "manual-final-coordinator"
            )
            expected_coordinator = {
                key: coordinator_config[key]
                for key in ("provider", "model", "effort")
            }
            if len(coordinators) != 1 or {
                "provider": coordinators[0].provider,
                "model": coordinators[0].model,
                "effort": coordinators[0].effort,
            } != expected_coordinator:
                raise CertificationModelError(
                    "manual-final coordinator process does not match final_coordinator_config"
                )

    def identity_record(self) -> dict[str, Any]:
        """Return exactly the fields covered by the plan digest."""

        record = {
            "certification_id": self.certification_id,
            "base_ref": self.base_ref,
            "base_sha": self.base_sha,
            "target_ref": self.target_ref,
            "target_sha": self.target_sha,
            "scope_source": list(self.scope_source),
            "scope_revision": self.scope_revision,
            "source_snapshot_revision": self.source_snapshot_revision,
            "source_digest": self.source_digest,
            "protocol": self.protocol,
            "lane_plan": [lane.to_record() for lane in self.lane_plan],
            "verification_policy": self.verification_policy.to_record(),
        }
        if self.execution_kind:
            record["execution_kind"] = self.execution_kind
            record["protocol_key"] = self.protocol_key
        if self.execution is not None:
            record["execution"] = self.execution.to_record()
        if self.process_plan:
            record["process_plan"] = [item.to_record() for item in self.process_plan]
        if self.final_coordinator_config:
            record["final_coordinator_config"] = dict(self.final_coordinator_config)
        return record

    @property
    def digest(self) -> str:
        return canonical_digest(self.identity_record())

    @property
    def plan_digest(self) -> str:
        """Compatibility spelling used by final-review artifacts."""

        return self.digest

    @property
    def source_snapshot(self) -> int:
        """Compatibility spelling for the frozen source snapshot revision."""

        return self.source_snapshot_revision

    @property
    def source_refs(self) -> tuple[str, ...]:
        """Compatibility spelling for the locked scope source references."""

        return self.scope_source

    def to_record(self) -> dict[str, Any]:
        return {**self.identity_record(), "plan_digest": self.digest}

    @classmethod
    def from_record(cls, value: Any) -> "CertificationPlan":
        record = _record(value, "certification plan")
        _required_fields(
            record,
            "certification plan",
            (
                "certification_id",
                "base_sha",
                "target_sha",
                "scope_revision",
                "source_snapshot_revision",
                "source_digest",
                "protocol",
                "lane_plan",
                "verification_policy",
            ),
        )
        source = record.get("scope_source", record.get("source_refs", ()))
        plan = cls(
            certification_id=record["certification_id"],
            base_ref=record.get("base_ref", ""),
            base_sha=record["base_sha"],
            target_ref=record.get("target_ref", ""),
            target_sha=record["target_sha"],
            scope_source=tuple(_items(source, "scope_source")),
            scope_revision=record["scope_revision"],
            source_snapshot_revision=record["source_snapshot_revision"],
            source_digest=record["source_digest"],
            protocol=record["protocol"],
            execution_kind=record.get("execution_kind", ""),
            protocol_key=record.get("protocol_key", ""),
            process_plan=tuple(
                FinalReviewProcessPlan.from_record(item)
                for item in _items(record.get("process_plan", ()), "process_plan")
            ),
            final_coordinator_config=record.get("final_coordinator_config", {}),
            lane_plan=tuple(
                _coerce_lane(item, index)
                for index, item in enumerate(_items(record["lane_plan"], "lane_plan"))
            ),
            verification_policy=VerificationPolicy.from_record(
                record["verification_policy"]
            ),
            execution=(
                ManualFinalExecutionProvenance.from_record(record["execution"])
                if "execution" in record
                else None
            ),
        )
        supplied_digest = record.get("plan_digest")
        if supplied_digest is not None:
            supplied_digest = _required_text(supplied_digest, "plan_digest")
            if not hmac.compare_digest(supplied_digest, plan.digest):
                raise CertificationModelError(
                    "certification plan plan_digest does not match canonical identity"
                )
        return plan


def validate_plan_digest(
    plan: CertificationPlan | Mapping[str, Any], supplied_digest: str
) -> bool:
    """Compare a supplied plan digest without accepting a mutable identity."""

    candidate = (
        plan if isinstance(plan, CertificationPlan) else CertificationPlan.from_record(plan)
    )
    return hmac.compare_digest(candidate.digest, _required_text(supplied_digest, "plan_digest"))


def canonical_plan_digest(
    plan: CertificationPlan | Mapping[str, Any],
) -> str:
    """Return the canonical digest for a plan object or serialized plan."""

    return plan.digest if isinstance(plan, CertificationPlan) else CertificationPlan.from_record(plan).digest


@dataclass(frozen=True, slots=True)
class FinalReviewManifest:
    """Structured final-review evidence with a recomputable completeness gate."""

    certification_id: str
    prepared_plan_digest: str
    base_sha: str
    target_sha: str
    scope_revision: int
    source_snapshot_revision: int
    source_digest: str
    protocol: str
    review_manifest: ReviewManifest
    manifest_complete: bool
    verified_actionable_findings: int
    patch_verdict: str = "passed"
    execution: ManualFinalExecutionProvenance | None = None
    execution_kind: str = ""
    protocol_key: str = ""
    process_identities: tuple[FinalReviewProcessIdentity, ...] = ()
    final_coordinator_config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "certification_id",
            "prepared_plan_digest",
            "base_sha",
            "target_sha",
            "protocol",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.protocol != MANUAL_FINAL_PROTOCOL:
            raise CertificationModelError(
                f"manual final protocol must be {MANUAL_FINAL_PROTOCOL}"
            )
        if bool(self.execution_kind) != bool(self.protocol_key):
            raise CertificationModelError(
                "manual final manifest requires both execution_kind and protocol_key"
            )
        if self.execution_kind and (
            self.execution_kind != "manual-final"
            or self.protocol_key != "review.manual_final_protocol"
        ):
            raise CertificationModelError(
                "manual final manifest protocol identity is not canonical"
            )
        object.__setattr__(
            self,
            "prepared_plan_digest",
            _digest(self.prepared_plan_digest, "prepared_plan_digest"),
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
        object.__setattr__(
            self, "source_digest", _digest(self.source_digest, "source_digest")
        )
        if not isinstance(self.review_manifest, ReviewManifest):
            try:
                object.__setattr__(
                    self,
                    "review_manifest",
                    ReviewManifest.from_record(self.review_manifest),
                )
            except (
                TypeError,
                ValueError,
                KeyError,
                AttributeError,
                ReviewModelError,
            ) as exc:
                raise CertificationModelError(
                    f"review_manifest is invalid: {exc}"
                ) from exc
        if not isinstance(self.manifest_complete, bool):
            raise CertificationModelError("manifest_complete must be boolean")
        object.__setattr__(
            self,
            "verified_actionable_findings",
            _non_negative_int(
                self.verified_actionable_findings,
                "verified_actionable_findings",
            ),
        )
        if self.patch_verdict not in PATCH_VERDICTS:
            raise CertificationModelError(
                f"unsupported patch_verdict: {self.patch_verdict}"
            )
        if self.execution is not None:
            execution = ManualFinalExecutionProvenance.from_record(self.execution)
            if execution.target_sha != self.target_sha:
                raise CertificationModelError(
                    "manual-final execution target_sha must match manifest target_sha"
                )
            object.__setattr__(self, "execution", execution)
        process_identities = tuple(
            sorted(
                (
                    FinalReviewProcessIdentity.from_record(item)
                    for item in self.process_identities
                ),
                key=lambda item: item.process_id,
            )
        )
        if len({item.process_id for item in process_identities}) != len(
            process_identities
        ):
            raise CertificationModelError(
                "manual final process_identities must not contain duplicate process_id values"
            )
        object.__setattr__(self, "process_identities", process_identities)
        coordinator_config = _record(
            self.final_coordinator_config, "final_coordinator_config"
        )
        if coordinator_config:
            expected_fields = {"provider", "model", "effort", "sources", "provenance"}
            if set(coordinator_config) != expected_fields:
                raise CertificationModelError(
                    "final_coordinator_config fields are not canonical"
                )
            coordinator_config = json.loads(canonical_json(coordinator_config))
        object.__setattr__(self, "final_coordinator_config", coordinator_config)

    @classmethod
    def from_review_manifest(
        cls,
        plan: CertificationPlan,
        review_manifest: ReviewManifest,
        *,
        verified_actionable_findings: int = 0,
        patch_verdict: str = "passed",
    ) -> "FinalReviewManifest":
        return cls(
            certification_id=plan.certification_id,
            prepared_plan_digest=plan.digest,
            base_sha=plan.base_sha,
            target_sha=plan.target_sha,
            scope_revision=plan.scope_revision,
            source_snapshot_revision=plan.source_snapshot_revision,
            source_digest=plan.source_digest,
            protocol=plan.protocol,
            execution_kind=plan.execution_kind,
            protocol_key=plan.protocol_key,
            review_manifest=review_manifest,
            manifest_complete=review_manifest.completeness.complete,
            verified_actionable_findings=verified_actionable_findings,
            patch_verdict=patch_verdict,
            execution=plan.execution,
            process_identities=(),
            final_coordinator_config=plan.final_coordinator_config,
        )

    @property
    def completeness(self) -> ManifestCompleteness:
        return self.review_manifest.completeness

    @property
    def recomputed_verified_actionable_fingerprints(self) -> tuple[str, ...]:
        """Return findings that are fully verified and still require action."""

        return self.recomputed_verified_actionable_fingerprints_for_scope()

    def recomputed_verified_actionable_fingerprints_for_scope(
        self, *, allow_legacy: bool = False
    ) -> tuple[str, ...]:
        """Return verified actionable findings for the current scope policy."""

        return self.review_manifest.verified_actionable_fingerprints_for_scope(
            allow_legacy=allow_legacy
        )

    @property
    def recomputed_verified_actionable_count(self) -> int:
        return len(self.recomputed_verified_actionable_fingerprints)

    def recomputed_verified_actionable_count_for_scope(
        self, *, allow_legacy: bool = False
    ) -> int:
        return len(
            self.recomputed_verified_actionable_fingerprints_for_scope(
                allow_legacy=allow_legacy
            )
        )

    @property
    def recomputed_verified_release_blocking_fingerprints(self) -> tuple[str, ...]:
        return self.recomputed_verified_release_blocking_fingerprints_for_scope()

    def recomputed_verified_release_blocking_fingerprints_for_scope(
        self, *, allow_legacy: bool = False
    ) -> tuple[str, ...]:
        """Return verified findings that independently block the release."""

        return self.review_manifest.verified_release_blocking_fingerprints_for_scope(
            allow_legacy=allow_legacy
        )

    @property
    def recomputed_verified_release_blocking_count(self) -> int:
        return len(self.recomputed_verified_release_blocking_fingerprints)

    def recomputed_verified_release_blocking_count_for_scope(
        self, *, allow_legacy: bool = False
    ) -> int:
        return len(
            self.recomputed_verified_release_blocking_fingerprints_for_scope(
                allow_legacy=allow_legacy
            )
        )

    def to_record(self) -> dict[str, Any]:
        record = dict(self.review_manifest.to_record())
        record.update(
            {
                "certification_id": self.certification_id,
                "prepared_plan_digest": self.prepared_plan_digest,
                "base_sha": self.base_sha,
                "target_sha": self.target_sha,
                "scope_revision": self.scope_revision,
                "source_snapshot_revision": self.source_snapshot_revision,
                "source_digest": self.source_digest,
                "protocol": self.protocol,
                "manifest_complete": self.manifest_complete,
                "verified_actionable_findings": self.verified_actionable_findings,
                "patch_verdict": self.patch_verdict,
            }
        )
        if self.execution is not None:
            record["execution"] = self.execution.to_record()
        if self.execution_kind:
            record["execution_kind"] = self.execution_kind
            record["protocol_key"] = self.protocol_key
        if self.process_identities or self.final_coordinator_config:
            record["process_identities"] = [
                item.to_record() for item in self.process_identities
            ]
        if self.final_coordinator_config:
            record["final_coordinator_config"] = dict(self.final_coordinator_config)
        return record

    @classmethod
    def from_record(cls, value: Any) -> "FinalReviewManifest":
        record = _record(value, "final review manifest")
        _required_fields(
            record,
            "final review manifest",
            (
                "certification_id",
                "prepared_plan_digest",
                "base_sha",
                "target_sha",
                "scope_revision",
                "source_snapshot_revision",
                "source_digest",
                "protocol",
                "manifest_complete",
                "verified_actionable_findings",
                "patch_verdict",
            ),
        )
        nested = record.get("review_manifest")
        review_record = nested if nested is not None else record
        return cls(
            certification_id=record["certification_id"],
            prepared_plan_digest=record["prepared_plan_digest"],
            base_sha=record["base_sha"],
            target_sha=record["target_sha"],
            scope_revision=record["scope_revision"],
            source_snapshot_revision=record["source_snapshot_revision"],
            source_digest=record["source_digest"],
            protocol=record["protocol"],
            execution_kind=record.get("execution_kind", ""),
            protocol_key=record.get("protocol_key", ""),
            process_identities=tuple(
                FinalReviewProcessIdentity.from_record(item)
                for item in _items(
                    record.get("process_identities", ()), "process_identities"
                )
            ),
            final_coordinator_config=record.get("final_coordinator_config", {}),
            review_manifest=ReviewManifest.from_record(review_record),
            manifest_complete=record["manifest_complete"],
            verified_actionable_findings=record["verified_actionable_findings"],
            patch_verdict=record["patch_verdict"],
            execution=(
                ManualFinalExecutionProvenance.from_record(record["execution"])
                if "execution" in record
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CertificationValidation:
    """Recomputed result of recording one final-review manifest."""

    status: str
    issues: tuple[str, ...] = ()
    completeness: ManifestCompleteness | None = None

    def __post_init__(self) -> None:
        if self.status not in CERTIFICATION_STATUSES:
            raise CertificationModelError(f"unsupported certification status: {self.status}")
        object.__setattr__(self, "issues", tuple(sorted(set(self.issues))))

    @property
    def passed(self) -> bool:
        return self.status == "passed" and not self.issues

    @property
    def ok(self) -> bool:
        return self.passed

    @property
    def complete(self) -> bool:
        return self.completeness is not None and self.completeness.complete

    def raise_for_error(self) -> None:
        if not self.passed:
            raise CertificationModelError(
                "; ".join(self.issues) or f"manual final review is {self.status}"
            )


def _plan_from(value: CertificationPlan | Mapping[str, Any]) -> CertificationPlan:
    return value if isinstance(value, CertificationPlan) else CertificationPlan.from_record(value)


def _manifest_from(
    value: FinalReviewManifest | Mapping[str, Any],
) -> FinalReviewManifest:
    return value if isinstance(value, FinalReviewManifest) else FinalReviewManifest.from_record(value)


def _manifest_identity_issues(
    plan: CertificationPlan,
    manifest: FinalReviewManifest,
    *,
    current_target_sha: str | None = None,
    allow_legacy: bool = False,
) -> list[str]:
    issues: list[str] = []
    expected = {
        "certification-id": (plan.certification_id, manifest.certification_id),
        "prepared-plan-digest": (plan.digest, manifest.prepared_plan_digest),
        "base-sha": (plan.base_sha, manifest.base_sha),
        "target-sha": (plan.target_sha, manifest.target_sha),
        "scope-revision": (plan.scope_revision, manifest.scope_revision),
        "source-snapshot-revision": (
            plan.source_snapshot_revision,
            manifest.source_snapshot_revision,
        ),
        "source-digest": (plan.source_digest, manifest.source_digest),
        "protocol": (plan.protocol, manifest.protocol),
        "execution-kind": (plan.execution_kind, manifest.execution_kind),
        "protocol-key": (plan.protocol_key, manifest.protocol_key),
        "final-coordinator-config": (
            dict(plan.final_coordinator_config),
            dict(manifest.final_coordinator_config),
        ),
    }
    for label, (expected_value, actual_value) in expected.items():
        if expected_value != actual_value:
            issues.append(f"identity-mismatch:{label}")
    if current_target_sha is not None and manifest.target_sha != current_target_sha:
        issues.append("target-sha-drift")

    if plan.process_plan:
        planned = {item.process_id: item for item in plan.process_plan}
        evidence = {item.process_id: item for item in manifest.process_identities}
        missing = sorted(set(planned) - set(evidence))
        unknown = sorted(set(evidence) - set(planned))
        issues.extend(f"process-identity-missing:{item}" for item in missing)
        issues.extend(f"process-identity-unplanned:{item}" for item in unknown)
        for process_id in sorted(set(planned).intersection(evidence)):
            process = planned[process_id]
            identity = evidence[process_id]
            if identity.agent_identity.get("requested") != process.agent_identity.get(
                "requested"
            ):
                issues.append(f"process-identity-requested-mismatch:{process_id}")
            if process.attestation_required and not identity.verified:
                issues.append(f"process-identity-attestation-invalid:{process_id}")
    elif manifest.process_identities:
        issues.append("process-identity-unplanned")

    if plan.execution is None and manifest.execution is None:
        if not allow_legacy:
            issues.append("execution-provenance-missing")
    elif plan.execution is None or manifest.execution is None:
        issues.append("identity-mismatch:execution-provenance")
    elif plan.execution != manifest.execution:
        issues.append("identity-mismatch:execution-provenance")
    elif manifest.review_manifest.review_id != plan.execution.execution_id:
        issues.append("identity-mismatch:execution-id")

    review_plan = manifest.review_manifest.plan
    if review_plan.head_sha != plan.target_sha:
        issues.append("identity-mismatch:review-head-sha")
    if plan.process_plan:
        planned_processes = {item.process_id: item for item in plan.process_plan}
        expected_topology: dict[str, tuple[str, str, str]] = {}
        for provider_plan in review_plan.provider_plans:
            if provider_plan.role == "coordinator":
                expected_topology[
                    f"provider-{provider_plan.provider}-coordinator"
                ] = (
                    provider_plan.provider,
                    provider_plan.model,
                    provider_plan.coordinator_label,
                )
            for lane in provider_plan.lanes:
                expected_topology[f"lane-{lane.provider}-{lane.lane_id}"] = (
                    provider_plan.provider,
                    provider_plan.model,
                    lane.agent_label,
                )
            for index, agent_label in enumerate(
                provider_plan.verifier_agents, start=1
            ):
                expected_topology[
                    f"verifier-{provider_plan.provider}-{index}"
                ] = (
                    provider_plan.provider,
                    provider_plan.model,
                    agent_label,
                )
        actual_topology_ids = set(planned_processes) - {"manual-final-coordinator"}
        if actual_topology_ids != set(expected_topology):
            issues.append("identity-mismatch:process-topology-membership")
        for process_id in sorted(set(expected_topology).intersection(planned_processes)):
            process = planned_processes[process_id]
            expected_provider, expected_model, expected_label = expected_topology[
                process_id
            ]
            if (
                process.provider,
                process.model,
                process.agent_label,
            ) != (expected_provider, expected_model, expected_label):
                issues.append(
                    f"identity-mismatch:process-topology-identity:{process_id}"
                )
    expected_lanes = tuple(lane.to_record() for lane in plan.lane_plan)
    actual_lanes = tuple(lane.to_record() for lane in review_plan.expected_lanes)
    if expected_lanes != actual_lanes:
        issues.append("identity-mismatch:lane-plan")

    policy = plan.verification_policy
    budget = review_plan.budget
    if budget.max_parallel_verifiers != policy.max_parallel_verifiers:
        issues.append("identity-mismatch:verification-policy.max_parallel_verifiers")
    if budget.max_verifications != policy.max_verifications:
        issues.append("identity-mismatch:verification-policy.max_verifications")
    if budget.time_limit_seconds != policy.time_limit_seconds:
        issues.append("identity-mismatch:verification-policy.time_limit_seconds")
    if tuple(budget.provider_limits) != tuple(policy.provider_limits):
        issues.append("identity-mismatch:verification-policy.provider_limits")
    return issues


def validate_final_review(
    plan: CertificationPlan | Mapping[str, Any],
    manifest: FinalReviewManifest | Mapping[str, Any],
    *,
    current_target_sha: str | None = None,
    allow_legacy: bool = False,
    accepted_risk_authorizations: Mapping[str, Any] | None = None,
) -> CertificationValidation:
    """Recompute final-review completeness and return a fail-closed result."""

    try:
        prepared = _plan_from(plan)
        evidence = _manifest_from(manifest)
    except (
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
        ReviewModelError,
        CertificationModelError,
    ) as exc:
        return CertificationValidation(status="failed", issues=(f"invalid-artifact:{exc}",))

    completeness = evidence.completeness
    issues = _manifest_identity_issues(
        prepared,
        evidence,
        current_target_sha=current_target_sha,
        allow_legacy=allow_legacy,
    )
    issues.extend(f"manifest:{issue}" for issue in completeness.issues)
    policy_issues = validate_manifest_policy(
        evidence.review_manifest,
        allow_legacy=allow_legacy,
        accepted_risk_authorizations=accepted_risk_authorizations,
    )
    issues.extend(f"policy:{issue}" for issue in policy_issues)
    if policy_issues:
        recomputed_actionable_count = 0
    else:
        recomputed_actionable_count, _ = review_manifest_policy_counts(
            evidence.review_manifest,
            allow_legacy=allow_legacy,
            accepted_risk_authorizations=accepted_risk_authorizations,
        )
    if evidence.manifest_complete != completeness.complete:
        issues.append("manifest-complete-claim-mismatch")
    if (
        evidence.verified_actionable_findings
        != recomputed_actionable_count
    ):
        issues.append("verified-actionable-finding-count-mismatch")
    if evidence.verified_actionable_findings:
        issues.append(
            f"verified-actionable-findings:{evidence.verified_actionable_findings}"
        )
    if evidence.patch_verdict != "passed":
        issues.append(f"patch-verdict:{evidence.patch_verdict}")

    has_incomplete_evidence = bool(completeness.issues)
    status = "passed"
    if issues:
        status = "incomplete" if has_incomplete_evidence and not any(
            issue.startswith(
                (
                    "identity-mismatch:",
                    "target-sha-drift",
                    "verified-actionable",
                    "policy:",
                )
            )
            for issue in issues
        ) else "failed"
    return CertificationValidation(
        status=status,
        issues=tuple(issues),
        completeness=completeness,
    )


check_final_review = validate_final_review
validate_certification = validate_final_review


def certify_final_review(
    plan: CertificationPlan | Mapping[str, Any],
    manifest: FinalReviewManifest | Mapping[str, Any],
    *,
    current_target_sha: str | None = None,
    allow_legacy: bool = False,
    accepted_risk_authorizations: Mapping[str, Any] | None = None,
) -> CertificationValidation:
    """Validate evidence and raise if the manual final gate cannot pass."""

    result = validate_final_review(
        plan,
        manifest,
        current_target_sha=current_target_sha,
        allow_legacy=allow_legacy,
        accepted_risk_authorizations=accepted_risk_authorizations,
    )
    result.raise_for_error()
    return result


@dataclass(frozen=True, slots=True)
class ReopenValidation:
    """Pure validation output for an attempted review reopen."""

    accepted: bool
    issues: tuple[str, ...] = ()
    source_phase: str = ""
    action: str = ""

    @property
    def ok(self) -> bool:
        return self.accepted and not self.issues


@dataclass(frozen=True, slots=True)
class ReopenResult:
    """Copy-on-write result of an atomic reopen request."""

    state: Mapping[str, Any]
    validation: ReopenValidation

    @property
    def accepted(self) -> bool:
        return self.validation.accepted

    @property
    def ok(self) -> bool:
        return self.validation.ok

    @property
    def issues(self) -> tuple[str, ...]:
        return self.validation.issues


def _source_phase(state: Mapping[str, Any]) -> str:
    phase = str(state.get("phase") or "").strip()
    if phase in REOPENABLE_PHASES:
        return phase
    convergence = state.get("review_convergence")
    if isinstance(convergence, Mapping) and convergence.get("status") == "exhausted":
        return "review_convergence_exhausted"
    manual = state.get("manual_final_review")
    if isinstance(manual, Mapping):
        status = manual.get("status")
        if status == "failed":
            return "manual_final_review_failed"
        if status == "incomplete":
            return "manual_final_review_incomplete"
    return phase


def _valid_user_input_id(value: Any, field_name: str) -> bool:
    return isinstance(value, str) and bool(_INPUT_ID_RE.fullmatch(value.strip()))


def _confirmed_actionable_count(state: Mapping[str, Any]) -> int:
    manual = state.get("manual_final_review")
    value = manual.get("verified_actionable_findings") if isinstance(manual, Mapping) else None
    if value is None:
        convergence = state.get("review_convergence")
        value = (
            convergence.get("verified_actionable_findings")
            if isinstance(convergence, Mapping)
            else None
        )
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _scope_amendment_issues(
    state: Mapping[str, Any], amendment: Any, user_input_id: str
) -> list[str]:
    if not isinstance(amendment, Mapping):
        return ["scope-amendment-required"]
    issues: list[str] = []
    kind = amendment.get("kind")
    if kind not in SCOPE_AMENDMENT_KINDS:
        issues.append("scope-amendment-kind-invalid")
    amendment_input = (
        amendment.get("reopen_user_input_id")
        or amendment.get("input_id")
        or amendment.get("user_input_id")
    )
    if amendment_input != user_input_id:
        issues.append("scope-amendment-input-mismatch")
    release_scope = state.get("release_scope")
    if not isinstance(release_scope, Mapping):
        return [*issues, "release-scope-missing"]
    current_scope_revision = release_scope.get("scope_revision")
    current_snapshot_revision = release_scope.get("source_snapshot_revision")
    if isinstance(current_scope_revision, bool) or not isinstance(current_scope_revision, int):
        issues.append("release-scope-revision-invalid")
    else:
        expected_scope_revision = current_scope_revision + (1 if kind == "scope-change" else 0)
        proposed_scope_revision = amendment.get(
            "new_scope_revision", amendment.get("scope_revision")
        )
        if proposed_scope_revision != expected_scope_revision:
            issues.append("scope-amendment-revision-invalid")
    if isinstance(current_snapshot_revision, bool) or not isinstance(current_snapshot_revision, int):
        issues.append("source-snapshot-revision-invalid")
    else:
        proposed_snapshot_revision = amendment.get(
            "new_source_snapshot_revision", amendment.get("source_snapshot_revision")
        )
        if (
            isinstance(proposed_snapshot_revision, bool)
            or not isinstance(proposed_snapshot_revision, int)
            or proposed_snapshot_revision != current_snapshot_revision + 1
        ):
            issues.append("scope-amendment-source-snapshot-invalid")
    # Keep the legacy diagnostic names for callers that supplied the former
    # transient mapping, while the canonical path below requires a complete
    # immutable ScopeAmendment record.
    if "amendment_id" not in amendment:
        issues.append("scope-amendment-artifact-id-invalid")
    if "new_scope_digest" not in amendment and "source_digest" not in amendment:
        issues.append("scope-amendment-source-digest-invalid")
    if "new_source_digests" not in amendment and "source_digests" not in amendment:
        issues.append("scope-amendment-source-digests-invalid")
    try:
        scope = hloop_release_scope.ReleaseScope.from_record(release_scope)
    except hloop_release_scope.ReleaseScopeError as exc:
        issues.append(f"release-scope-invalid:{exc}")
        return list(dict.fromkeys(issues))
    try:
        immutable = hloop_release_scope.ScopeAmendment.from_record(amendment)
        hloop_release_scope.validate_amendment(scope, immutable)
        if immutable.kind == "scope-change" and immutable.user_input_id != user_input_id:
            issues.append("scope-amendment-input-mismatch")
    except hloop_release_scope.ReleaseScopeError as exc:
        issues.append(f"scope-amendment-invalid:{exc}")
    return list(dict.fromkeys(issues))


def _reopen_issues(
    state: Mapping[str, Any],
    *,
    action: str,
    user_input_id: str,
    authorized_extra_rounds: int,
    authorization_input_id: str | None,
    scope_amendment: Mapping[str, Any] | None,
) -> tuple[str, tuple[str, ...]]:
    if not isinstance(state, Mapping):
        return "", ("state-must-be-object",)
    source_phase = _source_phase(state)
    issues: list[str] = []
    if source_phase not in REOPENABLE_PHASES:
        issues.append(f"source-phase-not-reopenable:{source_phase or 'missing'}")
    if action not in REOPEN_ACTIONS:
        issues.append(f"unsupported-reopen-action:{action}")
    if not _valid_user_input_id(user_input_id, "user_input_id"):
        issues.append("user-input-id-invalid")
    elif not hloop_release_scope.is_captured_input_id(
        user_input_id, state.get("inputs_index")
    ):
        issues.append("user-input-id-not-captured")
    if (
        isinstance(authorized_extra_rounds, bool)
        or not isinstance(authorized_extra_rounds, int)
        or authorized_extra_rounds < 0
    ):
        issues.append("authorized-extra-rounds-invalid")
    if authorization_input_id is not None:
        if not _valid_user_input_id(authorization_input_id, "authorization_input_id"):
            issues.append("authorization-input-id-invalid")
        elif not hloop_release_scope.is_captured_input_id(
            authorization_input_id, state.get("inputs_index")
        ):
            issues.append("authorization-input-id-not-captured")

    freeze = state.get("dispatch_freeze")
    if not isinstance(freeze, Mapping) or freeze.get("status") != "active":
        issues.append("dispatch-freeze-not-active")
    review_policy = state.get("review_policy")
    convergence = state.get("review_convergence")
    if not isinstance(review_policy, Mapping) or not isinstance(convergence, Mapping):
        issues.append("review-convergence-state-missing")
        max_fix_rounds = 0
        fix_round = 0
    else:
        max_fix_rounds = review_policy.get("max_fix_rounds")
        fix_round = convergence.get("fix_round")
        if isinstance(max_fix_rounds, bool) or not isinstance(max_fix_rounds, int) or max_fix_rounds < 1:
            issues.append("max-fix-rounds-invalid")
            max_fix_rounds = 0
        if isinstance(fix_round, bool) or not isinstance(fix_round, int) or fix_round < 0:
            issues.append("fix-round-invalid")
            fix_round = 0
        existing_extra_rounds = convergence.get("authorized_extra_rounds", 0)
        if (
            isinstance(existing_extra_rounds, bool)
            or not isinstance(existing_extra_rounds, int)
            or existing_extra_rounds < 0
        ):
            issues.append("existing-authorized-extra-rounds-invalid")
            existing_extra_rounds = 0
    exhausted = bool(max_fix_rounds and fix_round >= max_fix_rounds)
    available_extra_rounds = existing_extra_rounds if isinstance(existing_extra_rounds, int) else 0
    has_finding = _confirmed_actionable_count(state) > 0

    manual = state.get("manual_final_review")
    if source_phase in {"manual_final_review_failed", "manual_final_review_incomplete"}:
        expected_status = source_phase.removeprefix("manual_final_review_")
        if isinstance(manual, Mapping) and manual.get("status") not in {
            expected_status,
            "invalidated",
        }:
            issues.append("manual-review-status-phase-mismatch")

    if action == "retry-certification" and source_phase != "manual_final_review_incomplete":
        issues.append("retry-certification-requires-incomplete-review")
    if action in {"remediate", "disable-feature", "mark-experimental"} and not has_finding:
        issues.append(f"{action}-requires-confirmed-in-scope-finding")
    if (
        action in {"remediate", "disable-feature", "mark-experimental"}
        and source_phase == "manual_final_review_incomplete"
    ):
        issues.append(f"{action}-requires-failed-or-exhausted-review")
    if action in {"remediate", "disable-feature", "mark-experimental"} and exhausted:
        if available_extra_rounds + authorized_extra_rounds < 1:
            issues.append("authorized-extra-rounds-required")
        if authorized_extra_rounds and not authorization_input_id:
            issues.append("authorization-input-id-required")
    elif action not in {"remediate", "disable-feature", "mark-experimental"}:
        if authorized_extra_rounds:
            issues.append("extra-rounds-not-compatible-with-action")
        if authorization_input_id:
            issues.append("authorization-input-not-compatible-with-action")
    elif authorized_extra_rounds and not authorization_input_id:
        issues.append("authorization-input-id-required")

    if action in {"disable-feature", "mark-experimental", "scope-amend"}:
        issues.extend(_scope_amendment_issues(state, scope_amendment, user_input_id))
    elif scope_amendment is not None:
        issues.append("scope-amendment-not-compatible-with-action")

    if action == "abort" and source_phase == "review_convergence_exhausted":
        # An exhausted convergence is already a user-visible stop; abort is
        # still legal, but it must not quietly become dispatchable.
        pass
    return source_phase, tuple(sorted(set(issues)))


def validate_reopen_transition(
    state: Mapping[str, Any],
    *,
    action: str,
    user_input_id: str,
    authorized_extra_rounds: int = 0,
    authorization_input_id: str | None = None,
    scope_amendment: Mapping[str, Any] | None = None,
) -> ReopenValidation:
    """Validate a reopen without mutating ``state``."""

    source_phase, issues = _reopen_issues(
        state,
        action=action,
        user_input_id=user_input_id,
        authorized_extra_rounds=authorized_extra_rounds,
        authorization_input_id=authorization_input_id,
        scope_amendment=scope_amendment,
    )
    return ReopenValidation(
        accepted=not issues,
        issues=issues,
        source_phase=source_phase,
        action=action,
    )


def _clear_certification(state: dict[str, Any], *, prior_phase: str, action: str) -> None:
    convergence = state.get("review_convergence")
    if isinstance(convergence, dict):
        # A reopen invalidates the fixed-target evidence as one atomic unit.
        # In particular, leaving the recorded digest or prepared plan behind
        # would let ``review convergence record`` treat an old manifest as an
        # idempotent retry after a scope amendment.
        convergence["status"] = "pending"
        convergence["verified_actionable_findings"] = None
        convergence["release_blocking_findings"] = None
        convergence["artifact_refs"] = []
        for field_name in (
            "review_plan",
            "base_ref",
            "base_sha",
            "prepared_at",
            "recorded_at",
            "recorded_round",
            "recorded_manifest_digest",
            "recorded_status",
            "last_status",
            "manifest_completeness",
            "accepted_risk_authorizations",
        ):
            convergence.pop(field_name, None)

    manual = state.setdefault("manual_final_review", {})
    if not isinstance(manual, dict):
        manual = {}
        state["manual_final_review"] = manual
    history = list(manual.get("attempt_history") or [])
    history.append(
        {
            "prior_phase": prior_phase,
            "prior_status": manual.get("status", ""),
            "action": action,
        }
    )
    manual["attempt_history"] = history
    manual["status"] = "invalidated"
    manual["prepared_plan"] = ""
    manual["prepared_plan_digest"] = ""
    manual["manifest"] = ""
    manual["report"] = ""
    manual["manifest_complete"] = None
    manual["shortfall_count"] = None
    manual["verified_actionable_findings"] = None
    manual["release_blocking_findings"] = None
    manual.pop("accepted_risk_authorizations", None)
    state.pop("accepted_risk_authorizations", None)


def _apply_scope_amendment(
    state: dict[str, Any], amendment: Mapping[str, Any], user_input_id: str
) -> None:
    release_scope = state.get("release_scope")
    if not isinstance(release_scope, dict):
        raise hloop_release_scope.ScopeValidationError("release_scope must be an object")
    current = hloop_release_scope.ReleaseScope.from_record(release_scope)
    immutable = hloop_release_scope.ScopeAmendment.from_record(amendment)
    updated = current.apply_amendment(immutable).to_record()
    release_scope.clear()
    release_scope.update(updated)


def reopen_review(
    state: Mapping[str, Any],
    *,
    action: str,
    user_input_id: str,
    authorized_extra_rounds: int = 0,
    authorization_input_id: str | None = None,
    scope_amendment: Mapping[str, Any] | None = None,
) -> ReopenResult:
    """Apply a valid reopen to a deep copy, or return the frozen original copy.

    No caller-owned mapping is ever changed.  In particular, every rejected
    request returns a state equal to the input, which gives the CLI a simple
    atomic boundary: persist only when ``result.accepted`` is true.
    """

    validation = validate_reopen_transition(
        state,
        action=action,
        user_input_id=user_input_id,
        authorized_extra_rounds=authorized_extra_rounds,
        authorization_input_id=authorization_input_id,
        scope_amendment=scope_amendment,
    )
    original = deepcopy(dict(state)) if isinstance(state, Mapping) else {}
    if not validation.accepted:
        return ReopenResult(state=original, validation=validation)

    candidate = deepcopy(dict(state))
    try:
        source_phase = validation.source_phase
        _clear_certification(candidate, prior_phase=source_phase, action=action)
        convergence = candidate.setdefault("review_convergence", {})
        if not isinstance(convergence, dict):
            convergence = {}
            candidate["review_convergence"] = convergence
        freeze = candidate.setdefault("dispatch_freeze", {})
        if not isinstance(freeze, dict):
            freeze = {}
            candidate["dispatch_freeze"] = freeze

        convergence["status"] = "pending"
        convergence["verified_actionable_findings"] = None
        convergence["artifact_refs"] = []
        if action in {"remediate", "disable-feature", "mark-experimental"}:
            previous_fix_round = int(convergence.get("fix_round") or 0)
            convergence["fix_round"] = previous_fix_round + 1
            # A remediation reopen starts a new canonical round.  Clear the
            # previous round's audit echo and manifest identity so prepare
            # must create a fresh artifact for the advanced counter.
            convergence.pop("recorded_round", None)
            convergence.pop("recorded_manifest_digest", None)
            convergence.pop("recorded_status", None)
            existing_extra_rounds = int(
                convergence.get("authorized_extra_rounds") or 0
            )
            if previous_fix_round >= int(
                candidate["review_policy"]["max_fix_rounds"]
            ):
                total_extra_rounds = existing_extra_rounds + authorized_extra_rounds
                convergence["authorized_extra_rounds"] = max(
                    0, total_extra_rounds - 1
                )
                if authorized_extra_rounds:
                    refs = list(convergence.get("extra_round_authorization_refs") or [])
                    refs.append(authorization_input_id)
                    convergence["extra_round_authorization_refs"] = refs
            if action in {"disable-feature", "mark-experimental"}:
                _apply_scope_amendment(candidate, scope_amendment or {}, user_input_id)
            candidate["phase"] = "review_convergence"
            freeze.update(
                {
                    "status": "inactive",
                    "reason": "reopen-approved",
                    "source_input_id": user_input_id,
                    "allowed_running_role_ids": [],
                }
            )
        elif action == "scope-amend":
            _apply_scope_amendment(candidate, scope_amendment or {}, user_input_id)
            candidate["phase"] = "review_readiness"
            freeze.update(
                {
                    "status": "inactive",
                    "reason": "scope-amend-approved",
                    "source_input_id": user_input_id,
                    "allowed_running_role_ids": [],
                }
            )
        elif action == "retry-certification":
            candidate["phase"] = "awaiting_manual_final_review"
            manual = candidate.setdefault("manual_final_review", {})
            if isinstance(manual, dict):
                manual["status"] = "pending"
            # The freeze intentionally remains active while only the manual
            # review is retried at the same target SHA.
        else:  # abort
            candidate["phase"] = "paused"
            freeze.update(
                {
                    "status": "active",
                    "reason": "reopen-aborted",
                    "source_input_id": user_input_id,
                    "allowed_running_role_ids": [],
                }
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        failed = ReopenValidation(
            accepted=False,
            issues=(*validation.issues, f"reopen-apply-failed:{exc}"),
            source_phase=validation.source_phase,
            action=action,
        )
        return ReopenResult(state=original, validation=failed)

    return ReopenResult(
        state=candidate,
        validation=validation,
    )


apply_reopen = reopen_review
validate_reopen = validate_reopen_transition


__all__ = [
    "MANUAL_FINAL_PROTOCOL",
    "CERTIFICATION_STATUSES",
    "PATCH_VERDICTS",
    "REOPENABLE_PHASES",
    "REOPEN_ACTIONS",
    "CertificationError",
    "CertificationModelError",
    "FinalReviewLane",
    "VerificationPolicy",
    "CertificationPlan",
    "FinalReviewPlan",
    "ManualFinalReviewPlan",
    "FinalReviewManifest",
    "ManualFinalReviewManifest",
    "CertificationValidation",
    "canonical_json",
    "canonical_digest",
    "canonical_plan_digest",
    "validate_plan_digest",
    "validate_final_review",
    "check_certification_completeness",
    "check_final_review",
    "validate_certification",
    "certify_final_review",
    "ReopenValidation",
    "ReopenResult",
    "validate_reopen_transition",
    "validate_reopen",
    "reopen_review",
    "apply_reopen",
]


# Names used by the design document and by the eventual CLI integration.  The
# aliases keep this pure module small while allowing callers to describe the
# same immutable artifact in domain language.
FinalReviewPlan = CertificationPlan
ManualFinalReviewPlan = CertificationPlan
ManualFinalReviewManifest = FinalReviewManifest


def check_certification_completeness(
    manifest: FinalReviewManifest | Mapping[str, Any],
) -> ManifestCompleteness:
    """Recompute the embedded lane/verification completeness only."""

    return _manifest_from(manifest).completeness
