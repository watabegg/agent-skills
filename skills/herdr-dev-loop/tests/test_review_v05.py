from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:  # pragma: no cover - exercised by minimal installations
    jsonschema = None
    Registry = None
    Resource = None
    DRAFT202012 = None


SCRIPTS = Path(__file__).parents[1] / "scripts"
SCHEMAS = Path(__file__).parents[1] / "references" / "schemas"
RUNTIME_SCHEMAS = Path(__file__).parents[1] / "schemas"
sys.path.insert(0, str(SCRIPTS))

HLOOP_SCRIPT = SCRIPTS / "hloop"
loader = importlib.machinery.SourceFileLoader(
    "hloop_review_schema_runtime", str(HLOOP_SCRIPT)
)
spec = importlib.util.spec_from_loader(loader.name, loader)
runtime_hloop = importlib.util.module_from_spec(spec)
loader.exec_module(runtime_hloop)

from hloop_lib.review import (  # noqa: E402
    FindingCandidate,
    ReviewManifest,
    ReviewModelError,
    VerificationRecord,
    normalize_findings,
    plan_review_group,
    plan_verification,
)


HEAD = "abc123"


def review_schema_validator(schema_path: Path):
    """Load a review schema with repository-relative references available."""

    if (
        jsonschema is None
        or Registry is None
        or Resource is None
        or not hasattr(jsonschema, "Draft202012Validator")
    ):
        raise AssertionError(
            "review schema tests require jsonschema.Draft202012Validator "
            "and the referencing registry"
        )
    registry = Registry()
    for path in (
        SCHEMAS / "review-manifest.schema.json",
        SCHEMAS / "review-finding.schema.json",
        SCHEMAS / "review-group-state.schema.json",
        RUNTIME_SCHEMAS / "review-group-state.schema.json",
    ):
        registry = registry.with_resource(
            path.resolve().as_uri(),
            Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))),
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=registry,
    )


def offline_file_ref_validator(schema_path: Path):
    """Build a validator for a schema using only generic file-based $ref retrieval.

    Unlike ``review_schema_validator``, this does not pre-register every
    dependency file in a hand-built registry. It retrieves whatever a
    relative ``$ref`` resolves to, purely from local disk, and raises
    instead of attempting network access -- the behavior expected of a
    standard offline JSON Schema validator pointed at a single schema file.
    """

    if (
        jsonschema is None
        or Registry is None
        or Resource is None
        or not hasattr(jsonschema, "Draft202012Validator")
    ):
        raise AssertionError(
            "review schema tests require jsonschema.Draft202012Validator "
            "and the referencing registry"
        )

    def retrieve(uri: str):
        if not uri.startswith("file://"):
            raise AssertionError(
                f"offline validator attempted non-file retrieval for {uri!r}"
            )
        from urllib.parse import unquote, urlparse

        local_path = Path(unquote(urlparse(uri).path))
        return Resource.from_contents(
            json.loads(local_path.read_text(encoding="utf-8")),
            default_specification=DRAFT202012,
        )

    registry = Registry(retrieve=retrieve)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=registry,
    )


def harvested_review_group_state() -> dict:
    """Return the review state written by the real start and harvest paths."""

    previous_namespace = runtime_hloop.LOOP_NAMESPACE
    runtime_hloop.configure_loop_namespace("test-review-schema-runtime")
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )

            state = {
                "state_format_version": runtime_hloop.STATE_FORMAT_VERSION,
                "schema_revision": runtime_hloop.STATE_SCHEMA_REVISION,
                "namespace": runtime_hloop.LOOP_NAMESPACE,
                "goal_id": "review-schema-runtime",
                "run_id": "review-schema-runtime-run",
                "skill_version": runtime_hloop.SKILL_VERSION,
                "persistence": "local-only",
                "phase": "dispatching",
                "base_branch": "main",
                "integration_branch": "main",
                "reviews": {},
                "reviewer_runner": "tui",
                "reviewer_agent_provider": "codex",
                "reviewer_agent_model": "auto",
            }
            runtime_hloop.save_state(repo, state)

            review_id = "R001"
            worktree = root / "review-worktree"
            worktree.mkdir()
            credential = root / "review-credential.json"
            credential.write_text("{}\n", encoding="utf-8")
            start_args = SimpleNamespace(
                repo=str(repo),
                review_id=review_id,
                base=None,
                head=None,
                worktree=str(worktree),
                manager_pane=None,
                direction="down",
                launcher="pane",
                runner="tui",
                agent_provider="codex",
                agent_model="auto",
                mode="dual-swarm",
                dry_run=False,
            )
            with mock.patch.object(
                runtime_hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                runtime_hloop, "require_role_snapshot"
            ), mock.patch.object(
                runtime_hloop, "prepare_role_worktree"
            ), mock.patch.object(
                runtime_hloop, "ensure_review_visible_in_worktree"
            ), mock.patch.object(
                runtime_hloop, "porcelain_paths", return_value=[]
            ), mock.patch.object(
                runtime_hloop.hloop_providers,
                "check_review_capacity",
                return_value=(),
            ), mock.patch.object(
                runtime_hloop,
                "register_role_report_identity_and_ack_floor",
                return_value=(credential, 0),
            ), mock.patch.object(
                runtime_hloop, "start_pane_launcher", return_value="test-pane"
            ), contextlib.redirect_stdout(io.StringIO()):
                if runtime_hloop.cmd_reviewer_start(start_args) != 0:
                    raise AssertionError("runtime reviewer start fixture failed")

            review_state = state["reviews"][review_id]
            runtime_hloop.resolve_semantic_ack_barrier(
                review_state,
                decision="approve",
                reason="runtime schema fixture",
                latest_ack={"event_id": "fixture-ack", "sequence": 1},
            )
            runtime_hloop.save_state(repo, state)

            plan = runtime_hloop.hloop_review.ReviewGroupPlan.from_record(
                review_state["review_plan"]
            )
            manifest = runtime_hloop.hloop_review.ReviewManifest(
                review_id=review_id,
                plan=plan,
                lane_results=tuple(lane.result() for lane in plan.expected_lanes),
                findings=(),
                verification_plan=runtime_hloop.hloop_review.plan_verification(
                    plan, ()
                ),
                verifications=(),
            )
            runtime_hloop.write_text(
                runtime_hloop.review_manifest_file(worktree, review_id),
                json.dumps(manifest.to_record(), indent=2, sort_keys=True) + "\n",
            )
            for provider in plan.providers:
                runtime_hloop.write_text(
                    runtime_hloop.review_provider_file(
                        worktree, review_id, provider
                    ),
                    runtime_hloop.frontmatter(
                        {
                            "review_id": review_id,
                            "run_id": state["run_id"],
                            "skill_version": runtime_hloop.SKILL_VERSION,
                            "head_sha": review_state["head_sha"],
                            "provider": provider,
                            "status": "reported",
                        }
                    )
                    + f"\n# {provider} provider report\n",
                )
            runtime_hloop.write_text(
                Path(review_state["worktree_review_path"]),
                runtime_hloop.frontmatter(
                    {
                        "review_id": review_id,
                        "run_id": state["run_id"],
                        "skill_version": runtime_hloop.SKILL_VERSION,
                        "base": "main",
                        "head": "main",
                        "head_sha": review_state["head_sha"],
                        "status": "reported",
                    }
                )
                + "\n## Fix Task Candidates\n\nNo fix task candidates.\n",
            )

            with mock.patch.object(
                runtime_hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                runtime_hloop, "validate_reviewer_worktree_scope", return_value=[]
            ), mock.patch.object(
                runtime_hloop, "cleanup_completed_agent_pane"
            ), mock.patch.object(
                runtime_hloop, "cleanup_review_worktree"
            ), mock.patch.object(
                runtime_hloop, "revoke_active_role_report_identity"
            ), contextlib.redirect_stdout(io.StringIO()):
                if runtime_hloop.cmd_reviewer_harvest(
                    SimpleNamespace(
                        repo=str(repo),
                        review_id=review_id,
                        keep_pane=False,
                        session_cleanup="none",
                    )
                ) != 0:
                    raise AssertionError("runtime reviewer harvest fixture failed")

            return json.loads(json.dumps(review_state))
    finally:
        runtime_hloop.configure_loop_namespace(previous_namespace)


def candidate(
    finding_id: str = "C-F001",
    *,
    provider: str = "codex",
    head_sha: str = HEAD,
    discovering_agent: str | None = None,
    severity: str = "P2",
    title: str = "Queue retry can lose the pending item",
    file_path: str = "src/queue.py",
    line: int = 42,
    symbol: str = "drain_queue",
    trigger: str = "Worker crashes after acknowledging the queue item",
    product_impact: str = "The pending item is never processed",
    proposed_fix: str = "Commit the acknowledgement with the state transition",
    requires_spec_decision: bool = False,
) -> FindingCandidate:
    return FindingCandidate(
        finding_id=finding_id,
        provider=provider,
        head_sha=head_sha,
        discovering_agent=discovering_agent or f"{provider}-discovery-01",
        severity=severity,
        confidence=0.9,
        title=title,
        file_path=file_path,
        line=line,
        symbol=symbol,
        trigger=trigger,
        product_impact=product_impact,
        origin="introduced",
        proposed_fix=proposed_fix,
        requires_spec_decision=requires_spec_decision,
    )


def confirmed_records(verification_plan):
    return tuple(
        VerificationRecord.from_assignment(
            assignment,
            fact_status="confirmed",
            ignore_status="must_not_ignore",
            decision_status="none",
            progress_without_decision="yes",
            severity="P1" if assignment.pass_number == 2 else "P2",
            recommended_action="fix_task",
        )
        for assignment in verification_plan.assignments
    )


def completed_lanes(group, findings=()):
    counts = Counter(
        (candidate.provider, candidate.discovering_agent)
        for finding in findings
        for candidate in finding.candidates
    )
    return tuple(
        lane.result(finding_count=counts[(lane.provider, lane.agent_label)])
        for lane in group.expected_lanes
    )


class ReviewGroupPlanningTests(unittest.TestCase):
    def test_modes_have_deterministic_provider_and_lane_topologies(self):
        single = plan_review_group("single", head_sha=HEAD)
        swarm = plan_review_group("swarm", head_sha=HEAD)
        dual = plan_review_group("dual", head_sha=HEAD)
        dual_swarm = plan_review_group("dual-swarm", head_sha=HEAD)

        self.assertEqual(single.providers, ("codex",))
        self.assertEqual([len(plan.lanes) for plan in single.provider_plans], [1])
        self.assertEqual([len(plan.lanes) for plan in swarm.provider_plans], [6])
        self.assertEqual(dual.providers, ("codex", "claude"))
        self.assertEqual([len(plan.lanes) for plan in dual.provider_plans], [1, 1])
        self.assertEqual(dual_swarm.providers, ("codex", "claude"))
        self.assertEqual(
            [len(plan.lanes) for plan in dual_swarm.provider_plans], [4, 4]
        )
        self.assertEqual(
            plan_review_group("swarm", head_sha=HEAD),
            plan_review_group("swarm", head_sha=HEAD),
        )

    def test_swarm_accepts_four_to_eight_lanes_and_rejects_outside_range(self):
        four = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        eight = plan_review_group("swarm", head_sha=HEAD, probe_count=8)
        self.assertEqual(len(four.expected_lanes), 4)
        self.assertEqual(len(eight.expected_lanes), 8)

        for count in (3, 9):
            with self.subTest(count=count), self.assertRaisesRegex(
                ReviewModelError, "4 to 8"
            ):
                plan_review_group("swarm", head_sha=HEAD, probe_count=count)

    def test_all_provider_plans_are_pinned_to_the_same_sha(self):
        plan = plan_review_group(
            "dual-swarm",
            head_sha=HEAD,
            providers=("claude", "codex"),
            probes_per_provider=5,
        )
        self.assertEqual(plan.providers, ("codex", "claude"))
        self.assertEqual({item.head_sha for item in plan.provider_plans}, {HEAD})


class FindingNormalizationTests(unittest.TestCase):
    def test_fingerprint_dedupes_provider_wording_not_title_or_line(self):
        codex = candidate()
        claude = candidate(
            "A-F009",
            provider="claude",
            title="Acknowledged work disappears after a crash",
            line=47,
        )

        normalized = normalize_findings((codex, claude))

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].classification, "consensus")
        self.assertTrue(normalized[0].cross_model_consensus)
        self.assertEqual(normalized[0].providers, ("codex", "claude"))
        self.assertEqual(len(normalized[0].candidates), 2)

    def test_semantic_fingerprint_changes_when_the_fix_changes(self):
        first = candidate()
        second = candidate(
            "C-F002", proposed_fix="Never acknowledge work until process shutdown"
        )
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(normalize_findings((first, second))), 2)

    def test_consensus_requires_matching_target_sha(self):
        same_sha = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        different_sha = normalize_findings(
            (
                candidate(),
                candidate("A-F001", provider="claude", head_sha="different-head"),
            )
        )

        self.assertEqual(same_sha[0].classification, "consensus")
        self.assertEqual(len(different_sha), 2)
        self.assertEqual(
            {finding.classification for finding in different_sha}, {"unique"}
        )


class VerificationPlanningTests(unittest.TestCase):
    def test_p0_and_p1_get_two_independent_passes_while_p2_gets_one(self):
        group = plan_review_group("single", head_sha=HEAD, verifier_pool_size=2)
        findings = normalize_findings(
            (
                candidate("C-F000", severity="P0"),
                candidate(
                    "C-F001",
                    severity="P1",
                    file_path="src/other.py",
                    symbol="write_state",
                ),
                candidate(
                    "C-F002",
                    severity="P2",
                    file_path="src/minor.py",
                    symbol="format_result",
                ),
            )
        )

        verification = plan_verification(group, findings)
        by_severity = {
            finding.severity: verification.assignments_for(finding.fingerprint)
            for finding in findings
        }
        self.assertEqual(len(by_severity["P0"]), 2)
        self.assertEqual(len(by_severity["P1"]), 2)
        self.assertEqual(len(by_severity["P2"]), 1)
        for severity in ("P0", "P1"):
            assignments = by_severity[severity]
            self.assertEqual({item.pass_number for item in assignments}, {1, 2})
            self.assertEqual(len({item.verifier_agent for item in assignments}), 2)

    def test_dual_critical_finding_requires_both_provider_verifiers(self):
        group = plan_review_group("dual", head_sha=HEAD)
        finding = normalize_findings((candidate(severity="P1"),))[0]

        assignments = plan_verification(group, (finding,)).assignments

        self.assertEqual({item.provider for item in assignments}, {"codex", "claude"})
        self.assertEqual({item.pass_number for item in assignments}, {1, 2})

    def test_discoverer_cannot_fill_a_verifier_pass(self):
        group = plan_review_group("single", head_sha=HEAD, verifier_pool_size=2)
        finding = normalize_findings(
            (
                candidate(
                    severity="P0", discovering_agent="codex-verifier-01"
                ),
            )
        )[0]

        verification = plan_verification(group, (finding,))

        self.assertEqual(len(verification.assignments), 1)
        self.assertEqual(verification.assignments[0].verifier_agent, "codex-verifier-02")
        self.assertEqual(
            verification.shortfalls[0].reason,
            "independent-verifier-unavailable",
        )

    def test_budget_exhaustion_marks_unverified_candidate_instead_of_dropping_it(self):
        group = plan_review_group(
            "single", head_sha=HEAD, max_verifications=1
        )
        findings = normalize_findings(
            (
                candidate("C-F001"),
                candidate(
                    "C-F002",
                    file_path="src/second.py",
                    symbol="second_path",
                ),
            )
        )

        verification = plan_verification(group, findings)

        self.assertEqual(len(findings), 2)
        self.assertEqual(len(verification.assignments), 1)
        self.assertTrue(verification.budget_exhausted)
        self.assertEqual(len(verification.shortfalls), 1)
        self.assertEqual(verification.shortfalls[0].reason, "budget-exhausted")
        self.assertIn(
            verification.shortfalls[0].fingerprint,
            {finding.fingerprint for finding in findings},
        )


class ManifestGateTests(unittest.TestCase):
    def test_complete_manifest_requires_all_lanes_and_verification_records(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        findings = normalize_findings((candidate(),))
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        self.assertTrue(manifest.completeness.complete)
        self.assertEqual(manifest.completeness.issues, ())
        self.assertTrue(manifest.to_record()["completeness"]["complete"])

    def test_missing_lane_fails_closed_and_names_the_lane(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes[:-1]),
            findings=(),
            verification_plan=plan_verification(group, ()),
            verifications=(),
        )

        result = manifest.completeness
        expected = group.expected_lanes[-1]
        self.assertFalse(result.complete)
        self.assertEqual(result.missing_lanes, (f"{expected.provider}:{expected.lane_id}",))
        self.assertIn(
            f"missing-lane:{expected.provider}:{expected.lane_id}", result.issues
        )

    def test_shortfall_and_missing_second_pass_keep_manifest_open(self):
        group = plan_review_group(
            "single", head_sha=HEAD, verifier_pool_size=1
        )
        findings = normalize_findings(
            (candidate(severity="P1", discovering_agent="codex-reviewer"),)
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        self.assertFalse(manifest.completeness.complete)
        self.assertTrue(
            any(
                issue.startswith("verification-shortfall:")
                for issue in manifest.completeness.issues
            )
        )
        self.assertEqual(
            manifest.completeness.incomplete_findings,
            (findings[0].fingerprint,),
        )
        self.assertEqual(manifest.confirmed_fingerprints, ())

    def test_manifest_rejects_lane_finding_count_drift(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        findings = normalize_findings((candidate(),))
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        self.assertFalse(manifest.completeness.complete)
        self.assertIn(
            "lane-finding-count-mismatch:codex:codex-L01",
            manifest.completeness.issues,
        )

    def test_manifest_round_trip_deserializes_actual_lane_and_verifier_data(self):
        group = plan_review_group("dual-swarm", head_sha=HEAD, probes_per_provider=4)
        findings = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )

        restored = ReviewManifest.from_record(
            json.loads(json.dumps(manifest.to_record()))
        )

        self.assertEqual(restored, manifest)
        self.assertTrue(restored.completeness.complete)
        self.assertEqual(restored.confirmed_fingerprints, (findings[0].fingerprint,))

    def test_manifest_deserialization_rejects_claimed_complete_missing_lane(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=tuple(lane.result() for lane in group.expected_lanes),
            findings=(),
            verification_plan=plan_verification(group, ()),
            verifications=(),
        ).to_record()
        manifest["providers"][0]["lanes"].pop()

        with self.assertRaisesRegex(ReviewModelError, "completeness does not match"):
            ReviewManifest.from_record(manifest)

    def test_only_fully_confirmed_finding_is_triage_eligible(self):
        group = plan_review_group("swarm", head_sha=HEAD, probe_count=4)
        findings = normalize_findings((candidate(),))
        verification = plan_verification(group, findings)
        refuted = tuple(
            VerificationRecord.from_assignment(
                assignment,
                fact_status="refuted",
                ignore_status="no_action",
                decision_status="none",
                progress_without_decision="yes",
                severity="P2",
                recommended_action="discard",
            )
            for assignment in verification.assignments
        )
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=refuted,
        )

        self.assertTrue(manifest.completeness.complete)
        self.assertEqual(manifest.confirmed_fingerprints, ())


class ReviewSchemaTests(unittest.TestCase):
    def test_validator_unavailability_fails_explicitly(self):
        with mock.patch.object(sys.modules[__name__], "jsonschema", None):
            with self.assertRaisesRegex(
                AssertionError, "jsonschema.Draft202012Validator"
            ):
                review_schema_validator(SCHEMAS / "review-manifest.schema.json")

    def test_runtime_records_validate_against_review_family_schemas(self):
        group = plan_review_group(
            "dual-swarm", head_sha=HEAD, probes_per_provider=4
        )
        findings = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )
        manifest_record = manifest.to_record()
        group_state_record = harvested_review_group_state()

        records = (
            (
                SCHEMAS / "review-manifest.schema.json",
                manifest_record,
            ),
            (
                SCHEMAS / "review-finding.schema.json",
                findings[0].to_record(),
            ),
            (
                SCHEMAS / "review-group-state.schema.json",
                group_state_record,
            ),
        )
        for schema_path, record in records:
            with self.subTest(schema=schema_path.name):
                errors = sorted(
                    review_schema_validator(schema_path).iter_errors(record),
                    key=lambda error: list(error.absolute_path),
                )
                self.assertEqual(
                    errors,
                    [],
                    "\n".join(error.message for error in errors),
                )

    def test_public_wrapper_validates_manifest_offline_without_network(self):
        """The public schemas/ wrapper must be directly, offline-validatable.

        Regression for a wrapper whose $ref only resolved correctly when a
        network-facing $id hijacked relative-ref resolution; a standard
        offline validator pointed only at this file (no hand-built registry
        of every dependency, no network) must accept a real manifest and
        reject an invalid one.
        """

        wrapper_schema = json.loads(
            (RUNTIME_SCHEMAS / "review-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn(
            "$id",
            wrapper_schema,
            "a network $id would hijack the relative $ref away from the "
            "sibling references/schemas/ file during offline resolution",
        )

        group = plan_review_group("dual-swarm", head_sha=HEAD, probes_per_provider=4)
        findings = normalize_findings(
            (candidate(), candidate("A-F001", provider="claude"))
        )
        verification = plan_verification(group, findings)
        manifest = ReviewManifest(
            review_id="R001",
            plan=group,
            lane_results=completed_lanes(group, findings),
            findings=findings,
            verification_plan=verification,
            verifications=confirmed_records(verification),
        )
        manifest_record = manifest.to_record()

        validator = offline_file_ref_validator(
            RUNTIME_SCHEMAS / "review-manifest.schema.json"
        )
        errors = list(validator.iter_errors(manifest_record))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

        invalid_record = dict(manifest_record)
        invalid_record["mode"] = "not-a-real-mode"
        invalid_errors = list(validator.iter_errors(invalid_record))
        self.assertTrue(
            invalid_errors, "offline validator must reject an invalid manifest"
        )

    def test_review_schemas_capture_manifest_finding_and_verification_contracts(self):
        finding_schema = json.loads(
            (SCHEMAS / "review-finding.schema.json").read_text(encoding="utf-8")
        )
        manifest_schema = json.loads(
            (SCHEMAS / "review-manifest.schema.json").read_text(encoding="utf-8")
        )

        self.assertIn("fingerprint", finding_schema["required"])
        self.assertEqual(
            finding_schema["properties"]["classification"]["enum"],
            ["consensus", "unique"],
        )
        self.assertEqual(
            manifest_schema["properties"]["mode"]["enum"],
            ["single", "swarm", "dual", "dual-swarm"],
        )
        self.assertIn(
            "insufficient_evidence",
            manifest_schema["$defs"]["verificationRecord"]["properties"][
                "fact_status"
            ]["enum"],
        )
        self.assertIn("completeness", manifest_schema["required"])

        runtime_manifest_schema = json.loads(
            (RUNTIME_SCHEMAS / "review-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        group_state_schema = json.loads(
            (RUNTIME_SCHEMAS / "review-group-state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            runtime_manifest_schema["$ref"],
            "../references/schemas/review-manifest.schema.json",
        )
        self.assertIn("review_plan", group_state_schema["required"])
        self.assertIn("manifest_path", group_state_schema["properties"])


if __name__ == "__main__":
    unittest.main()
