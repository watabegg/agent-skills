# ACSU Authentication Flow

## Observed Flow

The common ACSU flow for eALPS, timetable, Campus Information System, and SharePoint federation is:

1. Target site redirects to `gakunin.ealps.shinshu-u.ac.jp`.
2. ACSU login page appears with title `信州大学 ACSU`.
3. Fill the visible login ID text input and password input.
4. Click the visible submit button, usually labeled `次へ進む`.
5. WisePoint MFA page appears with title `WisePoint`.
6. Click image-password cells based on `ACSU_LOGIN_MULTIFACTOR`.
7. Click `#btnLogin`.
8. Shibboleth attribute-release page appears with title `送信情報の選択`.
9. Select one-time consent `#_shib_idp_doNotRememberConsent`.
10. Click submit `input[name="_eventId_proceed"]` or the visible `同意` button.
11. Return to the requested service.

## WisePoint Details

WisePoint uses 25 GIF images:

- Image URLs: `/idp/tenant/0/images/imatrix/i1.gif` through `i25.gif`.
- `i26.gif` is absent.
- The image alphabet is `ABCDEFGHIJKLMNOPRSTUVWXYZ`.
- `Q` is not present.

Map `ACSU_LOGIN_MULTIFACTOR` by uppercasing the string, removing spaces, and converting each character to its 1-based index in `ABCDEFGHIJKLMNOPRSTUVWXYZ`.

Examples:

- `A -> i1.gif`
- `P -> i16.gif`
- `R -> i17.gif`
- `Z -> i25.gif`

The clickable cells are `div.input_imgdiv_class`; identify them by:

```js
getComputedStyle(el).backgroundImage.includes('/imatrix/i17.gif')
```

Do not use DOM text for WisePoint letters. The visible letters are images and are not reliable text nodes.

## SharePoint Federation

`https://shinshuuniversity.sharepoint.com/sites/acsu?wa=wsignin1.0` first opens Microsoft sign-in at `login.microsoftonline.com`.

Observed path:

1. Fill the Microsoft account field with `SHINSHU_MICROSOFT_UPN`.
2. If that key is absent, derive `${ACSU_LOGIN_ID}@shinshu-u.ac.jp`.
3. Click `Next`.
4. The browser redirects to ACSU.
5. Complete the ACSU/WisePoint/Shibboleth flow.
6. The final page title becomes `ポータルサイトACSU - ホーム`.

If Microsoft keeps the browser on a password screen, fill the password only when it is clearly the user account password for this flow. Prefer waiting for ACSU federation first.

## Failure Modes

- `The selected pattern is wrong`: WisePoint mapping is wrong. Recheck that the `Q`-less 25-letter map is used.
- Still on ACSU login page after submit: credentials may be invalid, fields changed, or the login button selector failed.
- Stuck on `送信情報の選択`: click the one-time consent radio and `_eventId_proceed`.
- Stuck on Microsoft login: use `SHINSHU_MICROSOFT_UPN` explicitly; deriving from ACSU ID may not match every account.
