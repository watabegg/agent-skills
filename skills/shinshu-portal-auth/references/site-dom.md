# Site DOM Notes

These notes summarize observed structures. They are not a source of personal data or current course facts; verify live pages before concluding.

## eALPS LMS

Typical URL:

- `https://lms.ealps.shinshu-u.ac.jp/2026/t/course/view.php?id=...`

Observed structure:

- Moodle Boost-style page.
- Top nav has links such as `HOME`, `ダッシュボード`, `マイコース`.
- Course index/drawer buttons include visible labels like `コースインデックスを開く` and `ブロックドロワを開く`.
- Section headings are `h3`/heading text such as `一般`, `01`, `02`, ...
- Section expand/collapse controls often have IDs like `collapsesectionid...`.
- Activity links commonly use Moodle module paths:
  - `/mod/assign/view.php?id=...`
  - `/mod/quiz/view.php?id=...`
  - `/mod/lti/view.php?id=...`
  - `/mod/url/view.php?id=...`
  - `/mod/forum/view.php?id=...`

Read-only operations:

- Expand sections before extracting assignments or quizzes.
- Prefer activity URLs and visible labels over fragile CSS positions.
- For due dates and ToDo markers, inspect visible text near the activity link and Moodle activity type.

## eALPS Timetable Portal

Typical URL:

- `https://timetable.ealps.shinshu-u.ac.jp/portal/#/`

Observed structure:

- SPA root is `#app`.
- Notice blocks are rendered as BootstrapVue-style tables.
- Timetable is a visible table with weekday columns and period rows.
- Course names link directly to eALPS LMS course URLs.
- `コース情報` entries are hash links or SPA actions.
- Buttons include pagination labels such as `1`, `2`, `3`, `4`, `次へ`.
- Term filters include `前期`, `後期`, `通年`, `不定期`.
- A year selector is a visible `select`.

Read-only operations:

- Wait for `#app` and the timetable table to render.
- Parse tables for notices and timetable data.
- Click a course-name link to open the LMS course page.
- Click term buttons by visible text, then re-snapshot after the table changes.

## Shinshu SharePoint ACSU Site

Typical URL:

- `https://shinshuuniversity.sharepoint.com/sites/acsu?wa=wsignin1.0`

Observed structure after Microsoft-to-ACSU federation:

- Page title: `ポータルサイトACSU - ホーム`.
- Modern SharePoint page with Office header and site nav.
- Top/site nav links include `Home`, `パスワード変更`, `多要素パスワード設定`, `パスワードリマインダ設定`, `多要素認証システム利用方法`, `パスワードリマインダ利用方法`, `アンケート`.
- Quick links include targets such as Gmail, eALPS, Campus Information System, reports, seating registration, inquiries, and J-PEAKS.

Read-only operations:

- Use visible link text and `href` when available.
- Some SharePoint quick links may be rendered as buttons or hydrated links; if `href` is empty, click by exact visible text and wait for navigation.
- Avoid `Publish`, `Build`, page editing, password setting, or account-setting actions unless explicitly requested.
- Do not report personal profile text or user names.

## Campus Information System

Typical URL:

- `https://gakumu-web02.shinshu-u.ac.jp/campus/portal`

Observed structure:

- SPA root is `#app`.
- Header text includes `CAMPUS INFO SYSTEM`, language radios `日本語` and `English`, and `My Page`.
- Left menu is Element UI style:
  - `.el-aside.sideMenu`
  - `ul.el-menu`
  - category toggles backed by checkboxes like `toggle1`, `toggle2`, ...
- Search input placeholder is `Search`.
- Major menu groups include `Course Information`, `Syllabus`, `Course Registration & Academic Record`, `Course Lottery`, `Attendance Management`, `Course Feedback Survey`, `Grades`, `Announcements`, `Student Life Information`, `Admission/Tuition Fees & Scholarships`, `Thesis`, `Job Information`, `Career-Related Information`, `Portal`.
- Main dashboard can contain headings such as `Current Course Registration`, `Announcements`, and bookmarked job information.
- Timetable/course tables are visible HTML tables.

Read-only operations:

- Click side-menu items by visible text.
- Use the `Search` input for navigation only after the app is loaded.
- Parse visible tables for timetable or announcement summaries.
- Treat course registration, academic record edits, lottery entries, surveys, and account-affecting flows as mutation-capable; do not submit forms without explicit user instruction.
