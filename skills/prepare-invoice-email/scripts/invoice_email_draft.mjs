#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const CONFIG_PATH = path.join(os.homedir(), '.config', 'sync-teams-attendance', 'config.json');
const CHROME_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36';
const CACHE_DIR_NAMES = new Set(['Cache', 'Code Cache', 'DawnCache', 'GPUCache', 'GrShaderCache', 'GraphiteDawnCache', 'ShaderCache', 'Service Worker']);

const argv = process.argv.slice(2);
const mode = argv.includes('--export') ? 'export'
  : argv.includes('--verify-pdf') ? 'verify'
    : argv.includes('--draft') ? 'draft'
      : argv.includes('--self-test') ? 'self-test'
        : '';
const argValue = (name) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : undefined;
const month = argValue('--month');
const pdfPath = argValue('--pdf');
const configPath = path.resolve(argValue('--config') || CONFIG_PATH);
if (!mode || (mode !== 'self-test' && (!/^\d{4}-\d{2}$/.test(month || '') || !pdfPath || !path.isAbsolute(pdfPath)))) {
  throw new Error('Usage: node invoice_email_draft.mjs (--export|--verify-pdf|--draft) --month YYYY-MM --pdf /absolute/path.pdf [--config /path/config.json]\n       node invoice_email_draft.mjs --self-test');
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function which(command) {
  const result = spawnSync('bash', ['-lc', `command -v ${JSON.stringify(command)}`], { encoding: 'utf8' });
  return result.status === 0 ? result.stdout.trim() : '';
}
function readConfig() {
  if (!fs.existsSync(configPath)) throw new Error('Config file was not found');
  if ((fs.statSync(configPath).mode & 0o077) !== 0) throw new Error('Config permissions are too broad; use mode 600');
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  if (!config.chrome?.userDataDir || !config.chrome?.profileDirectory || !config.accounts?.googleEmail || !config.spreadsheet?.url) {
    throw new Error('Config requires chrome.userDataDir, chrome.profileDirectory, accounts.googleEmail, and spreadsheet.url');
  }
  const sheet = new URL(config.spreadsheet.url);
  if (sheet.protocol !== 'https:' || sheet.hostname !== 'docs.google.com' || !sheet.pathname.includes('/spreadsheets/d/')) {
    throw new Error('spreadsheet.url must be a docs.google.com/spreadsheets URL');
  }
  return config;
}
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}
async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

class CDP {
  constructor(url) { this.url = url; this.nextId = 0; this.pending = new Map(); }
  async connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const pending = this.pending.get(message.id);
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(`${pending.method}: ${JSON.stringify(message.error)}`));
      else pending.resolve(message.result);
    };
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error('Chrome DevTools websocket connection failed'));
    });
  }
  send(method, params = {}, timeoutMs = 30000) {
    const id = ++this.nextId;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`Chrome DevTools timeout: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, method, timer });
    });
  }
  close() { try { this.ws.close(); } catch {} }
}

async function evaluate(cdp, expression, awaitPromise = false) {
  const response = await cdp.send('Runtime.evaluate', {
    expression, awaitPromise, returnByValue: true, userGesture: true,
  });
  if (response.exceptionDetails) {
    const detail = response.exceptionDetails.exception?.description || response.exceptionDetails.text;
    throw new Error(`Browser evaluation failed: ${detail}`);
  }
  return response.result.value;
}
async function waitFor(cdp, predicate, { timeoutMs = 60000, intervalMs = 750, description = 'condition' } = {}) {
  const started = Date.now();
  let last;
  while (Date.now() - started < timeoutMs) {
    last = await predicate().catch((error) => ({ error: error.message }));
    if (last && !last.error) return last;
    await sleep(intervalMs);
  }
  throw new Error(`Timed out waiting for ${description}${last?.error ? `: ${last.error}` : ''}`);
}

function profileFilter(source) {
  const name = path.basename(source);
  return !CACHE_DIR_NAMES.has(name) && !/^(blob_storage|Crashpad|BrowserMetrics|optimization_guide_model_store)$/.test(name);
}
async function launchChrome(config) {
  const chrome = process.env.CHROME_BIN || which('google-chrome') || which('chromium') || which('chromium-browser');
  if (!chrome) throw new Error('Chrome not found');
  const sourceRoot = path.resolve(config.chrome.userDataDir);
  const sourceProfile = path.join(sourceRoot, config.chrome.profileDirectory);
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-workflow-chrome.'));
  const tempProfile = path.join(tempRoot, config.chrome.profileDirectory);
  fs.mkdirSync(tempProfile, { recursive: true, mode: 0o700 });
  const localState = path.join(sourceRoot, 'Local State');
  if (fs.existsSync(localState)) fs.copyFileSync(localState, path.join(tempRoot, 'Local State'));
  fs.cpSync(sourceProfile, tempProfile, { recursive: true, filter: profileFilter, force: true });
  const port = await freePort();
  const logPath = path.join(os.tmpdir(), `invoice-workflow-chrome-${process.pid}.log`);
  const errorFd = fs.openSync(logPath, 'a');
  const runtimeDir = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`;
  const child = spawn(chrome, [
    `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1', '--no-sandbox',
    '--disable-gpu', '--disable-dev-shm-usage', '--disable-crashpad', '--disable-crash-reporter',
    '--disable-breakpad', '--disable-background-networking', '--no-first-run', '--no-default-browser-check',
    `--user-data-dir=${tempRoot}`, `--profile-directory=${config.chrome.profileDirectory}`,
    '--window-size=1440,1000', '--headless=new', 'about:blank',
  ], {
    env: {
      ...process.env,
      XDG_RUNTIME_DIR: runtimeDir,
      DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS || `unix:path=${runtimeDir}/bus`,
      GNOME_KEYRING_CONTROL: process.env.GNOME_KEYRING_CONTROL || path.join(runtimeDir, 'keyring'),
    },
    stdio: ['ignore', 'ignore', errorFd],
  });
  return { child, errorFd, logPath, port, tempRoot };
}
async function stopChrome(browser) {
  try { browser.child.kill('SIGTERM'); } catch {}
  await sleep(700);
  try { fs.closeSync(browser.errorFd); } catch {}
  fs.rmSync(browser.tempRoot, { recursive: true, force: true });
  fs.rmSync(browser.logPath, { force: true });
}
async function connectPage(browser) {
  await waitFor(null, async () => {
    await fetchJson(`http://127.0.0.1:${browser.port}/json/version`);
    return true;
  }, { timeoutMs: 30000, intervalMs: 250, description: 'Chrome startup' });
  const page = await fetchJson(`http://127.0.0.1:${browser.port}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' });
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Network.enable');
  await cdp.send('DOM.enable');
  await cdp.send('Network.setUserAgentOverride', { userAgent: CHROME_UA, platform: 'Linux x86_64' });
  return cdp;
}
async function navigate(cdp, url) {
  await cdp.send('Page.navigate', { url }, 60000);
  await waitFor(cdp, async () => {
    const state = await evaluate(cdp, 'document.readyState');
    return state === 'complete' || state === 'interactive' ? state : null;
  }, { timeoutMs: 60000, description: `navigation to ${new URL(url).hostname}` });
}
async function press(cdp, key, code, modifiers = 0, virtualKey = 0) {
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, modifiers, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, modifiers, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey });
}

function spreadsheetId(url) {
  const match = new URL(url).pathname.match(/\/spreadsheets\/d\/([^/]+)/);
  if (!match) throw new Error('Spreadsheet id missing');
  return match[1];
}
async function selectSheetRange(cdp, range) {
  const found = await evaluate(cdp, `(() => {
    const box = document.querySelector('#t-name-box, input[aria-label*="Name box"], input[aria-label*="名前ボックス"]');
    if (!box) return false;
    box.focus(); box.select(); return true;
  })()`);
  if (!found) throw new Error('Google Sheets name box was not found');
  await cdp.send('Input.insertText', { text: range });
  await press(cdp, 'Enter', 'Enter', 0, 13);
  await sleep(600);
}
async function readSelectedCell(cdp, range) {
  await selectSheetRange(cdp, range);
  await press(cdp, 'c', 'KeyC', 2, 67);
  await sleep(400);
  return String(await evaluate(cdp, 'navigator.clipboard.readText()', true)).trim();
}
async function writeSelectedCell(cdp, range, value) {
  await selectSheetRange(cdp, range);
  await evaluate(cdp, `navigator.clipboard.writeText(${JSON.stringify(value)})`, true);
  await press(cdp, 'v', 'KeyV', 2, 86);
  await sleep(4500);
}
async function activateInvoiceSheet(cdp) {
  const result = await waitFor(cdp, async () => evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const compact = (s) => String(s || '').replace(/\s+/g, ' ').trim();
    const candidates = [...document.querySelectorAll('.docs-sheet-tab, [role="tab"]')].filter(visible);
    const tab = candidates.find((e) => compact(e.innerText || e.textContent || e.getAttribute('aria-label')) === '請求書');
    if (!tab) return null;
    const target = tab.querySelector('.docs-sheet-tab-name') || tab;
    const rect = target.getBoundingClientRect();
    return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
  })()`), { timeoutMs: 60000, intervalMs: 1000, description: '請求書 sheet tab' });
  if (!result) throw new Error('請求書 sheet tab not found');
  await cdp.send('Input.dispatchMouseEvent', {type: 'mousePressed', x: result.x, y: result.y, button: 'left', clickCount: 1});
  await cdp.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: result.x, y: result.y, button: 'left', clickCount: 1});
  await sleep(2500);
  return evaluate(cdp, `(() => {
    const compact = (s) => String(s || '').replace(/\s+/g, ' ').trim();
    const tab = [...document.querySelectorAll('.docs-sheet-tab, [role="tab"]')]
      .find((e) => compact(e.innerText || e.textContent || e.getAttribute('aria-label')) === '請求書');
    const attrs = Object.fromEntries([...tab.attributes].map((a) => [a.name, a.value]));
    return { href: location.href, attrs };
  })()`);
}
function invoiceGid(tabInfo) {
  const url = new URL(tabInfo.href);
  const fromUrl = new URLSearchParams(url.hash.replace(/^#/, '')).get('gid') || url.searchParams.get('gid');
  if (fromUrl) return fromUrl;
  for (const [key, value] of Object.entries(tabInfo.attrs || {})) {
    if (/gid|sheet.*id|id/i.test(key) && /^\d+$/.test(value)) return value;
    const match = String(value).match(/(?:gid=|sheet-tab-)(\d+)/);
    if (match) return match[1];
  }
  throw new Error('Could not identify 請求書 gid');
}
async function loadResource(cdp, url) {
  const { frameTree } = await cdp.send('Page.getFrameTree');
  const { resource } = await cdp.send('Network.loadNetworkResource', {
    frameId: frameTree.frame.id,
    url,
    options: { disableCache: true, includeCredentials: true },
  }, 120000);
  if (!resource.success || resource.httpStatusCode !== 200 || !resource.stream) {
    throw new Error(`Resource load failed: status=${resource.httpStatusCode || 'unknown'} net=${resource.netErrorName || 'unknown'}`);
  }
  const chunks = [];
  try {
    while (true) {
      const part = await cdp.send('IO.read', { handle: resource.stream }, 120000);
      chunks.push(part.base64Encoded ? Buffer.from(part.data, 'base64') : Buffer.from(part.data));
      if (part.eof) break;
    }
  } finally {
    await cdp.send('IO.close', { handle: resource.stream }).catch(() => {});
  }
  return { data: Buffer.concat(chunks), headers: resource.headers || {} };
}

async function exportInvoice(cdp, config) {
  await navigate(cdp, config.spreadsheet.url);
  await sleep(4500);
  const state = await evaluate(cdp, `({title: document.title, text: String(document.body?.innerText || '').slice(0, 1000)})`);
  if (/sign in|ログイン|アカウントを選択/i.test(`${state.title} ${state.text}`)) throw new Error('Google authentication is missing');
  const origin = new URL(config.spreadsheet.url).origin;
  await cdp.send('Browser.grantPermissions', { origin, permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] }).catch(async () => {
    await cdp.send('Browser.grantPermissions', { origin, permissions: ['clipboardReadWrite'] });
  });
  const tabInfo = await activateInvoiceSheet(cdp);
  const gid = invoiceGid(tabInfo);
  const originalMonth = await readSelectedCell(cdp, 'U2');
  let changed = false;
  try {
    if (originalMonth !== month) {
      await writeSelectedCell(cdp, 'U2', month);
      changed = true;
    }
    const selectedMonth = await readSelectedCell(cdp, 'U2');
    if (selectedMonth !== month) throw new Error(`Invoice month did not update (got ${JSON.stringify(selectedMonth)})`);
    await sleep(3500);
    const id = spreadsheetId(config.spreadsheet.url);
    const params = new URLSearchParams({
      format: 'pdf', size: 'A4', portrait: 'true', scale: '4', fitw: 'true',
      sheetnames: 'false', printtitle: 'false', pagenum: 'UNDEFINED', gridlines: 'false',
      fzr: 'false', include_notes: 'false', gid, range: 'A1:R34',
    });
    const resource = await loadResource(cdp, `https://docs.google.com/spreadsheets/d/${id}/export?${params}`);
    const contentType = resource.headers['content-type'] || resource.headers['Content-Type'] || '';
    if (!/application\/pdf/i.test(contentType) || !resource.data.subarray(0, 5).equals(Buffer.from('%PDF-'))) {
      throw new Error(`Invoice export was not a PDF (type=${contentType || 'unknown'})`);
    }
    fs.writeFileSync(pdfPath, resource.data, { mode: 0o600 });
    console.log(`EXPORT_OK month=${month} bytes=${resource.data.length}`);
  } finally {
    if (changed) {
      await writeSelectedCell(cdp, 'U2', originalMonth);
      const restored = await readSelectedCell(cdp, 'U2');
      if (restored !== originalMonth) throw new Error('Invoice month restoration failed');
      console.log('MONTH_RESTORED true');
    }
  }
}

function verifyInvoicePdf(targetPath, targetMonth) {
  if (!fs.existsSync(targetPath)) throw new Error('Invoice PDF is missing');
  const pdfinfo = which('pdfinfo');
  const pdftotext = which('pdftotext');
  if (!pdfinfo || !pdftotext) throw new Error('pdfinfo and pdftotext are required to verify the invoice PDF');
  const info = spawnSync(pdfinfo, [targetPath], { encoding: 'utf8' });
  if (info.status !== 0) throw new Error('pdfinfo could not read the invoice PDF');
  const pages = Number(info.stdout.match(/^Pages:\s+(\d+)/m)?.[1]);
  const sizeLine = info.stdout.match(/^Page size:\s+(.+)$/m)?.[1] || '';
  const isA4 = /\bA4\b/i.test(sizeLine) || (/595(?:\.\d+)?\s+x\s+842(?:\.\d+)?/i.test(sizeLine));
  if (pages !== 1 || !isA4) throw new Error(`Invoice PDF must be one A4 page (pages=${pages || 'unknown'}, size=${sizeLine || 'unknown'})`);
  const extracted = spawnSync(pdftotext, [targetPath, '-'], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
  if (extracted.status !== 0) throw new Error('pdftotext could not read the invoice PDF');
  const text = extracted.stdout.replace(/\s+/g, ' ');
  const [year, monthNumber] = targetMonth.split('-').map(Number);
  if (!text.includes('請求書')) throw new Error('The PDF does not contain an invoice title');
  if (text.includes('勤怠明細') && !text.includes('ご請求')) throw new Error('The PDF appears to be an attendance sheet, not the invoice tab');
  if (!text.includes(String(year)) || !text.includes(`${monthNumber}月`)) throw new Error('The PDF does not contain the requested invoice month');
  console.log(`PDF_OK month=${targetMonth} pages=1 size=A4 invoice=true`);
}

function replacePeriod(text, targetMonth) {
  const [year, monthNumber] = targetMonth.split('-').map(Number);
  return String(text || '')
    .replace(/20\d{2}年\s*\d{1,2}月/g, `${year}年${monthNumber}月`)
    .replace(/20\d{2}[-/]\d{1,2}(?=月|分|[^0-9]|$)/g, targetMonth)
    .replace(/(^|[^0-9])\d{1,2}月分/g, `$1${monthNumber}月分`);
}
function defaultSubject(targetMonth) {
  const [year, monthNumber] = targetMonth.split('-').map(Number);
  return `${year}年${monthNumber}月分 請求書送付のご案内`;
}
function defaultBody(targetMonth) {
  const [year, monthNumber] = targetMonth.split('-').map(Number);
  return `お世話になっております。\n\n${year}年${monthNumber}月分の請求書を添付いたします。\nご確認のほど、よろしくお願いいたします。`;
}
function gmailUrl(email, hash) {
  return `https://mail.google.com/mail/u/0/?authuser=${encodeURIComponent(email)}#${hash}`;
}
async function gmailSearch(cdp, config, query) {
  await navigate(cdp, gmailUrl(config.accounts.googleEmail, `search/${encodeURIComponent(query)}`));
  await waitFor(cdp, async () => evaluate(cdp, `(() => {
    const body = String(document.body?.innerText || '');
    if (/sign in|ログイン|アカウントを選択/i.test(document.title + ' ' + body.slice(0, 800))) return {error: 'Google authentication is missing'};
    const gmailReady = document.querySelector('input[placeholder*="Search"], input[placeholder*="検索"], [aria-label*="Search mail"], [aria-label*="メールを検索"], [role="main"]');
    return document.querySelector('tr.zA, table[role="grid"] tr') || /一致するメールはありません|検索条件に一致|No emails/i.test(body) || gmailReady ? true : null;
  })()`), { timeoutMs: 60000, intervalMs: 1000, description: 'Gmail search results' });
  await sleep(3500);
}
async function readPreviousInvoiceMail(cdp, config) {
  await gmailSearch(cdp, config, 'in:sent has:attachment filename:pdf 請求書');
  const clicked = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const rows = [...document.querySelectorAll('tr.zA, table[role="grid"] tr')].filter(visible);
    const row = rows.find((e) => /請求|invoice/i.test(e.innerText || '')) || rows[0];
    if (!row) return false;
    row.click(); return true;
  })()`);
  if (!clicked) throw new Error('No previous sent invoice email was found; recipient cannot be inferred safely');
  await waitFor(cdp, async () => evaluate(cdp, `document.querySelector('h2.hP, [data-legacy-thread-id] h2') ? true : null`), {
    timeoutMs: 30000, description: 'previous invoice email',
  });
  await sleep(1200);
  await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const detail = [...document.querySelectorAll('[aria-label], [data-tooltip]')].filter(visible)
      .find((e) => /show details|詳細を表示/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-tooltip') || '')));
    if (detail) detail.click();
  })()`);
  await sleep(700);
  const message = await evaluate(cdp, `(() => {
    const subject = (document.querySelector('h2.hP, [data-legacy-thread-id] h2')?.innerText || '').trim();
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const detailRows = [...document.querySelectorAll('tr')].filter(visible);
    const toRow = detailRows.findLast((row) => /^(to|宛先)[：:]?$/i.test(String(row.querySelector('td,th')?.innerText || '').trim()));
    const toAttrs = [...(toRow?.querySelectorAll('[email]') || [])].map((e) => e.getAttribute('email'));
    const toText = String(toRow?.innerText || '').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/ig) || [];
    const toEmails = [...new Set([...toAttrs, ...toText].filter(Boolean))];
    const bodies = [...document.querySelectorAll('.a3s.aiL, [data-message-id] .a3s')].filter((e) => e.innerText?.trim());
    const body = bodies.at(-1)?.innerText?.trim() || '';
    return { subject, toEmails, body };
  })()`);
  const own = String(config.accounts.googleEmail).toLowerCase();
  const recipient = message.toEmails.find((email) => email.toLowerCase() !== own && !/no-?reply/i.test(email));
  if (!recipient) throw new Error('Previous invoice recipient could not be inferred safely');
  return {
    recipient,
    subject: replacePeriod(message.subject, month) || defaultSubject(month),
    body: replacePeriod(message.body, month) || defaultBody(month),
  };
}
async function setValue(cdp, selector, value) {
  return evaluate(cdp, `((selector, value) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');
    if (descriptor?.set) descriptor.set.call(el, value); else el.value = value;
    el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  })(${JSON.stringify(selector)}, ${JSON.stringify(value)})`);
}
async function findFileInputNode(cdp) {
  const { root } = await cdp.send('DOM.getDocument', { depth: -1, pierce: true });
  const selectors = ['input[type="file"][name="Filedata"]', 'input[type="file"]'];
  for (const selector of selectors) {
    const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector });
    if (nodeId) return nodeId;
  }
  return 0;
}
async function createDraft(cdp, config) {
  verifyInvoicePdf(pdfPath, month);
  const previous = await readPreviousInvoiceMail(cdp, config);
  await gmailSearch(cdp, config, `in:drafts subject:"${previous.subject.replaceAll('"', '')}"`);
  const reused = await evaluate(cdp, `(() => {
    const subject = ${JSON.stringify(previous.subject)};
    const row = [...document.querySelectorAll('tr.zA, table[role="grid"] tr')]
      .find((e) => (e.innerText || '').includes(subject));
    if (!row) return false;
    row.click(); return true;
  })()`);
  if (!reused) await navigate(cdp, gmailUrl(config.accounts.googleEmail, 'compose'));
  await waitFor(cdp, async () => evaluate(cdp, `document.querySelector('input[name="subjectbox"]') ? true : null`), {
    timeoutMs: 45000, intervalMs: 750, description: 'Gmail compose window',
  });
  const recipientState = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const recipient = ${JSON.stringify(previous.recipient)}.toLowerCase();
    if ([...document.querySelectorAll('[email]')].some((e) => String(e.getAttribute('email') || '').toLowerCase() === recipient)) return 'present';
    const inputs = [...document.querySelectorAll('input, textarea')].filter(visible);
    const el = inputs.find((e) => e.matches('input[peoplekit-id], textarea[name="to"]') || /to recipients|recipients|宛先/i.test(e.getAttribute('aria-label') || ''));
    if (!el) return false;
    el.focus(); return 'ready';
  })()`);
  if (!recipientState) throw new Error('Gmail recipient field was not found');
  if (recipientState === 'ready') {
    await cdp.send('Input.insertText', { text: previous.recipient });
    await press(cdp, 'Enter', 'Enter', 0, 13);
  }
  if (!await setValue(cdp, 'input[name="subjectbox"]', previous.subject)) throw new Error('Gmail subject field was not found');
  const bodyFound = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const candidates = [...document.querySelectorAll('[contenteditable="true"][role="textbox"], [aria-label="Message Body"], [aria-label="メッセージ本文"]')].filter(visible);
    const el = candidates.at(-1);
    if (!el) return false;
    el.focus(); return true;
  })()`);
  if (!bodyFound) throw new Error('Gmail message body was not found');
  await press(cdp, 'a', 'KeyA', 2, 65);
  await press(cdp, 'Backspace', 'Backspace', 0, 8);
  await cdp.send('Input.insertText', { text: previous.body });
  const filename = path.basename(pdfPath);
  const alreadyAttached = await evaluate(cdp, `String(document.body?.innerText || '').includes(${JSON.stringify(filename)})`);
  if (!alreadyAttached) {
    let fileNode = await findFileInputNode(cdp);
    if (!fileNode) {
      await evaluate(cdp, `(() => {
        const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const button = [...document.querySelectorAll('[aria-label], [data-tooltip], [command="Files"]')].filter(visible)
          .find((e) => /attach files|ファイルを添付/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-tooltip') || '')));
        if (button) button.click();
      })()`);
      await sleep(600);
      fileNode = await findFileInputNode(cdp);
    }
    if (!fileNode) throw new Error('Gmail attachment input was not found');
    await cdp.send('DOM.setFileInputFiles', { files: [path.resolve(pdfPath)], nodeId: fileNode });
    await waitFor(cdp, async () => evaluate(cdp, `(() => {
      const text = String(document.body?.innerText || '');
      if (!text.includes(${JSON.stringify(filename)})) return null;
      const uploading = [...document.querySelectorAll('[aria-label], [data-tooltip]')]
        .some((e) => /uploading|アップロード中/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-tooltip') || '')));
      return uploading ? null : true;
    })()`), { timeoutMs: 90000, intervalMs: 1000, description: 'invoice PDF attachment upload' });
  }
  await sleep(7000);
  const closed = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const button = [...document.querySelectorAll('[aria-label], [data-tooltip]')].filter(visible)
      .find((e) => /save & close|保存して閉じる/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-tooltip') || '')));
    if (!button) return false;
    button.click(); return true;
  })()`);
  if (!closed) throw new Error('Gmail Save & close control was not found');
  await sleep(3500);
  await gmailSearch(cdp, config, `in:drafts subject:"${previous.subject.replaceAll('"', '')}"`);
  const verified = await evaluate(cdp, `(() => {
    const subject = ${JSON.stringify(previous.subject)};
    return [...document.querySelectorAll('tr.zA, table[role="grid"] tr')].some((e) => (e.innerText || '').includes(subject));
  })()`);
  if (!verified) throw new Error('Draft verification failed');
  console.log(`DRAFT_OK month=${month} attachment=${filename} sent=false`);
}

function runSelfTest() {
  if (replacePeriod('2026年7月分 請求書', '2026-08') !== '2026年8月分 請求書') throw new Error('replacePeriod self-test failed');
  if (defaultSubject('2026-08') !== '2026年8月分 請求書送付のご案内') throw new Error('defaultSubject self-test failed');
  if (!defaultBody('2026-08').includes('2026年8月分')) throw new Error('defaultBody self-test failed');
  console.log('SELF_TEST_OK');
}

if (mode === 'self-test') {
  runSelfTest();
} else if (mode === 'verify') {
  verifyInvoicePdf(pdfPath, month);
} else {
  const config = readConfig();
  const browser = await launchChrome(config);
  let cdp;
  try {
    cdp = await connectPage(browser);
    if (mode === 'export') await exportInvoice(cdp, config);
    else await createDraft(cdp, config);
  } finally {
    cdp?.close();
    await stopChrome(browser);
  }
}
