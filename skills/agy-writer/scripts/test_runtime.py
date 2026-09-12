"""Offline deployment, launcher and tool-policy tests."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / f"scripts/{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module("install_runtime")
policy = module("document_writer_policy")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="writer runtime ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.bin = self.root / "bin"
        self.config = self.root / "config"

    def test_preview_then_idempotent_migration_preserves_unrelated_files(self):
        self.config.mkdir()
        global_path = self.config / "hooks.json"
        legacy = self.config / "hooks/document_writer_policy.py"
        legacy.parent.mkdir()
        legacy.write_text("legacy policy")
        other = {"PostToolUse": []}
        global_path.write_text(json.dumps({"other": other, "document-writer-policy": installer.hook_definition(self.config / "hooks/document_writer_policy.py")}))
        original = global_path.read_bytes()
        preview = installer.install(SKILL, self.workspace, self.bin, self.config)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(global_path.read_bytes(), original)
        self.assertTrue(preview["migrate_global_writer_hook"])
        self.workspace.mkdir()
        (self.workspace / "comparison.md").write_text("user document")
        installer.install(SKILL, self.workspace, self.bin, self.config, True)
        self.assertEqual(json.loads(global_path.read_text()), {"other": other})
        self.assertEqual((self.workspace / "comparison.md").read_text(), "user document")
        self.assertEqual((self.workspace / "GEMINI.md").read_bytes(), (SKILL / "assets/GEMINI.md").read_bytes())
        self.assertEqual((self.workspace / "style-profile.md").resolve(), self.workspace / "GEMINI.md")
        self.assertEqual(legacy.resolve(), self.workspace / "bin/document_writer_policy.py")
        self.assertEqual(installer.install(SKILL, self.workspace, self.bin, self.config, True)["changed"], [])

    def test_unexpected_global_hook_is_not_replaced(self):
        self.config.mkdir()
        path = self.config / "hooks.json"
        path.write_text('{"document-writer-policy":{"enabled":false}}')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            installer.install(SKILL, self.workspace, self.bin, self.config, True)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.workspace.exists())

    def test_launcher_uses_workspace_and_forwards_literal_args_and_exit(self):
        self.workspace.mkdir()
        self.bin.mkdir()
        fake = self.bin / "agy"
        fake.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"cwd":os.getcwd(),"argv":sys.argv[1:]}))\nsys.exit(23)\n')
        fake.chmod(0o700)
        environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], AGY_WRITER_DIR=str(self.workspace))
        extra = ["--model", "requested-model", "--effort", "low", "-p", 'literal `x` $(y) "z"']
        result = subprocess.run([str(SKILL / "scripts/agy-writer"), *extra], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 23)
        output = json.loads(result.stdout)
        self.assertEqual(output["cwd"], str(self.workspace))
        self.assertEqual(output["argv"], ["--mode", "accept-edits", "--model", "gemini-3.8-flash-high", "--effort", "high", *extra])

    def test_sync_command_installs_codex_claude_and_runtime_together(self):
        codex, claude = self.root / "codex", self.root / "claude"
        command = [sys.executable, str(REPO / "scripts/sync_installed_skills.py"), "--only", "agy-writer", "--writer-runtime",
                   "--codex-home", str(codex), "--claude-home", str(claude), "--writer-workspace", str(self.workspace),
                   "--writer-bin-dir", str(self.bin), "--agy-config", str(self.config)]
        preview = json.loads(subprocess.check_output(command, text=True))
        self.assertEqual(preview["writer_runtime"]["links"][str(self.bin / "agy-writer")], str(codex / "skills/agy-writer/scripts/agy-writer"))
        self.assertFalse(codex.exists())
        subprocess.run(command + ["--apply"], capture_output=True, text=True, check=True)
        self.assertEqual((claude / "skills/agy-writer").resolve(), codex / "skills/agy-writer")
        self.assertEqual((self.bin / "agy-writer").resolve(), codex / "skills/agy-writer/scripts/agy-writer")
        again = json.loads(subprocess.check_output(command + ["--apply"], text=True))
        self.assertEqual(again["writer_runtime"]["changed"], [])

    def test_document_policy_rejects_code_secrets_and_arbitrary_commands(self):
        def decision(name, args, cwd=None):
            return policy.evaluate({"toolCall": {"name": name, "args": args}, "workspacePaths": [str(cwd or policy.HOME_ROOT / "agy-writer")]})["decision"]
        self.assertEqual(decision("write_to_file", {"TargetFile": str(self.root / "draft.md")}), "allow")
        for path in [self.root / "app.py", policy.HOME_ROOT / ".env", self.root / "GEMINI.md"]:
            self.assertEqual(decision("write_to_file", {"TargetFile": str(path)}), "deny")
        self.assertEqual(decision("view_file", {"AbsolutePath": str(policy.HOME_ROOT / ".ssh/config")}), "deny")
        self.assertEqual(decision("run_command", {"CommandLine": "git status"}), "deny")
        self.assertEqual(decision("run_command", {"CommandLine": f"./bin/agy-doc-count {self.root}/draft.md"}), "deny")
        # Use a simple document path; shell metacharacters and space-splitting are intentionally rejected.
        counter = str(Path(policy.__file__).with_name("agy-doc-count"))
        self.assertEqual(decision("run_command", {"CommandLine": f"{counter} /tmp/draft.md"}), "allow")
        self.assertEqual(decision("run_command", {"CommandLine": "./bin/agy-doc-count /tmp/draft.md", "Cwd": "/tmp/unmanaged"}), "deny")


if __name__ == "__main__":
    unittest.main()
