"""Unit tests for hloop_lib.config (herdr-dev-loop 0.5.0 config primitives)."""

import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hloop_lib import config  # noqa: E402


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
