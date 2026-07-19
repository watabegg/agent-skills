"""Runtime integration tests for canonical config and evidence identity."""

from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_config_runtime_v053", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class AgentIdentityRuntimeV053Tests(unittest.TestCase):
    def test_requested_values_are_not_copied_into_observed_or_attested(self):
        requested = {"provider": "codex", "model": "gpt-5.6-sol", "effort": "max"}

        projection = hloop.agent_identity_record(requested)

        self.assertEqual(projection["status"], "requested-only")
        self.assertEqual(
            projection["observed"],
            {"provider": "unavailable", "model": "unavailable", "effort": "unavailable"},
        )
        self.assertEqual(projection["observed"], projection["attested"])
        self.assertFalse(projection["verified"])

    def test_matching_attestation_is_verified_and_auto_request_accepts_observation(self):
        concrete = {"provider": "codex", "model": "gpt-5.6-sol", "effort": "max"}
        attested = hloop.agent_identity_record(
            concrete, observed=concrete, attested=concrete
        )
        auto = hloop.agent_identity_record(
            {**concrete, "model": "auto"}, observed=concrete, attested=concrete
        )

        self.assertEqual(attested["status"], "attested")
        self.assertTrue(attested["verified"])
        self.assertEqual(auto["status"], "attested")
        self.assertTrue(auto["verified"])

    def test_manager_assertion_warns_on_unavailable_but_rejects_mismatch(self):
        resolved = copy.deepcopy(hloop.BUILT_IN_CONFIG_DEFAULTS)
        state = {"resolved_config": resolved}
        with mock.patch.dict(os.environ, {}, clear=True):
            projection = hloop.assert_manager_identity(state)
        self.assertEqual(projection["assertion"], "unavailable-warning")

        exposed = {
            "HLOOP_MANAGER_PROVIDER": "claude",
            "HLOOP_MANAGER_MODEL": resolved["manager"]["model"],
            "HLOOP_MANAGER_REASONING_EFFORT": resolved["manager"]["effort"],
        }
        with mock.patch.dict(os.environ, exposed, clear=True), self.assertRaisesRegex(
            hloop.HLoopError, "does not match"
        ):
            hloop.assert_manager_identity({"resolved_config": resolved})

        strict = copy.deepcopy(resolved)
        strict["manager"]["identity_policy"] = "strict"
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            hloop.HLoopError, "unavailable under strict"
        ):
            hloop.assert_manager_identity({"resolved_config": strict})


class ConfigProjectionRuntimeV053Tests(unittest.TestCase):
    def canonical_state(self) -> dict:
        state: dict = {}
        hloop.apply_resolved_config_to_state(
            state, copy.deepcopy(hloop.BUILT_IN_CONFIG_DEFAULTS), None
        )
        state["schema_revision"] = hloop.STATE_SCHEMA_REVISION
        return state

    def test_runtime_projects_all_roles_and_three_distinct_protocol_keys(self):
        state = self.canonical_state()

        for role in hloop.hloop_config.CONFIG_ROLE_NAMES:
            if role == "manager":
                continue
            self.assertEqual(
                state[f"{role}_agent_model"],
                hloop.BUILT_IN_CONFIG_DEFAULTS[role]["model"],
            )
        selections = state["review_protocol_selection"]
        self.assertEqual(
            [selections[kind]["key"] for kind in ("ordinary", "pre-final", "manual-final")],
            [
                "reviewer.protocol",
                "review.pre_final_protocol",
                "review.manual_final_protocol",
            ],
        )
        self.assertFalse(state["config_identity_projection"]["stale"])

    def test_config_init_template_contains_the_complete_canonical_defaults(self):
        rendered = tomllib.loads(hloop.CONFIG_TEMPLATE)["defaults"]
        hloop.hloop_config.validate_config(
            {"version": 1, "defaults": rendered}
        )

        self.assertEqual(rendered, hloop.BUILT_IN_CONFIG_DEFAULTS)

    def test_fresh_init_without_protocol_override_uses_v053_reviewer_topology(self):
        args = hloop.build_parser().parse_args(["init", "--goal", "fresh-v053"])
        self.assertIsNone(args.review_protocol)
        self.assertNotIn(
            "protocol", (hloop.init_config_override(args).get("reviewer") or {})
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            hloop.hloop_config, "find_config_file", return_value=None
        ):
            resolution, candidate = hloop.resolve_init_config(args, Path(directory))

        self.assertIsNone(candidate)
        self.assertEqual(
            resolution.get("reviewer", "protocol"), "codex-review-multi-v2"
        )
        self.assertEqual(resolution.get("reviewer", "lane_count"), 6)

    def test_explicit_native_init_protocol_remains_an_override(self):
        args = hloop.build_parser().parse_args(
            ["init", "--goal", "fresh-native", "--review-protocol", "native"]
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            hloop.hloop_config, "find_config_file", return_value=None
        ):
            resolution, _candidate = hloop.resolve_init_config(args, Path(directory))

        self.assertEqual(resolution.get("reviewer", "protocol"), "native")
        self.assertEqual(resolution.get("reviewer", "lane_count"), 6)

    def test_unknown_config_leaf_marks_validation_and_audit_identity_stale(self):
        state = self.canonical_state()
        state["resolved_config"]["future"] = {"toggle": True}

        audit = hloop.build_audit_identity(state)
        projection = hloop.hloop_validation_identity.project_config_identities(
            state["resolved_config"], schema_revision=3
        )

        self.assertTrue(audit["stale"])
        self.assertTrue(projection.stale)
        self.assertIn("unclassified-config-leaf:future.toggle", audit["stale_reasons"])

        current_identity = {
            "target_sha": "a" * 40,
            "commands": ["test"],
            "dependency_identity": "same",
            "stale": True,
            "stale_reasons": ["unclassified-config-leaf:future.toggle"],
        }
        evidence_state = {
            "integration_branch": "main",
            "validation_commands": ["test"],
            "validation_stale": False,
            "last_validation": {
                "validation_identity": current_identity,
                "results": [{"result": "passed"}],
            },
        }
        self.assertFalse(hloop._validation_reusable(evidence_state, current_identity))
        with mock.patch.object(hloop, "git", return_value="a" * 40), mock.patch.object(
            hloop, "build_validation_identity", return_value=current_identity
        ):
            hloop._refresh_validation_staleness(Path("/unused"), evidence_state)
        self.assertTrue(evidence_state["validation_stale"])
        self.assertIn("future.toggle", evidence_state["validation_stale_reason"])

    def test_role_routing_changes_audit_but_not_validation_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            state = self.canonical_state()
            before_validation = hloop.build_validation_identity(
                repo, state, "a" * 40, ["python3 -m unittest"]
            )
            before_audit = hloop.build_audit_identity(state, target_sha="a" * 40)
            changed = copy.deepcopy(state)
            changed["resolved_config"]["reviewer"]["model"] = "gpt-5.6-luna"
            hloop.apply_resolved_config_to_state(
                changed, changed["resolved_config"], None
            )
            changed["schema_revision"] = 3
            after_validation = hloop.build_validation_identity(
                repo, changed, "a" * 40, ["python3 -m unittest"]
            )
            after_audit = hloop.build_audit_identity(changed, target_sha="a" * 40)

        self.assertEqual(
            before_validation["dependency_identity"],
            after_validation["dependency_identity"],
        )
        self.assertNotEqual(before_audit["digest"], after_audit["digest"])


if __name__ == "__main__":
    unittest.main()
