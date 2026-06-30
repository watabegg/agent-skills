## Skills

A skill is a set of local instructions stored in a `SKILL.md` file.

### Available skills

- find-skills: Helps users discover and install agent skills when they ask questions like "how do I do X", "find a skill for X", "is there a skill that can...", or express interest in extending capabilities. This skill should be used when the user is looking for functionality that might exist as an installable skill. (file: skills/find-skills/SKILL.md)
- japanese-tech-writing: 日本語の質問回答と技術文書の文章規範。整形（読みやすい段落分け、引用ブロック、脚注、コラム記法）、段落と論証の構成（パラグラフライティング）、論証の厳密さ（ツッコミどころの除去）、読み手の負荷の管理、視点と語り、演出の抑制、LLM っぽい空句の禁止、冗長の排除を定める。日本語で回答するすべての質問、技術書の章・草稿・記事・解説文の執筆、推敲・リライト、設計、実装方針、コードレビュー、調査結果、作業報告を書くときに使用する。 (file: skills/japanese-tech-writing/SKILL.md)
- pencil-pencli: Safely inspect, edit, validate, and export Pencil design files through the Pencil headless CLI. Use whenever Codex needs to work with Pencil, PenCLI, design.pen, *.pen, *.pencli, encrypted Pencil design files, or any request to read/change/export a Pencil canvas or design file. Prefer `pencil interactive` headless mode over direct MCP tool calls; never inspect encrypted Pencil files with shell reads, grep, cat, sed, Python, or normal filesystem tools. (file: skills/pencil-pencli/SKILL.md)
- react-doctor: Diagnose and fix React codebase health issues. Use when reviewing React code, fixing performance problems, auditing security, or improving code quality. (file: skills/react-doctor/SKILL.md)

### How to use skills

- Discovery: The list above is the skills available in this repository.
- Trigger rules: If the user names a skill with `$SkillName` or plain text, or the task clearly matches a skill description above, use that skill for that turn.
- Missing/blocked: If a named skill is missing or the path cannot be read, say so briefly and continue with the best fallback.
- Progressive disclosure:
  1. Open the skill's `SKILL.md` and read only what is needed.
  2. Resolve relative paths relative to that skill directory first.
  3. Load additional files only when required.
  4. Prefer running bundled scripts over rewriting large command blocks.
