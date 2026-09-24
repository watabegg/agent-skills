import test from 'node:test';
import assert from 'node:assert/strict';
import {parseArgs, runWorkflow} from '../scripts/invoice_workflow.mjs';
const input = {mode: 'draft', month: '2026-08', pdf: '/tmp/example invoice.pdf'};
const marks = {export: 'EXPORT_OK', verify_pdf: 'PDF_OK', create_draft: 'DRAFT_OK', normalize_recipients: 'DRAFT_UPDATED'};
function runner(calls, failing) {
  return async (command, args, {stage}) => {
    calls.push({command, args, stage});
    return {status: stage === failing ? 1 : 0, stdout: marks[stage] || ''};
  };
}
test('draft without a visual check does not run any command', async () => {
  const calls = [];
  const result = await runWorkflow(input, runner(calls));
  assert.equal(result.status, 'needs_input');
  assert.equal(calls.length, 0);
});
test('prepare stops with a preview and never creates a draft', async () => {
  const calls = [];
  const result = await runWorkflow({...input, mode: 'prepare'}, runner(calls));
  assert.equal(result.status, 'needs_input');
  assert.ok(result.artifacts.preview.endsWith('.png'));
  assert.deepEqual(calls.map(x => x.stage), ['export', 'verify_pdf', 'render_pdf']);
  assert.ok(calls[0].args.includes(input.pdf));
});
test('verification failure prevents all draft writes', async () => {
  const calls = [];
  const result = await runWorkflow({...input, visualChecked: true}, runner(calls, 'verify_pdf'));
  assert.equal(result.status, 'failed');
  assert.equal(result.changed, false);
  assert.deepEqual(calls.map(x => x.stage), ['verify_pdf']);
});
test('verified draft explicitly applies recipient normalization; failures preserve uncertainty', async () => {
  for (const failure of [undefined, 'normalize_recipients']) {
    const calls = [];
    const result = await runWorkflow({...input, visualChecked: true}, runner(calls, failure));
    assert.deepEqual(calls.map(x => x.stage), ['verify_pdf', 'create_draft', 'normalize_recipients']);
    assert.ok(calls.at(-1).args.includes('--apply'));
    assert.equal(result.status, failure ? 'failed' : 'completed');
    assert.equal(result.changed, failure ? null : true);
    if (!failure) assert.equal(result.sent, false);
  }
});
test('export failure requires sheet inspection before retry', async () => {
  const calls = [];
  const result = await runWorkflow({...input, mode: 'prepare'}, runner(calls, 'export'));
  assert.equal(result.changed, null);
  assert.equal(result.retry, 'inspect_sheet_before_retry');
  assert.equal(calls.length, 1);
});
test('invalid modes, months and paths are rejected before execution', () => {
  assert.throws(() => parseArgs(['--prepare', '--draft']));
  assert.throws(() => parseArgs(['--prepare', '--month', '2026-13', '--pdf', input.pdf]));
  assert.throws(() => parseArgs(['--prepare', '--month', input.month, '--pdf', 'relative.pdf']));
});
