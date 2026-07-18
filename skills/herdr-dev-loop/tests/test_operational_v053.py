"""Operational regression tests for the schema-3.3 session-efficiency work."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional dependency in minimal installs
    jsonschema = None


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_operational_v053", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)


class CompletionModeTests(unittest.TestCase):
    def test_preflight_probes_real_git_metadata_and_binds_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            probe = hloop.probe_completion_mode(repo)

        self.assertEqual(probe["status"], "writable")
        self.assertEqual(probe["mode"], "commit")
        self.assertTrue(probe["git_metadata_paths"])
        self.assertTrue(all(item["status"] == "writable" for item in probe["checks"]))
        resources = {item["resource"] for item in probe["checks"]}
        self.assertTrue(
            {
                "object-store",
                "index-lock",
                "head-lock",
                "head-log",
                "reference-lock",
                "reference-log",
            }.issubset(resources)
        )

        task = {
            "active_attempt_id": "T001-A001",
            "completion_mode": "handoff",
            "completion_mode_attempt_id": "T001-A001",
        }
        self.assertEqual(hloop.bound_completion_mode(task, "handoff", revision=3), "handoff")
        with self.assertRaisesRegex(hloop.HLoopError, "does not match"):
            hloop.bound_completion_mode(task, "commit", revision=3)
        with self.assertRaisesRegex(hloop.HLoopError, "binding"):
            hloop.bound_completion_mode({}, None, revision=3)

    def test_uncertain_descriptor_close_fails_closed_without_retry_or_leftover(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / ".git" / "HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
            real_close = os.close
            close_calls = 0

            def close_then_raise(descriptor):
                nonlocal close_calls
                close_calls += 1
                real_close(descriptor)
                raise OSError(5, "simulated close uncertainty after OS close")

            with mock.patch.object(
                hloop, "git", side_effect=[".git", ".git"]
            ), mock.patch.object(hloop.os, "close", side_effect=close_then_raise):
                probe = hloop.probe_completion_mode(repo)

            self.assertEqual(probe["mode"], "handoff")
            self.assertEqual(probe["status"], "unwritable")
            self.assertEqual(close_calls, 1)
            self.assertEqual(list((repo / ".git").glob(".hloop-write-probe-*")), [])

    def test_any_required_git_resource_failure_selects_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            real_probe = hloop._probe_completion_resource

            def fail_objects(label, path, kind):
                if label == "object-store":
                    return {
                        "resource": label,
                        "path": str(path),
                        "status": "unwritable",
                        "detail": "simulated object store denial",
                    }
                return real_probe(label, path, kind)

            with mock.patch.object(
                hloop, "_probe_completion_resource", side_effect=fail_objects
            ):
                probe = hloop.probe_completion_mode(repo)

        self.assertEqual(probe["mode"], "handoff")
        self.assertEqual(probe["status"], "unwritable")
        self.assertEqual(probe["checks"][-1]["resource"], "object-store")

    def test_preexisting_git_locks_are_never_removed_by_capability_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            locks = {
                "index-lock": repo / ".git" / "index.lock",
                "head-lock": repo / ".git" / "HEAD.lock",
                "packed-refs-lock": repo / ".git" / "packed-refs.lock",
                "reference-lock": repo / ".git" / "refs" / "heads" / "main.lock",
            }
            marker = b"owned by another git process\n"
            for resource, lock_path in locks.items():
                with self.subTest(resource=resource):
                    lock_path.parent.mkdir(parents=True, exist_ok=True)
                    lock_path.write_bytes(marker)

                    probe = hloop.probe_completion_mode(repo)

                    self.assertEqual(probe["mode"], "handoff")
                    self.assertEqual(probe["checks"][-1]["resource"], resource)
                    self.assertEqual(lock_path.read_bytes(), marker)
                    lock_path.unlink()

    def test_head_uncertainty_and_valid_special_ref_fail_closed_or_probe_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            real_read_text = Path.read_text

            def deny_head(path, *args, **kwargs):
                if path == repo / ".git" / "HEAD":
                    raise OSError("HEAD unavailable")
                return real_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", autospec=True, side_effect=deny_head):
                uncertain = hloop.probe_completion_mode(repo)
            self.assertEqual(uncertain["mode"], "handoff")
            self.assertEqual(uncertain["status"], "unknown")

            (repo / ".git" / "HEAD").write_text(
                "ref: refs/heads/feature@x\n", encoding="utf-8"
            )
            special = hloop.probe_completion_mode(repo)
            resources = {item["resource"] for item in special["checks"]}
            self.assertEqual(special["mode"], "commit")
            self.assertIn("reference-lock", resources)
            self.assertIn("reference-log", resources)

    @unittest.skipIf(jsonschema is None, "jsonschema is required for probe contract test")
    def test_actual_probe_output_matches_state_schema_and_report_contract(self):
        schema_path = (
            SCRIPT.parents[1] / "references" / "schemas" / "state.schema.json"
        )
        state_schema = hloop.json.loads(schema_path.read_text(encoding="utf-8"))
        probe_schema = state_schema["$defs"]["workerCandidateTaskProjection"][
            "properties"
        ]["completion_mode_probe"]
        validator = jsonschema.Draft202012Validator(probe_schema)

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            probes = [hloop.probe_completion_mode(repo)]
            with mock.patch.object(
                hloop, "git", side_effect=hloop.HLoopError("metadata lookup denied")
            ):
                probes.append(hloop.probe_completion_mode(repo))

        for probe in probes:
            with self.subTest(status=probe["status"]):
                validator.validate(probe)
                self.assertEqual(
                    probe["git_metadata_paths"],
                    list(dict.fromkeys(probe["git_metadata_paths"])),
                )
                report = hloop.hloop_events.validate_report(
                    {
                        "run_id": "run-1",
                        "role_id": "T001",
                        "attempt_id": "T001-A001",
                        "task_contract_digest": "a" * 64,
                        "type": "ack",
                        "stage": "planning",
                        "summary": "understood",
                        "understood_goal": "goal",
                        "scope": ["src/**"],
                        "acceptance": ["tests pass"],
                        "approach": "small patch",
                        "next": "wait",
                        "needs_manager": True,
                        "evidence_refs": [],
                        "created_at": "2026-07-18T00:00:00+00:00",
                        "completion_mode": probe["mode"],
                        "completion_mode_probe": probe,
                    }
                )
                self.assertEqual(report["completion_mode_probe"], probe)

    def test_revision_three_attempt_rejects_late_or_stale_mode_binding(self):
        attempt = {
            "active_attempt_id": "T001-A001",
            "completion_mode": "commit",
        }
        with self.assertRaisesRegex(hloop.HLoopError, "initial semantic ACK"):
            hloop.bound_completion_mode(attempt, "commit", revision=3)

        attempt["completion_mode_attempt_id"] = "T001-A000"
        with self.assertRaisesRegex(hloop.HLoopError, "attempt"):
            hloop.bound_completion_mode(attempt, "commit", revision=3)

        legacy = {
            "active_attempt_id": "T001-A001",
            "semantic_ack_barrier": {"status": "approved"},
        }
        with self.assertRaisesRegex(hloop.HLoopError, "initial semantic ACK"):
            hloop.bound_completion_mode(legacy, "handoff", revision=3)

    def test_worker_prompt_is_completion_mode_specific(self):
        base_task = {
            "id": "T001",
            "attempt_id": "T001-A001",
            "contract_schema_revision": 3,
            "base_ref": "integration",
            "base_sha": "a" * 40,
            "worker_protocol": "native",
            "worker_qa_profile": "repo-default",
        }
        state = {
            "run_id": "run-1",
            "branch_strategy": "integration",
            "worker_protocol": "native",
            "worker_qa_profile": "repo-default",
        }

        handoff = hloop.render_worker_prompt(
            "T001",
            dict(base_task, completion_mode="handoff"),
            Path("/tmp/T001"),
            "ai/T001",
            state,
        )
        self.assertNotIn("Commit product changes first", handoff)
        self.assertNotIn("Generate and commit the result", handoff)
        self.assertIn("worker submit T001 --completion-mode handoff", handoff)
        self.assertIn("worker finalize T001 --handoff", handoff)

        commit = hloop.render_worker_prompt(
            "T001",
            dict(base_task, completion_mode="commit"),
            Path("/tmp/T001"),
            "ai/T001",
            state,
        )
        self.assertIn("Commit product changes first", commit)
        self.assertIn("Generate and commit the result", commit)
        self.assertNotIn("worker finalize T001 --handoff", commit)

    def test_explicit_ack_retry_reuses_retained_probe_before_fresh_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox = root / "outbox.json"
            probe = {
                "version": 1,
                "mode": "handoff",
                "status": "unwritable",
                "checked_at": "2026-07-18T00:00:00+00:00",
                "git_metadata_paths": ["/repo/.git"],
                "checks": [
                    {
                        "resource": "git-metadata",
                        "path": "/repo/.git",
                        "status": "unwritable",
                        "detail": "EROFS",
                    }
                ],
            }
            report = hloop.hloop_events.validate_report(
                {
                    "run_id": "run-1",
                    "role_id": "T001",
                    "attempt_id": "T001-A001",
                    "task_contract_digest": "a" * 64,
                    "type": "ack",
                    "stage": "planning",
                    "summary": "understood",
                    "understood_goal": "goal",
                    "scope": ["src/**"],
                    "acceptance": ["tests pass"],
                    "approach": "small patch",
                    "next": "wait",
                    "needs_manager": True,
                    "evidence_refs": [],
                    "created_at": "2026-07-18T00:00:00+00:00",
                    "completion_mode": "handoff",
                    "completion_mode_probe": probe,
                }
            )
            first = hloop.hloop_broker.role_outbox_client_event(
                outbox,
                report=report,
                invocation_id="T001-A001:ack/0001",
            )
            with mock.patch.object(
                hloop,
                "probe_completion_mode",
                side_effect=AssertionError("fresh probe must not run on retry"),
            ):
                retained = hloop.worker_ack_completion_mode_probe(
                    root,
                    outbox,
                    run_id="run-1",
                    role_id="T001",
                    attempt_id="T001-A001",
                    task_contract_digest="a" * 64,
                    invocation_id="T001-A001:ack/0001",
                )

        self.assertEqual(retained, probe)
        self.assertEqual(first["completion_mode_probe"], probe)

    def test_implicit_ack_retry_reuses_original_event_while_pending_and_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            state = {"run_id": "run-1"}
            credential, _floor = hloop.register_role_report_identity_and_ack_floor(
                repo,
                state,
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest="a" * 64,
            )
            parser = hloop.build_parser()
            argv = [
                "--repo",
                str(repo),
                "agent",
                "report",
                "--role-id",
                "T001",
                "--attempt-id",
                "T001-A001",
                "--run-id",
                "run-1",
                "--task-contract-digest",
                "a" * 64,
                "--report-credential-file",
                str(credential),
                "--type",
                "ack",
                "--stage",
                "planning",
                "--summary",
                "understood",
                "--understood-goal",
                "goal",
                "--scope",
                "src/**",
                "--acceptance",
                "tests pass",
                "--approach",
                "small patch",
                "--next",
                "wait",
            ]
            self.assertEqual(hloop.cmd_agent_report(parser.parse_args(argv)), 0)
            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                first = store.events(transaction)
            self.assertEqual(len(first), 1)
            original = first[0]
            outbox = hloop.role_report_outbox_path(
                repo,
                run_id="run-1",
                role_id="T001",
                attempt_id="T001-A001",
            )
            original_outbox = hloop.json.loads(outbox.read_text(encoding="utf-8"))
            original_entry = original_outbox["entries"][0]
            agent = {
                "active_attempt_id": "T001-A001",
                "contract_schema_revision": 3,
            }
            hloop.arm_semantic_ack_barrier(
                agent,
                message_id="initial:T001-A001",
                digest="a" * 64,
                kind="initial",
            )
            self.assertEqual(
                agent["semantic_ack_barrier"]["approval_application"]["status"],
                "pending",
            )

            with mock.patch.object(
                hloop,
                "probe_completion_mode",
                side_effect=AssertionError("implicit retry must retain the first probe"),
            ):
                self.assertEqual(hloop.cmd_agent_report(parser.parse_args(argv)), 0)
            approved = hloop.resolve_semantic_ack_barrier(
                agent,
                decision="approve",
                reason="semantic contract approved",
                latest_ack=original,
            )
            self.assertEqual(approved["status"], "approved")
            with mock.patch.object(
                hloop,
                "probe_completion_mode",
                side_effect=AssertionError("approved retry must retain the first probe"),
            ):
                self.assertEqual(hloop.cmd_agent_report(parser.parse_args(argv)), 0)
            with store.transaction() as transaction:
                retried = store.events(transaction)
            retried_outbox = hloop.json.loads(outbox.read_text(encoding="utf-8"))

        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["event_id"], original["event_id"])
        self.assertEqual(retried[0]["created_at"], original["created_at"])
        self.assertEqual(
            retried[0]["completion_mode_probe"],
            original["completion_mode_probe"],
        )
        self.assertEqual(len(retried_outbox["entries"]), 1)
        self.assertEqual(
            retried_outbox["entries"][0]["semantic_digest"],
            original_entry["semantic_digest"],
        )
        self.assertEqual(agent["semantic_ack_barrier"]["status"], "approved")
        self.assertEqual(agent["completion_mode_ack_event_id"], original["event_id"])

    def test_contract_reack_reuses_attempt_probe_but_explicit_invocations_differ(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox = root / "outbox.json"
            probe = {
                "version": 1,
                "mode": "handoff",
                "status": "unwritable",
                "checked_at": "2026-07-18T00:00:00+00:00",
                "git_metadata_paths": ["/repo/.git/objects"],
                "checks": [
                    {
                        "resource": "object-store",
                        "path": "/repo/.git/objects",
                        "status": "unwritable",
                        "detail": "EROFS",
                    }
                ],
            }
            base_report = {
                "run_id": "run-1",
                "role_id": "T001",
                "attempt_id": "T001-A001",
                "task_contract_digest": "a" * 64,
                "type": "ack",
                "stage": "planning",
                "summary": "understood",
                "understood_goal": "goal",
                "scope": ["src/**"],
                "acceptance": ["tests pass"],
                "approach": "small patch",
                "next": "wait",
                "needs_manager": True,
                "evidence_refs": [],
                "created_at": "2026-07-18T00:00:00+00:00",
                "completion_mode": "handoff",
                "completion_mode_probe": probe,
            }
            first = hloop.hloop_broker.role_outbox_client_event(
                outbox,
                report=base_report,
                invocation_id="T001-A001:ack/0001",
            )
            second = hloop.hloop_broker.role_outbox_client_event(
                outbox,
                report=base_report,
                invocation_id="T001-A001:ack/0002",
            )
            self.assertNotEqual(first["event_id"], second["event_id"])
            with mock.patch.object(
                hloop,
                "probe_completion_mode",
                side_effect=AssertionError("contract re-ACK must reuse attempt capability"),
            ):
                retained = hloop.worker_ack_completion_mode_probe(
                    root,
                    outbox,
                    run_id="run-1",
                    role_id="T001",
                    attempt_id="T001-A001",
                    task_contract_digest="b" * 64,
                )

        self.assertEqual(retained, probe)


class SemanticAckTests(unittest.TestCase):
    def test_revision_three_candidate_gate_requires_exact_structured_ack_binding(self):
        probe = {
            "version": 1,
            "mode": "commit",
            "status": "writable",
            "checked_at": "2026-07-18T00:00:00+00:00",
            "git_metadata_paths": ["/repo/.git"],
            "checks": [
                {
                    "resource": "git-metadata",
                    "path": "/repo/.git",
                    "status": "writable",
                    "detail": "ok",
                }
            ],
        }
        legacy = {
            "contract_schema_revision": 3,
            "active_attempt_id": "T001-A001",
            "semantic_ack_barrier": {
                "status": "approved",
                "ack_event_id": "ack-current",
            },
            "completion_mode": "commit",
            "completion_mode_attempt_id": "T001-A001",
            "completion_mode_ack_event_id": "ack-other",
            "completion_mode_probe": dict(probe, mode="handoff"),
        }
        with self.assertRaisesRegex(hloop.HLoopError, "legacy unstructured ACK"):
            hloop.approved_semantic_ack_event_id("T001", legacy)

        structured = {
            **legacy,
            "semantic_ack_barrier": {
                "kind": "initial",
                "message_id": "initial:T001-A001",
                "status": "approved",
                "ack_event_id": "ack-current",
                "ack_sequence": 7,
                "semantic_decision": {
                    "status": "approved",
                    "ack_event_id": "ack-current",
                    "ack_sequence": 7,
                },
            },
            "completion_mode_ack_event_id": "ack-current",
            "completion_mode_probe": probe,
        }
        self.assertEqual(
            hloop.approved_semantic_ack_event_id("T001", structured),
            "ack-current",
        )
        structured["completion_mode_attempt_id"] = "T001-A000"
        with self.assertRaisesRegex(hloop.HLoopError, "attempt binding"):
            hloop.approved_semantic_ack_event_id("T001", structured)
    def test_role_ack_status_emits_event_and_only_manager_consumer_applies(self):
        manager_next = hloop.build_parser().parse_args(["manager", "next"])
        self.assertTrue(hloop.command_requires_loop_lock(manager_next))
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            ack_event_id = "11111111-1111-4111-8111-111111111111"
            digest = "a" * 64
            state = {
                "run_id": "run-1",
                "tasks": {
                    "T001": {
                        "attempt_id": "T001-A001",
                        "active_attempt_id": "T001-A001",
                        "semantic_ack_barrier": {
                            "message_id": "initial:T001-A001",
                            "digest": digest,
                            "status": "approved",
                            "semantic_decision": {
                                "status": "approved",
                                "ack_event_id": ack_event_id,
                            },
                            "approval_application": {"status": "pending"},
                        },
                    }
                },
            }
            hloop.state_path(repo).parent.mkdir(parents=True, exist_ok=True)
            hloop.save_state(repo, state)
            hloop.register_role_report_identity_and_ack_floor(
                repo,
                state,
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest=digest,
            )
            source = hloop.state_path(repo).read_bytes()
            args = argparse.Namespace(
                repo=str(repo),
                agent_id="T001",
                attempt_id="T001-A001",
                acknowledge=False,
                apply=True,
                json=False,
            )
            with mock.patch.dict(
                os.environ, {"HLOOP_MANAGER_REPO": str(repo)}, clear=False
            ):
                self.assertEqual(hloop.cmd_agent_ack_status(args), 0)
            self.assertEqual(hloop.state_path(repo).read_bytes(), source)

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                events = store.events(transaction)
            application_event = events[-1]
            self.assertEqual(application_event["type"], "attention")
            self.assertEqual(
                application_event["approval_application"]["requested_status"],
                "applied",
            )
            manager_state = hloop.load_state(repo)
            self.assertTrue(
                hloop.apply_semantic_ack_application_event(
                    manager_state, application_event
                )
            )
            application = manager_state["tasks"]["T001"][
                "semantic_ack_barrier"
            ]["approval_application"]
            self.assertEqual(application["status"], "applied")
            self.assertEqual(
                application["application_event_id"], application_event["event_id"]
            )
            self.assertFalse(
                hloop.apply_semantic_ack_application_event(
                    manager_state, application_event
                )
            )

    def test_manager_rejects_stale_ack_application_event(self):
        ack_event_id = "11111111-1111-4111-8111-111111111111"
        state = {
            "run_id": "run-1",
            "tasks": {
                "T001": {
                    "active_attempt_id": "T001-A002",
                    "semantic_ack_barrier": {
                        "message_id": "initial:T001-A002",
                        "digest": "b" * 64,
                        "semantic_decision": {
                            "status": "approved",
                            "ack_event_id": ack_event_id,
                        },
                        "approval_application": {"status": "pending"},
                    },
                }
            },
        }
        report = hloop.hloop_events.validate_report(
            {
                "run_id": "run-1",
                "role_id": "T001",
                "attempt_id": "T001-A001",
                "task_contract_digest": "b" * 64,
                "type": "attention",
                "stage": "semantic-ack-application",
                "summary": "apply",
                "impact": "approval pending",
                "attempted": ["read decision"],
                "options": ["apply"],
                "recommendation": "apply",
                "blocked_scope": ["material work"],
                "approval_application": {
                    "message_id": "initial:T001-A002",
                    "decision_ack_event_id": ack_event_id,
                    "requested_status": "applied",
                },
                "next": "manager applies",
                "needs_manager": True,
                "evidence_refs": ["semantic-ack:" + ack_event_id],
                "created_at": "2026-07-18T00:00:00+00:00",
            }
        )
        event = hloop.hloop_events.prepare_client_event(report)
        with self.assertRaisesRegex(hloop.HLoopError, "stale attempt"):
            hloop.apply_semantic_ack_application_event(state, event)

    def test_decision_and_application_are_separate_and_carry_completion_mode(self):
        agent = {
            "active_attempt_id": "T001-A001",
            "contract_schema_revision": 3,
        }
        hloop.arm_semantic_ack_barrier(
            agent,
            message_id="initial:T001-A001",
            digest="a" * 64,
            kind="initial",
        )
        barrier = hloop.resolve_semantic_ack_barrier(
            agent,
            decision="approve",
            reason="contract understood",
            latest_ack={
                "event_id": "ack-1",
                "sequence": 1,
                "completion_mode": "handoff",
                "completion_mode_probe": {
                    "version": 1,
                    "mode": "handoff",
                    "status": "unwritable",
                    "checked_at": "2026-07-18T00:00:00+00:00",
                    "git_metadata_paths": ["/repo/.git"],
                    "checks": [
                        {
                            "resource": "git-metadata",
                            "path": "/repo/.git",
                            "status": "unwritable",
                            "detail": "EACCES",
                        }
                    ],
                },
            },
        )

        self.assertEqual(barrier["semantic_decision"]["status"], "approved")
        self.assertEqual(barrier["approval_application"]["status"], "pending")
        self.assertEqual(agent["completion_mode"], "handoff")
        self.assertEqual(agent["completion_mode_attempt_id"], "T001-A001")
        self.assertEqual(agent["completion_mode_ack_event_id"], "ack-1")
        self.assertEqual(agent["completion_mode_probe"]["status"], "unwritable")
        self.assertEqual(barrier["status"], "approved")

    def test_later_ack_cannot_select_or_overwrite_completion_mode(self):
        probe = {
            "version": 1,
            "mode": "handoff",
            "status": "unwritable",
            "checked_at": "2026-07-18T00:00:00+00:00",
            "git_metadata_paths": ["/repo/.git"],
            "checks": [
                {
                    "resource": "git-metadata",
                    "path": "/repo/.git",
                    "status": "unwritable",
                    "detail": "EACCES",
                }
            ],
        }
        late = {
            "active_attempt_id": "T001-A001",
            "contract_schema_revision": 3,
        }
        hloop.arm_semantic_ack_barrier(
            late,
            message_id="contract-change",
            digest="a" * 64,
            kind="task-contract",
        )
        with self.assertRaisesRegex(hloop.HLoopError, "initial semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                late,
                decision="approve",
                reason="too late",
                latest_ack={
                    "event_id": "ack-late",
                    "sequence": 1,
                    "task_contract_digest": "a" * 64,
                    "completion_mode": "handoff",
                    "completion_mode_probe": probe,
                },
            )

        agent = {
            "active_attempt_id": "T001-A001",
            "contract_schema_revision": 3,
        }
        hloop.arm_semantic_ack_barrier(
            agent,
            message_id="initial:T001-A001",
            digest="a" * 64,
            kind="initial",
        )
        hloop.resolve_semantic_ack_barrier(
            agent,
            decision="approve",
            reason="initial binding",
            latest_ack={
                "event_id": "ack-initial",
                "sequence": 1,
                "completion_mode": "handoff",
                "completion_mode_probe": probe,
            },
        )
        hloop.arm_semantic_ack_barrier(
            agent,
            message_id="contract-change",
            digest="b" * 64,
            kind="task-contract",
            required_reack_after_sequence=1,
        )
        drift_probe = dict(probe, mode="commit", status="writable")
        with self.assertRaisesRegex(hloop.HLoopError, "completion mode drift"):
            hloop.resolve_semantic_ack_barrier(
                agent,
                decision="approve",
                reason="must retain initial binding",
                latest_ack={
                    "event_id": "ack-drift",
                    "sequence": 2,
                    "task_contract_digest": "b" * 64,
                    "completion_mode": "commit",
                    "completion_mode_probe": drift_probe,
                },
            )
        self.assertEqual(agent["completion_mode"], "handoff")
        self.assertEqual(agent["completion_mode_ack_event_id"], "ack-initial")


class ValidationAndConvergenceTests(unittest.TestCase):
    def passing_l3(self, target: str = "b" * 40) -> dict:
        return {
            "validation_id": "V1",
            "head_sha": target,
            "level": "L3",
            "stale": False,
            "results": [{"command": "full", "result": "passed"}],
            "execution_provenance": {
                "execution_id": "validation-manager-1",
                "role_id": "manager",
            },
            "independent_verification": {
                "commands": ["independent"],
                "results": [{"command": "independent", "result": "passed"}],
                "execution_id": "validation-independent-1",
                "role_id": "V001",
                "role_attempt_id": "V001-A001",
                "report_event_id": "event-independent-1",
                "artifact": ".ai/herdr-dev-loop/loops/default/validation/V001.json",
                "source_execution_id": "validation-manager-1",
                "source_role_id": "manager",
            },
        }

    def test_schema_three_release_gates_and_audit_prompts_reverify_l3_provenance(self):
        target = "b" * 40
        state = {
            "schema_revision": 3,
            "run_id": "run-1",
            "last_validation": self.passing_l3(target),
            "validation_stale": False,
        }
        with mock.patch.object(
            hloop, "manager_validation_provenance_is_current", return_value=False
        ):
            self.assertFalse(
                hloop.release_validation_is_current(Path("."), state, target)
            )
            reviewer = hloop.render_reviewer_prompt(
                "R001", "main", "integration", state, head_sha=target, repo=Path(".")
            )
            gap = hloop.render_gap_prompt(
                "G001",
                "main",
                "integration",
                [],
                state,
                head_sha=target,
                repo=Path("."),
            )
        self.assertIn("No reusable fresh L3 evidence", reviewer)
        self.assertIn("No reusable fresh L3 evidence", gap)

        with mock.patch.object(
            hloop, "manager_validation_provenance_is_current", return_value=True
        ):
            self.assertTrue(
                hloop.release_validation_is_current(Path("."), state, target)
            )
            reviewer = hloop.render_reviewer_prompt(
                "R001", "main", "integration", state, head_sha=target, repo=Path(".")
            )
            gap = hloop.render_gap_prompt(
                "G001",
                "main",
                "integration",
                [],
                state,
                head_sha=target,
                repo=Path("."),
            )
        self.assertIn("Fresh L3 Manager validation already covers", reviewer)
        self.assertIn("Fresh L3 Manager validation already covers", gap)

        legacy = dict(state, schema_revision=2)
        with mock.patch.object(
            hloop, "manager_validation_provenance_is_current", return_value=False
        ):
            self.assertTrue(
                hloop.release_validation_is_current(Path("."), legacy, target)
            )

    def test_fresh_l3_requires_current_official_and_independent_evidence(self):
        target = "b" * 40
        state = {
            "last_validation": self.passing_l3(target),
            "validation_stale": False,
            "independent_validation_commands": ["independent"],
        }
        with mock.patch.object(
            hloop, "manager_validation_provenance_is_current", return_value=True
        ):
            evidence = hloop.fresh_l3_validation_evidence(
                state, target, repo=Path(".")
            )
            self.assertEqual(evidence["validation_id"], "V1")

            state["independent_validation_commands"] = ["independent", "new-check"]
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["independent_validation_commands"] = ["independent"]
            state["last_validation"] = self.passing_l3(target)
            del state["last_validation"]["independent_verification"]
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["last_validation"] = self.passing_l3(target)
            state["last_validation"]["independent_verification"]["results"][0][
                "command"
            ] = "different-check"
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["last_validation"] = self.passing_l3(target)
            state["last_validation"]["independent_verification"]["results"][0]["result"] = "failed:1"
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["last_validation"] = self.passing_l3(target)
            state["last_validation"]["independent_verification"]["results"] = []
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["last_validation"] = self.passing_l3(target)
            state["last_validation"]["independent_verification"]["role_id"] = "manager"
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["last_validation"] = self.passing_l3(target)
            state["last_validation"]["independent_verification"]["execution_id"] = (
                "validation-manager-1"
            )
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

            state["last_validation"] = self.passing_l3(target)
            del state["last_validation"]["independent_verification"]["role_id"]
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=Path("."))
            )

    def test_fresh_l3_binds_current_identity_history_and_manager_broker_event(self):
        target = "f" * 40
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            state = {
                "run_id": "run-manager-validation",
                "validation_stale": False,
                "independent_validation_commands": [],
            }
            identity = hloop.build_validation_identity(
                repo, state, target, ["python3 -m unittest"]
            )
            record = {
                "validation_id": "V-canonical",
                "head_sha": target,
                "level": "L3",
                "stale": False,
                "completed_at": hloop.now_iso(),
                "commands": ["python3 -m unittest"],
                "results": [
                    {"command": "python3 -m unittest", "result": "passed"}
                ],
                "validation_identity": identity,
                "execution_provenance": {
                    "execution_id": "validation-manager-canonical",
                    "role_id": "manager",
                    "role_attempt_id": hloop.manager_validation_attempt_id(state),
                },
                "independent_verification": {"commands": [], "results": []},
            }
            record["execution_provenance"].update(
                hloop.record_manager_validation_attestation(repo, state, record)
            )
            state["last_validation"] = record
            state["validation_history"] = [
                hloop.json.loads(hloop.json.dumps(record))
            ]

            self.assertIsNotNone(
                hloop.fresh_l3_validation_evidence(state, target, repo=repo)
            )

            mutated = hloop.json.loads(hloop.json.dumps(state))
            mutated["last_validation"]["validation_identity"]["commands"] = [
                "fabricated"
            ]
            mutated["validation_history"] = [mutated["last_validation"]]
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(mutated, target, repo=repo)
            )

            mutated = hloop.json.loads(hloop.json.dumps(state))
            mutated["last_validation"]["execution_provenance"]["role_id"] = "worker"
            mutated["validation_history"] = [mutated["last_validation"]]
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(mutated, target, repo=repo)
            )

            mutated = hloop.json.loads(hloop.json.dumps(state))
            mutated["last_validation"]["execution_provenance"]["report_event_id"] = (
                "00000000-0000-4000-8000-000000000000"
            )
            mutated["validation_history"] = [mutated["last_validation"]]
            self.assertIsNone(
                hloop.fresh_l3_validation_evidence(mutated, target, repo=repo)
            )

    def test_explicit_empty_validation_groups_do_not_restore_prior_commands(self):
        state = {
            "integration_branch": "main",
            "independent_validation_commands": [],
            "auxiliary_validation_commands": [],
            "last_validation": {
                "independent_verification": {"commands": ["old-independent"]},
                "auxiliary_validation": {"commands": ["old-auxiliary"]},
            },
        }
        args = argparse.Namespace(
            repo=".",
            dry_run=True,
            validation_command=["official"],
            level="L3",
            independent_command=None,
            auxiliary_command=None,
            validation_execution_id=None,
            validation_role_id=None,
            independent_execution_id=None,
            independent_role_id=None,
        )
        with mock.patch.object(
            hloop, "repo_root", return_value=Path(".").resolve()
        ), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(
            hloop, "git", return_value="a" * 40
        ), mock.patch.object(
            hloop, "_refresh_validation_staleness"
        ), mock.patch.object(
            hloop, "build_validation_identity", return_value={"digest": "identity"}
        ), mock.patch.object(
            hloop, "authenticated_independent_validation_results"
        ) as authenticated, mock.patch(
            "builtins.print"
        ) as printed:
            result = hloop.cmd_validate(args)

        self.assertEqual(result, 0)
        authenticated.assert_not_called()
        rendered = "\n".join(
            " ".join(map(str, call.args)) for call in printed.call_args_list
        )
        self.assertIn("official", rendered)
        self.assertNotIn("old-independent", rendered)
        self.assertNotIn("old-auxiliary", rendered)

    def test_independent_validation_requires_authenticated_registered_role_report(self):
        target = "b" * 40
        execution_id = "validation-independent-1"
        command = "python3 -m unittest tests.independent"
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            artifact = (
                repo
                / ".ai/herdr-dev-loop/loops/default/reviews/R001.md"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                "---\n"
                "review_id: R001\n"
                "run_id: run-1\n"
                f"skill_version: {hloop.SKILL_VERSION}\n"
                f"head_sha: {target}\n"
                "status: reported\n"
                "---\n\n# authenticated evidence\n",
                encoding="utf-8",
            )
            artifact_digest = hloop._sha256_labelled(artifact.read_bytes())
            attestation = {
                "record_type": "independent_validation_result",
                "execution_id": execution_id,
                "command": command,
                "result": "passed",
                "head_sha": target,
                "evidence_ref": "reviews/R001.md#validation",
            }
            event = {
                "event_id": "11111111-1111-4111-8111-111111111111",
                "run_id": "run-1",
                "role_id": "R001",
                "attempt_id": "R001-A001",
                "type": "completion",
                "head_sha": target,
                "artifact": str(artifact.relative_to(repo)),
                "artifact_digest": artifact_digest,
                "validation_results": [
                    hloop.json.dumps(attestation, sort_keys=True)
                ],
            }

            class FakeTransaction:
                def __enter__(self):
                    return object()

                def __exit__(self, *_args):
                    return False

            class FakeStore:
                def transaction(self):
                    return FakeTransaction()

                def latest_role_event(self, _transaction, **_identity):
                    return event

            state = {
                "run_id": "run-1",
                "reviews": {
                    "R001": {
                        "attempt_id": "R001-A001",
                        "status": "reported",
                        "gate_status": "reported",
                        "head_sha": target,
                        "skill_version": hloop.SKILL_VERSION,
                        "harvested_at": hloop.now_iso(),
                        "artifact_digest": artifact_digest,
                    }
                },
            }
            with mock.patch.object(
                hloop, "_open_broker_store", return_value=FakeStore()
            ):
                results, provenance = (
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )
                )
                with self.assertRaisesRegex(
                    hloop.HLoopError, "not registered"
                ):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="V999",
                    )
                state["reviews"]["R001"]["harvested_at"] = ""
                with self.assertRaisesRegex(hloop.HLoopError, "terminal and harvested"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )
                state["reviews"]["R001"]["harvested_at"] = hloop.now_iso()
                state["reviews"]["R001"].update(
                    {"status": "running", "gate_status": "running"}
                )
                with self.assertRaisesRegex(hloop.HLoopError, "terminal and harvested"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )
                state["reviews"]["R001"].update(
                    {"status": "reported", "gate_status": "reported"}
                )
                with self.assertRaisesRegex(hloop.HLoopError, "fresh authenticated"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                        forbidden_event_id=event["event_id"],
                    )

                event["head_sha"] = "c" * 40
                with self.assertRaisesRegex(hloop.HLoopError, "different SHA"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )
                event["head_sha"] = target

                event["attempt_id"] = "R001-A000"
                with self.assertRaisesRegex(hloop.HLoopError, "role attempt"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )
                event["attempt_id"] = "R001-A001"

                event["artifact_digest"] = "sha256:" + "0" * 64
                with self.assertRaisesRegex(hloop.HLoopError, "digest"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )
                event["artifact_digest"] = artifact_digest

                substituted = repo / hloop.LOOP_DIR / "JOURNAL.md"
                substituted.write_text("arbitrary\n", encoding="utf-8")
                event["artifact"] = str(substituted.relative_to(repo))
                with self.assertRaisesRegex(hloop.HLoopError, "canonical"):
                    hloop.authenticated_independent_validation_results(
                        repo,
                        state,
                        commands=[command],
                        target_sha=target,
                        execution_id=execution_id,
                        role_id="R001",
                    )

        self.assertEqual(results[0]["result"], "passed")
        self.assertEqual(
            provenance["report_event_id"],
            "11111111-1111-4111-8111-111111111111",
        )

    def test_convergence_draft_requires_same_sha_reviewer_gap_and_fresh_l3(self):
        target = "c" * 40
        state = {
            "last_validation": self.passing_l3(target),
            "validation_stale": False,
            "review_epochs": {
                "active_epoch_id": "E001",
                "records": {
                    "E001": {
                        "active_revision": 1,
                        "revisions": {
                            "1": {
                                "plan": {
                                    "epoch_id": "E001",
                                    "epoch_revision": 1,
                                    "target_sha": target,
                                    "plan_digest": "sha256:" + "d" * 64,
                                    "required_executions": [
                                        {"execution_id": "R001", "source_kind": "reviewer"},
                                        {"execution_id": "G001", "source_kind": "gap"},
                                    ],
                                },
                                "execution_outcomes": [
                                    {"execution_id": "R001", "status": "succeeded", "artifact_complete": True, "artifact_ref": "reviews/R001.md"},
                                    {"execution_id": "G001", "status": "succeeded", "artifact_complete": True, "artifact_ref": "gaps/G001.md"},
                                ],
                            }
                        },
                    }
                },
            },
        }

        with mock.patch.object(
            hloop, "manager_validation_provenance_is_current", return_value=True
        ):
            draft = hloop.convergence_draft_projection(
                state, target, repo=Path(".")
            )
        self.assertEqual(draft["target_sha"], target)
        self.assertEqual({item["source_kind"] for item in draft["executions"]}, {"reviewer", "gap"})
        self.assertFalse(draft["manual_final_claimed"])


class RequirementPlanningAndMetricsTests(unittest.TestCase):
    @unittest.skipIf(jsonschema is None, "jsonschema is unavailable")
    def test_outcome_schema_keeps_new_metrics_optional_for_legacy_records(self):
        schema_path = (
            SCRIPT.parents[1] / "references" / "schemas" / "outcome.schema.json"
        )
        schema = hloop.json.loads(schema_path.read_text(encoding="utf-8"))
        metrics_schema = schema["$defs"]["execution_metrics"]
        legacy_required = [
            "planned_task_count",
            "remediation_task_count",
            "task_origin_counts",
            "scope_revision_counts",
            "review_fix_rounds",
            "candidate_count",
            "confirmed_count",
            "finding_origin_counts",
            "finding_contract_relation_counts",
            "finding_decision_requirement_counts",
            "finding_disposition_counts",
            "review_completed_count",
            "stale_review_count",
            "aborted_review_count",
            "timeout_review_count",
            "gap_completed_count",
            "stale_gap_count",
            "aborted_gap_count",
            "timeout_gap_count",
            "worker_count",
            "planned_task_completed",
            "scope_expansion_started_at",
            "scope_expansion_user_input_id",
            "effective_parallelism",
            "phase_wall_time_seconds",
            "validation_time_seconds",
            "review_wait_time_seconds",
            "longest_worker_seconds",
        ]
        self.assertEqual(metrics_schema["required"], legacy_required)
        current = hloop.hloop_reports.ExecutionMetrics().to_record()
        legacy = {field: current[field] for field in legacy_required}
        validator = jsonschema.Draft202012Validator(
            {
                "$schema": schema["$schema"],
                "$defs": schema["$defs"],
                "$ref": "#/$defs/execution_metrics",
            }
        )
        validator.validate(legacy)
        validator.validate(current)

    def test_finish_persists_one_terminal_snapshot_before_final_and_recovers_retry(self):
        target = "9" * 40
        timestamp = "2026-07-18T03:04:05+00:00"
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            state = {"phase": "dispatching", "integration_branch": "main"}
            persisted = []
            ordering = []

            def save_terminal(_repo, current):
                ordering.append("state")
                self.assertEqual(
                    hloop.terminal_finish_snapshot_error(current, target), ""
                )
                persisted.append(hloop.json.loads(hloop.json.dumps(current)))

            def fail_report(_repo, current, current_target):
                ordering.append("report")
                self.assertEqual(current_target, target)
                self.assertEqual(current["finished_at"], timestamp)
                raise OSError("simulated FINAL write failure")

            args = argparse.Namespace(repo=str(repo))
            with mock.patch.object(
                hloop, "repo_root", return_value=repo
            ), mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "git", return_value=target
            ), mock.patch.object(
                hloop, "completion_errors", return_value=[]
            ), mock.patch.object(
                hloop, "write_requirement_artifacts"
            ), mock.patch.object(
                hloop, "write_progress_artifact"
            ), mock.patch.object(
                hloop, "_record_metric_duration"
            ), mock.patch.object(
                hloop, "now_iso", return_value=timestamp
            ), mock.patch.object(
                hloop, "save_state", side_effect=save_terminal
            ), mock.patch.object(
                hloop,
                "recover_or_confirm_final_report",
                side_effect=fail_report,
            ), mock.patch.object(hloop, "journal"):
                with self.assertRaisesRegex(OSError, "FINAL write failure"):
                    hloop.cmd_finish(args)

            self.assertEqual(ordering, ["state", "report"])
            self.assertEqual(len(persisted), 1)
            terminal_snapshot = persisted[0]

            recovered = []

            def recover(_repo, current, current_target):
                recovered.append(hloop.json.loads(hloop.json.dumps(current)))
                self.assertEqual(current_target, target)
                return repo / hloop.LOOP_DIR / "reports" / "FINAL.md", True

            with mock.patch.object(
                hloop, "repo_root", return_value=repo
            ), mock.patch.object(
                hloop, "preflight_loop", return_value=terminal_snapshot
            ), mock.patch.object(
                hloop, "git", return_value=target
            ), mock.patch.object(
                hloop, "advancing_commit_count", return_value=0
            ), mock.patch.object(
                hloop, "recover_or_confirm_final_report", side_effect=recover
            ):
                self.assertEqual(hloop.cmd_finish(args), 0)

            self.assertEqual(recovered, [terminal_snapshot])
            self.assertEqual(recovered[0]["finished_at"], timestamp)

    def test_finish_state_write_failure_never_attempts_final_projection(self):
        target = "8" * 40
        state = {"phase": "dispatching", "integration_branch": "main"}
        args = argparse.Namespace(repo=".")
        with mock.patch.object(
            hloop, "repo_root", return_value=Path(".").resolve()
        ), mock.patch.object(
            hloop, "preflight_loop", return_value=state
        ), mock.patch.object(
            hloop, "git", return_value=target
        ), mock.patch.object(
            hloop, "completion_errors", return_value=[]
        ), mock.patch.object(
            hloop, "write_requirement_artifacts"
        ), mock.patch.object(
            hloop, "write_progress_artifact"
        ), mock.patch.object(
            hloop, "_record_metric_duration"
        ), mock.patch.object(
            hloop, "now_iso", return_value="2026-07-18T03:04:05+00:00"
        ), mock.patch.object(
            hloop, "save_state", side_effect=OSError("state write failed")
        ), mock.patch.object(
            hloop, "recover_or_confirm_final_report"
        ) as final_projection:
            with self.assertRaisesRegex(OSError, "state write failed"):
                hloop.cmd_finish(args)
        final_projection.assert_not_called()

    def test_final_projection_matches_terminal_state_identity_and_timing(self):
        target = "7" * 40
        finished_at = "2026-07-18T03:04:05+00:00"
        terminal = {
            "status": "done",
            "target_sha": target,
            "recorded_at": finished_at,
            "report": (hloop.LOOP_DIR / "reports" / "FINAL.md").as_posix(),
        }
        progress = hloop.hloop_requirements.RequirementProgress(
            "REQ-001",
            status="verified",
            evidence=(
                hloop.hloop_requirements.EvidenceRef(
                    kind="artifact",
                    reference="results/T001/result.md",
                    verified_by="hloop",
                    head_sha=target,
                ),
                hloop.hloop_requirements.EvidenceRef(
                    kind="test",
                    reference="full-suite",
                    verified_by="hloop",
                    head_sha=target,
                    result="passed",
                ),
            ),
        )
        gate = hloop.hloop_reports.OutcomeGate(
            name="validation",
            status="passed",
            evidence_refs=("full-suite",),
            target_sha=target,
            verified_by="hloop",
        )
        report = hloop.hloop_reports.final_outcome(
            run_id="run-terminal",
            goal="terminal equality",
            generated_at=finished_at,
            requirement_progress=(progress,),
            gates=(gate,),
            integration_target_sha=target,
            current_branch_sha=target,
            phase="done",
            final_target_sha=target,
            finished_at=finished_at,
            terminal_outcome=terminal,
        )
        record = report.to_record()
        self.assertEqual(record["phase"], "done")
        self.assertEqual(record["final_target_sha"], target)
        self.assertEqual(record["generated_at"], finished_at)
        self.assertEqual(record["finished_at"], finished_at)
        self.assertEqual(record["terminal_outcome"], terminal)
        rendered = hloop.hloop_reports.render_outcome_markdown(report)
        self.assertIn(f"- Finished: `{finished_at}`", rendered)
        self.assertIn(f"- Final target: `{target}`", rendered)

    def test_operational_mutators_use_serialized_schema_guarded_boundary(self):
        parser = hloop.build_parser()
        mutating = (
            parser.parse_args(["review", "convergence", "draft"]),
            parser.parse_args(["requirements", "reconcile", "--apply"]),
        )
        read_only = (
            parser.parse_args(["review", "convergence", "draft", "--dry-run"]),
            parser.parse_args(["requirements", "reconcile", "--dry-run"]),
        )

        for args in mutating:
            with self.subTest(command=hloop.material_command_identity(args)):
                self.assertTrue(hloop.command_requires_loop_lock(args))
                self.assertTrue(hloop.command_requires_state_schema_guard(args))
                self.assertTrue(hloop.should_record_first_v053_mutation(args))
        for args in read_only:
            with self.subTest(command=hloop.material_command_identity(args)):
                self.assertFalse(hloop.command_requires_loop_lock(args))
                self.assertFalse(hloop.command_requires_state_schema_guard(args))
                self.assertFalse(hloop.should_record_first_v053_mutation(args))

    def test_operational_dry_runs_preserve_every_managed_file_byte_for_byte(self):
        target = "e" * 40
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            loop = repo / hloop.LOOP_DIR
            managed = {
                "STATE.json": b'{"sentinel":"state"}\n',
                "JOURNAL.md": b"# sentinel journal\n",
                "reviews/convergence/DRAFT.json": b'{"sentinel":"draft"}\n',
                "requirements/REQUIREMENTS.md": b"# sentinel requirements\n",
                "requirements/STATUS.md": b"# sentinel status\n",
                "progress/P0001.md": b"# sentinel progress\n",
                "progress/LATEST.md": b"# sentinel latest\n",
            }
            for relative, content in managed.items():
                path = loop / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            def snapshot():
                return {
                    path.relative_to(loop).as_posix(): path.read_bytes()
                    for path in loop.rglob("*")
                    if path.is_file()
                }

            state = {"requirements": {}, "tasks": {}}
            cases = (
                (
                    hloop.cmd_review_convergence_draft,
                    argparse.Namespace(repo=str(repo), dry_run=True, json=True),
                    mock.patch.object(
                        hloop,
                        "convergence_draft_projection",
                        return_value={"record_type": "review_convergence_draft"},
                    ),
                ),
                (
                    hloop.cmd_requirements_reconcile,
                    argparse.Namespace(
                        repo=str(repo), dry_run=True, apply=False, json=True
                    ),
                    mock.patch.object(
                        hloop,
                        "requirement_reconciliation_projection",
                        return_value={},
                    ),
                ),
            )
            for command, args, projection_patch in cases:
                with self.subTest(command=command.__name__):
                    before = snapshot()
                    with mock.patch.object(
                        hloop, "repo_root", return_value=repo
                    ), mock.patch.object(
                        hloop, "preflight_loop", return_value=state
                    ), mock.patch.object(
                        hloop, "current_integration_target", return_value=target
                    ), projection_patch, mock.patch("builtins.print"):
                        self.assertEqual(command(args), 0)
                    self.assertEqual(snapshot(), before)

    def test_requirements_reconcile_exposes_explicit_read_only_mode(self):
        parser = hloop.build_parser()
        args = parser.parse_args(["requirements", "reconcile", "--dry-run"])
        self.assertTrue(args.dry_run)
        self.assertFalse(args.apply)
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            parser.parse_args(
                ["requirements", "reconcile", "--dry-run", "--apply"]
            )

    def test_reconcile_uses_superseded_ledger_status_as_terminal_source(self):
        requirement = hloop.hloop_requirements.Requirement(
            requirement_id="REQ-001",
            source_inputs=("U0001",),
            acceptance=("legacy behavior is replaced",),
            priority="P1",
            accepted_at="2026-07-18T00:00:00+00:00",
            status="superseded",
            superseded_by="REQ-002",
        )
        state = {"requirements": {"REQ-001": requirement.to_record()}, "tasks": {}}

        current = hloop.requirement_progress_from_state(state, "REQ-001")
        suggestion = hloop.requirement_reconciliation_projection(
            state, target_sha="e" * 40
        )["REQ-001"]
        updated = hloop._apply_reconciled_requirement_progress(
            current,
            suggestion["recommended_status"],
            task_ids=tuple(suggestion["task_ids"]),
        )

        self.assertEqual(current.status, "superseded")
        self.assertEqual(suggestion["recommended_status"], "superseded")
        self.assertEqual(updated.status, "superseded")

    def test_reconcile_preserves_terminal_superseded_progress_cross_product(self):
        progress = {
            "requirement_id": "REQ-001",
            "status": "superseded",
            "task_ids": [],
            "evidence": [],
            "remaining_work": "",
            "blockers": [],
        }
        for ledger_status in ("accepted", "superseded"):
            for task_status in (None, "merged", "running"):
                with self.subTest(
                    ledger_status=ledger_status, task_status=task_status
                ):
                    requirement = {
                        "status": ledger_status,
                        "progress": dict(progress),
                    }
                    if ledger_status == "superseded":
                        requirement["superseded_by"] = "REQ-002"
                    tasks = (
                        {
                            "T001": {
                                "status": task_status,
                                "requirement_refs": ["REQ-001"],
                            }
                        }
                        if task_status is not None
                        else {}
                    )
                    state = {
                        "requirements": {"REQ-001": requirement},
                        "tasks": tasks,
                    }

                    suggestion = hloop.requirement_reconciliation_projection(
                        state, target_sha="e" * 40
                    )["REQ-001"]
                    updated = hloop._apply_reconciled_requirement_progress(
                        hloop.requirement_progress_from_state(state, "REQ-001"),
                        suggestion["recommended_status"],
                        task_ids=tuple(suggestion["task_ids"]),
                    )

                    self.assertEqual(
                        suggestion["recommended_status"], "superseded"
                    )
                    self.assertEqual(updated.status, "superseded")
                    forced = hloop._apply_reconciled_requirement_progress(
                        hloop.requirement_progress_from_state(state, "REQ-001"),
                        "implemented_unverified",
                        task_ids=tuple(suggestion["task_ids"]),
                    )
                    self.assertEqual(forced.status, "superseded")

    def test_reconcile_never_verifies_from_task_or_agent_evidence_alone(self):
        state = {
            "requirements": {"REQ-001": {"status": "accepted"}},
            "requirement_progress": {},
            "tasks": {
                "T001": {
                    "status": "merged",
                    "requirement_refs": ["REQ-001"],
                }
            },
        }
        projection = hloop.requirement_reconciliation_projection(state, target_sha="e" * 40)
        self.assertEqual(projection["REQ-001"]["recommended_status"], "implemented_unverified")
        self.assertNotEqual(projection["REQ-001"]["recommended_status"], "verified")

    def test_reconcile_does_not_recommend_implementation_from_unmerged_tasks(self):
        for status in ("result_reported", "done"):
            with self.subTest(status=status):
                state = {
                    "requirements": {"REQ-001": {"status": "accepted"}},
                    "requirement_progress": {},
                    "tasks": {
                        "T001": {
                            "status": status,
                            "requirement_refs": ["REQ-001"],
                        }
                    },
                }
                projection = hloop.requirement_reconciliation_projection(
                    state, target_sha="e" * 40
                )
                self.assertEqual(
                    projection["REQ-001"]["recommended_status"], "in_progress"
                )

    def test_reconcile_preserves_explicit_progress_without_contrary_evidence(self):
        progress_records = {
            "in_progress": {
                "requirement_id": "REQ-001",
                "status": "in_progress",
                "task_ids": [],
                "evidence": [],
                "remaining_work": "implementation remains",
                "blockers": [],
            },
            "implemented_unverified": {
                "requirement_id": "REQ-001",
                "status": "implemented_unverified",
                "task_ids": [],
                "evidence": [],
                "remaining_work": "verification remains",
                "blockers": [],
            },
            "blocked": {
                "requirement_id": "REQ-001",
                "status": "blocked",
                "task_ids": [],
                "evidence": [],
                "remaining_work": "blocked work remains",
                "blockers": ["external dependency"],
            },
            "deferred": {
                "requirement_id": "REQ-001",
                "status": "deferred",
                "task_ids": [],
                "evidence": [],
                "remaining_work": "explicitly deferred",
                "blockers": [],
            },
            "verified": {
                "requirement_id": "REQ-001",
                "status": "verified",
                "task_ids": [],
                "evidence": [
                    {
                        "kind": "artifact",
                        "reference": "results/T001/result.md",
                        "verified_by": "hloop",
                        "head_sha": "e" * 40,
                        "result": "passed",
                    },
                    {
                        "kind": "test",
                        "reference": "validation/L3.log",
                        "verified_by": "manager",
                        "head_sha": "e" * 40,
                        "result": "passed",
                    },
                ],
                "remaining_work": "",
                "blockers": [],
            },
        }

        for status, progress in progress_records.items():
            with self.subTest(status=status):
                state = {
                    "requirements": {
                        "REQ-001": {"status": "accepted", "progress": progress}
                    },
                    "tasks": {},
                }
                projection = hloop.requirement_reconciliation_projection(
                    state, target_sha="e" * 40
                )
                suggestion = projection["REQ-001"]
                self.assertEqual(suggestion["recommended_status"], status)
                current = hloop.requirement_progress_from_state(state, "REQ-001")
                updated = hloop._apply_reconciled_requirement_progress(
                    current,
                    suggestion["recommended_status"],
                    task_ids=tuple(suggestion["task_ids"]),
                )
                self.assertEqual(updated.status, status)

    def test_reconcile_only_emits_applyable_forward_routes(self):
        cases = (
            ("not_started", [], "merged", "implemented_unverified"),
            ("in_progress", [], "merged", "implemented_unverified"),
            ("blocked", ["dependency"], "merged", "implemented_unverified"),
            ("deferred", [], "merged", "implemented_unverified"),
            ("not_started", [], "running", "in_progress"),
            ("blocked", ["dependency"], "running", "in_progress"),
            ("deferred", [], "running", "in_progress"),
            (
                "implemented_unverified",
                [],
                "running",
                "implemented_unverified",
            ),
        )
        for current_status, blockers, task_status, expected in cases:
            with self.subTest(current=current_status, task=task_status):
                progress = {
                    "requirement_id": "REQ-001",
                    "status": current_status,
                    "task_ids": [],
                    "evidence": [],
                    "remaining_work": "remaining" if current_status != "not_started" else "",
                    "blockers": blockers,
                }
                state = {
                    "requirements": {
                        "REQ-001": {"status": "accepted", "progress": progress}
                    },
                    "tasks": {
                        "T001": {
                            "status": task_status,
                            "requirement_refs": ["REQ-001"],
                        }
                    },
                }
                suggestion = hloop.requirement_reconciliation_projection(
                    state, target_sha="e" * 40
                )["REQ-001"]
                self.assertEqual(suggestion["recommended_status"], expected)
                updated = hloop._apply_reconciled_requirement_progress(
                    hloop.requirement_progress_from_state(state, "REQ-001"),
                    suggestion["recommended_status"],
                    task_ids=tuple(suggestion["task_ids"]),
                )
                self.assertEqual(updated.status, expected)

    def test_historical_tasks_do_not_stale_mutable_planning_projection(self):
        state = {
            "tasks": {
                "T001": {"status": "merged", "write_allow": ["src/shared.py"]},
                "T002": {"status": "aborted", "write_allow": ["src/shared.py"]},
                "T003": {"status": "queued", "write_allow": ["src/active.py"]},
                "T004": {"status": "running", "write_allow": ["src/active.py"]},
            }
        }
        self.assertEqual(hloop.mutable_planning_task_ids(state), ["T003", "T004"])
        graph = hloop.build_write_scope_conflict_graph(state)
        self.assertEqual(graph["task_ids"], ["T003", "T004"])
        self.assertEqual(graph["edges"][0]["tasks"], ["T003", "T004"])

        issues = hloop.planning_projection_issues(
            state,
            {
                "tasks": [
                    {"task_id": "T003", "depends_on": [], "write_allow": ["src/active.py"], "scope_refs": []},
                    {"task_id": "T004", "depends_on": [], "write_allow": ["src/active.py"], "scope_refs": []},
                ]
            },
        )
        self.assertEqual(issues, [])

    def test_metrics_are_derived_without_manufacturing_observations(self):
        state = {
            "created_at": "2026-07-18T00:00:00+00:00",
            "updated_at": "2026-07-18T00:01:00+00:00",
            "tasks": {
                "T001": {
                    "status": "merged",
                    "task_origin": "planned",
                    "completion_mode": "commit",
                    "patch_review_history": [
                        {
                            "review_attempt_id": "PR-T001-R001",
                            "verdict": "fix_required",
                            "unresolved_finding_fingerprints": ["sha256:" + "f" * 64],
                        }
                    ],
                    "semantic_ack_history": [
                        {
                            "armed_at": "2026-07-18T00:00:10+00:00",
                            "resolved_at": "2026-07-18T00:00:20+00:00",
                        }
                    ],
                },
                "T002": {"status": "queued", "task_origin": "finding", "completion_mode": "handoff"},
            },
            "reviews": {},
            "gaps": {},
            "validation_history": [
                {
                    "validation_id": "V1",
                    "head_sha": "a" * 40,
                    "level": "L3",
                    "reused": False,
                },
                {
                    "validation_id": "V2",
                    "head_sha": "a" * 40,
                    "level": "L3",
                    "reused": True,
                },
                {
                    "validation_id": "V3",
                    "head_sha": "b" * 40,
                    "level": "L3",
                    "reused": False,
                },
                {
                    "validation_id": "V-L1",
                    "head_sha": "a" * 40,
                    "level": "L1",
                    "reused": False,
                },
                {
                    "validation_id": "V-L2",
                    "head_sha": "b" * 40,
                    "level": "L2",
                    "reused": True,
                },
            ],
            "manager_sleep_history": [{"duration_seconds": 12.5}],
            "remediation_ledger": {
                "batches": [
                    {
                        "classification_conflicts": ["sha256:" + "c" * 64],
                        "observations": [{"id": "O1"}, {"id": "O2"}],
                        "canonical_candidates": [{"fingerprint": "sha256:" + "f" * 64}],
                    }
                ]
            },
            "execution_metrics": hloop._empty_execution_metrics(),
            "review_epochs": {
                "records": {
                    "E001": {
                        "revisions": {
                            "1": {
                                "plan": {
                                    "epoch_id": "E001",
                                    "epoch_revision": 1,
                                    "target_sha": "a" * 40,
                                    "required_executions": [
                                        {
                                            "execution_id": "R001",
                                            "source_kind": "reviewer",
                                            "processes": [
                                                {
                                                    "process_id": "R001-lane-correctness",
                                                    "process_kind": "discovery",
                                                    "lane_id": "correctness",
                                                    "provider": "codex",
                                                }
                                            ],
                                        }
                                    ],
                                },
                                "capacity": {
                                    "leases": [
                                        {
                                            "lease_id": "lease-R001",
                                            "reserved_slots": 1,
                                            "status": "terminal",
                                        }
                                    ],
                                    "reserved_slots": 0,
                                    "live_slots": 0,
                                },
                                "execution_outcomes": [
                                    {"execution_id": "R001", "status": "succeeded"}
                                ],
                            }
                        }
                    }
                }
            },
        }
        state["execution_metrics"].update(
            {
                "review_epoch_peak_live": {"E001:1": 2},
                "finding_metric_history": [
                    {
                        "manifest_digest": "sha256:manifest-1",
                        "fingerprints": [
                            "sha256:" + "f" * 64,
                            "sha256:" + "d" * 64,
                        ],
                        "confirmed_fingerprints": ["sha256:" + "f" * 64],
                    },
                    {
                        "manifest_digest": "sha256:manifest-2",
                        "fingerprints": ["sha256:" + "f" * 64],
                        "confirmed_fingerprints": ["sha256:" + "f" * 64],
                    },
                ],
            }
        )
        hloop._refresh_execution_metrics(state)
        metrics = state["execution_metrics"]
        self.assertEqual(metrics["completion_mode_counts"], {"commit": 1, "handoff": 1})
        self.assertEqual(metrics["validation_execution_count"], 2)
        self.assertEqual(metrics["validation_reuse_count"], 1)
        self.assertEqual(
            metrics["validation_by_target_sha"],
            [
                {
                    "target_sha": "a" * 40,
                    "execution_count": 1,
                    "reuse_count": 1,
                    "validation_ids": ["V1", "V2"],
                },
                {
                    "target_sha": "b" * 40,
                    "execution_count": 1,
                    "reuse_count": 0,
                    "validation_ids": ["V3"],
                },
            ],
        )
        self.assertEqual(metrics["review_epoch_metrics"][0]["planned_agent_count"], 1)
        self.assertEqual(metrics["review_epoch_metrics"][0]["reserved_agent_count"], 1)
        self.assertEqual(metrics["review_epoch_metrics"][0]["peak_live_agent_count"], 2)
        self.assertEqual(metrics["lane_metrics"][0]["lane_id"], "correctness")
        self.assertIsNone(metrics["lane_metrics"][0]["runtime_seconds"])
        self.assertIsNone(metrics["lane_metrics"][0]["finding_candidate_count"])
        self.assertEqual(metrics["role_usage_metrics"][0]["role"], "reviewer")
        self.assertIsNone(metrics["role_usage_metrics"][0]["token_count"])
        self.assertIsNone(metrics["role_usage_metrics"][0]["cost"])
        self.assertIsNone(metrics["artifact_reconcile_time_seconds"])
        self.assertIsNone(metrics["finish_preparation_time_seconds"])
        self.assertIsNone(metrics["provider_capacity_wait_time_seconds"])
        self.assertIsNone(metrics["escaped_finding_count"])
        self.assertEqual(metrics["manager_wait_time_seconds"], 22.5)
        self.assertEqual(metrics["ack_wait_time_seconds"], 10.0)
        self.assertEqual(metrics["manager_active_time_seconds"], 37.5)
        self.assertEqual(metrics["candidate_count"], 3)
        self.assertEqual(metrics["confirmed_count"], 1)
        self.assertEqual(metrics["classification_conflict_count"], 1)
        self.assertEqual(metrics["fingerprint_duplicate_rate"], 0.333333)
        self.assertEqual(metrics["patch_review_round_count"], 1)
        self.assertEqual(metrics["patch_findings_prevented_count"], 1)

        report = hloop.hloop_reports.draft_outcome(
            run_id="run-1",
            goal="metrics",
            generated_at="2026-07-18T00:01:00+00:00",
            requirement_progress=(),
            gates=(),
            integration_target_sha="a" * 40,
            current_branch_sha="a" * 40,
            execution_metrics=hloop.hloop_reports.ExecutionMetrics.from_record(metrics),
        )
        rendered = hloop.hloop_reports.render_outcome_markdown(report)
        self.assertIn("Validation target " + "a" * 40, rendered)
        self.assertIn("Epoch E001 r1", rendered)
        self.assertIn("Lane E001/R001/correctness", rendered)
        self.assertIn("Role usage reviewer", rendered)
        self.assertIn("artifact-reconcile=unknown", rendered)

    def test_metric_producers_accumulate_durations_and_preserve_epoch_peak(self):
        digest = "sha256:" + "a" * 64
        role_state = {
            "review_epoch_identity": {
                "epoch_id": "E001",
                "epoch_revision": 1,
                "execution_id": "R001",
                "attempt_id": "R001-A001",
                "plan_digest": digest,
            }
        }
        state = {
            "execution_metrics": hloop._empty_execution_metrics(),
            "tasks": {},
            "reviews": {"R001": role_state},
            "gaps": {},
            "review_epochs": {
                "records": {
                    "E001": {
                        "revisions": {
                            "1": {
                                "plan": {
                                    "epoch_id": "E001",
                                    "epoch_revision": 1,
                                    "plan_digest": digest,
                                    "target_sha": "a" * 40,
                                    "required_executions": [
                                        {
                                            "execution_id": "R001",
                                            "attempt_id": "R001-A001",
                                            "source_kind": "reviewer",
                                            "processes": [
                                                {
                                                    "process_id": "R001-lane-correctness",
                                                    "provider": "codex",
                                                    "lane_id": "correctness",
                                                }
                                            ],
                                        }
                                    ],
                                },
                                "capacity": {
                                    "leases": [],
                                    "reserved_slots": 0,
                                    "live_slots": 0,
                                },
                            }
                        }
                    }
                }
            },
        }
        hloop._record_metric_duration(state, "artifact_reconcile_time_seconds", 1.25)
        hloop._record_metric_duration(state, "artifact_reconcile_time_seconds", 0.75)
        hloop._record_metric_duration(
            state, "provider_capacity_wait_time_seconds", 0.5
        )

        collection = mock.Mock()
        collection.plan.epoch_id = "E001"
        collection.plan.epoch_revision = 1
        collection.capacity.live_slots = 3
        hloop._record_review_epoch_capacity_metrics(state, collection)
        collection.capacity.live_slots = 0
        hloop._record_review_epoch_capacity_metrics(state, collection)
        lane = mock.Mock(
            provider="codex",
            lane_id="correctness",
            agent_label="reviewer-lane-correctness",
            status="completed",
            finding_count=2,
        )
        candidate = mock.Mock(
            provider="codex", discovering_agent="reviewer-lane-correctness"
        )
        finding = mock.Mock(
            fingerprint="sha256:" + "f" * 64, candidates=(candidate,)
        )
        manifest = mock.Mock(
            lane_results=(lane,),
            findings=(finding,),
            confirmed_fingerprints=(finding.fingerprint,),
        )
        role_state["lane_metric_observations"] = hloop.reviewer_lane_metric_observations(
            state,
            "R001",
            role_state,
            manifest,
            {
                "lane_metric_observations": [
                    {
                        "provider": "codex",
                        "lane_id": "correctness",
                        "runtime_seconds": 4.5,
                    }
                ]
            },
        )
        escaped = "sha256:" + "e" * 64
        hloop._record_escaped_finding_metrics(state, [escaped])
        hloop._record_escaped_finding_metrics(state, [escaped])
        hloop._refresh_execution_metrics(state)

        metrics = state["execution_metrics"]
        self.assertEqual(metrics["artifact_reconcile_time_seconds"], 2.0)
        self.assertEqual(metrics["provider_capacity_wait_time_seconds"], 0.5)
        self.assertEqual(metrics["review_epoch_peak_live"], {"E001:1": 3})
        self.assertEqual(metrics["peak_live_agent_count"], 3)
        self.assertEqual(metrics["lane_metrics"][0]["runtime_seconds"], 4.5)
        self.assertFalse(metrics["lane_metrics"][0]["timed_out"])
        self.assertEqual(metrics["lane_metrics"][0]["finding_candidate_count"], 2)
        self.assertEqual(metrics["lane_metrics"][0]["verified_finding_count"], 1)
        self.assertEqual(metrics["escaped_finding_count"], 1)


if __name__ == "__main__":
    unittest.main()
