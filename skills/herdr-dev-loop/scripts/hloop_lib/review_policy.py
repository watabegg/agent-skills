"""Finding disposition policy and stable follow-up issue identities.

The existing :mod:`hloop_lib.review` module models discovery and verification
within one review.  This module models the Manager's cross-review decision:
the facts about a candidate, its relationship to the release contract, and
the action taken are deliberately independent fields.  It also provides the
stable semantic identity used by first-class follow-ups.

No review title, proposed fix, severity, target SHA, or source line is part of
a follow-up issue key.  Those values are evidence that can change as a review
is repeated; the component, trigger class, product impact, and (when known)
root cause are the semantic identity of the issue.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


FACT_STATUSES = frozenset({"confirmed", "refuted", "insufficient_evidence"})
FINDING_ORIGINS = frozenset(
    {
        "introduced",
        "diff-expanded-pre-existing",
        "unrelated-pre-existing",
        "unknown",
    }
)
CONTRACT_RELATIONS = frozenset({"in_scope", "outside_release", "ambiguous"})
DECISION_REQUIREMENTS = frozenset({"none", "spec", "user"})
SEVERITIES = ("P0", "P1", "P2", "P3")
DISPOSITIONS = frozenset(
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
RELEASE_EFFECTS = frozenset({"blocking", "non_blocking"})
RELATION_TYPES = frozenset({"duplicate_of", "supersedes", "alias_of"})

FOLLOW_UP_ISSUE_KEY_VERSION = 1
FOLLOW_UP_ISSUE_KEY_PREFIX = "fu"
_ISSUE_KEY_RE = re.compile(
    rf"^{re.escape(FOLLOW_UP_ISSUE_KEY_PREFIX)}:v(?P<version>[1-9][0-9]*):sha256:(?P<digest>[0-9a-f]{{64}})$"
)
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

BLOCKING_DISPOSITIONS = frozenset(
    {"fix_now", "disable_feature", "mark_experimental", "user_decision"}
)
NON_BLOCKING_DISPOSITIONS = frozenset(
    {"defer_follow_up", "accepted_risk", "discard"}
)
_REGRESSION_ORIGINS = frozenset({"introduced", "diff-expanded-pre-existing"})

# A P0 is safety-critical by definition.  P1 findings are safety-critical
# when their evidence describes a security or data-integrity boundary.  Keep
# this detector deliberately conservative: it is only used to reject an
# unsafe non-blocking disposition at the convergence gate, so a false
# positive leaves the Manager an explicit blocking disposition to resolve.
_SAFETY_CRITICAL_MARKERS = (
    re.compile(r"\bsecurity\b", re.IGNORECASE),
    re.compile(r"\bdata[ -](?:loss|leak(?:age)?|exposure|corruption)\b", re.IGNORECASE),
    re.compile(r"\bcredential(?:s)?\b", re.IGNORECASE),
    re.compile(r"\bauth(?:entication|orization)?\s+bypass\b", re.IGNORECASE),
    re.compile(r"\b(?:privacy|tenant)[ -](?:breach|escape|isolation)\b", re.IGNORECASE),
    re.compile(r"\bcross[ -]tenant\b", re.IGNORECASE),
    re.compile(r"\bsecret(?:s)?\b", re.IGNORECASE),
)


class ReviewPolicyError(ValueError):
    """Raised when a finding classification or follow-up relation is invalid."""


def is_safety_critical_finding(
    *,
    severity: str,
    title: str = "",
    trigger: str = "",
    product_impact: str = "",
    proposed_fix: str = "",
) -> bool:
    """Return whether finding evidence requires the security/data-loss gate.

    The policy model intentionally keeps this as evidence-derived metadata
    rather than adding another serialized classification axis.  P0 is always
    critical; for P1, matching any security or data-integrity marker is enough
    to require a blocking disposition.
    """

    if severity == "P0":
        return True
    if severity != "P1":
        return False
    evidence = " ".join(
        value.strip()
        for value in (title, trigger, product_impact, proposed_fix)
        if isinstance(value, str) and value.strip()
    )
    return any(marker.search(evidence) for marker in _SAFETY_CRITICAL_MARKERS)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewPolicyError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ReviewPolicyError(f"{field_name} must be a string")
    return value.strip()


def _canonical_text(value: Any, field_name: str) -> str:
    """Normalize human-entered identity components deterministically."""

    text = unicodedata.normalize("NFKC", _required_text(value, field_name))
    text = text.replace("\\", "/")
    return " ".join(text.split()).casefold()


def _text_tuple(
    values: Sequence[Any] | None, field_name: str, *, unique: bool = True
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReviewPolicyError(f"{field_name} must be an array of strings")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if unique and len(set(normalized)) != len(normalized):
        raise ReviewPolicyError(f"{field_name} must not contain duplicates")
    return normalized


def _axis(value: Any, field_name: str, allowed: Sequence[str] | set[str]) -> str:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ReviewPolicyError(f"unsupported {field_name}: {value!r}; expected {choices}")
    return value


def _record(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewPolicyError(f"{field_name} must be an object")
    return value


def _issue_key_text(value: Any, field_name: str = "issue_key") -> str:
    key = _required_text(value, field_name)
    if _ISSUE_KEY_RE.fullmatch(key) is None:
        raise ReviewPolicyError(f"{field_name} is not a valid follow-up issue key")
    return key


@dataclass(frozen=True, slots=True)
class FindingDisposition:
    """A finding's independent classification axes and Manager disposition.

    The seven policy axes are the first fields and are always serialized.  The
    remaining fields are evidence/provenance fields used by review artifacts;
    they do not participate in the policy identity.
    """

    fact_status: str
    origin: str
    contract_relation: str
    decision_requirement: str
    severity: str
    disposition: str
    release_effect: str
    finding_id: str = ""
    source_artifact: str = ""
    source_candidate_id: str = ""
    fingerprint: str = ""
    target_sha: str = ""
    requirement_refs: tuple[str, ...] = ()
    why_fix_now: str = ""
    remediation_round: int | None = None
    duplicate_of: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fact_status",
            _axis(self.fact_status, "fact_status", FACT_STATUSES),
        )
        object.__setattr__(self, "origin", _axis(self.origin, "origin", FINDING_ORIGINS))
        object.__setattr__(
            self,
            "contract_relation",
            _axis(self.contract_relation, "contract_relation", CONTRACT_RELATIONS),
        )
        object.__setattr__(
            self,
            "decision_requirement",
            _axis(self.decision_requirement, "decision_requirement", DECISION_REQUIREMENTS),
        )
        object.__setattr__(
            self,
            "severity",
            _axis(self.severity, "severity", set(SEVERITIES)),
        )
        object.__setattr__(
            self,
            "disposition",
            _axis(self.disposition, "disposition", DISPOSITIONS),
        )
        object.__setattr__(
            self,
            "release_effect",
            _axis(self.release_effect, "release_effect", RELEASE_EFFECTS),
        )

        for field_name in (
            "finding_id",
            "source_artifact",
            "source_candidate_id",
            "fingerprint",
            "target_sha",
            "why_fix_now",
            "duplicate_of",
        ):
            object.__setattr__(
                self, field_name, _optional_text(getattr(self, field_name), field_name)
            )
        if self.fingerprint and _FINGERPRINT_RE.fullmatch(self.fingerprint) is None:
            raise ReviewPolicyError("fingerprint must use the sha256:<hex> format")
        object.__setattr__(
            self,
            "requirement_refs",
            _text_tuple(self.requirement_refs, "requirement_refs"),
        )
        if self.remediation_round is not None and (
            isinstance(self.remediation_round, bool)
            or not isinstance(self.remediation_round, int)
            or self.remediation_round < 1
        ):
            raise ReviewPolicyError("remediation_round must be a positive integer or null")

        expected_effect = (
            "blocking"
            if self.disposition in BLOCKING_DISPOSITIONS
            else "non_blocking"
        )
        if self.release_effect != expected_effect:
            raise ReviewPolicyError(
                f"{self.disposition} requires release_effect={expected_effect}"
            )
        # These are hard safety invariants, independent of Manager evidence
        # that may be supplied to ``validate_disposition`` later.
        if self.fact_status == "refuted" and self.disposition != "discard":
            raise ReviewPolicyError("refuted findings must be discarded")
        if (
            self.origin in _REGRESSION_ORIGINS
            and self.contract_relation == "in_scope"
            and self.disposition == "defer_follow_up"
        ):
            raise ReviewPolicyError(
                "introduced or diff-expanded in-scope regressions cannot be deferred as follow-ups"
            )
        if (
            self.origin in _REGRESSION_ORIGINS
            and self.contract_relation == "in_scope"
            and self.severity in {"P0", "P1"}
            and self.disposition not in {"fix_now", "disable_feature", "user_decision"}
        ):
            raise ReviewPolicyError(
                "in-scope P0/P1 regressions require fix_now, disable_feature, or user_decision"
            )

    @property
    def is_blocking(self) -> bool:
        return self.release_effect == "blocking"

    def to_record(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "source_artifact": self.source_artifact,
            "source_candidate_id": self.source_candidate_id,
            "fingerprint": self.fingerprint,
            "target_sha": self.target_sha,
            "fact_status": self.fact_status,
            "severity": self.severity,
            "origin": self.origin,
            "contract_relation": self.contract_relation,
            "decision_requirement": self.decision_requirement,
            "disposition": self.disposition,
            "release_effect": self.release_effect,
            "requirement_refs": list(self.requirement_refs),
            "why_fix_now": self.why_fix_now,
            "remediation_round": self.remediation_round,
            "duplicate_of": self.duplicate_of,
        }

    @classmethod
    def from_record(cls, value: Any) -> "FindingDisposition":
        record = _record(value, "finding disposition")
        required = (
            "fact_status",
            "origin",
            "contract_relation",
            "decision_requirement",
            "severity",
            "disposition",
            "release_effect",
        )
        missing = [field for field in required if field not in record]
        if missing:
            raise ReviewPolicyError(
                "finding disposition is missing required fields: " + ", ".join(missing)
            )
        return cls(
            **{
                field: record[field]
                for field in required
            },
            finding_id=record.get("finding_id", ""),
            source_artifact=record.get("source_artifact", ""),
            source_candidate_id=record.get("source_candidate_id", ""),
            fingerprint=record.get("fingerprint", ""),
            target_sha=record.get("target_sha", record.get("head_sha", "")),
            requirement_refs=record.get("requirement_refs", ()),
            why_fix_now=record.get("why_fix_now", ""),
            remediation_round=record.get("remediation_round"),
            duplicate_of=record.get("duplicate_of", ""),
        )


# Names used by callers can follow either the review artifact terminology or
# the shorter policy terminology.  They intentionally refer to one model.
ReviewDisposition = FindingDisposition
Disposition = FindingDisposition


def validate_disposition(
    value: FindingDisposition | Mapping[str, Any],
    *,
    acceptance_can_be_met_without_decision: bool = False,
    safety_critical: bool = False,
    scope_change_authorized: bool = False,
    accepted_risk_authorized: bool = False,
) -> FindingDisposition:
    """Validate the approved safety rules for a finding disposition.

    ``acceptance_can_be_met_without_decision`` and the authorization flags are
    explicit escape hatches for the Manager's recorded evidence; they default
    to the safe, blocking behavior.  In particular, a current-scope regression
    introduced or expanded by this diff cannot be silently converted to an
    outside-release follow-up.
    """

    disposition = (
        value if isinstance(value, FindingDisposition) else FindingDisposition.from_record(value)
    )
    if not isinstance(acceptance_can_be_met_without_decision, bool):
        raise ReviewPolicyError("acceptance_can_be_met_without_decision must be boolean")
    if not isinstance(safety_critical, bool):
        raise ReviewPolicyError("safety_critical must be boolean")
    if not isinstance(scope_change_authorized, bool):
        raise ReviewPolicyError("scope_change_authorized must be boolean")
    if not isinstance(accepted_risk_authorized, bool):
        raise ReviewPolicyError("accepted_risk_authorized must be boolean")

    if disposition.fact_status == "refuted":
        if disposition.disposition != "discard":
            raise ReviewPolicyError("refuted findings must be discarded")
        return disposition

    if disposition.disposition == "accepted_risk":
        if not accepted_risk_authorized:
            raise ReviewPolicyError("accepted_risk requires an explicit authorization")
        if disposition.fact_status != "confirmed":
            raise ReviewPolicyError("accepted_risk requires a confirmed finding")
        if disposition.decision_requirement != "none":
            raise ReviewPolicyError("accepted_risk cannot bypass a specification or user decision")

    if disposition.disposition == "fix_now" and (
        disposition.contract_relation == "outside_release"
        and not scope_change_authorized
    ):
        raise ReviewPolicyError(
            "outside_release findings cannot create fix_now work without an authorized scope change"
        )

    if (
        disposition.origin in _REGRESSION_ORIGINS
        and disposition.contract_relation == "in_scope"
    ):
        if disposition.disposition == "defer_follow_up":
            raise ReviewPolicyError(
                "introduced or diff-expanded in-scope regressions cannot be deferred as follow-ups"
            )
        if disposition.disposition == "discard":
            raise ReviewPolicyError(
                "confirmed current-scope regressions cannot be discarded"
            )
        if disposition.severity in {"P0", "P1"} and disposition.disposition not in {
            "fix_now",
            "disable_feature",
            "user_decision",
        }:
            raise ReviewPolicyError(
                "in-scope P0/P1 regressions require fix_now, disable_feature, or user_decision"
            )
        if disposition.severity == "P1" and disposition.requirement_refs:
            if disposition.disposition != "fix_now":
                raise ReviewPolicyError(
                    "an in-scope P1 tied to a requirement must use fix_now"
                )

    if disposition.fact_status == "insufficient_evidence":
        if (
            disposition.contract_relation in {"in_scope", "ambiguous"}
            and not acceptance_can_be_met_without_decision
            and disposition.disposition != "user_decision"
        ):
            raise ReviewPolicyError(
                "insufficient evidence for current acceptance or safety requires user_decision"
            )
        if disposition.contract_relation in {"outside_release", "ambiguous"} and (
            disposition.disposition == "defer_follow_up"
            and disposition.release_effect != "non_blocking"
        ):
            raise ReviewPolicyError("insufficient-evidence follow-ups must be non_blocking")

    if (
        disposition.decision_requirement in {"spec", "user"}
        and disposition.contract_relation in {"in_scope", "ambiguous"}
        and not acceptance_can_be_met_without_decision
        and disposition.disposition != "user_decision"
    ):
        raise ReviewPolicyError(
            "a required specification or user decision cannot be bypassed for in-scope work"
        )

    if safety_critical and disposition.severity in {"P0", "P1"}:
        if disposition.disposition not in {
            "fix_now",
            "disable_feature",
            "user_decision",
        }:
            raise ReviewPolicyError(
                "reachable security or data-loss P0/P1 findings cannot be deferred"
            )
        if disposition.release_effect != "blocking":
            raise ReviewPolicyError("unresolved security or data-loss findings are blocking")

    if disposition.fact_status == "confirmed" and disposition.contract_relation == "outside_release":
        allowed = {"defer_follow_up", "discard"}
        if scope_change_authorized:
            allowed.add("fix_now")
        if disposition.disposition not in allowed:
            raise ReviewPolicyError(
                "confirmed outside-release findings may only be deferred or discarded"
            )

    if (
        disposition.disposition == "user_decision"
        and disposition.release_effect != "blocking"
    ):
        raise ReviewPolicyError("user_decision must remain blocking")

    return disposition


# Descriptive aliases make the policy entry point easy to discover without
# creating separate semantics.
enforce_disposition_policy = validate_disposition
validate_finding_policy = validate_disposition


def canonical_follow_up_identity(
    *,
    component: str,
    trigger_class: str,
    product_impact: str,
    root_cause: str | None = None,
    version: int = FOLLOW_UP_ISSUE_KEY_VERSION,
) -> dict[str, Any]:
    """Return the exact canonical payload hashed for a follow-up issue key."""

    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ReviewPolicyError("issue key version must be a positive integer")
    payload: dict[str, Any] = {
        "component": _canonical_text(component, "component"),
        "product_impact": _canonical_text(product_impact, "product_impact"),
        "trigger_class": _canonical_text(trigger_class, "trigger_class"),
        "version": version,
    }
    if root_cause is not None and _optional_text(root_cause, "root_cause"):
        payload["root_cause"] = _canonical_text(root_cause, "root_cause")
    return payload


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class FollowUpIssueKey:
    """A versioned semantic key for a first-class follow-up."""

    component: str
    trigger_class: str
    product_impact: str
    root_cause: str | None = None
    version: int = FOLLOW_UP_ISSUE_KEY_VERSION

    def __post_init__(self) -> None:
        identity = canonical_follow_up_identity(
            component=self.component,
            trigger_class=self.trigger_class,
            product_impact=self.product_impact,
            root_cause=self.root_cause,
            version=self.version,
        )
        object.__setattr__(self, "component", identity["component"])
        object.__setattr__(self, "trigger_class", identity["trigger_class"])
        object.__setattr__(self, "product_impact", identity["product_impact"])
        object.__setattr__(self, "version", identity["version"])
        object.__setattr__(self, "root_cause", identity.get("root_cause"))

    @property
    def provisional(self) -> bool:
        return self.root_cause is None

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return canonical_follow_up_identity(
            component=self.component,
            trigger_class=self.trigger_class,
            product_impact=self.product_impact,
            root_cause=self.root_cause,
            version=self.version,
        )

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload)

    @property
    def key(self) -> str:
        digest = hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()
        return f"{FOLLOW_UP_ISSUE_KEY_PREFIX}:v{self.version}:sha256:{digest}"

    @property
    def issue_key(self) -> str:
        return self.key

    def to_record(self) -> dict[str, Any]:
        return {
            "issue_key": self.key,
            "issue_key_version": self.version,
            "provisional": self.provisional,
            "component": self.component,
            "trigger_class": self.trigger_class,
            "product_impact": self.product_impact,
            "root_cause": self.root_cause,
        }

    @classmethod
    def from_record(cls, value: Any) -> "FollowUpIssueKey":
        record = _record(value, "follow-up issue key")
        required = ("component", "trigger_class", "product_impact")
        missing = [field for field in required if field not in record]
        if missing:
            raise ReviewPolicyError(
                "follow-up issue key is missing required fields: " + ", ".join(missing)
            )
        key = cls(
            component=record["component"],
            trigger_class=record["trigger_class"],
            product_impact=record["product_impact"],
            root_cause=record.get("root_cause"),
            version=record.get("issue_key_version", record.get("version", 1)),
        )
        if "issue_key" in record and record["issue_key"] != key.key:
            raise ReviewPolicyError("follow-up issue_key does not match its semantic identity")
        if "provisional" in record and record["provisional"] is not key.provisional:
            raise ReviewPolicyError("follow-up provisional flag does not match root_cause")
        return key


def build_follow_up_issue_key(
    *,
    component: str,
    trigger_class: str,
    product_impact: str,
    root_cause: str | None = None,
    version: int = FOLLOW_UP_ISSUE_KEY_VERSION,
) -> FollowUpIssueKey:
    return FollowUpIssueKey(
        component=component,
        trigger_class=trigger_class,
        product_impact=product_impact,
        root_cause=root_cause,
        version=version,
    )


def follow_up_issue_key(
    *,
    component: str,
    trigger_class: str,
    product_impact: str,
    root_cause: str | None = None,
    version: int = FOLLOW_UP_ISSUE_KEY_VERSION,
) -> str:
    """Build the stable string key; evidence-only fields are not accepted."""

    return build_follow_up_issue_key(
        component=component,
        trigger_class=trigger_class,
        product_impact=product_impact,
        root_cause=root_cause,
        version=version,
    ).key


compute_follow_up_issue_key = follow_up_issue_key


def issue_key_components(value: str) -> dict[str, Any]:
    """Validate a key's envelope without pretending its digest is reversible."""

    match = _ISSUE_KEY_RE.fullmatch(_issue_key_text(value))
    assert match is not None  # _issue_key_text performed the validation.
    return {"version": int(match.group("version")), "digest": match.group("digest")}


@dataclass(frozen=True, slots=True)
class FollowUpRelation:
    """An auditable link between two semantic follow-up issue keys."""

    relation: str
    target_issue_key: str
    source_issue_key: str = ""

    def __post_init__(self) -> None:
        relation = "alias_of" if self.relation == "alias" else self.relation
        object.__setattr__(self, "relation", _axis(relation, "relation", RELATION_TYPES))
        object.__setattr__(self, "target_issue_key", _issue_key_text(self.target_issue_key))
        object.__setattr__(
            self,
            "source_issue_key",
            _optional_text(self.source_issue_key, "source_issue_key"),
        )
        if self.source_issue_key:
            _issue_key_text(self.source_issue_key, "source_issue_key")
            if self.source_issue_key == self.target_issue_key:
                raise ReviewPolicyError("follow-up relation cannot target itself")

    @property
    def kind(self) -> str:
        return self.relation

    @property
    def target_key(self) -> str:
        return self.target_issue_key

    def to_record(self) -> dict[str, str]:
        record = {
            "relation": self.relation,
            "target_issue_key": self.target_issue_key,
        }
        if self.source_issue_key:
            record["source_issue_key"] = self.source_issue_key
        return record

    @classmethod
    def from_record(cls, value: Any) -> "FollowUpRelation":
        record = _record(value, "follow-up relation")
        if "relation" not in record or "target_issue_key" not in record:
            raise ReviewPolicyError(
                "follow-up relation requires relation and target_issue_key"
            )
        return cls(
            relation=record["relation"],
            target_issue_key=record["target_issue_key"],
            source_issue_key=record.get("source_issue_key", ""),
        )


FollowUpLink = FollowUpRelation


def follow_up_relation(
    *,
    relation: str,
    target_issue_key: str,
    source_issue_key: str = "",
) -> FollowUpRelation:
    return FollowUpRelation(
        relation=relation,
        target_issue_key=target_issue_key,
        source_issue_key=source_issue_key,
    )


def resolve_issue_key_alias(
    issue_key: str,
    aliases: Mapping[str, str] | None = None,
) -> str:
    """Resolve an issue-key alias chain to one canonical key.

    Alias maps are persisted by the CLI, but the resolution rule belongs in
    the policy layer so every caller rejects malformed keys and cycles in the
    same way.  A relation target is never accepted merely because it happens
    to be present as an unvalidated dictionary key.
    """

    current = _issue_key_text(issue_key)
    mapping = aliases or {}
    if not isinstance(mapping, Mapping):
        raise ReviewPolicyError("issue key aliases must be an object")
    visited: set[str] = set()
    while current in mapping:
        if current in visited:
            raise ReviewPolicyError("follow-up issue-key alias cycle detected")
        visited.add(current)
        current = _issue_key_text(mapping[current], "canonical_issue_key")
    return current


def validate_follow_up_relation(
    value: FollowUpRelation | Mapping[str, Any],
    *,
    known_issue_keys: Sequence[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> FollowUpRelation:
    """Validate a relation against known canonical or aliased issue keys."""

    relation = value if isinstance(value, FollowUpRelation) else FollowUpRelation.from_record(value)
    known = {
        resolve_issue_key_alias(key, aliases)
        for key in known_issue_keys
    }
    target = resolve_issue_key_alias(relation.target_issue_key, aliases)
    if known and target not in known:
        raise ReviewPolicyError(
            f"follow-up relation target is unknown: {relation.target_issue_key}"
        )
    if relation.source_issue_key:
        source = resolve_issue_key_alias(relation.source_issue_key, aliases)
        if source == target:
            raise ReviewPolicyError("follow-up relation cannot target itself")
    return relation


def same_follow_up_issue(
    left: FollowUpIssueKey | str, right: FollowUpIssueKey | str
) -> bool:
    """Compare semantic keys, not review fingerprints or evidence metadata."""

    left_key = left.key if isinstance(left, FollowUpIssueKey) else _issue_key_text(left)
    right_key = right.key if isinstance(right, FollowUpIssueKey) else _issue_key_text(right)
    return left_key == right_key


def deduplicate_follow_up_keys(
    keys: Sequence[FollowUpIssueKey | str],
) -> tuple[str, ...]:
    if isinstance(keys, (str, bytes)) or not isinstance(keys, Sequence):
        raise ReviewPolicyError("follow-up keys must be a sequence")
    result: list[str] = []
    seen: set[str] = set()
    for value in keys:
        key = value.key if isinstance(value, FollowUpIssueKey) else _issue_key_text(value)
        if key not in seen:
            result.append(key)
            seen.add(key)
    return tuple(result)


deduplicate_issue_keys = deduplicate_follow_up_keys


__all__ = [
    "BLOCKING_DISPOSITIONS",
    "CONTRACT_RELATIONS",
    "DECISION_REQUIREMENTS",
    "DISPOSITIONS",
    "FACT_STATUSES",
    "FINDING_ORIGINS",
    "FOLLOW_UP_ISSUE_KEY_PREFIX",
    "FOLLOW_UP_ISSUE_KEY_VERSION",
    "FollowUpIssueKey",
    "FollowUpLink",
    "FollowUpRelation",
    "FindingDisposition",
    "Disposition",
    "RELEASE_EFFECTS",
    "RELATION_TYPES",
    "ReviewDisposition",
    "ReviewPolicyError",
    "SEVERITIES",
    "build_follow_up_issue_key",
    "canonical_follow_up_identity",
    "compute_follow_up_issue_key",
    "deduplicate_follow_up_keys",
    "deduplicate_issue_keys",
    "enforce_disposition_policy",
    "follow_up_issue_key",
    "follow_up_relation",
    "is_safety_critical_finding",
    "issue_key_components",
    "same_follow_up_issue",
    "resolve_issue_key_alias",
    "validate_follow_up_relation",
    "validate_disposition",
    "validate_finding_policy",
]
