#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const argValue = (name) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : undefined;
const configPath = path.resolve(argValue('--config') || path.join(os.homedir(), '.config', 'sync-teams-attendance', 'config.json'));
const month = argValue('--month');
const pdfPath = argValue('--pdf');
const selfTest = argv.includes('--self-test');
if (!selfTest && (!/^\d{4}-\d{2}$/.test(month || '') || !pdfPath || !path.isAbsolute(pdfPath))) {
  throw new Error('Usage: node normalize_invoice_recipients.mjs --month YYYY-MM --pdf /absolute/path.pdf [--config /path/config.json] [--inspect]\n       node normalize_invoice_recipients.mjs --self-test');
}
const [targetYear, targetMonthNumber] = selfTest ? [2000, 1] : month.split('-').map(Number);
const targetMonth = `${targetYear}年${targetMonthNumber}月`;
const targetAttachment = selfTest ? 'invoice.pdf' : path.basename(pdfPath);
const inspectOnly = process.argv.includes('--inspect');
const cacheNames = new Set(['Cache', 'Code Cache', 'DawnCache', 'GPUCache', 'GrShaderCache', 'GraphiteDawnCache', 'ShaderCache', 'Service Worker']);
const userAgent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function which(command) {
  const result = spawnSync('bash', ['-lc', `command -v ${JSON.stringify(command)}`], { encoding: 'utf8' });
  return result.status === 0 ? result.stdout.trim() : '';
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
  constructor(url) { this.url = url; this.id = 0; this.pending = new Map(); }
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
  send(method, params = {}, timeoutMs = 60000) {
    const id = ++this.id;
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
async function evaluate(cdp, expression) {
  const response = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, userGesture: true });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  return response.result.value;
}
async function waitFor(cdp, predicate, description, timeoutMs = 60000) {
  const started = Date.now();
  let last;
  while (Date.now() - started < timeoutMs) {
    last = await predicate().catch((error) => ({ error: error.message }));
    if (last && !last.error) return last;
    await sleep(750);
  }
  throw new Error(`Timed out waiting for ${description}${last?.error ? `: ${last.error}` : ''}`);
}
function profileFilter(source) {
  const name = path.basename(source);
  return !cacheNames.has(name) && !/^(blob_storage|Crashpad|BrowserMetrics|optimization_guide_model_store)$/.test(name);
}
async function launchChrome(config) {
  const chrome = process.env.CHROME_BIN || which('google-chrome') || which('chromium') || which('chromium-browser');
  if (!chrome) throw new Error('Chrome not found');
  const sourceRoot = path.resolve(config.chrome.userDataDir);
  const sourceProfile = path.join(sourceRoot, config.chrome.profileDirectory);
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-cc-chrome.'));
  const tempProfile = path.join(tempRoot, config.chrome.profileDirectory);
  fs.mkdirSync(tempProfile, { recursive: true, mode: 0o700 });
  const localState = path.join(sourceRoot, 'Local State');
  if (fs.existsSync(localState)) fs.copyFileSync(localState, path.join(tempRoot, 'Local State'));
  fs.cpSync(sourceProfile, tempProfile, { recursive: true, filter: profileFilter, force: true });
  const port = await freePort();
  const logPath = path.join(os.tmpdir(), `invoice-cc-chrome-${process.pid}.log`);
  const errorFd = fs.openSync(logPath, 'a');
  const runtimeDir = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`;
  const child = spawn(chrome, [
    `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--disable-crashpad', '--disable-crash-reporter', '--disable-breakpad',
    '--disable-background-networking', '--no-first-run', '--no-default-browser-check',
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
  }, 'Chrome startup', 30000);
  const page = await fetchJson(`http://127.0.0.1:${browser.port}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' });
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Network.enable');
  await cdp.send('Network.setUserAgentOverride', { userAgent, platform: 'Linux x86_64' });
  return cdp;
}
async function navigate(cdp, url) {
  await cdp.send('Page.navigate', { url });
  await waitFor(cdp, async () => {
    const state = await evaluate(cdp, 'document.readyState');
    return state === 'complete' || state === 'interactive' ? state : null;
  }, `navigation to ${new URL(url).hostname}`);
}
function gmailUrl(email, hash) {
  return `https://mail.google.com/mail/u/0/?authuser=${encodeURIComponent(email)}#${hash}`;
}
async function gmailSearch(cdp, config, query) {
  await navigate(cdp, gmailUrl(config.accounts.googleEmail, `search/${encodeURIComponent(query)}`));
  await waitFor(cdp, async () => evaluate(cdp, `(() => {
    const body = String(document.body?.innerText || '');
    if (/sign in|ログイン|アカウントを選択/i.test(document.title + ' ' + body.slice(0, 800))) return {error: 'Google authentication is missing'};
    return document.querySelector('tr.zA, table[role="grid"] tr') || /一致するメールはありません|検索条件に一致|No emails/i.test(body) ? true : null;
  })()`), 'Gmail search results');
  await sleep(1200);
}
async function openFirstRow(cdp, pattern) {
  const opened = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const row = [...document.querySelectorAll('tr.zA, table[role="grid"] tr')].filter(visible)
      .find((e) => ${pattern}.test(e.innerText || ''));
    if (!row) return false;
    row.click(); return true;
  })()`);
  if (!opened) throw new Error('Expected Gmail message was not found');
}
function buildRecipientPlan(recipients, ownEmail) {
  const key = (email) => String(email || '').trim().toLowerCase();
  const own = key(ownEmail);
  const isNoReply = (email) => /^(?:no[._-]?reply|do[._-]?not[._-]?reply)@/i.test(key(email));
  const externalTo = recipients.to.filter((email) => key(email) !== own && !isNoReply(email));
  const priorCc = recipients.cc.filter((email) => key(email) !== own && !isNoReply(email));
  const priorBcc = recipients.bcc.filter((email) => key(email));
  if (!externalTo.length) throw new Error('Previous invoice mail has no external To recipient');
  if (priorBcc.length) throw new Error('Previous invoice mail contains BCC recipients; refusing to infer a no-BCC draft');
  const primary = externalTo[0];
  const seen = new Set([key(primary)]);
  const cc = [...externalTo.slice(1), ...priorCc].filter((email) => {
    const normalized = key(email);
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
  return { primary, cc };
}
async function readPriorTo(cdp, config) {
  await gmailSearch(cdp, config, 'in:sent has:attachment filename:pdf 請求書');
  await openFirstRow(cdp, '/請求|invoice/i');
  await waitFor(cdp, async () => evaluate(cdp, `document.querySelector('h2.hP, [data-legacy-thread-id] h2') ? true : null`), 'sent invoice email');
  await sleep(1200);
  const clicked = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const button = [...document.querySelectorAll('.ajz, [aria-label], [data-tooltip]')].filter(visible)
      .findLast((e) => /show details|詳細を表示/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-tooltip') || '')));
    if (!button) return false;
    button.click(); return true;
  })()`);
  if (!clicked) throw new Error('Previous recipient details could not be opened');
  await sleep(900);
  const recipients = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const rows = [...document.querySelectorAll('tr')].filter(visible);
    const read = (pattern) => {
      const row = rows.findLast((candidate) => pattern.test(String(candidate.querySelector('td,th')?.innerText || '').trim()));
      if (!row) return [];
      return [...new Set([...row.querySelectorAll('[email]')].map((e) => e.getAttribute('email')).filter(Boolean))];
    };
    return { to: read(/^(to|宛先)[：:]?$/i), cc: read(/^cc[：:]?$/i), bcc: read(/^bcc[：:]?$/i) };
  })()`);
  return buildRecipientPlan(recipients, config.accounts.googleEmail);
}
const composeRecipientHelpers = `
  const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const inputs = [...document.querySelectorAll('input,textarea')].filter(visible);
  const marker = (e) => ((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('placeholder') || '')).trim();
  const fieldFor = (e) => {
    const name = String(e.name || '').toLowerCase();
    if (name === 'bcc' || /bcc recipients|bcc の宛先|^bcc$/i.test(marker(e))) return 'bcc';
    if (name === 'cc' || /cc recipients|cc の宛先|^cc$/i.test(marker(e))) return 'cc';
    if (name === 'to' || e.matches('input[peoplekit-id], textarea[name="to"]') || /to recipients|宛先|^recipients$/i.test(marker(e))) return 'to';
    return null;
  };
  const recipientInputs = inputs.map((e) => ({e, field: fieldFor(e)})).filter(({field}) => field);
  const inputFor = (field) => recipientInputs.find((item) => item.field === field)?.e || null;
  const scopeFor = (field) => {
    const input = inputFor(field);
    if (!input) return null;
    let scope = input.parentElement;
    while (scope?.parentElement && scope.parentElement !== document.body) {
      const parent = scope.parentElement;
      const containsOtherField = recipientInputs.some(({e, field: other}) => other !== field && parent.contains(e));
      if (containsOtherField) break;
      scope = parent;
    }
    return scope;
  };
  const emailPattern = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/ig;
  const emailsFor = (field) => {
    const input = inputFor(field);
    const scope = scopeFor(field);
    if (!input || !scope) return null;
    const nodes = [input, ...scope.querySelectorAll('.vR, [email], [data-email], [data-hovercard-id]')];
    const values = nodes.flatMap((node) => [node.value, node.getAttribute?.('email'), node.getAttribute?.('data-email'), node.getAttribute?.('data-hovercard-id'), node.getAttribute?.('aria-label'), node.getAttribute?.('title')]
      .filter(Boolean).flatMap((value) => String(value).match(emailPattern) || []));
    const seen = new Set();
    return values.filter((email) => { const key = email.toLowerCase(); if (seen.has(key)) return false; seen.add(key); return true; });
  };
`;
async function recipientPresent(cdp, email, field) {
  return evaluate(cdp, `((email, field) => {
    ${composeRecipientHelpers}
    const fields = field ? [field] : ['to', 'cc', 'bcc'];
    return fields.some((name) => (emailsFor(name) || []).some((candidate) => candidate.toLowerCase() === email.toLowerCase()));
  })(${JSON.stringify(email)}, ${JSON.stringify(field || '')})`);
}
async function removeRecipient(cdp, email, field) {
  const result = await evaluate(cdp, `((email, field) => {
    ${composeRecipientHelpers}
    const target = email.toLowerCase();
    const scope = scopeFor(field);
    if (!scope) return {ok: false};
    const candidates = [...scope.querySelectorAll('.vR, [email], [data-email], [data-hovercard-id]')].filter(visible).filter((e) =>
      [e.getAttribute('email'), e.getAttribute('data-email'), e.getAttribute('data-hovercard-id'), e.getAttribute('aria-label'), e.getAttribute('title')]
        .filter(Boolean).flatMap((value) => String(value).match(emailPattern) || []).some((candidate) => candidate.toLowerCase() === target));
    candidates.sort((a, b) => a.outerHTML.length - b.outerHTML.length);
    const found = candidates[0];
    if (!found) return {ok: false};
    const chip = found.closest('.vR, [role="option"]') || found.closest('[data-hovercard-id]') || found.parentElement;
    const remove = chip?.querySelector('[aria-label*="Remove" i], [aria-label*="削除"], [data-tooltip*="Remove" i], [data-tooltip*="削除"]');
    if (remove) { remove.click(); return {ok: true, method: 'button'}; }
    (chip || found).click();
    return {ok: true, method: 'keyboard'};
  })(${JSON.stringify(email)}, ${JSON.stringify(field)})`);
  if (!result.ok) throw new Error('A secondary To recipient could not be located');
  if (result.method === 'keyboard') {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8, nativeVirtualKeyCode: 8 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8, nativeVirtualKeyCode: 8 });
  }
  await sleep(600);
  if (await recipientPresent(cdp, email, field)) throw new Error(`A ${field.toUpperCase()} recipient was not removed`);
}
async function findCcInput(cdp) {
  return evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const inputs = [...document.querySelectorAll('input,textarea')].filter(visible);
    const input = inputs.find((e) => String(e.name || '').toLowerCase() === 'cc')
      || inputs.find((e) => /cc recipients|cc の宛先|(^|\\b)cc$/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('placeholder') || '')));
    if (!input) return false;
    input.focus(); return true;
  })()`);
}
async function revealCc(cdp) {
  if (await findCcInput(cdp)) return;
  const clicked = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const controls = [...document.querySelectorAll('span.aB,button,[role="button"],[aria-label],[data-tooltip]')].filter(visible)
      .map((e) => ({e, text: String(e.innerText || e.textContent || e.getAttribute('aria-label') || e.getAttribute('data-tooltip') || '').trim()}))
      .filter(({text}) => /(^|\\b)cc(\\b|$)/i.test(text) && !/^bcc$/i.test(text))
      .sort((a, b) => a.text.length - b.text.length);
    const control = controls[0]?.e;
    if (!control) return false;
    control.click(); return true;
  })()`);
  if (!clicked) {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'c', code: 'KeyC', modifiers: 10, windowsVirtualKeyCode: 67, nativeVirtualKeyCode: 67 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'c', code: 'KeyC', modifiers: 10, windowsVirtualKeyCode: 67, nativeVirtualKeyCode: 67 });
    await sleep(500);
    if (await findCcInput(cdp)) return;
    throw new Error('CC control was not found');
  }
  await waitFor(cdp, async () => findCcInput(cdp), 'CC recipient field', 10000);
}
async function addCc(cdp, email) {
  if (!await findCcInput(cdp)) throw new Error('CC recipient field was unavailable');
  await cdp.send('Input.insertText', { text: email });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await sleep(600);
  if (!await recipientPresent(cdp, email, 'cc')) throw new Error('A CC recipient could not be verified');
}
async function revealBcc(cdp) {
  const hasInput = await evaluate(cdp, `(() => {
    ${composeRecipientHelpers}
    return !!inputFor('bcc');
  })()`);
  if (hasInput) return;
  const clicked = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const control = [...document.querySelectorAll('span,button,[role="button"]')].filter(visible)
      .find((e) => /^(bcc|密送)$/i.test(String(e.innerText || e.textContent || '').trim()));
    if (!control) return false;
    control.click(); return true;
  })()`);
  if (!clicked) throw new Error('BCC control was not found; refusing to claim bcc=0');
  await waitFor(cdp, async () => evaluate(cdp, `(() => {
    ${composeRecipientHelpers}
    return inputFor('bcc') ? true : null;
  })()`), 'BCC recipient field', 10000);
}
async function readDraftRecipients(cdp) {
  return evaluate(cdp, `(() => {
    ${composeRecipientHelpers}
    return {to: emailsFor('to'), cc: emailsFor('cc'), bcc: emailsFor('bcc')};
  })()`);
}
function verifyRecipientState(actual, recipientPlan) {
  if (!actual || !Array.isArray(actual.to) || !Array.isArray(actual.cc) || !Array.isArray(actual.bcc)) return false;
  const keys = (values) => values.map((email) => String(email).toLowerCase());
  const to = keys(actual.to);
  const cc = keys(actual.cc);
  const expectedCc = keys(recipientPlan.cc);
  return to.length === 1
    && to[0] === String(recipientPlan.primary).toLowerCase()
    && cc.length === expectedCc.length
    && cc.every((email, index) => email === expectedCc[index])
    && new Set(cc).size === cc.length
    && actual.bcc.length === 0;
}
async function updateDraft(cdp, config, recipientPlan) {
  await gmailSearch(cdp, config, `in:drafts has:attachment filename:"${targetAttachment.replaceAll('"', '')}" ${targetMonth}`);
  await openFirstRow(cdp, `new RegExp(${JSON.stringify(`${targetMonth}|${targetMonthNumber}月分`)})`);
  await waitFor(cdp, async () => evaluate(cdp, `document.querySelector('input[name="subjectbox"]') ? true : null`), 'invoice draft');
  const draft = await evaluate(cdp, `(() => ({
    subject: document.querySelector('input[name="subjectbox"]')?.value || '',
    hasAttachment: String(document.body?.innerText || '').includes(${JSON.stringify(targetAttachment)})
  }))()`);
  if (!draft.subject.includes(targetMonth) || !draft.hasAttachment) throw new Error('Target invoice draft did not match');
  await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const inputs = [...document.querySelectorAll('input')].filter(visible);
    const field = inputs.find((e) => e.type === 'text' && !e.name && !e.getAttribute('aria-label'));
    if (field) { field.click(); field.focus(); return true; }
    return false;
  })()`);
  await sleep(500);
  if (inspectOnly) {
    const diagnostics = [];
    for (const [index, email] of [recipientPlan.primary, ...recipientPlan.cc].entries()) {
      diagnostics.push(await evaluate(cdp, `((email, index) => {
        const target = email.toLowerCase();
        const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const redact = (s) => String(s || '').replaceAll(email, '<email>').slice(0, 180);
        const matches = [...document.querySelectorAll('*')].filter(visible).filter((e) =>
          [e.getAttribute('email'), e.getAttribute('data-email'), e.getAttribute('data-hovercard-id'), e.getAttribute('aria-label'), e.getAttribute('title'), e.value, e.innerText]
            .some((value) => String(value || '').toLowerCase().includes(target)));
        matches.sort((a, b) => a.outerHTML.length - b.outerHTML.length);
        return {index, count: matches.length, nodes: matches.slice(0, 4).map((e) => ({
          tag: e.tagName, cls: redact(e.className), role: e.getAttribute('role') || '',
          aria: redact(e.getAttribute('aria-label')), name: e.getAttribute('name') || '',
          parentTag: e.parentElement?.tagName || '', parentRole: e.parentElement?.getAttribute('role') || '',
          parentClass: redact(e.parentElement?.className)
        }))};
      })(${JSON.stringify(email)}, ${index})`));
    }
    console.log(`RECIPIENT_DIAGNOSTIC ${JSON.stringify(diagnostics)}`);
    return;
  }
  await revealCc(cdp);
  await revealBcc(cdp);
  const initialRecipients = await readDraftRecipients(cdp);
  if (!initialRecipients || !Array.isArray(initialRecipients.to) || !Array.isArray(initialRecipients.cc) || !Array.isArray(initialRecipients.bcc)) {
    throw new Error('Draft recipient fields could not be read safely');
  }
  const allowed = new Set([recipientPlan.primary, ...recipientPlan.cc].map((email) => email.toLowerCase()));
  if (initialRecipients.bcc.length || [...initialRecipients.to, ...initialRecipients.cc].some((email) => !allowed.has(email.toLowerCase()))) {
    throw new Error('Draft contains unexpected recipients; refusing to normalize it');
  }
  if (!initialRecipients.to.some((email) => email.toLowerCase() === recipientPlan.primary.toLowerCase())) {
    throw new Error('Primary To recipient was missing');
  }
  for (const email of initialRecipients.to) {
    if (email.toLowerCase() !== recipientPlan.primary.toLowerCase()) await removeRecipient(cdp, email, 'to');
  }
  for (const email of initialRecipients.cc) await removeRecipient(cdp, email, 'cc');
  for (const email of recipientPlan.cc) await addCc(cdp, email);
  const actualRecipients = await readDraftRecipients(cdp);
  if (!verifyRecipientState(actualRecipients, recipientPlan)) {
    throw new Error('Draft recipient verification failed; refusing to report normalized recipients');
  }
  await sleep(7000);
  const closed = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const button = [...document.querySelectorAll('[aria-label], [data-tooltip]')].filter(visible)
      .find((e) => /save & close|保存して閉じる/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-tooltip') || '')));
    if (!button) return false;
    button.click(); return true;
  })()`);
  if (!closed) throw new Error('Save & close control was not found');
  await sleep(3000);
  await gmailSearch(cdp, config, `in:drafts has:attachment filename:"${targetAttachment.replaceAll('"', '')}" ${targetMonth}`);
  const verified = await evaluate(cdp, `(() => [...document.querySelectorAll('tr.zA, table[role="grid"] tr')]
    .some((e) => new RegExp(${JSON.stringify(`${targetMonth}|${targetMonthNumber}月分`)}).test(e.innerText || '')))()`);
  if (!verified) throw new Error('Updated draft was not found');
  console.log(`DRAFT_UPDATED to=${actualRecipients.to.length} cc=${actualRecipients.cc.length} bcc=${actualRecipients.bcc.length} sent=false`);
}

function runSelfTest() {
  const plan = buildRecipientPlan({
    to: ['primary@example.com', 'owner@example.com', 'secondary@example.com'],
    cc: ['secondary@example.com', 'reviewer@example.com'],
    bcc: [],
  }, 'owner@example.com');
  if (plan.primary !== 'primary@example.com' || plan.cc.join(',') !== 'secondary@example.com,reviewer@example.com') {
    throw new Error('recipient plan self-test failed');
  }
  const noReplyPlan = buildRecipientPlan({
    to: ['no-reply@example.com', 'primary@example.com', 'owner@example.com'],
    cc: ['noreply@example.com', 'do_not_reply@example.com', 'reviewer@example.com'],
    bcc: [],
  }, 'owner@example.com');
  if (noReplyPlan.primary !== 'primary@example.com' || noReplyPlan.cc.join(',') !== 'reviewer@example.com') {
    throw new Error('no-reply exclusion self-test failed');
  }
  if (!verifyRecipientState({
    to: ['PRIMARY@example.com'], cc: ['secondary@example.com', 'reviewer@example.com'], bcc: [],
  }, plan)) throw new Error('recipient state verification self-test failed');
  if (verifyRecipientState({
    to: ['primary@example.com'], cc: ['secondary@example.com', 'reviewer@example.com'], bcc: ['hidden@example.com'],
  }, plan)) throw new Error('draft BCC verification self-test failed');
  try {
    buildRecipientPlan({ to: ['primary@example.com'], cc: [], bcc: ['hidden@example.com'] }, 'owner@example.com');
    throw new Error('BCC refusal self-test failed');
  } catch (error) {
    if (!/contains BCC/.test(error.message)) throw error;
  }
  try {
    buildRecipientPlan({ to: ['primary@example.com'], cc: [], bcc: ['owner@example.com'] }, 'owner@example.com');
    throw new Error('own-address BCC refusal self-test failed');
  } catch (error) {
    if (!/contains BCC/.test(error.message)) throw error;
  }
  console.log('SELF_TEST_OK');
}

if (selfTest) {
  runSelfTest();
} else {
  if (!fs.existsSync(configPath)) throw new Error('Config file was not found');
  if ((fs.statSync(configPath).mode & 0o077) !== 0) throw new Error('Config permissions are too broad; use mode 600');
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  if (!config.chrome?.userDataDir || !config.chrome?.profileDirectory || !config.accounts?.googleEmail) {
    throw new Error('Config requires chrome.userDataDir, chrome.profileDirectory, and accounts.googleEmail');
  }
  const browser = await launchChrome(config);
  let cdp;
  try {
    cdp = await connectPage(browser);
    const recipientPlan = await readPriorTo(cdp, config);
    await updateDraft(cdp, config, recipientPlan);
  } finally {
    cdp?.close();
    await stopChrome(browser);
  }
}
