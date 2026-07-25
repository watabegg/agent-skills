## Skills

A skill is a set of local instructions stored in a `SKILL.md` file.

### Available skills

- japanese-tech-writing: 日本語の長文・高負荷な技術文書を書くときの文章規範。Use for substantial Japanese deliverables such as design docs, spec Markdown, implementation plans, code review reports, investigation reports, technical articles, book chapters, and serious rewrites or proofreading. Do not use for casual chat, short status updates, simple Q&A, command results, or brief final reports. (file: skills/japanese-tech-writing/SKILL.md)
- pencil-pencli: Safely inspect, edit, validate, and export Pencil design files through the Pencil headless CLI. Use whenever Codex needs to work with Pencil, PenCLI, design.pen, *.pen, *.pencli, encrypted Pencil design files, or any request to read/change/export a Pencil canvas or design file. Prefer `pencil interactive` headless mode over direct MCP tool calls; never inspect encrypted Pencil files with shell reads, grep, cat, sed, Python, or normal filesystem tools. (file: skills/pencil-pencli/SKILL.md)
- shinshu-portal-auth: Safely access Shinshu University ACSU-authenticated portals through browser automation. Use when Codex needs to open, inspect, QA, or collect DOM evidence from *.ealps.shinshu-u.ac.jp, lms.ealps.shinshu-u.ac.jp, timetable.ealps.shinshu-u.ac.jp, gakumu-web02.shinshu-u.ac.jp/campus, or shinshuuniversity.sharepoint.com pages, including ACSU login, WisePoint image-password MFA, Shibboleth consent, and SharePoint Microsoft-to-ACSU federation without relying on Tampermonkey. (file: skills/shinshu-portal-auth/SKILL.md)
- ealps-moodle-operator: Operate Shinshu University eALPS Moodle course pages after ACSU login: inspect course sections, collect assignment and quiz requirements, snapshot Moodle DOM, upload assignment files through Moodle filemanager, fill and final-submit quizzes, and verify submission status. Use when Codex needs browser-based Moodle/eALPS task discovery, evidence collection, or explicitly authorized assignment/quiz submission. (file: skills/ealps-moodle-operator/SKILL.md)
- sync-teams-attendance: Synchronize Microsoft Teams attendance punch messages into the configured integrated Google Sheets attendance and invoicing workbook through temporary-profile Chrome automation, including Gmail-delivered Microsoft verification codes, punch pairing, deduplication, append-only writes to 勤怠明細, workbook-contract checks, and post-write verification. Use when Codex needs to collect start, pause, resume, end, or clock-out messages, update this workbook safely, or reason about its 設定, 月次集計, 請求書, PDF export, and 請求履歴 boundaries. (file: skills/sync-teams-attendance/SKILL.md)
- codex-review-multi-v2: Use when the user asks for code review, PR diff review, uncommitted diff review, repository-wide audit, crv2, multi-reviewer Codex review, or post-implementation review with Codex reviewer lanes. (file: skills/codex-review-multi-v2/SKILL.md)
- herdr-dev-loop: Orchestrate a bounded Herdr-managed multi-agent coding loop with Codex or Claude Worker, Gap Auditor, Reviewer, and opt-in Advisor panes. Use inside Herdr to persist namespaced state, isolate attempts in git worktrees, recover merge transactions, validate completion gates, and finish explicitly. (file: skills/herdr-dev-loop/SKILL.md)

### How to use skills

- Discovery: The list above is the skills available in this repository.
- Trigger rules: If the user names a skill with `$SkillName` or plain text, or the task clearly matches a skill description above, use that skill for that turn.
- Missing/blocked: If a named skill is missing or the path cannot be read, say so briefly and continue with the best fallback.
- Progressive disclosure:
  1. Open the skill's `SKILL.md` and read only what is needed.
  2. Resolve relative paths relative to that skill directory first.
  3. Load additional files only when required.
  4. Prefer running bundled scripts over rewriting large command blocks.
