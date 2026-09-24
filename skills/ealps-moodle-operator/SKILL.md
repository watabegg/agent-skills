---
name: ealps-moodle-operator
description: Inspect eALPS Moodle courses and submission evidence, or perform explicitly authorized assignment uploads and quiz submissions after ACSU login.
---

Choose saved-evidence analysis or live Moodle operation. Saved evidence needs no login skill, browser or credentials.

Resolve `moodle_skill_dir` to the absolute directory containing this SKILL.md. Summarize a saved JSON file or directory with:

```sh
python3 "$moodle_skill_dir/scripts/summarize_ealps_evidence.py" /tmp/ealps-evidence --verify
```

Run the bundled command directly for routine summaries; read its implementation only when diagnosing or changing it. Read raw JSON only if the returned rows lack information the user needs.

`--verify` returns `{status, reason, changed, artifacts, rows}`. Rows include `state` such as `submitted`, `draft`, `not_submitted`, `completed`, `in_progress` or `unknown`. Exit 0 means the evidence was summarized, not that every activity was submitted: inspect each row's `ok`. Exit 2 means `missing_input` or `empty_evidence`, so the submission state is unknown. Exit 1 means invalid evidence. `--json` retains the older row-array interface; use `--verify` for routine agent operation. Saved evidence establishes only the captured state, not the live site's latest state.

For live work, use the available `shinshu-portal-auth` skill to collect the target course/activity pages. Read [moodle-operations.md](references/moodle-operations.md) before assignment/quiz operation. Discover activities from the current page: assignments use `mod/assign/view.php`, quizzes `mod/quiz/view.php`; check headings because section numbers can differ from display labels.

Prepare and validate the requested local files before upload. Use Moodle's current form/API flow and extract fresh draft itemid, sesskey, client_id and field names. These values belong to the current session. A quiz summary button may open a confirmation modal; it is not itself final submission.

After an authorized mutation, refresh the activity view/review page:

- Assignment: `提出ステータス 評定のために提出済み` plus the expected file name or online-text summary.
- Quiz: `mod/quiz/review.php` plus `ステータス 終了`.
- Upload: successful HTTP response without an upload error, followed by a saved assignment and the above submission evidence. A save/submit button alone proves nothing.

Use existing explicit authorization for the specified upload, edit, quiz attempt or final submission. Broad requests to inspect or try operations do not authorize those mutations. Keep credentials, cookies, redirect URLs and personal evidence outside public repositories and reports. If the page layout or submission evidence is unclear, report the unresolved state before attempting further mutations.
