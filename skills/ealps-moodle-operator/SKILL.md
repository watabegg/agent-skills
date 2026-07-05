---
name: ealps-moodle-operator
description: "Operate Shinshu University eALPS Moodle course pages after ACSU login: inspect course sections, collect assignment and quiz requirements, snapshot Moodle DOM, upload assignment files through Moodle filemanager, fill and final-submit quizzes, and verify submission status. Use when Codex needs browser-based Moodle/eALPS task discovery, evidence collection, or explicitly authorized assignment/quiz submission."
---

# eALPS Moodle Operator

## Overview

Use this skill for Moodle-specific work inside eALPS after authentication: course page discovery, assignment and quiz inspection, Moodle filemanager uploads, quiz answer entry, final submission, and verification.

Use `shinshu-portal-auth` for login and generic portal access. This skill assumes that authentication path and adds Moodle/eALPS operational recipes.

## Safety Boundary

- Default to read-only inspection.
- Do not start a quiz attempt, submit a quiz, upload assignment files, delete files, or edit a saved submission unless the user explicitly asked for that final action in the current task.
- For broad requests such as "try operations", only run read-only probes unless a target course/activity and mutation are explicitly named.
- Keep credentials, cookies, long ACSU/Microsoft redirect URLs, and personal identifiers out of repo files and final reports.
- Keep generated screenshots and JSON evidence in `/tmp` unless the user asks for sanitized artifacts in a repo.

## Standard Workflow

1. Load `shinshu-portal-auth` and confirm the credential env file exists without printing values.
2. Use the auth script to open the course or activity URLs and save evidence in `/tmp`.
3. Inspect Moodle activity links and infer activity types from paths:
   - `mod/assign/view.php?id=...`
   - `mod/quiz/view.php?id=...`
   - `mod/resource/view.php?id=...`
   - `mod/url/view.php?id=...`
4. For assignment and quiz work, read [references/moodle-operations.md](references/moodle-operations.md).
5. Solve or prepare local files outside Moodle first. Run the relevant local validation before submitting.
6. If submission is authorized, use Moodle's own form/API flow:
   - online text: fill `onlinetext_editor[text]` and save
   - file submissions: upload to the current draft item id through `repository_ajax.php?action=upload`, then save
   - quizzes: fill fields by `name`, finish attempt, confirm the modal, then verify `review.php`
7. Verify from Moodle after every mutation. Trust completion status only after a fresh view/review page shows it.

## Useful Evidence Commands

Run the generic auth script from the sibling skill:

```bash
node ~/agent-skills/skills/shinshu-portal-auth/scripts/shinshu_portal_cdp.mjs \
  --url 'https://lms.ealps.shinshu-u.ac.jp/2026/t/course/view.php?id=202' \
  --out-dir /tmp/ealps-course-probe
```

Summarize saved JSON evidence:

```bash
python3 ~/agent-skills/skills/ealps-moodle-operator/scripts/summarize_ealps_evidence.py /tmp/ealps-course-probe
```

## Verification Rules

- Assignment submitted: activity view text includes `提出ステータス 評定のために提出済み` and the expected file name or online text summary.
- Quiz submitted: final URL is `mod/quiz/review.php?...` and review text includes `ステータス 終了`.
- File upload succeeded: repository upload JSON is HTTP 200 without `error`, then the assignment save returns to the activity view.
- If a file upload reports a file type error, retry only after checking the current `M.form_filemanager.init` options. Do not send an empty `accepted_types[]` field when Moodle's accepted type list is empty.

## Common Pitfalls

- Moodle section numbers and `course/view.php?section=N` can be offset from display section labels. Verify by reading headings and activity titles.
- Quiz summary buttons open a confirmation modal. Clicking the page button once is not final submission.
- Moodle filemanager draft `itemid`, `sesskey`, and `client_id` are session-specific. Extract them from the current edit form every time.
- Some assignment pages show `この状態で提出する` even after an upload failure. Check the view page for `評定のために提出済み`, not just the save button click.
