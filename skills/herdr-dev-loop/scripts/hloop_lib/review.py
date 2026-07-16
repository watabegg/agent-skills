"""Deterministic review-swarm primitives for herdr-dev-loop 0.5.

The CLI owns provider processes, clocks, files, and state transitions.  This
module only plans a review group, normalizes provider findings, allocates a
bounded verifier pool, and evaluates whether a review manifest is complete.
No result becomes consensus merely because its title matches: fingerprints are
derived from the reported location, trigger, impact, and proposed fix.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence

from . import review_policy as hloop_review_policy


REVIEW_MODES = frozenset({"single", "swarm", "dual", "dual-swarm"})
SWARM_MODES = frozenset({"swarm", "dual-swarm"})
DUAL_MODES = frozenset({"dual", "dual-swarm"})
SUPPORTED_PROVIDERS = ("codex", "claude")
SEVERITIES = ("P0", "P1", "P2", "P3")
CRITICAL_SEVERITIES = frozenset({"P0", "P1"})
DISCOVERY_STATUSES = frozenset({"completed", "failed", "timeout"})
FACT_STATUSES = frozenset(
    {"confirmed", "refuted", "needs_spec", "insufficient_evidence"}
)
IGNORE_STATUSES = frozenset({"must_not_ignore", "may_defer", "no_action"})
DECISION_STATUSES = frozenset(
    {"none", "localized_user_choice", "blocking_user_choice"}
)
PROGRESS_STATUSES = frozenset({"yes", "partial", "no"})
RECOMMENDED_ACTIONS = frozenset(
    {"fix_task", "ask_user", "accepted_risk_candidate", "discard"}
)
FINDING_ORIGINS = frozenset(
    {
        "introduced",
        "diff-expanded-pre-existing",
        "unrelated-pre-existing",
        "unknown",
    }
)

# These are the Manager's seven independent disposition axes.  The older
# verification model below intentionally keeps its ``FACT_STATUSES`` and
# ``DECISION_STATUSES`` values (for example ``needs_spec``) for legacy review
# manifests.  New finding records use this separate vocabulary and are
# required to serialize every axis.
POLICY_FACT_STATUSES = frozenset(
    {"confirmed", "refuted", "insufficient_evidence"}
)
POLICY_CONTRACT_RELATIONS = frozenset(
    {"in_scope", "outside_release", "ambiguous"}
)
POLICY_DECISION_REQUIREMENTS = frozenset({"none", "spec", "user"})
POLICY_DISPOSITIONS = frozenset(
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
POLICY_RELEASE_EFFECTS = frozenset({"blocking", "non_blocking"})
POLICY_BLOCKING_DISPOSITIONS = frozenset(
    {"fix_now", "disable_feature", "mark_experimental", "user_decision"}
)
POLICY_NON_BLOCKING_DISPOSITIONS = frozenset(
    {"defer_follow_up", "accepted_risk", "discard"}
)
POLICY_AXES = (
    "fact_status",
    "origin",
    "contract_relation",
    "decision_requirement",
    "disposition",
    "release_effect",
)

DEFAULT_DISCOVERY_LANES = (
    "integration contract and write scope",
    "product correctness and edge cases",
    "security privacy auth and tenant scope",
    "data integrity migration concurrency and idempotency",
    "performance resources failure recovery and observability",
    "validation tests and QA evidence",
    "UX accessibility and copy",
    "repository-specific risks",
)
DEFAULT_SWARM_PROBES = 6
DEFAULT_DUAL_SWARM_PROBES = 4
MIN_SWARM_PROBES = 4
MAX_SWARM_PROBES = 8

_REVIEW_ID_RE = re.compile(r"^R[0-9]{3}$")


class ReviewModelError(ValueError):
    """Raised when a review plan or artifact violates an invariant."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewModelError(f"{field_name} must be a non-empty string")
    return value.strip()


def _text_tuple(
    values: Sequence[str], field_name: str, *, unique: bool = True
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReviewModelError(f"{field_name} must be a sequence of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if unique and len(set(normalized)) != len(normalized):
        raise ReviewModelError(f"{field_name} must not contain duplicates")
    return normalized


def _provider_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    providers = _text_tuple(values, field_name)
    invalid = set(providers) - set(SUPPORTED_PROVIDERS)
    if invalid:
        raise ReviewModelError(
            f"{field_name} contains unsupported providers: {', '.join(sorted(invalid))}"
        )
    return tuple(provider for provider in SUPPORTED_PROVIDERS if provider in providers)


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewModelError(f"{field_name} must be an object")
    return value


def _record_items(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReviewModelError(f"{field_name} must be an array")
    return tuple(value)


def _require_record_fields(
    record: Mapping[str, Any], field_name: str, fields: Sequence[str]
) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise ReviewModelError(
            f"{field_name} is missing required fields: {', '.join(missing)}"
        )


def _policy_axis(
    value: Any, field_name: str, allowed: Sequence[str] | set[str]
) -> str:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ReviewModelError(
            f"unsupported {field_name}: {value!r}; expected {choices}"
        )
    return str(value)


def _policy_axes_present(record: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(has_any, has_all)`` for the new finding axes.

    A format-3 manifest created before 0.5.2 may omit all policy axes.  It is
    still readable so that legacy cadence and review evidence remain usable.
    A partially upgraded record is never accepted: it would otherwise make a
    missing axis look like a deliberate Manager decision.
    """

    # ``origin`` existed on the pre-0.5.2 candidate record, so it cannot by
    # itself signal that a new policy record is present.
    new_axis_fields = tuple(
        field_name for field_name in POLICY_AXES if field_name != "origin"
    )
    has_new_axis = any(field_name in record for field_name in new_axis_fields)
    present = [field_name in record for field_name in POLICY_AXES]
    return has_new_axis, all(present)


def _legacy_policy_axes(*, requires_spec_decision: bool) -> dict[str, str]:
    """Provide a compatibility projection for pre-0.5.2 candidates.

    The projection is intentionally marked as legacy by the caller.  Runtime
    convergence uses the old verification recommendation for those records;
    the values here only make the serialized shape schema-complete.
    """

    return {
        "fact_status": "confirmed",
        "contract_relation": "in_scope",
        "decision_requirement": "user" if requires_spec_decision else "none",
        "disposition": "fix_now",
        "release_effect": "blocking",
    }


def _policy_axes_from_record(
    record: Mapping[str, Any],
    *,
    requires_spec_decision: bool,
    field_name: str,
) -> tuple[dict[str, str], bool]:
    has_any, has_all = _policy_axes_present(record)
    if has_any and not has_all:
        missing = [name for name in POLICY_AXES if name not in record]
        raise ReviewModelError(
            f"{field_name} is missing policy axes: {', '.join(missing)}"
        )
    if not has_all:
        return _legacy_policy_axes(requires_spec_decision=requires_spec_decision), False

    axes = {
        "fact_status": _policy_axis(
            record["fact_status"], "fact_status", POLICY_FACT_STATUSES
        ),
        "origin": _policy_axis(record["origin"], "origin", FINDING_ORIGINS),
        "contract_relation": _policy_axis(
            record["contract_relation"],
            "contract_relation",
            POLICY_CONTRACT_RELATIONS,
        ),
        "decision_requirement": _policy_axis(
            record["decision_requirement"],
            "decision_requirement",
            POLICY_DECISION_REQUIREMENTS,
        ),
        "disposition": _policy_axis(
            record["disposition"], "disposition", POLICY_DISPOSITIONS
        ),
        "release_effect": _policy_axis(
            record["release_effect"],
            "release_effect",
            POLICY_RELEASE_EFFECTS,
        ),
    }
    explicit = record.get("policy_axes_explicit", True)
    if not isinstance(explicit, bool):
        raise ReviewModelError(f"{field_name}.policy_axes_explicit must be boolean")
    return axes, explicit


@dataclass(frozen=True, slots=True)
class ReviewBudget:
    """Hard planning bounds for verifier concurrency and total usage."""

    max_parallel_verifiers: int = 2
    max_verifications: int = 64
    time_limit_seconds: int = 1800
    provider_limits: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_parallel_verifiers, bool)
            or not isinstance(self.max_parallel_verifiers, int)
            or not 1 <= self.max_parallel_verifiers <= 8
        ):
            raise ReviewModelError("max_parallel_verifiers must be between 1 and 8")
        if (
            isinstance(self.max_verifications, bool)
            or not isinstance(self.max_verifications, int)
            or self.max_verifications < 1
        ):
            raise ReviewModelError("max_verifications must be a positive integer")
        if (
            isinstance(self.time_limit_seconds, bool)
            or not isinstance(self.time_limit_seconds, int)
            or self.time_limit_seconds < 1
        ):
            raise ReviewModelError("time_limit_seconds must be a positive integer")

        raw_limits = self.provider_limits
        if isinstance(raw_limits, Mapping):
            raw_limits = tuple(raw_limits.items())
        normalized: list[tuple[str, int]] = []
        seen: set[str] = set()
        for provider, limit in raw_limits:
            provider = _required_text(provider, "provider_limits provider")
            if provider not in SUPPORTED_PROVIDERS:
                raise ReviewModelError(f"unsupported provider limit: {provider}")
            if provider in seen:
                raise ReviewModelError(f"duplicate provider limit: {provider}")
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ReviewModelError("provider limits must be non-negative integers")
            normalized.append((provider, limit))
            seen.add(provider)
        normalized.sort(key=lambda item: SUPPORTED_PROVIDERS.index(item[0]))
        object.__setattr__(self, "provider_limits", tuple(normalized))

    def limit_for(self, provider: str) -> int:
        for candidate, limit in self.provider_limits:
            if candidate == provider:
                return limit
        return self.max_verifications

    def to_record(self) -> dict[str, Any]:
        return {
            "max_parallel_verifiers": self.max_parallel_verifiers,
            "max_verifications": self.max_verifications,
            "time_limit_seconds": self.time_limit_seconds,
            "provider_limits": dict(self.provider_limits),
        }

    @classmethod
    def from_record(cls, value: Any) -> "ReviewBudget":
        record = _record(value, "budget")
        _require_record_fields(
            record,
            "budget",
            (
                "max_parallel_verifiers",
                "max_verifications",
                "time_limit_seconds",
                "provider_limits",
            ),
        )
        limits = _record(record["provider_limits"], "budget.provider_limits")
        return cls(
            max_parallel_verifiers=record["max_parallel_verifiers"],
            max_verifications=record["max_verifications"],
            time_limit_seconds=record["time_limit_seconds"],
            provider_limits=tuple(limits.items()),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryLanePlan:
    provider: str
    lane_id: str
    purpose: str
    agent_label: str

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ReviewModelError(f"unsupported lane provider: {self.provider}")
        object.__setattr__(self, "lane_id", _required_text(self.lane_id, "lane_id"))
        object.__setattr__(self, "purpose", _required_text(self.purpose, "lane purpose"))
        object.__setattr__(
            self, "agent_label", _required_text(self.agent_label, "lane agent_label")
        )

    def result(self, *, status: str = "completed", finding_count: int = 0) -> "DiscoveryLaneResult":
        return DiscoveryLaneResult(
            provider=self.provider,
            lane_id=self.lane_id,
            purpose=self.purpose,
            agent_label=self.agent_label,
            status=status,
            finding_count=finding_count,
        )

    def to_record(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "lane_id": self.lane_id,
            "purpose": self.purpose,
            "agent_label": self.agent_label,
        }

    @classmethod
    def from_record(cls, value: Any) -> "DiscoveryLanePlan":
        record = _record(value, "lane plan")
        _require_record_fields(
            record, "lane plan", ("provider", "lane_id", "purpose", "agent_label")
        )
        return cls(
            provider=record["provider"],
            lane_id=record["lane_id"],
            purpose=record["purpose"],
            agent_label=record["agent_label"],
        )


@dataclass(frozen=True, slots=True)
class DiscoveryLaneResult:
    provider: str
    lane_id: str
    purpose: str
    agent_label: str
    status: str
    finding_count: int = 0

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ReviewModelError(f"unsupported lane provider: {self.provider}")
        for field_name in ("lane_id", "purpose", "agent_label"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.status not in DISCOVERY_STATUSES:
            raise ReviewModelError(f"unsupported discovery status: {self.status}")
        if (
            isinstance(self.finding_count, bool)
            or not isinstance(self.finding_count, int)
            or self.finding_count < 0
        ):
            raise ReviewModelError("finding_count must be a non-negative integer")

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.lane_id

    def to_record(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "lane_id": self.lane_id,
            "purpose": self.purpose,
            "agent_label": self.agent_label,
            "status": self.status,
            "finding_count": self.finding_count,
        }

    @classmethod
    def from_record(cls, value: Any) -> "DiscoveryLaneResult":
        record = _record(value, "lane result")
        _require_record_fields(
            record,
            "lane result",
            ("provider", "lane_id", "purpose", "agent_label", "status", "finding_count"),
        )
        return cls(
            provider=record["provider"],
            lane_id=record["lane_id"],
            purpose=record["purpose"],
            agent_label=record["agent_label"],
            status=record["status"],
            finding_count=record["finding_count"],
        )


@dataclass(frozen=True, slots=True)
class ProviderReviewPlan:
    provider: str
    model: str
    head_sha: str
    role: str
    coordinator_label: str
    lanes: tuple[DiscoveryLanePlan, ...]
    verifier_agents: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ReviewModelError(f"unsupported review provider: {self.provider}")
        object.__setattr__(self, "model", _required_text(self.model, "model"))
        object.__setattr__(self, "head_sha", _required_text(self.head_sha, "head_sha"))
        if self.role not in {"reviewer", "coordinator"}:
            raise ReviewModelError(f"unsupported provider review role: {self.role}")
        object.__setattr__(
            self,
            "coordinator_label",
            _required_text(self.coordinator_label, "coordinator_label"),
        )
        lanes = tuple(self.lanes)
        if not lanes:
            raise ReviewModelError("provider review plans require at least one lane")
        if any(lane.provider != self.provider for lane in lanes):
            raise ReviewModelError("lane provider must match its provider review plan")
        lane_ids = [lane.lane_id for lane in lanes]
        if len(set(lane_ids)) != len(lane_ids):
            raise ReviewModelError("lane ids must be unique per provider")
        verifiers = _text_tuple(self.verifier_agents, "verifier_agents")
        object.__setattr__(self, "lanes", lanes)
        object.__setattr__(self, "verifier_agents", verifiers)

    def to_record(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "head_sha": self.head_sha,
            "role": self.role,
            "coordinator_label": self.coordinator_label,
            "required_lanes": [lane.to_record() for lane in self.lanes],
            "verifier_pool": list(self.verifier_agents),
        }

    @classmethod
    def from_record(cls, value: Any) -> "ProviderReviewPlan":
        record = _record(value, "provider plan")
        _require_record_fields(
            record,
            "provider plan",
            (
                "provider",
                "model",
                "head_sha",
                "role",
                "coordinator_label",
                "required_lanes",
                "verifier_pool",
            ),
        )
        return cls(
            provider=record["provider"],
            model=record["model"],
            head_sha=record["head_sha"],
            role=record["role"],
            coordinator_label=record["coordinator_label"],
            lanes=tuple(
                DiscoveryLanePlan.from_record(item)
                for item in _record_items(record["required_lanes"], "required_lanes")
            ),
            verifier_agents=tuple(
                _record_items(record["verifier_pool"], "verifier_pool")
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewGroupPlan:
    mode: str
    head_sha: str
    providers: tuple[str, ...]
    provider_plans: tuple[ProviderReviewPlan, ...]
    budget: ReviewBudget

    def __post_init__(self) -> None:
        if self.mode not in REVIEW_MODES:
            raise ReviewModelError(f"unsupported review mode: {self.mode}")
        head_sha = _required_text(self.head_sha, "head_sha")
        providers = _provider_tuple(self.providers, "providers")
        expected_count = 2 if self.mode in DUAL_MODES else 1
        if len(providers) != expected_count:
            raise ReviewModelError(
                f"{self.mode} mode requires {expected_count} distinct provider(s)"
            )
        plans = tuple(self.provider_plans)
        if tuple(plan.provider for plan in plans) != providers:
            raise ReviewModelError("provider plans must follow canonical provider order")
        if any(plan.head_sha != head_sha for plan in plans):
            raise ReviewModelError("all provider plans must audit the same head_sha")
        for plan in plans:
            if self.mode in SWARM_MODES:
                if (
                    plan.role != "coordinator"
                    or not MIN_SWARM_PROBES <= len(plan.lanes) <= MAX_SWARM_PROBES
                ):
                    raise ReviewModelError("swarm provider plans require 4 to 8 discovery lanes")
            elif plan.role != "reviewer" or len(plan.lanes) != 1:
                raise ReviewModelError("single-review provider plans require one holistic lane")
            if len(plan.verifier_agents) != self.budget.max_parallel_verifiers:
                raise ReviewModelError(
                    "verifier pool size must equal max_parallel_verifiers"
                )
        object.__setattr__(self, "head_sha", head_sha)
        object.__setattr__(self, "providers", providers)
        object.__setattr__(self, "provider_plans", plans)

    def provider_plan(self, provider: str) -> ProviderReviewPlan:
        for plan in self.provider_plans:
            if plan.provider == provider:
                return plan
        raise ReviewModelError(f"provider is not part of this review group: {provider}")

    @property
    def expected_lanes(self) -> tuple[DiscoveryLanePlan, ...]:
        return tuple(lane for plan in self.provider_plans for lane in plan.lanes)

    def to_record(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "head_sha": self.head_sha,
            "providers": [plan.to_record() for plan in self.provider_plans],
            "budget": self.budget.to_record(),
        }

    @classmethod
    def from_record(cls, value: Any) -> "ReviewGroupPlan":
        record = _record(value, "review group plan")
        _require_record_fields(
            record, "review group plan", ("mode", "head_sha", "providers", "budget")
        )
        provider_plans = tuple(
            ProviderReviewPlan.from_record(item)
            for item in _record_items(record["providers"], "providers")
        )
        return cls(
            mode=record["mode"],
            head_sha=record["head_sha"],
            providers=tuple(plan.provider for plan in provider_plans),
            provider_plans=provider_plans,
            budget=ReviewBudget.from_record(record["budget"]),
        )


DEFAULT_PROVIDER_REVIEW_CAPACITY = 10
"""Default concurrent-agent ceiling per provider (coordinator/reviewer + discovery
lanes + verifier pool). Swarm modes request up to 8 lanes + a coordinator + a
verifier pool, so the default leaves headroom without being unbounded."""


def required_provider_capacity(plan: ReviewGroupPlan) -> dict[str, int]:
    """Concurrent agents each provider must host to run ``plan``.

    Counts the coordinator process (swarm modes only; single/dual reviewers run
    holistically with no separate coordinator), every discovery lane, and the
    bounded verifier pool. Callers check this against a configured or
    provider-reported ceiling *before* creating any pane, so an unsatisfiable
    plan never leaves partially-started process state behind.
    """

    required: dict[str, int] = {}
    for provider_plan in plan.provider_plans:
        coordinator = 1 if provider_plan.role == "coordinator" else 0
        required[provider_plan.provider] = (
            coordinator + len(provider_plan.lanes) + len(provider_plan.verifier_agents)
        )
    return required


def plan_review_group(
    mode: str,
    *,
    head_sha: str,
    provider: str = "codex",
    providers: Sequence[str] | None = None,
    model: str = "auto",
    models: Mapping[str, str] | None = None,
    lane_purposes: Sequence[str] | None = None,
    probe_count: int | None = None,
    probes_per_provider: int | None = None,
    verifier_pool_size: int = 2,
    max_verifications: int = 64,
    time_limit_seconds: int = 1800,
    provider_verification_limits: Mapping[str, int] | None = None,
) -> ReviewGroupPlan:
    """Build a stable provider/lane/verifier topology for one target SHA."""

    if mode not in REVIEW_MODES:
        raise ReviewModelError(f"unsupported review mode: {mode}")
    if probe_count is not None and probes_per_provider is not None:
        raise ReviewModelError("set probe_count or probes_per_provider, not both")

    raw_providers = providers if providers is not None else (
        SUPPORTED_PROVIDERS if mode in DUAL_MODES else (provider,)
    )
    selected_providers = _provider_tuple(raw_providers, "providers")
    expected_count = 2 if mode in DUAL_MODES else 1
    if len(selected_providers) != expected_count:
        raise ReviewModelError(
            f"{mode} mode requires {expected_count} distinct provider(s)"
        )
    unused_provider_limits = set(provider_verification_limits or {}) - set(
        selected_providers
    )
    if unused_provider_limits:
        raise ReviewModelError(
            "provider verification limits contain providers outside the review group: "
            + ", ".join(sorted(unused_provider_limits))
        )

    if mode in SWARM_MODES:
        requested_count = probe_count if probe_count is not None else probes_per_provider
        if requested_count is None:
            requested_count = (
                DEFAULT_DUAL_SWARM_PROBES if mode == "dual-swarm" else DEFAULT_SWARM_PROBES
            )
        if isinstance(requested_count, bool) or not isinstance(requested_count, int):
            raise ReviewModelError("review probe count must be an integer")
        if not MIN_SWARM_PROBES <= requested_count <= MAX_SWARM_PROBES:
            raise ReviewModelError("swarm modes require 4 to 8 discovery probes per provider")
        purposes = (
            _text_tuple(lane_purposes, "lane_purposes", unique=False)
            if lane_purposes is not None
            else DEFAULT_DISCOVERY_LANES[:requested_count]
        )
        if len(purposes) != requested_count:
            raise ReviewModelError("lane_purposes must match the requested probe count")
    else:
        if lane_purposes is not None or probe_count is not None or probes_per_provider is not None:
            raise ReviewModelError("single and dual modes use one fixed holistic lane")
        purposes = ("holistic review",)

    budget = ReviewBudget(
        max_parallel_verifiers=verifier_pool_size,
        max_verifications=max_verifications,
        time_limit_seconds=time_limit_seconds,
        provider_limits=tuple((provider_verification_limits or {}).items()),
    )
    model_map = dict(models or {})
    unknown_models = set(model_map) - set(selected_providers)
    if unknown_models:
        raise ReviewModelError(
            "models contains providers outside the review group: "
            + ", ".join(sorted(unknown_models))
        )

    provider_plans: list[ProviderReviewPlan] = []
    for selected in selected_providers:
        is_swarm = mode in SWARM_MODES
        lanes = tuple(
            DiscoveryLanePlan(
                provider=selected,
                lane_id=f"{selected}-L{index:02d}",
                purpose=purpose,
                agent_label=(
                    f"{selected}-discovery-{index:02d}"
                    if is_swarm
                    else f"{selected}-reviewer"
                ),
            )
            for index, purpose in enumerate(purposes, start=1)
        )
        provider_plans.append(
            ProviderReviewPlan(
                provider=selected,
                model=_required_text(model_map.get(selected, model), f"model for {selected}"),
                head_sha=head_sha,
                role="coordinator" if is_swarm else "reviewer",
                coordinator_label=(
                    f"{selected}-coordinator" if is_swarm else f"{selected}-reviewer"
                ),
                lanes=lanes,
                verifier_agents=tuple(
                    f"{selected}-verifier-{index:02d}"
                    for index in range(1, verifier_pool_size + 1)
                ),
            )
        )

    return ReviewGroupPlan(
        mode=mode,
        head_sha=head_sha,
        providers=selected_providers,
        provider_plans=tuple(provider_plans),
        budget=budget,
    )


def _canonical_phrase(value: str) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "fingerprint component"))
    return " ".join(text.split()).casefold()


def finding_fingerprint(
    *,
    file_path: str,
    symbol: str,
    trigger: str,
    product_impact: str,
    proposed_fix: str,
) -> str:
    """Fingerprint semantics rather than provider wording or finding title."""

    normalized_path = _required_text(file_path, "file_path").replace("\\", "/")
    while normalized_path.startswith("./"):
        normalized_path = normalized_path[2:]
    payload = {
        "location": {
            "file": normalized_path,
            "symbol": _required_text(symbol, "symbol"),
        },
        "trigger": _canonical_phrase(trigger),
        "product_impact": _canonical_phrase(product_impact),
        "proposed_fix": _canonical_phrase(proposed_fix),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FindingCandidate:
    finding_id: str
    provider: str
    head_sha: str
    discovering_agent: str
    severity: str
    confidence: float
    title: str
    file_path: str
    line: int
    symbol: str
    trigger: str
    product_impact: str
    origin: str
    proposed_fix: str
    requires_spec_decision: bool = False
    fact_status: str | None = None
    contract_relation: str | None = None
    decision_requirement: str | None = None
    disposition: str | None = None
    release_effect: str | None = None
    requirement_refs: tuple[str, ...] = ()
    why_fix_now: str = ""
    policy_axes_explicit: bool | None = None
    accepted_risk_decision_id: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "finding_id",
            "head_sha",
            "discovering_agent",
            "title",
            "file_path",
            "symbol",
            "trigger",
            "product_impact",
            "proposed_fix",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ReviewModelError(f"unsupported finding provider: {self.provider}")
        if self.severity not in SEVERITIES:
            raise ReviewModelError(f"unsupported finding severity: {self.severity}")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ReviewModelError("confidence must be a number between 0 and 1")
        if not 0 <= float(self.confidence) <= 1:
            raise ReviewModelError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", float(self.confidence))
        if isinstance(self.line, bool) or not isinstance(self.line, int) or self.line < 1:
            raise ReviewModelError("line must be a positive integer")
        if self.origin not in FINDING_ORIGINS:
            raise ReviewModelError(f"unsupported finding origin: {self.origin}")
        if not isinstance(self.requires_spec_decision, bool):
            raise ReviewModelError("requires_spec_decision must be boolean")
        object.__setattr__(
            self,
            "accepted_risk_decision_id",
            str(self.accepted_risk_decision_id or "").strip(),
        )

        axes, inferred_explicit = _policy_axes_from_record(
            {
                "origin": self.origin,
                **{
                    name: getattr(self, name)
                    for name in POLICY_AXES
                    if name != "origin" and getattr(self, name) is not None
                },
            },
            requires_spec_decision=self.requires_spec_decision,
            field_name="finding candidate",
        )
        explicit = inferred_explicit
        if self.policy_axes_explicit is not None:
            if not isinstance(self.policy_axes_explicit, bool):
                raise ReviewModelError("policy_axes_explicit must be boolean")
            explicit = self.policy_axes_explicit
        if explicit and not inferred_explicit:
            raise ReviewModelError(
                "policy_axes_explicit cannot be true without all policy axes"
            )
        for field_name, value in axes.items():
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "requirement_refs",
            _text_tuple(self.requirement_refs, "requirement_refs"),
        )
        if not isinstance(self.why_fix_now, str):
            raise ReviewModelError("why_fix_now must be a string")
        object.__setattr__(self, "why_fix_now", self.why_fix_now.strip())
        object.__setattr__(self, "policy_axes_explicit", explicit)

    @property
    def fingerprint(self) -> str:
        return finding_fingerprint(
            file_path=self.file_path,
            symbol=self.symbol,
            trigger=self.trigger,
            product_impact=self.product_impact,
            proposed_fix=self.proposed_fix,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "fingerprint": self.fingerprint,
            "provider": self.provider,
            "head_sha": self.head_sha,
            "discovering_agent": self.discovering_agent,
            "severity": self.severity,
            "confidence": self.confidence,
            "title": self.title,
            "file": self.file_path,
            "line": self.line,
            "symbol": self.symbol,
            "trigger": self.trigger,
            "product_impact": self.product_impact,
            "origin": self.origin,
            "proposed_fix": self.proposed_fix,
            "requires_spec_decision": self.requires_spec_decision,
            "fact_status": self.fact_status,
            "contract_relation": self.contract_relation,
            "decision_requirement": self.decision_requirement,
            "disposition": self.disposition,
            "release_effect": self.release_effect,
            "requirement_refs": list(self.requirement_refs),
            "why_fix_now": self.why_fix_now,
            "policy_axes_explicit": self.policy_axes_explicit,
            "accepted_risk_decision_id": self.accepted_risk_decision_id,
        }

    @classmethod
    def from_record(cls, value: Any) -> "FindingCandidate":
        record = _record(value, "finding candidate")
        _require_record_fields(
            record,
            "finding candidate",
            (
                "finding_id",
                "fingerprint",
                "provider",
                "head_sha",
                "discovering_agent",
                "severity",
                "confidence",
                "title",
                "file",
                "line",
                "symbol",
                "trigger",
                "product_impact",
                "origin",
                "proposed_fix",
                "requires_spec_decision",
            ),
        )
        axes, explicit = _policy_axes_from_record(
            record,
            requires_spec_decision=record["requires_spec_decision"],
            field_name="finding candidate",
        )
        axes.pop("origin", None)
        candidate = cls(
            finding_id=record["finding_id"],
            provider=record["provider"],
            head_sha=record["head_sha"],
            discovering_agent=record["discovering_agent"],
            severity=record["severity"],
            confidence=record["confidence"],
            title=record["title"],
            file_path=record["file"],
            line=record["line"],
            symbol=record["symbol"],
            trigger=record["trigger"],
            product_impact=record["product_impact"],
            origin=record["origin"],
            proposed_fix=record["proposed_fix"],
            requires_spec_decision=record["requires_spec_decision"],
            **axes,
            requirement_refs=record.get("requirement_refs", ()),
            why_fix_now=record.get("why_fix_now", ""),
            policy_axes_explicit=record.get("policy_axes_explicit", explicit),
            accepted_risk_decision_id=record.get(
                "accepted_risk_decision_id", record.get("decision_id", "")
            ),
        )
        if record["fingerprint"] != candidate.fingerprint:
            raise ReviewModelError("finding candidate fingerprint does not match its evidence")
        return candidate


@dataclass(frozen=True, slots=True)
class NormalizedFinding:
    fingerprint: str
    head_sha: str
    candidates: tuple[FindingCandidate, ...]
    fact_status: str | None = None
    origin: str | None = None
    contract_relation: str | None = None
    decision_requirement: str | None = None
    disposition: str | None = None
    release_effect: str | None = None
    requirement_refs: tuple[str, ...] = ()
    why_fix_now: str = ""
    policy_axes_explicit: bool | None = None
    accepted_risk_decision_id: str = ""

    def __post_init__(self) -> None:
        fingerprint = _required_text(self.fingerprint, "fingerprint")
        head_sha = _required_text(self.head_sha, "head_sha")
        candidates = tuple(
            sorted(self.candidates, key=lambda item: (item.provider, item.finding_id))
        )
        if not candidates:
            raise ReviewModelError("normalized findings require at least one candidate")
        if any(candidate.fingerprint != fingerprint for candidate in candidates):
            raise ReviewModelError("normalized finding candidates must share a fingerprint")
        if any(candidate.head_sha != head_sha for candidate in candidates):
            raise ReviewModelError("normalized finding candidates must share a head_sha")
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "head_sha", head_sha)
        object.__setattr__(self, "candidates", candidates)
        candidate_decision_ids = {
            candidate.accepted_risk_decision_id
            for candidate in candidates
            if candidate.accepted_risk_decision_id
        }
        decision_id = str(self.accepted_risk_decision_id or "").strip()
        if decision_id and candidate_decision_ids and candidate_decision_ids != {decision_id}:
            raise ReviewModelError(
                "normalized finding accepted-risk decision does not match candidates"
            )
        if not decision_id and len(candidate_decision_ids) == 1:
            decision_id = next(iter(candidate_decision_ids))
        if len(candidate_decision_ids) > 1:
            raise ReviewModelError(
                "normalized finding candidates must share one accepted-risk decision"
            )
        object.__setattr__(self, "accepted_risk_decision_id", decision_id)

        candidate_axes = [
            candidate
            for candidate in candidates
            if candidate.policy_axes_explicit
        ]
        inferred_explicit = bool(candidate_axes)
        if all(value is None for value in (
            self.fact_status,
            self.origin,
            self.contract_relation,
            self.decision_requirement,
            self.disposition,
            self.release_effect,
        )):
            if candidate_axes:
                source = candidate_axes[0]
                axes = {
                    field_name: getattr(source, field_name)
                    for field_name in POLICY_AXES
                }
                requirement_refs = source.requirement_refs
                why_fix_now = source.why_fix_now
            else:
                source = candidates[0]
                axes = _legacy_policy_axes(
                    requires_spec_decision=source.requires_spec_decision
                )
                axes["origin"] = source.origin
                requirement_refs = ()
                why_fix_now = ""
        else:
            axes, explicit_from_record = _policy_axes_from_record(
                {
                    "origin": self.origin,
                    "fact_status": self.fact_status,
                    "contract_relation": self.contract_relation,
                    "decision_requirement": self.decision_requirement,
                    "disposition": self.disposition,
                    "release_effect": self.release_effect,
                },
                requires_spec_decision=candidates[0].requires_spec_decision,
                field_name="normalized finding",
            )
            inferred_explicit = explicit_from_record
            requirement_refs = self.requirement_refs
            why_fix_now = self.why_fix_now
        explicit = inferred_explicit
        if self.policy_axes_explicit is not None:
            if not isinstance(self.policy_axes_explicit, bool):
                raise ReviewModelError("policy_axes_explicit must be boolean")
            explicit = self.policy_axes_explicit
        if explicit and not inferred_explicit:
            raise ReviewModelError(
                "policy_axes_explicit cannot be true without all policy axes"
            )
        for field_name, value in axes.items():
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "requirement_refs",
            _text_tuple(requirement_refs, "requirement_refs"),
        )
        if not isinstance(why_fix_now, str):
            raise ReviewModelError("why_fix_now must be a string")
        object.__setattr__(self, "why_fix_now", why_fix_now.strip())
        object.__setattr__(self, "policy_axes_explicit", explicit)

    @property
    def providers(self) -> tuple[str, ...]:
        discovered = {candidate.provider for candidate in self.candidates}
        return tuple(provider for provider in SUPPORTED_PROVIDERS if provider in discovered)

    @property
    def discovering_agents(self) -> tuple[str, ...]:
        return tuple(sorted({candidate.discovering_agent for candidate in self.candidates}))

    @property
    def severity(self) -> str:
        return min(
            (candidate.severity for candidate in self.candidates),
            key=SEVERITIES.index,
        )

    @property
    def requires_spec_decision(self) -> bool:
        return any(candidate.requires_spec_decision for candidate in self.candidates)

    @property
    def classification(self) -> str:
        return "consensus" if len(self.providers) > 1 else "unique"

    @property
    def cross_model_consensus(self) -> bool:
        return self.classification == "consensus"

    @property
    def is_actionable(self) -> bool:
        """Whether the approved axes require in-scope release action."""

        return bool(
            self.policy_axes_explicit
            and self.fact_status == "confirmed"
            and self.contract_relation == "in_scope"
            and self.disposition in POLICY_BLOCKING_DISPOSITIONS
            and self.release_effect == "blocking"
        )

    @property
    def is_release_blocking(self) -> bool:
        """Whether this verified finding blocks the current release."""

        return bool(
            self.policy_axes_explicit
            and self.fact_status == "confirmed"
            and self.contract_relation == "in_scope"
            and self.release_effect == "blocking"
            and self.disposition not in POLICY_NON_BLOCKING_DISPOSITIONS
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "head_sha": self.head_sha,
            "severity": self.severity,
            "requires_spec_decision": self.requires_spec_decision,
            "classification": self.classification,
            "cross_model_consensus": self.cross_model_consensus,
            "providers": list(self.providers),
            "candidate_ids": [
                f"{candidate.provider}:{candidate.finding_id}"
                for candidate in self.candidates
            ],
            "discovering_agents": list(self.discovering_agents),
            "fact_status": self.fact_status,
            "origin": self.origin,
            "contract_relation": self.contract_relation,
            "decision_requirement": self.decision_requirement,
            "disposition": self.disposition,
            "release_effect": self.release_effect,
            "requirement_refs": list(self.requirement_refs),
            "why_fix_now": self.why_fix_now,
            "policy_axes_explicit": self.policy_axes_explicit,
            "accepted_risk_decision_id": self.accepted_risk_decision_id,
            "candidates": [candidate.to_record() for candidate in self.candidates],
        }

    @classmethod
    def from_record(cls, value: Any) -> "NormalizedFinding":
        record = _record(value, "normalized finding")
        _require_record_fields(
            record,
            "normalized finding",
            (
                "fingerprint",
                "head_sha",
                "severity",
                "requires_spec_decision",
                "classification",
                "cross_model_consensus",
                "providers",
                "candidate_ids",
                "discovering_agents",
                "candidates",
            ),
        )
        candidate_records = tuple(
            FindingCandidate.from_record(item)
            for item in _record_items(record["candidates"], "finding candidates")
        )
        axes, explicit = _policy_axes_from_record(
            record,
            requires_spec_decision=bool(record.get("requires_spec_decision", False)),
            field_name="normalized finding",
        )
        # The normalized record is authoritative when present.  Candidate
        # records remain available for each provider's independent evidence.
        if not explicit:
            axes = dict(axes)
            axes["origin"] = str(record.get("origin") or candidate_records[0].origin)
        finding = cls(
            fingerprint=record["fingerprint"],
            head_sha=record["head_sha"],
            candidates=candidate_records,
            **axes,
            requirement_refs=record.get("requirement_refs", ()),
            why_fix_now=record.get("why_fix_now", ""),
            policy_axes_explicit=record.get("policy_axes_explicit", explicit),
            accepted_risk_decision_id=record.get(
                "accepted_risk_decision_id", record.get("decision_id", "")
            ),
        )
        declared = {
            "severity": record["severity"],
            "requires_spec_decision": record["requires_spec_decision"],
            "classification": record["classification"],
            "cross_model_consensus": record["cross_model_consensus"],
            "providers": list(_record_items(record["providers"], "finding providers")),
            "candidate_ids": list(
                _record_items(record["candidate_ids"], "finding candidate_ids")
            ),
            "discovering_agents": list(
                _record_items(record["discovering_agents"], "finding discovering_agents")
            ),
            "fact_status": finding.fact_status,
            "origin": finding.origin,
            "contract_relation": finding.contract_relation,
            "decision_requirement": finding.decision_requirement,
            "disposition": finding.disposition,
            "release_effect": finding.release_effect,
            "requirement_refs": list(finding.requirement_refs),
            "why_fix_now": finding.why_fix_now,
            "policy_axes_explicit": finding.policy_axes_explicit,
        }
        actual = finding.to_record()
        if any(actual[key] != value for key, value in declared.items()):
            raise ReviewModelError("normalized finding summary does not match its candidates")
        return finding


def normalize_findings(findings: Sequence[FindingCandidate]) -> tuple[NormalizedFinding, ...]:
    """Deduplicate within a SHA and retain every provider's independent report."""

    if isinstance(findings, (str, bytes)) or not isinstance(findings, Sequence):
        raise ReviewModelError("findings must be a sequence")
    grouped: dict[tuple[str, str], list[FindingCandidate]] = {}
    discovery_ids: set[tuple[str, str, str]] = set()
    for finding in findings:
        if not isinstance(finding, FindingCandidate):
            raise ReviewModelError("findings must contain FindingCandidate values")
        discovery_id = (finding.head_sha, finding.provider, finding.finding_id)
        if discovery_id in discovery_ids:
            raise ReviewModelError(
                "finding ids must be unique within one provider and target SHA"
            )
        discovery_ids.add(discovery_id)
        grouped.setdefault((finding.head_sha, finding.fingerprint), []).append(finding)

    return tuple(
        NormalizedFinding(fingerprint=fingerprint, head_sha=head_sha, candidates=tuple(items))
        for (head_sha, fingerprint), items in sorted(grouped.items())
    )


@dataclass(frozen=True, slots=True)
class VerifierAssignment:
    fingerprint: str
    head_sha: str
    provider: str
    verifier_agent: str
    pass_number: int

    def __post_init__(self) -> None:
        for field_name in ("fingerprint", "head_sha", "verifier_agent"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ReviewModelError(f"unsupported verifier provider: {self.provider}")
        if self.pass_number not in {1, 2}:
            raise ReviewModelError("pass_number must be 1 or 2")

    @property
    def key(self) -> tuple[str, str, str, int]:
        return self.fingerprint, self.provider, self.verifier_agent, self.pass_number

    def to_record(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "head_sha": self.head_sha,
            "provider": self.provider,
            "verifier_agent": self.verifier_agent,
            "pass_number": self.pass_number,
        }

    @classmethod
    def from_record(cls, value: Any) -> "VerifierAssignment":
        record = _record(value, "verifier assignment")
        _require_record_fields(
            record,
            "verifier assignment",
            ("fingerprint", "head_sha", "provider", "verifier_agent", "pass_number"),
        )
        return cls(
            fingerprint=record["fingerprint"],
            head_sha=record["head_sha"],
            provider=record["provider"],
            verifier_agent=record["verifier_agent"],
            pass_number=record["pass_number"],
        )


@dataclass(frozen=True, slots=True)
class VerificationShortfall:
    fingerprint: str
    reason: str
    required_passes: int
    assigned_passes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "fingerprint", _required_text(self.fingerprint, "fingerprint")
        )
        if self.reason not in {
            "budget-exhausted",
            "provider-budget-exhausted",
            "independent-verifier-unavailable",
        }:
            raise ReviewModelError(f"unsupported verification shortfall: {self.reason}")
        if (
            isinstance(self.required_passes, bool)
            or not isinstance(self.required_passes, int)
            or not 1 <= self.required_passes <= 2
        ):
            raise ReviewModelError("required_passes must be 1 or 2")
        if (
            isinstance(self.assigned_passes, bool)
            or not isinstance(self.assigned_passes, int)
            or not 0 <= self.assigned_passes < self.required_passes
        ):
            raise ReviewModelError("assigned_passes must be below required_passes")

    def to_record(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "fact_status": "insufficient_evidence",
            "reason": self.reason,
            "required_passes": self.required_passes,
            "assigned_passes": self.assigned_passes,
        }

    @classmethod
    def from_record(cls, value: Any) -> "VerificationShortfall":
        record = _record(value, "verification shortfall")
        _require_record_fields(
            record,
            "verification shortfall",
            ("fingerprint", "fact_status", "reason", "required_passes", "assigned_passes"),
        )
        if record["fact_status"] != "insufficient_evidence":
            raise ReviewModelError("verification shortfall fact_status must be insufficient_evidence")
        return cls(
            fingerprint=record["fingerprint"],
            reason=record["reason"],
            required_passes=record["required_passes"],
            assigned_passes=record["assigned_passes"],
        )


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    assignments: tuple[VerifierAssignment, ...]
    shortfalls: tuple[VerificationShortfall, ...]
    provider_usage: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        assignments = tuple(self.assignments)
        shortfalls = tuple(self.shortfalls)
        if any(not isinstance(item, VerifierAssignment) for item in assignments):
            raise ReviewModelError("assignments must contain VerifierAssignment values")
        if any(not isinstance(item, VerificationShortfall) for item in shortfalls):
            raise ReviewModelError("shortfalls must contain VerificationShortfall values")
        usage: list[tuple[str, int]] = []
        seen: set[str] = set()
        for provider, count in self.provider_usage:
            if provider not in SUPPORTED_PROVIDERS or provider in seen:
                raise ReviewModelError("provider_usage must contain unique supported providers")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ReviewModelError("provider_usage counts must be non-negative integers")
            usage.append((provider, count))
            seen.add(provider)
        usage.sort(key=lambda item: SUPPORTED_PROVIDERS.index(item[0]))
        if {item.provider for item in assignments} - seen:
            raise ReviewModelError("provider_usage must include every assignment provider")
        actual_usage = {
            provider: sum(1 for item in assignments if item.provider == provider)
            for provider, _ in usage
        }
        if any(actual_usage[provider] != count for provider, count in usage):
            raise ReviewModelError("provider_usage must match verifier assignments")
        object.__setattr__(self, "assignments", assignments)
        object.__setattr__(self, "shortfalls", shortfalls)
        object.__setattr__(self, "provider_usage", tuple(usage))

    @property
    def insufficient_fingerprints(self) -> tuple[str, ...]:
        return tuple(shortfall.fingerprint for shortfall in self.shortfalls)

    @property
    def budget_exhausted(self) -> bool:
        return any("budget-exhausted" in shortfall.reason for shortfall in self.shortfalls)

    def assignments_for(self, fingerprint: str) -> tuple[VerifierAssignment, ...]:
        return tuple(
            assignment
            for assignment in self.assignments
            if assignment.fingerprint == fingerprint
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "assignments": [assignment.to_record() for assignment in self.assignments],
            "shortfalls": [shortfall.to_record() for shortfall in self.shortfalls],
            "provider_usage": dict(self.provider_usage),
            "budget_exhausted": self.budget_exhausted,
        }

    @classmethod
    def from_record(cls, value: Any) -> "VerificationPlan":
        record = _record(value, "verification plan")
        _require_record_fields(
            record,
            "verification plan",
            ("assignments", "shortfalls", "provider_usage", "budget_exhausted"),
        )
        usage = _record(record["provider_usage"], "verification provider_usage")
        unknown_usage = set(usage) - set(SUPPORTED_PROVIDERS)
        if unknown_usage:
            raise ReviewModelError(
                "verification provider_usage contains unsupported providers: "
                + ", ".join(sorted(unknown_usage))
            )
        plan = cls(
            assignments=tuple(
                VerifierAssignment.from_record(item)
                for item in _record_items(record["assignments"], "verification assignments")
            ),
            shortfalls=tuple(
                VerificationShortfall.from_record(item)
                for item in _record_items(record["shortfalls"], "verification shortfalls")
            ),
            provider_usage=tuple(
                (provider, usage[provider])
                for provider in SUPPORTED_PROVIDERS
                if provider in usage
            ),
        )
        if record["budget_exhausted"] is not plan.budget_exhausted:
            raise ReviewModelError("verification budget_exhausted does not match shortfalls")
        return plan


def plan_verification(
    group: ReviewGroupPlan,
    findings: Sequence[NormalizedFinding],
) -> VerificationPlan:
    """Allocate one or two independent passes without exceeding group bounds."""

    ordered = tuple(sorted(findings, key=lambda item: item.fingerprint))
    usage = {provider: 0 for provider in group.providers}
    assignments: list[VerifierAssignment] = []
    shortfalls: list[VerificationShortfall] = []

    for finding in ordered:
        if finding.head_sha != group.head_sha:
            raise ReviewModelError("verification findings must target the review group head_sha")
        if not set(finding.providers).issubset(group.providers):
            raise ReviewModelError("finding provider is outside the review group")

        critical = finding.severity in CRITICAL_SEVERITIES or finding.requires_spec_decision
        required_passes = 2 if critical else 1
        if critical and group.mode in DUAL_MODES:
            desired_providers = group.providers
        else:
            first_provider = next(
                provider for provider in group.providers if provider in finding.providers
            )
            desired_providers = (first_provider,) * required_passes

        assigned_agents: set[str] = set()
        discoverers = set(finding.discovering_agents)
        assigned_before = len(assignments)
        reason = ""
        for pass_number, verifier_provider in enumerate(desired_providers, start=1):
            if len(assignments) >= group.budget.max_verifications:
                reason = "budget-exhausted"
                break
            if usage[verifier_provider] >= group.budget.limit_for(verifier_provider):
                reason = "provider-budget-exhausted"
                break

            pool = group.provider_plan(verifier_provider).verifier_agents
            available = tuple(
                agent
                for agent in pool
                if agent not in discoverers and agent not in assigned_agents
            )
            if not available:
                reason = "independent-verifier-unavailable"
                break
            verifier_agent = available[usage[verifier_provider] % len(available)]
            assignments.append(
                VerifierAssignment(
                    fingerprint=finding.fingerprint,
                    head_sha=finding.head_sha,
                    provider=verifier_provider,
                    verifier_agent=verifier_agent,
                    pass_number=pass_number,
                )
            )
            assigned_agents.add(verifier_agent)
            usage[verifier_provider] += 1

        assigned_count = len(assignments) - assigned_before
        if assigned_count < required_passes:
            shortfalls.append(
                VerificationShortfall(
                    fingerprint=finding.fingerprint,
                    reason=reason or "independent-verifier-unavailable",
                    required_passes=required_passes,
                    assigned_passes=assigned_count,
                )
            )

    return VerificationPlan(
        assignments=tuple(assignments),
        shortfalls=tuple(shortfalls),
        provider_usage=tuple((provider, usage[provider]) for provider in group.providers),
    )


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    fingerprint: str
    head_sha: str
    provider: str
    verifier_agent: str
    pass_number: int
    fact_status: str
    ignore_status: str
    decision_status: str
    progress_without_decision: str
    severity: str
    recommended_action: str

    def __post_init__(self) -> None:
        assignment = VerifierAssignment(
            fingerprint=self.fingerprint,
            head_sha=self.head_sha,
            provider=self.provider,
            verifier_agent=self.verifier_agent,
            pass_number=self.pass_number,
        )
        object.__setattr__(self, "fingerprint", assignment.fingerprint)
        object.__setattr__(self, "head_sha", assignment.head_sha)
        object.__setattr__(self, "verifier_agent", assignment.verifier_agent)
        if self.fact_status not in FACT_STATUSES:
            raise ReviewModelError(f"unsupported fact_status: {self.fact_status}")
        if self.ignore_status not in IGNORE_STATUSES:
            raise ReviewModelError(f"unsupported ignore_status: {self.ignore_status}")
        if self.decision_status not in DECISION_STATUSES:
            raise ReviewModelError(f"unsupported decision_status: {self.decision_status}")
        if self.progress_without_decision not in PROGRESS_STATUSES:
            raise ReviewModelError(
                f"unsupported progress_without_decision: {self.progress_without_decision}"
            )
        if self.severity not in SEVERITIES:
            raise ReviewModelError(f"unsupported verification severity: {self.severity}")
        if self.recommended_action not in RECOMMENDED_ACTIONS:
            raise ReviewModelError(
                f"unsupported recommended_action: {self.recommended_action}"
            )

    @classmethod
    def from_assignment(
        cls,
        assignment: VerifierAssignment,
        *,
        fact_status: str,
        ignore_status: str,
        decision_status: str,
        progress_without_decision: str,
        severity: str,
        recommended_action: str,
    ) -> "VerificationRecord":
        return cls(
            fingerprint=assignment.fingerprint,
            head_sha=assignment.head_sha,
            provider=assignment.provider,
            verifier_agent=assignment.verifier_agent,
            pass_number=assignment.pass_number,
            fact_status=fact_status,
            ignore_status=ignore_status,
            decision_status=decision_status,
            progress_without_decision=progress_without_decision,
            severity=severity,
            recommended_action=recommended_action,
        )

    @property
    def assignment_key(self) -> tuple[str, str, str, int]:
        return self.fingerprint, self.provider, self.verifier_agent, self.pass_number

    def to_record(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "head_sha": self.head_sha,
            "provider": self.provider,
            "verifier_agent": self.verifier_agent,
            "pass_number": self.pass_number,
            "fact_status": self.fact_status,
            "ignore_status": self.ignore_status,
            "decision_status": self.decision_status,
            "progress_without_decision": self.progress_without_decision,
            "severity": self.severity,
            "recommended_action": self.recommended_action,
        }

    @classmethod
    def from_record(cls, value: Any) -> "VerificationRecord":
        record = _record(value, "verification record")
        _require_record_fields(
            record,
            "verification record",
            (
                "fingerprint",
                "head_sha",
                "provider",
                "verifier_agent",
                "pass_number",
                "fact_status",
                "ignore_status",
                "decision_status",
                "progress_without_decision",
                "severity",
                "recommended_action",
            ),
        )
        return cls(**{field: record[field] for field in (
            "fingerprint",
            "head_sha",
            "provider",
            "verifier_agent",
            "pass_number",
            "fact_status",
            "ignore_status",
            "decision_status",
            "progress_without_decision",
            "severity",
            "recommended_action",
        )})


@dataclass(frozen=True, slots=True)
class ManifestCompleteness:
    complete: bool
    issues: tuple[str, ...]
    missing_lanes: tuple[str, ...]
    incomplete_findings: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "issues": list(self.issues),
            "missing_lanes": list(self.missing_lanes),
            "incomplete_findings": list(self.incomplete_findings),
        }

    @classmethod
    def from_record(cls, value: Any) -> "ManifestCompleteness":
        record = _record(value, "manifest completeness")
        _require_record_fields(
            record,
            "manifest completeness",
            ("complete", "issues", "missing_lanes", "incomplete_findings"),
        )
        if not isinstance(record["complete"], bool):
            raise ReviewModelError("manifest completeness.complete must be boolean")
        return cls(
            complete=record["complete"],
            issues=_text_tuple(record["issues"], "manifest issues"),
            missing_lanes=_text_tuple(record["missing_lanes"], "manifest missing_lanes"),
            incomplete_findings=_text_tuple(
                record["incomplete_findings"], "manifest incomplete_findings"
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewManifest:
    review_id: str
    plan: ReviewGroupPlan
    lane_results: tuple[DiscoveryLaneResult, ...]
    findings: tuple[NormalizedFinding, ...]
    verification_plan: VerificationPlan
    verifications: tuple[VerificationRecord, ...]

    def __post_init__(self) -> None:
        if not _REVIEW_ID_RE.fullmatch(self.review_id):
            raise ReviewModelError("review_id must match R001")
        object.__setattr__(self, "lane_results", tuple(self.lane_results))
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "verifications", tuple(self.verifications))

    @property
    def completeness(self) -> ManifestCompleteness:
        return check_manifest_completeness(self)

    def to_record(self) -> dict[str, Any]:
        provider_records: list[dict[str, Any]] = []
        for provider_plan in self.plan.provider_plans:
            record = provider_plan.to_record()
            record["lanes"] = [
                lane.to_record()
                for lane in self.lane_results
                if lane.provider == provider_plan.provider
            ]
            provider_records.append(record)
        return {
            "review_id": self.review_id,
            "mode": self.plan.mode,
            "head_sha": self.plan.head_sha,
            "budget": self.plan.budget.to_record(),
            "providers": provider_records,
            "findings": [finding.to_record() for finding in self.findings],
            "verification": {
                **self.verification_plan.to_record(),
                "records": [record.to_record() for record in self.verifications],
            },
            "completeness": self.completeness.to_record(),
        }

    @property
    def confirmed_fingerprints(self) -> tuple[str, ...]:
        by_fingerprint: dict[str, list[VerificationRecord]] = {}
        for record in self.verifications:
            by_fingerprint.setdefault(record.fingerprint, []).append(record)
        incomplete = set(self.completeness.incomplete_findings)
        return tuple(
            finding.fingerprint
            for finding in self.findings
            if finding.fingerprint not in incomplete
            if by_fingerprint.get(finding.fingerprint)
            and all(
                record.fact_status == "confirmed"
                for record in by_fingerprint[finding.fingerprint]
            )
        )

    def _verified_fingerprints_for_policy(
        self, *, blocking_only: bool = False, allow_legacy: bool = False
    ) -> tuple[str, ...]:
        """Return fully verified findings classified by the new policy axes.

        Legacy manifests do not carry the policy axes and are intentionally
        excluded by default; a migrated legacy scope may explicitly request
        the pre-0.5.2 recommendation fallback for those records.  This keeps
        the compatibility path explicit rather than silently mixing old and
        new semantics.
        """

        incomplete = set(self.completeness.incomplete_findings)
        by_fingerprint: dict[str, list[VerificationRecord]] = {}
        for record in self.verifications:
            by_fingerprint.setdefault(record.fingerprint, []).append(record)
        result: list[str] = []
        for finding in self.findings:
            if not finding.policy_axes_explicit and not allow_legacy:
                continue
            if finding.fingerprint in incomplete:
                continue
            records = by_fingerprint.get(finding.fingerprint, [])
            if not records or any(record.fact_status != "confirmed" for record in records):
                continue
            if finding.policy_axes_explicit:
                if blocking_only:
                    eligible = finding.is_release_blocking
                else:
                    eligible = finding.is_actionable
            else:
                eligible = any(
                    record.recommended_action in {"fix_task", "ask_user"}
                    and record.ignore_status == "must_not_ignore"
                    for record in records
                )
                if blocking_only:
                    eligible = eligible and finding.severity in CRITICAL_SEVERITIES
            if eligible:
                result.append(finding.fingerprint)
        return tuple(sorted(set(result)))

    @property
    def verified_actionable_fingerprints(self) -> tuple[str, ...]:
        return self._verified_fingerprints_for_policy()

    @property
    def verified_release_blocking_fingerprints(self) -> tuple[str, ...]:
        return self._verified_fingerprints_for_policy(blocking_only=True)

    def verified_actionable_fingerprints_for_scope(
        self, *, allow_legacy: bool = False
    ) -> tuple[str, ...]:
        """Return verified actionable findings for an explicit scope mode."""

        return self._verified_fingerprints_for_policy(allow_legacy=allow_legacy)

    def verified_release_blocking_fingerprints_for_scope(
        self, *, allow_legacy: bool = False
    ) -> tuple[str, ...]:
        """Return verified release-blocking findings for an explicit scope mode."""

        return self._verified_fingerprints_for_policy(
            blocking_only=True, allow_legacy=allow_legacy
        )

    @classmethod
    def from_record(cls, value: Any) -> "ReviewManifest":
        record = _record(value, "review manifest")
        _require_record_fields(
            record,
            "review manifest",
            (
                "review_id",
                "mode",
                "head_sha",
                "budget",
                "providers",
                "findings",
                "verification",
                "completeness",
            ),
        )
        provider_records = _record_items(record["providers"], "manifest providers")
        plan = ReviewGroupPlan.from_record(record)
        lane_results = tuple(
            DiscoveryLaneResult.from_record(item)
            for provider in provider_records
            for item in _record_items(
                _record(provider, "manifest provider").get("lanes"),
                "manifest provider lanes",
            )
        )
        verification_record = _record(record["verification"], "manifest verification")
        _require_record_fields(verification_record, "manifest verification", ("records",))
        manifest = cls(
            review_id=record["review_id"],
            plan=plan,
            lane_results=lane_results,
            findings=tuple(
                NormalizedFinding.from_record(item)
                for item in _record_items(record["findings"], "manifest findings")
            ),
            verification_plan=VerificationPlan.from_record(verification_record),
            verifications=tuple(
                VerificationRecord.from_record(item)
                for item in _record_items(
                    verification_record["records"], "manifest verification records"
                )
            ),
        )
        declared_completeness = ManifestCompleteness.from_record(record["completeness"])
        if declared_completeness != manifest.completeness:
            raise ReviewModelError(
                "manifest completeness does not match deserialized lane and verification data"
            )
        return manifest


def validate_manifest_policy(
    manifest: ReviewManifest,
    *,
    allow_legacy: bool = False,
    accepted_risk_authorizations: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Validate every finding's disposition before a review gate mutates state.

    New-policy manifests must carry every independent policy axis.  Only a
    migrated ``legacy-unlocked`` scope may use the old verification
    recommendation fallback.  Explicit findings are always checked with the
    shared :class:`FindingDisposition` invariants, including incomplete
    findings that will not contribute to the verified count.
    """

    if not isinstance(manifest, ReviewManifest):
        raise ReviewModelError("manifest must be a ReviewManifest")
    issues: list[str] = []
    for finding in manifest.findings:
        if not finding.policy_axes_explicit:
            if not allow_legacy:
                issues.append(
                    "review finding "
                    f"{finding.fingerprint} requires explicit policy axes in a fresh scope"
                )
            continue
        if not allow_legacy and any(
            not candidate.policy_axes_explicit for candidate in finding.candidates
        ):
            issues.append(
                "review finding "
                f"{finding.fingerprint} has candidate records without explicit policy axes"
            )
        try:
            authorization = None
            if finding.disposition == "accepted_risk":
                authorization = (
                    accepted_risk_authorizations or {}
                ).get(finding.fingerprint)
                if authorization is None:
                    issues.append(
                        "review finding "
                        f"{finding.fingerprint} requires a finding-linked accepted-risk decision"
                    )
                else:
                    resolved = getattr(authorization, "authorization", authorization)
                    if isinstance(resolved, Mapping):
                        auth_fingerprint = str(
                            resolved.get(
                                "finding_fingerprint", resolved.get("fingerprint", "")
                            )
                        )
                        auth_target = str(
                            resolved.get("target_sha", resolved.get("head_sha", ""))
                        )
                        auth_status = str(
                            getattr(authorization, "status", "")
                            or resolved.get("status", "")
                        )
                    else:
                        auth_fingerprint = str(
                            getattr(resolved, "finding_fingerprint", "")
                        )
                        auth_target = str(getattr(resolved, "target_sha", ""))
                        auth_status = str(getattr(authorization, "status", ""))
                    if auth_fingerprint != finding.fingerprint:
                        issues.append(
                            f"review finding {finding.fingerprint} accepted-risk decision links a different finding"
                        )
                    if auth_target != finding.head_sha:
                        issues.append(
                            f"review finding {finding.fingerprint} accepted-risk decision targets a different SHA"
                        )
                    if auth_status != "accepted":
                        issues.append(
                            f"review finding {finding.fingerprint} accepted-risk decision is not accepted"
                        )
                    decision_id = str(
                        getattr(authorization, "decision_id", "")
                        or (authorization.get("decision_id", "") if isinstance(authorization, Mapping) else "")
                    )
                    if finding.accepted_risk_decision_id and decision_id != finding.accepted_risk_decision_id:
                        issues.append(
                            f"review finding {finding.fingerprint} accepted-risk decision id does not match"
                        )
            disposition = hloop_review_policy.FindingDisposition(
                fact_status=finding.fact_status,
                origin=finding.origin,
                contract_relation=finding.contract_relation,
                decision_requirement=finding.decision_requirement,
                severity=finding.severity,
                disposition=finding.disposition,
                release_effect=finding.release_effect,
                finding_id=finding.fingerprint,
                fingerprint=finding.fingerprint,
                target_sha=finding.head_sha,
                requirement_refs=finding.requirement_refs,
                why_fix_now=finding.why_fix_now,
                accepted_risk_decision_id=finding.accepted_risk_decision_id,
            )
            safety_critical = any(
                hloop_review_policy.is_safety_critical_finding(
                    severity=candidate.severity,
                    title=candidate.title,
                    trigger=candidate.trigger,
                    product_impact=candidate.product_impact,
                    proposed_fix=candidate.proposed_fix,
                )
                for candidate in finding.candidates
            )
            hloop_review_policy.validate_disposition(
                disposition,
                safety_critical=safety_critical,
                accepted_risk_authorized=(
                    finding.disposition == "accepted_risk"
                    and authorization is not None
                ),
            )
        except hloop_review_policy.ReviewPolicyError as exc:
            issues.append(
                f"review finding {finding.fingerprint} violates disposition policy: {exc}"
            )
    return tuple(sorted(set(issues)))


def review_manifest_policy_counts(
    manifest: ReviewManifest,
    *,
    allow_legacy: bool = False,
    accepted_risk_authorizations: Mapping[str, Any] | None = None,
) -> tuple[int, int]:
    """Return ``(actionable, release_blocking)`` after policy validation."""

    issues = validate_manifest_policy(
        manifest,
        allow_legacy=allow_legacy,
        accepted_risk_authorizations=accepted_risk_authorizations,
    )
    if issues:
        raise ReviewModelError("; ".join(issues))
    return (
        len(manifest.verified_actionable_fingerprints_for_scope(allow_legacy=allow_legacy)),
        len(
            manifest.verified_release_blocking_fingerprints_for_scope(
                allow_legacy=allow_legacy
            )
        ),
    )


def check_manifest_completeness(manifest: ReviewManifest) -> ManifestCompleteness:
    """Return a fail-closed gate result for missing lanes or verification."""

    issues: set[str] = set()
    missing_lanes: set[str] = set()
    incomplete_findings: set[str] = set()

    expected_lanes = {
        (lane.provider, lane.lane_id): lane for lane in manifest.plan.expected_lanes
    }
    observed_lanes: dict[tuple[str, str], DiscoveryLaneResult] = {}
    for result in manifest.lane_results:
        if result.key in observed_lanes:
            issues.add(f"duplicate-lane:{result.provider}:{result.lane_id}")
        observed_lanes[result.key] = result
    for key, lane in expected_lanes.items():
        result = observed_lanes.get(key)
        if result is None:
            label = f"{lane.provider}:{lane.lane_id}"
            missing_lanes.add(label)
            issues.add(f"missing-lane:{label}")
        elif result.status != "completed":
            issues.add(f"incomplete-lane:{result.provider}:{result.lane_id}:{result.status}")
        elif (
            result.purpose != lane.purpose
            or result.agent_label != lane.agent_label
        ):
            issues.add(f"lane-contract-mismatch:{result.provider}:{result.lane_id}")
    for provider, lane_id in set(observed_lanes) - set(expected_lanes):
        issues.add(f"unexpected-lane:{provider}:{lane_id}")

    finding_by_fingerprint: dict[str, NormalizedFinding] = {}
    for finding in manifest.findings:
        if finding.fingerprint in finding_by_fingerprint:
            issues.add(f"duplicate-normalized-finding:{finding.fingerprint}")
        finding_by_fingerprint[finding.fingerprint] = finding
        if finding.head_sha != manifest.plan.head_sha:
            issues.add(f"finding-head-mismatch:{finding.fingerprint}")
            incomplete_findings.add(finding.fingerprint)
        if not set(finding.providers).issubset(manifest.plan.providers):
            issues.add(f"finding-provider-mismatch:{finding.fingerprint}")
            incomplete_findings.add(finding.fingerprint)

    planned_discoverers = {
        (lane.provider, lane.agent_label): lane
        for lane in manifest.plan.expected_lanes
    }
    observed_finding_counts: dict[tuple[str, str], int] = {
        key: 0 for key in planned_discoverers
    }
    for finding in manifest.findings:
        for candidate in finding.candidates:
            key = (candidate.provider, candidate.discovering_agent)
            if key not in planned_discoverers:
                issues.add(
                    f"discoverer-outside-plan:{candidate.provider}:"
                    f"{candidate.discovering_agent}:{finding.fingerprint}"
                )
                incomplete_findings.add(finding.fingerprint)
            else:
                observed_finding_counts[key] += 1
    for result in manifest.lane_results:
        key = (result.provider, result.agent_label)
        if key in observed_finding_counts and result.finding_count != observed_finding_counts[key]:
            issues.add(f"lane-finding-count-mismatch:{result.provider}:{result.lane_id}")

    expected_assignments: dict[tuple[str, str, str, int], VerifierAssignment] = {}
    for assignment in manifest.verification_plan.assignments:
        if assignment.key in expected_assignments:
            issues.add(f"duplicate-verifier-assignment:{assignment.fingerprint}")
        expected_assignments[assignment.key] = assignment
        if assignment.fingerprint not in finding_by_fingerprint:
            issues.add(f"assignment-for-unknown-finding:{assignment.fingerprint}")
        if assignment.head_sha != manifest.plan.head_sha:
            issues.add(f"assignment-head-mismatch:{assignment.fingerprint}")
        if assignment.provider not in manifest.plan.providers:
            issues.add(f"assignment-provider-mismatch:{assignment.fingerprint}")
            incomplete_findings.add(assignment.fingerprint)
        elif assignment.verifier_agent not in manifest.plan.provider_plan(
            assignment.provider
        ).verifier_agents:
            issues.add(f"assignment-verifier-outside-plan:{assignment.fingerprint}")
            incomplete_findings.add(assignment.fingerprint)

    observed_records: dict[tuple[str, str, str, int], VerificationRecord] = {}
    records_by_fingerprint: dict[str, list[VerificationRecord]] = {}
    for record in manifest.verifications:
        if record.assignment_key in observed_records:
            issues.add(f"duplicate-verification-record:{record.fingerprint}")
        observed_records[record.assignment_key] = record
        records_by_fingerprint.setdefault(record.fingerprint, []).append(record)
        if record.assignment_key not in expected_assignments:
            issues.add(f"unexpected-verification-record:{record.fingerprint}")
            incomplete_findings.add(record.fingerprint)
        if record.head_sha != manifest.plan.head_sha:
            issues.add(f"verification-head-mismatch:{record.fingerprint}")
            incomplete_findings.add(record.fingerprint)
        finding = finding_by_fingerprint.get(record.fingerprint)
        if finding is not None and record.severity != finding.severity:
            issues.add(f"verification-severity-mismatch:{record.fingerprint}")
            incomplete_findings.add(record.fingerprint)

    for key, assignment in expected_assignments.items():
        if key not in observed_records:
            issues.add(
                f"missing-verification-record:{assignment.fingerprint}:"
                f"pass-{assignment.pass_number}"
            )
            incomplete_findings.add(assignment.fingerprint)

    for shortfall in manifest.verification_plan.shortfalls:
        issues.add(f"verification-shortfall:{shortfall.fingerprint}:{shortfall.reason}")
        incomplete_findings.add(shortfall.fingerprint)

    for fingerprint, finding in finding_by_fingerprint.items():
        records = records_by_fingerprint.get(fingerprint, [])
        critical = finding.severity in CRITICAL_SEVERITIES or finding.requires_spec_decision
        required_passes = 2 if critical else 1
        discoverers = set(finding.discovering_agents)
        independent_records = [
            record for record in records if record.verifier_agent not in discoverers
        ]
        if len(independent_records) != len(records):
            issues.add(f"verifier-not-independent:{fingerprint}")
            incomplete_findings.add(fingerprint)
        if len(independent_records) < required_passes:
            issues.add(f"insufficient-verification-passes:{fingerprint}")
            incomplete_findings.add(fingerprint)
        if len({record.verifier_agent for record in independent_records}) < required_passes:
            issues.add(f"duplicate-verifier-for-two-pass:{fingerprint}")
            incomplete_findings.add(fingerprint)
        expected_pass_numbers = set(range(1, required_passes + 1))
        if not expected_pass_numbers.issubset(
            {record.pass_number for record in independent_records}
        ):
            issues.add(f"missing-verification-pass-number:{fingerprint}")
            incomplete_findings.add(fingerprint)
        if critical and manifest.plan.mode in DUAL_MODES:
            if not set(manifest.plan.providers).issubset(
                {record.provider for record in independent_records}
            ):
                issues.add(f"missing-cross-model-verifier:{fingerprint}")
                incomplete_findings.add(fingerprint)
        if any(record.fact_status == "insufficient_evidence" for record in records):
            issues.add(f"finding-insufficient-evidence:{fingerprint}")
            incomplete_findings.add(fingerprint)
        conclusive = {
            record.fact_status
            for record in records
            if record.fact_status != "insufficient_evidence"
        }
        if len(conclusive) > 1:
            issues.add(f"verification-disagreement:{fingerprint}")
            incomplete_findings.add(fingerprint)

    return ManifestCompleteness(
        complete=not issues,
        issues=tuple(sorted(issues)),
        missing_lanes=tuple(sorted(missing_lanes)),
        incomplete_findings=tuple(sorted(incomplete_findings)),
    )
