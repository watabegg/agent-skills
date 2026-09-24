import test from 'node:test';
import assert from 'node:assert/strict';
import {navigationOutcome, settleAuth} from '../scripts/shinshu_portal_cdp.mjs';
const target = 'https://lms.ealps.shinshu-u.ac.jp/course/view.php?id=1';
const ready = {href: target, title: 'コース', text: '授業の概要', ready: 'complete', inputs: []};

test('navigation distinguishes a target page, login, wrong origin and loading', () => {
  assert.equal(navigationOutcome(ready, target), 'ready');
  assert.equal(navigationOutcome({...ready, inputs: [{type: 'password'}]}, target, true), 'auth_required');
  assert.equal(navigationOutcome({...ready, href: 'https://example.invalid/'}, target, true), 'layout_changed');
  assert.equal(navigationOutcome({...ready, ready: 'loading'}, target, true), 'timeout');
  assert.equal(navigationOutcome({...ready, title: 'Access denied'}, target, true), 'layout_changed');
});

test('expired loop is not a successful return and does not retry credentials', async () => {
  let time = 0;
  let snapshots = 0;
  const result = await settleAuth({}, {}, 3000, target, {
    now: () => time,
    sleep: async ms => {time += ms;},
    snapshot: async () => {snapshots++; return {...ready, inputs: [{type: 'password'}]};},
  });
  assert.equal(result.status, 'auth_required');
  assert.ok(snapshots > 0);
  assert.equal(result.events.length, 1);
});

test('snapshot failures yield timeout; ready page completes', async () => {
  let time = 0;
  const runtime = {now: () => time, sleep: async ms => {time += ms;}, snapshot: async () => {throw new Error('unavailable');}};
  assert.equal((await settleAuth({}, {}, 2500, target, runtime)).status, 'timeout');
  time = 0;
  runtime.snapshot = async () => ready;
  assert.equal((await settleAuth({}, {}, 2500, target, runtime)).status, 'ready');
});
