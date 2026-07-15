from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional for minimal skill installs
    jsonschema = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.config import (  # noqa: E402
    ConfigValidationError,
    REVIEW_POLICY_DEFAULTS,
    load_config_file,
    resolve_config,
    validate_config,
)
from hloop_lib.migration import (  # noqa: E402
    FORMAT_3_REVISION_2_MIGRATION,
    V052_STATE_SCHEMA_VERSION,
    migrate_format_three_revision_two,
    migrate_schema,
)


class ReviewPolicyConfigTests(unittest.TestCase):
    def valid_config(self) -> dict:
        return {
            "version": 1,
            "defaults": {
                "review": dict(REVIEW_POLICY_DEFAULTS),
            },
        }

    def test_approved_batch_defaults_validate_and_resolve(self):
        data = self.valid_config()
        validate_config(data)

        resolved = resolve_config(
            {"review": {"cadence": "batch", "max_fix_rounds": 2}},
            data,
        )
        self.assertEqual(resolved.get("review", "cadence"), "batch")
        self.assertEqual(
            resolved.get("review", "pre_final_protocol"),
            "codex-review-multi-v2",
        )
        self.assertEqual(resolved.get("review", "max_fix_rounds"), 2)
        self.assertEqual(
            resolved.source_of("review", "cadence"),
            "config-defaults",
        )

    def test_review_policy_can_be_overridden_by_a_matching_scope(self):
        data = self.valid_config()
        data["scope"] = [
            {
                "path": str(Path.cwd()),
                "review": {"cadence": "merge-count", "lane_count": 6},
            }
        ]
        resolved = resolve_config(
            {"review": dict(REVIEW_POLICY_DEFAULTS)},
            data,
            target_dir=Path.cwd(),
        )
        self.assertEqual(resolved.get("review", "cadence"), "merge-count")
        self.assertEqual(resolved.get("review", "lane_count"), 6)

    def test_review_policy_rejects_unknown_or_unsafe_values(self):
        cases = (
            ({"cadence": "per-merge"}, "cadence"),
            ({"pre_final_protocol": "chat"}, "pre_final_protocol"),
            ({"max_fix_rounds": 3}, "max_fix_rounds"),
            ({"scope_expansion_action": "fix_now"}, "scope_expansion_action"),
            ({"final_required": "finding_count_only"}, "final_required"),
            ({"lane_count": 3}, "lane_count"),
        )
        for update, field in cases:
            with self.subTest(field=field):
                data = self.valid_config()
                data["defaults"]["review"].update(update)
                with self.assertRaisesRegex(ConfigValidationError, field):
                    validate_config(data)

    def test_example_config_contains_the_approved_review_defaults(self):
        example = load_config_file(Path(__file__).parents[1] / "examples" / "config.toml")
        validate_config(example)
        self.assertEqual(example["defaults"]["review"], REVIEW_POLICY_DEFAULTS)


def legacy_state() -> dict:
    return {
        "state_format_version": 3,
        "schema_revision": 1,
        "run_id": "run-legacy",
        "review_after_merges": 7,
        "tasks": {
            "T001": {
                "id": "T001",
                "status": "merged",
                "kind": "implementation",
            }
        },
        "nested": {"unchanged": [1, 2, 3]},
    }


class StateMigrationTests(unittest.TestCase):
    def test_format_three_revision_two_is_deterministic_and_side_effect_free(self):
        original = legacy_state()
        before = copy.deepcopy(original)

        first = migrate_schema(
            original,
            target=V052_STATE_SCHEMA_VERSION,
            steps=(FORMAT_3_REVISION_2_MIGRATION,),
        )
        second = migrate_schema(
            original,
            target=V052_STATE_SCHEMA_VERSION,
            steps=(FORMAT_3_REVISION_2_MIGRATION,),
        )

        self.assertEqual(original, before)
        self.assertEqual(first.state, second.state)
        self.assertEqual(
            (first.state["state_format_version"], first.state["schema_revision"]),
            (3, 2),
        )
        self.assertEqual(first.applied_steps, ("format-3-revision-2",))

    def test_legacy_cadence_and_finish_requirements_are_preserved(self):
        result = migrate_format_three_revision_two(legacy_state())

        self.assertEqual(result["review_after_merges"], 7)
        self.assertEqual(result["review_policy"]["cadence"], "merge-count")
        self.assertEqual(result["review_policy"]["max_fix_rounds"], 2)
        self.assertEqual(result["review_policy"]["scope_expansion_action"], "follow_up")
        self.assertEqual(
            result["manual_final_review"]["status"],
            "not-required-for-legacy-run",
        )
        self.assertEqual(result["release_scope"]["status"], "legacy-unlocked")
        self.assertEqual(result["release_scope"]["scope_revision"], 0)
        self.assertEqual(result["release_scope"]["source_snapshot_revision"], 0)
        self.assertEqual(result["dispatch_freeze"]["status"], "inactive")

    def test_legacy_task_and_follow_up_inventories_start_unclassified_and_empty(self):
        result = migrate_format_three_revision_two(legacy_state())
        task = result["tasks"]["T001"]

        self.assertEqual(task["task_origin"], "legacy-unclassified")
        self.assertEqual(task["release_scope_revision"], 0)
        self.assertEqual(result["follow_ups"]["next_id"], 1)
        self.assertEqual(result["follow_ups"]["open_count"], 0)
        self.assertEqual(result["follow_ups"]["issue_keys"], {})
        self.assertEqual(result["follow_ups"]["issue_key_aliases"], {})

    def test_new_audit_state_blocks_are_initialized(self):
        result = migrate_format_three_revision_two(legacy_state())

        self.assertEqual(result["review_convergence"]["status"], "not-started")
        self.assertEqual(result["review_convergence"]["authorized_extra_rounds"], 0)
        self.assertIn("manager_invocation", result)
        self.assertIn("execution_metrics", result)
        self.assertEqual(result["execution_metrics"]["review_fix_rounds"], 0)
        self.assertIsNone(result["execution_metrics"]["effective_parallelism"])


def current_state_with_v052_blocks() -> dict:
    state = {
        "state_format_version": 3,
        "schema_revision": 2,
        "goal_id": "bounded-review-convergence",
        "run_id": "run-1",
        "skill_version": "0.5.2",
        "namespace": "bounded-review-convergence-052",
        "loop_path": ".ai/herdr-dev-loop/loops/bounded-review-convergence-052",
        "persistence": "local-only",
        "worktree_setup_commands": [],
        "worker_setup_commands": [],
        "reviewer_setup_commands": [],
        "gap_setup_commands": [],
        "advisor_setup_commands": [],
        "phase": "dispatching",
        "base_branch": "master",
        "integration_branch": "feat/integration",
        "worktree_root": "/tmp/worktrees",
        "branch_strategy": "integration",
        "worker_qa_profile": "repo-default",
        "manager_qa_profile": "none",
        "manager_qa_status": "not-required",
        "worker_protocol": "native",
        "review_protocol": "native",
        "worker_agent_provider": "codex",
        "worker_agent_model": "auto",
        "worker_agent_effort": "max",
        "worker_claude_permission_mode": "auto",
        "reviewer_agent_provider": "codex",
        "reviewer_agent_model": "auto",
        "reviewer_agent_effort": "xhigh",
        "reviewer_claude_permission_mode": "auto",
        "gap_agent_provider": "codex",
        "gap_agent_model": "auto",
        "gap_agent_effort": "xhigh",
        "gap_claude_permission_mode": "auto",
        "review_lanes": [],
        "cycle": 0,
        "max_workers": 1,
        "max_reviewers": 1,
        "max_gap_auditors": 1,
        "tasks": {},
        "batches": {},
        "reviews": {},
        "gaps": {},
        "advice": {},
        "decisions": {},
    }
    state.update(migrate_format_three_revision_two(legacy_state()))
    state.update(
        {
            "state_format_version": 3,
            "schema_revision": 2,
            "goal_id": "bounded-review-convergence",
            "run_id": "run-1",
            "skill_version": "0.5.2",
            "namespace": "bounded-review-convergence-052",
            "loop_path": ".ai/herdr-dev-loop/loops/bounded-review-convergence-052",
            "persistence": "local-only",
            "worktree_setup_commands": [],
            "worker_setup_commands": [],
            "reviewer_setup_commands": [],
            "gap_setup_commands": [],
            "advisor_setup_commands": [],
            "phase": "dispatching",
            "base_branch": "master",
            "integration_branch": "feat/integration",
            "worktree_root": "/tmp/worktrees",
            "branch_strategy": "integration",
            "worker_qa_profile": "repo-default",
            "manager_qa_profile": "none",
            "manager_qa_status": "not-required",
            "worker_protocol": "native",
            "review_protocol": "native",
            "worker_agent_provider": "codex",
            "worker_agent_model": "auto",
            "worker_agent_effort": "max",
            "worker_claude_permission_mode": "auto",
            "reviewer_agent_provider": "codex",
            "reviewer_agent_model": "auto",
            "reviewer_agent_effort": "xhigh",
            "reviewer_claude_permission_mode": "auto",
            "gap_agent_provider": "codex",
            "gap_agent_model": "auto",
            "gap_agent_effort": "xhigh",
            "gap_claude_permission_mode": "auto",
            "review_lanes": [],
            "cycle": 0,
            "max_workers": 1,
            "max_reviewers": 1,
            "max_gap_auditors": 1,
            "tasks": {},
            "batches": {},
            "reviews": {},
            "gaps": {},
            "advice": {},
            "decisions": {},
        }
    )
    return state


@unittest.skipUnless(jsonschema is not None, "jsonschema is optional")
class StateSchemaTests(unittest.TestCase):
    def test_v052_state_blocks_validate_against_state_schema(self):
        schema = json.loads((SCHEMAS / "state.schema.json").read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(current_state_with_v052_blocks()))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_state_schema_rejects_a_fix_round_limit_above_two(self):
        schema = json.loads((SCHEMAS / "state.schema.json").read_text(encoding="utf-8"))
        state = current_state_with_v052_blocks()
        state["review_policy"]["max_fix_rounds"] = 3
        self.assertFalse(jsonschema.Draft202012Validator(schema).is_valid(state))


if __name__ == "__main__":
    unittest.main()
