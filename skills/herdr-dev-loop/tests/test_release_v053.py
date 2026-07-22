"""Release-contract checks for herdr-dev-loop 0.5.3."""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib import config  # noqa: E402
from hloop_lib.release_dependency import (  # noqa: E402
    ReleaseDependencyError,
    ReleaseDependencyUnavailable,
    sha256_tree_v1,
    provider_companion_root,
    validate_release_distribution,
    validate_provider_distribution,
    validate_release_dependencies,
)
from hloop_lib.review import (  # noqa: E402
    ExternalReviewProtocolAdapter,
    ReviewModelError,
)


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

    def test_protocol_docs_distinguish_execution_kind_defaults(self):
        documents = {
            relative_path: (SKILL_ROOT / relative_path).read_text(encoding="utf-8")
            for relative_path in (
                "SKILL.md",
                "README.md",
                "references/cli-notes.md",
                "references/manager-loop.md",
                "references/reviewer-contract.md",
                "references/state-machine.md",
            )
        }
        expected_semantics = {
            "SKILL.md": (
                "Fresh 0.5.3 defaults set ordinary `reviewer.protocol`, `review.pre_final_protocol`, and `review.manual_final_protocol` to `$codex-review-multi-v2` and use the canonical six-lane Reviewer topology.",
                "`--review-protocol native` changes only ordinary review.",
                "To select the supported native pre-final path, set `pre_final_protocol = \"native\"` separately in `[defaults.review]` or a matching scope.",
                "Manual-final has no native override: `manual_final_protocol` accepts only `codex-review-multi-v2`.",
            ),
            "README.md": (
                "新規0.5.3 loopではordinary review、pre-final、manual-finalがすべて`$codex-review-multi-v2`を既定にし、canonicalなReviewer topologyは6 laneです。",
                "`--review-protocol native`はordinary reviewだけを変更します。",
                "pre-finalのnative pathは`pre_final_protocol = \"native\"`で別途選択できます。",
                "manual-finalにnative overrideはなく、`manual_final_protocol`は`codex-review-multi-v2`だけを受理します。",
            ),
            "references/cli-notes.md": (
                "Fresh 0.5.3 defaults set ordinary `reviewer.protocol`, `review.pre_final_protocol`, and `review.manual_final_protocol` to `$codex-review-multi-v2` with the canonical six-lane Reviewer topology.",
                "`--review-protocol native` changes only ordinary review.",
                "The supported native pre-final path is selected separately with `pre_final_protocol = \"native\"` in `[defaults.review]` or a matching scope.",
                "Manual-final has no native override and accepts only `codex-review-multi-v2`.",
            ),
            "references/manager-loop.md": (
                "Fresh 0.5.3 ordinary review defaults to `reviewer.protocol = \"codex-review-multi-v2\"` with the canonical six-lane Reviewer topology.",
                "`--review-protocol native` is an explicit override for ordinary review only.",
                "Select the supported native pre-final path separately with `[defaults.review] pre_final_protocol = \"native\"`.",
                "Manual-final has no native override; `manual_final_protocol` accepts only `codex-review-multi-v2`.",
            ),
            "references/reviewer-contract.md": (
                "Fresh 0.5.3 ordinary review defaults to `reviewer.protocol = \"codex-review-multi-v2\"` with the canonical six-lane Reviewer topology.",
                "`--review-protocol native` is an explicit override for ordinary review only.",
                "Select the supported native pre-final path separately with `[defaults.review] pre_final_protocol = \"native\"`.",
                "Manual-final has no native override; `manual_final_protocol` accepts only `codex-review-multi-v2`.",
            ),
            "references/state-machine.md": (
                "Fresh 0.5.3 ordinary review defaults to `reviewer.protocol = \"codex-review-multi-v2\"` with the canonical six-lane Reviewer topology.",
                "`--review-protocol native` is an explicit override for ordinary review only.",
                "Select the supported native pre-final path separately with `[defaults.review] pre_final_protocol = \"native\"`.",
                "Manual-final has no native override; `manual_final_protocol` accepts only `codex-review-multi-v2`.",
            ),
        }
        for relative_path, expected_fragments in expected_semantics.items():
            with self.subTest(relative_path=relative_path):
                for expected in expected_fragments:
                    self.assertIn(expected, documents[relative_path])
        combined = "\n".join(documents.values())
        self.assertNotIn(
            "optional compatibility protocols, not default dependencies",
            combined,
        )
        self.assertNotIn(
            "`$codex-impl` と `$codex-review-multi-v2` は互換protocolで、通常の既定値ではありません。",
            combined,
        )
        self.assertNotIn(
            "optional compatibility skills; native HLoop Worker and Reviewer protocols do not require them",
            combined,
        )
        self.assertNotIn(
            "follow the HLoop Native Review Protocol by default",
            documents["references/reviewer-contract.md"],
        )
        self.assertNotIn(
            "Reviewer protocol: default `native`",
            documents["references/manager-loop.md"],
        )


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
            shutil.copytree(
                SKILL_ROOT.parent / "codex-review-multi-v2",
                copied_skill.parent / "codex-review-multi-v2",
            )
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


class CompanionDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = json.loads((SKILL_ROOT / "release-dependencies.json").read_text())

    def unavailable_record(self) -> dict:
        record = copy.deepcopy(self.record)
        record["release"]["release_ready"] = False
        dependency = record["dependencies"][0]
        dependency.update(
            {
                "availability": "unavailable",
                "blocking_reason": "immutable companion distribution is unavailable",
                "minimum_compatible_version": None,
                "distribution_identity": None,
            }
        )
        dependency["capability_manifest"]["relative_path"] = None
        return record

    def test_shipped_companion_matches_the_complete_immutable_pin(self):
        adapter = validate_release_dependencies(self.record)
        dependency = self.record["dependencies"][0]
        self.assertTrue(self.record["release"]["release_ready"])
        self.assertEqual(dependency["availability"], "available")
        self.assertEqual(
            adapter.source,
            "https://github.com/watabegg/agent-skills.git#sha256-tree-v1="
            "b61a068af3e018e0597f1ce1e9dd242efc7580b96305bbb8a803161d69478fac",
        )
        self.assertEqual(adapter.version, "2.1.1")
        self.assertEqual(
            adapter.content_digest,
            "sha256:b61a068af3e018e0597f1ce1e9dd242efc7580b96305bbb8a803161d69478fac",
        )
        companion_root = SKILL_ROOT.parent / "codex-review-multi-v2"
        self.assertEqual(
            sha256_tree_v1(
                companion_root,
                capability_manifest_relative_path=dependency["capability_manifest"][
                    "relative_path"
                ],
            ),
            adapter.content_digest,
        )
        self.assertEqual(
            validate_release_distribution(
                SKILL_ROOT / "release-dependencies.json", companion_root
            ),
            adapter,
        )

    def test_provider_distribution_validation_uses_real_discovery_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex_root = home / "codex-profile"
            claude_root = home / "claude-profile"
            environment = {
                "HOME": str(home),
                "CODEX_HOME": str(codex_root),
                "CLAUDE_CONFIG_DIR": str(claude_root),
            }
            source = SKILL_ROOT.parent / "codex-review-multi-v2"
            for root in (codex_root, claude_root):
                shutil.copytree(
                    source,
                    root / "skills" / "codex-review-multi-v2",
                )
            release_path = SKILL_ROOT / "release-dependencies.json"
            for provider, config_root in (
                ("codex", codex_root),
                ("claude", claude_root),
            ):
                with self.subTest(provider=provider):
                    expected_root = config_root / "skills" / "codex-review-multi-v2"
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
                    self.assertEqual(
                        adapter, validate_release_dependencies(self.record)
                    )

            codex_distribution = (
                codex_root / "skills" / "codex-review-multi-v2"
            )
            shutil.rmtree(codex_distribution)
            codex_distribution.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ReleaseDependencyError, "symlink"):
                validate_provider_distribution(
                    release_path,
                    "codex",
                    environ=environment,
                )

            (claude_root / "skills" / "codex-review-multi-v2" / "SKILL.md").write_text(
                "drift\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseDependencyError, "digest"):
                validate_provider_distribution(
                    release_path,
                    "claude",
                    environ=environment,
                )

    def test_immutable_pin_archive_reconstructs_a_valid_distribution(self):
        dependency = self.record["dependencies"][0]
        immutable_id = dependency["distribution_identity"]["immutable_id"]
        repository_root = SKILL_ROOT.parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "companion.tar"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository_root),
                    "archive",
                    "--format=tar",
                    f"--output={archive}",
                    immutable_id,
                    "skills/codex-review-multi-v2",
                ],
                check=True,
                capture_output=True,
            )
            extracted = root / "extracted"
            extracted.mkdir()
            shutil.unpack_archive(str(archive), str(extracted))
            adapter = validate_release_distribution(
                SKILL_ROOT / "release-dependencies.json",
                extracted / "skills/codex-review-multi-v2",
            )
            self.assertEqual(adapter, validate_release_dependencies(self.record))

    def test_unavailable_companion_blocks_the_release_without_placeholders(self):
        unavailable = self.unavailable_record()
        dependency = unavailable["dependencies"][0]
        self.assertFalse(unavailable["release"]["release_ready"])
        self.assertEqual(dependency["availability"], "unavailable")
        self.assertIsNone(dependency["minimum_compatible_version"])
        self.assertIsNone(dependency["distribution_identity"])
        self.assertIsNone(dependency["capability_manifest"]["relative_path"])
        with self.assertRaisesRegex(ReleaseDependencyUnavailable, "release_ready"):
            validate_release_dependencies(unavailable)

        placeholder = copy.deepcopy(unavailable)
        placeholder["dependencies"][0]["distribution_identity"] = {
            "source": "mutable-installed-copy"
        }
        with self.assertRaisesRegex(ValueError, "cannot claim a distribution identity"):
            validate_release_dependencies(placeholder)

    def test_schema_version_requires_exact_json_integer_one(self):
        validate_release_dependencies(copy.deepcopy(self.record))
        for value in (True, False, 1.0, "1"):
            with self.subTest(value=value, value_type=type(value).__name__):
                record = copy.deepcopy(self.record)
                record["schema_version"] = value
                with self.assertRaisesRegex(
                    ReleaseDependencyError, "schema_version"
                ):
                    validate_release_dependencies(record)

    def test_distribution_validation_rejects_payload_manifest_and_symlink_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency_path = root / "release-dependencies.json"
            dependency_path.write_text(
                json.dumps(self.record), encoding="utf-8"
            )
            companion_root = root / "codex-review-multi-v2"
            shutil.copytree(SKILL_ROOT.parent / "codex-review-multi-v2", companion_root)

            skill_path = companion_root / "SKILL.md"
            original_skill = skill_path.read_bytes()
            skill_path.write_bytes(original_skill + b"\n# drift\n")
            with self.assertRaisesRegex(ReleaseDependencyError, "digest"):
                validate_release_distribution(dependency_path, companion_root)
            skill_path.write_bytes(original_skill)

            manifest_path = (
                companion_root / "capabilities" / "externally-planned-v1.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "2.1.2"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseDependencyError, "version"):
                validate_release_distribution(dependency_path, companion_root)
            shutil.copy2(
                SKILL_ROOT.parent
                / "codex-review-multi-v2"
                / "capabilities"
                / "externally-planned-v1.json",
                manifest_path,
            )

            (companion_root / "unexpected-link").symlink_to(skill_path)
            with self.assertRaisesRegex(ReleaseDependencyError, "symlink"):
                validate_release_distribution(dependency_path, companion_root)
            (companion_root / "unexpected-link").unlink()

            cache_dir = companion_root / "assets" / "__pycache__"
            cache_dir.mkdir()
            (cache_dir / "render_review.cpython-311.pyc").write_bytes(b"executable")
            with self.assertRaisesRegex(ReleaseDependencyError, "forbidden"):
                validate_release_distribution(dependency_path, companion_root)

    def test_distribution_validation_rejects_unreadable_and_non_utf8_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency_path = root / "release-dependencies.json"
            dependency_path.write_text(json.dumps(self.record), encoding="utf-8")
            companion_root = root / "codex-review-multi-v2"
            shutil.copytree(SKILL_ROOT.parent / "codex-review-multi-v2", companion_root)

            opaque = companion_root / "opaque-extra"
            opaque.mkdir()
            (opaque / "untrusted.py").write_text("raise SystemExit\n", encoding="utf-8")
            opaque.chmod(0)
            try:
                with self.assertRaisesRegex(
                    ReleaseDependencyError, "enumerate|digest"
                ):
                    validate_release_distribution(dependency_path, companion_root)
            finally:
                opaque.chmod(0o700)
            shutil.rmtree(opaque)

            encoded_root = os.fsencode(companion_root)
            invalid_name = encoded_root + b"/bad-\xff.py"
            fd = os.open(invalid_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            try:
                with self.assertRaisesRegex(ReleaseDependencyError, "UTF-8"):
                    validate_release_distribution(dependency_path, companion_root)
            finally:
                os.unlink(invalid_name)

            manifest_path = (
                companion_root / "capabilities" / "externally-planned-v1.json"
            )
            original_manifest = manifest_path.read_bytes()
            manifest_path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ReleaseDependencyError, "cannot load"):
                validate_release_distribution(dependency_path, companion_root)
            manifest_path.write_bytes(original_manifest)

            dependency_path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ReleaseDependencyError, "cannot load"):
                validate_release_distribution(dependency_path, companion_root)

    def test_complete_pin_produces_exact_runtime_adapter(self):
        record = copy.deepcopy(self.record)
        record["release"]["release_ready"] = True
        dependency = record["dependencies"][0]
        dependency.update(
            {
                "availability": "available",
                "blocking_reason": "",
                "minimum_compatible_version": "2.1.0",
                "distribution_identity": {
                    "source": "https://example.invalid/codex-review-multi-v2.git",
                    "immutable_id": "a" * 40,
                    "version": "2.1.0",
                    "digest_algorithm": "sha256-tree-v1",
                    "content_digest": "sha256:" + "b" * 64,
                },
            }
        )
        dependency["capability_manifest"]["relative_path"] = (
            "capabilities/externally-planned-v1.json"
        )
        adapter = validate_release_dependencies(record)
        self.assertEqual(adapter.version, "2.1.0")
        self.assertEqual(adapter.capabilities, ("externally-planned-v1",))
        self.assertEqual(adapter.content_digest, "sha256:" + "b" * 64)

    def test_available_pin_enforces_semantic_version_lower_bound(self):
        for exact, expected in (
            ("2.0.9", "below minimum"),
            ("2.1.0", None),
            ("2.2.0", None),
        ):
            with self.subTest(exact=exact):
                record = copy.deepcopy(self.record)
                record["release"]["release_ready"] = True
                dependency = record["dependencies"][0]
                dependency.update(
                    {
                        "availability": "available",
                        "blocking_reason": "",
                        "minimum_compatible_version": "2.1.0",
                        "distribution_identity": {
                            "source": "https://example.invalid/review.git",
                            "immutable_id": "a" * 40,
                            "version": exact,
                            "digest_algorithm": "sha256-tree-v1",
                            "content_digest": "sha256:" + "b" * 64,
                        },
                    }
                )
                dependency["capability_manifest"]["relative_path"] = (
                    "capabilities/externally-planned-v1.json"
                )
                if expected:
                    with self.assertRaisesRegex(ReleaseDependencyError, expected):
                        validate_release_dependencies(record)
                else:
                    self.assertEqual(validate_release_dependencies(record).version, exact)

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
                record = copy.deepcopy(self.record)
                record["release"]["release_ready"] = True
                dependency = record["dependencies"][0]
                dependency.update(
                    {
                        "availability": "available",
                        "blocking_reason": "",
                        "minimum_compatible_version": (
                            value if field == "minimum" else "2.1.0"
                        ),
                        "distribution_identity": {
                            "source": "https://example.invalid/review.git",
                            "immutable_id": "a" * 40,
                            "version": value if field == "exact" else "2.1.0",
                            "digest_algorithm": "sha256-tree-v1",
                            "content_digest": "sha256:" + "b" * 64,
                        },
                    }
                )
                dependency["capability_manifest"]["relative_path"] = (
                    "capabilities/externally-planned-v1.json"
                )
                with self.assertRaisesRegex(ReleaseDependencyError, "invalid"):
                    validate_release_dependencies(record)

    def test_available_pin_requires_an_exact_commit_sha(self):
        for immutable_id in ("master", "A" * 40, "a" * 39, "a" * 41):
            with self.subTest(immutable_id=immutable_id):
                record = copy.deepcopy(self.record)
                record["dependencies"][0]["distribution_identity"][
                    "immutable_id"
                ] = immutable_id
                with self.assertRaisesRegex(ReleaseDependencyError, "commit SHA"):
                    validate_release_dependencies(record)

    def test_runtime_adapter_rejects_missing_capability_and_bad_digest(self):
        base = {
            "record_type": "external_review_protocol_adapter",
            "protocol": "codex-review-multi-v2",
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
    def test_install_and_restore_cover_hloop_and_companion_for_both_providers(self):
        instructions = (
            SKILL_ROOT / "references/migration-install.md"
        ).read_text(encoding="utf-8")
        destinations = (
            "CODEX_SKILL_DIR",
            "CLAUDE_SKILL_DIR",
            "CODEX_COMPANION_DIR",
            "CLAUDE_COMPANION_DIR",
        )
        for destination in destinations:
            with self.subTest(destination=destination):
                self.assertIn(f'{destination}="', instructions)
                self.assertIn(f'${destination}\" || cp -a', instructions)
        for backup in (
            "CODEX_SKILL_BACKUP",
            "CLAUDE_SKILL_BACKUP",
            "CODEX_COMPANION_BACKUP",
            "CLAUDE_COMPANION_BACKUP",
        ):
            with self.subTest(backup=backup):
                self.assertIn(f'{backup}="', instructions)
                self.assertIn(f'${backup}\" || cp -a', instructions)
        self.assertIn(
            'CLAUDE_BACKUP_ROOT="$(dirname "$CLAUDE_SKILLS_ROOT")/skill-backups/claude/${STAMP}"',
            instructions,
        )
        self.assertIn(
            'CLAUDE_SKILLS_ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"',
            instructions,
        )
        self.assertIn("unsafe {label} overlap", instructions)
        self.assertIn("source/install overlap", instructions)
        self.assertIn("backup/discovery overlap", instructions)
        self.assertIn("staging/install overlap", instructions)
        self.assertIn('test ! -e "$BACKUP"', instructions)
        self.assertIn("archive_legacy_discovery_backups", instructions)
        self.assertIn("rollback_partial_install", instructions)
        self.assertIn("trap 'rollback_partial_install 130' INT", instructions)
        self.assertIn("trap 'rollback_partial_install 143' TERM", instructions)
        self.assertIn("STAGED=(", instructions)
        self.assertIn("install-transaction", instructions)
        self.assertIn("sys.version_info < (3, 11)", instructions)
        self.assertIn("import tomllib", instructions)
        self.assertIn("herdr-dev-loop.failed-*", instructions)
        self.assertIn("codex-review-multi-v2.failed-*", instructions)
        self.assertIn('test ! -L "$DESTINATION"', instructions)

    def test_migration_docs_distinguish_recovery_and_committed_rollback(self):
        instructions = (
            SKILL_ROOT / "references/migration-install.md"
        ).read_text(encoding="utf-8")
        release_note = (
            SKILL_ROOT / "docs/2026-07-17-v0.5.3-release-notes.md"
        ).read_text(encoding="utf-8")
        for expected in (
            "Prepared/partial recovery rollback",
            "Committed pre-first-mutation rollback",
            "rollback-prepared",
            "first_v053_mutation_at",
            "first_v053_mutation_command",
        ):
            self.assertIn(expected, instructions)
        self.assertIn("prepared/running", release_note)
        self.assertIn("committed", release_note)


if __name__ == "__main__":
    unittest.main()
