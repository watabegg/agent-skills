"""0.5.3 validation/audit config identity classification tests."""

from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hloop_lib import config  # noqa: E402
from hloop_lib import validation_identity  # noqa: E402


class ClassificationRegistryTests(unittest.TestCase):
    def test_every_canonical_schema_leaf_is_classified_exactly_once(self):
        validation_identity.ensure_registry_complete()

        validation = validation_identity.VALIDATION_AFFECTING_CONFIG_LEAVES
        audit = validation_identity.AUDIT_ONLY_CONFIG_LEAVES
        self.assertFalse(validation & audit)
        self.assertEqual(validation | audit, config.CANONICAL_CONFIG_LEAF_PATHS)
        self.assertEqual(
            set(validation_identity.CONFIG_LEAF_CLASSIFICATION_REGISTRY),
            set(config.CANONICAL_CONFIG_LEAF_PATHS),
        )

    def test_registry_audit_detects_missing_overlap_and_extra_entries(self):
        issues = validation_identity.registry_issues(
            schema_leaves={"a", "b"},
            validation_affecting={"a", "outside"},
            audit_only={"a"},
        )

        self.assertEqual(
            issues,
            (
                "multiply-classified:a",
                "unclassified-schema-leaf:b",
                "classification-outside-schema:outside",
            ),
        )

    def test_migration_aliases_are_not_canonical_registry_leaves(self):
        self.assertFalse(
            config.LEGACY_CONFIG_ALIAS_PATHS
            & set(validation_identity.CONFIG_LEAF_CLASSIFICATION_REGISTRY)
        )


class ConfigIdentityProjectionTests(unittest.TestCase):
    def setUp(self):
        self.resolved = copy.deepcopy(config.V053_BUILT_IN_CONFIG_DEFAULTS)

    def project(self, value=None, **kwargs):
        return validation_identity.project_config_identities(
            self.resolved if value is None else value,
            schema_revision=kwargs.pop("schema_revision", 3),
            **kwargs,
        )

    def test_complete_canonical_config_projects_fresh(self):
        projection = self.project()

        self.assertTrue(projection.reusable)
        self.assertFalse(projection.stale)
        self.assertEqual(projection.stale_reasons, ())
        self.assertEqual(projection.validation_config, {})
        self.assertEqual(projection.unclassified_config, {})
        self.assertEqual(
            validation_identity.flatten_config_leaves(projection.audit_config),
            validation_identity.flatten_config_leaves(self.resolved),
        )

    def test_role_routing_changes_only_audit_identity(self):
        before = self.project()
        changed = copy.deepcopy(self.resolved)
        changed["reviewer"]["model"] = "gpt-5.6-luna"
        changed["reviewer"]["effort"] = "max"
        changed["reviewer"]["lane_count"] = 8
        changed["gap"]["coordinator"]["provider"] = "claude"
        changed["advisor"]["model"] = "gpt-5.6-sol"
        after = self.project(changed)

        self.assertTrue(after.reusable)
        self.assertEqual(before.validation_digest, after.validation_digest)
        self.assertNotEqual(before.audit_digest, after.audit_digest)

    def test_unknown_leaf_is_retained_and_marks_evidence_stale(self):
        changed = copy.deepcopy(self.resolved)
        changed["future"] = {"product_toggle": True}

        projection = self.project(changed)

        self.assertTrue(projection.stale)
        self.assertFalse(projection.reusable)
        self.assertIn("unclassified-config-leaf:future.product_toggle", projection.stale_reasons)
        self.assertEqual(
            projection.unclassified_config,
            {"future": {"product_toggle": True}},
        )
        self.assertIn("unclassified_config", projection.as_dict())

    def test_unknown_schema_revision_stales_without_dropping_known_leaves(self):
        projection = self.project(schema_revision=4)

        self.assertTrue(projection.stale)
        self.assertIn("unknown-schema-revision:4", projection.stale_reasons)
        self.assertEqual(
            validation_identity.flatten_config_leaves(projection.audit_config),
            validation_identity.flatten_config_leaves(self.resolved),
        )

    def test_registry_version_mismatch_is_fail_closed(self):
        projection = self.project(registry_version=999)

        self.assertTrue(projection.stale)
        self.assertIn(
            "classification-registry-version-mismatch:expected=1:actual=999",
            projection.stale_reasons,
        )

    def test_raw_legacy_alias_cannot_bypass_canonical_classification(self):
        raw = {"reviewer": {"probe_count": 6}}

        projection = self.project(raw)

        self.assertTrue(projection.stale)
        self.assertEqual(projection.unclassified_config, raw)
        self.assertIn(
            "unclassified-config-leaf:reviewer.probe_count", projection.stale_reasons
        )

    def test_digest_is_deterministic_across_mapping_order(self):
        left = {"review": {"cadence": "batch"}, "max_workers": 3}
        right = {"max_workers": 3, "review": {"cadence": "batch"}}

        left_projection = self.project(left)
        right_projection = self.project(right)

        self.assertEqual(left_projection.validation_digest, right_projection.validation_digest)
        self.assertEqual(left_projection.audit_digest, right_projection.audit_digest)


if __name__ == "__main__":
    unittest.main()
