# Moodle/eALPS Operations

## Course And Section Inspection

Open the course page with `shinshu-portal-auth` and inspect visible links. Activity URLs encode the Moodle module type:

- assignment: `/mod/assign/view.php?id=<cmid>`
- quiz: `/mod/quiz/view.php?id=<cmid>`
- resource: `/mod/resource/view.php?id=<cmid>`
- URL activity: `/mod/url/view.php?id=<cmid>`

When a user asks for "23回目" or similar, do not trust the URL section number alone. Open the page and verify visible headings, activity titles, and surrounding text.

## Assignment Forms

Assignment edit pages normally use:

```text
/mod/assign/view.php?id=<cmid>&action=editsubmission
```

Common hidden fields:

- `id`: course module id
- `action=savesubmission`
- `sesskey`: current Moodle session key
- `files_filemanager`: draft file area item id

Online text assignments expose `textarea[name="onlinetext_editor[text]"]`. Fill it with the desired HTML, update any visible Atto editor when present, then click `この状態で提出する`.

File assignments expose Moodle filemanager instead of an online text textarea. Extract the current filemanager options from the script call:

```js
M.form_filemanager.init(Y, { ... })
```

Important fields:

- `itemid`: draft file area id
- `client_id`: current filemanager client id
- `context.id`: Moodle context id
- `author`: author string Moodle will attach
- `filepicker.repositories`: find the repository with `type: "upload"`; this is the `repo_id`
- `filepicker.accepted_types`: accepted file types; if empty, do not append `accepted_types[]`

Upload through Moodle's standard endpoint before saving the assignment:

```js
const formData = new FormData();
formData.append("repo_upload_file", new File([content], filename), filename);
formData.append("sesskey", M.cfg.sesskey);
formData.append("repo_id", uploadRepository.id);
formData.append("itemid", itemid);
formData.append("savepath", "/");
formData.append("title", filename);
formData.append("overwrite", "1");
formData.append("author", author);
formData.append("ctx_id", contextId);
for (const type of acceptedTypes) formData.append("accepted_types[]", type);

await fetch(`${M.cfg.wwwroot}/repository/repository_ajax.php?action=upload`, {
  method: "POST",
  body: formData,
  credentials: "same-origin",
});
```

After a successful upload, click `この状態で提出する`. Verify on the assignment view page that the text includes `提出ステータス 評定のために提出済み` and the expected file name.

## Quiz Attempts

Only start or submit quiz attempts when explicitly authorized. For existing attempts, use the current attempt URL or summary URL:

```text
/mod/quiz/attempt.php?attempt=<attemptid>&cmid=<cmid>
/mod/quiz/summary.php?attempt=<attemptid>&cmid=<cmid>
```

Fill answers by exact input/select `name`, for example:

```js
document.querySelector('[name="q33769:1_sub1_answer"]').value = "4/45";
```

For select boxes, match the visible option text first, then fall back to raw option value.

Final submission requires two actions:

1. Click `テストを終了する ...` or navigate to the summary page.
2. Click `すべての解答を送信して終了する`, then click the same action in the confirmation modal.

Do not consider the quiz submitted while still on `summary.php`. Verify that the final URL is `review.php` and the review text contains `ステータス 終了`.

## Evidence To Keep

For each mutated activity, save JSON and screenshot evidence in `/tmp`:

- assignment result: final URL, `filled` result, save button text, status text
- quiz result: fill result, finish button text, modal confirmation text, final review URL
- summary file: one row per activity with output JSON path

Use `scripts/summarize_ealps_evidence.py` to produce a compact status table from those JSON files.
