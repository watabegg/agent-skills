---
name: shinshu-portal-auth
description: Open ACSU-authenticated Shinshu University eALPS, timetable, Campus Information System and SharePoint pages, and capture DOM/screenshot evidence with the bundled login script.
---

Use the bundled script for the user's normal login flow. Resolve `auth_skill_dir` to the absolute directory containing this SKILL.md; it is not the working directory. Use that absolute path in each shell invocation.

```sh
node "$auth_skill_dir/scripts/shinshu_portal_cdp.mjs" \
  --url 'https://timetable.ealps.shinshu-u.ac.jp/portal/#/' \
  --out-dir /tmp/shinshu-portal-probe
```

Repeat `--url` for multiple targets in one browser session. Use a unique evidence directory per run. Target the final portal page, not an intermediate login URL.

The last stdout line is a result JSON. `completed`/exit 0 means each requested origin reached a loaded page without a recognized login gate; this is navigation evidence, not independent proof of account identity. Per-page `status` is `ready`, `auth_required`, `timeout` or `layout_changed`. `needs_input`/exit 2 means authentication is still required; `failed`/exit 1 means navigation or execution failed. Inspect the reported artifacts before reporting success. Do not treat a screenshot's existence as successful login.

Credentials come from `--env-file`, `SHINSHU_AUTH_ENV`, `~/.config/shinshu-portal-auth/env`, then cwd `.env`; process environment values override the selected file. Required keys are `ACSU_LOGIN_ID`, `ACSU_LOGIN_PASSWORD`, `ACSU_LOGIN_MULTIFACTOR`. The optional `SHINSHU_MICROSOFT_UPN` overrides the derived university address. `CHROME_BIN` selects Chrome. Use `env.example` for key names and keep real values outside git, normally in the user config file with mode 600.

Use `--check-config` to diagnose missing configuration without opening a browser or printing values. `--help` is also offline. Ordinary runs already check configuration, so do not add a separate preflight to every task.

Read [site-dom.md](references/site-dom.md) only for site-specific inspection. Read [auth-flow.md](references/auth-flow.md) when diagnosing an unfamiliar login screen or repairing the script. A routine operator reports the failure and evidence; repairing the script is a separate task.

Keep credentials, cookies, redirect tokens and personal identifiers out of reports and repositories. Store evidence outside public repositories. Inspection is read-only; assignment submission, registration and account changes require the user's authorization for that action. Do not use Tampermonkey or bypass authentication. Report host, title and outcome instead of full OAuth redirect URLs.
