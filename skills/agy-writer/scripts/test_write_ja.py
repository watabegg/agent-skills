"""Offline transport and document-integrity checks; no model or network calls."""

import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("write_ja.py")
SPEC = importlib.util.spec_from_file_location("write_ja", SCRIPT)
writer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(writer)


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packet = self.root / "packet.md"
        self.packet.write_text("A nullable reference is planned; its storage mechanism is undecided.\n", encoding="utf-8")
        self.profile = self.root / "style.md"
        self.profile.write_text("普通の言葉で書く。", encoding="utf-8")
        self.output = self.root / "draft.md"
        self.fake = self.root / "fake-writer"
        self.fake.write_text("#!/usr/bin/env python3\n"
                             "import json, os, pathlib, sys\n"
                             "pathlib.Path(os.environ['TEST_ARGS']).write_text(json.dumps(sys.argv[1:]))\n"
                             "print(os.environ['TEST_RESPONSE'])\n"
                             "raise SystemExit(int(os.environ.get('TEST_EXIT', '0')))\n", encoding="utf-8")
        self.fake.chmod(0o700)
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.root / "state"), TEST_ARGS=str(self.root / "args.json"))

    def run_cli(self, operation, arguments, body, *, provider_status="SUCCESS", exit_code=0):
        response = {"status": provider_status, "response": body, "conversation_id": "offline-fixture"}
        env = dict(self.env, TEST_RESPONSE=json.dumps(response), TEST_EXIT=str(exit_code))
        result = subprocess.run([sys.executable, str(SCRIPT), operation, "--writer", str(self.fake),
                                 "--profile", str(self.profile), *arguments], env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def draft(self, text="開始\r\n外部キー列を追加する。\r\n末尾\r\n"):
        return self.run_cli("draft", ["--packet", str(self.packet), "--out", str(self.output)], text)

    def finding_file(self):
        path = self.root / "findings.json"
        path.write_text(json.dumps([{"before": "外部キー列を追加する。", "evidence": "Storage mechanism is undecided.",
                                     "issue": "A foreign-key constraint has been added without a decision."}]), encoding="utf-8")
        return path

    def test_draft_keeps_prompt_out_of_stdout_and_uses_literal_arguments(self):
        self.packet.write_text("Literal: `command` $(another) \"quotes\"\n", encoding="utf-8")
        code, result = self.draft("# 日本語の原稿\n")
        self.assertEqual((code, result["status"]), (0, "needs_review"))
        self.assertEqual(self.output.read_text(), "# 日本語の原稿\n")
        self.assertNotIn("response", result)
        argv = json.loads((self.root / "args.json").read_text())
        self.assertIn("--new-project", argv)
        self.assertEqual(argv[argv.index("--model") + 1], writer.MODEL)
        self.assertIn(self.packet.read_text(), argv[-1])
        self.assertIn(self.profile.read_text(), argv[-1])
        self.assertEqual(argv[-2], "-p")
        self.assertEqual(Path(result["run_dir"]).stat().st_mode & 0o777, 0o700)

    def test_repair_only_changes_requested_bytes(self):
        _, base = self.draft()
        before = self.output.read_bytes()
        answer = {"replacements": [{"id": 0, "text": "参照を追加する。"}], "needs_input": ""}
        code, result = self.run_cli("repair", ["--run", base["run_dir"], "--findings", str(self.finding_file())], json.dumps(answer))
        self.assertEqual((code, result["status"]), (0, "needs_review"))
        self.assertEqual(self.output.read_bytes(), before.replace("外部キー列を追加する。".encode(), "参照を追加する。".encode()))
        self.assertTrue(Path(result["changes"]).is_file())

    def test_failures_and_missing_material_preserve_existing_output(self):
        _, base = self.draft()
        before = self.output.read_bytes()
        findings = self.finding_file()
        cases = [
            ("Not JSON", "SUCCESS", 0, 1, "failed"),
            (json.dumps({"replacements": [], "needs_input": "保存方法を確認してください。"}), "SUCCESS", 0, 2, "needs_input"),
            ("# false success", "ERROR", 0, 1, "failed"),
            ("# failed process", "SUCCESS", 7, 1, "failed"),
            ("", "SUCCESS", 0, 1, "failed"),
        ]
        for body, status, exit_code, expected_code, expected_status in cases:
            with self.subTest(status=status, exit_code=exit_code, body=body):
                code, result = self.run_cli("repair", ["--run", base["run_dir"], "--findings", str(findings)],
                                            body, provider_status=status, exit_code=exit_code)
                self.assertEqual((code, result["status"]), (expected_code, expected_status))
                self.assertEqual(self.output.read_bytes(), before)
        # TimeoutExpired includes the entire argv (and prompt) in its default message.
        stream = io.StringIO()
        arguments = [str(SCRIPT), "draft", "--packet", str(self.packet), "--out", str(self.output),
                     "--profile", str(self.profile)]
        with patch.object(sys, "argv", arguments), patch.dict(os.environ, self.env), contextlib.redirect_stdout(stream):
            with patch.object(writer.subprocess, "run", side_effect=subprocess.TimeoutExpired(["agy-writer", "-p", "PRIVATE_MATERIAL"], 30)):
                self.assertEqual(writer.main(), 1)
        self.assertEqual(json.loads(stream.getvalue())["status"], "failed")
        self.assertNotIn("PRIVATE_MATERIAL", stream.getvalue())
        self.assertEqual(self.output.read_bytes(), before)

    def test_changed_output_is_not_overwritten_or_sent_to_writer(self):
        _, base = self.draft()
        self.output.write_text("ユーザーが編集した文書\n", encoding="utf-8")
        (self.root / "args.json").unlink()
        code, result = self.run_cli("repair", ["--run", base["run_dir"], "--findings", str(self.finding_file())], "unused")
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(self.output.read_text(), "ユーザーが編集した文書\n")
        self.assertFalse((self.root / "args.json").exists())

    def test_repair_rejects_ambiguous_or_overlapping_targets(self):
        def finding(before):
            return {"before": before, "evidence": "source", "issue": "condition changed"}
        for draft, findings in [("same same", [finding("same")]), ("abcdef", [finding("abc"), finding("bcde")])]:
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                writer.locate_findings(draft, findings)

    def test_multiple_replacements_preserve_surroundings(self):
        draft = "先頭\nAAA\n中間\nBBB\n末尾\n"
        findings = [{"before": text, "evidence": "material", "issue": "wrong condition"} for text in ("BBB", "AAA")]
        body = draft
        replacements = {0: "短い説明", 1: "長さの違う説明"}
        for start, end, index in reversed(writer.locate_findings(draft, findings)):
            body = body[:start] + replacements[index] + body[end:]
        self.assertEqual(body, "先頭\n長さの違う説明\n中間\n短い説明\n末尾\n")

    def test_transport_repeats_must_agree(self):
        answer = {"replacements": [{"id": 0, "text": "natural Japanese"}], "needs_input": ""}
        repeated = json.dumps(dict(answer, toolAction="Finished", toolSummary="Done")) + "\n" + json.dumps(answer)
        self.assertEqual(writer.decode_repair(repeated, 1)["replacements"], {0: "natural Japanese"})
        other = {"replacements": [{"id": 0, "text": "different meaning"}], "needs_input": ""}
        with self.assertRaises(ValueError):
            writer.decode_repair(json.dumps(answer) + "\n" + json.dumps(other), 1)
        for replacements in [[{"id": 1, "text": "wrong id"}], [{"id": True, "text": "boolean"}]]:
            with self.assertRaises(ValueError):
                writer.decode_repair(json.dumps({"replacements": replacements, "needs_input": ""}), 1)


if __name__ == "__main__":
    unittest.main()
