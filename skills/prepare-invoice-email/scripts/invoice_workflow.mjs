#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const scripts = path.dirname(fileURLToPath(import.meta.url));
const usage = `Usage:
  node invoice_workflow.mjs --prepare --month YYYY-MM --pdf /absolute/invoice.pdf [--config /path/config.json]
  node invoice_workflow.mjs --draft --month YYYY-MM --pdf /absolute/invoice.pdf --visual-checked [--config /path/config.json]

--prepare exports, verifies and renders the invoice, then stops for visual review (exit 2).
--draft requires visual review, verifies the PDF again, creates/resumes the draft and normalizes recipients.
Result JSON: completed=exit 0; needs_input=exit 2; failed=exit 1. Mail is never sent.
`;

export function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (['--month', '--pdf', '--config'].includes(arg)) {
      const value = argv[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for ${arg}`);
      args[arg.slice(2)] = value;
    } else if (arg === '--prepare' || arg === '--draft') {
      if (args.mode) throw new Error('Choose one of --prepare or --draft');
      args.mode = arg.slice(2);
    } else if (arg === '--visual-checked') args.visualChecked = true;
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (args.help) return args;
  if (!args.mode || !/^\d{4}-(0[1-9]|1[0-2])$/.test(args.month || '') || !args.pdf || !path.isAbsolute(args.pdf)) {
    throw new Error('A mode, valid --month YYYY-MM and absolute --pdf path are required');
  }
  if (args.mode === 'prepare' && args.visualChecked) throw new Error('Review the newly exported PDF before using --draft');
  return args;
}

export async function runWorkflow(args, run) {
  const preview = `${args.pdf}.preview.png`;
  const log = `${args.pdf}.${args.mode}.log`;
  const artifacts = {pdf: args.pdf, log};
  if (args.mode === 'draft' && !args.visualChecked) {
    return {status: 'needs_input', reason: 'visual_review_required', changed: false, artifacts: {pdf: args.pdf, preview}};
  }
  const common = ['--month', args.month, '--pdf', args.pdf, ...(args.config ? ['--config', args.config] : [])];
  let stage = 'prepare';
  let writeStarted = false;
  async function step(name, command, params, marker) {
    stage = name;
    const result = await run(command, params, {stage, log});
    if (result.status !== 0 || (marker && !String(result.stdout || '').includes(marker))) {
      throw new Error(`Step ${name} did not verify completion`);
    }
  }
  const invoice = path.join(scripts, 'invoice_email_draft.mjs');
  try {
    if (args.mode === 'prepare') {
      writeStarted = true;
      await step('export', process.execPath, [invoice, '--export', ...common], 'EXPORT_OK');
      writeStarted = false;
    }
    await step('verify_pdf', process.execPath, [invoice, '--verify-pdf', ...common], 'PDF_OK');
    if (args.mode === 'prepare') {
      await step('render_pdf', 'pdftoppm', ['-f', '1', '-singlefile', '-scale-to', '1600', '-png', args.pdf, `${args.pdf}.preview`]);
      return {status: 'needs_input', reason: 'visual_review_required', changed: false, artifacts: {...artifacts, preview}};
    }
    writeStarted = true;
    await step('create_draft', process.execPath, [invoice, '--draft', ...common], 'DRAFT_OK');
    await step('normalize_recipients', process.execPath, [path.join(scripts, 'normalize_invoice_recipients.mjs'), '--apply', ...common], 'DRAFT_UPDATED');
    return {status: 'completed', reason: 'draft_verified', changed: true, sent: false, artifacts};
  } catch {
    return {status: 'failed', reason: `${stage}_failed`, changed: writeStarted ? null : false,
      retry: writeStarted ? (args.mode === 'prepare' ? 'inspect_sheet_before_retry' : 'inspect_draft_before_retry') : 'diagnose', artifacts};
  }
}

function runCommand(command, params, {stage, log}) {
  fs.mkdirSync(path.dirname(log), {recursive: true});
  const result = spawnSync(command, params, {encoding: 'utf8', timeout: 600000, maxBuffer: 8 * 1024 * 1024});
  fs.appendFileSync(log, `\n[${stage}]\n${result.stdout || ''}${result.stderr || ''}${result.error ? `\n${result.error.message}\n` : ''}`, {mode: 0o600});
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const args = parseArgs(process.argv.slice(2));
    if (args.help) console.log(usage);
    else {
      const result = await runWorkflow(args, runCommand);
      console.log(JSON.stringify(result));
      process.exitCode = result.status === 'completed' ? 0 : result.status === 'needs_input' ? 2 : 1;
    }
  } catch (error) {
    console.error(error.message);
    console.log(JSON.stringify({status: 'failed', reason: 'invalid_arguments', changed: false, artifacts: {}}));
    process.exitCode = 1;
  }
}
