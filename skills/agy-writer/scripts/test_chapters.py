"""Offline public-CLI checks for chapter isolation, assembly and safe repair."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("write_ja.py")
FAKE = """#!/usr/bin/env python3
import json, os, pathlib, sys, time
prompt = sys.argv[-1]
phase = 'repair' if '\"replacements\"' in prompt else ('edit' if '未確認の初稿:' in prompt else 'write')
tag = 'A' if 'A_ONLY' in prompt else ('B' if 'B_ONLY' in prompt else 'single')
event = {'argv':sys.argv[1:], 'phase':phase, 'tag':tag}
pathlib.Path(os.environ['EVENTS'], str(os.getpid())+'.json').write_text(json.dumps(event))
if tag == 'B':
    time.sleep(0.03)
if os.environ.get('MODIFY_OUTPUT'):
    pathlib.Path(os.environ['MODIFY_OUTPUT']).write_text('USER EDIT')
if os.environ.get('FAIL_TAG') == tag and phase == 'edit':
    raise SystemExit(9)
if phase == 'repair':
    body = json.dumps({'replacements':[{'id':0,'text':'fixed A'}],'needs_input':''})
else:
    body = '## '+tag+'\\r\\n\\r\\n'+('finished ' if phase=='edit' else 'initial ')+tag+'\\r\\n'
print(json.dumps({'status':'SUCCESS','response':body,'conversation_id':str(os.getpid())}))
"""


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="writer chapters ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = self.root / "events"
        self.events.mkdir()
        self.packet = self.root / "packet.md"
        fence = chr(96) * 3
        self.packet.write_text("# Packet\nPREFACE_SHARED\n\n## Reader\nREADER_SHARED\n\n"
                               "## Alpha\nA_ONLY\n" + fence + "\n## Example heading\n" + fence +
                               "\n\n## Beta\nB_ONLY\n", encoding="utf-8")
        self.plan = self.root / "plan.json"
        self.definition = {"title": "Document", "shared": ["Reader"], "chapters": [
            {"title": "Second first", "sections": ["Beta"]},
            {"title": "First second", "sections": ["Alpha"], "target_chars": 1000}]}
        self.plan.write_text(json.dumps(self.definition))
        self.output = self.root / "literal $(text) draft.md"
        self.fake = self.root / "fake-writer"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.fake.chmod(0o700)
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.root / "state"), EVENTS=str(self.events))

    def invoke(self, *, plan=True, extra=(), environment=None, operation="chapters"):
        args = [sys.executable, str(SCRIPT), operation, "--writer", str(self.fake)]
        if operation == "chapters":
            args += ["--packet", str(self.packet), "--out", str(self.output)]
            if plan:
                args += ["--plan", str(self.plan)]
        result = subprocess.run(args + list(extra), env=dict(self.env, **(environment or {})),
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def calls(self):
        return [json.loads(p.read_text()) for p in self.events.glob("*.json")]

    def test_fresh_writer_and_editor_per_chapter_preserve_order_and_isolate_material(self):
        code, result = self.invoke()
        self.assertEqual((code, result["status"], result["chapters"], result["review_mode"]),
                         (0, "needs_review", 2, "continue"))
        final = self.output.read_bytes()
        self.assertLess(final.index(b"finished B"), final.index(b"finished A"))
        self.assertNotIn(b"initial", final)
        calls = self.calls()
        self.assertEqual(len(calls), 4)
        self.assertEqual(sorted((x["tag"], x["phase"]) for x in calls),
                         [("A", "edit"), ("A", "write"), ("B", "edit"), ("B", "write")])
        for call in calls:
            argv = call["argv"]
            self.assertIn("--new-project", argv)
            self.assertIn("PREFACE_SHARED", argv[-1])
            self.assertIn("READER_SHARED", argv[-1])
            self.assertNotIn("B_ONLY" if call["tag"] == "A" else "A_ONLY", argv[-1])
            if call["phase"] == "edit":
                self.assertIn("initial " + call["tag"], argv[-1])
        run = Path(result["run_dir"])
        self.assertEqual((run / "packet.md").read_bytes(), self.packet.read_bytes())
        self.assertEqual((run / "draft.md").read_bytes(), final)
        self.assertEqual(run.stat().st_mode & 0o777, 0o700)
        record = json.loads((run / "run.json").read_text())
        self.assertEqual([x["title_hint"] for x in record["chapter_results"]],
                         ["Second first", "First second"])
        self.assertNotIn("chapter_results", result)

    def test_invalid_plan_does_not_invoke_models_or_change_existing_output(self):
        cases = [
            {"title": "Doc", "chapters": [{"title": "A", "sections": ["Alpha"]}]},
            {"title": "Doc", "shared": ["Reader"], "chapters": [
                {"title": "A", "sections": ["Alpha", "Beta"]},
                {"title": "B", "sections": ["Beta"]}]},
            {"title": "Doc", "shared": ["Reader"], "chapters": [{"title": "A", "sections": ["Missing"]}]},
            {"title": "Doc", "chapters": []},
        ]
        self.output.write_text("EXISTING")
        for plan in cases:
            with self.subTest(plan=plan):
                self.plan.write_text(json.dumps(plan))
                code, result = self.invoke()
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertEqual(self.output.read_text(), "EXISTING")
                self.assertEqual(self.calls(), [])

    def test_failed_chapter_does_not_publish_successful_siblings(self):
        self.output.write_text("EXISTING")
        code, result = self.invoke(environment={"FAIL_TAG": "B"})
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(self.output.read_text(), "EXISTING")
        self.assertIn("edit failed", result["error"])
        run = Path(result["run_dir"])
        self.assertTrue((run / "chapter-02/edit/draft.md").exists())
        self.assertFalse((run / "draft.md").exists())

    def test_user_edit_during_generation_is_preserved(self):
        self.output.write_text("EXISTING")
        code, result = self.invoke(environment={"MODIFY_OUTPUT": str(self.output)})
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(self.output.read_text(), "USER EDIT")
        self.assertIn("Output changed", result["error"])
        self.assertEqual(len(list(Path(result["run_dir"]).glob("chapter-*/edit/draft.md"))), 2)

    def test_one_chapter_without_plan_accepts_an_unstructured_short_packet(self):
        self.packet.write_text("A short PR description with fixed facts.")
        code, result = self.invoke(plan=False)
        self.assertEqual((code, result["status"], result["chapters"]), (0, "needs_review", 1))
        self.assertEqual(len(self.calls()), 2)
        self.assertIn(b"finished single", self.output.read_bytes())

    def test_parent_run_supports_targeted_repair_with_full_source_and_unchanged_bytes(self):
        _, base = self.invoke()
        before = self.output.read_bytes()
        findings = self.root / "findings.json"
        findings.write_text(json.dumps([{"before": "finished A", "evidence": "Original condition",
                                        "issue": "Condition changed"}]))
        code, result = self.invoke(operation="repair", extra=["--run", base["run_dir"], "--findings", str(findings)])
        self.assertEqual((code, result["status"], result["review_mode"]), (0, "needs_review", "continue"))
        self.assertEqual(self.output.read_bytes(), before.replace(b"finished A", b"fixed A"))
        prompt = next(x for x in self.calls() if x["phase"] == "repair")["argv"][-1]
        self.assertIn(self.packet.read_text(), prompt)
        self.assertTrue(Path(result["changes"]).is_file())

    def test_input_paths_are_not_output_destinations(self):
        before = self.plan.read_bytes()
        self.output = self.plan
        code, result = self.invoke()
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_ambiguous_packet_headings_fail_before_model_calls(self):
        self.packet.write_text("## Alpha\nOne\n## Alpha\nTwo\n")
        code, result = self.invoke()
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertIn("unique", result["error"])
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
