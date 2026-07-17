"""Fail-closed config projections for herdr-dev-loop validation reuse.

The 0.5.2 runtime hashes the entire resolved config into validation evidence.
That unnecessarily invalidates product validation when only audit routing
(model, effort, protocol, or lane topology) changes.  This module owns the
versioned, exhaustive classification registry used by the 0.5.3 runtime to
separate validation and audit identities.

Unknown schema revisions, registry-version mismatches, incomplete registries,
and unclassified resolved leaves never become an implicit allow-list.  The
projection retains those leaves and marks the evidence stale.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from . import config as hloop_config

VALIDATION_AFFECTING = "validation-affecting"
AUDIT_ONLY = "audit-only"
CLASSIFICATION_REGISTRY_VERSION = 1
# The registry is a 3.3 state contract.  Legacy 3.2 evidence is migrated by
# the runtime before projection; accepting it here would make an unconverted
# resolved-config shape look fresh.
SUPPORTED_SCHEMA_REVISIONS = frozenset({3})
# Compatibility spelling for early 0.5.3 callers.
SUPPORTED_CONFIG_SCHEMA_REVISIONS = SUPPORTED_SCHEMA_REVISIONS

# The current hierarchical schema contains orchestration and audit routing
# only.  Target SHA, ordered commands, dependencies, toolchain, and setup
# command are bound separately by the runtime validation identity.  Therefore
# no current config leaf is product/test-affecting; keeping this explicit empty
# set prevents role routing from invalidating otherwise fresh L3 evidence.
VALIDATION_AFFECTING_CONFIG_LEAVES = frozenset()
# Keep this registry independent from the schema-derived leaf set.  Adding a
# schema-valid key must create ``unclassified-schema-leaf`` until a maintainer
# makes an explicit validation-versus-audit decision.
AUDIT_ONLY_CONFIG_LEAVES = frozenset(
    {
        "advisor.effort",
        "advisor.model",
        "advisor.provider",
        "audit.agent_budget",
        "audit.max_patch_review_rounds_per_task",
        "final_coordinator.effort",
        "final_coordinator.model",
        "final_coordinator.provider",
        "gap.coordinator.effort",
        "gap.coordinator.model",
        "gap.coordinator.provider",
        "gap.effort",
        "gap.lane.effort",
        "gap.lane.model",
        "gap.lane.provider",
        "gap.lane_count",
        "gap.mode",
        "gap.model",
        "gap.provider",
        "gap.verifier.effort",
        "gap.verifier.model",
        "gap.verifier.provider",
        "manager.effort",
        "manager.identity_policy",
        "manager.model",
        "manager.provider",
        "max_workers",
        "patch_reviewer.effort",
        "patch_reviewer.model",
        "patch_reviewer.provider",
        "plan_gap.effort",
        "plan_gap.model",
        "plan_gap.provider",
        "review.cadence",
        "review.final_required",
        "review.manual_final_execution",
        "review.manual_final_protocol",
        "review.max_fix_rounds",
        "review.pre_final_protocol",
        "review.scope_expansion_action",
        "reviewer.coordinator.effort",
        "reviewer.coordinator.model",
        "reviewer.coordinator.provider",
        "reviewer.effort",
        "reviewer.lane.effort",
        "reviewer.lane.model",
        "reviewer.lane.provider",
        "reviewer.lane_count",
        "reviewer.mode",
        "reviewer.model",
        "reviewer.protocol",
        "reviewer.provider",
        "reviewer.providers",
        "reviewer.required_capabilities",
        "reviewer.verifier.effort",
        "reviewer.verifier.model",
        "reviewer.verifier.provider",
        "session_cleanup",
        "specification_scout",
        "worker.effort",
        "worker.model",
        "worker.provider",
    }
)

CONFIG_LEAF_CLASSIFICATION_REGISTRY = MappingProxyType(
    {
        **{path: VALIDATION_AFFECTING for path in VALIDATION_AFFECTING_CONFIG_LEAVES},
        **{path: AUDIT_ONLY for path in AUDIT_ONLY_CONFIG_LEAVES},
    }
)


class ConfigIdentityError(ValueError):
    """Raised when an identity input cannot be represented deterministically."""


def flatten_config_leaves(
    value: Mapping[str, Any], prefix: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Return dotted leaf paths while treating sequences as one config leaf."""

    if not isinstance(value, Mapping):
        raise ConfigIdentityError("resolved config must be a mapping")
    leaves: dict[str, Any] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ConfigIdentityError("resolved config keys must be non-empty strings")
        path = (*prefix, raw_key)
        if isinstance(item, Mapping):
            leaves.update(flatten_config_leaves(item, path))
        else:
            leaves[".".join(path)] = item
    return leaves


def _nested_config(leaves: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dotted_path, value in sorted(leaves.items()):
        cursor = result
        parts = dotted_path.split(".")
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ConfigIdentityError(
                    f"config leaf path collides with a parent leaf: {dotted_path}"
                )
            cursor = child
        cursor[parts[-1]] = value
    return result


def registry_issues(
    *,
    schema_leaves: Iterable[str] = hloop_config.CANONICAL_CONFIG_LEAF_PATHS,
    validation_affecting: Iterable[str] = VALIDATION_AFFECTING_CONFIG_LEAVES,
    audit_only: Iterable[str] = AUDIT_ONLY_CONFIG_LEAVES,
) -> tuple[str, ...]:
    """Return deterministic exact-once classification errors."""

    schema = frozenset(schema_leaves)
    validation = frozenset(validation_affecting)
    audit = frozenset(audit_only)
    issues: list[str] = []
    issues.extend(f"multiply-classified:{path}" for path in sorted(validation & audit))
    issues.extend(
        f"unclassified-schema-leaf:{path}"
        for path in sorted(schema - validation - audit)
    )
    issues.extend(
        f"classification-outside-schema:{path}"
        for path in sorted((validation | audit) - schema)
    )
    return tuple(issues)


def ensure_registry_complete() -> None:
    issues = registry_issues()
    if issues:
        raise ConfigIdentityError("invalid config classification registry: " + "; ".join(issues))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigIdentityError(f"config identity is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclasses.dataclass(frozen=True)
class ConfigIdentityProjection:
    """Resolved config split into validation, audit, and fail-closed leaves."""

    schema_revision: Any
    registry_version: Any
    validation_config: Mapping[str, Any]
    audit_config: Mapping[str, Any]
    unclassified_config: Mapping[str, Any]
    stale_reasons: tuple[str, ...]
    validation_digest: str
    audit_digest: str

    @property
    def stale(self) -> bool:
        return bool(self.stale_reasons)

    @property
    def reusable(self) -> bool:
        return not self.stale

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_revision": self.schema_revision,
            "registry_version": self.registry_version,
            "classification_registry_version": CLASSIFICATION_REGISTRY_VERSION,
            "validation_config": dict(self.validation_config),
            "audit_config": dict(self.audit_config),
            "unclassified_config": dict(self.unclassified_config),
            "stale": self.stale,
            "stale_reasons": list(self.stale_reasons),
            "validation_digest": self.validation_digest,
            "audit_digest": self.audit_digest,
        }


def project_config_identities(
    resolved_config: Mapping[str, Any],
    *,
    schema_revision: Any | None = None,
    config_schema_revision: Any | None = None,
    registry_version: Any = CLASSIFICATION_REGISTRY_VERSION,
) -> ConfigIdentityProjection:
    """Build validation/audit projections and stale unsafe inputs.

    The digests include the schema and registry identity.  Unclassified leaves
    are retained in both payloads, but ``stale`` is the authoritative reuse
    gate: callers must never reuse evidence when it is true.
    """

    if schema_revision is None:
        schema_revision = config_schema_revision
    elif config_schema_revision is not None and config_schema_revision != schema_revision:
        raise ConfigIdentityError(
            "schema_revision and config_schema_revision must match when both are supplied"
        )
    if schema_revision is None:
        raise ConfigIdentityError("schema_revision is required")

    leaves = flatten_config_leaves(resolved_config)
    stale_reasons = list(registry_issues())
    if schema_revision not in SUPPORTED_SCHEMA_REVISIONS:
        stale_reasons.append(f"unknown-schema-revision:{schema_revision!r}")
    if registry_version != CLASSIFICATION_REGISTRY_VERSION:
        stale_reasons.append(
            "classification-registry-version-mismatch:"
            f"expected={CLASSIFICATION_REGISTRY_VERSION!r}:actual={registry_version!r}"
        )

    validation_leaves: dict[str, Any] = {}
    audit_leaves: dict[str, Any] = {}
    unclassified_leaves: dict[str, Any] = {}
    for path, value in sorted(leaves.items()):
        classification = CONFIG_LEAF_CLASSIFICATION_REGISTRY.get(path)
        if classification == VALIDATION_AFFECTING:
            validation_leaves[path] = value
        elif classification == AUDIT_ONLY:
            audit_leaves[path] = value
        else:
            unclassified_leaves[path] = value
            stale_reasons.append(f"unclassified-config-leaf:{path}")

    validation_config = _nested_config(validation_leaves)
    audit_config = _nested_config(audit_leaves)
    unclassified_config = _nested_config(unclassified_leaves)
    common = {
        "schema_revision": schema_revision,
        "registry_version": registry_version,
        "classification_registry_version": CLASSIFICATION_REGISTRY_VERSION,
        # Retain unknown leaves in the identity rather than silently dropping
        # them, even though stale evidence is already non-reusable.
        "unclassified_config": unclassified_config,
    }
    validation_digest = _canonical_digest(
        {**common, "validation_config": validation_config}
    )
    audit_digest = _canonical_digest({**common, "audit_config": audit_config})
    return ConfigIdentityProjection(
        schema_revision=schema_revision,
        registry_version=registry_version,
        validation_config=validation_config,
        audit_config=audit_config,
        unclassified_config=unclassified_config,
        stale_reasons=tuple(dict.fromkeys(stale_reasons)),
        validation_digest=validation_digest,
        audit_digest=audit_digest,
    )


# Concise spelling for the central runtime integration.
config_identity_projection = project_config_identities
