import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "hloop"
sys.path.insert(0, str(SCRIPT.parent))
loader = importlib.machinery.SourceFileLoader("hloop_v05_runtime_runtime", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)
hloop_broker = hloop.hloop_broker


class RepositoryLockRuntimeTests(unittest.TestCase):
    """Exercises the private cross-worktree repository transaction lock."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-repository-lock")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_worktrees(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        linked = root / "linked"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "worktree", "add", "-b", "linked", str(linked), "HEAD"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return repo, linked

    def wait_for_path(self, path: Path, *, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.01)
        return path.exists()

    def subprocess_lock_helper(self) -> str:
        return """
import importlib.machinery
import importlib.util
import sys
import time
from pathlib import Path

script, repo, namespace, runtime_base, role, ready, acquired, release, path_output = sys.argv[1:]
sys.path.insert(0, str(Path(script).parent))
loader = importlib.machinery.SourceFileLoader("hloop_lock_subprocess", script)
spec = importlib.util.spec_from_loader(loader.name, loader)
hloop = importlib.util.module_from_spec(spec)
loader.exec_module(hloop)
hloop.configure_loop_namespace(namespace)
hloop.REPOSITORY_LOCK_RUNTIME_BASE = Path(runtime_base)
Path(path_output).write_text(str(hloop.repo_lock_path(Path(repo))), encoding="utf-8")
real_flock = hloop.fcntl.flock
def observed_flock(descriptor, operation):
    if operation & hloop.fcntl.LOCK_EX:
        Path(ready).write_text("ready", encoding="utf-8")
    return real_flock(descriptor, operation)
hloop.fcntl.flock = observed_flock
with hloop.loop_lock(Path(repo)):
    Path(acquired).write_text("acquired", encoding="utf-8")
    if role == "holder":
        deadline = time.monotonic() + 15.0
        while not Path(release).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not Path(release).exists():
            raise TimeoutError("release marker was not created")
"""

    def test_default_lock_runtime_base_is_fixed_posix_tmp(self):
        self.assertEqual(hloop.REPOSITORY_LOCK_RUNTIME_BASE, Path("/tmp"))

    def test_lock_path_is_private_shared_and_outside_protected_git_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, linked = self.init_worktrees(root)
            runtime_base = root / "runtime"
            linked_git_dir = Path(
                hloop.git(linked, ["rev-parse", "--path-format=absolute", "--git-dir"])
            )
            protected_mode = stat.S_IMODE(linked_git_dir.stat().st_mode)

            with mock.patch.object(
                hloop, "REPOSITORY_LOCK_RUNTIME_BASE", runtime_base
            ), mock.patch.dict(
                os.environ,
                {
                    "HLOOP_RUNTIME_DIR": str(root / "ignored-hloop-runtime"),
                    "XDG_RUNTIME_DIR": str(root / "ignored-xdg-runtime"),
                    "TMPDIR": str(root / "ignored-tmpdir"),
                },
            ):
                repo_path = hloop.repo_lock_path(repo)
                linked_path = hloop.repo_lock_path(linked)
                self.assertEqual(repo_path, linked_path)
                self.assertTrue(repo_path.is_relative_to(runtime_base))
                self.assertFalse(repo_path.is_relative_to(hloop.git_common_dir(repo)))
                expected_digest = hashlib.sha256(
                    f"{hloop.git_common_dir(repo)}\0{hloop.LOOP_NAMESPACE}".encode("utf-8")
                ).hexdigest()
                self.assertEqual(repo_path.name, f"{expected_digest}.lock")

                linked_git_dir.chmod(0o500)
                try:
                    with hloop.loop_lock(linked):
                        self.assertTrue(repo_path.exists())
                finally:
                    linked_git_dir.chmod(protected_mode)

                self.assertEqual(stat.S_IMODE(repo_path.parent.stat().st_mode), 0o700)
                self.assertEqual(
                    stat.S_IMODE(repo_path.parent.parent.stat().st_mode), 0o700
                )
                self.assertEqual(stat.S_IMODE(repo_path.stat().st_mode), 0o600)
                self.assertEqual(repo_path.stat().st_uid, os.geteuid())
                old_lock = linked_git_dir / "hloop.lock"
                self.assertFalse(old_lock.exists())

                hloop.configure_loop_namespace("test-repository-lock-other")
                try:
                    self.assertNotEqual(hloop.repo_lock_path(repo), repo_path)
                finally:
                    hloop.configure_loop_namespace("test-repository-lock")

    def test_uid_private_runtime_root_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _linked = self.init_worktrees(root)
            runtime_base = root / "runtime"
            runtime_base.mkdir()
            symlink_target = root / "attacker-controlled"
            symlink_target.mkdir()
            uid = os.geteuid()
            (runtime_base / f"herdr-dev-loop-{uid}").symlink_to(
                symlink_target, target_is_directory=True
            )

            with mock.patch.object(
                hloop, "REPOSITORY_LOCK_RUNTIME_BASE", runtime_base
            ), self.assertRaisesRegex(hloop.HLoopError, "must be a real directory"):
                hloop.repo_lock_path(repo)

    def test_divergent_process_environments_contend_on_the_same_flock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _linked = self.init_worktrees(root)
            runtime_base = root / "canonical-runtime"
            markers = root / "markers"
            markers.mkdir()
            release = markers / "release"
            first_ready = markers / "first-ready"
            first_acquired = markers / "first-acquired"
            first_path = markers / "first-path"
            second_ready = markers / "second-ready"
            second_acquired = markers / "second-acquired"
            second_path = markers / "second-path"
            helper = self.subprocess_lock_helper()
            base_args = [
                sys.executable,
                "-c",
                helper,
                str(SCRIPT),
                str(repo),
                hloop.LOOP_NAMESPACE,
                str(runtime_base),
            ]
            first_env = os.environ.copy()
            first_env.update(
                {
                    "HLOOP_RUNTIME_DIR": str(root / "process-a-hloop"),
                    "XDG_RUNTIME_DIR": str(root / "process-a-xdg"),
                    "TMPDIR": str(root / "process-a-tmp"),
                }
            )
            second_env = os.environ.copy()
            second_env.update(
                {
                    "HLOOP_RUNTIME_DIR": str(root / "process-b-hloop"),
                    "XDG_RUNTIME_DIR": str(root / "process-b-xdg"),
                    "TMPDIR": str(root / "process-b-tmp"),
                }
            )

            first = subprocess.Popen(
                base_args
                + [
                    "holder",
                    str(first_ready),
                    str(first_acquired),
                    str(release),
                    str(first_path),
                ],
                env=first_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            second = None
            try:
                self.assertTrue(self.wait_for_path(first_ready))
                self.assertTrue(self.wait_for_path(first_acquired))
                second = subprocess.Popen(
                    base_args
                    + [
                        "contender",
                        str(second_ready),
                        str(second_acquired),
                        str(release),
                        str(second_path),
                    ],
                    env=second_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertTrue(self.wait_for_path(second_ready))
                self.assertFalse(self.wait_for_path(second_acquired, timeout=0.2))
                self.assertEqual(
                    first_path.read_text(encoding="utf-8"),
                    second_path.read_text(encoding="utf-8"),
                )
                release.write_text("release\n", encoding="utf-8")
                self.assertTrue(self.wait_for_path(second_acquired))
                first_stdout, first_stderr = first.communicate(timeout=5)
                second_stdout, second_stderr = second.communicate(timeout=5)
                self.assertEqual((first.returncode, first_stdout, first_stderr), (0, "", ""))
                self.assertEqual(
                    (second.returncode, second_stdout, second_stderr), (0, "", "")
                )
            finally:
                release.touch(exist_ok=True)
                for process in (first, second):
                    if process is not None and process.poll() is None:
                        process.kill()
                    if process is not None:
                        process.communicate(timeout=5)

    def test_linked_worktrees_contend_on_the_same_flock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, linked = self.init_worktrees(root)
            runtime_base = root / "runtime"
            first_acquired = threading.Event()
            release_first = threading.Event()
            second_called_flock = threading.Event()
            second_acquired = threading.Event()
            errors = []
            real_flock = hloop.fcntl.flock

            def observed_flock(descriptor, operation):
                if (
                    threading.current_thread().name == "second-lock"
                    and operation & hloop.fcntl.LOCK_EX
                ):
                    second_called_flock.set()
                return real_flock(descriptor, operation)

            def hold_first():
                try:
                    with hloop.loop_lock(repo):
                        first_acquired.set()
                        if not release_first.wait(timeout=2):
                            raise TimeoutError("first lock was not released")
                except BaseException as exc:
                    errors.append(exc)

            def acquire_second():
                try:
                    with hloop.loop_lock(linked):
                        second_acquired.set()
                except BaseException as exc:
                    errors.append(exc)

            with mock.patch.object(
                hloop, "REPOSITORY_LOCK_RUNTIME_BASE", runtime_base
            ), mock.patch.object(hloop.fcntl, "flock", side_effect=observed_flock):
                first = threading.Thread(target=hold_first, name="first-lock")
                second = threading.Thread(target=acquire_second, name="second-lock")
                first.start()
                self.assertTrue(first_acquired.wait(timeout=2))
                second.start()
                self.assertTrue(second_called_flock.wait(timeout=2))
                self.assertFalse(second_acquired.wait(timeout=0.1))
                release_first.set()
                self.assertTrue(second_acquired.wait(timeout=2))
                first.join(timeout=2)
                second.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])


class ProviderPreflightIntegrationTests(unittest.TestCase):
    """Exercises the hloop_lib.providers wiring inside preflight_loop."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-provider-preflight")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
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
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        return repo

    def write_fake_provider(self, bin_dir: Path, name: str, help_text: str) -> None:
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--help\" ] || [ \"$2\" = \"--help\" ]; then\n"
            f"  printf '%s' {self._quote(help_text)}\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _quote(text: str) -> str:
        return "'" + text.replace("'", "'\\''") + "'"

    def prepended_path_env(self, bin_dir: Path):
        return mock.patch.dict(
            os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        )

    def test_preflight_records_provider_capability_when_flags_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.write_fake_provider(
                bin_dir,
                "codex",
                "usage: codex --sandbox <mode> --ask-for-approval <mode> --no-alt-screen",
            )

            with self.prepended_path_env(bin_dir):
                state = hloop.preflight_loop(
                    repo, require_agent_provider="codex", require_integration_branch=True
                )

            preflight = state.get("last_provider_preflight")
            self.assertIsNotNone(preflight)
            self.assertEqual(preflight["provider"], "codex")
            self.assertEqual(preflight["capability"], "supported")
            self.assertTrue(preflight["launch_allowed"])

    def test_preflight_probes_the_exact_role_invocation_not_a_generic_stand_in(self):
        """`require_agent_invocation` must probe the real launch argv.

        A role-specific invocation (explicit model/effort/exec runner) that
        the generic TUI/auto preflight probe would never construct must still
        be exactly what gets probed and recorded, so `last_provider_preflight`
        describes the actual launch, not a fixed stand-in.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.write_fake_provider(
                bin_dir,
                "codex",
                "usage: codex exec --sandbox <mode> -c <key=value> --output-last-message <path>",
            )
            output_path = root / "last.txt"
            invocation = hloop.hloop_providers.build_provider_invocation(
                provider="codex",
                runner="exec",
                sandbox="workspace-write",
                prompt_path=root / "prompt.md",
                model="auto",
                effort="high",
                output_path=output_path,
            )

            with self.prepended_path_env(bin_dir):
                state = hloop.preflight_loop(
                    repo,
                    require_agent_invocation=invocation,
                    require_integration_branch=True,
                )

            preflight = state.get("last_provider_preflight")
            self.assertIsNotNone(preflight)
            self.assertEqual(preflight["argv"], list(invocation.argv))
            self.assertEqual(preflight["runner"], "exec")
            self.assertEqual(preflight["effort"], "high")
            self.assertTrue(preflight["launch_allowed"])

    def test_preflight_fails_when_provider_lacks_required_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.write_fake_provider(bin_dir, "codex", "usage: codex [subcommand]")

            with self.prepended_path_env(bin_dir):
                with self.assertRaisesRegex(hloop.HLoopError, "lacks required flags"):
                    hloop.preflight_loop(
                        repo,
                        require_agent_provider="codex",
                        require_integration_branch=True,
                    )

            blocked = hloop.load_state(repo)
            self.assertEqual(blocked["phase"], "blocked_environment")
            preflight = blocked.get("last_provider_preflight")
            self.assertIsNotNone(preflight)
            self.assertEqual(preflight["capability"], "unsupported")
            self.assertFalse(preflight["launch_allowed"])
            self.assertEqual(
                blocked["last_preflight_error"]["reason"], preflight["reason"]
            )

    def test_preflight_blocks_on_git_executable_identity_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            recorded = hloop.git_executable_identity()
            state = hloop.load_state(repo)
            state["git_identity"] = {**recorded, "sha256": "0" * 64}
            hloop.save_state(repo, state)

            with self.assertRaisesRegex(hloop.HLoopError, "git executable identity drift"):
                hloop.preflight_loop(repo, require_integration_branch=True)

            blocked = hloop.load_state(repo)
            self.assertEqual(blocked["phase"], "blocked_environment")
            self.assertIn(
                "git executable identity drift", blocked["last_preflight_error"]["reason"]
            )

    def test_trust_git_updates_recorded_identity_and_requires_idle_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["git_identity"] = {**hloop.git_executable_identity(), "sha256": "0" * 64}
            state["tasks"] = {"T001": {"status": "running"}}
            hloop.save_state(repo, state)

            with self.assertRaisesRegex(hloop.HLoopError, "role is running"):
                hloop.cmd_runtime_trust_git(
                    SimpleNamespace(repo=str(repo), reason="package upgrade")
                )

            state["tasks"]["T001"]["status"] = "merged"
            hloop.save_state(repo, state)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    hloop.cmd_runtime_trust_git(
                        SimpleNamespace(repo=str(repo), reason="package upgrade")
                    ),
                    0,
                )
            trusted = hloop.load_state(repo)
            self.assertEqual(
                trusted["git_identity"]["sha256"], hloop.git_executable_identity()["sha256"]
            )
            hloop.preflight_loop(repo, require_integration_branch=True)

    def test_main_never_executes_a_malicious_path_git_before_identity_check(self):
        """A PATH-substituted `git` must be refused, and never executed.

        `main()` resolves/verifies the trusted git identity before
        `repo_root()`/`loop_lock()` (or any other code path) executes a PATH
        `git`. Fingerprinting is filesystem-only (path/device/inode/hash), so
        the substituted binary should never run even once.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            recorded = hloop.git_executable_identity()
            state = hloop.load_state(repo)
            state["git_identity"] = recorded
            hloop.save_state(repo, state)

            marker = root / "malicious-git-ran.marker"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            malicious_git = bin_dir / "git"
            malicious_git.write_text(
                "#!/bin/sh\n"
                f"echo ran >> {marker}\n"
                "exit 0\n",
                encoding="utf-8",
            )
            malicious_git.chmod(
                malicious_git.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
            )

            previous_trusted = hloop.TRUSTED_GIT_PATH
            try:
                with self.prepended_path_env(bin_dir), contextlib.redirect_stderr(io.StringIO()):
                    code = hloop.main(
                        [
                            "--repo",
                            str(repo),
                            "--namespace",
                            hloop.LOOP_NAMESPACE,
                            "status",
                        ]
                    )
            finally:
                hloop.TRUSTED_GIT_PATH = previous_trusted

            self.assertEqual(code, 2)
            self.assertFalse(marker.exists(), "malicious PATH git must never execute")

            unchanged = hloop.load_state(repo)
            self.assertEqual(unchanged, state)


class MergeCrashRecoveryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-merge-crash-recovery")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=master"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
        )
        return repo

    def test_save_state_fsyncs_file_before_replace_and_parent_after_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            events = []
            real_fsync = os.fsync
            real_replace = os.replace

            def record_fsync(descriptor):
                events.append("fsync")
                return real_fsync(descriptor)

            def record_replace(source, destination):
                events.append("replace")
                return real_replace(source, destination)

            state = {
                "state_format_version": hloop.STATE_FORMAT_VERSION,
                "schema_revision": hloop.STATE_SCHEMA_REVISION,
                "namespace": hloop.LOOP_NAMESPACE,
                "phase": "dispatching",
                "integration_branch": "master",
            }
            with mock.patch.object(hloop.os, "fsync", side_effect=record_fsync), mock.patch.object(
                hloop.os, "replace", side_effect=record_replace
            ):
                hloop.save_state(repo, state)

            self.assertEqual(events, ["fsync", "replace", "fsync"])
            self.assertEqual(hloop.load_state(repo)["phase"], "dispatching")
            self.assertEqual(list(hloop.state_path(repo).parent.glob(".STATE.json.*.tmp")), [])

    def test_merge_reconciles_squash_commit_landed_before_final_state_save(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            pre_merge_head = hloop.git(repo, ["rev-parse", "HEAD"])
            subprocess.run(
                ["git", "switch", "-c", "worker"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            (repo / "feature.txt").write_text("landed once\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "worker change"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            worker_head = hloop.git(repo, ["rev-parse", "HEAD"])
            subprocess.run(
                ["git", "switch", "master"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            transaction = hloop.build_merge_transaction(
                task_id="T001",
                attempt_id="T001-A001",
                branch="worker",
                pre_merge_head=pre_merge_head,
                worker_head=worker_head,
                index_state=hloop.merge_transaction_index_state(repo, "HEAD"),
                changed_paths=("feature.txt",),
            )
            state = {
                "state_format_version": hloop.STATE_FORMAT_VERSION,
                "schema_revision": hloop.STATE_SCHEMA_REVISION,
                "namespace": hloop.LOOP_NAMESPACE,
                "phase": "dispatching",
                "integration_branch": "master",
                "merge_mode": "squash",
                "manager_qa_profile": "none",
                "tasks": {
                    "T001": {
                        "status": "result_reported",
                        "branch": "worker",
                    }
                },
                "active_merge": transaction.to_record(),
            }
            hloop.save_state(repo, state)
            subprocess.run(
                ["git", "merge", "--squash", "worker"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            transaction = hloop.record_squash_tree(repo, state, transaction)
            subprocess.run(
                ["git", "commit", "-m", "ai-loop(T001): squash worker"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "commit", "--amend", "-m", "manual commit"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            args = SimpleNamespace(
                repo=str(repo),
                task_id="T001",
                abort=False,
                continue_merge=False,
                retry=False,
                mode=None,
                dry_run=False,
            )
            with mock.patch.object(
                hloop, "preflight_loop", return_value=hloop.load_state(repo)
            ):
                with self.assertRaisesRegex(hloop.HLoopError, "commit subject"):
                    hloop.cmd_merge(args)
            self.assertIn("active_merge", hloop.load_state(repo))

            subprocess.run(
                ["git", "commit", "--amend", "-m", "ai-loop(T001): squash worker"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
            )
            landed_head = hloop.git(repo, ["rev-parse", "HEAD"])
            commit_count = hloop.git(repo, ["rev-list", "--count", "HEAD"])
            self.assertEqual(
                hloop.load_state(repo)["active_merge"]["squash_tree"],
                hloop.merge_transaction_index_state(repo, landed_head),
            )

            with mock.patch.object(
                hloop, "preflight_loop", return_value=hloop.load_state(repo)
            ), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(hloop.cmd_merge(args), 0)

            reconciled = hloop.load_state(repo)
            self.assertIn("reconciled merge T001", output.getvalue())
            self.assertNotIn("active_merge", reconciled)
            self.assertEqual(reconciled["tasks"]["T001"]["status"], "merged")
            self.assertEqual(reconciled["last_merged_task"], "T001")
            self.assertEqual(reconciled["unreviewed_merge_count"], 1)
            self.assertEqual(reconciled["ungapped_merge_count"], 1)
            self.assertEqual(hloop.git(repo, ["rev-parse", "HEAD"]), landed_head)
            self.assertEqual(hloop.git(repo, ["rev-list", "--count", "HEAD"]), commit_count)

            with mock.patch.object(hloop, "preflight_loop", return_value=reconciled):
                with self.assertRaisesRegex(hloop.HLoopError, "task is not merge ready"):
                    hloop.cmd_merge(args)
            self.assertEqual(hloop.git(repo, ["rev-parse", "HEAD"]), landed_head)


class BrokerReportLifecycleTests(unittest.TestCase):
    """Exercises agent report / inbox / manager sleep-next / broker status-recover."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-broker-report-lifecycle")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
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
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "run_id": "run-001",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        store = hloop._open_broker_store(repo)
        with store.transaction() as transaction:
            store.register_active_role(
                transaction,
                run_id="run-001",
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
                token="test-report-token",
            )
        return repo

    def report_args(self, repo: Path, **overrides) -> SimpleNamespace:
        base = dict(
            repo=str(repo),
            run_id="run-001",
            role_id="T001",
            attempt_id="T001-A001",
            event_id=None,
            task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
            report_token="test-report-token",
            report_credential_file=None,
            file=None,
            stdin=False,
            type="milestone",
            stage="implementing",
            summary="made progress",
            next="continue implementation",
            evidence_ref=["skills/herdr-dev-loop/scripts/hloop:1"],
            understood_goal=None,
            scope=None,
            acceptance=None,
            approach=None,
            risk=["none identified"],
            impact=None,
            attempted=None,
            option_text=None,
            recommendation=None,
            blocked_scope=None,
            artifact=None,
            head_sha=None,
            validation_result_ref=None,
            residual_risk=None,
            handoff=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def body_args(self, repo: Path, **overrides) -> SimpleNamespace:
        """`report_args` with every individual content flag cleared to None.

        Used for --file/--stdin tests, since the CLI rejects using both an
        input source and the individual compatibility flags at once.
        """

        base = self.report_args(
            repo,
            type=None,
            stage=None,
            summary=None,
            next=None,
            evidence_ref=None,
            understood_goal=None,
            scope=None,
            acceptance=None,
            approach=None,
            risk=None,
            impact=None,
            attempted=None,
            option_text=None,
            recommendation=None,
            blocked_scope=None,
            artifact=None,
            head_sha=None,
            validation_result_ref=None,
            residual_risk=None,
            handoff=None,
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_report_inbox_manager_sleep_next_and_ack_round_trip(self):
        event_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            errors = []
            runtime_dir = root / "runtime"
            socket_path = hloop_broker.derive_runtime_socket_path(
                hloop.LOOP_NAMESPACE, "run-001", runtime_directory=runtime_dir
            )

            def report_after_sleep_starts():
                try:
                    deadline = time.monotonic() + 2
                    while not socket_path.exists():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("manager sleep socket was not created")
                        time.sleep(0.005)
                    hloop.cmd_agent_report(
                        self.report_args(
                            repo,
                            event_id=event_id,
                            type="attention",
                            impact="Manager must inspect the fault-path report",
                            attempted=["persisted the report before signalling"],
                            option_text=["inspect the durable inbox event"],
                            recommendation="inspect and explicitly acknowledge the event",
                            blocked_scope=["manager sleep"],
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            reporter = threading.Thread(target=report_after_sleep_starts, daemon=True)
            reporter.start()
            sleep_output = io.StringIO()
            with mock.patch.dict(os.environ, {"HLOOP_RUNTIME_DIR": str(runtime_dir)}):
                with contextlib.redirect_stdout(sleep_output):
                    hloop.cmd_manager_sleep(
                        SimpleNamespace(
                            repo=str(repo), ttl_seconds=2, manager_session_id="sess", pane_id="pane"
                        )
                    )
            reporter.join(timeout=2)
            self.assertEqual(errors, [])
            self.assertIn("manager sleep returned: report", sleep_output.getvalue())
            self.assertIn(event_id, sleep_output.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_next(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

            hloop.cmd_inbox_ack(SimpleNamespace(repo=str(repo), event_id=event_id))
            with self.assertRaisesRegex(hloop.HLoopError, "duplicate"):
                hloop.cmd_inbox_ack(
                    SimpleNamespace(repo=str(repo), event_id=event_id)
                )

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_next(SimpleNamespace(repo=str(repo)))
            self.assertIn("no pending wakes", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_broker_status(SimpleNamespace(repo=str(repo)))
            self.assertIn('"events": 1', buffer.getvalue())

    def test_report_spools_when_broker_is_unavailable(self):
        event_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            with mock.patch.object(
                hloop,
                "_open_broker_store",
                side_effect=hloop_broker.BrokerUnavailableError(
                    "simulated broker outage"
                ),
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    hloop.cmd_agent_report(self.report_args(repo, event_id=event_id))
                self.assertIn(f"spooled {event_id}", buffer.getvalue())

            spool_dir = hloop.broker_spool_dir(repo)
            spooled_files = list(spool_dir.glob("*"))
            self.assertTrue(spooled_files, "expected a spooled report file")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_broker_recover(SimpleNamespace(repo=str(repo)))
            self.assertIn("replayed 1 spooled report", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue())

    def test_agent_report_accepts_a_schema_validated_json_body_via_file_or_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            body = {
                "type": "ack",
                "stage": "planning",
                "summary": "契約を確認した",
                "next": "material editを開始する",
                "evidence_refs": ["skills/herdr-dev-loop/scripts/hloop:1"],
                "understood_goal": "対象機能を契約どおり実装する",
                "scope": ["skills/herdr-dev-loop/scripts/hloop"],
                "acceptance": ["対象テストが通る"],
                "approach": "既存の境界を保った最小変更",
            }
            body_path = root / "report.json"
            body_path.write_text(json.dumps(body), encoding="utf-8")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(self.body_args(repo, file=str(body_path)))
            first_event_id = buffer.getvalue().split()[1]

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                stored = store.get_event(transaction, event_id=first_event_id)
            self.assertEqual(stored["type"], "ack")
            self.assertEqual(stored["summary"], body["summary"])

            stdin_body = dict(body, summary="stdinから受理した契約確認")
            with mock.patch.object(
                hloop.sys, "stdin", io.StringIO(json.dumps(stdin_body))
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    hloop.cmd_agent_report(self.body_args(repo, stdin=True))
            second_event_id = buffer.getvalue().split()[1]
            self.assertNotEqual(first_event_id, second_event_id)

    def test_agent_report_rejects_combining_json_body_with_individual_flags_or_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            body_path = root / "report.json"
            body_path.write_text(
                json.dumps(
                    {
                        "type": "milestone",
                        "stage": "implementing",
                        "summary": "progress",
                        "next": "continue",
                        "evidence_refs": ["hloop:1"],
                        "risks": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(hloop.HLoopError, "use only one of"):
                hloop.cmd_agent_report(
                    self.body_args(repo, file=str(body_path), stage="implementing")
                )

            unknown_field_path = root / "unknown.json"
            unknown_field_path.write_text(
                json.dumps({"type": "milestone", "not_a_field": True}), encoding="utf-8"
            )
            with self.assertRaisesRegex(hloop.HLoopError, "unknown fields"):
                hloop.cmd_agent_report(self.body_args(repo, file=str(unknown_field_path)))

    def test_two_successful_identical_agent_reports_receive_distinct_event_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(self.report_args(repo))
            first_event_id = buffer.getvalue().split()[1]

            outbox_dir = hloop.broker_transport_root(repo) / "outbox"
            outbox_files = [
                path for path in outbox_dir.glob("*.json") if not path.name.startswith(".")
            ]
            self.assertEqual(len(outbox_files), 1)
            self.assertEqual(stat.S_IMODE(outbox_files[0].stat().st_mode), 0o600)
            first_outbox = json.loads(outbox_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["status"] for entry in first_outbox["entries"]],
                ["confirmed"],
            )

            # The first successful broker delivery confirmed its outbox
            # envelope. A later implicit invocation with the same semantic
            # content is a distinct report, not a crash retry.
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(self.report_args(repo))
            second_event_id = buffer.getvalue().split()[1]
            self.assertNotEqual(first_event_id, second_event_id)
            second_outbox = json.loads(outbox_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["status"] for entry in second_outbox["entries"]],
                ["confirmed", "confirmed"],
            )

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                events = store.events(transaction)
                inbox_rows = store.inbox(transaction)
            self.assertEqual(
                [event["event_id"] for event in events],
                [first_event_id, second_event_id],
            )
            self.assertEqual(len(inbox_rows), 2)

    def test_agent_report_crash_after_broker_commit_reuses_pending_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            first_created_at = "2026-07-15T00:00:00+00:00"
            retry_created_at = "2026-07-15T00:05:00+00:00"

            real_confirm = hloop_broker.confirm_role_outbox_delivery
            confirmation_calls = 0

            def crash_before_first_confirmation(*args, **kwargs):
                nonlocal confirmation_calls
                confirmation_calls += 1
                if confirmation_calls == 1:
                    raise OSError("simulated crash before delivery confirmation")
                return real_confirm(*args, **kwargs)

            with mock.patch.object(
                hloop, "now_iso", side_effect=[first_created_at, retry_created_at]
            ), mock.patch.object(
                hloop_broker,
                "confirm_role_outbox_delivery",
                side_effect=crash_before_first_confirmation,
            ):
                with self.assertRaisesRegex(
                    hloop.HLoopError, "cannot confirm broker report delivery"
                ):
                    hloop.cmd_agent_report(
                        self.report_args(
                            repo,
                            type="attention",
                            impact="inspect",
                            attempted=["checked"],
                            option_text=["continue"],
                            recommendation="continue",
                            blocked_scope=["broker"],
                        )
                    )

                store = hloop._open_broker_store(repo)
                with store.transaction() as transaction:
                    first_stored_events = store.events(transaction)
                self.assertEqual(len(first_stored_events), 1)
                first_event_id = first_stored_events[0]["event_id"]
                outbox_path = hloop.role_report_outbox_path(
                    repo,
                    run_id="run-001",
                    role_id="T001",
                    attempt_id="T001-A001",
                )
                pending_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
                self.assertEqual(pending_outbox["entries"][-1]["status"], "pending")

                second_output = io.StringIO()
                with contextlib.redirect_stdout(second_output):
                    hloop.cmd_agent_report(
                        self.report_args(
                            repo,
                            type="attention",
                            impact="inspect",
                            attempted=["checked"],
                            option_text=["continue"],
                            recommendation="continue",
                            blocked_scope=["broker"],
                        )
                    )

            second_event_id = second_output.getvalue().split()[1]
            self.assertEqual(second_event_id, first_event_id)

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                stored_events = store.events(transaction)
                inbox_rows = store.inbox(transaction)
            self.assertEqual(len(stored_events), 1)
            self.assertEqual(len(inbox_rows), 1)
            self.assertEqual(stored_events[0]["created_at"], first_created_at)
            confirmed_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
            self.assertEqual(confirmed_outbox["entries"][-1]["status"], "confirmed")

    def test_explicit_event_id_retry_reuses_confirmed_full_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            event_id = str(uuid.uuid4())
            first_created_at = "2026-07-15T00:00:00+00:00"
            retry_created_at = "2026-07-15T00:05:00+00:00"

            with mock.patch.object(
                hloop, "now_iso", side_effect=[first_created_at, retry_created_at]
            ):
                first_output = io.StringIO()
                with contextlib.redirect_stdout(first_output):
                    hloop.cmd_agent_report(
                        self.report_args(repo, event_id=event_id)
                    )
                second_output = io.StringIO()
                with contextlib.redirect_stdout(second_output):
                    hloop.cmd_agent_report(
                        self.report_args(repo, event_id=event_id)
                    )

            self.assertIn(f"accepted {event_id}", first_output.getvalue())
            self.assertIn(f"accepted {event_id}", second_output.getvalue())
            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                stored_events = store.events(transaction)
                inbox_rows = store.inbox(transaction)
            self.assertEqual(len(stored_events), 1)
            self.assertEqual(len(inbox_rows), 1)
            self.assertEqual(stored_events[0]["created_at"], first_created_at)

    def test_two_successful_identical_spooled_reports_receive_distinct_event_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            outputs = []
            with mock.patch.object(
                hloop,
                "_open_broker_store",
                side_effect=hloop_broker.BrokerUnavailableError(
                    "simulated broker outage"
                ),
            ):
                for _ in range(2):
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        hloop.cmd_agent_report(self.report_args(repo))
                    outputs.append(buffer.getvalue())

            first_event_id = outputs[0].split()[1]
            second_event_id = outputs[1].split()[1]
            self.assertNotEqual(first_event_id, second_event_id)
            self.assertEqual(
                len(list(hloop.broker_spool_dir(repo).glob("*.json"))), 2
            )

    def test_agent_report_idempotency_conflict_fails_closed_without_spooling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            event_id = str(uuid.uuid4())

            hloop.cmd_agent_report(
                self.report_args(repo, event_id=event_id, summary="first report")
            )
            with mock.patch.object(hloop, "_open_broker_store") as open_store:
                with self.assertRaisesRegex(
                    hloop.HLoopError,
                    "cannot persist report outbox.*different semantic content",
                ):
                    hloop.cmd_agent_report(
                        self.report_args(
                            repo,
                            event_id=event_id,
                            summary="different report",
                        )
                    )
                open_store.assert_not_called()

            spool_dir = hloop.broker_spool_dir(repo)
            self.assertEqual(list(spool_dir.glob("*.json")), [])

    def test_main_reports_permanent_broker_errors_without_traceback_or_spooling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            digest = hashlib.sha256(b"T001").hexdigest()
            argv = [
                "--repo",
                str(repo),
                "--namespace",
                hloop.LOOP_NAMESPACE,
                "agent",
                "report",
                "--role-id",
                "T001",
                "--attempt-id",
                "T001-A001",
                "--run-id",
                "run-001",
                "--task-contract-digest",
                digest,
                "--report-token",
                "test-report-token",
                "--type",
                "milestone",
                "--stage",
                "implementing",
                "--summary",
                "made progress",
                "--next",
                "continue implementation",
                "--evidence-ref",
                "skills/herdr-dev-loop/scripts/hloop:1",
                "--risk",
                "none identified",
            ]
            permanent_errors = [
                hloop_broker.IdempotencyConflict("different digest"),
                hloop_broker.ReportAuthenticationError("identity rejected"),
                hloop_broker.BrokerIntegrityError("invalid broker schema"),
                hloop_broker.BrokerStorageError("unsupported storage semantics"),
            ]

            previous_trusted_git = hloop.TRUSTED_GIT_PATH
            try:
                for error in permanent_errors:
                    with self.subTest(error=type(error).__name__), mock.patch.object(
                        hloop, "_open_broker_store", side_effect=error
                    ):
                        stderr = io.StringIO()
                        with contextlib.redirect_stderr(stderr):
                            code = hloop.main(argv)
                        self.assertEqual(code, 2)
                        self.assertIn("hloop: report rejected by broker", stderr.getvalue())
                        self.assertNotIn("Traceback", stderr.getvalue())
            finally:
                hloop.TRUSTED_GIT_PATH = previous_trusted_git

            self.assertEqual(
                list(hloop.broker_spool_dir(repo).glob("*.json")), []
            )

    def test_agent_report_does_not_spool_an_unclassified_broker_os_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            args = self.report_args(repo)

            with mock.patch.object(
                hloop, "_open_broker_store", side_effect=OSError("permission denied")
            ):
                with self.assertRaisesRegex(
                    hloop.HLoopError, "cannot access report broker: permission denied"
                ):
                    hloop.cmd_agent_report(args)

            self.assertEqual(
                list(hloop.broker_spool_dir(repo).glob("*.json")), []
            )

    def test_agent_report_spools_when_sqlite_database_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            database_path = hloop.broker_root(repo) / "broker.sqlite3"
            database_path.write_bytes(b"not a sqlite database")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(self.report_args(repo))

            self.assertIn("spooled", buffer.getvalue())
            self.assertEqual(len(list(hloop.broker_spool_dir(repo).glob("*.json"))), 1)

    def test_agent_report_reuses_a_pre_persisted_outbox_event_id_after_a_simulated_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            report_ns = self.report_args(repo, type="attention")

            # Simulate a client that persisted the outbox entry (the mandatory
            # pre-broker-send step) and then crashed before ever reaching the
            # broker.
            outbox_path = hloop.role_report_outbox_path(
                repo, run_id="run-001", role_id="T001", attempt_id="T001-A001"
            )
            content = {
                "stage": report_ns.stage,
                "summary": report_ns.summary,
                "next": report_ns.next,
                "evidence_refs": report_ns.evidence_ref,
                "impact": report_ns.summary,
                "attempted": ["investigated"],
                "options": ["proceed"],
                "recommendation": "proceed",
                "blocked_scope": ["material edits"],
            }
            raw_report = dict(
                run_id="run-001",
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
                type="attention",
                needs_manager=True,
                created_at=hloop.now_iso(),
                **content,
            )
            normalized = hloop.hloop_events.validate_report(raw_report)
            digest = hloop.hloop_events.payload_digest(normalized)
            pre_persisted_event_id = hloop_broker.role_outbox_event_id(
                outbox_path, payload_digest=digest
            )

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        type="attention",
                        impact=report_ns.summary,
                        attempted=["investigated"],
                        option_text=["proceed"],
                        recommendation="proceed",
                        blocked_scope=["material edits"],
                    )
                )
            self.assertIn(f"accepted {pre_persisted_event_id}", buffer.getvalue())

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                events = store.events(transaction)
            self.assertEqual(len(events), 1)

    def test_manager_next_and_broker_recover_quarantine_poison_entries_and_replay_valid_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            spool_dir = hloop.broker_spool_dir(repo)

            with mock.patch.object(
                hloop,
                "_open_broker_store",
                side_effect=hloop_broker.BrokerUnavailableError(
                    "simulated broker outage"
                ),
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    hloop.cmd_agent_report(self.report_args(repo, event_id=str(uuid.uuid4())))
                self.assertIn("spooled", buffer.getvalue())

            poison = spool_dir / "ffffffff-ffff-4fff-8fff-ffffffffffff.json"
            poison.write_text(json.dumps({"not": "an event"}), encoding="utf-8")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_next(SimpleNamespace(repo=str(repo)))
            self.assertIn("replayed 1 spooled report", buffer.getvalue())
            self.assertEqual(list(spool_dir.glob("*.json")), [])
            quarantine_dir = spool_dir / "quarantine"
            self.assertEqual(
                [
                    path.name
                    for path in quarantine_dir.glob("*.json")
                    if not path.name.endswith(".audit.json")
                ],
                ["ffffffff-ffff-4fff-8fff-ffffffffffff.json"],
            )

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_broker_status(SimpleNamespace(repo=str(repo)))
            status = json.loads(buffer.getvalue())
            self.assertEqual(status["spool_quarantined"], 1)

            # A second recovery pass over an already-drained, already-quarantined
            # spool must not fail or duplicate the quarantine entry.
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_broker_recover(SimpleNamespace(repo=str(repo)))
            self.assertIn("replayed 0 spooled report", buffer.getvalue())
            self.assertIn("quarantined 1 poison entrie", buffer.getvalue())


class BrokerTransportAndAuthenticationTests(unittest.TestCase):
    """Exercises detached-worktree broker sharing, inbox show, report auth, hooks, drain."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-broker-transport-and-auth")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "run_id": "run-001",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        store = hloop._open_broker_store(repo)
        with store.transaction() as transaction:
            store.register_active_role(
                transaction,
                run_id="run-001",
                role_id="T001",
                attempt_id="T001-A001",
                task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
                token="test-report-token",
            )
        return repo

    def report_args(self, repo: Path, **overrides) -> SimpleNamespace:
        base = dict(
            repo=str(repo),
            run_id="run-001",
            role_id="T001",
            attempt_id="T001-A001",
            event_id=None,
            task_contract_digest=hashlib.sha256(b"T001").hexdigest(),
            report_token="test-report-token",
            type="milestone",
            stage="implementing",
            summary="made progress",
            next="continue implementation",
            evidence_ref=["skills/herdr-dev-loop/scripts/hloop:1"],
            understood_goal=None,
            scope=None,
            acceptance=None,
            approach=None,
            risk=["none identified"],
            impact=None,
            attempted=None,
            option_text=None,
            recommendation=None,
            blocked_scope=None,
            artifact=None,
            head_sha=None,
            validation_result_ref=None,
            residual_risk=None,
            handoff=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_broker_transport_is_shared_across_worktrees_without_copying_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            worktree = root / "worker-worktree"
            subprocess.run(
                ["git", "worktree", "add", "-b", "T001-A001", str(worktree), "main"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(hloop.broker_root(repo), hloop.broker_root(worktree))
            self.assertFalse(
                str(hloop.broker_root(worktree)).startswith(str(hloop.loop_path(worktree))),
                "broker storage must live outside the per-worktree loop snapshot",
            )

            event_id = str(uuid.uuid4())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(
                    self.report_args(worktree, role_id="T001", attempt_id="T001-A001", event_id=event_id)
                )
            self.assertIn(f"accepted {event_id}", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_list(SimpleNamespace(repo=str(repo)))
            self.assertIn(event_id, buffer.getvalue(), "Manager's own repo path did not see the Worker's report")

    def test_inbox_show_matches_the_fixed_wake_message_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            event_id = str(uuid.uuid4())
            hloop.cmd_agent_report(self.report_args(repo, event_id=event_id))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_inbox_show(SimpleNamespace(repo=str(repo), event_id=event_id))
            shown = json.loads(buffer.getvalue())
            self.assertEqual(shown["event_id"], event_id)
            self.assertEqual(shown["role_id"], "T001")

            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_inbox_show(SimpleNamespace(repo=str(repo), event_id=str(uuid.uuid4())))

    def test_report_token_authentication_accepts_matching_and_rejects_mismatched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            store = hloop._open_broker_store(repo)
            with store.transaction() as txn:
                store.register_active_role(
                    txn,
                    run_id="run-001",
                    role_id="T001",
                    attempt_id="T001-A001",
                    task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                    token="s3cr3t-token",
                )

            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        attempt_id="T001-A001",
                        task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                        report_token="wrong-token",
                    )
                )

            event_id = str(uuid.uuid4())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        event_id=event_id,
                        attempt_id="T001-A001",
                        task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                        report_token="s3cr3t-token",
                    )
                )
            self.assertIn(f"accepted {event_id}", buffer.getvalue())

            with store.transaction() as txn:
                store.revoke_active_role(txn, run_id="run-001", role_id="T001")
            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        attempt_id="T001-A001",
                        task_contract_digest=hashlib.sha256(b"contract").hexdigest(),
                        report_token="s3cr3t-token",
                    )
                )

    def test_manager_sleep_surfaces_already_pending_report_instead_of_losing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            event_id = str(uuid.uuid4())
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    event_id=event_id,
                    type="attention",
                    impact="Manager must inspect the pending report",
                    attempted=["persisted the report"],
                    option_text=["inspect the durable inbox event"],
                    recommendation="inspect and explicitly acknowledge the event",
                    blocked_scope=["manager sleep"],
                )
            )

            buffer = io.StringIO()
            with mock.patch.dict(
                os.environ, {"HLOOP_RUNTIME_DIR": str(root / "runtime")}
            ), contextlib.redirect_stdout(buffer):
                hloop.cmd_manager_sleep(
                    SimpleNamespace(
                        repo=str(repo), ttl_seconds=3600, manager_session_id="sess", pane_id="pane"
                    )
                )
            output = buffer.getvalue()
            self.assertIn("manager sleep returned: report", output)
            self.assertIn(event_id, output)

    def test_manager_sleep_merges_only_sleep_fields_into_latest_locked_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            def concurrent_update(**_kwargs):
                latest = hloop.load_state(repo)
                latest["phase"] = "reviewing"
                latest["tasks"] = {
                    "T999": {"status": "merged", "head_sha": "a" * 40}
                }
                latest["decisions"] = {
                    "D999": {"status": "accepted", "selected_option": "opt_1"}
                }
                latest["last_harvested_task"] = "T999"
                hloop.save_state(repo, latest)
                return hloop.hloop_supervisor.ManagerSleepResult(
                    reason="timeout",
                    lease_generation=7,
                    drained_reports=0,
                )

            with mock.patch.object(
                hloop.hloop_supervisor.ManagerSleepSupervisor,
                "sleep",
                side_effect=concurrent_update,
            ):
                hloop.cmd_manager_sleep(
                    SimpleNamespace(
                        repo=str(repo),
                        ttl_seconds=1,
                        manager_session_id="sess",
                        pane_id="pane",
                    )
                )

            current = hloop.load_state(repo)
            self.assertEqual(current["phase"], "reviewing")
            self.assertEqual(current["tasks"]["T999"]["status"], "merged")
            self.assertEqual(current["decisions"]["D999"]["status"], "accepted")
            self.assertEqual(current["last_harvested_task"], "T999")
            self.assertEqual(current["broker_lease"]["generation"], 7)
            self.assertEqual(current["last_manager_sleep"]["reason"], "timeout")

    def test_hooks_install_status_uninstall_round_trip_preserves_user_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            settings_path = repo / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-hook"}]}]}}),
                encoding="utf-8",
            )

            hloop.cmd_hooks_install(
                SimpleNamespace(
                    repo=str(repo),
                    provider="claude",
                    settings_path=None,
                    codex_continuation_capability="unknown",
                )
            )
            installed = json.loads(settings_path.read_text(encoding="utf-8"))
            stop_handlers = [h for group in installed["hooks"]["Stop"] for h in group["hooks"]]
            self.assertEqual(len(stop_handlers), 2, "existing user hook must be preserved")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_hooks_status(
                    SimpleNamespace(repo=str(repo), provider="claude", settings_path=None)
                )
            status = json.loads(buffer.getvalue())
            self.assertEqual(status["owned_stop_hooks"], 1)

            hloop.cmd_hooks_uninstall(
                SimpleNamespace(repo=str(repo), provider="claude", settings_path=None)
            )
            after = json.loads(settings_path.read_text(encoding="utf-8"))
            remaining = [h for group in after["hooks"]["Stop"] for h in group["hooks"]]
            self.assertEqual(len(remaining), 1)
            self.assertIn("echo user-hook", remaining[0]["command"])

    def test_hooks_guard_returns_fixed_context_only_when_active_roles_lack_a_valid_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["tasks"] = {"T001": {"status": "running"}}
            hloop.save_state(repo, state)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_hooks_guard(
                    SimpleNamespace(repo=str(repo), provider="claude", hloop_hook_owner="x")
                )
            response = json.loads(buffer.getvalue())
            self.assertIn("additionalContext", response["hookSpecificOutput"])

            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                store.register_wake_lease(
                    transaction,
                    run_id="run-001",
                    manager_session_id="sess",
                    pane_id="pane",
                    expires_at="2999-01-01T00:00:00+00:00",
                )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_hooks_guard(
                    SimpleNamespace(repo=str(repo), provider="claude", hloop_hook_owner="x")
                )
            self.assertEqual(json.loads(buffer.getvalue()), {})

    def test_message_drain_resends_pending_message_and_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "pane_id": "pane-1",
                "agent_provider": "codex",
                "pending_manager_messages": [],
            }
            pending_rel = hloop.write_pending_manager_message(
                repo,
                role="Worker",
                agent_id="T001",
                pane_id="pane-1",
                source="argv",
                message="please retry",
                error="simulated send failure",
            )
            task_state["pending_manager_messages"].append(
                {"at": hloop.now_iso(), "source": "argv", "pending_path": pending_rel.as_posix(), "error": "boom"}
            )
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)

            sent = []

            def capture_send(provider, pane_id, message, *unused):
                sent.append((pane_id, message))

            with mock.patch.object(hloop, "send_agent_tui_message", side_effect=capture_send), mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), contextlib.redirect_stdout(io.StringIO()) as buffer:
                hloop.cmd_message_drain(
                    SimpleNamespace(
                        repo=str(repo),
                        timeout_ms=1,
                        input_settle_ms=0,
                        submit_verify_ms=1,
                        submit_attempts=1,
                    )
                )
            self.assertIn("drained 1 pending message(s); 0 still pending", buffer.getvalue())
            self.assertEqual(len(sent), 1)
            self.assertIn("please retry", sent[0][1])
            self.assertEqual(task_state["pending_manager_messages"], [])
            self.assertFalse((repo / pending_rel).exists())

    def test_message_drain_only_retries_undelivered_and_never_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "pane_id": "pane-1",
                "agent_provider": "codex",
                "pending_manager_messages": [],
            }
            undelivered_rel = hloop.write_pending_manager_message(
                repo,
                role="Worker",
                agent_id="T001",
                pane_id="pane-1",
                source="argv",
                message="please retry me",
                error="simulated send failure",
            )
            unknown_rel = hloop.write_pending_manager_message(
                repo,
                role="Worker",
                agent_id="T001",
                pane_id="pane-1",
                source="argv",
                message="do not auto-resend me",
                error="simulated ambiguous delivery",
            )
            task_state["pending_manager_messages"] = [
                {
                    "at": hloop.now_iso(),
                    "source": "argv",
                    "pending_path": undelivered_rel.as_posix(),
                    "error": "boom",
                    "status": "undelivered",
                },
                {
                    "at": hloop.now_iso(),
                    "source": "argv",
                    "pending_path": unknown_rel.as_posix(),
                    "error": "ambiguous",
                    "status": "unknown",
                },
            ]
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)

            sent = []

            def capture_send(provider, pane_id, message, *unused):
                sent.append((pane_id, message))

            with mock.patch.object(hloop, "send_agent_tui_message", side_effect=capture_send), mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), contextlib.redirect_stdout(io.StringIO()) as buffer:
                hloop.cmd_message_drain(
                    SimpleNamespace(
                        repo=str(repo),
                        timeout_ms=1,
                        input_settle_ms=0,
                        submit_verify_ms=1,
                        submit_attempts=1,
                    )
                )
            self.assertIn("drained 1 pending message(s); 1 still pending", buffer.getvalue())
            self.assertEqual(len(sent), 1)
            self.assertIn("please retry me", sent[0][1])
            self.assertEqual(len(task_state["pending_manager_messages"]), 1)
            self.assertEqual(task_state["pending_manager_messages"][0]["status"], "unknown")
            self.assertFalse((repo / undelivered_rel).exists())
            self.assertTrue((repo / unknown_rel).exists())

    def test_message_drain_preserves_undelivered_entry_when_payload_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "pane_id": "pane-1",
                "agent_provider": "codex",
                "pending_manager_messages": [
                    {
                        "at": hloop.now_iso(),
                        "source": "argv",
                        "pending_path": ".ai/missing-message.md",
                        "error": "original delivery failed",
                        "status": "undelivered",
                    }
                ],
            }
            state["tasks"] = {"T001": task_state}

            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(hloop, "send_agent_tui_message") as send, contextlib.redirect_stdout(
                io.StringIO()
            ) as buffer:
                hloop.cmd_message_drain(
                    SimpleNamespace(
                        repo=str(repo),
                        timeout_ms=1,
                        input_settle_ms=0,
                        submit_verify_ms=1,
                        submit_attempts=1,
                    )
                )

            send.assert_not_called()
            self.assertIn("0 pending message(s); 1 still pending", buffer.getvalue())
            self.assertEqual(len(task_state["pending_manager_messages"]), 1)
            self.assertIn("file is missing", task_state["pending_manager_messages"][0]["error"])

    def test_supersede_marks_every_non_applied_message_terminal(self):
        agent_state = {
            "manager_messages": [
                {"message_id": "delivered", "delivery_status": "delivered"},
                {"message_id": "acked", "delivery_status": "acknowledged"},
                {"message_id": "applied", "delivery_status": "applied"},
            ]
        }

        hloop.supersede_pending_manager_messages(Path("/unused"), agent_state)

        statuses = {
            entry["message_id"]: entry["delivery_status"]
            for entry in agent_state["manager_messages"]
        }
        self.assertEqual(statuses["delivered"], "superseded")
        self.assertEqual(statuses["acked"], "superseded")
        self.assertEqual(statuses["applied"], "applied")

    def test_record_manager_message_carries_digest_and_valid_delivery_status(self):
        target = {}
        hloop.record_manager_message(target, "argv", "hello world")
        entry = target["manager_messages"][-1]
        self.assertEqual(entry["delivery_status"], "delivered")
        self.assertEqual(entry["digest"], hloop.message_digest("hello world"))
        with self.assertRaisesRegex(hloop.HLoopError, "unsupported message delivery status"):
            hloop.record_manager_message(target, "argv", "hello", delivery_status="bogus")

    def test_message_unknown_ack_and_applied_resolution_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "attempt_id": "T001-A001",
                "unknown_manager_messages": [],
            }
            _, envelope = hloop.manager_message_envelope(
                state, "T001", task_state, "apply the bounded fix"
            )
            entry = hloop.record_manager_message(
                task_state,
                "argv",
                envelope,
                delivery_status="unknown",
                delivery_error="visual confirmation timed out",
            )
            task_state["unknown_manager_messages"].append(
                {
                    "message_id": entry["message_id"],
                    "error": "visual confirmation timed out",
                }
            )
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)

            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_message_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="T001",
                        message_id=entry["message_id"],
                        status="acknowledged",
                        result=None,
                        error=None,
                    )
                )
                hloop.cmd_message_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="T001",
                        message_id=entry["message_id"],
                        status="applied",
                        result="fix applied and tests queued",
                        error=None,
                    )
                )
            reloaded = hloop.load_state(repo)["tasks"]["T001"]
            resolved = hloop.manager_message_by_id(reloaded, entry["message_id"])
            self.assertEqual(resolved["delivery_status"], "applied")
            self.assertEqual(resolved["result"], "fix applied and tests queued")
            self.assertEqual(
                [item["to"] for item in reloaded["manager_message_events"]],
                ["acknowledged", "applied"],
            )
            self.assertEqual(reloaded["unknown_manager_messages"], [])

    def test_every_long_running_role_identity_and_prompt_is_authenticated(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            store = hloop._open_broker_store(repo)
            identities = {
                "T009": "T009-A001",
                "R009": "R009-A001",
                "G009": "G009-A001",
                "A009/P1": "A009-P1-A001",
            }
            for role_id, attempt_id in identities.items():
                digest = hashlib.sha256(role_id.encode()).hexdigest()
                credential_file, _ = hloop.register_role_report_identity_and_ack_floor(
                    repo,
                    state,
                    role_id=role_id,
                    attempt_id=attempt_id,
                    task_contract_digest=digest,
                )
                credential_payload = json.loads(credential_file.read_text(encoding="utf-8"))
                token = credential_payload["token"]
                self.assertEqual(stat.S_IMODE(credential_file.stat().st_mode), 0o600)
                self.assertFalse(credential_file.is_relative_to(repo / hloop.LOOP_DIR))
                with store.transaction() as transaction:
                    self.assertTrue(
                        store.authenticate_role_report(
                            transaction,
                            run_id="run-001",
                            role_id=role_id,
                            attempt_id=attempt_id,
                            task_contract_digest=digest,
                            token=token,
                        )
                    )
                contract = hloop.report_contract_text(
                    role_id,
                    attempt_id,
                    state,
                    report_credential_file=str(credential_file),
                    task_contract_digest=digest,
                )
                self.assertIn(f"--role-id {role_id}", contract)
                self.assertIn(f"--attempt-id {attempt_id}", contract)
                self.assertIn("--report-credential-file", contract)
                self.assertNotIn(token, contract)
                self.assertIn("stop before material work", contract)

            with store.transaction() as transaction:
                self.assertFalse(
                    store.authenticate_role_report(
                        transaction,
                        run_id="run-001",
                        role_id="R999",
                        attempt_id="R999-A001",
                        task_contract_digest="0" * 64,
                        token="unknown",
                    )
                )

    def test_report_credential_permissions_and_diagnostics_fail_closed_without_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            digest = hashlib.sha256(b"T010").hexdigest()
            credential_file, _ = hloop.register_role_report_identity_and_ack_floor(
                repo,
                state,
                role_id="T010",
                attempt_id="T010-A001",
                task_contract_digest=digest,
            )
            secret = json.loads(credential_file.read_text(encoding="utf-8"))["token"]
            with contextlib.redirect_stdout(io.StringIO()) as output:
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        role_id="T010",
                        attempt_id="T010-A001",
                        task_contract_digest=digest,
                        report_token=None,
                        report_credential_file=str(credential_file),
                    )
                )
            self.assertIn("accepted", output.getvalue())
            self.assertNotIn(secret, output.getvalue())
            credential_file.chmod(0o644)

            with self.assertRaises(hloop.HLoopError) as raised:
                hloop.read_role_report_credential(
                    repo,
                    str(credential_file),
                    run_id="run-001",
                    role_id="T010",
                    attempt_id="T010-A001",
                )

            self.assertIn("mode 0600", str(raised.exception))
            self.assertNotIn(secret, str(raised.exception))

    def test_codex_transcript_visibility_is_delivery_evidence(self):
        message = "Manager message id: 00000000-0000-0000-0000-000000000001\napply fix"
        typed_snapshot = f"input> {message}"
        submitted_snapshot = f"user> {message}\nstatus: queued"
        pane = {"agent": "codex", "agent_status": "idle", "session_id": "session-1"}
        with mock.patch.object(hloop, "pane_info", return_value=pane), mock.patch.object(
            hloop, "pane_text", return_value=submitted_snapshot
        ):
            self.assertTrue(
                hloop.manager_message_submitted(
                    "codex",
                    "pane-1",
                    message,
                    typed_snapshot,
                    "session-1",
                    0,
                )
            )

    def test_visible_message_transport_failure_is_unknown_and_never_auto_drained(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "attempt_id": "T001-A001",
                "pane_id": "pane-1",
                "agent_provider": "codex",
            }
            state["tasks"] = {"T001": task_state}
            args = SimpleNamespace(
                contract_changing=False,
                timeout_ms=1,
                input_settle_ms=0,
                submit_verify_ms=1,
                submit_attempts=1,
            )
            with mock.patch.object(
                hloop,
                "send_agent_tui_message",
                side_effect=hloop.ManagerMessageDeliveryUnknown("visible but ambiguous"),
            ), contextlib.redirect_stderr(io.StringIO()):
                code = hloop.send_manager_message_and_record(
                    repo,
                    state,
                    task_state,
                    role="Worker",
                    agent_id="T001",
                    pane_id="pane-1",
                    message="apply the fix once",
                    source="argv",
                    args=args,
                )

            self.assertEqual(code, 3)
            self.assertEqual(task_state["manager_messages"][-1]["delivery_status"], "unknown")
            self.assertEqual(task_state.get("pending_manager_messages", []), [])
            self.assertEqual(len(task_state["unknown_manager_messages"]), 1)

    def test_successful_send_text_with_unconfirmed_visibility_is_unknown(self):
        with mock.patch.object(hloop, "check_herdr_env"), mock.patch.object(
            hloop,
            "wait_agent_tui_ready",
            return_value=({"agent": "codex", "session_id": "session-1"}, ""),
        ), mock.patch.object(
            hloop, "run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")
        ), mock.patch.object(
            hloop,
            "wait_manager_message_visible",
            side_effect=hloop.HLoopError("visibility timed out"),
        ):
            with self.assertRaises(hloop.ManagerMessageDeliveryUnknown):
                hloop.send_agent_tui_message(
                    "codex", "pane-1", "apply once", 1, 0, 1, 1
                )

    def test_semantic_ack_barrier_blocks_worker_finalize_done_until_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "result_status": "in_progress",
                "attempt_id": "T001-A001",
            }
            hloop.arm_semantic_ack_barrier(task_state, message_id="msg-1", digest="deadbeef")
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)
            hloop.write_text(
                hloop.task_file(repo, "T001"),
                hloop.frontmatter({"id": "T001", "run_id": state["run_id"]}) + "\n\n# Task T001\n",
            )
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    type="ack",
                    stage="planning",
                    summary="contract understood",
                    next="wait for approval",
                    risk=[],
                    understood_goal="complete T001",
                    scope=["tracked.txt"],
                    acceptance=["validation passes"],
                    approach="smallest safe change",
                )
            )

            self.assertTrue(hloop.semantic_ack_barrier_blocking(task_state))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                    hloop.cmd_worker_finalize(
                        SimpleNamespace(
                            repo=str(repo),
                            task_id="T001",
                            status="done",
                            validation_command=[],
                            validation_result=[],
                            blocking_question=[],
                        )
                    )

                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo), agent_id="T001", decision="approve", reason="reviewed the change"
                    )
                )
            reloaded_task_state = hloop.load_state(repo)["tasks"]["T001"]
            self.assertEqual(reloaded_task_state["semantic_ack_barrier"]["status"], "approved")
            self.assertEqual(hloop.semantic_ack_barrier_blocking(reloaded_task_state), "")

    def test_running_task_update_rebinds_digest_and_requires_matching_reack(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            task_meta = {
                "id": "T001",
                "run_id": "run-001",
                "kind": "fix",
                "status": "running",
                "branch": "main",
                "base_ref": "main",
                "base_sha": head,
                "write_allow": ["tracked.txt"],
                "write_deny": [],
                "acceptance": ["original contract"],
            }
            hloop.write_text(
                hloop.task_file(repo, "T001"),
                hloop.frontmatter(task_meta) + "\n\n# Task T001\n",
            )
            original_digest = hashlib.sha256(hloop.task_file(repo, "T001").read_bytes()).hexdigest()
            state = hloop.load_state(repo)
            state["persistence"] = "local-only"
            state["tasks"] = {
                "T001": {
                    **task_meta,
                    "worktree": str(repo),
                    "attempt_id": "T001-A001",
                    "active_attempt_id": "T001-A001",
                    "worker_base_sha": head,
                    "task_contract_digest": original_digest,
                }
            }
            hloop.save_state(repo, state)
            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                store.register_active_role(
                    transaction,
                    run_id="run-001",
                    role_id="T001",
                    attempt_id="T001-A001",
                    task_contract_digest=original_digest,
                    token="test-report-token",
                )
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    type="ack",
                    stage="planning",
                    summary="original contract understood",
                    next="wait",
                    task_contract_digest=original_digest,
                    understood_goal="complete T001",
                    scope=["tracked.txt"],
                    acceptance=["original contract"],
                    approach="bounded change",
                )
            )
            update_args = SimpleNamespace(
                repo=str(repo),
                task_id="T001",
                add_write_allow=None,
                remove_write_allow=None,
                add_write_deny=None,
                remove_write_deny=None,
                add_acceptance=["updated contract"],
                set_acceptance=None,
                priority=None,
                validation_minimum=None,
                worker_protocol=None,
                worker_qa_profile=None,
                worker_agent_provider=None,
                worker_agent_model=None,
            )
            with mock.patch.object(hloop, "preflight_loop", return_value=state):
                hloop.cmd_task_update(update_args)

            updated_digest = hashlib.sha256(hloop.task_file(repo, "T001").read_bytes()).hexdigest()
            self.assertNotEqual(updated_digest, original_digest)
            reloaded = hloop.load_state(repo)["tasks"]["T001"]
            barrier = reloaded["semantic_ack_barrier"]
            self.assertEqual(barrier["kind"], "task-contract")
            self.assertEqual(barrier["digest"], updated_digest)
            self.assertEqual(barrier["status"], "awaiting_ack")
            self.assertGreaterEqual(barrier["required_reack_after_sequence"], 1)
            with store.transaction() as transaction:
                self.assertFalse(
                    store.authenticate_role_report(
                        transaction,
                        run_id="run-001",
                        role_id="T001",
                        attempt_id="T001-A001",
                        task_contract_digest=original_digest,
                        token="test-report-token",
                    )
                )
                self.assertTrue(
                    store.authenticate_role_report(
                        transaction,
                        run_id="run-001",
                        role_id="T001",
                        attempt_id="T001-A001",
                        task_contract_digest=updated_digest,
                        token="test-report-token",
                    )
                )

            with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                hloop.cmd_worker_finalize(
                    SimpleNamespace(
                        repo=str(repo),
                        task_id="T001",
                        status="done",
                        validation_command=["true"],
                        validation_result=["passed"],
                        validation_summary="pass",
                        blocking_question=[],
                        no_commit=True,
                    )
                )
            with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="T001",
                        decision="approve",
                        reason="stale ACK must not pass",
                    )
                )

            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    type="ack",
                    stage="planning",
                    summary="updated contract understood",
                    next="wait",
                    task_contract_digest=updated_digest,
                    understood_goal="complete updated T001",
                    scope=["tracked.txt"],
                    acceptance=["updated contract"],
                    approach="bounded updated change",
                )
            )
            hloop.cmd_agent_ack_resolve(
                SimpleNamespace(
                    repo=str(repo),
                    agent_id="T001",
                    decision="approve",
                    reason="updated digest reviewed",
                )
            )
            approved = hloop.load_state(repo)["tasks"]["T001"]["semantic_ack_barrier"]
            self.assertEqual(approved["status"], "approved")

            with mock.patch.object(hloop, "porcelain_paths", return_value=[]), mock.patch.object(hloop, "porcelain_paths_no_renames", return_value=[]):
                with self.assertRaisesRegex(hloop.HLoopError, "validation did not pass"):
                    hloop.cmd_worker_finalize(
                        SimpleNamespace(
                            repo=str(repo),
                            task_id="T001",
                            status="done",
                            validation_command=["false"],
                            validation_result=["failed"],
                            validation_summary="failed",
                            blocking_question=[],
                            no_commit=True,
                        )
                    )

    def test_contract_changing_message_rearms_an_already_resolved_barrier(self):
        agent_state = {}
        hloop.arm_semantic_ack_barrier(agent_state, message_id="msg-1", digest="aaa")
        hloop.resolve_semantic_ack_barrier(
            agent_state,
            decision="approve",
            reason="ok",
            latest_ack={"event_id": "ack-1", "sequence": 1},
        )
        self.assertEqual(agent_state["semantic_ack_barrier"]["status"], "approved")
        hloop.arm_semantic_ack_barrier(
            agent_state,
            message_id="msg-2",
            digest="bbb",
            required_reack_after_sequence=1,
        )
        self.assertEqual(agent_state["semantic_ack_barrier"]["status"], "awaiting_ack")
        self.assertEqual(agent_state["semantic_ack_barrier"]["message_id"], "msg-2")
        self.assertTrue(hloop.semantic_ack_barrier_blocking(agent_state))
        with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                agent_state,
                decision="approve",
                reason="old ACK must not approve a new contract",
                latest_ack={"event_id": "ack-1", "sequence": 1},
            )

    def test_contract_message_preserves_pending_task_digest_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.init_repo(Path(directory))
            state = hloop.load_state(repo)
            task_state = {
                "status": "running",
                "attempt_id": "T001-A001",
                "agent_provider": "codex",
            }
            hloop.arm_semantic_ack_barrier(
                task_state,
                message_id="task-contract:" + "a" * 64,
                digest="a" * 64,
                kind="task-contract",
                required_reack_after_sequence=3,
            )
            state["tasks"] = {"T001": task_state}
            hloop.save_state(repo, state)
            args = SimpleNamespace(
                contract_changing=True,
                timeout_ms=1,
                input_settle_ms=1,
                submit_verify_ms=1,
                submit_attempts=1,
            )
            with mock.patch.object(hloop, "send_agent_tui_message"):
                hloop.send_manager_message_and_record(
                    repo,
                    state,
                    task_state,
                    role="Worker",
                    agent_id="T001",
                    pane_id="w:p1",
                    message="apply the updated task contract",
                    source="test",
                    args=args,
                )
            barrier = hloop.load_state(repo)["tasks"]["T001"]["semantic_ack_barrier"]
            self.assertEqual(barrier["kind"], "task-contract")
            self.assertEqual(barrier["digest"], "a" * 64)
            self.assertEqual(barrier["required_reack_after_sequence"], 3)

    def test_reject_and_timeout_require_a_fresh_corrected_ack(self):
        state = {}
        hloop.arm_initial_semantic_ack_barrier(
            state, attempt_id="T001-A001", contract_digest="a" * 64
        )
        first = {"event_id": "ack-1", "sequence": 1}
        hloop.resolve_semantic_ack_barrier(
            state, decision="reject", reason="scope is wrong", latest_ack=first
        )
        self.assertTrue(hloop.semantic_ack_barrier_blocking(state))
        with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                state, decision="approve", reason="retry", latest_ack=first
            )
        hloop.resolve_semantic_ack_barrier(
            state,
            decision="approve",
            reason="corrected",
            latest_ack={"event_id": "ack-2", "sequence": 2},
        )
        self.assertEqual(hloop.semantic_ack_barrier_blocking(state), "")

        hloop.arm_initial_semantic_ack_barrier(
            state, attempt_id="T001-A002", contract_digest="b" * 64
        )
        hloop.resolve_semantic_ack_barrier(
            state, decision="timeout", reason="manager lease expired", latest_ack=None
        )
        self.assertEqual(state["semantic_ack_barrier"]["status"], "timed_out")
        self.assertTrue(hloop.semantic_ack_barrier_blocking(state))
        hloop.resolve_semantic_ack_barrier(
            state,
            decision="approve",
            reason="fresh ACK after timeout",
            latest_ack={"event_id": "ack-3", "sequence": 3},
        )
        self.assertEqual(hloop.semantic_ack_barrier_blocking(state), "")

        hloop.arm_initial_semantic_ack_barrier(
            state,
            attempt_id="T001-A003",
            contract_digest="c" * 64,
            required_reack_after_sequence=7,
        )
        with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
            hloop.resolve_semantic_ack_barrier(
                state,
                decision="approve",
                reason="pre-registration ACK must not approve a new role start",
                latest_ack={"event_id": "ack-old", "sequence": 7},
            )
        hloop.resolve_semantic_ack_barrier(
            state,
            decision="approve",
            reason="post-registration ACK",
            latest_ack={"event_id": "ack-new", "sequence": 8},
        )
        self.assertEqual(hloop.semantic_ack_barrier_blocking(state), "")


class ReviewGroupRuntimeTests(unittest.TestCase):
    """Exercises reviewer start/harvest/close for every 0.5 review mode."""

    RUN_ID = "review-runtime-run"

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-review-group-runtime")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> tuple[Path, dict]:
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
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "run_id": self.RUN_ID,
            "skill_version": hloop.SKILL_VERSION,
            "persistence": "local-only",
            "phase": "dispatching",
            "base_branch": "main",
            "integration_branch": "main",
            "reviews": {},
            "reviewer_runner": "tui",
            "reviewer_agent_provider": "codex",
            "reviewer_agent_model": "auto",
        }
        hloop.save_state(repo, state)
        return repo, state

    def start_args(self, repo: Path, mode: str, review_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            repo=str(repo),
            review_id=review_id,
            base=None,
            head=None,
            worktree=str(repo.parent / f"{review_id}-worktree"),
            manager_pane=None,
            direction="down",
            launcher="pane",
            runner="tui",
            agent_provider="codex",
            agent_model="auto",
            mode=mode,
            dry_run=True,
        )

    def write_review_artifacts(
        self,
        repo: Path,
        state: dict,
        *,
        review_id: str,
        mode: str,
        complete: bool = True,
    ) -> dict:
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        agent_config = hloop.role_agent_config(state, "reviewer")
        plan = hloop.build_reviewer_group_plan(mode, head_sha, agent_config)
        worktree = repo.parent / f"{review_id}-artifact-worktree"
        worktree.mkdir()
        review_state = {
            "status": "running",
            "gate_status": "running",
            "mode": mode,
            "review_plan": plan.to_record(),
            "review_providers": list(plan.providers),
            "base": "main",
            "head": "main",
            "head_sha": head_sha,
            "attempt_id": f"{review_id}-A001",
            "skill_version": hloop.SKILL_VERSION,
            "worktree": str(worktree),
            "baseline_dirty_files": [],
        }
        if mode == "single":
            artifact = hloop.review_file(worktree, review_id)
            review_state["worktree_review_path"] = str(artifact)
            review_state["review_path"] = str(hloop.review_file(repo, review_id))
            hloop.write_text(
                artifact,
                hloop.frontmatter(
                    {
                        "review_id": review_id,
                        "run_id": self.RUN_ID,
                        "skill_version": hloop.SKILL_VERSION,
                        "base": "main",
                        "head": "main",
                        "head_sha": head_sha,
                        "status": "reported",
                    }
                )
                + "\n## Fix Task Candidates\n\nNo fix task candidates.\n",
            )
        else:
            lane_results = tuple(lane.result() for lane in plan.expected_lanes)
            if not complete:
                lane_results = lane_results[:-1]
            manifest = hloop.hloop_review.ReviewManifest(
                review_id=review_id,
                plan=plan,
                lane_results=lane_results,
                findings=(),
                verification_plan=hloop.hloop_review.plan_verification(plan, ()),
                verifications=(),
            )
            hloop.write_text(
                hloop.review_manifest_file(worktree, review_id),
                json.dumps(manifest.to_record(), indent=2, sort_keys=True) + "\n",
            )
            for provider in plan.providers:
                hloop.write_text(
                    hloop.review_provider_file(worktree, review_id, provider),
                    hloop.frontmatter(
                        {
                            "review_id": review_id,
                            "run_id": self.RUN_ID,
                            "skill_version": hloop.SKILL_VERSION,
                            "head_sha": head_sha,
                            "provider": provider,
                            "status": "reported",
                        }
                    )
                    + f"\n# {provider} provider report\n",
                )
            artifact = hloop.review_final_file(worktree, review_id)
            review_state["worktree_review_path"] = str(artifact)
            review_state["review_path"] = str(hloop.review_final_file(repo, review_id))
            hloop.write_text(
                artifact,
                hloop.frontmatter(
                    {
                        "review_id": review_id,
                        "run_id": self.RUN_ID,
                        "skill_version": hloop.SKILL_VERSION,
                        "base": "main",
                        "head": "main",
                        "head_sha": head_sha,
                        "status": "reported",
                    }
                )
                + "\n## Fix Task Candidates\n\nNo fix task candidates.\n",
            )
        state["reviews"][review_id] = review_state
        hloop.save_state(repo, state)
        return review_state

    def test_start_prompts_harvest_and_close_every_review_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            for index, mode in enumerate(
                ("single", "swarm", "dual", "dual-swarm"), start=1
            ):
                review_id = f"R{index:03d}"
                with self.subTest(mode=mode):
                    with contextlib.redirect_stdout(io.StringIO()) as buffer:
                        self.assertEqual(
                            hloop.cmd_reviewer_start(
                                self.start_args(repo, mode, review_id)
                            ),
                            0,
                        )
                    self.assertIn("DRY RUN", buffer.getvalue())

                    agent_config = hloop.role_agent_config(state, "reviewer")
                    head_sha = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=repo,
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip()
                    plan = hloop.build_reviewer_group_plan(mode, head_sha, agent_config)
                    prompt = hloop.render_reviewer_prompt(
                        review_id,
                        "main",
                        "main",
                        state,
                        agent_config=agent_config,
                        head_sha=head_sha,
                        review_plan=plan,
                    )
                    if mode == "single":
                        self.assertIn(f"reviews/{review_id}.md", prompt)
                    else:
                        for lane in plan.expected_lanes:
                            self.assertIn(lane.lane_id, prompt)
                            self.assertIn(lane.agent_label, prompt)
                        self.assertIn(f"reviews/{review_id}/MANIFEST.json", prompt)
                        self.assertIn(f"reviews/{review_id}/FINAL.md", prompt)

                    review_state = self.write_review_artifacts(
                        repo, state, review_id=review_id, mode=mode
                    )
                    with mock.patch.object(
                        hloop, "preflight_loop", return_value=state
                    ), mock.patch.object(
                        hloop, "validate_reviewer_worktree_scope", return_value=[]
                    ), mock.patch.object(
                        hloop, "cleanup_completed_agent_pane"
                    ), mock.patch.object(
                        hloop, "cleanup_review_worktree"
                    ), contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(
                            hloop.cmd_reviewer_harvest(
                                SimpleNamespace(
                                    repo=str(repo),
                                    review_id=review_id,
                                    keep_pane=False,
                                    session_cleanup="none",
                                )
                            ),
                            0,
                        )
                        self.assertEqual(review_state["gate_status"], "reported")
                        self.assertEqual(
                            hloop.cmd_reviewer_close(
                                SimpleNamespace(
                                    repo=str(repo),
                                    review_id=review_id,
                                    verdict="passed",
                                    reason="runtime fixture passed",
                                    keep_pane=False,
                                    session_cleanup="none",
                                )
                            ),
                            0,
                        )
                    self.assertEqual(review_state["gate_status"], "triaged")
                    if mode != "single":
                        self.assertTrue(review_state["manifest_complete"])
                        self.assertTrue(Path(review_state["manifest_path"]).is_file())
                        self.assertEqual(
                            set(review_state["provider_report_paths"]),
                            set(plan.providers),
                        )

    def test_config_only_topology_resolves_mode_providers_and_probe_count(self):
        """An unset CLI --mode must not silently narrow a configured topology."""

        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["resolved_config"] = {
                "reviewer": {
                    "mode": "dual-swarm",
                    "providers": ["codex", "claude"],
                    "probes_per_provider": 8,
                }
            }
            state["review_capacity_limits"] = {"codex": 20, "claude": 20}
            hloop.save_state(repo, state)

            topology = hloop.resolved_reviewer_topology(state)
            self.assertEqual(topology["mode"], "dual-swarm")
            self.assertEqual(topology["providers"], ["codex", "claude"])
            self.assertEqual(topology["probes_per_provider"], 8)
            self.assertIsNone(topology["probe_count"])

            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            agent_config = hloop.role_agent_config(state, "reviewer")
            plan = hloop.build_reviewer_group_plan(
                topology["mode"], head_sha, agent_config, topology=topology
            )
            self.assertEqual(plan.mode, "dual-swarm")
            self.assertEqual(set(plan.providers), {"codex", "claude"})
            for provider_plan in plan.provider_plans:
                self.assertEqual(len(provider_plan.lanes), 8)

            args = self.start_args(repo, None, "R901")
            args.providers = None
            args.probe_count = None
            args.probes_per_provider = None
            with contextlib.redirect_stdout(io.StringIO()) as buffer:
                self.assertEqual(hloop.cmd_reviewer_start(args), 0)
            self.assertIn("DRY RUN", buffer.getvalue())

    def test_explicit_cli_mode_overrides_configured_reviewer_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["resolved_config"] = {"reviewer": {"mode": "dual-swarm"}}
            hloop.save_state(repo, state)
            topology = hloop.resolved_reviewer_topology(state, mode="single")
            self.assertEqual(topology["mode"], "single")

    def test_incomplete_swarm_harvests_but_close_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            review_state = self.write_review_artifacts(
                repo, state, review_id="R001", mode="swarm", complete=False
            )
            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "validate_reviewer_worktree_scope", return_value=[]
            ), mock.patch.object(
                hloop, "cleanup_completed_agent_pane"
            ), mock.patch.object(
                hloop, "cleanup_review_worktree"
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    hloop.cmd_reviewer_harvest(
                        SimpleNamespace(
                            repo=str(repo),
                            review_id="R001",
                            keep_pane=False,
                            session_cleanup="none",
                        )
                    ),
                    0,
                )
                self.assertFalse(review_state["manifest_complete"])
                with self.assertRaisesRegex(hloop.HLoopError, "manifest incomplete"):
                    hloop.cmd_reviewer_close(
                        SimpleNamespace(
                            repo=str(repo),
                            review_id="R001",
                            verdict="passed",
                            reason="must not close",
                            keep_pane=False,
                            session_cleanup="none",
                        )
                    )

    def test_reviewer_start_rejects_swarm_exceeding_configured_capacity_before_pane_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["review_capacity_limits"] = {"codex": 3}
            hloop.save_state(repo, state)
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    hloop.hloop_providers.ProviderError, "review swarm capacity exceeded"
                ):
                    hloop.cmd_reviewer_start(self.start_args(repo, "swarm", "R901"))
            self.assertNotIn("R901", state.get("reviews", {}))
            self.assertFalse((repo.parent / "R901-worktree").exists())

    def test_triage_accepts_only_manifest_confirmed_fingerprints(self):
        confirmed = "sha256:" + "a" * 64
        refuted = "sha256:" + "b" * 64
        candidates = [
            {"title": "confirmed", "source_finding": confirmed},
            {"title": "refuted", "source_finding": refuted},
            {"title": "missing source", "source_finding": ""},
        ]

        accepted, rejected = hloop.confirmed_review_fix_task_candidates(
            SimpleNamespace(confirmed_fingerprints=(confirmed,)), candidates, []
        )

        self.assertEqual([item["title"] for item in accepted], ["confirmed"])
        self.assertEqual([item["title"] for item in rejected], ["refuted", "missing source"])
        self.assertTrue(
            all("all confirmed" in item["reasons"][0] for item in rejected)
        )


class SpecificationDecisionRoleTests(unittest.TestCase):
    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-specification-roles")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def decision(self):
        return hloop.DecisionRecord(
            decision_id="D001",
            decision_class=hloop.DECISION_BLOCKING_USER,
            status=hloop.DECISION_PENDING,
            question="公開 API の互換性を維持しますか",
            options=(
                {"id": "opt_1", "label": "維持する", "tradeoffs": ["安全だが移行期間が必要"]},
                {"id": "opt_2", "label": "変更する", "tradeoffs": ["簡潔だが既存利用者へ影響"]},
            ),
            recommendation={"option_id": "opt_1", "rationale": "既存利用者を保護できるため"},
            affected_task_ids=("T001",),
        )

    def init_repo(self, root: Path) -> tuple[Path, dict]:
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
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "run_id": "run-spec",
            "goal_id": "spec",
            "phase": "dispatching",
            "integration_branch": "main",
            "specification_scout": "always",
            "specification_scout_run": {},
            "decision_liaisons": {},
            "tasks": {"T001": {"status": "queued", "kind": "implementation"}},
            "requirements": {},
            "decisions": {"D001": self.decision().to_record()},
        }
        hloop.save_state(repo, state)
        return repo, state

    def report_args(
        self,
        repo: Path,
        credential: Path,
        *,
        role_id: str,
        attempt_id: str,
        digest: str,
        report_type: str = "ack",
    ) -> SimpleNamespace:
        completion = report_type == "completion"
        attention = report_type == "attention"
        ack = report_type == "ack"
        nonce = uuid.uuid4().hex
        return SimpleNamespace(
            repo=str(repo),
            run_id="run-spec",
            role_id=role_id,
            attempt_id=attempt_id,
            event_id=None,
            task_contract_digest=digest,
            report_token=None,
            report_credential_file=str(credential),
            file=None,
            stdin=False,
            type=report_type,
            stage="completed" if completion else ("blocked" if attention else "planning"),
            summary=(
                f"role completed {nonce}"
                if completion
                else (f"Manager attention needed {nonce}" if attention else f"contract understood {nonce}")
            ),
            next="Manager handoff" if completion else "wait for Manager",
            evidence_ref=["skills/herdr-dev-loop/scripts/hloop:1"],
            understood_goal="perform the bounded decision role" if ack else None,
            scope=["decision artifact only"] if ack else None,
            acceptance=["Manager approves before material work"] if ack else None,
            approach="reuse the common report contract" if ack else None,
            risk=[],
            impact="Manager must inspect the decision role" if attention else None,
            attempted=["persisted role evidence"] if attention else None,
            option_text=["inspect the durable report"] if attention else None,
            recommendation="inspect and respond" if attention else None,
            blocked_scope=["decision role"] if attention else None,
            artifact="decisions/artifact.md" if completion else None,
            head_sha="a" * 40 if completion else None,
            validation_result_ref=["synthetic role validation"] if completion else None,
            residual_risk=["none"] if completion else None,
            handoff="Manager may harvest" if completion else None,
        )

    def test_specification_scout_auto_always_and_off(self):
        base = {
            "tasks": {
                "T001": {
                    "status": "queued",
                    "kind": "implementation",
                    "title": "公開 API schema migration",
                }
            },
            "requirements": {},
            "specification_scout_run": {},
        }
        required, reasons = hloop.specification_scout_required(
            {**base, "specification_scout": "auto"}
        )
        self.assertTrue(required)
        self.assertTrue(any("公開" in reason or "schema" in reason for reason in reasons))
        self.assertEqual(
            hloop.specification_scout_required(
                {**base, "specification_scout": "always"}
            ),
            (True, ["policy is always"]),
        )
        self.assertFalse(
            hloop.specification_scout_required(
                {**base, "specification_scout": "off"}
            )[0]
        )

    def test_scout_and_liaison_fallbacks_are_durable_and_plain_japanese(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            state = {
                "state_format_version": hloop.STATE_FORMAT_VERSION,
                "schema_revision": hloop.STATE_SCHEMA_REVISION,
                "run_id": "run-spec",
                "goal_id": "spec",
                "phase": "dispatching",
                "integration_branch": "main",
                "specification_scout": "always",
                "specification_scout_run": {},
                "tasks": {"T001": {"status": "queued", "kind": "implementation"}},
                "requirements": {},
                "decisions": {"D001": self.decision().to_record()},
            }
            hloop.save_state(repo, state)
            args = SimpleNamespace(
                repo=str(repo),
                force=False,
                worktree=None,
                runner="tui",
                agent_provider=None,
                agent_model=None,
                launcher="pane",
                manager_pane=None,
                direction="right",
            )
            output = io.StringIO()
            with mock.patch.object(hloop, "repo_root", return_value=repo), mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "git", return_value="a" * 40
            ), mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                self.assertEqual(hloop.cmd_specification_scout_start(args), 0)
            scout = hloop.load_state(repo)["specification_scout_run"]
            self.assertEqual(scout["mode"], "manager-fallback")
            self.assertEqual(scout["status"], "waiting-manager")

            state = hloop.load_state(repo)
            output = io.StringIO()
            with mock.patch.object(hloop, "git", return_value="a" * 40), mock.patch.dict(
                os.environ, {}, clear=True
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    hloop.start_decision_liaison(repo, state, self.decision(), args),
                    0,
                )
            liaison = hloop.load_state(repo)["decision_liaisons"]["D001"]
            self.assertEqual(liaison["mode"], "manager-fallback")
            self.assertEqual(liaison["status"], "waiting-manager")
            rendered = output.getvalue()
            self.assertIn("# 判断のお願い", rendered)
            self.assertNotIn("D001", rendered)
            self.assertNotIn("T001", rendered)

    def test_successful_liaison_start_records_dedicated_role(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            worktree = root / "liaison"
            repo.mkdir()
            worktree.mkdir()
            credential = root / "liaison-credential.json"
            credential.write_text("{}\n", encoding="utf-8")
            credential.chmod(0o600)
            state = {
                "state_format_version": hloop.STATE_FORMAT_VERSION,
                "schema_revision": hloop.STATE_SCHEMA_REVISION,
                "run_id": "run-liaison",
                "goal_id": "liaison",
                "integration_branch": "main",
                "decision_liaisons": {},
            }
            invocation = SimpleNamespace(as_record=lambda: {"provider": "codex"})
            args = SimpleNamespace(
                worktree=str(worktree),
                agent_provider="codex",
                agent_model="auto",
                launcher="pane",
                manager_pane="w:p1",
                direction="right",
            )
            calls = []

            def register(*_args, **_kwargs):
                calls.append("register")
                return credential, 0

            def start_pane(**_kwargs):
                calls.append("pane")
                return "w:p2"

            with mock.patch.object(hloop, "git", return_value="b" * 40), mock.patch.object(
                hloop, "command_exists", return_value=True
            ), mock.patch.object(
                hloop, "role_agent_command", return_value=("codex", invocation)
            ), mock.patch.object(
                hloop, "prepare_role_worktree"
            ), mock.patch.object(
                hloop, "ensure_advisor_visible_in_worktree"
            ), mock.patch.object(
                hloop, "porcelain_paths", return_value=[]
            ), mock.patch.object(
                hloop,
                "register_role_report_identity_and_ack_floor",
                side_effect=register,
            ), mock.patch.object(
                hloop, "start_pane_launcher", side_effect=start_pane
            ), mock.patch.dict(
                os.environ, {"HERDR_ENV": "1"}, clear=True
            ):
                self.assertEqual(
                    hloop.start_decision_liaison(repo, state, self.decision(), args),
                    0,
                )
            liaison = hloop.load_state(repo)["decision_liaisons"]["D001"]
            self.assertEqual(liaison["mode"], "agent-pane")
            self.assertEqual(liaison["pane_id"], "w:p2")
            self.assertEqual(liaison["role_id"], "L-D001")
            self.assertEqual(liaison["attempt_id"], "L-D001-A001")
            self.assertEqual(liaison["semantic_ack_barrier"]["status"], "awaiting_ack")
            self.assertEqual(calls, ["register", "pane"])
            prompt = Path(liaison["prompt"]).read_text(encoding="utf-8")
            self.assertIn(str(credential), prompt)
            self.assertIn("agent report", prompt)
            self.assertIn("stop before material work", prompt)
            self.assertIn("推奨案は利用者の同意では", prompt)
            self.assertIn("`Manager message id:`", prompt)
            self.assertIn("completion report も完了 sentinel も送らない", prompt)
            self.assertIn("response_source: explicit-user-input", prompt)
            self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)

    def test_liaison_harvest_requires_explicit_subsequent_user_input_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, state = self.init_repo(root)
            worktree = root / "liaison-artifact"
            source = (
                worktree
                / hloop.LOOP_DIR
                / "decisions"
                / "D001"
                / "RESPONSE.md"
            )
            source.parent.mkdir(parents=True)
            head_sha = "b" * 40
            liaison = {
                "role_id": "L-D001",
                "decision_id": "D001",
                "status": "running",
                "gate_status": "running",
                "worktree": str(worktree),
                "attempt_id": "L-D001-A001",
                "skill_version": hloop.SKILL_VERSION,
                "head_sha": head_sha,
                "semantic_ack_barrier": {"status": "approved"},
            }
            state["decision_liaisons"]["D001"] = liaison
            args = SimpleNamespace(
                repo=str(repo), id="D001", session_cleanup="none"
            )
            identity = {
                "decision_id": "D001",
                "responded_by": "liaison",
                "responded_at": "2026-07-15T12:00:01+00:00",
                "attempt_id": "L-D001-A001",
                "run_id": "run-spec",
                "skill_version": hloop.SKILL_VERSION,
                "head_sha": head_sha,
            }

            # The live failure shape selected the recommendation immediately
            # after presenting it, without any later user message provenance.
            source.write_text(
                hloop.frontmatter({**identity, "selected_option": "opt_1"})
                + "\n# 回答\n\n推奨案を選びます。\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "validate_decision_role_scope", return_value=[]
            ):
                with self.assertRaisesRegex(
                    hloop.HLoopError,
                    "lacks explicit subsequent user input provenance",
                ):
                    hloop.cmd_decision_liaison_harvest(args)
            self.assertEqual(state["decisions"]["D001"]["status"], hloop.DECISION_PENDING)

            explicit_response = {
                **identity,
                "response_source": "explicit-user-input",
                "response_channel": "same-pane",
                "response_turn": "after-question",
                "user_input_received_at": "2026-07-15T12:00:00+00:00",
                "user_input_kind": "free-text",
            }
            source.write_text(
                hloop.frontmatter(explicit_response)
                + "\n# 回答\n\n段階的な互換期間を設けてください。\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "validate_decision_role_scope", return_value=[]
            ), mock.patch.object(
                hloop, "cleanup_completed_agent_pane"
            ), mock.patch.object(
                hloop, "cleanup_decision_role_worktree"
            ), mock.patch.object(
                hloop, "revoke_active_role_report_identity"
            ), mock.patch.object(
                hloop, "write_decision_artifacts"
            ), mock.patch.object(
                hloop, "save_state"
            ), mock.patch.object(
                hloop, "journal"
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hloop.cmd_decision_liaison_harvest(args), 0)
            response = hloop.DecisionRecord.from_record(
                state["decisions"]["D001"]
            ).response
            self.assertIsNotNone(response)
            self.assertEqual(response.selected_option, "")
            self.assertEqual(response.free_text, "段階的な互換期間を設けてください。")
            self.assertEqual(
                liaison["response_provenance"]["response_source"],
                "explicit-user-input",
            )
            with mock.patch.object(hloop, "decision_attention_notice"):
                hloop.write_decision_artifacts(repo, state)
            canonical_meta = hloop.read_frontmatter(
                hloop.decision_liaison_file(repo, "D001")
            )
            self.assertEqual(canonical_meta["response_channel"], "same-pane")
            self.assertEqual(canonical_meta["response_turn"], "after-question")
            self.assertEqual(canonical_meta["user_input_kind"], "free-text")
            self.assertNotIn("selected_option", canonical_meta)

    def test_liaison_provenance_accepts_free_text_without_recommendation_fallback(self):
        meta = {
            "responded_by": "liaison",
            "responded_at": "2026-07-15T12:00:01+00:00",
            "response_source": "explicit-user-input",
            "response_channel": "same-pane",
            "response_turn": "after-question",
            "user_input_received_at": "2026-07-15T12:00:00+00:00",
            "user_input_kind": "free-text",
        }
        self.assertEqual(hloop.decision_liaison_response_provenance_error(meta), "")
        self.assertNotIn("selected_option", meta)
        self.assertEqual(
            hloop.decision_liaison_response_provenance_error(
                {**meta, "user_input_kind": "option", "selected_option": "opt_1"}
            ),
            "",
        )
        self.assertIn(
            "response_source",
            hloop.decision_liaison_response_provenance_error(
                {**meta, "response_source": "manager-message"}
            ),
        )
        self.assertIn(
            "selected_option",
            hloop.decision_liaison_response_provenance_error(
                {**meta, "user_input_kind": "option"}
            ),
        )
        self.assertIn(
            "selected_option",
            hloop.decision_liaison_response_provenance_error(
                {**meta, "selected_option": "opt_1"}
            ),
        )

    def test_successful_scout_start_registers_identity_before_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, state = self.init_repo(root)
            worktree = root / "scout"
            worktree.mkdir()
            credential = root / "scout-credential.json"
            credential.write_text("{}\n", encoding="utf-8")
            credential.chmod(0o600)
            invocation = SimpleNamespace(as_record=lambda: {"provider": "codex"})
            args = SimpleNamespace(
                repo=str(repo),
                force=True,
                worktree=str(worktree),
                runner="tui",
                agent_provider="codex",
                agent_model="auto",
                launcher="pane",
                manager_pane="w:p1",
                direction="right",
            )
            calls = []
            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(
                hloop, "git", return_value="a" * 40
            ), mock.patch.object(
                hloop, "command_exists", return_value=True
            ), mock.patch.object(
                hloop, "role_agent_command", return_value=("codex", invocation)
            ), mock.patch.object(
                hloop, "prepare_role_worktree"
            ), mock.patch.object(
                hloop, "ensure_advisor_visible_in_worktree"
            ), mock.patch.object(
                hloop, "porcelain_paths", return_value=[]
            ), mock.patch.object(
                hloop,
                "register_role_report_identity_and_ack_floor",
                side_effect=lambda *_args, **_kwargs: (calls.append("register") or (credential, 0)),
            ), mock.patch.object(
                hloop,
                "start_pane_launcher",
                side_effect=lambda **_kwargs: (calls.append("pane") or "w:p2"),
            ), mock.patch.dict(
                os.environ, {"HERDR_ENV": "1"}, clear=True
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hloop.cmd_specification_scout_start(args), 0)
            scout = hloop.load_state(repo)["specification_scout_run"]
            self.assertEqual(scout["attempt_id"], "S001-A001")
            self.assertEqual(scout["semantic_ack_barrier"]["status"], "awaiting_ack")
            self.assertEqual(calls, ["register", "pane"])
            prompt = Path(scout["prompt"]).read_text(encoding="utf-8")
            self.assertIn(str(credential), prompt)
            self.assertIn("agent report", prompt)

    def test_scout_and_liaison_ack_reject_timeout_completion_abort_and_requeue(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            identities = {}
            for role_id, attempt_id in (
                ("S001", "S001-A001"),
                ("L-D001", "L-D001-A001"),
            ):
                digest = hashlib.sha256(role_id.encode("utf-8")).hexdigest()
                credential, floor = hloop.register_role_report_identity_and_ack_floor(
                    repo,
                    state,
                    role_id=role_id,
                    attempt_id=attempt_id,
                    task_contract_digest=digest,
                )
                self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
                identities[role_id] = (attempt_id, digest, credential)
                role_state = {
                    "role_id": role_id,
                    "status": "running",
                    "gate_status": "running",
                    "attempt_no": 1,
                    "attempt_id": attempt_id,
                    "skill_version": hloop.SKILL_VERSION,
                    "task_contract_digest": digest,
                }
                hloop.arm_initial_semantic_ack_barrier(
                    role_state,
                    attempt_id=attempt_id,
                    contract_digest=digest,
                    required_reack_after_sequence=floor,
                )
                if role_id == "S001":
                    state["specification_scout_run"] = role_state
                else:
                    state["decision_liaisons"]["D001"] = {
                        **role_state,
                        "decision_id": "D001",
                    }
            hloop.save_state(repo, state)

            scout_attempt, scout_digest, scout_credential = identities["S001"]
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    scout_credential,
                    role_id="S001",
                    attempt_id=scout_attempt,
                    digest=scout_digest,
                )
            )
            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="S001",
                        decision="approve",
                        reason="Scout scope confirmed",
                    )
                )
            self.assertEqual(
                hloop.load_state(repo)["specification_scout_run"]["semantic_ack_barrier"]["status"],
                "approved",
            )

            liaison_attempt, liaison_digest, liaison_credential = identities["L-D001"]
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    liaison_credential,
                    role_id="L-D001",
                    attempt_id=liaison_attempt,
                    digest=liaison_digest,
                )
            )
            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="L-D001",
                        decision="reject",
                        reason="question wording is incomplete",
                    )
                )
                with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
                    hloop.cmd_agent_ack_resolve(
                        SimpleNamespace(
                            repo=str(repo),
                            agent_id="L-D001",
                            decision="approve",
                            reason="old ACK must not pass",
                        )
                    )
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    liaison_credential,
                    role_id="L-D001",
                    attempt_id=liaison_attempt,
                    digest=liaison_digest,
                )
            )
            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="L-D001",
                        decision="approve",
                        reason="corrected ACK accepted",
                    )
                )
            reloaded = hloop.load_state(repo)
            liaison = reloaded["decision_liaisons"]["D001"]
            floor = hloop.latest_semantic_ack_sequence(
                repo, reloaded, role_id="L-D001", agent_state=liaison
            )
            hloop.arm_semantic_ack_barrier(
                liaison,
                message_id="contract-change",
                digest="updated",
                required_reack_after_sequence=floor,
            )
            hloop.save_state(repo, reloaded)
            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="L-D001",
                        decision="timeout",
                        reason="Manager approval timed out",
                    )
                )
                with self.assertRaisesRegex(hloop.HLoopError, "corrected semantic ACK"):
                    hloop.cmd_agent_ack_resolve(
                        SimpleNamespace(
                            repo=str(repo),
                            agent_id="L-D001",
                            decision="approve",
                            reason="stale ACK",
                        )
                    )
            hloop.cmd_agent_report(
                self.report_args(
                    repo,
                    liaison_credential,
                    role_id="L-D001",
                    attempt_id=liaison_attempt,
                    digest=liaison_digest,
                )
            )
            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_agent_ack_resolve(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="L-D001",
                        decision="approve",
                        reason="fresh ACK after timeout",
                    )
                )

            for role_id, (attempt_id, digest, credential) in identities.items():
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        credential,
                        role_id=role_id,
                        attempt_id=attempt_id,
                        digest=digest,
                        report_type="attention",
                    )
                )
                hloop.cmd_agent_report(
                    self.report_args(
                        repo,
                        credential,
                        role_id=role_id,
                        attempt_id=attempt_id,
                        digest=digest,
                        report_type="completion",
                    )
                )
            store = hloop._open_broker_store(repo)
            with store.transaction() as transaction:
                events = store.events(transaction)
                completions = [event for event in events if event["type"] == "completion"]
                attentions = [event for event in events if event["type"] == "attention"]
            self.assertEqual({event["role_id"] for event in completions}, {"S001", "L-D001"})
            self.assertEqual({event["role_id"] for event in attentions}, {"S001", "L-D001"})

            with contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_agent_abort(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="S001",
                        reason="synthetic abort",
                        keep_worktree=False,
                        force_cleanup=False,
                    )
                )
                hloop.cmd_agent_requeue(
                    SimpleNamespace(
                        repo=str(repo),
                        agent_id="L-D001",
                        reason="synthetic requeue",
                        force_cleanup=False,
                    )
                )
            final = hloop.load_state(repo)
            self.assertEqual(final["specification_scout_run"]["status"], "aborted")
            self.assertEqual(final["decision_liaisons"]["D001"]["status"], "queued")
            self.assertFalse(scout_credential.exists())
            self.assertFalse(liaison_credential.exists())

    def test_agent_message_and_manager_sleep_include_scout_and_liaison(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            state["specification_scout_run"] = {
                "role_id": "S001",
                "status": "running",
                "gate_status": "running",
                "attempt_id": "S001-A001",
                "pane_id": "w:p2",
                "agent_provider": "codex",
            }
            state["decision_liaisons"]["D001"] = {
                "role_id": "L-D001",
                "decision_id": "D001",
                "status": "running",
                "gate_status": "running",
                "attempt_id": "L-D001-A001",
                "pane_id": "w:p3",
                "agent_provider": "codex",
            }
            hloop.save_state(repo, state)
            args = SimpleNamespace(
                repo=str(repo),
                message="scope changed",
                file=None,
                timeout_ms=100,
                input_settle_ms=0,
                submit_verify_ms=1,
                submit_attempts=1,
                contract_changing=True,
            )
            with mock.patch.object(
                hloop, "preflight_loop", return_value=state
            ), mock.patch.object(hloop, "send_agent_tui_message") as send:
                for role_id in ("S001", "L-D001"):
                    args.agent_id = role_id
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(hloop.cmd_agent_message(args), 0)
            self.assertEqual(send.call_count, 2)
            reloaded = hloop.load_state(repo)
            self.assertEqual(
                reloaded["specification_scout_run"]["semantic_ack_barrier"]["status"],
                "awaiting_ack",
            )
            self.assertEqual(
                reloaded["decision_liaisons"]["D001"]["semantic_ack_barrier"]["status"],
                "awaiting_ack",
            )
            with mock.patch.object(hloop, "preflight_loop", return_value=reloaded):
                with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                    hloop.cmd_specification_scout_harvest(
                        SimpleNamespace(repo=str(repo))
                    )
                with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                    hloop.cmd_specification_scout_close(
                        SimpleNamespace(
                            repo=str(repo), verdict="no-decision", reason="too early"
                        )
                    )
                with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                    hloop.cmd_decision_liaison_harvest(
                        SimpleNamespace(repo=str(repo), id="D001")
                    )
                with self.assertRaisesRegex(hloop.HLoopError, "semantic ACK barrier"):
                    hloop.cmd_decision_respond(
                        SimpleNamespace(
                            repo=str(repo),
                            id="D001",
                            option="opt_1",
                            responded_by="manager",
                            text="too early",
                            recommendation=None,
                        )
                    )

            sleep_result = SimpleNamespace(
                lease_generation=1,
                reason="fallback",
                event_ids=(),
                drained_reports=0,
                fallback=SimpleNamespace(pane_id="w:p2", status="done", returncode=0),
            )
            supervisor_instance = mock.Mock()
            supervisor_instance.sleep.return_value = sleep_result
            with mock.patch.object(
                hloop.hloop_supervisor,
                "ManagerSleepSupervisor",
                return_value=supervisor_instance,
            ), contextlib.redirect_stdout(io.StringIO()):
                hloop.cmd_manager_sleep(
                    SimpleNamespace(
                        repo=str(repo),
                        ttl_seconds=1,
                        manager_session_id="manager",
                        pane_id="w:p1",
                    )
                )
            watches = {
                (watch.pane_id, watch.status)
                for watch in supervisor_instance.sleep.call_args.kwargs["fallback_watches"]
            }
            for pane_id in ("w:p2", "w:p3"):
                self.assertEqual(
                    {status for watched_pane, status in watches if watched_pane == pane_id},
                    set(hloop.hloop_supervisor.FALLBACK_STATUSES),
                )

    def test_decision_attention_is_idempotent_and_falls_back_auditably(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, state = self.init_repo(Path(directory))
            output = io.StringIO()
            with mock.patch.object(hloop, "command_exists", return_value=True), mock.patch.object(
                hloop, "run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")
            ) as notify, mock.patch.dict(
                os.environ, {"HERDR_ENV": "1"}, clear=True
            ), contextlib.redirect_stdout(output):
                hloop.write_decision_artifacts(repo, state)
                hloop.write_decision_artifacts(repo, state)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(output.getvalue().count("HERDR_LOOP_DECISION_ATTENTION:"), 1)
            self.assertEqual(state["decision_attention"]["D001"]["status"], "notified")

            second = self.decision().to_record()
            second["id"] = "D002"
            state["decisions"]["D002"] = second
            fallback = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(fallback):
                hloop.write_decision_artifacts(repo, state)
                hloop.write_decision_artifacts(repo, state)
            self.assertEqual(
                fallback.getvalue().count("HERDR_LOOP_DECISION_ATTENTION_FALLBACK:"), 1
            )
            self.assertIn("# 判断のお願い", fallback.getvalue())
            self.assertEqual(
                state["decision_attention"]["D002"]["status"], "manager-fallback"
            )


class RequirementProgressOutcomeTests(unittest.TestCase):
    """Exercises input record / requirement new / progress record / outcome show."""

    def setUp(self):
        self.previous_namespace = hloop.LOOP_NAMESPACE
        hloop.configure_loop_namespace("test-requirement-progress-outcome")

    def tearDown(self):
        hloop.configure_loop_namespace(self.previous_namespace)

    def init_repo(self, root: Path) -> Path:
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
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE
        )
        state = {
            "state_format_version": hloop.STATE_FORMAT_VERSION,
            "schema_revision": hloop.STATE_SCHEMA_REVISION,
            "namespace": hloop.LOOP_NAMESPACE,
            "phase": "dispatching",
            "integration_branch": "main",
        }
        hloop.save_state(repo, state)
        return repo

    def test_input_requirement_progress_outcome_round_trip_redacts_and_gates_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_input_record(
                    SimpleNamespace(
                        repo=str(repo),
                        source="manager-chat",
                        text="please add auth token=abcdefghijklmnop123456",
                    )
                )
            self.assertIn("captured U0001", buffer.getvalue())

            input_path = hloop.local_sensitive_input_dir(repo) / "U0001.json"
            stored = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertNotIn("abcdefghijklmnop123456", stored["raw_input"])
            self.assertIn("credential-assignment", stored["redactions"])

            # STATE.json (checked into the loop's checkpoint) must never retain
            # the raw/redacted prompt text itself, only a digest pointer.
            state_after_input = hloop.load_state(repo)
            self.assertNotIn("raw_input", json.dumps(state_after_input["inputs_index"]))

            # The local-sensitive inputs/** file itself must never be swept
            # into `hloop checkpoint`, even though it lives under LOOP_DIR.
            checkpoint_candidates = hloop.checkpoint_paths(
                repo, SimpleNamespace(path=None, force=True, include_lock=False, include_prompts=False)
            )
            self.assertFalse(any("inputs/" in path for path in checkpoint_candidates))

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_requirement_new(
                    SimpleNamespace(
                        repo=str(repo),
                        id=None,
                        source_input=["U0001"],
                        acceptance=["auth token support is added"],
                        priority="P1",
                        depends_on=None,
                    )
                )
            self.assertIn("created REQ-001", buffer.getvalue())
            loop_dir = repo / hloop.LOOP_DIR
            self.assertTrue((loop_dir / "requirements" / "REQUIREMENTS.md").is_file())
            self.assertTrue((loop_dir / "requirements" / "STATUS.md").is_file())

            hloop.cmd_progress_record(
                SimpleNamespace(
                    repo=str(repo),
                    requirement_id="REQ-001",
                    status="in_progress",
                    task_id=["T001"],
                    evidence_kind=None,
                    evidence_ref=None,
                    verified_by=None,
                    head_sha=None,
                    result=None,
                    remaining_work="implement token handling",
                    blocker=None,
                )
            )

            # An agent-only assertion of "verified" without Manager/HLoop-checked
            # artifact+test evidence must be rejected (requirement evidence gate).
            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_progress_record(
                    SimpleNamespace(
                        repo=str(repo),
                        requirement_id="REQ-001",
                        status="verified",
                        task_id=None,
                        evidence_kind="agent-report",
                        evidence_ref="worker says done",
                        verified_by=None,
                        head_sha=None,
                        result=None,
                        remaining_work=None,
                        blocker=None,
                    )
                )

            hloop.cmd_progress_record(
                SimpleNamespace(
                    repo=str(repo),
                    requirement_id="REQ-001",
                    status="implemented_unverified",
                    task_id=None,
                    evidence_kind=None,
                    evidence_ref=None,
                    verified_by=None,
                    head_sha=None,
                    result=None,
                    remaining_work=None,
                    blocker=None,
                )
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                hloop.cmd_outcome_show(SimpleNamespace(repo=str(repo), requirement_id="REQ-001"))
            outcome = json.loads(buffer.getvalue())
            self.assertEqual(outcome["requirement"]["id"], "REQ-001")
            self.assertEqual(outcome["progress"]["status"], "implemented_unverified")
            self.assertTrue((loop_dir / "progress" / "LATEST.md").is_file())
            self.assertTrue((loop_dir / "progress" / "P0002.md").is_file())

            hloop.cmd_context_update(
                SimpleNamespace(
                    repo=str(repo),
                    source="U0001",
                    text="Keep authentication changes backward compatible; token=context-secret-12345678.",
                )
            )
            context = (loop_dir / "context" / "MANAGER_CONTEXT.md").read_text()
            self.assertIn("backward compatible", context)
            self.assertIn("U0001", context)
            self.assertNotIn("context-secret-12345678", context)
            self.assertIn("[REDACTED]", context)

    def test_progress_record_clears_or_replaces_blockers_then_advances_to_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)

            hloop.cmd_input_record(
                SimpleNamespace(
                    repo=str(repo),
                    source="manager-chat",
                    text="add blocker recovery support",
                )
            )
            hloop.cmd_requirement_new(
                SimpleNamespace(
                    repo=str(repo),
                    id=None,
                    source_input=["U0001"],
                    acceptance=["blocked progress is recoverable"],
                    priority="P1",
                    depends_on=None,
                )
            )

            def record(**overrides):
                base = dict(
                    repo=str(repo),
                    requirement_id="REQ-001",
                    task_id=None,
                    evidence_kind=None,
                    evidence_ref=None,
                    verified_by=None,
                    head_sha=None,
                    result=None,
                    remaining_work=None,
                    blocker=None,
                    clear_blockers=False,
                )
                base.update(overrides)
                return hloop.cmd_progress_record(SimpleNamespace(**base))

            record(status="in_progress", remaining_work="waiting on upstream fix")
            record(status="blocked", blocker=["upstream dependency is broken"])
            state = hloop.load_state(repo)
            self.assertEqual(
                state["requirements"]["REQ-001"]["progress"]["blockers"],
                ["upstream dependency is broken"],
            )

            # --clear-blockers and --blocker together is ambiguous and rejected.
            with self.assertRaises(hloop.HLoopError):
                record(
                    status="in_progress",
                    blocker=["still blocked"],
                    clear_blockers=True,
                )

            # A stale blocker from an old obstacle must not silently persist once
            # a different one replaces it.
            record(status="in_progress", blocker=["a different, newer blocker"])
            state = hloop.load_state(repo)
            self.assertEqual(
                state["requirements"]["REQ-001"]["progress"]["blockers"],
                ["a different, newer blocker"],
            )

            # Explicitly clearing blockers (rather than merely omitting --blocker)
            # is required to advance cleanly; omission alone must retain them.
            record(status="in_progress")
            state = hloop.load_state(repo)
            self.assertEqual(
                state["requirements"]["REQ-001"]["progress"]["blockers"],
                ["a different, newer blocker"],
            )
            record(status="in_progress", clear_blockers=True, remaining_work="")
            state = hloop.load_state(repo)
            self.assertEqual(state["requirements"]["REQ-001"]["progress"]["blockers"], [])
            self.assertEqual(state["requirements"]["REQ-001"]["progress"]["remaining_work"], "")

            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            record(
                status="implemented_unverified",
                evidence_kind="artifact",
                evidence_ref="results/REQ-001/result.md",
                verified_by="manager",
                head_sha=head_sha,
            )
            record(
                status="verified",
                evidence_kind="test",
                evidence_ref="targeted suite",
                verified_by="manager",
                head_sha=head_sha,
                result="passed",
            )
            state = hloop.load_state(repo)
            progress = state["requirements"]["REQ-001"]["progress"]
            self.assertEqual(progress["status"], "verified")
            self.assertEqual(progress["blockers"], [])

    def test_input_file_extract_and_accept_preserve_confirmation_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            source = root / "instruction.txt"
            source.write_text(
                "公開 API の互換性を検証可能な形で維持する。\n",
                encoding="utf-8",
            )
            hloop.cmd_input_record(
                SimpleNamespace(
                    repo=str(repo),
                    source="manager-file",
                    text=None,
                    file=str(source),
                )
            )
            hloop.cmd_requirements_extract(
                SimpleNamespace(
                    repo=str(repo),
                    id=None,
                    input=["U0001"],
                    acceptance=None,
                    priority="P1",
                    depends_on=None,
                    supersedes=None,
                )
            )
            draft_state = hloop.load_state(repo)
            self.assertEqual(draft_state["requirement_drafts"]["DRQ-001"]["status"], "draft")
            self.assertEqual(draft_state.get("requirements", {}), {})
            hloop.cmd_requirements_accept(
                SimpleNamespace(
                    repo=str(repo),
                    draft="DRQ-001",
                    id=None,
                    acceptance=["公開 API の互換性テストが通る"],
                    priority=None,
                    depends_on=None,
                    supersedes=None,
                )
            )
            accepted = hloop.load_state(repo)
            self.assertEqual(
                accepted["requirement_drafts"]["DRQ-001"]["accepted_requirement_id"],
                "REQ-001",
            )
            self.assertIn("REQ-001", accepted["requirements"])
            with self.assertRaises(hloop.HLoopError):
                hloop.cmd_requirements_accept(
                    SimpleNamespace(
                        repo=str(repo),
                        draft="DRQ-001",
                        id=None,
                        acceptance=None,
                        priority=None,
                        depends_on=None,
                        supersedes=None,
                    )
                )

    def test_outcome_projects_confirmed_review_fixes_risks_and_failed_returncode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            state = hloop.load_state(repo)
            state.update(
                {
                    "run_id": "run-outcome",
                    "goal": "outcome projection",
                    "integration_head_sha": head,
                    "completion_target_sha": head,
                    "last_validation": {
                        "head_sha": head,
                        "results": [
                            {
                                "command": "false",
                                "result": "failed:7",
                                "returncode": 7,
                                "log": "validation/failed.log",
                            }
                        ],
                    },
                    "max_reviewers": 1,
                    "needs_review": False,
                    "max_gap_auditors": 0,
                    "manager_qa_profile": "none",
                    "manager_qa_status": "not-required",
                    "tasks": {
                        "T009": {
                            "title": "Fix confirmed race",
                            "status": "merged",
                        }
                    },
                    "reviews": {
                        "R001": {
                            "status": "triaged",
                            "gate_status": "triaged",
                            "head_sha": head,
                            "closed_head_sha": head,
                            "confirmed_finding_fingerprints": ["sha256:" + "a" * 64],
                            "created_fix_tasks": ["T009"],
                            "verdict": "accepted-risk",
                            "triage_reason": "legacy client remains unsupported",
                            "review_path": "reviews/R001/FINAL.md",
                        }
                    },
                }
            )
            report = hloop.build_outcome_report(repo, state, kind="DRAFT")
            validation = next(
                gate for gate in report.gates if gate.name == "integration-validation"
            )
            self.assertEqual(validation.status, "failed")
            self.assertEqual(len(report.review_findings), 1)
            self.assertIn("T009", report.review_fixes[0])
            self.assertIn("legacy client", report.accepted_risks[0])
            rendered = hloop.hloop_reports.render_outcome_markdown(report)
            self.assertIn("Confirmed findings", rendered)
            self.assertIn("failed", rendered)

    def test_config_apply_is_explicit_dry_run_safe_and_idle_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["resolved_config"] = {
                "max_workers": 3,
                "session_cleanup": "archive",
                "worker": {"provider": "codex", "model": "auto", "effort": "auto"},
                "reviewer": {"provider": "codex", "model": "auto", "effort": "auto"},
            }
            hloop.save_state(repo, state)
            config_home = root / "config"
            config_home.mkdir()
            (config_home / "config.toml").write_text(
                """version = 1
[defaults]
max_workers = 2
session_cleanup = "none"
[defaults.worker]
provider = "claude"
model = "sonnet"
effort = "high"
""",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"HLOOP_CONFIG_HOME": str(config_home)}):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    hloop.cmd_config_apply(
                        SimpleNamespace(repo=str(repo), dry_run=True, apply=False)
                    )
                preview = json.loads(output.getvalue())
                self.assertTrue(preview["changed"])
                self.assertIsNone(hloop.load_state(repo).get("max_workers"))

                hloop.cmd_config_apply(
                    SimpleNamespace(repo=str(repo), dry_run=False, apply=True)
                )
            applied = hloop.load_state(repo)
            self.assertEqual(applied["max_workers"], 2)
            self.assertEqual(applied["session_cleanup"], "none")
            self.assertEqual(applied["worker_agent_provider"], "claude")
            self.assertEqual(applied["worker_agent_model"], "sonnet")

            applied["tasks"] = {"T001": {"status": "running"}}
            hloop.save_state(repo, applied)
            with mock.patch.dict(os.environ, {"HLOOP_CONFIG_HOME": str(config_home)}):
                with self.assertRaisesRegex(hloop.HLoopError, "roles are running"):
                    hloop.cmd_config_apply(
                        SimpleNamespace(repo=str(repo), dry_run=False, apply=True)
                    )

    def test_decision_artifacts_are_plain_language_and_block_only_affected_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["tasks"] = {
                "T001": {"status": "queued", "depends_on": []},
                "T002": {"status": "queued", "depends_on": []},
            }
            hloop.save_state(repo, state)
            with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(
                io.StringIO()
            ):
                hloop.cmd_decision_new(
                    SimpleNamespace(
                        repo=str(repo),
                        id="D001",
                        title="既存データの表示方法をどちらにするか",
                        decision_class="deferred-user",
                        affects=["T001"],
                        option=["従来表示を保つ", "新表示へ切り替える"],
                        tradeoff=["互換性を保てる", "操作を簡潔にできる"],
                        recommend_option="opt_1",
                        recommend_rationale="既存利用者への影響が小さいため",
                        source_finding=["scout:F001"],
                        created_from="specification-scout",
                    )
                )
            state = hloop.load_state(repo)
            scheduler = hloop.build_decision_scheduler(state)
            self.assertFalse(scheduler.loop_blocked)
            self.assertEqual(scheduler.decision_blocked_task_ids, ("T001",))
            self.assertIn("T002", scheduler.dispatchable_task_ids)
            question = (
                repo / hloop.LOOP_DIR / "decisions" / "D001" / "QUESTION.md"
            ).read_text()
            self.assertIn("# 判断のお願い", question)
            self.assertIn("## 選択肢", question)
            self.assertIn("この判断に依存しない作業は継続できます", question)

            hloop.cmd_decision_respond(
                SimpleNamespace(
                    repo=str(repo),
                    id="D001",
                    text="従来表示を保つ",
                    option="opt_1",
                    recommendation=None,
                    responded_by="user",
                )
            )
            response = (
                repo / hloop.LOOP_DIR / "decisions" / "D001" / "RESPONSE.md"
            ).read_text()
            self.assertIn("従来表示を保つ", response)

    def test_advisor_close_is_idempotent_and_consumes_reported_participants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            state["advice"] = {
                "A001": {
                    "status": "reported",
                    "gate_status": "reported",
                    "participants": [
                        {"participant_id": "P1", "status": "reported", "gate_status": "reported"}
                    ],
                }
            }
            hloop.save_state(repo, state)
            args = SimpleNamespace(
                repo=str(repo),
                advice_id="A001",
                verdict="no-action",
                reason="evidence is sufficient",
            )
            with mock.patch.object(
                hloop,
                "preflight_loop",
                side_effect=lambda checked_repo, **_kwargs: hloop.load_state(checked_repo),
            ):
                hloop.cmd_advisor_close(args)
                first = hloop.load_state(repo)
                first_time = first["advice"]["A001"]["triaged_at"]
                hloop.cmd_advisor_close(args)
            closed = hloop.load_state(repo)["advice"]["A001"]
            self.assertEqual(closed["triaged_at"], first_time)
            self.assertEqual(closed["participants"][0]["gate_status"], "consumed")

    def test_run_level_handoff_abandon_and_supersede_are_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()

            hloop.cmd_completion_handoff(
                SimpleNamespace(
                    repo=str(repo),
                    next_namespace="next-run",
                    head_sha=head,
                    reason="split remaining scope",
                )
            )
            self.assertEqual(hloop.load_state(repo)["phase"], "handoff")

            state = hloop.load_state(repo)
            state["phase"] = "dispatching"
            state["terminal_outcome"] = {}
            hloop.save_state(repo, state)
            hloop.cmd_completion_abandon(
                SimpleNamespace(repo=str(repo), reason="user cancelled the goal")
            )
            self.assertEqual(hloop.load_state(repo)["phase"], "abandoned_by_user")

            state = hloop.load_state(repo)
            state["phase"] = "done"
            state["final_target_sha"] = head
            state["terminal_outcome"] = {"status": "done", "target_sha": head}
            hloop.save_state(repo, state)
            hloop.cmd_completion_supersede(
                SimpleNamespace(
                    repo=str(repo),
                    head_sha=head,
                    reason="a successor run owns later evidence",
                    next_namespace="successor-run",
                )
            )
            superseded = hloop.load_state(repo)
            self.assertEqual(superseded["phase"], "superseded")
            self.assertEqual(
                superseded["terminal_outcome"]["next_namespace"], "successor-run"
            )
            inventory = hloop.collect_loop_inventory(repo, probe_panes=False)
            self.assertFalse(
                any(
                    "reviewer start" in action or "gap start" in action
                    for action in inventory["next_actions"]
                )
            )

    def test_blocked_outcome_is_explicit_and_outcome_report_rendered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.init_repo(root)
            state = hloop.load_state(repo)
            requirement = hloop.hloop_requirements.Requirement(
                requirement_id="REQ-001",
                source_inputs=("U0001",),
                acceptance=("user decision is resolved",),
                priority="P1",
                accepted_at="2026-07-15T00:00:00+00:00",
            ).to_record()
            requirement["progress"] = hloop.hloop_requirements.RequirementProgress(
                requirement_id="REQ-001",
                status="blocked",
                blockers=("user response",),
            ).to_record()
            state.update(
                {
                    "goal_id": "blocked-goal",
                    "run_id": "run-blocked",
                    "phase": "blocked_user_decision",
                    "requirements": {"REQ-001": requirement},
                    "tasks": {
                        "T009": {
                            "title": "Applied confirmed fix",
                            "status": "merged",
                        }
                    },
                    "reviews": {
                        "R001": {
                            "status": "triaged",
                            "gate_status": "triaged",
                            "confirmed_finding_fingerprints": ["sha256:" + "b" * 64],
                            "created_fix_tasks": ["T009"],
                            "verdict": "accepted-risk",
                            "triage_reason": "blocked run retains a known compatibility risk",
                        }
                    },
                }
            )
            hloop.save_state(repo, state)
            hloop.cmd_outcome_blocked(
                SimpleNamespace(repo=str(repo), reason="same user decision blocks all safe work")
            )
            blocked = hloop.load_state(repo)
            self.assertEqual(blocked["phase"], "blocked")
            report_path = repo / hloop.LOOP_DIR / "reports" / "BLOCKED.md"
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# Blocked Outcome", report)
            self.assertIn("same user decision blocks all safe work", report)
            self.assertIn("sha256:" + "b" * 64, report)
            self.assertIn("T009", report)
            self.assertIn("known compatibility risk", report)


if __name__ == "__main__":
    unittest.main()
