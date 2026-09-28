"""Release-contract checks for herdr-dev-loop 0.5.3."""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILL_ROOT / "tests"))

from hloop_lib import config  # noqa: E402
from hloop_lib.release_dependency import (  # noqa: E402
    ReleaseDependencyError,
    ReleaseDependencyUnavailable,
    load_release_catalog,
    sha256_tree_v1,
    provider_companion_root,
    validate_release_distribution,
    validate_provider_distribution,
    validate_release_catalog,
    validate_release_dependencies,
)
from hloop_lib.review import (  # noqa: E402
    ExternalReviewProtocolAdapter,
    ReviewModelError,
)
from adapter_fixture import make_external_fixture, write_release_record  # noqa: E402


class ReleaseIdentityTests(unittest.TestCase):
    def test_version_runtime_schema_docs_and_example_are_v053(self):
        self.assertEqual((SKILL_ROOT / "VERSION").read_text().strip(), "0.5.3")
        for relative_path in (
            "README.md",
            "SKILL.md",
            "docs/2026-07-17-v0.5.3-release-notes.md",
            "references/artifact-contract.md",
            "references/cli-notes.md",
            "references/configuration.md",
            "references/manager-loop.md",
            "references/migration-install.md",
            "references/report-protocol.md",
            "references/review-swarm.md",
            "references/reviewer-contract.md",
            "references/state-machine.md",
            "references/validation-policy.md",
            "examples/config.toml",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertIn("0.5.3", (SKILL_ROOT / relative_path).read_text())

        state_schema = json.loads(
            (SKILL_ROOT / "references/schemas/state.schema.json").read_text()
        )
        self.assertEqual(state_schema["properties"]["state_format_version"]["const"], 3)
        self.assertIn(3, state_schema["properties"]["schema_revision"]["enum"])
        example = config.load_config_file(SKILL_ROOT / "examples/config.toml")
        self.assertEqual(example["defaults"], config.V053_BUILT_IN_CONFIG_DEFAULTS)

    def test_historical_v052_release_document_remains_historical(self):
        historical = (SKILL_ROOT / "docs/RELEASE-0.5.2.md").read_text()
        self.assertIn("0.5.2", historical)

    def test_protocol_docs_describe_native_defaults_and_optional_external_execution(self):
        documents = [
            (SKILL_ROOT / path).read_text(encoding="utf-8").lower()
            for path in (
                "SKILL.md",
                "README.md",
                "references/cli-notes.md",
                "references/manager-loop.md",
                "references/reviewer-contract.md",
                "references/state-machine.md",
                "references/migration-install.md",
            )
        ]
        self.assertTrue(all("native" in document for document in documents))
        self.assertTrue(all("external-review" in document for document in documents))
        defaults = config.V053_BUILT_IN_CONFIG_DEFAULTS
        self.assertEqual(defaults["reviewer"]["protocol"], "native")
        self.assertEqual(defaults["review"]["pre_final_protocol"], "native")
        self.assertEqual(defaults["review"]["manual_final_protocol"], "native")


class ReleaseSelftestTests(unittest.TestCase):
    def _run_selftest(self, skill_root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "hloop"),
                "selftest",
                "--skill-dir",
                str(skill_root),
                "--json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_v053_selftest_requires_exact_public_final_review_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            copied_skill = Path(directory) / "herdr-dev-loop"
            shutil.copytree(SKILL_ROOT, copied_skill)
            wrappers = (
                "final-review-plan.schema.json",
                "final-review-manifest.schema.json",
            )

            valid = self._run_selftest(copied_skill)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            self.assertTrue(json.loads(valid.stdout)["ok"])

            for name in wrappers:
                path = copied_skill / "schemas" / name
                original = path.read_bytes()
                cases = (
                    (
                        "missing",
                        lambda: path.unlink(),
                        f"schemas/{name} is missing from the 0.5.3 publication",
                    ),
                    (
                        "invalid-json",
                        lambda: path.write_text("{", encoding="utf-8"),
                        f"schemas/{name} is invalid JSON",
                    ),
                    (
                        "wrong-ref",
                        lambda: path.write_text(
                            json.dumps(
                                {
                                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                                    "$ref": (
                                        "../references/schemas/final-review-manifest.schema.json"
                                        if name == "final-review-plan.schema.json"
                                        else "../references/schemas/final-review-plan.schema.json"
                                    ),
                                }
                            ),
                            encoding="utf-8",
                        ),
                        f"schemas/{name} does not point to its exact canonical schema",
                    ),
                )
                for case_name, mutate, expected_error in cases:
                    with self.subTest(name=name, case=case_name):
                        mutate()
                        result = self._run_selftest(copied_skill)
                        self.assertNotEqual(result.returncode, 0)
                        payload = json.loads(result.stdout)
                        self.assertFalse(payload["ok"])
                        self.assertTrue(
                            any(expected_error in error for error in payload["errors"]),
                            payload,
                        )
                        path.write_bytes(original)


class ReleaseCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = json.loads(
            (SKILL_ROOT / "release-dependencies.json").read_text(encoding="utf-8")
        )

    def test_shipped_release_catalog_is_native_only_and_selftest_needs_no_sibling(self):
        self.assertEqual(self.record["dependencies"], [])
        self.assertTrue(self.record["release"]["release_ready"])
        self.assertEqual(
            self.record["required_release_evidence"],
            [
                "hloop_codex_install_parity",
                "hloop_claude_install_parity",
                "codex_fresh_session_handshake",
                "claude_fresh_session_handshake",
            ],
        )
        release_path = SKILL_ROOT / "release-dependencies.json"
        self.assertEqual(load_release_catalog(release_path), ())
        self.assertEqual(validate_release_catalog(self.record), ())

        with tempfile.TemporaryDirectory() as directory:
            copied_skill = Path(directory) / "herdr-dev-loop"
            shutil.copytree(SKILL_ROOT, copied_skill)
            self.assertFalse((copied_skill.parent / "external-review").exists())
            self.assertEqual(
                load_release_catalog(copied_skill / "release-dependencies.json"), ()
            )
            result = ReleaseSelftestTests()._run_selftest(copied_skill)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_strict_external_lookup_rejects_native_only_catalog_clearly(self):
        from hloop_lib.release_dependency import load_release_dependencies

        with self.assertRaisesRegex(
            ReleaseDependencyUnavailable, "no external-review adapter is configured"
        ):
            validate_release_dependencies(self.record)
        with self.assertRaisesRegex(
            ReleaseDependencyUnavailable, "no external-review adapter is configured"
        ):
            load_release_dependencies(SKILL_ROOT / "release-dependencies.json")

    def test_optional_catalog_load_needs_no_distribution_but_strict_lookup_stays_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            distribution_root, record = make_external_fixture(parent)
            record["dependencies"][0]["required"] = False
            shutil.rmtree(distribution_root)
            record_path = write_release_record(parent / "catalog.json", record)

            adapters = load_release_catalog(record_path)
            self.assertEqual(len(adapters), 1)
            self.assertEqual(adapters[0].version, "2.1.1")
            self.assertEqual(validate_release_dependencies(record), adapters[0])
            with self.assertRaisesRegex(ReleaseDependencyError, "manifest is missing"):
                validate_release_distribution(record_path, distribution_root)

    def test_synthetic_fixture_matches_its_immutable_adapter_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            distribution_root, record = make_external_fixture(parent)
            dependency_path = write_release_record(parent / "release.json", record)
            adapter = validate_release_dependencies(record)
            dependency = record["dependencies"][0]
            self.assertEqual(adapter.version, "2.1.1")
            self.assertEqual(adapter.capabilities, ("externally-planned-v1",))
            self.assertEqual(
                sha256_tree_v1(
                    distribution_root,
                    capability_manifest_relative_path=dependency[
                        "capability_manifest"
                    ]["relative_path"],
                ),
                adapter.content_digest,
            )
            self.assertEqual(
                validate_release_distribution(dependency_path, distribution_root),
                adapter,
            )

    def test_provider_distribution_validation_uses_synthetic_discovery_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex_root = home / "codex-profile"
            claude_root = home / "claude-profile"
            environment = {
                "HOME": str(home),
                "CODEX_HOME": str(codex_root),
                "CLAUDE_CONFIG_DIR": str(claude_root),
            }
            source, record = make_external_fixture(home / "fixture")
            release_path = write_release_record(home / "release.json", record)
            for root in (codex_root, claude_root):
                shutil.copytree(source, root / "skills" / "external-review")

            for provider, config_root in (
                ("codex", codex_root),
                ("claude", claude_root),
            ):
                with self.subTest(provider=provider):
                    expected_root = config_root / "skills" / "external-review"
                    self.assertEqual(
                        provider_companion_root(provider, environ=environment),
                        expected_root.resolve(),
                    )
                    observed_root, adapter = validate_provider_distribution(
                        release_path,
                        provider,
                        environ=environment,
                    )
                    self.assertEqual(observed_root, expected_root.resolve())
                    self.assertEqual(adapter, validate_release_dependencies(record))

            codex_distribution = codex_root / "skills" / "external-review"
            shutil.rmtree(codex_distribution)
            codex_distribution.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ReleaseDependencyError, "symlink"):
                validate_provider_distribution(
                    release_path,
                    "codex",
                    environ=environment,
                )

            (claude_root / "skills" / "external-review" / "SKILL.md").write_text(
                "drift\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseDependencyError, "digest"):
                validate_provider_distribution(
                    release_path,
                    "claude",
                    environ=environment,
                )

    def test_unavailable_required_adapter_cannot_be_selected_or_claim_a_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            _, available = make_external_fixture(Path(directory))
        unavailable = copy.deepcopy(available)
        unavailable["release"]["release_ready"] = False
        dependency = unavailable["dependencies"][0]
        dependency.update(
            {
                "availability": "unavailable",
                "blocking_reason": "immutable adapter distribution is unavailable",
                "minimum_compatible_version": None,
                "distribution_identity": None,
            }
        )
        dependency["capability_manifest"]["relative_path"] = None
        self.assertEqual(validate_release_catalog(unavailable), ())
        self.assertFalse(unavailable["release"]["release_ready"])
        with self.assertRaisesRegex(
            ReleaseDependencyUnavailable, "adapter is unavailable"
        ):
            validate_release_dependencies(unavailable)

        placeholder = copy.deepcopy(unavailable)
        placeholder["dependencies"][0]["distribution_identity"] = {
            "source": "mutable-installed-copy"
        }
        with self.assertRaisesRegex(
            ValueError, "cannot claim a distribution identity"
        ):
            validate_release_dependencies(placeholder)

    def test_schema_and_required_flag_require_exact_types(self):
        with tempfile.TemporaryDirectory() as directory:
            _, base = make_external_fixture(Path(directory))
        validate_release_dependencies(copy.deepcopy(base))
        for value in (True, False, 1.0, "1"):
            with self.subTest(value=value, value_type=type(value).__name__):
                record = copy.deepcopy(base)
                record["schema_version"] = value
                with self.assertRaisesRegex(ReleaseDependencyError, "schema_version"):
                    validate_release_catalog(record)
        for value in (0, 1, "true"):
            with self.subTest(required=value):
                record = copy.deepcopy(base)
                record["dependencies"][0]["required"] = value
                with self.assertRaisesRegex(ReleaseDependencyError, "required must be boolean"):
                    validate_release_catalog(record)

    def test_distribution_validation_rejects_payload_manifest_and_symlink_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, record = make_external_fixture(root / "fixture")
            dependency_path = write_release_record(root / "release.json", record)
            distribution_root = root / "external-review"
            shutil.copytree(source, distribution_root)

            skill_path = distribution_root / "SKILL.md"
            original_skill = skill_path.read_bytes()
            skill_path.write_bytes(original_skill + b"\n# drift\n")
            with self.assertRaisesRegex(ReleaseDependencyError, "digest"):
                validate_release_distribution(dependency_path, distribution_root)
            skill_path.write_bytes(original_skill)

            manifest_path = distribution_root / "capabilities" / "externally-planned-v1.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "2.1.2"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseDependencyError, "version"):
                validate_release_distribution(dependency_path, distribution_root)
            original_manifest = source / "capabilities" / "externally-planned-v1.json"
            shutil.copy2(original_manifest, manifest_path)

            (distribution_root / "unexpected-link").symlink_to(skill_path)
            with self.assertRaisesRegex(ReleaseDependencyError, "symlink"):
                validate_release_distribution(dependency_path, distribution_root)
            (distribution_root / "unexpected-link").unlink()

            cache_dir = distribution_root / "assets" / "__pycache__"
            cache_dir.mkdir(parents=True)
            (cache_dir / "render_review.cpython-311.pyc").write_bytes(b"executable")
            with self.assertRaisesRegex(ReleaseDependencyError, "forbidden"):
                validate_release_distribution(dependency_path, distribution_root)

    def test_distribution_validation_rejects_unreadable_and_non_utf8_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, record = make_external_fixture(root / "fixture")
            dependency_path = write_release_record(root / "release.json", record)
            distribution_root = root / "external-review"
            shutil.copytree(source, distribution_root)

            opaque = distribution_root / "opaque-extra"
            opaque.mkdir()
            (opaque / "untrusted.py").write_text("raise SystemExit\n", encoding="utf-8")
            opaque.chmod(0)
            try:
                with self.assertRaisesRegex(ReleaseDependencyError, "enumerate|digest"):
                    validate_release_distribution(dependency_path, distribution_root)
            finally:
                opaque.chmod(0o700)
            shutil.rmtree(opaque)

            encoded_root = os.fsencode(distribution_root)
            invalid_name = encoded_root + b"/bad-\xff.py"
            fd = os.open(invalid_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            try:
                with self.assertRaisesRegex(ReleaseDependencyError, "UTF-8"):
                    validate_release_distribution(dependency_path, distribution_root)
            finally:
                os.unlink(invalid_name)

            manifest_path = distribution_root / "capabilities" / "externally-planned-v1.json"
            original_manifest = manifest_path.read_bytes()
            manifest_path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ReleaseDependencyError, "cannot load"):
                validate_release_distribution(dependency_path, distribution_root)
            manifest_path.write_bytes(original_manifest)

            dependency_path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ReleaseDependencyError, "cannot load"):
                validate_release_distribution(dependency_path, distribution_root)

    def test_available_pin_enforces_semantic_version_lower_bound(self):
        for exact, expected in (
            ("2.0.9", "below minimum"),
            ("2.1.0", None),
            ("2.2.0", None),
        ):
            with self.subTest(exact=exact):
                with tempfile.TemporaryDirectory() as directory:
                    _, record = make_external_fixture(Path(directory))
                dependency = record["dependencies"][0]
                dependency["distribution_identity"]["version"] = exact
                if expected:
                    with self.assertRaisesRegex(ReleaseDependencyError, expected):
                        validate_release_dependencies(record)
                else:
                    self.assertEqual(
                        validate_release_dependencies(record).version, exact
                    )

    def test_available_pin_rejects_leading_zero_semantic_versions(self):
        for field, value in (
            ("minimum", "02.1.0"),
            ("minimum", "2.01.0"),
            ("minimum", "2.1.00"),
            ("exact", "02.1.0"),
            ("exact", "2.01.0"),
            ("exact", "2.1.00"),
        ):
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    _, record = make_external_fixture(Path(directory))
                dependency = record["dependencies"][0]
                if field == "minimum":
                    dependency["minimum_compatible_version"] = value
                else:
                    dependency["distribution_identity"]["version"] = value
                with self.assertRaisesRegex(ReleaseDependencyError, "invalid"):
                    validate_release_dependencies(record)

    def test_available_pin_requires_an_exact_commit_sha(self):
        for immutable_id in ("master", "A" * 40, "a" * 39, "a" * 41):
            with self.subTest(immutable_id=immutable_id):
                with tempfile.TemporaryDirectory() as directory:
                    _, record = make_external_fixture(Path(directory))
                record["dependencies"][0]["distribution_identity"][
                    "immutable_id"
                ] = immutable_id
                with self.assertRaisesRegex(ReleaseDependencyError, "commit SHA"):
                    validate_release_dependencies(record)

    def test_runtime_adapter_rejects_missing_capability_and_bad_digest(self):
        base = {
            "record_type": "external_review_protocol_adapter",
            "protocol": "external-review",
            "source": "https://example.invalid/review@" + "a" * 40,
            "version": "2.1.0",
            "content_digest": "sha256:" + "b" * 64,
            "capabilities": ["externally-planned-v1"],
        }
        for field, value, message in (
            ("capabilities", [], "externally-planned-v1|capabilities"),
            ("content_digest", "unlabelled", "labelled SHA-256"),
        ):
            with self.subTest(field=field):
                record = {**base, field: value}
                with self.assertRaisesRegex(ReviewModelError, message):
                    ExternalReviewProtocolAdapter.from_record(record)


class HistoricalQaReconstructionTests(unittest.TestCase):
    _TASKS = {
        "T037": (
            "cc2c0d7989caffd0f3e037c52cfa002a29a4321f",
            "b2db6086c95042debad3e828bd594e4005654295",
            "587892bc8f0ad9d31e287d8b9987742ec471006b2d092f5ad476d974a4322d8b",
        ),
        "T038": (
            "5182ebefce33dbfd18bdbc87e7885ddb19a34a83",
            "b2db6086c95042debad3e828bd594e4005654295",
            "debd4123acf56622a509f74436e1d3604ba4c5197784325e1b6f7e8e52911536",
        ),
        "T039": (
            "cb7dcafabd41a25e6971a489db5c3aed3b493698",
            "b2db6086c95042debad3e828bd594e4005654295",
            "4046e95cbebcb6edadab60c4891b31547c48a7caca65b56beea0efb6b43e739f",
        ),
        "T040": (
            "b5baf26695ca4a4ede17519a90aaa8141b3ca1c2",
            "2b35c4547f3a34b6bd7fb34911492c95b75f3a02",
            "7e5dbc440c5060d5e4148047c252643caabd4b6c53043dce4f4fe0646effa2fc",
        ),
    }

    def test_t037_through_t040_results_are_durable_in_a_normal_clone(self):
        document = (
            SKILL_ROOT / "docs/2026-07-17-v0.5.3-worker-qa-reconstruction.md"
        ).read_text()
        evidence_path = (
            SKILL_ROOT
            / "references/release-evidence/v0.5.3-worker-results.json"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["encoding"], "base64")
        records = {item["task_id"]: item for item in evidence["results"]}
        for task_id, (commit, base, expected_digest) in self._TASKS.items():
            with self.subTest(task_id=task_id):
                self.assertIn(commit, document)
                self.assertIn(base, document)
                self.assertIn(expected_digest, document)
                record = records[task_id]
                self.assertEqual(record["source_commit"], commit)
                self.assertEqual(record["base_sha"], base)
                result = base64.b64decode(record["result_base64"], validate=True)
                self.assertEqual(
                    hashlib.sha256(result).hexdigest(), expected_digest
                )


class ReleaseOperationsDocumentationTests(unittest.TestCase):
    def test_install_recipe_has_two_hloop_targets_and_safe_staging_roots(self):
        instructions = (
            SKILL_ROOT / "references/migration-install.md"
        ).read_text(encoding="utf-8")
        heading = "## HLoop-only install"
        self.assertIn(heading, instructions)
        section = instructions.split(heading, 1)[1].split("\n## ", 1)[0]

        for name in (
            "CODEX_CONFIG_ROOT",
            "CLAUDE_CONFIG_ROOT",
            "CODEX_SKILLS_ROOT",
            "CLAUDE_SKILLS_ROOT",
            "CODEX_SKILL_DIR",
            "CLAUDE_SKILL_DIR",
            "CODEX_BACKUP_ROOT",
            "CLAUDE_BACKUP_ROOT",
            "CODEX_STAGE_ROOT",
            "CLAUDE_STAGE_ROOT",
            "CODEX_SKILL_BACKUP",
            "CLAUDE_SKILL_BACKUP",
            "DESTINATIONS=(",
            "STAGED=(",
            "OLD=(",
            "FAILED=(",
            "rollback_partial_install()",
        ):
            with self.subTest(marker=name):
                self.assertIn(name, section)
        self.assertNotIn("COMPANION", section.upper())
        self.assertNotIn("external-review", section)
        self.assertIn('CODEX_BACKUP_ROOT="${CODEX_CONFIG_ROOT}/skill-backups/codex/${STAMP}"', section)
        self.assertIn('CLAUDE_BACKUP_ROOT="${CLAUDE_CONFIG_ROOT}/skill-backups/claude/${STAMP}"', section)
        self.assertIn('CODEX_STAGE_ROOT="${CODEX_CONFIG_ROOT}/.hloop-install-stage-${STAMP}"', section)
        self.assertIn('CLAUDE_STAGE_ROOT="${CLAUDE_CONFIG_ROOT}/.hloop-install-stage-${STAMP}"', section)
        self.assertIn("selftest", section)
        self.assertIn("scripts/hloop", section)
        self.assertIn("CODEX_SKILL_BACKUP", section)
        self.assertIn("CLAUDE_SKILL_BACKUP", section)
        for array_name in ("DESTINATIONS", "STAGED", "OLD", "FAILED"):
            match = re.search(
                rf"(?m)^{array_name}=\(([^)]*)\)", section, flags=re.DOTALL
            )
            self.assertIsNotNone(match, array_name)
            self.assertEqual(len(re.findall(r'"[^"\n]+"', match.group(1))), 2)
        destinations = re.search(
            r"(?m)^DESTINATIONS=\(([^)]*)\)", section, flags=re.DOTALL
        )
        self.assertEqual(
            re.findall(r'"([^"\n]+)"', destinations.group(1)),
            ["$CODEX_SKILL_DIR", "$CLAUDE_SKILL_DIR"],
        )

    def test_historical_release_notes_are_marked_as_historical(self):
        release_note = (
            SKILL_ROOT / "docs/2026-07-17-v0.5.3-release-notes.md"
        ).read_text(encoding="utf-8").lower()
        self.assertTrue(
            "historical" in release_note or "history" in release_note,
            "the old release note must not remain current install/default authority",
        )


if __name__ == "__main__":
    unittest.main()
