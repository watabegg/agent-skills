# Launcher and Gemini runtime

Maintain this skill in `~/agent-skills/skills/agy-writer`. It includes the Codex entrypoint, launcher, Gemini instructions, document tool policy, and character counter. Generated documents, conversations, credentials, and account settings stay outside this public repository.

From the repository, preview or apply this skill and its runtime:

```sh
python3 scripts/sync_installed_skills.py --only agy-writer --writer-runtime
python3 scripts/sync_installed_skills.py --only agy-writer --writer-runtime --apply
```

| Maintained source | Installed use |
|---|---|
| This skill directory | `~/.codex/skills/agy-writer`; Claude links to it |
| `scripts/agy-writer`, `scripts/write_ja.py` | `~/.local/bin/agy-writer`, `agent-write-ja` link to the Codex installation |
| `assets/GEMINI.md` | Copied to `~/agy-writer/GEMINI.md` |
| `assets/WORKFLOW.md` | Copied to `~/agy-writer/WORKFLOW.md` as a short usage pointer |
| `scripts/document_writer_policy.py`, `scripts/agy-doc-count` | Copied into `~/agy-writer/bin/` |
| Generated workspace hook | `~/agy-writer/.agents/hooks.json` |

Gemini reads the workspace `GEMINI.md` at startup. It contains both meaning-preservation and Japanese vocabulary preferences. The old `style-profile.md` path links to this file for existing callers. The automation no longer repeats those instructions in each prompt; `--profile <file>` supplies an optional request-specific addition.

`agy-writer` defaults to Gemini 3.8 Flash High with high effort. Its remaining arguments are passed literally to `agy`, so explicit `--model` or `--effort` options override the defaults. Set `AGY_WRITER_DIR` when using a separately installed workspace; changing this variable alone does not install its instructions or hook. `agent-write-ja` requests a new **Gemini** conversation for each writing, editing or repair call. This is separate from the continuing **Codex** meaning-review conversation. Interactive `agy-writer` follows the CLI's normal conversation selection.

The installer migrates only the known `document-writer-policy` registration from `~/.gemini/config/hooks.json` to the writer workspace and preserves unrelated hooks. An unexpected definition requires inspection. An existing legacy hook executable becomes a symlink so running sessions can still call it. General CLI permissions and authentication are separate account settings, outside this installation. The tool policy preserves document-only editing; it is not an OS sandbox.

Copies are intentional: the document policy rejects reading sensitive configuration directories, including `.codex`. Gemini's workspace instructions and helper files therefore do not link into that directory. Edit the source here, then sync; local changes to managed copies are replaced. Other files in the writer workspace are retained.

For isolated installation checks, the sync command also accepts `--codex-home`, `--claude-home`, `--writer-workspace`, `--writer-bin-dir`, and `--agy-config`.

Validate changes offline:

```sh
python3 skills/agy-writer/scripts/test_write_ja.py
python3 skills/agy-writer/scripts/test_chapters.py
python3 skills/agy-writer/scripts/test_runtime.py
```

Antigravity documents [workspace instructions](https://antigravity.google/docs/cli/best-practices/#write-a-codebase-rule-file) and [workspace hooks](https://antigravity.google/docs/hooks#configuration). After changing hook placement, `/hooks` shows which definitions the CLI loaded.
