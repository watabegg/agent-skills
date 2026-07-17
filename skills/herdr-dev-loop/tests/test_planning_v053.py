from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import unquote, urlparse

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # pragma: no cover - minimal installs fail explicitly
    jsonschema = None
    Registry = None
    Resource = None
    DRAFT202012 = None


SKILL_ROOT = Path(__file__).parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
REFERENCE_SCHEMAS = SKILL_ROOT / "references" / "schemas"
PUBLIC_SCHEMAS = SKILL_ROOT / "schemas"
sys.path.insert(0, str(SCRIPTS))

from hloop_lib.planning import (  # noqa: E402
    COVERAGE_RECORD_TYPE,
    IMPACT_RECORD_TYPE,
    PLAN_GAP_RECORD_TYPE,
    PLANNING_SCHEMA_REVISION,
    TASK_GRAPH_RECORD_TYPE,
    PlanningContractError,
    PlanningIdentity,
    artifact_digest,
    canonical_digest,
    is_planning_stale,
    seal_planning_artifact,
    validate_coverage,
    validate_dispatch_readiness,
    validate_planning_artifact,
    validate_planning_bundle,
)


def identity(**overrides: object) -> PlanningIdentity:
    values: dict[str, object] = {
        "run_id": "run-v053",
        "plan_revision": 3,
        "requirements_revision": 5,
        "release_scope_revision": 2,
        "release_scope_digest": canonical_digest({"scope": "release-v053"}),
        "source_revision": 7,
        "source_digest": canonical_digest({"sources": ["MISSION.md", "PLAN.md"]}),
    }
    values.update(overrides)
    return PlanningIdentity(**values)  # type: ignore[arg-type]


def raw_digest(record: dict) -> dict:
    record = copy.deepcopy(record)
    record.pop("artifact_digest", None)
    record["artifact_digest"] = artifact_digest(record)
    return record


def planning_bundle(
    *, second_task: bool = False
) -> tuple[PlanningIdentity, dict, dict, dict, dict]:
    current = identity()
    common = current.to_dict()
    impact = seal_planning_artifact(
        {
            **common,
            "record_type": IMPACT_RECORD_TYPE,
            "surfaces": [
                {
                    "surface_id": "planning-contracts",
                    "kind": "module",
                    "paths": ["skills/herdr-dev-loop/scripts/hloop_lib/planning.py"],
                    "symbols": ["validate_dispatch_readiness"],
                    "entry_points": ["hloop task new"],
                    "callers": ["runtime.task_creation"],
                    "consumers": ["runtime.worker_dispatch"],
                    "success_paths": ["clean planning bundle permits dispatch"],
                    "failure_paths": ["stale planning bundle blocks dispatch"],
                    "test_refs": ["test_planning_v053"],
                    "fixture_refs": ["clean-planning-bundle"],
                    "validation_commands": [
                        "python3 -m unittest "
                        "skills.herdr-dev-loop.tests.test_planning_v053"
                    ],
                    "final_evidence_refs": ["gap:planning-coverage"],
                    "requirement_refs": ["REQ-001"],
                    "plan_item_refs": ["P002"],
                    "scope_refs": ["runtime-release"],
                }
            ],
            "preserved_invariants": ["locked release scope remains authoritative"],
            "non_goals": ["generic issue tracker"],
            "shared_contracts": [
                {
                    "contract_ref": "planning-identity",
                    "surface_refs": ["planning-contracts"],
                    "invariant_refs": ["locked release scope remains authoritative"],
                }
            ],
        }
    )
    tasks = [
        {
            "task_id": "T005",
            "changes": ["implement planning contracts"],
            "change_refs": ["planning-contracts"],
            "preserved_invariants": ["locked release scope remains authoritative"],
            "write_allow": [
                "skills/herdr-dev-loop/scripts/hloop_lib/planning.py"
            ],
            "non_goals": ["generic issue tracker"],
            "acceptance": ["stale evidence blocks dispatch"],
            "regression_checks": ["test_planning_v053"],
            "depends_on": [],
            "shared_surface_refs": [],
            "risk_class": "high",
            "required_gates": ["patch_review", "full_suite"],
            "requirement_refs": ["REQ-001"],
            "plan_item_refs": ["P002"],
            "scope_refs": ["runtime-release"],
            "owner": "worker",
        }
    ]
    if second_task:
        tasks.append(
            {
                "task_id": "T007",
                "changes": ["integrate planning dispatch gate"],
                "change_refs": ["planning-contracts"],
                "preserved_invariants": [
                    "locked release scope remains authoritative"
                ],
                "write_allow": ["skills/herdr-dev-loop/scripts/hloop"],
                "non_goals": ["generic issue tracker"],
                "acceptance": ["CLI blocks stale planning evidence"],
                "regression_checks": ["test_planning_worker_cli_v053"],
                "depends_on": ["T005"],
                "shared_surface_refs": [],
                "risk_class": "high",
                "required_gates": ["patch_review", "full_suite"],
                "requirement_refs": ["REQ-001"],
                "plan_item_refs": ["P003"],
                "scope_refs": ["runtime-release"],
                "owner": "worker",
            }
        )
    task_graph = seal_planning_artifact(
        {**common, "record_type": TASK_GRAPH_RECORD_TYPE, "tasks": tasks}
    )
    coverage = seal_planning_artifact(
        {
            **common,
            "record_type": COVERAGE_RECORD_TYPE,
            "impact_map_digest": impact["artifact_digest"],
            "task_graph_digest": task_graph["artifact_digest"],
            "entries": [
                {
                    "coverage_id": "COV-001",
                    "acceptance_ref": "REQ-001:dispatch-planning",
                    "requirement_refs": ["REQ-001"],
                    "plan_item_refs": ["P002"],
                    "task_refs": ["T005"],
                    "surface_refs": ["planning-contracts"],
                    "caller_refs": ["runtime.task_creation"],
                    "consumer_refs": ["runtime.worker_dispatch"],
                    "failure_path_refs": ["stale planning bundle blocks dispatch"],
                    "test_refs": ["test_planning_v053"],
                    "final_evidence_refs": ["gap:planning-coverage"],
                    "final_evidence_owner": "gap",
                    "review_lane_refs": ["requirement-coverage"],
                }
            ],
        }
    )
    plan_gap = seal_planning_artifact(
        {
            **common,
            "record_type": PLAN_GAP_RECORD_TYPE,
            "impact_map_digest": impact["artifact_digest"],
            "task_graph_digest": task_graph["artifact_digest"],
            "coverage_digest": coverage["artifact_digest"],
            "checker": {
                "role_id": "S001",
                "role_kind": "specification_scout",
                "mode": "coverage",
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "effort": "xhigh",
            },
            "verdict": "clean",
            "findings": [],
        }
    )
    return current, impact, task_graph, coverage, plan_gap


def offline_validator(schema_path: Path):
    if (
        jsonschema is None
        or Registry is None
        or Resource is None
        or DRAFT202012 is None
    ):
        raise AssertionError("planning schema tests require jsonschema")

    def retrieve(uri: str):
        if uri.startswith("https://schemas.herdr.dev/herdr-dev-loop/"):
            path = REFERENCE_SCHEMAS / uri.rsplit("/", 1)[-1].split("#", 1)[0]
        elif uri.startswith("file://"):
            path = Path(unquote(urlparse(uri).path))
        else:
            raise AssertionError(f"schema validation attempted network access: {uri}")
        return Resource.from_contents(
            json.loads(path.read_text(encoding="utf-8")),
            default_specification=DRAFT202012,
        )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    # Public entry points intentionally mirror the repository's existing
    # relative-$ref wrappers and do not publish a second canonical $id.  Give
    # an offline validator the same base URI a path-aware runtime supplies.
    schema.setdefault("$id", schema_path.resolve().as_uri())
    jsonschema.Draft202012Validator.check_schema(schema)
    registry = Registry(retrieve=retrieve).with_resource(
        schema_path.resolve().as_uri(),
        Resource.from_contents(schema, default_specification=DRAFT202012),
    )
    return jsonschema.Draft202012Validator(schema, registry=registry)


class PlanningContractTests(unittest.TestCase):
    def test_clean_bundle_is_strict_and_dispatch_ready(self):
        current, impact, graph, coverage, plan_gap = planning_bundle()

        result = validate_dispatch_readiness(
            impact,
            graph,
            coverage,
            plan_gap,
            current,
            required_requirement_refs=["REQ-001"],
            required_plan_item_refs=["P002"],
        )

        self.assertTrue(result.ok, result.issues)
        self.assertFalse(result.stale)
        self.assertEqual(impact["planning_schema_revision"], PLANNING_SCHEMA_REVISION)
        for artifact in (impact, graph, coverage, plan_gap):
            self.assertEqual(artifact["artifact_digest"], artifact_digest(artifact))
            self.assertTrue(validate_planning_artifact(artifact).ok)

    def test_schema_entry_points_validate_all_four_artifacts_offline(self):
        _, impact, graph, coverage, plan_gap = planning_bundle()
        cases = {
            "planning-impact.schema.json": impact,
            "planning-task-graph.schema.json": graph,
            "planning-coverage.schema.json": coverage,
            "planning-plan-gap.schema.json": plan_gap,
        }
        for schema_name, artifact in cases.items():
            for root in (REFERENCE_SCHEMAS, PUBLIC_SCHEMAS):
                with self.subTest(schema=schema_name, root=root.name):
                    errors = sorted(
                        offline_validator(root / schema_name).iter_errors(artifact),
                        key=lambda error: list(error.absolute_path),
                    )
                    self.assertEqual(errors, [])

    def test_unknown_fields_fail_module_and_json_schema(self):
        _, impact, _, _, _ = planning_bundle()
        impact["unexpected"] = True
        impact = raw_digest(impact)

        validation = validate_planning_artifact(impact)

        self.assertIn("unknown-field", {issue.code for issue in validation.issues})
        errors = list(
            offline_validator(
                REFERENCE_SCHEMAS / "planning-impact.schema.json"
            ).iter_errors(impact)
        )
        self.assertTrue(errors)

    def test_content_mutation_invalidates_artifact_digest(self):
        _, impact, _, _, _ = planning_bundle()
        impact["non_goals"].append("new out-of-scope work")

        validation = validate_planning_artifact(impact)

        self.assertIn(
            "artifact-digest-mismatch", {issue.code for issue in validation.issues}
        )

    def test_malformed_bundle_and_current_identity_fail_closed(self):
        current, impact, graph, coverage, plan_gap = planning_bundle()

        malformed_artifact = validate_planning_bundle(
            [],  # type: ignore[arg-type]
            graph,
            coverage,
            plan_gap,
            current,
        )
        malformed_identity = validate_planning_bundle(
            impact,
            graph,
            coverage,
            plan_gap,
            {"run_id": "incomplete"},
        )

        self.assertIn(
            "invalid-artifact", {issue.code for issue in malformed_artifact.issues}
        )
        self.assertIn(
            "invalid-current-identity",
            {issue.code for issue in malformed_identity.issues},
        )

    def test_each_identity_revision_or_digest_change_stales_every_artifact(self):
        current, impact, graph, coverage, plan_gap = planning_bundle()
        changes = {
            "run_id": "another-run",
            "plan_revision": current.plan_revision + 1,
            "requirements_revision": current.requirements_revision + 1,
            "release_scope_revision": current.release_scope_revision + 1,
            "release_scope_digest": canonical_digest({"scope": "amended"}),
            "source_revision": current.source_revision + 1,
            "source_digest": canonical_digest({"sources": "changed"}),
        }
        for field_name, value in changes.items():
            with self.subTest(field=field_name):
                changed_identity = identity(**{field_name: value})
                result = validate_planning_bundle(
                    impact, graph, coverage, plan_gap, changed_identity
                )
                mismatches = [
                    issue
                    for issue in result.issues
                    if issue.code == "identity-mismatch"
                ]
                self.assertEqual(len(mismatches), 4)
                self.assertTrue(result.stale)
                self.assertTrue(
                    is_planning_stale(
                        (impact, graph, coverage, plan_gap), changed_identity
                    )
                )

    def test_changed_task_graph_stales_dependent_coverage_and_plan_gap(self):
        current, impact, graph, coverage, plan_gap = planning_bundle()
        graph["tasks"][0]["risk_class"] = "normal"
        graph = seal_planning_artifact(graph)

        result = validate_planning_bundle(
            impact, graph, coverage, plan_gap, current
        )

        reference_issues = [
            issue
            for issue in result.issues
            if issue.code == "reference-digest-mismatch"
        ]
        self.assertEqual(len(reference_issues), 2)
        self.assertTrue(result.stale)

    def test_coverage_detects_every_required_unassigned_dimension(self):
        _, impact, graph, coverage, _ = planning_bundle()
        cases = {
            "requirement_refs": "unassigned-requirement",
            "plan_item_refs": "unassigned-plan-item",
            "surface_refs": "unassigned-surface",
            "caller_refs": "unassigned-caller",
            "consumer_refs": "unassigned-consumer",
            "failure_path_refs": "unassigned-failure-path",
            "test_refs": "unassigned-test",
            "final_evidence_refs": "missing-final-evidence",
        }
        for field_name, expected_code in cases.items():
            with self.subTest(field=field_name):
                incomplete = copy.deepcopy(coverage)
                incomplete["entries"][0][field_name] = []

                result = validate_coverage(impact, graph, incomplete)

                self.assertIn(expected_code, {issue.code for issue in result.issues})

    def test_external_required_refs_are_not_hidden_by_an_incomplete_map(self):
        _, impact, graph, coverage, _ = planning_bundle()

        result = validate_coverage(
            impact,
            graph,
            coverage,
            required_requirement_refs=["REQ-001", "REQ-UNASSIGNED"],
            required_plan_item_refs=["P002", "P-UNASSIGNED"],
        )

        refs = {ref for issue in result.issues for ref in issue.refs}
        self.assertIn("REQ-UNASSIGNED", refs)
        self.assertIn("P-UNASSIGNED", refs)

    def test_scope_external_task_fails_closed(self):
        current, impact, graph, coverage, plan_gap = planning_bundle()
        graph["tasks"][0]["scope_refs"].append("unapproved-improvement")
        graph = seal_planning_artifact(graph)
        coverage["task_graph_digest"] = graph["artifact_digest"]
        coverage = seal_planning_artifact(coverage)
        plan_gap["task_graph_digest"] = graph["artifact_digest"]
        plan_gap["coverage_digest"] = coverage["artifact_digest"]
        plan_gap = seal_planning_artifact(plan_gap)

        result = validate_planning_bundle(
            impact, graph, coverage, plan_gap, current
        )

        self.assertIn("scope-external", {issue.code for issue in result.issues})

    def test_task_change_must_fit_write_allow(self):
        _, impact, graph, coverage, _ = planning_bundle()
        graph["tasks"][0]["write_allow"] = ["unrelated/**"]
        graph = seal_planning_artifact(graph)

        result = validate_coverage(impact, graph, coverage)

        self.assertIn("write-scope-missing", {issue.code for issue in result.issues})

    def test_shared_change_requires_an_explicit_dependency_order(self):
        _, _, graph, _, _ = planning_bundle(second_task=True)
        graph["tasks"][1]["depends_on"] = []
        graph = raw_digest(graph)

        result = validate_planning_artifact(graph)

        self.assertIn(
            "unordered-shared-change", {issue.code for issue in result.issues}
        )

    def test_unknown_self_and_cyclic_dependencies_fail_closed(self):
        _, _, graph, _, _ = planning_bundle(second_task=True)
        cases = {
            "unknown": (["T999"], "unknown-dependency"),
            "self": (["T005"], "self-dependency"),
            "cycle": (["T007"], "dependency-cycle"),
        }
        for name, (dependencies, expected_code) in cases.items():
            with self.subTest(case=name):
                invalid = copy.deepcopy(graph)
                invalid["tasks"][0]["depends_on"] = dependencies
                invalid = raw_digest(invalid)

                result = validate_planning_artifact(invalid)

                self.assertIn(expected_code, {issue.code for issue in result.issues})

    def test_task_cannot_change_an_explicit_non_goal(self):
        _, _, graph, _, _ = planning_bundle()
        graph["tasks"][0]["non_goals"] = ["implement planning contracts"]
        graph = raw_digest(graph)

        result = validate_planning_artifact(graph)

        self.assertIn("change-is-non-goal", {issue.code for issue in result.issues})

    def test_plan_gap_is_bound_to_exact_inputs_and_must_be_clean(self):
        current, impact, graph, coverage, plan_gap = planning_bundle()
        plan_gap["verdict"] = "gaps_found"
        plan_gap["findings"] = [
            {
                "finding_id": "PG-001",
                "category": "unassigned_requirement",
                "refs": ["REQ-001"],
                "message": "requirement assignment is incomplete",
            }
        ]
        plan_gap = seal_planning_artifact(plan_gap)

        result = validate_planning_bundle(
            impact, graph, coverage, plan_gap, current
        )

        self.assertIn("plan-gap-not-clean", {issue.code for issue in result.issues})
        with self.assertRaises(PlanningContractError):
            invalid_checker = copy.deepcopy(plan_gap)
            invalid_checker["checker"]["effort"] = "max"
            seal_planning_artifact(invalid_checker)


if __name__ == "__main__":
    unittest.main()
