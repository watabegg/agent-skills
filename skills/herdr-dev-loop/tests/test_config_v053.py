"""0.5.3 hierarchical role/protocol config contract tests."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hloop_lib import config  # noqa: E402


class ConfigSchemaV053Tests(unittest.TestCase):
    def test_complete_role_defaults_are_schema_valid(self):
        data = {"version": 1, "defaults": config.V053_BUILT_IN_CONFIG_DEFAULTS}

        config.validate_config(data)

        self.assertEqual(
            set(config.CONFIG_ROLE_NAMES),
            {
                "manager",
                "worker",
                "reviewer",
                "gap",
                "plan_gap",
                "patch_reviewer",
                "final_coordinator",
                "advisor",
            },
        )
        self.assertEqual(
            data["defaults"]["review"]["manual_final_execution"], "independent"
        )
        for role in config.COORDINATED_ROLE_NAMES:
            for component in config.COORDINATOR_COMPONENT_NAMES:
                self.assertEqual(
                    set(data["defaults"][role][component]),
                    {"provider", "model", "effort"},
                )

    def test_role_specific_invalid_values_fail_closed(self):
        cases = (
            ({"manager": {"identity_policy": "assume"}}, "identity_policy"),
            ({"reviewer": {"protocol": "manual"}}, "protocol"),
            ({"gap": {"lane_count": 2}}, "lane_count"),
            ({"reviewer": {"coordinator": {"mode": "swarm"}}}, "mode"),
            ({"audit": {"agent_budget": 0}}, "agent_budget"),
            (
                {"audit": {"max_patch_review_rounds_per_task": 3}},
                "max_patch_review_rounds_per_task",
            ),
        )
        for defaults, field in cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                config.ConfigValidationError, field
            ):
                config.validate_config({"version": 1, "defaults": defaults})

    def test_manual_final_execution_accepts_only_explicit_contract_values(self):
        for value in config.SUPPORTED_MANUAL_FINAL_EXECUTIONS:
            with self.subTest(value=value):
                config.validate_config(
                    {
                        "version": 1,
                        "defaults": {"review": {"manual_final_execution": value}},
                    }
                )
        with self.assertRaisesRegex(config.ConfigValidationError, "manual_final_execution"):
            config.validate_config(
                {
                    "version": 1,
                    "defaults": {"review": {"manual_final_execution": "implicit"}},
                }
            )


class LayerAliasNormalizationTests(unittest.TestCase):
    def test_equal_aliases_in_one_layer_collapse_to_one_canonical_leaf(self):
        layer = {
            "reviewer": {
                "lane_count": 6,
                "probe_count": 6,
                "probes_per_provider": 6,
            },
            "review": {"lane_count": 6, "cadence": "batch"},
        }
        config.validate_config({"version": 1, "defaults": layer})

        normalized = config.normalize_config_layer(layer)

        self.assertEqual(normalized["reviewer"]["lane_count"], 6)
        self.assertNotIn("probe_count", normalized["reviewer"])
        self.assertNotIn("probes_per_provider", normalized["reviewer"])
        self.assertNotIn("lane_count", normalized["review"])
        self.assertEqual(normalized["review"]["cadence"], "batch")

    def test_differing_aliases_conflict_only_inside_their_own_layer(self):
        for layer in (
            {"reviewer": {"probe_count": 6, "probes_per_provider": 4}},
            {"reviewer": {"lane_count": 6}, "review": {"lane_count": 4}},
        ):
            with self.subTest(layer=layer), self.assertRaisesRegex(
                config.ConfigValidationError, "same layer|same table"
            ):
                config.validate_config({"version": 1, "defaults": layer})

        # The same values in different layers are ordinary overrides, not a
        # cross-layer alias conflict.
        resolved = config.merge_config_layers(
            [
                ("defaults", {"reviewer": {"probe_count": 6}}),
                ("scope", {"review": {"lane_count": "auto"}}),
            ]
        )
        self.assertEqual(resolved.get("reviewer", "lane_count"), "auto")
        self.assertEqual(resolved.source_of("reviewer", "lane_count"), "scope")

    def test_legacy_probe_aliases_accept_auto_as_a_real_override(self):
        resolved = config.merge_config_layers(
            [
                ("lower", {"reviewer": {"lane_count": 8}}),
                ("higher", {"reviewer": {"probe_count": "auto"}}),
            ]
        )

        self.assertEqual(resolved.get("reviewer", "lane_count"), "auto")
        self.assertEqual(resolved.get("reviewer", "probe_count"), "auto")
        self.assertIsNone(resolved.get("reviewer", "probes_per_provider"))
        self.assertEqual(resolved.as_dict()["reviewer"], {"lane_count": "auto"})

    def test_auto_and_one_legacy_explicit_value_use_the_same_layer_fallback(self):
        resolved = config.merge_config_layers(
            [
                (
                    "defaults",
                    {
                        "reviewer": {"probe_count": 6},
                        "review": {"lane_count": "auto"},
                    },
                )
            ]
        )

        self.assertEqual(resolved.get("reviewer", "lane_count"), 6)
        self.assertEqual(resolved.get("reviewer", "probe_count"), 6)

    def test_override_layer_lane_values_are_validated(self):
        with self.assertRaisesRegex(config.ConfigValidationError, "lane_count"):
            config.merge_config_layers(
                [("start-override", {"reviewer": {"lane_count": 99}})]
            )


class HierarchicalPrecedenceV053Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / ".git").mkdir()

    def test_all_layers_share_one_precedence_and_preserve_provenance(self):
        self.assertEqual(
            config.CONFIG_PRECEDENCE,
            (
                "built-in-default",
                "config-defaults",
                "matching-scope",
                "loop-snapshot",
                "task-override",
                "start-override",
                "participant-override",
            ),
        )
        config_data = {
            "version": 1,
            "defaults": {
                "advisor": {"model": "defaults"},
                "reviewer": {"probe_count": 5},
            },
            "scope": [
                {
                    "path": str(self.repo),
                    "advisor": {"model": "scope"},
                    "review": {"lane_count": "auto"},
                }
            ],
        }
        resolved = config.resolve_config(
            {
                "advisor": {"model": "built-in"},
                "reviewer": {"lane_count": 4},
            },
            config_data,
            target_dir=self.repo,
            loop_snapshot={
                "advisor": {"model": "snapshot"},
                "reviewer": {"probes_per_provider": 6},
            },
            task_override={
                "advisor": {"model": "task"},
                "reviewer": {"lane_count": 7},
            },
            start_override={
                "advisor": {"model": "start"},
                "review": {"lane_count": 8},
            },
            participant_override={
                "advisor": {"model": "participant"},
                "reviewer": {"probe_count": 4},
            },
        )

        self.assertEqual(resolved.get("advisor", "model"), "participant")
        self.assertEqual(resolved.source_of("advisor", "model"), "participant-override")
        self.assertEqual(resolved.get("reviewer", "lane_count"), 4)
        canonical = resolved.as_dict()
        self.assertEqual(canonical["reviewer"]["lane_count"], 4)
        self.assertNotIn("lane_count", canonical.get("review", {}))

        rows = {row["key"]: row for row in resolved.explain_provenance()}
        self.assertEqual(
            [item["source"] for item in rows["advisor.model"]["provenance"]],
            [
                "built-in-default",
                "config-defaults",
                f"scope:repo:{self.repo}",
                "loop-snapshot",
                "task-override",
                "start-override",
                "participant-override",
            ],
        )
        self.assertEqual(
            [item["input_key"] for item in rows["reviewer.lane_count"]["provenance"]],
            [
                "reviewer.lane_count",
                "reviewer.probe_count",
                "review.lane_count",
                "reviewer.probes_per_provider",
                "reviewer.lane_count",
                "review.lane_count",
                "reviewer.probe_count",
            ],
        )

    def test_every_role_uses_task_start_and_participant_override_tiers(self):
        for role in config.CONFIG_ROLE_NAMES:
            with self.subTest(role=role):
                resolved = config.resolve_config(
                    {role: {"model": "built-in"}},
                    loop_snapshot={role: {"model": "snapshot"}},
                    task_override={role: {"model": "task"}},
                    start_override={role: {"model": "start"}},
                    participant_override={role: {"model": "participant"}},
                    target_dir=self.repo,
                )
                self.assertEqual(resolved.get(role, "model"), "participant")
                self.assertEqual(
                    resolved.source_of(role, "model"), "participant-override"
                )

    def test_task_start_and_participant_overrides_share_canonical_validation(self):
        override_names = (
            "task_override",
            "start_override",
            "participant_override",
        )
        invalid_layers = (
            {"audit": {"max_patch_review_rounds_per_task": 3}},
            {"patch_reviewer": {"provider": "unknown"}},
            {"unknown_config_section": {"enabled": True}},
        )
        for override_name in override_names:
            for invalid_layer in invalid_layers:
                with self.subTest(
                    override=override_name, invalid_layer=invalid_layer
                ), self.assertRaises(config.ConfigValidationError):
                    config.resolve_config(
                        config.V053_BUILT_IN_CONFIG_DEFAULTS,
                        target_dir=self.repo,
                        **{override_name: invalid_layer},
                    )

            for max_rounds in (0, 1, config.MAX_PATCH_REVIEW_ROUNDS):
                with self.subTest(
                    override=override_name, max_rounds=max_rounds
                ):
                    resolved = config.resolve_config(
                        config.V053_BUILT_IN_CONFIG_DEFAULTS,
                        target_dir=self.repo,
                        **{
                            override_name: {
                                "audit": {
                                    "max_patch_review_rounds_per_task": max_rounds
                                }
                            }
                        },
                    )
                    self.assertEqual(
                        resolved.get(
                            "audit", "max_patch_review_rounds_per_task"
                        ),
                        max_rounds,
                    )


class CanonicalProtocolSelectionTests(unittest.TestCase):
    def test_execution_kinds_read_distinct_keys_without_fallback(self):
        resolved = config.resolve_config(
            {
                "reviewer": {"protocol": "native"},
                "review": {
                    "pre_final_protocol": "codex-review-multi-v2",
                    "manual_final_protocol": "codex-review-multi-v2",
                    "manual_final_execution": "independent",
                },
            }
        )

        ordinary = config.select_review_protocol(resolved, "ordinary")
        pre_final = config.select_review_protocol(resolved, "pre-final")
        manual_final = config.select_review_protocol(resolved, "manual-final")
        self.assertEqual((ordinary.key, ordinary.protocol), ("reviewer.protocol", "native"))
        self.assertEqual(
            (pre_final.key, pre_final.protocol),
            ("review.pre_final_protocol", "codex-review-multi-v2"),
        )
        self.assertEqual(
            (manual_final.key, manual_final.protocol),
            ("review.manual_final_protocol", "codex-review-multi-v2"),
        )
        self.assertEqual(resolved.get("review", "manual_final_execution"), "independent")

        missing_manual = config.select_review_protocol(
            {"reviewer": {"protocol": "native"}}, "manual-final"
        )
        self.assertIsNone(missing_manual.protocol)

    def test_v053_defaults_make_manual_final_independent(self):
        resolved = config.resolve_config(config.V053_BUILT_IN_CONFIG_DEFAULTS)

        self.assertEqual(
            resolved.get("review", "manual_final_execution"), "independent"
        )


if __name__ == "__main__":
    unittest.main()
