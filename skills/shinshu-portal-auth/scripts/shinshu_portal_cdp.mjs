#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';

const WISEPOINT_ALPHABET = 'ABCDEFGHIJKLMNOPRSTUVWXYZ';
const DEFAULT_ENV_FILE = path.join(os.homedir(), '.config', 'shinshu-portal-auth', 'env');

function usage() {
  console.log(`Usage:
  node scripts/shinshu_portal_cdp.mjs --url <url> [--url <url> ...] [--out-dir <dir>] [--env-file <file>]

Options:
  --url <url>        Target URL. Repeat for multiple pages in one browser session.
  --out-dir <dir>    Output directory for JSON summaries and screenshots. Default: /tmp/shinshu-portal-<timestamp>
  --env-file <file>  Env file containing ACSU_LOGIN_ID, ACSU_LOGIN_PASSWORD, ACSU_LOGIN_MULTIFACTOR.
                     Defaults to SHINSHU_AUTH_ENV, then ~/.config/shinshu-portal-auth/env, then .env.
  --timeout-ms <n>   Per-page auth/navigation loop timeout. Default: 160000.
  --headed           Do not use xvfb-run even when DISPLAY is absent.
  --help             Show this help.
`);
}

function parseArgs(argv) {
  const args = { urls: [], timeoutMs: 160000, headed: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--help' || a === '-h') args.help = true;
    else if (a === '--url') args.urls.push(argv[++i]);
    else if (a === '--out-dir') args.outDir = argv[++i];
    else if (a === '--env-file') args.envFile = argv[++i];
    else if (a === '--timeout-ms') args.timeoutMs = Number(argv[++i]);
    else if (a === '--headed') args.headed = true;
    else throw new Error(`Unknown argument: ${a}`);
  }
  return args;
}

function parseEnvFile(file) {
  const out = {};
  if (!file || !fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx < 0) continue;
    let value = line.slice(idx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    out[line.slice(0, idx).trim()] = value;
  }
  return out;
}

function loadSecrets(envFileArg) {
  const envFile = envFileArg
    || process.env.SHINSHU_AUTH_ENV
    || (fs.existsSync(DEFAULT_ENV_FILE) ? DEFAULT_ENV_FILE : null)
    || (fs.existsSync('.env') ? '.env' : null);
  const fileEnv = parseEnvFile(envFile);
  const env = { ...fileEnv, ...process.env };
  const missing = ['ACSU_LOGIN_ID', 'ACSU_LOGIN_PASSWORD', 'ACSU_LOGIN_MULTIFACTOR'].filter((k) => !env[k]);
  if (missing.length) {
    throw new Error(`Missing required secret keys: ${missing.join(', ')}`);
  }
  return {
    envFile,
    loginId: env.ACSU_LOGIN_ID,
    password: env.ACSU_LOGIN_PASSWORD,
    multifactor: env.ACSU_LOGIN_MULTIFACTOR,
    microsoftUpn: env.SHINSHU_MICROSOFT_UPN || `${env.ACSU_LOGIN_ID}@shinshu-u.ac.jp`,
  };
}

function which(cmd) {
  const r = spawnSync('bash', ['-lc', `command -v ${JSON.stringify(cmd)}`], { encoding: 'utf8' });
  return r.status === 0 ? r.stdout.trim() : '';
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

class CDP {
  constructor(url) {
    this.url = url;
    this.id = 0;
    this.pending = new Map();
  }

  async connect() {
    if (typeof WebSocket !== 'function') {
      throw new Error('This script requires Node.js with global WebSocket support.');
    }
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    };
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error('DevTools websocket error'));
    });
  }

  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`timeout ${method}`));
        }
      }, 15000);
    });
  }

  close() {
    try {
      this.ws.close();
    } catch {}
  }
}

function safeFilename(name) {
  return name.replace(/[^a-zA-Z0-9_.-]+/g, '_').slice(0, 80) || 'page';
}

function safeUrl(raw) {
  try {
    const u = new URL(raw);
    if (/login\.microsoftonline\.com|gakunin\.ealps\.shinshu-u\.ac\.jp/.test(u.hostname)) {
      u.search = '';
      u.hash = '';
    }
    return u.toString();
  } catch {
    return raw;
  }
}

async function runtimeValue(cdp, expression) {
  const res = await cdp.send('Runtime.evaluate', { expression, returnByValue: true });
  return res.result.value;
}

async function snapshot(cdp) {
  const res = await cdp.send('Runtime.evaluate', {
    returnByValue: true,
    expression: `(() => {
      const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
      const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
      const attr = (e, n) => e.getAttribute(n) || '';
      return {
        href: location.href,
        title: document.title,
        ready: document.readyState,
        text: compact(document.body && document.body.innerText || '').slice(0, 3000),
        headings: [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(visible)
          .map((e, i) => ({ i, tag: e.tagName, role: attr(e, 'role'), level: attr(e, 'aria-level'), text: compact(e.innerText || e.textContent).slice(0, 140) })).slice(0, 40),
        links: [...document.querySelectorAll('a')].filter(visible)
          .map((a, i) => ({ i, text: compact(a.innerText || a.title || attr(a, 'aria-label')).slice(0, 140), href: a.href, role: attr(a, 'role') })).filter((x) => x.text || x.href).slice(0, 100),
        buttons: [...document.querySelectorAll('button,input[type=submit],input[type=button],[role=button]')].filter(visible)
          .map((e, i) => ({ i, tag: e.tagName, text: compact(e.innerText || e.value || attr(e, 'aria-label') || e.id || e.name).slice(0, 140), type: e.type || '', name: e.name || '', id: e.id || '', role: attr(e, 'role'), disabled: !!e.disabled })).slice(0, 80),
        inputs: [...document.querySelectorAll('input,select,textarea')].filter(visible)
          .map((e, i) => ({ i, tag: e.tagName, type: e.type || '', name: e.name || '', id: e.id || '', placeholder: e.placeholder || '', label: (e.labels && e.labels[0] && compact(e.labels[0].innerText)) || '', autocomplete: e.autocomplete || '' })).slice(0, 70),
        iframes: [...document.querySelectorAll('iframe')]
          .map((e, i) => ({ i, src: e.src, title: e.title || '', name: e.name || '', visible: visible(e) })).slice(0, 40),
        tables: [...document.querySelectorAll('table')].filter(visible)
          .map((e, i) => ({ i, caption: compact(e.caption && e.caption.innerText || ''), rows: e.rows.length, text: compact(e.innerText).slice(0, 400) })).slice(0, 30),
        appHints: [...document.querySelectorAll('[id],[class],[data-region],[data-testid],[aria-label]')].filter(visible)
          .map((e, i) => ({ i, tag: e.tagName, id: e.id || '', cls: String(e.className || '').slice(0, 140), region: attr(e, 'data-region'), testid: attr(e, 'data-testid'), aria: attr(e, 'aria-label'), text: compact(e.innerText || e.textContent).slice(0, 200) }))
          .filter((x) => /course|section|calendar|timetable|portal|menu|nav|SharePoint|app|drawer|block|assignment|課題|時間割|お知らせ|掲示|Campus|学務|シラバス|授業|履修/i.test(Object.values(x).join(' '))).slice(0, 100),
      };
    })()`,
  });
  const value = res.result.value;
  value.href = safeUrl(value.href);
  value.links = (value.links || []).map((l) => ({ ...l, href: safeUrl(l.href) }));
  value.iframes = (value.iframes || []).map((f) => ({ ...f, src: safeUrl(f.src) }));
  return value;
}

async function clickContinue(cdp) {
  return runtimeValue(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const btn = [...document.querySelectorAll('button,input[type=submit],input[type=button],a')]
      .filter(visible).find((e) => /Continue|続行|次へ|進む/.test(e.innerText || e.value || e.id || e.name || ''));
    if (btn) { btn.click(); return 'clicked'; }
    if (/Loading Session Information/.test(document.title) && document.forms[0]) { document.forms[0].submit(); return 'submitted'; }
    return 'none';
  })()`);
}

async function fillAcsuLogin(cdp, secrets) {
  return runtimeValue(cdp, `((id, pw) => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const inputs = [...document.querySelectorAll('input')].filter(visible);
    const user = inputs.find((e) => ['text', 'email', ''].includes((e.type || '').toLowerCase()) && !e.disabled && !e.readOnly);
    const pass = inputs.find((e) => (e.type || '').toLowerCase() === 'password' && !e.disabled && !e.readOnly);
    const set = (el, val) => {
      el.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, val);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    if (!user || !pass) return 'fields_not_found';
    set(user, id);
    set(pass, pw);
    const submit = [...document.querySelectorAll('button,input[type=submit],input[type=button]')]
      .filter(visible).find((e) => /次へ|ログイン|login|sign in|continue|進む/i.test(e.innerText || e.value || e.id || e.name || ''))
      || document.querySelector('input[type=submit],button[type=submit]');
    if (submit) { submit.click(); return 'clicked_submit'; }
    if (pass.form) { pass.form.submit(); return 'submitted_form'; }
    return 'submit_not_found';
  })(${JSON.stringify(secrets.loginId)}, ${JSON.stringify(secrets.password)})`);
}

async function fillWisePoint(cdp, secrets) {
  return runtimeValue(cdp, `((secret, alphabet) => {
    const raw = String(secret).replace(/\\s+/g, '').toUpperCase();
    const nums = [...raw].map((ch) => alphabet.indexOf(ch) + 1);
    if (!nums.length || nums.some((n) => n < 1)) return { ok: false, reason: 'unsupported_char', count: 0 };
    const clicked = [];
    for (const n of nums) {
      const target = [...document.querySelectorAll('div.input_imgdiv_class')]
        .find((el) => getComputedStyle(el).backgroundImage.includes('/imatrix/i' + n + '.gif'));
      if (!target) return { ok: false, reason: 'image_not_found', needed: n, count: clicked.length };
      target.click();
      clicked.push(n);
    }
    const login = document.getElementById('btnLogin')
      || [...document.querySelectorAll('button,input[type=button],input[type=submit]')]
        .find((e) => /login|next|次へ|進む/i.test(e.innerText || e.value || e.id || ''));
    if (login) { login.click(); return { ok: true, action: 'clicked_login', count: clicked.length }; }
    return { ok: true, action: 'clicked_cells_no_login', count: clicked.length };
  })(${JSON.stringify(secrets.multifactor)}, ${JSON.stringify(WISEPOINT_ALPHABET)})`);
}

async function clickConsent(cdp) {
  return runtimeValue(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const once = document.getElementById('_shib_idp_doNotRememberConsent') || document.querySelector('input[name="_shib_idp_consentOptions"]');
    if (once) {
      once.checked = true;
      once.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const agree = [...document.querySelectorAll('button,input[type=submit],input[type=button]')]
      .filter(visible).find((e) => /同意|agree|proceed|送信/i.test(e.innerText || e.value || e.name || e.id || ''));
    if (agree) { agree.click(); return 'clicked_agree'; }
    const submit = document.querySelector('input[name="_eventId_proceed"],button[name="_eventId_proceed"]');
    if (submit) { submit.click(); return 'clicked_proceed'; }
    return 'none';
  })()`);
}

async function fillMicrosoftUser(cdp, secrets) {
  return runtimeValue(cdp, `((user) => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const input = [...document.querySelectorAll('input')].filter(visible)
      .find((e) => ['email', 'text', ''].includes((e.type || '').toLowerCase()) && !e.disabled && !e.readOnly);
    if (!input) return 'no_input';
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, user);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const next = document.getElementById('idSIButton9')
      || [...document.querySelectorAll('button,input[type=submit],input[type=button]')].filter(visible)
        .find((e) => /next|次へ|サインイン|sign in/i.test(e.innerText || e.value || e.id || ''));
    if (next) { next.click(); return 'clicked_next'; }
    if (input.form) { input.form.submit(); return 'submitted_form'; }
    return 'next_not_found';
  })(${JSON.stringify(secrets.microsoftUpn)})`);
}

async function fillMicrosoftPassword(cdp, secrets) {
  return runtimeValue(cdp, `((pw) => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const input = [...document.querySelectorAll('input')].filter(visible)
      .find((e) => (e.type || '').toLowerCase() === 'password' && !e.disabled && !e.readOnly);
    if (!input) return 'no_password';
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, pw);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const submit = document.getElementById('idSIButton9')
      || [...document.querySelectorAll('button,input[type=submit],input[type=button]')].filter(visible)
        .find((e) => /sign in|サインイン|next|次へ/i.test(e.innerText || e.value || e.id || ''));
    if (submit) { submit.click(); return 'clicked_password'; }
    if (input.form) { input.form.submit(); return 'submitted_form'; }
    return 'submit_not_found';
  })(${JSON.stringify(secrets.password)})`);
}

async function settleAuth(cdp, secrets, timeoutMs) {
  const events = [];
  const started = Date.now();
  let last = null;
  let acsuLoginTried = false;
  let wiseTried = false;
  let consentTried = false;
  let microsoftUserTried = false;
  let microsoftPasswordTried = false;

  while (Date.now() - started < timeoutMs) {
    await sleep(1000);
    try {
      last = await snapshot(cdp);
    } catch {
      continue;
    }

    const host = (() => {
      try { return new URL(last.href).hostname; } catch { return ''; }
    })();
    const mark = `${last.title} | ${host}`;
    if (events[events.length - 1] !== mark) events.push(mark);
    const hay = `${last.title} ${last.href} ${last.text}`;

    if (/Loading Session Information/.test(hay)) {
      const r = await clickContinue(cdp).catch(() => 'error');
      if (r !== 'none') events.push(`continue=${r}`);
      continue;
    }

    if (!microsoftUserTried && /login\.microsoftonline\.com/.test(last.href) && /Sign in|サインイン|account|メール|Email/i.test(hay)) {
      const r = await fillMicrosoftUser(cdp, secrets).catch(() => 'error');
      microsoftUserTried = true;
      events.push(`microsoft_user=${r}`);
      await sleep(5000);
      continue;
    }

    if (microsoftUserTried && !microsoftPasswordTried && /login\.microsoftonline\.com/.test(last.href) && (last.inputs || []).some((i) => i.type === 'password')) {
      const r = await fillMicrosoftPassword(cdp, secrets).catch(() => 'error');
      microsoftPasswordTried = true;
      events.push(`microsoft_password=${r}`);
      await sleep(6000);
      continue;
    }

    if (!acsuLoginTried && /ログインID|Authn\/External|信州大学 ACSU/.test(hay) && /パスワード/.test(hay)) {
      const r = await fillAcsuLogin(cdp, secrets).catch(() => 'error');
      acsuLoginTried = true;
      events.push(`acsu_login=${r}`);
      await sleep(2500);
      continue;
    }

    if (!wiseTried && /WisePoint|choose the image|image password/i.test(hay)) {
      const r = await fillWisePoint(cdp, secrets).catch(() => ({ ok: false, reason: 'error' }));
      wiseTried = true;
      events.push(`wisepoint=${r.ok ? r.action : r.reason}:count=${r.count ?? 0}`);
      await sleep(5000);
      continue;
    }

    if (!consentTried && /送信情報の選択|同意方法|_shib_idp/.test(hay)) {
      const r = await clickConsent(cdp).catch(() => 'error');
      consentTried = true;
      events.push(`consent=${r}`);
      await sleep(4000);
      continue;
    }

    if (!/login\.microsoftonline\.com|gakunin\.ealps\.shinshu-u\.ac\.jp|WisePoint|送信情報の選択|Loading Session Information/.test(hay) && last.ready === 'complete') {
      return { summary: last, events };
    }
  }

  return { summary: last || {}, events };
}

async function launchChrome(outDir, headed) {
  const chrome = process.env.CHROME_BIN || which('google-chrome') || which('chromium') || which('chromium-browser');
  if (!chrome) throw new Error('google-chrome/chromium was not found');
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'shinshu-home.'));
  const tmpProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'shinshu-chrome.'));
  const port = await freePort();
  const chromeArgs = [
    `--remote-debugging-port=${port}`,
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--disable-crashpad',
    '--disable-crash-reporter',
    '--disable-breakpad',
    '--no-first-run',
    '--no-default-browser-check',
    `--user-data-dir=${tmpProfile}`,
    '--window-size=1440,1000',
    'about:blank',
  ];
  const err = fs.openSync(path.join(outDir, 'chrome.err'), 'a');
  const useXvfb = !headed && !process.env.DISPLAY && which('xvfb-run');
  const command = useXvfb ? 'xvfb-run' : chrome;
  const args = useXvfb ? ['-a', chrome, ...chromeArgs] : chromeArgs;
  const child = spawn(command, args, {
    env: { ...process.env, HOME: tmpHome, XDG_CONFIG_HOME: path.join(tmpHome, '.config') },
    stdio: ['ignore', 'ignore', err],
  });
  return { child, port, tmpHome, tmpProfile };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    usage();
    return;
  }
  if (!args.urls.length) throw new Error('At least one --url is required');

  const secrets = loadSecrets(args.envFile);
  const outDir = args.outDir || path.join(os.tmpdir(), `shinshu-portal-${Date.now()}`);
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await launchChrome(outDir, args.headed);
  try {
    for (let i = 0; i < 80; i++) {
      try {
        await fetchJson(`http://127.0.0.1:${browser.port}/json/version`);
        break;
      } catch {
        await sleep(250);
      }
    }
    const page = await fetchJson(`http://127.0.0.1:${browser.port}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' });
    const cdp = new CDP(page.webSocketDebuggerUrl);
    await cdp.connect();
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');

    const results = [];
    for (const [idx, url] of args.urls.entries()) {
      const label = safeFilename(`${String(idx + 1).padStart(2, '0')}-${new URL(url).hostname}`);
      await cdp.send('Page.navigate', { url });
      await sleep(2000);
      const { summary, events } = await settleAuth(cdp, secrets, args.timeoutMs);
      await sleep(3000);
      const finalSummary = await snapshot(cdp).catch(() => summary);
      const screenshotPath = path.join(outDir, `${label}.png`);
      try {
        const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
        if (shot?.data) fs.writeFileSync(screenshotPath, Buffer.from(shot.data, 'base64'));
      } catch {}
      const result = {
        requestedUrl: url,
        finalUrl: finalSummary.href,
        finalTitle: finalSummary.title,
        events,
        summary: finalSummary,
        screenshot: fs.existsSync(screenshotPath) ? screenshotPath : null,
      };
      const jsonPath = path.join(outDir, `${label}.json`);
      fs.writeFileSync(jsonPath, JSON.stringify(result, null, 2));
      results.push({
        requestedUrl: url,
        finalUrl: finalSummary.href,
        finalTitle: finalSummary.title,
        json: jsonPath,
        screenshot: result.screenshot,
      });
      console.log(`DONE ${new URL(url).hostname} -> ${finalSummary.title || '(no title)'} | ${safeUrl(finalSummary.href || '')}`);
    }

    fs.writeFileSync(path.join(outDir, 'summary.json'), JSON.stringify(results, null, 2));
    console.log(`OUT_DIR ${outDir}`);
    cdp.close();
  } finally {
    try { browser.child.kill('SIGTERM'); } catch {}
    await sleep(700);
    fs.rmSync(browser.tmpHome, { recursive: true, force: true });
    fs.rmSync(browser.tmpProfile, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(`ERROR ${err.message}`);
  process.exit(1);
});
