"""Fail-closed release dependency validation for herdr-dev-loop 0.5.3."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .review import ExternalReviewProtocolAdapter, ReviewModelError


_SEMVER_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_RELEASE_EVIDENCE = [
    "hloop_codex_install_parity",
    "hloop_claude_install_parity",
    "companion_codex_install_parity",
    "companion_claude_install_parity",
    "codex_fresh_session_handshake",
    "claude_fresh_session_handshake",
]
_INSTALL_DESTINATIONS = {
    "codex": "${CODEX_HOME:-$HOME/.codex}/skills/codex-review-multi-v2",
    "claude": "${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/codex-review-multi-v2",
}


class ReleaseDependencyError(ValueError):
    """A release dependency record or runtime adapter is invalid."""


class ReleaseDependencyUnavailable(ReleaseDependencyError):
    """The immutable companion identity is not ready for release."""


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        raise ReleaseDependencyError(
            f"{label} fields are not canonical: missing={missing}, unknown={unknown}"
        )


def _semver(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _SEMVER_RE.fullmatch(value):
        raise ReleaseDependencyError(f"{label} is invalid")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def validate_release_dependencies(value: Any) -> ExternalReviewProtocolAdapter:
    """Validate the canonical release pin and return its exact runtime adapter.

    The unavailable branch is deliberately terminal. Placeholder distribution
    values and mutable installed copies cannot turn it into an executable pin.
    """

    if not isinstance(value, Mapping):
        raise ReleaseDependencyError("release dependency record must be an object")
    _require_exact_fields(
        value,
        {
            "record_type",
            "schema_version",
            "release",
            "required_release_evidence",
            "dependencies",
        },
        "release dependency record",
    )
    if value["record_type"] != "herdr_dev_loop_release_dependencies":
        raise ReleaseDependencyError("release dependency record_type is invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ReleaseDependencyError("release dependency schema_version is invalid")

    release = value["release"]
    if not isinstance(release, Mapping):
        raise ReleaseDependencyError("release must be an object")
    _require_exact_fields(release, {"name", "version", "release_ready"}, "release")
    if release.get("name") != "herdr-dev-loop" or release.get("version") != "0.5.3":
        raise ReleaseDependencyError("release identity is not canonical 0.5.3")
    if not isinstance(release.get("release_ready"), bool):
        raise ReleaseDependencyError("release_ready must be boolean")
    if value["required_release_evidence"] != _REQUIRED_RELEASE_EVIDENCE:
        raise ReleaseDependencyError(
            "Codex/Claude parity and fresh-session evidence are required"
        )

    dependencies = value["dependencies"]
    if not isinstance(dependencies, list) or len(dependencies) != 1:
        raise ReleaseDependencyError("exactly one release dependency is required")
    dependency = dependencies[0]
    if not isinstance(dependency, Mapping):
        raise ReleaseDependencyError("release dependency must be an object")
    _require_exact_fields(
        dependency,
        {
            "name",
            "kind",
            "required",
            "availability",
            "blocking_reason",
            "minimum_compatible_version",
            "distribution_identity",
            "capability_manifest",
            "install_destinations",
        },
        "dependency",
    )
    if (
        dependency["name"] != "codex-review-multi-v2"
        or dependency["kind"] != "external_review_protocol"
        or dependency["required"] is not True
    ):
        raise ReleaseDependencyError("required companion identity is invalid")

    capability = dependency["capability_manifest"]
    if not isinstance(capability, Mapping):
        raise ReleaseDependencyError("capability manifest contract must be an object")
    _require_exact_fields(
        capability,
        {"relative_path", "record_type", "protocol", "required_capabilities"},
        "capability manifest",
    )
    if capability["record_type"] != "external_review_protocol_adapter":
        raise ReleaseDependencyError("capability manifest record_type is invalid")
    if capability["protocol"] != "codex-review-multi-v2":
        raise ReleaseDependencyError("capability manifest protocol is invalid")
    if capability["required_capabilities"] != ["externally-planned-v1"]:
        raise ReleaseDependencyError("externally-planned-v1 is required")
    if dependency["install_destinations"] != _INSTALL_DESTINATIONS:
        raise ReleaseDependencyError("Codex and Claude companion destinations are required")

    availability = dependency["availability"]
    if availability == "unavailable":
        if release["release_ready"] is not False:
            raise ReleaseDependencyError("unavailable companion requires release_ready false")
        if (
            not isinstance(dependency["blocking_reason"], str)
            or not dependency["blocking_reason"].strip()
        ):
            raise ReleaseDependencyError("unavailable companion requires a blocking reason")
        if dependency["minimum_compatible_version"] is not None:
            raise ReleaseDependencyError(
                "unavailable companion cannot claim a minimum version"
            )
        if dependency["distribution_identity"] is not None:
            raise ReleaseDependencyError(
                "unavailable companion cannot claim a distribution identity"
            )
        if capability["relative_path"] is not None:
            raise ReleaseDependencyError(
                "unavailable companion cannot claim a manifest path"
            )
        raise ReleaseDependencyUnavailable("0.5.3 release_ready is false")
    if availability != "available":
        raise ReleaseDependencyError("companion availability is invalid")
    if release["release_ready"] is not True:
        raise ReleaseDependencyUnavailable(
            "available companion requires release_ready true"
        )
    if dependency["blocking_reason"] != "":
        raise ReleaseDependencyError(
            "available dependency cannot retain a blocking reason"
        )

    minimum_text = dependency["minimum_compatible_version"]
    minimum = _semver(minimum_text, "minimum compatible companion version")
    distribution = dependency["distribution_identity"]
    if not isinstance(distribution, Mapping):
        raise ReleaseDependencyUnavailable("immutable distribution identity is missing")
    _require_exact_fields(
        distribution,
        {"source", "immutable_id", "version", "digest_algorithm", "content_digest"},
        "distribution identity",
    )
    if not all(
        isinstance(distribution[key], str) and distribution[key].strip()
        for key in ("source", "immutable_id")
    ):
        raise ReleaseDependencyError(
            "distribution source and immutable_id must be non-empty"
        )
    version_text = distribution["version"]
    version = _semver(version_text, "exact companion version")
    if version < minimum:
        raise ReleaseDependencyError(
            "exact companion version is below minimum compatible version"
        )
    if distribution["digest_algorithm"] != "sha256-tree-v1":
        raise ReleaseDependencyError("companion digest algorithm is invalid")
    digest = distribution["content_digest"]
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ReleaseDependencyError("companion content digest is invalid")

    relative_path = capability["relative_path"]
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or Path(relative_path).is_absolute()
        or ".." in Path(relative_path).parts
    ):
        raise ReleaseDependencyError(
            "capability manifest path must be a safe relative path"
        )
    try:
        return ExternalReviewProtocolAdapter(
            protocol=capability["protocol"],
            source=f"{distribution['source']}@{distribution['immutable_id']}",
            version=version_text,
            content_digest=digest,
            capabilities=tuple(capability["required_capabilities"]),
        )
    except ReviewModelError as exc:
        raise ReleaseDependencyError(str(exc)) from exc


def load_release_dependencies(path: Path) -> ExternalReviewProtocolAdapter:
    """Load and validate one immutable release dependency declaration."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseDependencyError(
            f"cannot load release dependency record {path}: {exc}"
        ) from exc
    return validate_release_dependencies(value)


def validate_runtime_adapter(
    runtime: ExternalReviewProtocolAdapter,
    expected: ExternalReviewProtocolAdapter,
) -> None:
    """Require the runtime adapter to equal the immutable release pin exactly."""

    if runtime != expected:
        mismatches = [
            field
            for field in (
                "protocol",
                "source",
                "version",
                "content_digest",
                "capabilities",
            )
            if getattr(runtime, field) != getattr(expected, field)
        ]
        raise ReleaseDependencyError(
            "runtime adapter does not match release dependency pin: "
            + ", ".join(mismatches)
        )
