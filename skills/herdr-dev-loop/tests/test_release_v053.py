"""Release-contract checks for herdr-dev-loop 0.5.3."""

from __future__ import annotations

import copy
import base64
import hashlib
import json
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

    def test_unavailable_companion_blocks_the_release_without_placeholders(self):
        dependency = self.record["dependencies"][0]
        self.assertFalse(self.record["release"]["release_ready"])
        self.assertEqual(dependency["availability"], "unavailable")
        self.assertIsNone(dependency["minimum_compatible_version"])
        self.assertIsNone(dependency["distribution_identity"])
        self.assertIsNone(dependency["capability_manifest"]["relative_path"])
        with self.assertRaisesRegex(ReleaseDependencyUnavailable, "release_ready"):
            validate_release_dependencies(self.record)

        placeholder = copy.deepcopy(self.record)
        placeholder["dependencies"][0]["distribution_identity"] = {
            "source": "mutable-installed-copy"
        }
        with self.assertRaisesRegex(ValueError, "cannot claim a distribution identity"):
            validate_release_dependencies(placeholder)

    def test_schema_version_requires_exact_json_integer_one(self):
        with self.assertRaisesRegex(ReleaseDependencyUnavailable, "release_ready"):
            validate_release_dependencies(copy.deepcopy(self.record))
        for value in (True, False, 1.0, "1"):
            with self.subTest(value=value, value_type=type(value).__name__):
                record = copy.deepcopy(self.record)
                record["schema_version"] = value
                with self.assertRaisesRegex(
                    ReleaseDependencyError, "schema_version"
                ):
                    validate_release_dependencies(record)

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
