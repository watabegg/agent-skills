"""Unit tests for structured provider E2E timeout evidence."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("run_provider_e2e.py")
SPEC = importlib.util.spec_from_file_location("run_provider_e2e", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import guard
    raise RuntimeError(f"could not load {SCRIPT}")
run_provider_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_provider_e2e)


class NormalizeTimeoutOutputTests(unittest.TestCase):
    def test_accepts_bytes_text_and_none(self):
        self.assertEqual(run_provider_e2e.normalize_timeout_output(b"partial\xff"), "partial\ufffd")
        self.assertEqual(run_provider_e2e.normalize_timeout_output("partial"), "partial")
        self.assertEqual(run_provider_e2e.normalize_timeout_output(None), "")


class StructuredTimeoutResultTests(unittest.TestCase):
    def run_timeout(
        self,
        provider: str,
        stdout: str | bytes | None,
        stderr: str | bytes | None,
    ) -> tuple[int, dict[str, object]]:
        args = argparse.Namespace(
            provider=provider,
            model=None,
            allow_skip=False,
            skip_reason=None,
            timeout_seconds=1,
            json=True,
            output=None,
            keep_workdir=False,
        )

        def make_repo(root: Path) -> Path:
            repo = root / "repo"
            repo.mkdir()
            return repo

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            raise subprocess.TimeoutExpired(command, 1, output=stdout, stderr=stderr)

        output = io.StringIO()
        with (
            mock.patch.object(run_provider_e2e, "parse_args", return_value=args),
            mock.patch.object(run_provider_e2e.shutil, "which", return_value=f"/bin/{provider}"),
            mock.patch.object(
                run_provider_e2e,
                "command_version",
                return_value={"returncode": 0, "value": f"{provider} test"},
            ),
            mock.patch.object(run_provider_e2e, "make_repo", side_effect=make_repo),
            mock.patch.object(run_provider_e2e.subprocess, "run", side_effect=run),
            contextlib.redirect_stdout(output),
        ):
            returncode = run_provider_e2e.main()
        return returncode, json.loads(output.getvalue())

    def test_partial_and_no_output_timeouts_are_structured_for_each_provider(self):
        scenarios = {
            "partial-bytes": (b"partial stdout\xff", b"partial stderr\xfe"),
            "partial-text": ("partial stdout", "partial stderr"),
            "no-output": (None, None),
        }
        for provider in ("codex", "claude"):
            for scenario, (stdout, stderr) in scenarios.items():
                with self.subTest(provider=provider, scenario=scenario):
                    returncode, result = self.run_timeout(provider, stdout, stderr)
                    self.assertEqual(returncode, 1)
                    self.assertEqual(result["provider"], provider)
                    self.assertEqual(result["status"], "failed")
                    self.assertTrue(result["live_execution"])
                    self.assertEqual(result["diagnostic"]["returncode"], 124)
                    self.assertTrue(result["diagnostic"]["timed_out"])
                    self.assertIn("provider exit code 124", result["skip_reason"])

                    if scenario == "no-output":
                        self.assertEqual(
                            result["diagnostic"]["stderr_bytes"],
                            len("provider probe timed out".encode("utf-8")),
                        )


if __name__ == "__main__":
    unittest.main()
