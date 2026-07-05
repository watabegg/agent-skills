---
name: shinshu-portal-auth
description: Safely access Shinshu University ACSU-authenticated portals through browser automation. Use when Codex needs to open, inspect, QA, or collect DOM evidence from *.ealps.shinshu-u.ac.jp, lms.ealps.shinshu-u.ac.jp, timetable.ealps.shinshu-u.ac.jp, gakumu-web02.shinshu-u.ac.jp/campus, or shinshuuniversity.sharepoint.com pages, including ACSU login, WisePoint image-password MFA, Shibboleth consent, and SharePoint Microsoft-to-ACSU federation without relying on Tampermonkey.
---

# Shinshu Portal Auth

## Overview

Use this skill for read-only browser access to Shinshu University portals protected by ACSU, eALPS, Campus Information System, or Shinshu SharePoint authentication. Keep secrets out of logs, command lines, commits, and skill files.

Prefer the bundled CDP script over ad hoc Playwright snippets. It launches a temporary Chrome profile, logs in through ACSU, handles WisePoint image-password MFA without Tampermonkey, clicks one-time Shibboleth consent, captures a DOM summary, and removes the temporary profile.

## Secret Handling

Never commit or print credential values. Read them only from environment variables or a local env file outside git.

Required keys:

- `ACSU_LOGIN_ID`
- `ACSU_LOGIN_PASSWORD`
- `ACSU_LOGIN_MULTIFACTOR`

Optional keys:

- `SHINSHU_AUTH_ENV`: path to the local env file. If unset, the script reads `.env` in the current directory when present, plus process environment.
- `SHINSHU_MICROSOFT_UPN`: Microsoft/SharePoint sign-in UPN. If unset, the script derives `${ACSU_LOGIN_ID}@shinshu-u.ac.jp`.
- `CHROME_BIN`: override Chrome binary.

Recommended storage:

- Put secrets in `~/.config/shinshu-portal-auth/env` with mode `600`.
- Use `skills/shinshu-portal-auth/env.example` as the template; never put real values in the skill repo.
- Use `SHINSHU_AUTH_ENV` or `--env-file` only when you need a different local secret file.
- Keep secret files out of git. For public repositories, commit only example files with key names and dummy values.
- Do not copy Chrome `Login Data`, cookies, or profile state into a repo.
- Keep generated screenshots and JSON summaries in `/tmp` or another ignored path unless the user explicitly asks for sanitized evidence.

## Standard Workflow

1. Confirm the target URL is one of the expected domains or a direct ACSU/Microsoft login continuation for those domains.
2. Confirm the local secret source exists without printing values:
   ```bash
   node -e "const fs=require('fs'), os=require('os'), path=require('path'); const f=process.env.SHINSHU_AUTH_ENV||path.join(os.homedir(),'.config/shinshu-portal-auth/env'); if(fs.existsSync(f)) console.log('env file present:', f)"
   ```
3. Run the bundled script:
   ```bash
   node skills/shinshu-portal-auth/scripts/shinshu_portal_cdp.mjs \
     --url 'https://timetable.ealps.shinshu-u.ac.jp/portal/#/' \
     --out-dir /tmp/shinshu-portal-probe
   ```
4. Inspect the generated JSON summary and screenshot. Do not paste secrets or personal identifiers into final answers.
5. If the task needs site-specific DOM knowledge, read [references/site-dom.md](references/site-dom.md).
6. If login fails or a new ACSU screen appears, read [references/auth-flow.md](references/auth-flow.md) and update the script conservatively.

## Script Capabilities

`scripts/shinshu_portal_cdp.mjs` supports:

- Multiple `--url` flags in one browser session.
- ACSU login ID/password form submission.
- WisePoint MFA using `ACSU_LOGIN_MULTIFACTOR` and the 25-letter image map `ABCDEFGHIJKLMNOPRSTUVWXYZ` (`Q` is absent).
- Shibboleth attribute-release consent with the one-time option.
- Microsoft sign-in for `shinshuuniversity.sharepoint.com`, using `SHINSHU_MICROSOFT_UPN` or the derived UPN, then following federation into ACSU.
- JSON summaries of title, URL, text snippet, headings, links, buttons, inputs, iframes, tables, and app-specific hints.
- Per-target screenshots.

Run `node scripts/shinshu_portal_cdp.mjs --help` from the skill directory for options.

## Boundaries

- Default to read-only inspection. Do not submit assignments, change registration, alter passwords, publish SharePoint pages, or mutate account settings unless the user explicitly asks and the action is clearly reversible or intentionally final.
- Do not implement bypasses. This skill automates the user's normal login flow with user-provided credentials and MFA secret.
- Do not rely on Tampermonkey. Tampermonkey can be used as historical reference only; the maintained path is the bundled script.
- Do not expose full Microsoft OAuth URLs in reports; they can contain long state parameters. Report the host, page title, and outcome instead.
