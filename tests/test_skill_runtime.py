import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "skills/ealps-moodle-operator/scripts/summarize_ealps_evidence.py"
spec = importlib.util.spec_from_file_location("rtk_adapter", ROOT / "scripts/rtk_codex_hook.py")
rtk_adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtk_adapter)


class EvidenceTests(unittest.TestCase):
    def call(self, path, *flags):
        p = subprocess.run(["python3", str(EVIDENCE), str(path), *flags], cwd="/tmp", text=True, capture_output=True)
        return p.returncode, json.loads(p.stdout)

    def test_missing_empty_and_invalid_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code, data = self.call(root / "missing", "--verify")
            self.assertEqual((code, data["status"], data["reason"]), (2, "needs_input", "missing_input"))
            self.assertEqual(self.call(root, "--verify")[1]["reason"], "empty_evidence")
            (root / "bad.json").write_text("[1, 2]")
            code, data = self.call(root, "--verify")
            self.assertEqual((code, data["status"]), (1, "failed"))

    def test_completed_summary_is_not_all_submitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, text in [("submitted", "提出ステータス 評定のために提出済み"), ("draft", "提出ステータス 下書き（未提出）")]:
                (root / f"{name}.json").write_text(json.dumps({"finalUrl": "https://example.invalid/mod/assign/view.php?id=1", "text": text}))
            code, result = self.call(root, "--verify")
            self.assertEqual((code, result["status"]), (0, "completed"))
            self.assertEqual(sorted(row["ok"] for row in result["rows"]), [False, True])
            states = {row["file"]: row["state"] for row in result["rows"]}
            self.assertEqual(states, {"submitted.json": "submitted", "draft.json": "draft"})
            self.assertIsInstance(self.call(root, "--json")[1], list)


class RtkAdapterTests(unittest.TestCase):
    def test_only_command_rewrite_is_returned(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status", "workdir": "/tmp"}}
        backend = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": "rtk git status"}, "additionalContext": "unneeded text"}}
        result = rtk_adapter.adapt(payload, backend)["hookSpecificOutput"]
        self.assertEqual(result["permissionDecision"], "allow")
        self.assertEqual(result["updatedInput"], {"command": "rtk git status"})
        self.assertNotIn("additionalContext", result)
        backend["hookSpecificOutput"]["permissionDecision"] = "deny"
        self.assertIsNone(rtk_adapter.adapt(payload, backend))
        payload["tool_name"] = "apply_patch"
        self.assertIsNone(rtk_adapter.adapt(payload, backend))

    def test_offline_real_rtk_does_not_execute_input(self):
        for command in ["git status --short", "rtk git status --short", "python3 -c 'print(1)'", "git diff --stat", "cat <<'EOF'\n$HOME\nEOF", "git status --short | head -3", "GIT_OPTIONAL_LOCKS=0 git status --short", "git status --short && git diff --stat"]:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
            p = subprocess.run(["python3", str(ROOT / "scripts/rtk_codex_hook.py")], input=json.dumps(payload), text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            if p.stdout:
                output = json.loads(p.stdout)["hookSpecificOutput"]
                self.assertEqual(output["permissionDecision"], "allow")
                rewritten = output["updatedInput"]["command"]
                self.assertIn("rtk", rewritten)
                self.assertNotEqual(rewritten, command)
                syntax = subprocess.run(["bash", "-n"], input=rewritten, text=True, capture_output=True)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()
