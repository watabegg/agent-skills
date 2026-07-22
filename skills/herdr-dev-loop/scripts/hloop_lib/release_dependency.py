"""Fail-closed release dependency validation for herdr-dev-loop 0.5.3."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from .review import ExternalReviewProtocolAdapter, ReviewModelError


_SEMVER_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
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
    "claude": "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/codex-review-multi-v2",
}


class ReleaseDependencyError(ValueError):
    """A release dependency record or runtime adapter is invalid."""


class ReleaseDependencyUnavailable(ReleaseDependencyError):
    """The immutable companion identity is not ready for release."""


def _load_json_object(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseDependencyError(f"cannot load {label} {path}: {exc}") from exc


def _safe_relative_path(value: str, label: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ReleaseDependencyError(f"{label} must be a safe relative path")
    return path


def _is_forbidden_distribution_path(relative_path: Path) -> bool:
    """Return whether a generated executable or VCS path invalidates the tree."""

    return (
        any(part in {".git", "__pycache__"} for part in relative_path.parts)
        or relative_path.suffix in {".pyc", ".pyo"}
    )


def sha256_tree_v1(
    distribution_root: Path,
    *,
    capability_manifest_relative_path: str,
) -> str:
    """Digest one companion payload while excluding its self-referential manifest.

    The capability manifest embeds this digest and is therefore validated as a
    separate exact record. Every other regular payload file participates in the
    digest in lexicographic POSIX relative-path order.
    """

    root = distribution_root
    if root.is_symlink() or not root.is_dir():
        raise ReleaseDependencyError(
            f"companion distribution root is not a regular directory: {root}"
        )
    excluded = _safe_relative_path(
        capability_manifest_relative_path, "capability manifest path"
    ).as_posix()
    candidates: list[tuple[str, Path]] = []
    pending: list[tuple[Path, Path]] = [(root, Path())]
    while pending:
        directory, relative_directory = pending.pop()
        try:
            with os.scandir(directory) as scanner:
                entries = list(scanner)
        except OSError as exc:
            label = relative_directory.as_posix() or "."
            raise ReleaseDependencyError(
                f"cannot enumerate companion distribution directory {label}: {exc}"
            ) from exc
        for entry in entries:
            relative = relative_directory / entry.name
            relative_text = relative.as_posix()
            try:
                relative_text.encode("utf-8")
            except UnicodeError as exc:
                raise ReleaseDependencyError(
                    "companion distribution path is not valid UTF-8: "
                    f"{relative_text!r}"
                ) from exc
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise ReleaseDependencyError(
                    f"cannot inspect companion distribution path {relative_text}: {exc}"
                ) from exc
            if stat.S_ISLNK(mode):
                raise ReleaseDependencyError(
                    f"companion distribution contains a symlink: {relative_text}"
                )
            if _is_forbidden_distribution_path(relative):
                raise ReleaseDependencyError(
                    "companion distribution contains forbidden generated or VCS content: "
                    f"{relative_text}"
                )
            path = Path(entry.path)
            if stat.S_ISDIR(mode):
                pending.append((path, relative))
            elif stat.S_ISREG(mode):
                candidates.append((relative_text, path))
            else:
                raise ReleaseDependencyError(
                    f"companion distribution contains a non-regular file: {relative_text}"
                )

    payload: list[tuple[str, bytes]] = []
    for relative_text, path in sorted(candidates):
        if relative_text == excluded:
            continue
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("payload path changed to a non-regular file")
                with os.fdopen(fd, "rb") as payload_file:
                    fd = -1
                    contents = payload_file.read()
            finally:
                if fd >= 0:
                    os.close(fd)
            payload.append((relative_text, contents))
        except OSError as exc:
            raise ReleaseDependencyError(
                f"cannot read companion payload file {relative_text}: {exc}"
            ) from exc

    digest = hashlib.sha256()
    for relative_text, contents in payload:
        digest.update(relative_text.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(contents)).encode("ascii"))
        digest.update(b"\0")
        digest.update(contents)
    return f"sha256:{digest.hexdigest()}"


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
    if not _COMMIT_RE.fullmatch(distribution["immutable_id"]):
        raise ReleaseDependencyError(
            "distribution immutable_id must be an exact lowercase Git commit SHA"
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
    if not isinstance(relative_path, str):
        raise ReleaseDependencyError(
            "capability manifest path must be a safe relative path"
        )
    _safe_relative_path(relative_path, "capability manifest path")
    try:
        return ExternalReviewProtocolAdapter(
            protocol=capability["protocol"],
            source=(
                f"{distribution['source']}#sha256-tree-v1="
                f"{digest.removeprefix('sha256:')}"
            ),
            version=version_text,
            content_digest=digest,
            capabilities=tuple(capability["required_capabilities"]),
        )
    except ReviewModelError as exc:
        raise ReleaseDependencyError(str(exc)) from exc


def load_release_dependencies(path: Path) -> ExternalReviewProtocolAdapter:
    """Load a release declaration and validate its sibling distribution."""

    return validate_release_distribution(
        path,
        path.parent.parent / "codex-review-multi-v2",
    )


def provider_companion_root(
    provider: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the companion root that a provider session actually discovers."""

    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or Path.home()).expanduser()
    if provider == "codex":
        config_root = Path(env.get("CODEX_HOME") or home / ".codex").expanduser()
    elif provider == "claude":
        config_root = Path(
            env.get("CLAUDE_CONFIG_DIR") or home / ".claude"
        ).expanduser()
    else:
        raise ReleaseDependencyError(f"unsupported companion provider: {provider}")
    distribution_root = (
        config_root / "skills" / "codex-review-multi-v2"
    ).absolute()
    current = Path(distribution_root.anchor)
    for component in distribution_root.parts[1:]:
        current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ReleaseDependencyError(
                f"cannot inspect provider companion path {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ReleaseDependencyError(
                f"provider companion path contains a symlink: {current}"
            )
    return distribution_root


def validate_provider_distribution(
    release_dependency_path: Path,
    provider: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, ExternalReviewProtocolAdapter]:
    """Validate the exact distribution discoverable by one provider."""

    distribution_root = provider_companion_root(provider, environ=environ)
    return (
        distribution_root,
        validate_release_distribution(release_dependency_path, distribution_root),
    )


def validate_release_distribution(
    release_dependency_path: Path,
    distribution_root: Path,
) -> ExternalReviewProtocolAdapter:
    """Validate the vendored or installed companion against the release pin."""

    value = _load_json_object(release_dependency_path, "release dependency record")
    expected = validate_release_dependencies(value)
    dependency = value["dependencies"][0]
    manifest_relative_text = dependency["capability_manifest"]["relative_path"]
    manifest_relative = _safe_relative_path(
        manifest_relative_text, "capability manifest path"
    )
    manifest_path = distribution_root / manifest_relative
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseDependencyError(
            f"capability manifest is missing or is not a regular file: {manifest_path}"
        )
    manifest_value = _load_json_object(manifest_path, "capability manifest")
    try:
        observed = ExternalReviewProtocolAdapter.from_record(manifest_value)
    except ReviewModelError as exc:
        raise ReleaseDependencyError(str(exc)) from exc
    validate_runtime_adapter(observed, expected)
    observed_digest = sha256_tree_v1(
        distribution_root,
        capability_manifest_relative_path=manifest_relative_text,
    )
    if observed_digest != expected.content_digest:
        raise ReleaseDependencyError(
            "companion distribution digest does not match release dependency pin: "
            f"expected={expected.content_digest}, observed={observed_digest}"
        )
    return observed


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
