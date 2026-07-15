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
import re
from typing import Any, Mapping, Sequence

from .review import (
    CRITICAL_SEVERITIES,
    ManifestCompleteness,
    ReviewManifest,
    ReviewModelError,
    SUPPORTED_PROVIDERS,
)


MANUAL_FINAL_PROTOCOL = "codex-review-multi-v2"
CERTIFICATION_STATUSES = frozenset({"passed", "incomplete", "failed"})
PATCH_VERDICTS = frozenset({"passed", "failed", "incomplete"})
REOPENABLE_PHASES = frozenset(
    {
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

    def identity_record(self) -> dict[str, Any]:
        """Return exactly the fields covered by the plan digest."""

        return {
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
            lane_plan=tuple(
                _coerce_lane(item, index)
                for index, item in enumerate(_items(record["lane_plan"], "lane_plan"))
            ),
            verification_policy=VerificationPolicy.from_record(
                record["verification_policy"]
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
            review_manifest=review_manifest,
            manifest_complete=review_manifest.completeness.complete,
            verified_actionable_findings=verified_actionable_findings,
            patch_verdict=patch_verdict,
        )

    @property
    def completeness(self) -> ManifestCompleteness:
        return self.review_manifest.completeness

    @property
    def recomputed_verified_actionable_fingerprints(self) -> tuple[str, ...]:
        """Return findings that are fully verified and still require action."""

        incomplete = set(self.completeness.incomplete_findings)
        by_fingerprint: dict[str, list[Any]] = {}
        for record in self.review_manifest.verifications:
            by_fingerprint.setdefault(record.fingerprint, []).append(record)

        actionable: list[str] = []
        for finding in self.review_manifest.findings:
            if finding.fingerprint in incomplete:
                continue
            records = by_fingerprint.get(finding.fingerprint, [])
            if not records or any(record.fact_status != "confirmed" for record in records):
                continue
            if any(
                record.recommended_action in {"fix_task", "ask_user"}
                and record.ignore_status == "must_not_ignore"
                for record in records
            ):
                actionable.append(finding.fingerprint)
        return tuple(sorted(set(actionable)))

    @property
    def recomputed_verified_actionable_count(self) -> int:
        return len(self.recomputed_verified_actionable_fingerprints)

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
            review_manifest=ReviewManifest.from_record(review_record),
            manifest_complete=record["manifest_complete"],
            verified_actionable_findings=record["verified_actionable_findings"],
            patch_verdict=record["patch_verdict"],
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
    }
    for label, (expected_value, actual_value) in expected.items():
        if expected_value != actual_value:
            issues.append(f"identity-mismatch:{label}")
    if current_target_sha is not None and manifest.target_sha != current_target_sha:
        issues.append("target-sha-drift")

    review_plan = manifest.review_manifest.plan
    if review_plan.head_sha != plan.target_sha:
        issues.append("identity-mismatch:review-head-sha")
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
        prepared, evidence, current_target_sha=current_target_sha
    )
    issues.extend(f"manifest:{issue}" for issue in completeness.issues)
    if evidence.manifest_complete != completeness.complete:
        issues.append("manifest-complete-claim-mismatch")
    if (
        evidence.verified_actionable_findings
        != evidence.recomputed_verified_actionable_count
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
            issue.startswith(("identity-mismatch:", "target-sha-drift", "verified-actionable"))
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
) -> CertificationValidation:
    """Validate evidence and raise if the manual final gate cannot pass."""

    result = validate_final_review(
        plan, manifest, current_target_sha=current_target_sha
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
    if amendment.get("kind") not in SCOPE_AMENDMENT_KINDS:
        issues.append("scope-amendment-kind-invalid")
    if amendment.get("input_id") != user_input_id:
        issues.append("scope-amendment-input-mismatch")
    release_scope = state.get("release_scope")
    if not isinstance(release_scope, Mapping):
        return [*issues, "release-scope-missing"]
    current_scope_revision = release_scope.get("scope_revision")
    current_snapshot_revision = release_scope.get("source_snapshot_revision")
    if isinstance(current_scope_revision, bool) or not isinstance(current_scope_revision, int):
        issues.append("release-scope-revision-invalid")
    elif amendment.get("scope_revision") != current_scope_revision + 1:
        issues.append("scope-amendment-revision-invalid")
    if isinstance(current_snapshot_revision, bool) or not isinstance(current_snapshot_revision, int):
        issues.append("source-snapshot-revision-invalid")
    elif (
        isinstance(amendment.get("source_snapshot_revision"), bool)
        or not isinstance(amendment.get("source_snapshot_revision"), int)
        or amendment["source_snapshot_revision"] <= current_snapshot_revision
    ):
        issues.append("scope-amendment-source-snapshot-invalid")
    try:
        _digest(amendment.get("source_digest"), "scope-amendment.source_digest")
    except CertificationModelError:
        issues.append("scope-amendment-source-digest-invalid")
    try:
        _text_tuple(amendment.get("source_refs", ()), "scope-amendment.source_refs")
    except CertificationModelError:
        issues.append("scope-amendment-source-refs-invalid")
    source_digests = amendment.get("source_digests", {})
    if not isinstance(source_digests, Mapping):
        issues.append("scope-amendment-source-digests-invalid")
    return issues


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
    if (
        isinstance(authorized_extra_rounds, bool)
        or not isinstance(authorized_extra_rounds, int)
        or authorized_extra_rounds < 0
    ):
        issues.append("authorized-extra-rounds-invalid")
    if authorization_input_id is not None and not _valid_user_input_id(
        authorization_input_id, "authorization_input_id"
    ):
        issues.append("authorization-input-id-invalid")

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


def _apply_scope_amendment(
    state: dict[str, Any], amendment: Mapping[str, Any], user_input_id: str
) -> None:
    release_scope = state.setdefault("release_scope", {})
    if not isinstance(release_scope, dict):
        release_scope = {}
        state["release_scope"] = release_scope
    source_digests = dict(release_scope.get("source_digests") or {})
    source_digests.update(dict(amendment.get("source_digests") or {}))
    source_digest = str(amendment.get("source_digest") or "")
    source_digests["scope"] = source_digest
    refs = list(amendment.get("source_refs") or ())
    amendment_refs = list(release_scope.get("amendment_refs") or ())
    amendment_refs.append(user_input_id)
    release_scope.update(
        {
            "status": "locked",
            "source_refs": refs,
            "source_digests": source_digests,
            "scope_revision": amendment["scope_revision"],
            "source_snapshot_revision": amendment["source_snapshot_revision"],
            "last_user_input_id": user_input_id,
            "amendment_refs": amendment_refs,
        }
    )


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
