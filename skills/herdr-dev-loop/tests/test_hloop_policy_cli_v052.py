from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import importlib.machinery
import importlib.util
import sys


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_policy_cli_v052", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class PolicyCliV052Tests(unittest.TestCase):
    namespace = "policy-cli-v052"

    def setUp(self) -> None:
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace(self.namespace)

    def tearDown(self) -> None:
        hloop.configure_loop_namespace(self.previous_namespace)

    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        return repo

    def run_cli(self, repo: Path, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = hloop.main(["--repo", str(repo), "--namespace", self.namespace, *arguments])
        return result, output.getvalue()

    def init_and_lock(self, repo: Path) -> Path:
        result, output = self.run_cli(
            repo,
            "init",
            "--goal-id",
            "policy-cli-v052",
            "--goal",
            "policy CLI",
            "--base",
            "main",
            "--integration",
            "main",
            "--create-branch",
            "--specification-scout",
            "off",
        )
        self.assertEqual(result, 0, output)
        loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
        (loop / "PLAN.md").write_text(
            (loop / "PLAN.md").read_text(encoding="utf-8")
            + "\n- P004c: policy CLI test item\n",
            encoding="utf-8",
        )
        state_path = loop / "STATE.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["requirements"] = {
            "REQ-007": {
                "id": "REQ-007",
                "source_inputs": ["U0001"],
                "acceptance": ["policy CLI test requirement"],
                "priority": "P1",
                "dependencies": [],
                "accepted_at": "2026-07-16T00:00:00+00:00",
                "status": "accepted",
                "supersedes": [],
                "superseded_by": "",
            }
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        source_args = [
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/MISSION.md",
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/PLAN.md",
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/PROFILE.md",
            "--source-ref",
            f".ai/herdr-dev-loop/loops/{self.namespace}/DECISIONS.md",
        ]
        result, output = self.run_cli(
            repo,
            "release-scope",
            "lock",
            *source_args,
            "--plan-item-ref",
            "P004c",
            "--requirement-ref",
            "REQ-007",
            "--scope-ref",
            "release-contract",
        )
        self.assertEqual(result, 0, output)
        return loop

    def test_release_scope_lock_rejects_unknown_plan_and_requirement_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            result, output = self.run_cli(
                repo,
                "init",
                "--goal-id",
                "policy-cli-v052",
                "--goal",
                "policy CLI",
                "--base",
                "main",
                "--integration",
                "main",
                "--create-branch",
                "--specification-scout",
                "off",
            )
            self.assertEqual(result, 0, output)
            loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
            (loop / "PLAN.md").write_text(
                (loop / "PLAN.md").read_text(encoding="utf-8")
                + "\n- P004c: policy CLI test item\n",
                encoding="utf-8",
            )
            state_path = loop / "STATE.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["requirements"] = {
                "REQ-007": {
                    "id": "REQ-007",
                    "source_inputs": ["U0001"],
                    "acceptance": ["policy CLI test requirement"],
                    "priority": "P1",
                    "dependencies": [],
                    "accepted_at": "2026-07-16T00:00:00+00:00",
                    "status": "accepted",
                    "supersedes": [],
                    "superseded_by": "",
                }
            }
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            source_args = [
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/MISSION.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PLAN.md",
            ]
            result, output = self.run_cli(
                repo,
                "release-scope",
                "lock",
                *source_args,
                "--plan-item-ref",
                "P999",
                "--requirement-ref",
                "REQ-007",
            )
            self.assertNotEqual(result, 0)
            self.assertIn("missing locked PLAN reference", output)
            result, output = self.run_cli(
                repo,
                "release-scope",
                "lock",
                *source_args,
                "--plan-item-ref",
                "P004c",
                "--requirement-ref",
                "REQ-999",
            )
            self.assertNotEqual(result, 0)
            self.assertIn("accepted requirement", output)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["release_scope"]["status"], "unlocked")

    def test_fresh_init_requires_scope_lock_but_migrated_legacy_keeps_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            result, output = self.run_cli(
                repo,
                "init",
                "--goal-id",
                "policy-cli-v052",
                "--goal",
                "policy CLI",
                "--base",
                "main",
                "--integration",
                "main",
                "--create-branch",
                "--specification-scout",
                "off",
            )
            self.assertEqual(result, 0, output)
            loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
            state_path = loop / "STATE.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["release_scope"]["status"], "unlocked")
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "before lock",
                "--id",
                "T001",
                "--kind",
                "research",
                "--allow-no-write",
            )
            self.assertNotEqual(result, 0, output)
            with self.assertRaises(hloop.HLoopError):
                hloop.dispatch_start_preflight(
                    repo,
                    json.loads(state_path.read_text(encoding="utf-8")),
                    role_id="T001",
                    role_kind="worker",
                    task_meta={"task_origin": "planned"},
                )

            legacy = json.loads(state_path.read_text(encoding="utf-8"))
            legacy["schema_revision"] = 1
            legacy.pop("release_scope", None)
            state_path.write_text(json.dumps(legacy), encoding="utf-8")
            result, output = self.run_cli(repo, "migrate", "--apply")
            self.assertEqual(result, 0, output)
            migrated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["release_scope"]["status"], "legacy-unlocked")
            self.assertEqual(migrated["review_policy"]["cadence"], "merge-count")
            self.assertEqual(
                migrated["manual_final_review"]["status"],
                "not-required-for-legacy-run",
            )
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "legacy task",
                "--id",
                "T001",
                "--kind",
                "research",
                "--allow-no-write",
            )
            self.assertEqual(result, 0, output)

    def test_task_creation_and_update_use_immutable_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            result, output = self.run_cli(
                repo,
                "init",
                "--goal-id",
                "policy-cli-v052",
                "--goal",
                "policy CLI",
                "--base",
                "main",
                "--integration",
                "main",
                "--create-branch",
                "--specification-scout",
                "off",
            )
            self.assertEqual(result, 0, output)
            loop = repo / ".ai" / "herdr-dev-loop" / "loops" / self.namespace
            state_path = loop / "STATE.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["release_scope"] = {
                "status": "unlocked",
                "source_refs": [],
                "source_digests": {},
                "scope_revision": 0,
                "source_snapshot_revision": 0,
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "before lock",
                "--kind",
                "research",
                "--allow-no-write",
            )
            self.assertNotEqual(result, 0)
            loop = self.init_and_lock(repo)
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "planned task",
                "--id",
                "T010",
                "--kind",
                "research",
                "--allow-no-write",
                "--task-origin",
                "planned",
                "--plan-item-ref",
                "P004c",
            )
            self.assertEqual(result, 0, output)
            task_meta = hloop.read_frontmatter(loop / "tasks" / "T010.md")
            self.assertEqual(task_meta["task_origin"], "planned")
            self.assertEqual(task_meta["release_scope_revision"], "1")
            self.assertEqual(task_meta["plan_item_refs"], ["P004c"])

            result, output = self.run_cli(
                repo,
                "task",
                "update",
                "T010",
                "--add-acceptance",
                "still immutable",
            )
            self.assertEqual(result, 0, output)
            updated_meta = hloop.read_frontmatter(loop / "tasks" / "T010.md")
            self.assertEqual(updated_meta["task_origin"], "planned")
            self.assertEqual(updated_meta["plan_item_refs"], ["P004c"])

            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "scope expansion",
                "--kind",
                "research",
                "--allow-no-write",
                "--task-origin",
                "planned",
                "--plan-item-ref",
                "P004c",
                "--contract-relation",
                "outside_release",
            )
            self.assertNotEqual(result, 0)

    def test_finding_task_requires_persisted_evidence_and_allows_contract_violation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            loop = self.init_and_lock(repo)
            fingerprint = "sha256:" + "a" * 64
            finding_args = [
                "task",
                "new",
                "contract recovery",
                "--kind",
                "fix",
                "--write-allow",
                "scripts/fix.py",
                "--task-origin",
                "finding",
                "--source-finding",
                fingerprint,
                "--requirement-ref",
                "REQ-007",
                "--why-fix-now",
                "confirmed accepted requirement violation",
                "--origin",
                "unrelated-pre-existing",
                "--contract-relation",
                "in_scope",
                "--release-effect",
                "non_blocking",
                "--fact-status",
                "confirmed",
                "--disposition",
                "fix_now",
                "--remediation-round",
                "1",
            ]
            result, output = self.run_cli(repo, *finding_args)
            self.assertNotEqual(result, 0)
            self.assertIn("finding inventory is empty", output)

            state_path = loop / "STATE.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["finding_inventory"] = {
                "fingerprints": [fingerprint],
                "finding_ids": ["FND-RECOVERY"],
                "records": {
                    fingerprint: {
                        "fingerprint": fingerprint,
                        "finding_ids": ["FND-RECOVERY"],
                        "head_sha": "a" * 40,
                        "source_refs": ["reviews/R001/MANIFEST.json"],
                        "confirmed": True,
                    }
                },
                "updated_at": "2026-07-16T00:00:00+00:00",
            }
            state["finding_inventory"]["records"][fingerprint]["confirmed"] = False
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            result, output = self.run_cli(repo, *finding_args)
            self.assertNotEqual(result, 0)
            self.assertIn("finding inventory is empty", output)

            state["finding_inventory"]["records"][fingerprint]["confirmed"] = True
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            result, output = self.run_cli(repo, *finding_args)
            self.assertEqual(result, 0, output)

            outside_args = list(finding_args)
            outside_args[outside_args.index("in_scope")] = "outside_release"
            result, output = self.run_cli(repo, *outside_args)
            self.assertNotEqual(result, 0)
            self.assertIn("in_scope", output)

    def test_scope_amendment_dispatch_freeze_and_follow_up_deduplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            loop = self.init_and_lock(repo)
            (loop / "MISSION.md").write_text("authorized scope amendment\n", encoding="utf-8")
            (loop / "SCOPE.md").write_text("new authorized scope\n", encoding="utf-8")
            source_args = [
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/MISSION.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PLAN.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PROFILE.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/DECISIONS.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/SCOPE.md",
            ]
            result, output = self.run_cli(
                repo,
                "release-scope",
                "amend",
                "--kind",
                "scope-change",
                "--reason",
                "authorized scope clarification",
                *source_args,
                "--basis-ref",
                "REQ-007",
                "--user-input-id",
                "U0002",
            )
            self.assertEqual(result, 0, output)
            result, output = self.run_cli(repo, "dispatch", "freeze", "--reason", "validation in progress")
            self.assertEqual(result, 0, output)
            result, output = self.run_cli(
                repo,
                "task",
                "new",
                "blocked task",
                "--kind",
                "research",
                "--allow-no-write",
                "--task-origin",
                "user-amendment",
                "--authorization-input-id",
                "U0002",
            )
            self.assertNotEqual(result, 0)
            result, output = self.run_cli(repo, "dispatch", "unfreeze", "--user-input-id", "U0003")
            self.assertEqual(result, 0, output)

            fingerprint = "sha256:" + "0" * 64
            follow_up_args = [
                "follow-up",
                "add",
                "--title",
                "Deferred integration concern",
                "--component",
                "integration",
                "--trigger-class",
                "review-follow-up",
                "--product-impact",
                "operator visibility",
                "--source-review-fingerprint",
                fingerprint,
                "--discovered-head",
                "HEAD",
                "--evidence",
                "review R001 evidence",
                "--impact",
                "No current release behavior is affected.",
                "--affected-path",
                "docs/follow-up.md",
                "--deferred-reason",
                "Outside the locked release contract.",
                "--reconsider-condition",
                "When the next release scope includes the integration surface.",
            ]
            result, output = self.run_cli(repo, *follow_up_args)
            self.assertEqual(result, 0, output)
            result, output = self.run_cli(repo, "follow-up", "list", "--json")
            self.assertEqual(result, 0, output)
            listed = json.loads(output)
            self.assertEqual(listed[0]["id"], "F001")
            result, output = self.run_cli(repo, "follow-up", "show", "F001", "--json")
            self.assertEqual(result, 0, output)
            shown = json.loads(output)
            self.assertEqual(shown["issue_key"], listed[0]["issue_key"])
            self.assertEqual(shown["root_cause"], "")
            result, output = self.run_cli(
                repo,
                *follow_up_args[:14],
                "--evidence",
                "second review evidence",
                "--impact",
                "Same semantic concern remains deferred.",
                "--affected-path",
                "docs/follow-up.md",
                "--deferred-reason",
                "Still outside the current release contract.",
                "--reconsider-condition",
                "At the next release-scope lock.",
            )
            self.assertEqual(result, 0, output)
            state = hloop.load_state(repo)
            self.assertEqual(len(state["follow_ups"]["issue_keys"]), 1)
            self.assertEqual(state["follow_ups"]["open_count"], 1)
            result, output = self.run_cli(repo, "follow-up", "export", "--output", "docs/follow-ups.md")
            self.assertEqual(result, 0, output)
            self.assertTrue((repo / "docs" / "follow-ups.md").is_file())
            result, output = self.run_cli(repo, "dashboard", "--json", "--no-pane-probe")
            self.assertEqual(result, 0, output)
            payload = json.loads(output)
            self.assertFalse(payload["loop"]["dispatch_frozen"])
            self.assertNotIn("hloop reviewer start", payload["next_actions"])

    def test_scope_amendment_invalidates_fixed_target_review_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            loop = self.init_and_lock(repo)
            state_path = loop / "STATE.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            target = subprocess.check_output(
                ["git", "rev-parse", "main"], cwd=repo, text=True
            ).strip()
            state.update(
                {
                    "phase": "awaiting_manual_final_review",
                    "review_readiness": {
                        "status": "ready",
                        "target_sha": target,
                        "errors": [],
                        "checks": {"scope_status": "locked"},
                    },
                    "review_convergence": {
                        "status": "converged",
                        "target_sha": target,
                        "verified_actionable_findings": 0,
                        "artifact_refs": ["reviews/convergence/MANIFEST.json"],
                    },
                    "manual_final_review": {
                        "status": "passed",
                        "target_sha": target,
                        "certification_id": "C001",
                        "prepared_plan": "reviews/final/PLAN.json",
                        "prepared_plan_digest": "sha256:" + "a" * 64,
                        "manifest": "reviews/final/MANIFEST.json",
                        "report": "reviews/final/FINAL.md",
                        "manifest_complete": True,
                        "verified_actionable_findings": 0,
                        "attempt_history": [],
                    },
                }
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            (loop / "MISSION.md").write_text(
                "changed after the fixed review snapshot\n", encoding="utf-8"
            )
            source_args = [
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/MISSION.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PLAN.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/PROFILE.md",
                "--source-ref",
                f".ai/herdr-dev-loop/loops/{self.namespace}/DECISIONS.md",
            ]
            result, output = self.run_cli(
                repo,
                "release-scope",
                "amend",
                "--kind",
                "editorial",
                "--reason",
                "record the approved editorial source change",
                *source_args,
            )
            self.assertEqual(result, 0, output)
            after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(after["release_scope"]["scope_revision"], 1)
            self.assertEqual(after["release_scope"]["source_snapshot_revision"], 2)
            self.assertEqual(after["review_readiness"]["status"], "pending")
            self.assertEqual(after["review_convergence"]["status"], "pending")
            self.assertEqual(after["manual_final_review"]["status"], "pending")
            self.assertEqual(after["phase"], "dispatching")
            self.assertEqual(after["review_convergence"]["target_sha"], target)

    def test_review_failure_phases_reject_ordinary_dispatch_unfreeze(self) -> None:
        for phase in (
            "review_convergence_exhausted",
            "manual_final_review_failed",
            "manual_final_review_incomplete",
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                repo = self.make_repo(Path(directory))
                loop = self.init_and_lock(repo)
                state_path = loop / "STATE.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["phase"] = phase
                state["dispatch_freeze"] = {
                    "status": "active",
                    "reason": "review stop",
                    "frozen_at": "2026-07-16T00:00:00+00:00",
                    "source_input_id": "",
                    "allowed_running_role_ids": [],
                }
                state["review_convergence"] = {
                    "status": "exhausted" if phase == "review_convergence_exhausted" else "pending",
                    "fix_round": 2,
                    "verified_actionable_findings": 1,
                }
                state["manual_final_review"] = {
                    "status": phase.removeprefix("manual_final_review_")
                    if phase.startswith("manual_final_review_")
                    else "pending",
                    "verified_actionable_findings": 1,
                    "attempt_history": [],
                }
                state_path.write_text(json.dumps(state), encoding="utf-8")
                before = state_path.read_bytes()
                result, output = self.run_cli(
                    repo,
                    "dispatch",
                    "unfreeze",
                    "--reason",
                    "attempt bypass",
                )
                self.assertNotEqual(result, 0, output)
                self.assertIn("review reopen", output)
                self.assertEqual(state_path.read_bytes(), before)

    def test_follow_up_relations_promote_and_preserve_one_canonical_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            loop = self.init_and_lock(repo)
            fingerprint = "sha256:" + "1" * 64

            def add_follow_up(*extra: str) -> dict:
                args = [
                    "follow-up",
                    "add",
                    "--title",
                    "Review follow-up",
                    "--component",
                    "review runtime",
                    "--trigger-class",
                    "classification drift",
                    "--product-impact",
                    "operator needs a later release decision",
                    "--source-review-fingerprint",
                    fingerprint,
                    "--discovered-head",
                    "abc123",
                    "--evidence",
                    "review evidence one",
                    "--impact",
                    "outside the current release contract",
                    "--affected-path",
                    "src/review.py",
                    "--fact-status",
                    "confirmed",
                    "--severity",
                    "P2",
                    "--origin",
                    "unrelated-pre-existing",
                    "--contract-relation",
                    "outside_release",
                    "--decision-requirement",
                    "none",
                    "--release-effect",
                    "non_blocking",
                    "--disposition",
                    "defer_follow_up",
                    "--recommended-action",
                    "defer_follow_up",
                    "--deferred-reason",
                    "outside this release",
                    "--reconsider-condition",
                    "next release scope lock",
                    "--json",
                    *extra,
                ]
                result, output = self.run_cli(repo, *args)
                self.assertEqual(result, 0, output)
                return json.loads(output)

            provisional = add_follow_up()
            provisional_key = provisional["follow_up"]["issue_key"]
            self.assertTrue(provisional["follow_up"]["provisional"])

            final = add_follow_up(
                "--root-cause",
                "unsafe disposition merge",
                "--alias-of",
                provisional_key,
                "--evidence",
                "review evidence two",
                "--source-review-fingerprint",
                "sha256:" + "2" * 64,
            )
            final_record = final["follow_up"]
            final_key = final_record["issue_key"]
            self.assertEqual(final["status"], "deduplicated")
            self.assertEqual(final_record["id"], "F001")
            self.assertFalse(final_record["provisional"])
            self.assertIn(provisional_key, final_record["aliases"])
            self.assertEqual(
                final_record["source_review_fingerprints"],
                [fingerprint, "sha256:" + "2" * 64],
            )
            self.assertEqual(
                final_record["evidence"],
                ["review evidence one", "review evidence two"],
            )
            self.assertTrue(final_record["history"])
            state = hloop.load_state(repo)
            self.assertEqual(state["follow_ups"]["issue_keys"], {final_key: "F001"})
            self.assertEqual(
                state["follow_ups"]["issue_key_aliases"][provisional_key], final_key
            )
            result, output = self.run_cli(
                repo, "follow-up", "show", provisional_key, "--json"
            )
            self.assertEqual(result, 0, output)
            self.assertEqual(json.loads(output)["id"], "F001")
            self.assertEqual(json.loads(output)["issue_key"], final_key)

            duplicate = add_follow_up(
                "--root-cause",
                "different wording",
                "--duplicate-of",
                final_key,
            )
            duplicate_key = duplicate["follow_up"]["issue_key"]
            self.assertEqual(duplicate["follow_up"]["id"], "F001")
            self.assertEqual(
                hloop.load_state(repo)["follow_ups"]["issue_key_aliases"][duplicate_key],
                final_key,
            )

            superseding = add_follow_up(
                "--component",
                "review runtime replacement",
                "--root-cause",
                "replacement design",
                "--supersedes",
                final_key,
            )
            self.assertEqual(superseding["follow_up"]["id"], "F002")
            superseding_key = superseding["follow_up"]["issue_key"]
            state = hloop.load_state(repo)
            self.assertEqual(state["follow_ups"]["issue_keys"], {superseding_key: "F002"})
            self.assertEqual(
                state["follow_ups"]["issue_key_aliases"][final_key], superseding_key
            )
            self.assertEqual(
                hloop.read_frontmatter(loop / "follow-ups" / "F001.md")["status"],
                "superseded",
            )
            result, output = self.run_cli(
                repo, "follow-up", "show", provisional_key, "--json"
            )
            self.assertEqual(result, 0, output)
            self.assertEqual(json.loads(output)["id"], "F002")

            before = (loop / "STATE.json").read_bytes()
            result, output = self.run_cli(
                repo,
                "follow-up",
                "add",
                "--title",
                "Self relation",
                "--component",
                "review runtime",
                "--trigger-class",
                "classification drift",
                "--product-impact",
                "operator needs a later release decision",
                "--root-cause",
                "unsafe disposition merge",
                "--source-review-fingerprint",
                "sha256:" + "3" * 64,
                "--evidence",
                "invalid relation",
                "--impact",
                "invalid",
                "--affected-path",
                "src/review.py",
                "--deferred-reason",
                "invalid",
                "--reconsider-condition",
                "invalid",
                "--alias-of",
                final_key,
            )
            self.assertNotEqual(result, 0, output)
            self.assertEqual((loop / "STATE.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
