"""Validation and idempotency primitives for HLoop agent reports.

This module deliberately has no dependency on the HLoop CLI.  A role creates a
client event here, persists it to its local outbox, and later hands the event to
the broker.  The broker is the only component allowed to add ``sequence``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


REPORT_TYPES = frozenset({"ack", "milestone", "attention", "completion"})
MAX_TEXT_LENGTH = 16_384
MAX_IDENTIFIER_LENGTH = 512
MAX_LIST_ITEMS = 256
MAX_REFERENCE_LENGTH = 4_096

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REPORT_FIELDS = frozenset(
    {
        "run_id",
        "role_id",
        "attempt_id",
        "task_contract_digest",
        "type",
        "stage",
        "summary",
        "understood_goal",
        "scope",
        "acceptance",
        "approach",
        "risks",
        "impact",
        "attempted",
        "options",
        "recommendation",
        "blocked_scope",
        "artifact",
        "head_sha",
        "validation_results",
        "residual_risks",
        "handoff",
        "next",
        "needs_manager",
        "evidence_refs",
        "created_at",
    }
)
_CLIENT_EVENT_FIELDS = _REPORT_FIELDS | {"event_id", "payload_digest"}

# Identity and envelope fields the CLI derives from role registration rather
# than from a submitted report body: a JSON --file/--stdin payload carries
# only these remaining "content" fields, matching the individual CLI flags it
# replaces.
REPORT_CONTENT_FIELDS = _REPORT_FIELDS - {
    "run_id",
    "role_id",
    "attempt_id",
    "task_contract_digest",
    "needs_manager",
    "created_at",
}


class ReportValidationError(ValueError):
    """Raised when an agent report does not satisfy the transport schema."""


def utc_now() -> str:
    """Return a stable RFC 3339 timestamp suitable for persisted events."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_rfc3339(value: str, *, field: str = "created_at") -> datetime:
    """Parse a timezone-aware RFC 3339 timestamp or raise a schema error."""

    if not isinstance(value, str) or not value:
        raise ReportValidationError(f"{field} must be a non-empty RFC 3339 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ReportValidationError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReportValidationError(f"{field} must include a timezone")
    return parsed


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for digests and durable storage."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReportValidationError(f"value is not canonical JSON: {exc}") from exc


def _text(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
    allow_newlines: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ReportValidationError(f"{field} must be a string")
    if not allow_empty and not value.strip():
        raise ReportValidationError(f"{field} must not be empty")
    if len(value) > maximum:
        raise ReportValidationError(f"{field} exceeds {maximum} characters")
    for char in value:
        codepoint = ord(char)
        if codepoint == 0 or (codepoint < 32 and char not in {"\n", "\t"}):
            raise ReportValidationError(f"{field} contains a control character")
    if not allow_newlines and ("\n" in value or "\r" in value):
        raise ReportValidationError(f"{field} must be a single line")
    return value


def _string_list(
    value: Any,
    *,
    field: str,
    item_maximum: int,
    allow_newlines: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise ReportValidationError(f"{field} must be a JSON array of strings")
    if len(value) > MAX_LIST_ITEMS:
        raise ReportValidationError(f"{field} exceeds {MAX_LIST_ITEMS} items")
    return [
        _text(
            item,
            field=f"{field}[{index}]",
            maximum=item_maximum,
            allow_newlines=allow_newlines,
        )
        for index, item in enumerate(value)
    ]


def validate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the report payload before client persistence.

    Unknown fields are rejected so role output cannot silently become an
    executable broker or Manager command.  ACK reports carry their semantic
    contract fields.  Milestone, attention, and completion reports carry the
    evidence needed to interpret their state change independently.  A report
    cannot smuggle fields belonging to a different semantic type.
    """

    if not isinstance(report, Mapping):
        raise ReportValidationError("report must be a JSON object")
    unknown = set(report) - _REPORT_FIELDS
    if unknown:
        raise ReportValidationError(
            "report contains unknown fields: " + ", ".join(sorted(unknown))
        )

    required = {
        "run_id",
        "role_id",
        "attempt_id",
        "task_contract_digest",
        "type",
        "stage",
        "summary",
        "next",
        "needs_manager",
        "evidence_refs",
        "created_at",
    }
    missing = required - set(report)
    if missing:
        raise ReportValidationError(
            "report is missing required fields: " + ", ".join(sorted(missing))
        )

    report_type = _text(
        report["type"], field="type", maximum=32, allow_newlines=False
    )
    if report_type not in REPORT_TYPES:
        raise ReportValidationError(
            f"type must be one of: {', '.join(sorted(REPORT_TYPES))}"
        )

    digest = _text(
        report["task_contract_digest"],
        field="task_contract_digest",
        maximum=64,
        allow_newlines=False,
    ).lower()
    if not _DIGEST_RE.fullmatch(digest):
        raise ReportValidationError(
            "task_contract_digest must be a 64-character SHA-256 hex digest"
        )

    needs_manager = report["needs_manager"]
    if not isinstance(needs_manager, bool):
        raise ReportValidationError("needs_manager must be a boolean")
    if report_type in {"ack", "attention"} and not needs_manager:
        raise ReportValidationError(f"{report_type} reports must set needs_manager=true")
    if report_type == "milestone" and needs_manager:
        raise ReportValidationError(
            "milestone reports that need Manager action must use type=attention"
        )

    understood_goal = report.get("understood_goal", "")
    scope = report.get("scope", [])
    acceptance = report.get("acceptance", [])
    approach = report.get("approach", "")
    risks = report.get("risks", [])
    impact = report.get("impact", "")
    attempted = report.get("attempted", [])
    options = report.get("options", [])
    recommendation = report.get("recommendation", "")
    blocked_scope = report.get("blocked_scope", [])
    artifact = report.get("artifact", "")
    head_sha = report.get("head_sha", "")
    validation_results = report.get("validation_results", [])
    residual_risks = report.get("residual_risks", [])
    handoff = report.get("handoff", "")
    required_by_type = {
        "ack": {"understood_goal", "scope", "acceptance", "approach"},
        "milestone": {"risks"},
        "attention": {
            "impact",
            "attempted",
            "options",
            "recommendation",
            "blocked_scope",
        },
        "completion": {
            "artifact",
            "head_sha",
            "validation_results",
            "residual_risks",
            "handoff",
        },
    }
    type_specific_fields = set().union(
        required_by_type["milestone"],
        required_by_type["attention"],
        required_by_type["completion"],
    )
    unexpected_type_fields = (set(report) & type_specific_fields) - required_by_type[
        report_type
    ]
    if unexpected_type_fields:
        raise ReportValidationError(
            f"{report_type} reports contain fields for another type: "
            + ", ".join(sorted(unexpected_type_fields))
        )
    missing_type_fields = required_by_type[report_type] - set(report)
    if missing_type_fields:
        raise ReportValidationError(
            f"{report_type} reports require: "
            + ", ".join(sorted(missing_type_fields))
        )

    normalized = {
        "run_id": _text(
            report["run_id"],
            field="run_id",
            maximum=MAX_IDENTIFIER_LENGTH,
            allow_newlines=False,
        ),
        "role_id": _text(
            report["role_id"],
            field="role_id",
            maximum=MAX_IDENTIFIER_LENGTH,
            allow_newlines=False,
        ),
        "attempt_id": _text(
            report["attempt_id"],
            field="attempt_id",
            maximum=MAX_IDENTIFIER_LENGTH,
            allow_newlines=False,
        ),
        "task_contract_digest": digest,
        "type": report_type,
        "stage": _text(
            report["stage"], field="stage", maximum=512, allow_newlines=False
        ),
        "summary": _text(
            report["summary"], field="summary", maximum=MAX_TEXT_LENGTH
        ),
        "understood_goal": _text(
            understood_goal,
            field="understood_goal",
            maximum=MAX_TEXT_LENGTH,
            allow_empty=report_type != "ack",
        ),
        "scope": _string_list(
            scope, field="scope", item_maximum=MAX_TEXT_LENGTH
        ),
        "acceptance": _string_list(
            acceptance, field="acceptance", item_maximum=MAX_TEXT_LENGTH
        ),
        "approach": _text(
            approach,
            field="approach",
            maximum=MAX_TEXT_LENGTH,
            allow_empty=report_type != "ack",
        ),
        "next": _text(report["next"], field="next", maximum=MAX_TEXT_LENGTH),
        "needs_manager": needs_manager,
        "evidence_refs": _string_list(
            report["evidence_refs"],
            field="evidence_refs",
            item_maximum=MAX_REFERENCE_LENGTH,
            allow_newlines=False,
        ),
        "created_at": _text(
            report["created_at"],
            field="created_at",
            maximum=64,
            allow_newlines=False,
        ),
    }
    if report_type == "milestone":
        normalized["risks"] = _string_list(
            risks, field="risks", item_maximum=MAX_TEXT_LENGTH
        )
    elif report_type == "attention":
        normalized.update(
            {
                "impact": _text(
                    impact, field="impact", maximum=MAX_TEXT_LENGTH
                ),
                "attempted": _string_list(
                    attempted, field="attempted", item_maximum=MAX_TEXT_LENGTH
                ),
                "options": _string_list(
                    options, field="options", item_maximum=MAX_TEXT_LENGTH
                ),
                "recommendation": _text(
                    recommendation,
                    field="recommendation",
                    maximum=MAX_TEXT_LENGTH,
                ),
                "blocked_scope": _string_list(
                    blocked_scope,
                    field="blocked_scope",
                    item_maximum=MAX_TEXT_LENGTH,
                ),
            }
        )
    elif report_type == "completion":
        normalized.update(
            {
                "artifact": _text(
                    artifact,
                    field="artifact",
                    maximum=MAX_REFERENCE_LENGTH,
                    allow_newlines=False,
                ),
                "head_sha": _text(
                    head_sha,
                    field="head_sha",
                    maximum=64,
                    allow_newlines=False,
                ).lower(),
                "validation_results": _string_list(
                    validation_results,
                    field="validation_results",
                    item_maximum=MAX_TEXT_LENGTH,
                ),
                "residual_risks": _string_list(
                    residual_risks,
                    field="residual_risks",
                    item_maximum=MAX_TEXT_LENGTH,
                ),
                "handoff": _text(
                    handoff, field="handoff", maximum=MAX_TEXT_LENGTH
                ),
            }
        )
    parse_rfc3339(normalized["created_at"])
    required_nonempty_lists = {
        "ack": ("scope", "acceptance"),
        "milestone": ("evidence_refs", "risks"),
        "attention": ("attempted", "options", "blocked_scope"),
        "completion": (
            "evidence_refs",
            "validation_results",
            "residual_risks",
        ),
    }
    empty_type_fields = [
        field
        for field in required_nonempty_lists[report_type]
        if not normalized[field]
    ]
    if empty_type_fields:
        raise ReportValidationError(
            f"{report_type} reports require non-empty "
            + ", ".join(empty_type_fields)
        )
    if report_type == "completion" and not _HEAD_SHA_RE.fullmatch(
        normalized["head_sha"]
    ):
        raise ReportValidationError("head_sha must be a 40- or 64-character hex digest")
    canonical_json(normalized)
    return normalized


def payload_digest(report: Mapping[str, Any]) -> str:
    """Return the SHA-256 digest of a normalized report payload."""

    normalized = validate_report(report)
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def new_event_id() -> str:
    """Create a client-owned idempotency identifier."""

    return str(uuid.uuid4())


def normalize_event_id(value: Any) -> str:
    """Validate an event UUID and return its canonical representation."""

    text = _text(
        value, field="event_id", maximum=64, allow_newlines=False
    ).lower()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ReportValidationError("event_id must be a UUID") from exc
    if str(parsed) != text:
        raise ReportValidationError("event_id must use canonical UUID notation")
    return text


def normalize_invocation_id(value: Any) -> str:
    """Validate a caller-stable opaque invocation key."""

    text = _text(
        value,
        field="invocation_id",
        maximum=MAX_IDENTIFIER_LENGTH,
        allow_newlines=False,
    )
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in text):
        raise ReportValidationError(
            "invocation_id must contain only visible ASCII without whitespace"
        )
    return text


def prepare_client_event(
    report: Mapping[str, Any], *, event_id: str | None = None
) -> dict[str, Any]:
    """Validate a report and add the client idempotency envelope."""

    normalized = validate_report(report)
    result = dict(normalized)
    result["event_id"] = normalize_event_id(event_id or new_event_id())
    result["payload_digest"] = payload_digest(normalized)
    return result


def validate_client_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a client event and verify that its digest matches its payload."""

    if not isinstance(event, Mapping):
        raise ReportValidationError("client event must be a JSON object")
    if "sequence" in event:
        raise ReportValidationError("sequence is broker-assigned and forbidden on clients")
    unknown = set(event) - _CLIENT_EVENT_FIELDS
    if unknown:
        raise ReportValidationError(
            "client event contains unknown fields: " + ", ".join(sorted(unknown))
        )
    missing = {"event_id", "payload_digest"} - set(event)
    if missing:
        raise ReportValidationError(
            "client event is missing required fields: " + ", ".join(sorted(missing))
        )

    report = {key: event[key] for key in _REPORT_FIELDS if key in event}
    normalized_report = validate_report(report)
    supplied_digest = _text(
        event["payload_digest"],
        field="payload_digest",
        maximum=64,
        allow_newlines=False,
    ).lower()
    if not _DIGEST_RE.fullmatch(supplied_digest):
        raise ReportValidationError(
            "payload_digest must be a 64-character SHA-256 hex digest"
        )
    expected_digest = payload_digest(normalized_report)
    if supplied_digest != expected_digest:
        raise ReportValidationError("payload_digest does not match the report payload")

    result = dict(normalized_report)
    result["event_id"] = normalize_event_id(event["event_id"])
    result["payload_digest"] = supplied_digest
    return result


def assign_broker_sequence(
    client_event: Mapping[str, Any], sequence: int
) -> dict[str, Any]:
    """Return a stored event with a broker-owned monotonic sequence."""

    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ReportValidationError("sequence must be a positive integer")
    result = validate_client_event(client_event)
    result["sequence"] = sequence
    return result
