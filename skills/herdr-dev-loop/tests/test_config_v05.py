"""Unit tests for hloop_lib.config (herdr-dev-loop 0.5.0 config primitives)."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hloop_lib import config  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]

# Any absolute path under a specific user's home directory (e.g.
# /home/watabegg/...), regardless of username -- required-command blocks
# must only ever discover installs via $HOME/$CODEX_HOME/$CLAUDE_SKILLS_HOME
# or a project-relative path, so they work for every author's machine.
_AUTHOR_HOME_PATH = re.compile(r"/home/[^/\s\"'()]+/")

_PORTABLE_DOC_PATHS = (
    "README.md",
    "docs/RELEASE-0.5.0.md",
    "docs/RELEASE-0.5.1.md",
    "docs/RELEASE-0.5.2.md",
    "docs/2026-07-14-v0.5.0-plan.md",
    "docs/2026-07-16-v0.5.1-release-hardening-plan.md",
    "references/migration-install.md",
)


class RequiredCommandPortabilityTests(unittest.TestCase):
    def test_release_readme_migration_and_plan_docs_avoid_author_home_paths(self):
        for relative_path in _PORTABLE_DOC_PATHS:
            with self.subTest(doc=relative_path):
                text = (SKILL_ROOT / relative_path).read_text(encoding="utf-8")
                matches = _AUTHOR_HOME_PATH.findall(text)
                self.assertEqual(
                    matches,
                    [],
                    f"{relative_path} hard-codes an author-machine home path: {matches}",
                )

    def test_v051_repository_validation_contract_is_fail_closed_and_clean(self):
        for relative_path in (
            "docs/RELEASE-0.5.1.md",
            "docs/2026-07-16-v0.5.1-release-hardening-plan.md",
        ):
            with self.subTest(doc=relative_path):
                text = (SKILL_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("set -euo pipefail", text)
                self.assertIn('trap cleanup EXIT', text)
                self.assertIn('PYTHONPYCACHEPREFIX="$PYCACHE_DIR"', text)
                self.assertIn("if rg -n 'ResourceWarning", text)
                self.assertIn("warning_scan_status=$?", text)
                self.assertIn('*) exit "$warning_scan_status" ;;', text)
                self.assertIn("require_clean_worktree()", text)
                self.assertIn(
                    ': "${EXPECTED_CANDIDATE_SHA:?set the externally reviewed full candidate SHA}"',
                    text,
                )
                self.assertIn(
                    'ACTUAL_CANDIDATE_SHA="$(git rev-parse HEAD)"', text
                )
                self.assertIn(
                    'git merge-base "$EXPECTED_BASE_SHA" "$ACTUAL_CANDIDATE_SHA"',
                    text,
                )
                self.assertIn(
                    'EVIDENCE_ROOT="$EVIDENCE_PARENT/$EXPECTED_CANDIDATE_SHA"',
                    text,
                )
                self.assertIn('test ! -L "$EVIDENCE_ROOT"', text)
                self.assertIn('test ! -e "$EVIDENCE_ROOT"', text)
                self.assertIn('mkdir -m 700 "$EVIDENCE_ROOT"', text)
                self.assertIn("set -o noclobber", text)
                for evidence_name in (
                    "compile.status",
                    "version.json",
                    "selftest.log",
                    "quick-validate.log",
                    "synthetic.json",
                    "synthetic.stdout.log",
                    "synthetic.stderr.log",
                    "diff-check.log",
                    "repository-gates.status",
                ):
                    self.assertIn(evidence_name, text)
                self.assertIn(
                    'git diff --check "$EXPECTED_BASE_SHA...$EXPECTED_CANDIDATE_SHA"',
                    text,
                )
                self.assertGreaterEqual(text.count("require_candidate_identity"), 3)
                self.assertIn(
                    'worktree_status="$(git status --porcelain --untracked-files=all)"',
                    text,
                )
                self.assertEqual(
                    text.count("git status --porcelain --untracked-files=all"), 1
                )
                self.assertGreaterEqual(text.count("require_clean_worktree"), 3)
                self.assertIn(
                    "$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1/<candidate-sha>/",
                    text,
                )

    def test_v051_release_contract_atomically_finalizes_complete_manifest(self):
        release_text = (SKILL_ROOT / "docs/RELEASE-0.5.1.md").read_text(
            encoding="utf-8"
        )
        marker = (
            'python3 - "$EVIDENCE_ROOT" "$EXPECTED_BASE_SHA" '
            '"$EXPECTED_CANDIDATE_SHA" <<\'PY\'\n'
        )
        self.assertIn(marker, release_text)
        self.assertIn(
            'test "$(git rev-parse master)" = "$EXPECTED_CANDIDATE_SHA"',
            release_text,
        )
        self.assertIn(
            'test "$(git rev-parse origin/master)" = "$EXPECTED_CANDIDATE_SHA"',
            release_text,
        )
        self.assertIn(
            'test "$(git rev-parse --verify "${EXPECTED_BASE_SHA}^{commit}")" = "$EXPECTED_BASE_SHA"',
            release_text,
        )
        self.assertIn(
            'test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"',
            release_text,
        )
        self.assertGreaterEqual(
            release_text.count(
                'test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"'
            ),
            5,
        )
        finalizer = release_text.split(marker, 1)[1].split("\nPY\n", 1)[0]
        for expected_fragment in (
            '"codex-marker-probe.json"',
            '"provider-gates.status"',
            '"codex-install-diff.log"',
            '"claude-install-diff.log"',
            '"installed-source.json"',
            '"install-gates.status"',
            '"codex-discovery.json"',
            '"gap-audit.txt"',
            '"review-correctness.txt"',
            '"review-security.txt"',
            '"review-cli-docs.txt"',
            '"review-tests.txt"',
            '"claude_live_provider_e2e": "not_run"',
            '"fresh_claude_discovery": "not_run"',
            "os.O_EXCL",
            "os.replace(temporary_path, manifest_path)",
        ):
            self.assertIn(expected_fragment, finalizer)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "evidence"
            root.mkdir(mode=0o700)
            nonempty_text = {
                "compile.status": "passed\n",
                "unit.log": "test_example ... ok\n\nRan 1 tests in 0.001s\n\nOK\n",
                "selftest.log": "selftest ok\n",
                "quick-validate.log": "Skill is valid!\n",
                "synthetic.stdout.log": "synthetic E2E: passed\n",
                "repository-gates.status": "passed\n",
                "codex-marker-probe.stdout.log": "provider marker probe: passed\n",
                "provider-gates.status": "passed\n",
                "codex-installed-selftest.log": "selftest ok\n",
                "claude-installed-selftest.log": "selftest ok\n",
                "install-gates.status": "passed\n",
                "gap-audit.txt": (
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'2' * 40}\n"
                    "GAP AUDIT: PASS\n"
                ),
                "review-correctness.txt": (
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'2' * 40}\n"
                    "REVIEW RESULT: CLEAN\n"
                ),
                "review-security.txt": (
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'2' * 40}\n"
                    "REVIEW RESULT: CLEAN\n"
                ),
                "review-cli-docs.txt": (
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'2' * 40}\n"
                    "REVIEW RESULT: CLEAN\n"
                ),
                "review-tests.txt": (
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'2' * 40}\n"
                    "REVIEW RESULT: CLEAN\n"
                ),
            }
            empty_files = {
                "synthetic.stderr.log",
                "diff-check.log",
                "diff-check.stderr.log",
                "compile.stdout.log",
                "compile.stderr.log",
                "version.stderr.log",
                "selftest.stderr.log",
                "quick-validate.stderr.log",
                "codex-marker-probe.stderr.log",
                "codex-install-diff.log",
                "codex-install-diff.stderr.log",
                "claude-install-diff.log",
                "claude-install-diff.stderr.log",
                "codex-installed-version.stderr.log",
                "claude-installed-version.stderr.log",
                "codex-installed-selftest.stderr.log",
                "claude-installed-selftest.stderr.log",
                "installed-source.stderr.log",
            }
            json_evidence = {
                "version.json": {"runtime_skill_version": "0.5.1"},
                "synthetic.json": {
                    "status": "passed",
                    "scenario_count": 9,
                },
                "codex-marker-probe.json": {
                    "status": "passed",
                    "runner": "herdr-dev-loop-provider-marker-probe",
                },
                "codex-installed-version.json": {
                    "runtime_skill_version": "0.5.1"
                },
                "claude-installed-version.json": {
                    "runtime_skill_version": "0.5.1"
                },
                "install-backups.json": {
                    "status": "passed",
                    "codex_backup": "/tmp/codex-backup",
                    "claude_backup": "/tmp/claude-backup",
                },
                "installed-source.json": {
                    "status": "passed",
                    "source_sha": "2" * 40,
                },
                "codex-discovery.json": {
                    "status": "passed",
                    "source_sha": "2" * 40,
                },
            }
            for name, value in nonempty_text.items():
                (root / name).write_text(value, encoding="utf-8")
            for name in empty_files:
                (root / name).write_text("", encoding="utf-8")
            for name, value in json_evidence.items():
                (root / name).write_text(
                    json.dumps(value) + "\n", encoding="utf-8"
                )

            base_sha = "1" * 40
            candidate_sha = "2" * 40
            result = subprocess.run(
                [sys.executable, "-c", finalizer, str(root), base_sha, candidate_sha],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stderr.decode("utf-8", errors="replace"),
            )
            manifest = json.loads(
                (root / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["candidate_sha"], candidate_sha)
            self.assertEqual(manifest["installed_source_sha"], candidate_sha)
            self.assertEqual(manifest["gates"]["repository_unit"]["count"], 1)
            self.assertEqual(manifest["gates"]["manual_review"]["findings"], 0)

            second = subprocess.run(
                [sys.executable, "-c", finalizer, str(root), base_sha, candidate_sha],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(second.returncode, 0)

            scenario_mutations = {
                "missing-required": lambda path: (path / "quick-validate.log").unlink(),
                "invalid-json": lambda path: (path / "synthetic.json").write_text(
                    "{\n", encoding="utf-8"
                ),
                "resource-warning": lambda path: (path / "unit.log").write_text(
                    (path / "unit.log").read_text(encoding="utf-8")
                    + "ResourceWarning: leaked connection\n",
                    encoding="utf-8",
                ),
                "review-not-clean": lambda path: (
                    path / "review-security.txt"
                ).write_text("REVIEW RESULT: FINDINGS\n", encoding="utf-8"),
                "review-substring-false-positive": lambda path: (
                    path / "review-security.txt"
                ).write_text(
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'2' * 40}\n"
                    "Expected REVIEW RESULT: CLEAN, actual REVIEW RESULT: FINDINGS\n",
                    encoding="utf-8",
                ),
                "review-wrong-candidate": lambda path: (
                    path / "review-security.txt"
                ).write_text(
                    f"base_sha: {'1' * 40}\n"
                    f"candidate_sha: {'3' * 40}\n"
                    "REVIEW RESULT: CLEAN\n",
                    encoding="utf-8",
                ),
                "install-diff": lambda path: (
                    path / "codex-install-diff.log"
                ).write_text("Files differ\n", encoding="utf-8"),
                "wrong-installed-source": lambda path: (
                    path / "installed-source.json"
                ).write_text(
                    json.dumps({"status": "passed", "source_sha": "3" * 40})
                    + "\n",
                    encoding="utf-8",
                ),
                "failed-status": lambda path: (
                    path / "install-gates.status"
                ).write_text("failed\n", encoding="utf-8"),
            }
            source_files = [
                path for path in root.iterdir() if path.name != "manifest.json"
            ]
            for label, mutate in scenario_mutations.items():
                with self.subTest(invalid_manifest_scenario=label):
                    scenario_root = Path(directory) / label
                    scenario_root.mkdir(mode=0o700)
                    for source in source_files:
                        shutil.copy2(source, scenario_root / source.name)
                    mutate(scenario_root)
                    invalid = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            finalizer,
                            str(scenario_root),
                            base_sha,
                            candidate_sha,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    self.assertNotEqual(invalid.returncode, 0)
                    self.assertFalse((scenario_root / "manifest.json").exists())

    def test_v051_warning_scan_distinguishes_match_no_match_and_scan_failure(self):
        script = r"""
set -euo pipefail
rg() { return "${RG_STATUS:?}"; }
if rg warning-pattern warning.log; then
  warning_scan_status=0
else
  warning_scan_status=$?
fi
case "$warning_scan_status" in
  0) exit 1 ;;
  1) ;;
  *) exit "$warning_scan_status" ;;
esac
"""
        for rg_status, expected_status in ((0, 1), (1, 0), (2, 2), (127, 127)):
            with self.subTest(rg_status=rg_status):
                result = subprocess.run(
                    ["bash", "-c", script],
                    env={**os.environ, "RG_STATUS": str(rg_status)},
                    check=False,
                )
                self.assertEqual(result.returncode, expected_status)

    def test_v051_candidate_identity_gate_rejects_wrong_or_implicit_sha(self):
        script = r"""
set -euo pipefail
: "${EXPECTED_BASE_SHA:?set the externally reviewed full baseline SHA}"
: "${EXPECTED_CANDIDATE_SHA:?set the externally reviewed full candidate SHA}"
case "$EXPECTED_BASE_SHA" in
  *[!0-9a-f]*|'') exit 2 ;;
esac
case "$EXPECTED_CANDIDATE_SHA" in
  *[!0-9a-f]*|'') exit 2 ;;
esac
test "${#EXPECTED_BASE_SHA}" -eq 40
test "${#EXPECTED_CANDIDATE_SHA}" -eq 40
require_candidate_identity() {
  ACTUAL_CANDIDATE_SHA="$(git rev-parse HEAD)"
  test "$(git rev-parse --verify "${EXPECTED_BASE_SHA}^{commit}")" = "$EXPECTED_BASE_SHA"
  test "$(git rev-parse --verify "${EXPECTED_CANDIDATE_SHA}^{commit}")" = "$EXPECTED_CANDIDATE_SHA"
  test "$ACTUAL_CANDIDATE_SHA" = "$EXPECTED_CANDIDATE_SHA"
  test "$(git merge-base "$EXPECTED_BASE_SHA" "$ACTUAL_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
}
require_candidate_identity
EVIDENCE_PARENT="$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1"
EVIDENCE_ROOT="$EVIDENCE_PARENT/$EXPECTED_CANDIDATE_SHA"
test "$(basename "$EVIDENCE_ROOT")" = "$ACTUAL_CANDIDATE_SHA"
mkdir -p "$EVIDENCE_PARENT"
test ! -L "$EVIDENCE_ROOT"
test ! -e "$EVIDENCE_ROOT"
mkdir -m 700 "$EVIDENCE_ROOT"
if test -n "${MOVE_HEAD_TO:-}"; then
  git checkout --detach "$MOVE_HEAD_TO" >/dev/null 2>&1
fi
git diff --check "$EXPECTED_BASE_SHA...$EXPECTED_CANDIDATE_SHA"
require_candidate_identity
"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "--initial-branch=master"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "base"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            tracked.write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "candidate"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            candidate_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()

            scenarios = (
                ({"EXPECTED_BASE_SHA": base_sha, "EXPECTED_CANDIDATE_SHA": candidate_sha}, True),
                ({"EXPECTED_BASE_SHA": base_sha, "EXPECTED_CANDIDATE_SHA": base_sha}, False),
                ({"EXPECTED_BASE_SHA": "0" * 40, "EXPECTED_CANDIDATE_SHA": candidate_sha}, False),
                ({"EXPECTED_BASE_SHA": base_sha, "EXPECTED_CANDIDATE_SHA": candidate_sha[:12]}, False),
                ({"EXPECTED_BASE_SHA": base_sha, "EXPECTED_CANDIDATE_SHA": candidate_sha.upper()}, False),
                ({"EXPECTED_BASE_SHA": base_sha, "EXPECTED_CANDIDATE_SHA": candidate_sha[:-1] + ":"}, False),
                ({"EXPECTED_BASE_SHA": base_sha}, False),
                (
                    {
                        "EXPECTED_BASE_SHA": base_sha,
                        "EXPECTED_CANDIDATE_SHA": candidate_sha,
                        "MOVE_HEAD_TO": base_sha,
                    },
                    False,
                ),
            )
            for identity_environment, should_pass in scenarios:
                with self.subTest(identity_environment=identity_environment):
                    subprocess.run(
                        ["git", "checkout", "--detach", candidate_sha],
                        cwd=repo,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    shutil.rmtree(
                        repo / ".git" / "herdr-dev-loop-release-evidence",
                        ignore_errors=True,
                    )
                    result = subprocess.run(
                        ["bash", "-c", script],
                        cwd=repo,
                        env={**os.environ, **identity_environment},
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    if should_pass:
                        self.assertEqual(
                            result.returncode,
                            0,
                            result.stderr.decode("utf-8", errors="replace"),
                        )
                    else:
                        self.assertNotEqual(result.returncode, 0)

    def test_v051_evidence_creation_rejects_existing_paths_and_symlinks(self):
        script = r"""
set -euo pipefail
EVIDENCE_PARENT="${TEST_ROOT:?}/evidence/0.5.1"
EVIDENCE_ROOT="$EVIDENCE_PARENT/0123456789012345678901234567890123456789"
mkdir -p "$EVIDENCE_PARENT"
test ! -L "$EVIDENCE_ROOT"
test ! -e "$EVIDENCE_ROOT"
mkdir -m 700 "$EVIDENCE_ROOT"
WARNING_LOG="$EVIDENCE_ROOT/unit.log"
umask 077
set -o noclobber
if test -n "${INJECT_LOG_SYMLINK:-}"; then
  ln -s "$INJECT_LOG_SYMLINK" "$WARNING_LOG"
fi
: >"$WARNING_LOG"
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_root = (
                root
                / "evidence"
                / "0.5.1"
                / "0123456789012345678901234567890123456789"
            )
            target = root / "target"
            target.mkdir()

            def run(**environment):
                return subprocess.run(
                    ["bash", "-c", script],
                    env={**os.environ, "TEST_ROOT": str(root), **environment},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertEqual(run().returncode, 0)
            shutil.rmtree(evidence_root)

            evidence_root.mkdir(parents=True)
            self.assertNotEqual(run().returncode, 0)
            shutil.rmtree(evidence_root)

            evidence_root.parent.mkdir(parents=True, exist_ok=True)
            evidence_root.symlink_to(target, target_is_directory=True)
            self.assertNotEqual(run().returncode, 0)
            evidence_root.unlink()

            protected_target = root / "protected.log"
            protected_target.write_text("unchanged\n", encoding="utf-8")
            result = run(INJECT_LOG_SYMLINK=str(protected_target))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                protected_target.read_text(encoding="utf-8"), "unchanged\n"
            )

    def test_v051_clean_worktree_check_propagates_git_failure(self):
        script = r"""
set -euo pipefail
git() {
  printf '%s' "${GIT_OUTPUT:-}"
  return "${GIT_STATUS:?}"
}
require_clean_worktree() {
  local worktree_status
  worktree_status="$(git status --porcelain --untracked-files=all)"
  test -z "$worktree_status"
}
require_clean_worktree
"""
        scenarios = (
            (0, "", 0),
            (0, " M tracked-file", 1),
            (2, "", 2),
        )
        for git_status, git_output, expected_status in scenarios:
            with self.subTest(git_status=git_status, git_output=git_output):
                result = subprocess.run(
                    ["bash", "-c", script],
                    env={
                        **os.environ,
                        "GIT_STATUS": str(git_status),
                        "GIT_OUTPUT": git_output,
                    },
                    check=False,
                )
                self.assertEqual(result.returncode, expected_status)


class CurrentV053ReleaseArtifactTests(unittest.TestCase):
    def test_current_version_docs_and_public_final_review_schemas(self):
        self.assertEqual(
            (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            "0.5.3",
        )
        for relative_path in (
            "README.md",
            "SKILL.md",
            "docs/2026-07-17-v0.5.3-release-notes.md",
            "references/artifact-contract.md",
            "references/cli-notes.md",
            "references/configuration.md",
            "references/manager-loop.md",
            "references/migration-install.md",
            "references/report-protocol.md",
            "references/review-swarm.md",
            "references/reviewer-contract.md",
            "references/state-machine.md",
            "references/validation-policy.md",
        ):
            with self.subTest(relative_path=relative_path):
                text = (SKILL_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("0.5.3", text)

        historical_release = (
            SKILL_ROOT / "docs" / "RELEASE-0.5.2.md"
        ).read_text(encoding="utf-8")
        self.assertIn("0.5.2", historical_release)

        for schema_name in (
            "final-review-plan.schema.json",
            "final-review-manifest.schema.json",
        ):
            with self.subTest(schema_name=schema_name):
                schema = json.loads(
                    (SKILL_ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertTrue(
                    schema["$ref"].startswith("../references/schemas/final-review-")
                )

    def test_current_config_example_matches_review_policy_defaults(self):
        data = config.load_config_file(SKILL_ROOT / "examples" / "config.toml")
        self.assertEqual(
            data["defaults"]["review"], config.V053_REVIEW_POLICY_DEFAULTS
        )


def _extract_hloop_function_snippets(text):
    """Find each `hloop() { ... }` block, tracking brace depth (the body
    contains a nested `${CODEX_HOME:-$HOME/.codex}` brace pair, so a naive
    `[^}]*` regex would truncate at the wrong `}`)."""
    snippets = []
    start_marker = "hloop() {"
    cursor = 0
    while True:
        start = text.find(start_marker, cursor)
        if start == -1:
            break
        depth = 0
        end = None
        for pos in range(start, len(text)):
            char = text[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        if end is None:
            break
        snippets.append(text[start : end + 1])
        cursor = end + 1
    return snippets


class PortableReadmeSnippetExecutionTests(unittest.TestCase):
    """Actually execute README.md's `hloop()` snippet against HOME/CODEX_HOME paths containing spaces.

    A plain `HLOOP="python3 \"...\" --namespace ..."` string variable does not
    re-quote on `$HLOOP` expansion, so a space in $HOME/$CODEX_HOME used to
    split the resolved path into multiple argv words. The shell function form
    re-evaluates the quoted path fresh on every call, so it must keep working
    even when those directories contain spaces.
    """

    def _snippets(self):
        text = (SKILL_ROOT / "README.md").read_text(encoding="utf-8")
        snippets = _extract_hloop_function_snippets(text)
        self.assertTrue(snippets, "README.md must contain at least one hloop() shell function snippet")
        return snippets

    def _run_version(self, snippet, env):
        import subprocess

        script = snippet.replace("<namespace>", "test-namespace") + "\nhloop version"
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result

    def test_snippet_resolves_default_codex_home_under_spaced_home(self):
        import tempfile

        snippets = self._snippets()
        with tempfile.TemporaryDirectory() as tmp:
            spaced_home = Path(tmp) / "home with space"
            install_root = spaced_home / ".codex" / "skills" / "herdr-dev-loop"
            install_root.parent.mkdir(parents=True)
            os.symlink(SKILL_ROOT, install_root)

            env = dict(os.environ)
            env["HOME"] = str(spaced_home)
            env.pop("CODEX_HOME", None)

            for index, snippet in enumerate(snippets):
                with self.subTest(snippet_index=index):
                    result = self._run_version(snippet, env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("herdr-dev-loop", result.stdout)

    def test_snippet_resolves_explicit_codex_home_under_spaced_path(self):
        import tempfile

        snippets = self._snippets()
        with tempfile.TemporaryDirectory() as tmp:
            spaced_codex_home = Path(tmp) / "codex home with space"
            install_root = spaced_codex_home / "skills" / "herdr-dev-loop"
            install_root.parent.mkdir(parents=True)
            os.symlink(SKILL_ROOT, install_root)

            env = dict(os.environ)
            env["HOME"] = str(Path(tmp) / "unused-home")
            env["CODEX_HOME"] = str(spaced_codex_home)

            for index, snippet in enumerate(snippets):
                with self.subTest(snippet_index=index):
                    result = self._run_version(snippet, env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("herdr-dev-loop", result.stdout)


class CheckPythonCapabilityTests(unittest.TestCase):
    def test_current_interpreter_is_capable(self):
        config.check_python_capability()

    def test_rejects_old_python(self):
        fake_version = types.SimpleNamespace(major=3, minor=10)
        with self.assertRaises(config.ConfigCapabilityError):
            config.check_python_capability(fake_version)


class DiscoverConfigCandidatesTests(unittest.TestCase):
    def test_order_and_sources(self):
        env = {
            "HLOOP_CONFIG_HOME": "/tmp/hloop-home",
            "XDG_CONFIG_HOME": "/tmp/xdg-home",
            "HOME": "/tmp/home",
        }
        candidates = config.discover_config_candidates(env)
        self.assertEqual([c.source for c in candidates], ["HLOOP_CONFIG_HOME", "XDG_CONFIG_HOME", "default"])
        self.assertEqual(candidates[0].path, Path("/tmp/hloop-home/config.toml"))
        self.assertEqual(candidates[1].path, Path("/tmp/xdg-home/herdr-dev-loop/config.toml"))
        self.assertEqual(candidates[2].path, Path("/tmp/home/.config/herdr-dev-loop/config.toml"))

    def test_omits_unset_env_vars(self):
        env = {"HOME": "/tmp/home"}
        candidates = config.discover_config_candidates(env)
        self.assertEqual([c.source for c in candidates], ["default"])

    def test_find_config_file_picks_first_existing(self, tmp_dir=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            hloop_home = Path(tmp) / "hloop-home"
            xdg_home = Path(tmp) / "xdg-home" / "herdr-dev-loop"
            xdg_home.mkdir(parents=True)
            (xdg_home / "config.toml").write_text("version = 1\n")
            env = {"HLOOP_CONFIG_HOME": str(hloop_home), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg-home")}
            found = config.find_config_file(env)
            self.assertIsNotNone(found)
            self.assertEqual(found.source, "XDG_CONFIG_HOME")

    def test_find_config_file_none_when_nothing_exists(self):
        env = {"HOME": "/nonexistent-hloop-test-home"}
        self.assertIsNone(config.find_config_file(env))


class LoadConfigFileTests(unittest.TestCase):
    def test_loads_valid_toml(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('version = 1\n\n[defaults]\nmax_workers = 3\n')
            data = config.load_config_file(path)
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["defaults"]["max_workers"], 3)

    def test_raises_config_error_on_invalid_toml(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("this is not [ valid toml")
            with self.assertRaises(config.ConfigError):
                config.load_config_file(path)

    def test_raises_config_error_on_missing_file(self):
        with self.assertRaises(config.ConfigError):
            config.load_config_file(Path("/nonexistent/config.toml"))


class CanonicalizePathTests(unittest.TestCase):
    def test_expands_home(self):
        result = config.canonicalize_path("~/fullerene", base="/tmp")
        self.assertTrue(result.is_absolute())
        self.assertFalse(str(result).startswith("~"))

    def test_relative_path_resolved_against_base(self):
        result = config.canonicalize_path("child", base="/tmp/parent")
        self.assertEqual(result, Path("/tmp/parent/child"))

    def test_absolute_path_ignores_base(self):
        result = config.canonicalize_path("/abs/path", base="/tmp/parent")
        self.assertEqual(result, Path("/abs/path"))

    def test_resolves_symlinks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real_dir)
            resolved = config.canonicalize_path(link)
            self.assertEqual(resolved, real_dir.resolve())


class FindRepoRootTests(unittest.TestCase):
    def test_finds_git_root_from_nested_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            nested = root / "a" / "b" / "c"
            nested.mkdir(parents=True)
            found = config.find_repo_root(nested)
            self.assertEqual(found, root.resolve())

    def test_no_false_positive_within_a_repo_free_subtree(self):
        # Some hosts have a stray ancestor `.git` (e.g. /tmp/.git), so this
        # only asserts find_repo_root does not report our own bare subtree
        # as a repo root, rather than asserting a global None result.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b"
            nested.mkdir(parents=True)
            found = config.find_repo_root(nested)
            tmp_resolved = Path(tmp).resolve()
            if found is not None:
                self.assertFalse(tmp_resolved == found or tmp_resolved in found.parents)


class ValidateConfigTests(unittest.TestCase):
    def test_valid_minimal_config(self):
        config.validate_config({"version": 1})

    def test_valid_full_example_from_spec(self):
        data = {
            "version": 1,
            "defaults": {
                "max_workers": 3,
                "session_cleanup": "archive",
                "worker": {"provider": "codex", "model": "auto", "effort": "auto"},
                "reviewer": {"mode": "swarm", "provider": "codex", "model": "auto", "probe_count": 6},
            },
            "scope": [
                {"path": "~/fullerene", "worker": {"provider": "claude", "model": "sonnet"}},
                {
                    "path": "~/fullerene/vdr",
                    "reviewer": {
                        "mode": "dual-swarm",
                        "providers": ["codex", "claude"],
                        "probes_per_provider": 4,
                    },
                },
            ],
        }
        config.validate_config(data)

    def test_missing_version_is_error(self):
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config({})

    def test_wrong_type_version_is_error(self):
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config({"version": "1"})

    def test_unsupported_version_is_error(self):
        for version in (0, 2):
            with self.subTest(version=version), self.assertRaises(config.ConfigValidationError):
                config.validate_config({"version": version})

    def test_bool_rejected_for_int_field(self):
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config({"version": 1, "defaults": {"max_workers": True}})

    def test_unknown_top_level_key_is_error(self):
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config({"version": 1, "bogus": {}})

    def test_unknown_or_forbidden_nested_keys_are_errors(self):
        invalid_configs = {
            "defaults": {"version": 1, "defaults": {"bogus": True}},
            "worker": {"version": 1, "defaults": {"worker": {"setup_command": "echo unsafe"}}},
            "worker-review-option": {"version": 1, "defaults": {"worker": {"mode": "single"}}},
            "reviewer": {"version": 1, "defaults": {"reviewer": {"bogus": True}}},
            "scope": {"version": 1, "scope": [{"path": "/tmp", "bogus": True}]},
            "scope-role": {
                "version": 1,
                "scope": [{"path": "/tmp", "reviewer": {"setup_command": "echo unsafe"}}],
            },
        }
        for name, data in invalid_configs.items():
            with self.subTest(name=name), self.assertRaises(config.ConfigValidationError):
                config.validate_config(data)

    def test_invalid_enum_values_are_errors(self):
        invalid_configs = {
            "cleanup": {"version": 1, "defaults": {"session_cleanup": "later"}},
            "worker-provider": {"version": 1, "defaults": {"worker": {"provider": "other"}}},
            "reviewer-provider": {"version": 1, "defaults": {"reviewer": {"provider": "other"}}},
            "reviewer-providers": {
                "version": 1,
                "defaults": {"reviewer": {"providers": ["codex", "other"]}},
            },
            "review-mode": {"version": 1, "defaults": {"reviewer": {"mode": "many"}}},
            "scoped-cleanup": {"version": 1, "scope": [{"path": "/tmp", "session_cleanup": "later"}]},
            "specification-scout": {
                "version": 1,
                "defaults": {"specification_scout": "sometimes"},
            },
        }
        for name, data in invalid_configs.items():
            with self.subTest(name=name), self.assertRaises(config.ConfigValidationError):
                config.validate_config(data)

    def test_specification_scout_modes_are_valid_at_defaults_and_scope(self):
        for mode in ("auto", "always", "off"):
            with self.subTest(mode=mode):
                config.validate_config(
                    {
                        "version": 1,
                        "defaults": {"specification_scout": mode},
                        "scope": [
                            {"path": "/tmp/repo", "specification_scout": mode}
                        ],
                    }
                )

    def test_invalid_integer_ranges_are_errors(self):
        invalid_configs = {
            "zero-workers": {"version": 1, "defaults": {"max_workers": 0}},
            "negative-scope-workers": {"version": 1, "scope": [{"path": "/tmp", "max_workers": -1}]},
            "too-few-probes": {"version": 1, "defaults": {"reviewer": {"probe_count": 3}}},
            "too-many-probes": {"version": 1, "defaults": {"reviewer": {"probe_count": 9}}},
            "too-few-provider-probes": {
                "version": 1,
                "defaults": {"reviewer": {"probes_per_provider": 3}},
            },
            "too-many-provider-probes": {
                "version": 1,
                "defaults": {"reviewer": {"probes_per_provider": 9}},
            },
        }
        for name, data in invalid_configs.items():
            with self.subTest(name=name), self.assertRaises(config.ConfigValidationError):
                config.validate_config(data)

    def test_scope_missing_path_is_error(self):
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config({"version": 1, "scope": [{"match": "repo"}]})

    def test_scope_invalid_match_is_error(self):
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config({"version": 1, "scope": [{"path": "~/x", "match": "bogus"}]})

    def test_relative_scope_path_is_error_regardless_of_base(self):
        data = {"version": 1, "scope": [{"path": "."}, {"path": "."}]}
        messages = []
        for base in ("/tmp/repo", "/tmp/repo/nested"):
            with self.subTest(base=base), self.assertRaises(config.ConfigValidationError) as ctx:
                config.validate_config(data, base=base)
            messages.append(str(ctx.exception))
        self.assertIn("relative paths are cwd-dependent", messages[0])
        self.assertEqual(messages[0], messages[1])

    def test_duplicate_scope_same_match_and_path_is_error(self):
        data = {
            "version": 1,
            "scope": [
                {"path": "/tmp/dup", "match": "repo"},
                {"path": "/tmp/dup", "match": "repo"},
            ],
        }
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config(data)

    def test_same_path_different_match_is_not_duplicate(self):
        data = {
            "version": 1,
            "scope": [
                {"path": "/tmp/dup", "match": "repo"},
                {"path": "/tmp/dup", "match": "cwd"},
            ],
        }
        config.validate_config(data)

    def test_same_table_probe_count_and_probes_per_provider_is_error(self):
        data = {
            "version": 1,
            "defaults": {
                "reviewer": {"mode": "dual-swarm", "probe_count": 6, "probes_per_provider": 4}
            },
        }
        with self.assertRaises(config.ConfigValidationError) as ctx:
            config.validate_config(data)
        self.assertIn("must not set both probe_count and probes_per_provider", str(ctx.exception))

    def test_same_table_conflict_is_error_regardless_of_key_order(self):
        forward = {
            "version": 1,
            "defaults": {"reviewer": {"probe_count": 6, "probes_per_provider": 4}},
        }
        reversed_order = {
            "version": 1,
            "defaults": {"reviewer": {"probes_per_provider": 4, "probe_count": 6}},
        }
        for name, data in (("forward", forward), ("reversed", reversed_order)):
            with self.subTest(name=name), self.assertRaises(config.ConfigValidationError):
                config.validate_config(data)

    def test_same_table_conflict_in_scope_reviewer_is_error(self):
        data = {
            "version": 1,
            "scope": [
                {
                    "path": "/tmp/repo",
                    "reviewer": {"probes_per_provider": 4, "probe_count": 6},
                }
            ],
        }
        with self.assertRaises(config.ConfigValidationError) as ctx:
            config.validate_config(data)
        self.assertIn("must not set both probe_count and probes_per_provider", str(ctx.exception))

    def test_reviewer_providers_must_be_string_list(self):
        data = {"version": 1, "defaults": {"reviewer": {"providers": ["codex", 5]}}}
        with self.assertRaises(config.ConfigValidationError):
            config.validate_config(data)

    def test_collects_multiple_errors(self):
        data = {"bogus": 1, "scope": [{"match": "nope"}]}
        with self.assertRaises(config.ConfigValidationError) as ctx:
            config.validate_config(data)
        message = str(ctx.exception)
        self.assertIn("version is required", message)
        self.assertIn("unknown top-level key", message)
        self.assertIn("scope[0]", message)


class MatchScopesTests(unittest.TestCase):
    def test_repo_scope_matches_ancestor_of_repo_root(self):
        scopes = [{"path": "/home/user/fullerene"}]
        matched = config.match_scopes(
            scopes, repo_root=Path("/home/user/fullerene/vdr"), cwd=Path("/home/user/fullerene/vdr/sub")
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].match_kind, "repo")

    def test_cwd_scope_requires_explicit_match(self):
        scopes = [{"path": "/home/user/fullerene/vdr/sub", "match": "cwd"}]
        matched = config.match_scopes(
            scopes, repo_root=Path("/home/user/fullerene"), cwd=Path("/home/user/fullerene/vdr/sub")
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].match_kind, "cwd")

    def test_non_ancestor_scope_does_not_match(self):
        scopes = [{"path": "/other/place"}]
        matched = config.match_scopes(
            scopes, repo_root=Path("/home/user/fullerene"), cwd=Path("/home/user/fullerene")
        )
        self.assertEqual(matched, [])

    def test_no_repo_root_skips_repo_scopes(self):
        scopes = [{"path": "/home/user/fullerene"}]
        matched = config.match_scopes(scopes, repo_root=None, cwd=Path("/home/user/fullerene"))
        self.assertEqual(matched, [])

    def test_sorted_shallow_to_deep(self):
        scopes = [
            {"path": "/home/user/fullerene/vdr"},
            {"path": "/home/user/fullerene"},
        ]
        matched = config.match_scopes(
            scopes, repo_root=Path("/home/user/fullerene/vdr"), cwd=Path("/home/user/fullerene/vdr")
        )
        self.assertEqual([m.canonical_path for m in matched], [Path("/home/user/fullerene"), Path("/home/user/fullerene/vdr")])

    def test_symlink_scope_path_resolved_before_matching(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            (real_dir / "sub").mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real_dir)
            scopes = [{"path": str(link)}]
            matched = config.match_scopes(scopes, repo_root=real_dir / "sub", cwd=real_dir / "sub")
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0].canonical_path, real_dir.resolve())


class DeepMergeWithSourceTests(unittest.TestCase):
    def test_later_layer_overrides_leaf(self):
        resolution = config.deep_merge_with_source(
            [
                ("built-in-default", {"max_workers": 1}),
                ("config-defaults", {"max_workers": 3}),
            ]
        )
        self.assertEqual(resolution.get("max_workers"), 3)
        self.assertEqual(resolution.source_of("max_workers"), "config-defaults")

    def test_nested_keys_merge_independently(self):
        resolution = config.deep_merge_with_source(
            [
                ("built-in-default", {"worker": {"provider": "codex", "model": "auto"}}),
                ("config-defaults", {"worker": {"provider": "claude"}}),
            ]
        )
        self.assertEqual(resolution.get("worker", "provider"), "claude")
        self.assertEqual(resolution.get("worker", "model"), "auto")
        self.assertEqual(resolution.source_of("worker", "provider"), "config-defaults")
        self.assertEqual(resolution.source_of("worker", "model"), "built-in-default")

    def test_explain_and_as_dict(self):
        resolution = config.deep_merge_with_source(
            [("built-in-default", {"a": {"b": 1, "c": 2}})]
        )
        explanation = resolution.explain()
        self.assertEqual(
            explanation,
            [
                {"key": "a.b", "value": 1, "source": "built-in-default"},
                {"key": "a.c", "value": 2, "source": "built-in-default"},
            ],
        )
        self.assertEqual(resolution.as_dict(), {"a": {"b": 1, "c": 2}})

    def test_empty_layer_is_ignored(self):
        resolution = config.deep_merge_with_source([("built-in-default", {"a": 1}), ("config-defaults", None)])
        self.assertEqual(resolution.get("a"), 1)

    def test_later_layer_probes_per_provider_clears_inherited_probe_count(self):
        resolution = config.deep_merge_with_source(
            [
                ("config-defaults", {"reviewer": {"mode": "swarm", "probe_count": 6}}),
                (
                    "scope:cwd:/high-risk",
                    {"reviewer": {"mode": "dual-swarm", "probes_per_provider": 4}},
                ),
            ]
        )
        self.assertEqual(resolution.get("reviewer", "probes_per_provider"), 4)
        self.assertIsNone(resolution.get("reviewer", "probe_count"))

    def test_later_layer_probe_count_clears_inherited_probes_per_provider(self):
        resolution = config.deep_merge_with_source(
            [
                ("config-defaults", {"reviewer": {"probes_per_provider": 4}}),
                ("scope:cwd:/other", {"reviewer": {"probe_count": 8}}),
            ]
        )
        self.assertEqual(resolution.get("reviewer", "probe_count"), 8)
        self.assertIsNone(resolution.get("reviewer", "probes_per_provider"))

    def test_exclusive_clearing_does_not_affect_unrelated_keys(self):
        resolution = config.deep_merge_with_source(
            [
                (
                    "config-defaults",
                    {"reviewer": {"provider": "codex", "probe_count": 6}},
                ),
                ("scope:cwd:/x", {"reviewer": {"probes_per_provider": 4}}),
            ]
        )
        self.assertEqual(resolution.get("reviewer", "provider"), "codex")
        self.assertEqual(resolution.get("reviewer", "probes_per_provider"), 4)
        self.assertIsNone(resolution.get("reviewer", "probe_count"))


class ResolveConfigTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()
        self.nested = self.root / "sub"
        self.nested.mkdir()

    def test_built_in_defaults_only_when_no_config(self):
        resolution = config.resolve_config({"max_workers": 3}, None, target_dir=self.nested)
        self.assertEqual(resolution.get("max_workers"), 3)
        self.assertEqual(resolution.source_of("max_workers"), "built-in-default")

    def test_full_precedence_chain(self):
        config_data = {
            "version": 1,
            "defaults": {"max_workers": 5, "worker": {"provider": "codex"}},
            "scope": [
                {"path": str(self.root), "worker": {"provider": "claude"}},
            ],
        }
        resolution = config.resolve_config(
            {"max_workers": 1, "worker": {"provider": "built-in", "model": "auto"}},
            config_data,
            target_dir=self.nested,
            task_override={"worker": {"model": "opus"}},
            start_override={"max_workers": 9},
        )
        self.assertEqual(resolution.get("max_workers"), 9)
        self.assertEqual(resolution.source_of("max_workers"), "start-override")
        self.assertEqual(resolution.get("worker", "provider"), "claude")
        self.assertTrue(resolution.source_of("worker", "provider").startswith("scope:repo:"))
        self.assertEqual(resolution.get("worker", "model"), "opus")
        self.assertEqual(resolution.source_of("worker", "model"), "task-override")

    def test_scope_can_override_valid_default_values(self):
        config_data = {
            "version": 1,
            "defaults": {"max_workers": 2, "session_cleanup": "archive"},
            "scope": [{"path": str(self.root), "max_workers": 4, "session_cleanup": "none"}],
        }
        resolution = config.resolve_config({}, config_data, target_dir=self.nested)
        self.assertEqual(resolution.get("max_workers"), 4)
        self.assertEqual(resolution.get("session_cleanup"), "none")
        self.assertTrue(resolution.source_of("max_workers").startswith("scope:repo:"))

    def test_repo_scope_independent_of_subdirectory(self):
        config_data = {"version": 1, "scope": [{"path": str(self.root), "worker": {"provider": "claude"}}]}
        deeper = self.nested / "deeper"
        deeper.mkdir()
        resolution_shallow = config.resolve_config({}, config_data, target_dir=self.nested)
        resolution_deep = config.resolve_config({}, config_data, target_dir=deeper)
        self.assertEqual(resolution_shallow.get("worker", "provider"), "claude")
        self.assertEqual(resolution_deep.get("worker", "provider"), "claude")

    def test_relative_scope_cannot_change_result_by_invocation_directory(self):
        config_data = {"version": 1, "scope": [{"path": ".", "worker": {"provider": "claude"}}]}
        deeper = self.nested / "deeper"
        deeper.mkdir()
        for target_dir in (self.root, deeper):
            with self.subTest(target_dir=target_dir), self.assertRaises(config.ConfigValidationError):
                config.resolve_config({}, config_data, target_dir=target_dir)

    def test_cwd_scope_only_matches_explicit_directory(self):
        config_data = {
            "version": 1,
            "scope": [{"path": str(self.nested), "match": "cwd", "worker": {"provider": "claude"}}],
        }
        resolution_at_nested = config.resolve_config({}, config_data, target_dir=self.nested)
        resolution_at_root = config.resolve_config({}, config_data, target_dir=self.root)
        self.assertEqual(resolution_at_nested.get("worker", "provider"), "claude")
        self.assertIsNone(resolution_at_root.get("worker", "provider"))

    def test_invalid_config_data_raises_during_resolve(self):
        with self.assertRaises(config.ConfigValidationError):
            config.resolve_config({}, {"bogus": True}, target_dir=self.nested)

    def test_shipped_high_risk_example_resolves_probes_per_provider_only(self):
        """Reproduce the shipped examples/config.toml high-risk-service scope.

        `[defaults.reviewer]` sets `probe_count`; the deeper cwd-matched
        `high-risk-service` scope sets only `probes_per_provider`. The
        resolved reviewer topology must carry exactly one of the two
        exclusive knobs, or a dual-swarm 4-probes-per-provider review start
        raises "set only one of --probe-count or --probes-per-provider".
        """

        high_risk = self.nested / "high-risk-service"
        high_risk.mkdir()
        config_data = {
            "version": 1,
            "defaults": {
                "reviewer": {"mode": "swarm", "provider": "codex", "probe_count": 6}
            },
            "scope": [
                {
                    "path": str(high_risk),
                    "match": "cwd",
                    "reviewer": {
                        "mode": "dual-swarm",
                        "providers": ["codex", "claude"],
                        "probes_per_provider": 4,
                    },
                }
            ],
        }
        resolution = config.resolve_config({}, config_data, target_dir=high_risk)
        self.assertEqual(resolution.get("reviewer", "mode"), "dual-swarm")
        self.assertEqual(resolution.get("reviewer", "probes_per_provider"), 4)
        self.assertIsNone(resolution.get("reviewer", "probe_count"))


class LoadAndResolveTests(unittest.TestCase):
    def test_end_to_end_with_real_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            config_home = Path(tmp) / "config-home"
            config_home.mkdir()
            (config_home / "config.toml").write_text(
                "version = 1\n\n[defaults]\nmax_workers = 4\n\n"
                f'[[scope]]\npath = "{repo}"\n\n[scope.worker]\nprovider = "claude"\n'
            )
            env = {"HLOOP_CONFIG_HOME": str(config_home)}

            resolution, candidate = config.load_and_resolve(
                {"max_workers": 1}, target_dir=repo, env=env
            )
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate.source, "HLOOP_CONFIG_HOME")
            self.assertEqual(resolution.get("max_workers"), 4)
            self.assertEqual(resolution.get("worker", "provider"), "claude")

    def test_no_config_file_falls_back_to_built_in(self):
        env = {"HOME": "/nonexistent-hloop-test-home"}
        resolution, candidate = config.load_and_resolve({"max_workers": 2}, env=env)
        self.assertIsNone(candidate)
        self.assertEqual(resolution.get("max_workers"), 2)


if __name__ == "__main__":
    unittest.main()
