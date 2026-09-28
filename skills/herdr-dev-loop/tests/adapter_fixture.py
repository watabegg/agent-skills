"""Synthetic external-review adapter package used by offline release tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from hloop_lib.release_dependency import sha256_tree_v1  # noqa: E402


_SOURCE = "https://example.invalid/external-review.git"
_MANIFEST_RELATIVE_PATH = "capabilities/externally-planned-v1.json"
_INSTALL_DESTINATIONS = {
    "codex": "${CODEX_HOME:-$HOME/.codex}/skills/external-review",
    "claude": "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/external-review",
}
_REQUIRED_EVIDENCE = [
    "hloop_codex_install_parity",
    "hloop_claude_install_parity",
    "codex_fresh_session_handshake",
    "claude_fresh_session_handshake",
]


def make_external_fixture(parent: Path) -> tuple[Path, dict[str, Any]]:
    """Create a neutral synthetic adapter distribution and its release record."""

    distribution_root = parent / "external-review-fixture"
    capabilities = distribution_root / "capabilities"
    capabilities.mkdir(parents=True)
    (distribution_root / "README.md").write_text(
        "Synthetic adapter fixture for offline HLoop tests.\n", encoding="utf-8"
    )
    (distribution_root / "SKILL.md").write_text(
        "# Synthetic external review adapter\n\n"
        "This fixture contains no production review instructions.\n",
        encoding="utf-8",
    )

    content_digest = sha256_tree_v1(
        distribution_root,
        capability_manifest_relative_path=_MANIFEST_RELATIVE_PATH,
    )
    adapter_source = (
        f"{_SOURCE}#sha256-tree-v1={content_digest.removeprefix('sha256:')}"
    )
    manifest = {
        "record_type": "external_review_protocol_adapter",
        "protocol": "external-review",
        "source": adapter_source,
        "version": "2.1.1",
        "content_digest": content_digest,
        "capabilities": ["externally-planned-v1"],
    }
    manifest_path = distribution_root / _MANIFEST_RELATIVE_PATH
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    record: dict[str, Any] = {
        "record_type": "herdr_dev_loop_release_dependencies",
        "schema_version": 1,
        "release": {
            "name": "herdr-dev-loop",
            "version": "0.5.3",
            "release_ready": True,
        },
        "required_release_evidence": list(_REQUIRED_EVIDENCE),
        "dependencies": [
            {
                "name": "external-review",
                "kind": "external_review_protocol",
                "required": True,
                "availability": "available",
                "blocking_reason": "",
                "minimum_compatible_version": "2.1.0",
                "distribution_identity": {
                    "source": _SOURCE,
                    "immutable_id": "a" * 40,
                    "version": "2.1.1",
                    "digest_algorithm": "sha256-tree-v1",
                    "content_digest": content_digest,
                },
                "capability_manifest": {
                    "relative_path": _MANIFEST_RELATIVE_PATH,
                    "record_type": "external_review_protocol_adapter",
                    "protocol": "external-review",
                    "required_capabilities": ["externally-planned-v1"],
                },
                "install_destinations": dict(_INSTALL_DESTINATIONS),
            }
        ],
    }
    return distribution_root, record


def write_release_record(path: Path, record: dict[str, Any]) -> Path:
    """Write a generated fixture record as UTF-8 JSON and return its path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path
