#!/usr/bin/env node

import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const DEFAULT_CONFIG = path.join(os.homedir(), '.config', 'sync-teams-attendance', 'config.json');
const INTEGRATED_LAYOUT_CONTRACT = 'integrated-attendance-v1';
const CHROME_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36';
const MICROSOFT_AUTH_ORIGINS = [
  'https://login.microsoftonline.com',
  'https://login.live.com',
  'https://account.live.com',
  'https://account.microsoft.com',
  'https://teams.microsoft.com',
  'https://teams.cloud.microsoft',
];
const TEAMS_HOSTS = ['teams.microsoft.com', 'teams.cloud.microsoft'];
const CACHE_DIR_NAMES = new Set([
  'Cache', 'Code Cache', 'DawnCache', 'GPUCache', 'GrShaderCache',
  'GraphiteDawnCache', 'ShaderCache', 'Service Worker',
]);

function usage() {
  console.log(`Usage:
  node scripts/sync_teams_attendance.mjs [--inspect-sheet] [--since YYYY-MM-DD] [--apply]

Options:
  --config <path>    Config file. Default: ~/.config/sync-teams-attendance/config.json
  --since <date>     Earliest local work date to consider (YYYY-MM-DD).
  --inspect-sheet    Validate the integrated workbook contract and inspect the append row.
  --apply            Append verified missing intervals. The default is a dry run.
  --headed           Run Chrome with a visible window when DISPLAY is available.
  --timeout-ms <n>   Navigation/authentication timeout. Default: 180000.
  --self-test        Run deterministic parser, layout, and pairing tests.
  --help             Show this help.
`);
}

function parseArgs(argv) {
  const args = { apply: false, headed: false, inspectSheet: false, timeoutMs: 180000 };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') args.help = true;
    else if (arg === '--config') args.config = argv[++i];
    else if (arg === '--since') args.since = argv[++i];
    else if (arg === '--inspect-sheet') args.inspectSheet = true;
    else if (arg === '--apply') args.apply = true;
    else if (arg === '--headed') args.headed = true;
    else if (arg === '--timeout-ms') args.timeoutMs = Number(argv[++i]);
    else if (arg === '--self-test') args.selfTest = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs < 1000) throw new Error('--timeout-ms must be at least 1000');
  if (args.since && !/^\d{4}-\d{2}-\d{2}$/.test(args.since)) throw new Error('--since must use YYYY-MM-DD');
  if (args.apply && args.inspectSheet) throw new Error('--apply and --inspect-sheet cannot be combined');
  return args;
}

function readConfig(configPath) {
  const resolved = path.resolve(configPath || DEFAULT_CONFIG);
  if (!fs.existsSync(resolved)) throw new Error(`Config not found: ${resolved}`);
  const stat = fs.statSync(resolved);
  if ((stat.mode & 0o077) !== 0) throw new Error(`Config permissions are too broad; run: chmod 600 ${resolved}`);
  const config = JSON.parse(fs.readFileSync(resolved, 'utf8'));
  const required = [
    ['chrome.userDataDir', config.chrome?.userDataDir],
    ['chrome.profileDirectory', config.chrome?.profileDirectory],
    ['accounts.googleEmail', config.accounts?.googleEmail],
    ['accounts.teamsLoginEmail', config.accounts?.teamsLoginEmail],
    ['teams.tenantId', config.teams?.tenantId],
    ['teams.chatName', config.teams?.chatName],
    ['teams.authorDisplayName', config.teams?.authorDisplayName],
    ['teams.verificationSender', config.teams?.verificationSender],
    ['spreadsheet.url', config.spreadsheet?.url],
  ];
  const missing = required.filter(([, value]) => !value).map(([key]) => key);
  if (missing.length) throw new Error(`Missing config keys: ${missing.join(', ')}`);
  for (const kind of ['start', 'pause', 'resume', 'end']) {
    if (!Array.isArray(config.teams?.messages?.[kind]) || config.teams.messages[kind].length === 0) {
      throw new Error(`teams.messages.${kind} must be a non-empty array`);
    }
  }
  const sheet = new URL(config.spreadsheet.url);
  if (sheet.protocol !== 'https:' || sheet.hostname !== 'docs.google.com' || !sheet.pathname.includes('/spreadsheets/d/')) {
    throw new Error('spreadsheet.url must be a docs.google.com/spreadsheets URL');
  }
  if ((config.sync?.timezone || 'Asia/Tokyo') !== 'Asia/Tokyo') {
    throw new Error('This version supports sync.timezone=Asia/Tokyo only');
  }
  return {
    ...config,
    configPath: resolved,
    sync: {
      timezone: 'Asia/Tokyo',
      overlapDays: Number(config.sync?.overlapDays ?? 2),
      defaultLookbackDays: Number(config.sync?.defaultLookbackDays ?? 31),
    },
    spreadsheet: {
      ...config.spreadsheet,
      layoutContract: config.spreadsheet?.layoutContract || INTEGRATED_LAYOUT_CONTRACT,
      headerAliases: {
        date: config.spreadsheet?.headerAliases?.date || ['日付', '勤務日', 'date'],
        start: config.spreadsheet?.headerAliases?.start || ['出勤', '開始', '始業', 'start', 'clock in'],
        end: config.spreadsheet?.headerAliases?.end || ['退勤', '終了', '終業', 'end', 'clock out'],
      },
    },
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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
  constructor(url) {
    this.url = url;
    this.nextId = 0;
    this.pending = new Map();
  }

  async connect() {
    if (typeof WebSocket !== 'function') throw new Error('Node.js with global WebSocket support is required');
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const pending = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${JSON.stringify(message.error)}`));
      else pending.resolve(message.result);
    };
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error('Chrome DevTools websocket connection failed'));
    });
  }

  send(method, params = {}, timeoutMs = 20000) {
    const id = ++this.nextId;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`Chrome DevTools timeout: ${method}`));
      }, timeoutMs);
    });
  }

  close() {
    try { this.ws.close(); } catch {}
  }
}

async function evaluate(cdp, expression, awaitPromise = false) {
  const response = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise,
    returnByValue: true,
    userGesture: true,
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

async function launchChrome(config, headed) {
  const chrome = process.env.CHROME_BIN || which('google-chrome') || which('chromium') || which('chromium-browser');
  if (!chrome) throw new Error('google-chrome/chromium was not found');
  const sourceRoot = path.resolve(config.chrome.userDataDir);
  const sourceProfile = path.join(sourceRoot, config.chrome.profileDirectory);
  if (!fs.existsSync(sourceProfile)) throw new Error(`Chrome profile not found: ${sourceProfile}`);

  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'teams-attendance-chrome.'));
  const tempProfile = path.join(tempRoot, config.chrome.profileDirectory);
  fs.mkdirSync(tempProfile, { recursive: true, mode: 0o700 });
  const localState = path.join(sourceRoot, 'Local State');
  if (fs.existsSync(localState)) fs.copyFileSync(localState, path.join(tempRoot, 'Local State'));
  fs.cpSync(sourceProfile, tempProfile, { recursive: true, filter: profileFilter, force: true });

  const port = await freePort();
  const args = [
    `--remote-debugging-port=${port}`,
    '--remote-debugging-address=127.0.0.1',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--disable-crashpad',
    '--disable-crash-reporter',
    '--disable-breakpad',
    '--disable-background-networking',
    '--no-first-run',
    '--no-default-browser-check',
    `--user-data-dir=${tempRoot}`,
    `--profile-directory=${config.chrome.profileDirectory}`,
    '--window-size=1440,1000',
    ...(headed ? [] : ['--headless=new']),
    'about:blank',
  ];
  const logPath = path.join(os.tmpdir(), `teams-attendance-chrome-${process.pid}.log`);
  const errorFd = fs.openSync(logPath, 'a');
  const runtimeDir = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`;
  const browserEnv = {
    ...process.env,
    XDG_RUNTIME_DIR: runtimeDir,
    DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS || `unix:path=${runtimeDir}/bus`,
    GNOME_KEYRING_CONTROL: process.env.GNOME_KEYRING_CONTROL || path.join(runtimeDir, 'keyring'),
  };
  const child = spawn(chrome, args, {
    env: browserEnv,
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
  await cdp.send('Network.setUserAgentOverride', { userAgent: CHROME_UA, platform: 'Linux x86_64' });
  return cdp;
}

async function connectAuxPage(browser, url) {
  const page = await fetchJson(`http://127.0.0.1:${browser.port}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Network.enable');
  await cdp.send('Network.setUserAgentOverride', { userAgent: CHROME_UA, platform: 'Linux x86_64' });
  return cdp;
}

async function navigate(cdp, url) {
  await cdp.send('Page.navigate', { url });
  await waitFor(cdp, async () => {
    const state = await evaluate(cdp, 'document.readyState');
    return state === 'complete' || state === 'interactive' ? state : null;
  }, { timeoutMs: 60000, description: `navigation to ${new URL(url).hostname}` });
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { row.push(cell); cell = ''; }
    else if (char === '\n') { row.push(cell.replace(/\r$/, '')); rows.push(row); row = []; cell = ''; }
    else cell += char;
  }
  if (cell.length || row.length) { row.push(cell.replace(/\r$/, '')); rows.push(row); }
  return rows;
}

function normalizeText(value) {
  return String(value ?? '').normalize('NFKC').replace(/\s+/g, ' ').trim().toLocaleLowerCase('ja-JP');
}

function parseSheetDate(value) {
  const normalized = normalizeText(value).replace(/[年月]/g, '/').replace(/日$/, '').replace(/[.-]/g, '/');
  let match = normalized.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
  if (match) return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
  match = normalized.match(/^(\d{1,2})\/(\d{1,2})$/);
  return match ? { year: null, month: Number(match[1]), day: Number(match[2]) } : null;
}

function parseSheetTime(value) {
  const match = normalizeText(value).match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/);
  if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return null;
  return `${String(Number(match[1])).padStart(2, '0')}:${match[2]}`;
}

function formatMonthDay(month, day) {
  return `${month}/${day}`;
}

function intervalKey(date, start, end) {
  return `${date}|${start}|${end}`;
}

function inferSheetModel(rows, aliases) {
  const normalizedAliases = Object.fromEntries(Object.entries(aliases).map(([key, values]) => [key, new Set(values.map(normalizeText))]));
  const candidates = [];
  for (let rowIndex = 0; rowIndex < Math.min(rows.length, 80); rowIndex += 1) {
    const found = {};
    for (const semantic of ['date', 'start', 'end']) {
      const matches = [];
      for (let column = 0; column < rows[rowIndex].length; column += 1) {
        if (normalizedAliases[semantic].has(normalizeText(rows[rowIndex][column]))) matches.push(column);
      }
      if (matches.length === 1) found[semantic] = matches[0];
    }
    if (Object.keys(found).length === 3 && new Set(Object.values(found)).size === 3) candidates.push({ rowIndex, columns: found });
  }
  if (candidates.length === 0) throw new Error('Could not uniquely identify date/start/end headers; update spreadsheet.headerAliases');
  const signature = (candidate) => `${candidate.columns.date}:${candidate.columns.start}:${candidate.columns.end}`;
  const signatures = new Set(candidates.map(signature));
  if (signatures.size > 1) throw new Error('Multiple conflicting date/start/end header mappings were found');
  const header = candidates[0];
  let lastDataIndex = header.rowIndex;
  const existingKeys = new Set();
  const datedRows = [];
  for (let rowIndex = header.rowIndex + 1; rowIndex < rows.length; rowIndex += 1) {
    const dateRaw = rows[rowIndex]?.[header.columns.date] || '';
    const startRaw = rows[rowIndex]?.[header.columns.start] || '';
    const endRaw = rows[rowIndex]?.[header.columns.end] || '';
    if ([dateRaw, startRaw, endRaw].some((value) => normalizeText(value))) lastDataIndex = rowIndex;
    const date = parseSheetDate(dateRaw);
    const start = parseSheetTime(startRaw);
    const end = parseSheetTime(endRaw);
    if (date && start && end) {
      const monthDay = formatMonthDay(date.month, date.day);
      existingKeys.add(intervalKey(monthDay, start, end));
      datedRows.push({ ...date, rowIndex });
    }
  }
  return {
    headerRow: header.rowIndex + 1,
    columns: header.columns,
    appendRow: lastDataIndex + 2,
    existingKeys,
    datedRows,
  };
}

function validateWorkbookContract(rows, model, contract) {
  if (contract !== INTEGRATED_LAYOUT_CONTRACT) {
    throw new Error(`Unsupported spreadsheet.layoutContract: ${contract}`);
  }
  const errors = [];
  if (model.headerRow !== 1) errors.push(`header row is ${model.headerRow}, expected 1`);
  const expectedColumns = { date: 0, start: 1, end: 2 };
  for (const [semantic, expected] of Object.entries(expectedColumns)) {
    if (model.columns[semantic] !== expected) {
      errors.push(`${semantic} column is ${columnName(model.columns[semantic])}, expected ${columnName(expected)}`);
    }
  }
  const expectedHeaders = ['日付', '出勤', '退勤', '労働時間'];
  for (let column = 0; column < expectedHeaders.length; column += 1) {
    if (normalizeText(rows[0]?.[column]) !== normalizeText(expectedHeaders[column])) {
      errors.push(`${columnName(column)}1 is ${JSON.stringify(rows[0]?.[column] || '')}, expected ${JSON.stringify(expectedHeaders[column])}`);
    }
  }
  if (errors.length) {
    throw new Error(`Spreadsheet does not match ${INTEGRATED_LAYOUT_CONTRACT}: ${errors.join('; ')}`);
  }
  return model;
}

function workbookModel(rows, config) {
  const model = inferSheetModel(rows, config.spreadsheet.headerAliases);
  return validateWorkbookContract(rows, model, config.spreadsheet.layoutContract);
}

function columnName(index) {
  let value = index + 1;
  let out = '';
  while (value > 0) {
    value -= 1;
    out = String.fromCharCode(65 + (value % 26)) + out;
    value = Math.floor(value / 26);
  }
  return out;
}

function localParts(instant, timezone = 'Asia/Tokyo') {
  const date = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(date.valueOf())) throw new Error(`Invalid instant: ${instant}`);
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23',
  });
  const values = Object.fromEntries(formatter.formatToParts(date).filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
  return {
    year: Number(values.year), month: Number(values.month), day: Number(values.day),
    date: `${values.year}-${values.month}-${values.day}`,
    monthDay: `${Number(values.month)}/${Number(values.day)}`,
    time: `${values.hour}:${values.minute}`,
  };
}

function classifyMessage(text, messages) {
  const normalized = normalizeText(text);
  for (const kind of ['start', 'pause', 'resume', 'end']) {
    if (messages[kind].some((candidate) => normalizeText(candidate) === normalized)) return kind;
  }
  return null;
}

function buildIntervals(events, config, sinceDate) {
  const relevant = events
    .filter((event) => normalizeText(event.author) === normalizeText(config.teams.authorDisplayName))
    .map((event) => ({ ...event, kind: classifyMessage(event.text, config.teams.messages) }))
    .filter((event) => event.kind)
    .sort((a, b) => new Date(a.datetime) - new Date(b.datetime));
  const intervals = [];
  const anomalies = [];
  let open = null;
  for (const event of relevant) {
    const local = localParts(event.datetime, config.sync.timezone);
    if (event.kind === 'start' || event.kind === 'resume') {
      if (open) {
        anomalies.push({ type: 'duplicate_open', date: local.date, time: local.time });
      } else {
        open = { event, local };
      }
    } else if (!open) {
      anomalies.push({ type: 'orphan_close', date: local.date, time: local.time });
    } else {
      const interval = {
        date: open.local.monthDay,
        isoDate: open.local.date,
        start: open.local.time,
        end: local.time,
        startInstant: open.event.datetime,
        endInstant: event.datetime,
      };
      if (new Date(interval.endInstant) <= new Date(interval.startInstant)) {
        anomalies.push({ type: 'non_positive_interval', date: interval.isoDate, time: interval.start });
      } else if (!sinceDate || interval.isoDate >= sinceDate) {
        intervals.push(interval);
      }
      open = null;
    }
  }
  if (open) anomalies.push({ type: 'trailing_open', date: open.local.date, time: open.local.time });
  return { relevant, intervals, anomalies };
}

function resolveYear(month, current) {
  return month > current.month + 1 ? current.year - 1 : current.year;
}

function subtractDays(dateText, days) {
  const [year, month, day] = dateText.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function deriveSince(model, config, explicitSince) {
  if (explicitSince) return explicitSince;
  if (model.datedRows.length) {
    const today = localParts(new Date(), config.sync.timezone);
    const latest = model.datedRows
      .map((date) => ({ ...date, year: date.year || resolveYear(date.month, today) }))
      .sort((a, b) => Date.UTC(b.year, b.month - 1, b.day) - Date.UTC(a.year, a.month - 1, a.day))[0];
    const date = `${latest.year}-${String(latest.month).padStart(2, '0')}-${String(latest.day).padStart(2, '0')}`;
    return subtractDays(date, Math.max(0, config.sync.overlapDays));
  }
  return subtractDays(localParts(new Date(), config.sync.timezone).date, Math.max(0, config.sync.defaultLookbackDays));
}

function sheetExportUrl(sheetUrl) {
  const url = new URL(sheetUrl);
  const match = url.pathname.match(/\/spreadsheets\/d\/([^/]+)/);
  if (!match) throw new Error('Could not extract spreadsheet id');
  const hashGid = new URLSearchParams(url.hash.replace(/^#/, '')).get('gid');
  const gid = url.searchParams.get('gid') || hashGid || '0';
  return `https://docs.google.com/spreadsheets/d/${match[1]}/export?format=csv&gid=${encodeURIComponent(gid)}`;
}

async function fetchSheetCsv(cdp, config) {
  const exportUrl = `${sheetExportUrl(config.spreadsheet.url)}&_=${Date.now()}`;
  const { frameTree } = await cdp.send('Page.getFrameTree');
  const { resource } = await cdp.send('Network.loadNetworkResource', {
    frameId: frameTree.frame.id,
    url: exportUrl,
    options: { disableCache: true, includeCredentials: true },
  });
  const headers = resource.headers || {};
  const contentType = headers['content-type'] || headers['Content-Type'] || '';
  if (!resource.success || resource.httpStatusCode !== 200 || !resource.stream || !/text\/csv|application\/octet-stream/.test(contentType)) {
    throw new Error(`Could not export the spreadsheet with the configured Google session (status=${resource.httpStatusCode || 'unknown'}, type=${contentType || 'unknown'}, netError=${resource.netErrorName || resource.netError || 'none'})`);
  }
  const chunks = [];
  try {
    while (true) {
      const part = await cdp.send('IO.read', { handle: resource.stream });
      chunks.push(part.base64Encoded ? Buffer.from(part.data, 'base64') : Buffer.from(part.data));
      if (part.eof) break;
    }
  } finally {
    await cdp.send('IO.close', { handle: resource.stream }).catch(() => {});
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function inspectSheet(cdp, config) {
  await navigate(cdp, config.spreadsheet.url);
  await sleep(2500);
  let csv;
  try {
    csv = await fetchSheetCsv(cdp, config);
  } catch (error) {
    const state = await pageState(cdp).catch(() => ({ host: 'unknown', title: 'unknown', text: '' }));
    const authPrompt = /sign in|ログイン|アカウントを選択/i.test(`${state.title} ${state.text}`);
    throw new Error(`${error.message}; pageHost=${state.host}, pageTitle=${JSON.stringify(state.title)}, authPrompt=${authPrompt}`);
  }
  return workbookModel(parseCsv(csv), config);
}

async function pageState(cdp) {
  return evaluate(cdp, `(() => ({
    host: location.hostname,
    href: location.href,
    title: document.title,
    text: String(document.body?.innerText || '').replace(/\\s+/g, ' ').slice(0, 2500),
    inputs: [...document.querySelectorAll('input')].filter((e) => e.offsetWidth || e.offsetHeight || e.getClientRects().length)
      .map((e) => ({ id: e.id, name: e.name, type: e.type, placeholder: e.placeholder })).slice(0, 30)
  }))()`);
}

function isMicrosoftCodePrompt(state) {
  const haystack = `${state.title || ''} ${state.text || ''}`;
  const hasCodeInput = (state.inputs || []).some((input) => ['tel', 'text', 'number'].includes(input.type));
  return hasCodeInput
    && /verification code|confirmation code|security code|enter code|code entry|確認コード|セキュリティ コード|コード(?:を|の)入力|コードを送信しました|メール.*コード/i.test(haystack);
}

function isMicrosoftEmailEntryState(state) {
  return (state.inputs || []).some((input) =>
    input.type === 'email' || input.id === 'i0116' || input.name === 'loginfmt');
}

function isMicrosoftInvalidLoginState(state) {
  return /AADSTS90100|login parameter is empty or not valid/i.test(`${state.title || ''} ${state.text || ''}`);
}

function isTeamsHost(host) {
  return TEAMS_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
}

async function clearMicrosoftAuthState(cdp) {
  for (const origin of MICROSOFT_AUTH_ORIGINS) {
    await cdp.send('Storage.clearDataForOrigin', {
      origin,
      storageTypes: 'cookies,local_storage,session_storage,indexeddb,cache_storage,service_workers',
    });
  }
}

async function setFirstVisibleInput(cdp, types, value) {
  return evaluate(cdp, `((types, value) => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const input = [...document.querySelectorAll('input')].filter(visible)
      .find((e) => types.includes(String(e.type || '').toLowerCase()) && !e.disabled && !e.readOnly);
    if (!input) return false;
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const submit = document.getElementById('idSIButton9') || [...document.querySelectorAll('button,input[type=submit]')]
      .filter(visible).find((e) => /next|次へ|sign in|サインイン|continue|続行|verify|確認/i.test(e.innerText || e.value || e.id || ''));
    if (submit) submit.click(); else if (input.form) input.form.requestSubmit();
    return true;
  })(${JSON.stringify(types)}, ${JSON.stringify(value)})`);
}

async function typeMicrosoftEmail(cdp, email) {
  const focused = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const input = [...document.querySelectorAll('input')].filter(visible)
      .find((e) => ['email', 'text', ''].includes(String(e.type || '').toLowerCase()) && !e.disabled && !e.readOnly);
    if (!input) return false;
    input.focus(); input.select(); return true;
  })()`);
  if (!focused) return false;
  await cdp.send('Input.insertText', { text: email });
  return evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const submit = document.getElementById('idSIButton9') || [...document.querySelectorAll('button,input[type=submit]')]
      .filter(visible).find((e) => /next|次へ|sign in|サインイン|continue|続行/i.test(e.innerText || e.value || e.id || ''));
    if (submit) { submit.click(); return true; }
    const input = document.activeElement;
    if (input?.form) { input.form.requestSubmit(); return true; }
    return false;
  })()`);
}

async function typeMicrosoftCode(cdp, code, email) {
  const focused = await evaluate(cdp, `(() => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const input = [...document.querySelectorAll('input')].filter(visible)
      .find((e) => ['tel', 'text', 'number'].includes(String(e.type || '').toLowerCase()) && !e.disabled && !e.readOnly);
    if (!input) return false;
    input.focus(); input.select(); return true;
  })()`);
  if (!focused) return false;
  await cdp.send('Input.insertText', { text: code });
  return evaluate(cdp, `((email) => {
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    for (const input of document.querySelectorAll('input[name="login"], input[name="loginfmt"]')) {
      setter.call(input, email);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const submit = document.getElementById('idSIButton9') || [...document.querySelectorAll('button,input[type=submit]')]
      .filter(visible).find((e) => /next|次へ|sign in|サインイン|continue|続行|verify|確認/i.test(e.innerText || e.value || e.id || ''));
    if (submit) { submit.click(); return true; }
    const input = document.activeElement;
    if (input?.form) { input.form.requestSubmit(); return true; }
    return false;
  })(${JSON.stringify(email)})`);
}

async function chooseMicrosoftEmailCode(cdp) {
  return evaluate(cdp, `(() => {
    const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const controls = [...document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button], [role=option], [role=listitem]')].filter(visible);
    const emailCode = controls.find((e) => {
      const text = compact(e.innerText || e.textContent || e.value || e.getAttribute('aria-label') || e.title);
      return /sign in with (?:a )?code|send (?:me )?(?:a )?code|email.*code|code.*email|コードでサインイン|コードを(?:送信|使用)|メール.*コード|コード.*メール/i.test(text);
    });
    if (emailCode) { emailCode.click(); return 'email_code'; }
    return null;
  })()`);
}

async function openMicrosoftSignInOptions(cdp) {
  return evaluate(cdp, `(() => {
    const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const option = document.getElementById('idA_PWD_SwitchToCredPicker')
      || [...document.querySelectorAll('button,a,[role=button]')].filter(visible).find((e) =>
        /sign[ -]?in options|サインイン オプション|その他のサインイン方法|別の方法でサインイン/i
          .test(compact(e.innerText || e.textContent || e.getAttribute('aria-label'))));
    if (!option) return false;
    option.click(); return true;
  })()`);
}

async function obtainVerificationCode(browser, config, timeoutMs, requestedAt) {
  const email = config.accounts.googleEmail;
  const afterEpoch = Math.floor((requestedAt - 15000) / 1000);
  const query = `from:${config.teams.verificationSender} after:${afterEpoch}`;
  const gmailUrl = `https://mail.google.com/mail/u/0/?authuser=${encodeURIComponent(email)}#search/${encodeURIComponent(query)}`;
  const gmail = await connectAuxPage(browser, gmailUrl);
  try {
    const initialDelay = Math.max(0, 5000 - (Date.now() - requestedAt));
    if (initialDelay) await sleep(initialDelay);
    await navigate(gmail, gmailUrl);
    let openedMessage = false;
    let lastReloadAt = Date.now();
    return await waitFor(gmail, async () => {
      const result = await evaluate(gmail, `(() => {
        const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const bodies = [...document.querySelectorAll('.a3s.aiL, [data-message-id] .a3s')].filter(visible);
        const body = bodies.at(-1)?.innerText || '';
        const match = body.match(/(?:アカウント確認コード|account verification code|security code|確認コード)[^0-9]{0,120}([0-9]{6,8})/i);
        const firstRow = [...document.querySelectorAll('tr.zA, [role=main] tr')].find(visible);
        const sender = document.querySelector('.gD[email]')?.getAttribute('email') || '';
        return {
          host: location.hostname,
          login: /accounts\\.google\\.com/.test(location.href),
          code: match ? match[1] : null,
          hasRow: !!firstRow,
          sender
        };
      })()`);
      if (result.login) return { error: 'Google authentication is missing from the configured Chrome profile' };
      if (result.host === 'mail.google.com' && result.code) {
        return {
          code: result.code,
          senderMatches: result.sender.toLowerCase() === config.teams.verificationSender.toLowerCase(),
        };
      }
      if (result.hasRow && !openedMessage) {
        openedMessage = await evaluate(gmail, `(() => {
          const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const row = [...document.querySelectorAll('tr.zA, [role=main] tr')].find(visible);
          if (!row) return false;
          row.click(); return true;
        })()`);
        return null;
      }
      if (!result.hasRow && Date.now() - lastReloadAt > 8000) {
        lastReloadAt = Date.now();
        await gmail.send('Page.reload').catch(() => {});
      }
      return null;
    }, { timeoutMs, intervalMs: 2000, description: 'the Microsoft verification email in Gmail' });
  } finally {
    await gmail.send('Page.close').catch(() => {});
    gmail.close();
  }
}

async function settleTeamsLogin(cdp, browser, config, timeoutMs, targetUrl) {
  const started = Date.now();
  let emailSubmitted = false;
  let codeSubmitted = false;
  let staySignedInHandled = false;
  let teamsSeenAt = null;
  let codeRouteAttempts = 0;
  let codeRequestedAt = null;
  let codeSubmittedAt = null;
  let authRecoveryAttempts = 0;
  let emailSubmitAttempts = 0;
  let emailSubmittedAt = null;
  while (Date.now() - started < timeoutMs) {
    await sleep(1000);
    const state = await pageState(cdp).catch(() => null);
    if (!state) continue;
    if (isTeamsHost(state.host)) {
      teamsSeenAt ||= Date.now();
      const appReady = await evaluate(cdp, `!!document.querySelector('[data-tid="app-layout-area--main"], [data-tid="message-pane-list-viewport"], [data-tid="chat-pane-list"]')`).catch(() => false);
      if (appReady || Date.now() - teamsSeenAt > 8000) return;
      continue;
    }
    teamsSeenAt = null;
    const haystack = `${state.title} ${state.text}`;
    if (isMicrosoftInvalidLoginState(state)) {
      if (authRecoveryAttempts >= 1) throw new Error('Microsoft rejected the reconstructed sign-in request with AADSTS90100');
      authRecoveryAttempts += 1;
      await clearMicrosoftAuthState(cdp);
      await navigate(cdp, targetUrl);
      emailSubmitted = false;
      codeSubmitted = false;
      staySignedInHandled = false;
      codeRouteAttempts = 0;
      codeRequestedAt = null;
      codeSubmittedAt = null;
      emailSubmitAttempts = 0;
      emailSubmittedAt = null;
      continue;
    }
    if (codeSubmittedAt && Date.now() - codeSubmittedAt > 1500 && /try again|もう一度お試しください|正しくありません|incorrect/i.test(haystack)) {
      throw new Error('Microsoft rejected the newly retrieved email verification code');
    }
    if (!emailSubmitted && /login\.microsoftonline\.com/.test(state.host) && /sign in|サインイン|email|メール|account/i.test(haystack)) {
      if (await typeMicrosoftEmail(cdp, config.accounts.teamsLoginEmail)) {
        emailSubmitted = true;
        emailSubmitAttempts += 1;
        emailSubmittedAt = Date.now();
        continue;
      }
    }
    const hasPassword = state.inputs.some((input) => input.type === 'password');
    if (hasPassword && emailSubmitted && isMicrosoftEmailEntryState(state)) {
      if (Date.now() - emailSubmittedAt < 4000) continue;
      if (emailSubmitAttempts < 2 && await typeMicrosoftEmail(cdp, config.accounts.teamsLoginEmail)) {
        emailSubmitAttempts += 1;
        emailSubmittedAt = Date.now();
        continue;
      }
    }
    if (hasPassword) {
      const choice = await chooseMicrosoftEmailCode(cdp);
      if (choice) { codeRouteAttempts += 1; codeRequestedAt = Date.now(); await sleep(1200); continue; }
      if (emailSubmitted && codeRouteAttempts < 2 && await openMicrosoftSignInOptions(cdp)) {
        codeRouteAttempts += 1;
        await sleep(1200);
        continue;
      }
      const controls = await evaluate(cdp, `(() => {
        const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
        const redact = (s) => compact(s).replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/ig, '<email>');
        const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        return {
          title: redact(document.title),
          text: redact(document.body?.innerText || '').slice(0, 800),
          inputs: [...document.querySelectorAll('input')].filter(visible)
            .map((e) => ({ type: e.type, id: e.id, name: e.name, placeholder: redact(e.placeholder) })).slice(0, 20),
          controls: [...document.querySelectorAll('button,a,input[type=button],input[type=submit],[role=button]')].filter(visible)
            .map((e) => redact(e.innerText || e.textContent || e.value || e.getAttribute('aria-label') || e.title)).filter(Boolean).slice(0, 30)
        };
      })()`).catch(() => []);
      throw new Error(`Microsoft passwordless email-code action was not found; diagnostic=${JSON.stringify(controls)}`);
    }
    if (!codeSubmitted && codeRouteAttempts < 4 && /code|コード|email|メール/i.test(haystack)) {
      const choice = await chooseMicrosoftEmailCode(cdp);
      if (choice) { codeRouteAttempts += 1; codeRequestedAt = Date.now(); await sleep(1200); continue; }
    }
    const requestsCode = isMicrosoftCodePrompt(state);
    if (!codeSubmitted && requestsCode) {
      const verification = await obtainVerificationCode(browser, config, Math.min(timeoutMs, 120000), codeRequestedAt || Date.now());
      if (!verification.senderMatches) throw new Error('The newest verification email did not match the configured Microsoft sender');
      if (!await typeMicrosoftCode(cdp, verification.code, config.accounts.teamsLoginEmail)) throw new Error('Microsoft verification-code input was not found');
      codeSubmitted = true;
      codeSubmittedAt = Date.now();
      continue;
    }
    if (!staySignedInHandled && /stay signed in|サインインの状態を維持/i.test(haystack)) {
      const clicked = await evaluate(cdp, `(() => {
        const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const button = document.getElementById('idSIButton9') || [...document.querySelectorAll('button,input[type=submit]')]
          .filter(visible).find((e) => /yes|はい|continue|続行/i.test(e.innerText || e.value || ''));
        if (button) { button.click(); return true; }
        return false;
      })()`);
      staySignedInHandled = clicked;
      continue;
    }
  }
  const diagnostic = await evaluate(cdp, `(() => {
    const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    const redact = (s) => compact(s).replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/ig, '<email>');
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    return {
      host: location.hostname,
      title: redact(document.title),
      inputs: [...document.querySelectorAll('input')].filter(visible).map((e) => ({ type: e.type, id: e.id, name: e.name })).slice(0, 20),
      controls: [...document.querySelectorAll('button,a,[role=button],[role=option],[role=listitem]')].filter(visible)
        .map((e) => redact(e.innerText || e.textContent || e.getAttribute('aria-label'))).filter(Boolean).slice(0, 30)
    };
  })()`).catch(() => ({ host: 'unknown', title: 'unknown', inputs: [], controls: [] }));
  throw new Error(`Teams sign-in timed out before reaching ${new URL(targetUrl).hostname}; loginDiagnostic=${JSON.stringify(diagnostic)}`);
}

function teamsTargetUrl(config) {
  const base = `https://teams.microsoft.com/v2/?tenantId=${encodeURIComponent(config.teams.tenantId)}`;
  return config.teams.chatId
    ? `${base}#/conversations/${encodeURIComponent(config.teams.chatId)}?ctx=chat`
    : base;
}

async function openTeamsChat(cdp, browser, config, timeoutMs) {
  const url = teamsTargetUrl(config);
  await clearMicrosoftAuthState(cdp);
  await navigate(cdp, url);
  await settleTeamsLogin(cdp, browser, config, timeoutMs, url);
  try {
    await waitFor(cdp, async () => {
      const result = await evaluate(cdp, `(() => ({
        pane: !!document.querySelector('[data-tid="message-pane-list-viewport"], [data-tid="chat-pane-list"], [data-tid="message-pane-list-runway"]'),
        text: String(document.body?.innerText || '').slice(0, 5000)
      }))()`);
      if (!result.pane && config.teams.chatName && result.text.includes(config.teams.chatName)) {
        await evaluate(cdp, `((name) => {
          const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
          const target = [...document.querySelectorAll('[role=treeitem], [role=listitem], button, a')]
            .find((e) => compact(e.innerText || e.getAttribute('aria-label')) === name);
          if (target) target.click();
        })(${JSON.stringify(config.teams.chatName)})`);
      }
      return result.pane ? true : null;
    }, { timeoutMs, intervalMs: 1500, description: `Teams chat ${config.teams.chatName}` });
  } catch (error) {
    const diagnostic = await evaluate(cdp, `(() => {
      const body = String(document.body?.innerText || '');
      return {
        host: location.hostname,
        title: document.title,
        authPrompt: /sign in|サインイン|verification code|確認コード|password|パスワード/i.test(body),
        dataTids: [...new Set([...document.querySelectorAll('[data-tid]')].map((e) => e.getAttribute('data-tid')))].slice(0, 100),
        iframeCount: document.querySelectorAll('iframe').length
      };
    })()`).catch(() => ({ host: 'unknown', title: 'unknown', authPrompt: false, dataTids: [], iframeCount: -1 }));
    throw new Error(`${error.message}; teamsDiagnostic=${JSON.stringify(diagnostic)}`);
  }
}

async function scrapeTeamsEvents(cdp, cutoffDate, timeoutMs) {
  const cutoffInstant = new Date(`${cutoffDate}T00:00:00+09:00`).valueOf();
  const records = new Map();
  const started = Date.now();
  let stagnant = 0;
  let lastSignature = '';
  while (Date.now() - started < timeoutMs) {
    const batch = await evaluate(cdp, `(() => {
      const compact = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
      const viewport = document.querySelector('[data-tid="message-pane-list-viewport"]')
        || document.querySelector('[data-tid="chat-pane-list"]')
        || document.querySelector('[data-tid="message-pane-list-runway"]')?.parentElement;
      if (!viewport) return { items: [], top: null, height: null };
      const roots = [...viewport.querySelectorAll('[data-tid="chat-pane-item"], [data-tid="chat-pane-message"]')];
      const items = roots.map((root) => {
        const time = root.querySelector('time[datetime]') || root.closest('[data-tid="chat-pane-item"]')?.querySelector('time[datetime]');
        const author = root.querySelector('[data-tid="message-author-name"]')
          || root.closest('[data-tid="chat-pane-item"]')?.querySelector('[data-tid="message-author-name"]');
        const content = root.querySelector('[data-message-content], [data-tid="chat-pane-message"]') || root;
        return { datetime: time?.getAttribute('datetime') || '', author: compact(author?.textContent), text: compact(content?.innerText) };
      }).filter((item) => item.datetime && item.text);
      return { items, top: viewport.scrollTop, height: viewport.scrollHeight };
    })()`);
    for (const item of batch.items) records.set(`${item.datetime}|${item.author}|${item.text}`, item);
    const instants = [...records.values()].map((item) => new Date(item.datetime).valueOf()).filter(Number.isFinite);
    if (instants.length && Math.min(...instants) <= cutoffInstant) break;
    const signature = `${records.size}:${Math.min(...instants, Infinity)}:${batch.height}`;
    stagnant = signature === lastSignature ? stagnant + 1 : 0;
    lastSignature = signature;
    if (stagnant >= 4 && Number(batch.top) === 0) break;
    await evaluate(cdp, `(() => {
      const viewport = document.querySelector('[data-tid="message-pane-list-viewport"]')
        || document.querySelector('[data-tid="chat-pane-list"]')
        || document.querySelector('[data-tid="message-pane-list-runway"]')?.parentElement;
      if (viewport) { viewport.scrollTop = 0; viewport.dispatchEvent(new Event('scroll', { bubbles: true })); }
    })()`);
    await sleep(1800);
  }
  const events = [...records.values()].sort((a, b) => new Date(a.datetime) - new Date(b.datetime));
  if (!events.length) throw new Error('No timestamped Teams chat messages were found');
  return events;
}

async function selectSheetRange(cdp, range) {
  const found = await evaluate(cdp, `(() => {
    const box = document.querySelector('#t-name-box, input[aria-label*="Name box"], input[aria-label*="名前ボックス"]');
    if (!box) return false;
    box.focus(); box.select(); return true;
  })()`);
  if (!found) throw new Error('Google Sheets name box was not found; edit access may be missing');
  await cdp.send('Input.insertText', { text: range });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await sleep(400);
}

async function pasteColumn(cdp, column, startRow, values) {
  if (!values.length) return;
  const range = `${columnName(column)}${startRow}:${columnName(column)}${startRow + values.length - 1}`;
  await selectSheetRange(cdp, range);
  await evaluate(cdp, `navigator.clipboard.writeText(${JSON.stringify(values.join('\n'))})`, true);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'v', code: 'KeyV', modifiers: 2, windowsVirtualKeyCode: 86, nativeVirtualKeyCode: 86 });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'v', code: 'KeyV', modifiers: 2, windowsVirtualKeyCode: 86, nativeVirtualKeyCode: 86 });
  await sleep(900);
}

async function applyIntervals(cdp, config, model, intervals) {
  const origin = new URL(config.spreadsheet.url).origin;
  await cdp.send('Browser.grantPermissions', {
    origin,
    permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'],
  }).catch(async () => {
    await cdp.send('Browser.grantPermissions', { origin, permissions: ['clipboardReadWrite'] });
  });
  await cdp.send('Page.bringToFront');
  await pasteColumn(cdp, model.columns.date, model.appendRow, intervals.map((interval) => interval.date));
  await pasteColumn(cdp, model.columns.start, model.appendRow, intervals.map((interval) => interval.start));
  await pasteColumn(cdp, model.columns.end, model.appendRow, intervals.map((interval) => interval.end));
  await sleep(3000);
}

function printableModel(model) {
  return {
    headerRow: model.headerRow,
    columns: Object.fromEntries(Object.entries(model.columns).map(([key, value]) => [key, columnName(value)])),
    appendRow: model.appendRow,
    existingIntervals: model.existingKeys.size,
  };
}

function runSelfTest() {
  assert.deepEqual(parseCsv('a,"b,b","c""d"\r\n1,2,3\n'), [['a', 'b,b', 'c"d'], ['1', '2', '3']]);
  const rows = [
    ['勤怠表', '', '', '', ''],
    ['メモ', '終了', '合計', '日付', '開始'],
    ['', '18:00', '=E3-B3', '7/20', '9:00'],
    ['固定の集計', '', '', '', ''],
  ];
  const aliases = { date: ['日付'], start: ['開始'], end: ['終了'] };
  const model = inferSheetModel(rows, aliases);
  assert.deepEqual(printableModel(model), {
    headerRow: 2,
    columns: { date: 'D', start: 'E', end: 'B' },
    appendRow: 4,
    existingIntervals: 1,
  });
  assert(model.existingKeys.has('7/20|09:00|18:00'));
  const contractConfig = {
    spreadsheet: {
      layoutContract: INTEGRATED_LAYOUT_CONTRACT,
      headerAliases: { date: ['日付'], start: ['出勤'], end: ['退勤'] },
    },
  };
  const contractRows = [
    ['日付', '出勤', '退勤', '労働時間'],
    ['7/20', '09:00', '18:00', '=IFERROR(C2-B2,"")'],
  ];
  assert.equal(workbookModel(contractRows, contractConfig).appendRow, 3);
  assert.throws(
    () => workbookModel([['日付', '出勤', '退勤', '勤務時間']], contractConfig),
    /does not match integrated-attendance-v1/,
  );
  assert.throws(
    () => workbookModel([['メモ'], ...contractRows], contractConfig),
    /does not match integrated-attendance-v1/,
  );
  const config = {
    teams: {
      authorDisplayName: 'Example User',
      messages: { start: ['開始します'], pause: ['中断します'], resume: ['再開します'], end: ['終了します'] },
    },
    sync: { timezone: 'Asia/Tokyo' },
  };
  const events = [
    { datetime: '2026-07-20T00:00:02Z', author: 'Example User', text: '開始します' },
    { datetime: '2026-07-20T03:00:59Z', author: 'Example User', text: '中断します' },
    { datetime: '2026-07-20T04:00:10Z', author: 'Example User', text: '再開します' },
    { datetime: '2026-07-20T09:00:11Z', author: 'Example User', text: '終了します' },
    { datetime: '2026-07-21T00:00:00Z', author: '他の人', text: '開始します' },
  ];
  const paired = buildIntervals(events, config, '2026-07-20');
  assert.deepEqual(paired.intervals.map(({ date, start, end }) => ({ date, start, end })), [
    { date: '7/20', start: '09:00', end: '12:00' },
    { date: '7/20', start: '13:00', end: '18:00' },
  ]);
  assert.equal(paired.anomalies.length, 0);
  assert.equal(deriveSince({ datedRows: [{ year: 2026, month: 7, day: 20 }], existingKeys: new Set() }, { sync: { overlapDays: 2, defaultLookbackDays: 31 }, timezone: 'Asia/Tokyo' }, null), '2026-07-18');
  assert.equal(sheetExportUrl('https://docs.google.com/spreadsheets/d/abc/edit?gid=17#gid=17'), 'https://docs.google.com/spreadsheets/d/abc/export?format=csv&gid=17');
  assert.equal(isMicrosoftCodePrompt({
    title: 'アカウントにサインイン',
    text: 'コードの入力 <email> にコードを送信しました',
    inputs: [{ type: 'tel', id: 'idTxtBx_OTC_Password' }],
  }), true);
  assert.equal(isMicrosoftCodePrompt({
    title: 'Sign in to your account',
    text: 'Enter code',
    inputs: [{ type: 'tel', id: 'otc' }],
  }), true);
  assert.equal(isMicrosoftCodePrompt({
    title: 'アカウントにサインイン',
    text: 'メールアドレスを入力してください',
    inputs: [{ type: 'text', id: 'i0116' }],
  }), false);
  assert.equal(isMicrosoftEmailEntryState({
    inputs: [
      { type: 'email', id: 'i0116', name: 'loginfmt' },
      { type: 'password', id: 'i0118', name: 'passwd' },
    ],
  }), true);
  assert.equal(isMicrosoftEmailEntryState({
    inputs: [{ type: 'tel', id: 'idTxtBx_OTC_Password', name: 'npotc' }],
  }), false);
  assert.equal(isMicrosoftInvalidLoginState({
    title: 'アカウントにサインイン',
    text: 'AADSTS90100: login parameter is empty or not valid.',
  }), true);
  assert.equal(isMicrosoftInvalidLoginState({
    title: 'アカウントにサインイン',
    text: 'コードの入力',
  }), false);
  assert.deepEqual(MICROSOFT_AUTH_ORIGINS, [
    'https://login.microsoftonline.com',
    'https://login.live.com',
    'https://account.live.com',
    'https://account.microsoft.com',
    'https://teams.microsoft.com',
    'https://teams.cloud.microsoft',
  ]);
  assert.equal(isTeamsHost('teams.microsoft.com'), true);
  assert.equal(isTeamsHost('teams.cloud.microsoft'), true);
  assert.equal(isTeamsHost('chat.teams.cloud.microsoft'), true);
  assert.equal(isTeamsHost('teams.cloud.microsoft.example.com'), false);
  console.log('SELF_TEST_OK');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { usage(); return; }
  if (args.selfTest) { runSelfTest(); return; }
  const config = readConfig(args.config);
  const browser = await launchChrome(config, args.headed);
  let cdp;
  try {
    cdp = await connectPage(browser);
    const model = await inspectSheet(cdp, config);
    console.log(`SHEET ${JSON.stringify(printableModel(model))}`);
    if (args.inspectSheet) return;

    const since = deriveSince(model, config, args.since);
    console.log(`WINDOW since=${since} timezone=${config.sync.timezone}`);
    await openTeamsChat(cdp, browser, config, args.timeoutMs);
    const events = await scrapeTeamsEvents(cdp, subtractDays(since, 1), args.timeoutMs);
    const paired = buildIntervals(events, config, since);
    const blockers = paired.anomalies.filter((anomaly) => anomaly.type !== 'trailing_open' && anomaly.date >= since);
    const missing = paired.intervals.filter((interval) => !model.existingKeys.has(intervalKey(interval.date, interval.start, interval.end)));
    console.log(`RESULT matched_events=${paired.relevant.length} closed_intervals=${paired.intervals.length} missing=${missing.length} anomalies=${paired.anomalies.length}`);
    for (const interval of missing) console.log(`PROPOSE ${interval.date} ${interval.start}-${interval.end}`);
    for (const anomaly of paired.anomalies) console.log(`ANOMALY ${anomaly.type} ${anomaly.date} ${anomaly.time}`);
    if (!args.apply) { console.log('DRY_RUN no spreadsheet changes were made'); return; }
    if (blockers.length) throw new Error(`Refusing to apply because ${blockers.length} punch-order anomalies affect the candidate window`);
    if (!missing.length) { console.log('APPLY no missing intervals'); return; }

    await navigate(cdp, config.spreadsheet.url);
    await sleep(3000);
    const freshModel = workbookModel(parseCsv(await fetchSheetCsv(cdp, config)), config);
    const stillMissing = missing.filter((interval) => !freshModel.existingKeys.has(intervalKey(interval.date, interval.start, interval.end)));
    if (!stillMissing.length) { console.log('APPLY no missing intervals after pre-write refresh'); return; }
    await applyIntervals(cdp, config, freshModel, stillMissing);
    const verifiedModel = workbookModel(parseCsv(await fetchSheetCsv(cdp, config)), config);
    const unverified = stillMissing.filter((interval) => !verifiedModel.existingKeys.has(intervalKey(interval.date, interval.start, interval.end)));
    if (unverified.length) throw new Error(`Post-write verification failed for ${unverified.length} intervals; do not retry blindly`);
    console.log(`APPLIED count=${stillMissing.length} verified=true first=${stillMissing[0].isoDate} last=${stillMissing.at(-1).isoDate}`);
  } finally {
    cdp?.close();
    await stopChrome(browser);
  }
}

main().catch((error) => {
  console.error(`ERROR ${error.message}`);
  process.exitCode = 1;
});
