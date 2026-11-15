import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { AddressInfo } from 'node:net';
import { buildBrief, type BriefTodo } from '../src/personal-brief.js';
import { PersonalBriefSchedule } from '../src/personal-brief-schedule.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { PushService } from '../src/push.js';
import { Scheduler } from '../src/scheduler.js';

const now = new Date(2026, 10, 1, 9, 0);
const iso = (offsetHours: number) => new Date(now.getTime() + offsetHours * 3600000).toISOString();
const todo = (id: string, fields: Partial<BriefTodo> = {}): BriefTodo => ({ id, text: id, parentId: null, owner: 'me', state: 'open', lane: null, importance: null, urgentUntil: null, dueAt: null, done: false, proposed: false, createdAt: iso(-100), updatedAt: iso(-100), doneAt: null, ...fields });

test('morning counts all four important urgent roots, lists three, and ends with the remainder', () => {
  const todos = [todo('pin', { importance: 'high', urgentUntil: iso(1) }), todo('due', { dueAt: iso(2) }), todo('overdue', { importance: 'high', dueAt: iso(-2) }), todo('fourth', { importance: 'high', dueAt: iso(30) }),
    todo('wait', { lane: 'wait' }), todo('new', { proposed: true, createdAt: iso(-1), importance: 'high', dueAt: iso(-2) }),
    todo('old-new', { proposed: true }), todo('low', { importance: 'low', dueAt: iso(-10) }), todo('child', { parentId: 'fourth', importance: 'high', dueAt: iso(-5) })];
  const brief = buildBrief('morning', todos, now)!;
  assert.equal(brief.title, '早报 · 4 件现在做'); assert.match(brief.lines[0], /^先做 overdue.*已逾期/); assert.match(brief.lines[1], /^due.*今天截止/); assert.match(brief.lines[2], /^fourth.*明天截止/);
  assert.equal(brief.lines[3], '等别人 1 件超 3 天 · 新发现 1 条'); assert.equal(brief.lines[4], '…另 1 件'); assert.equal(brief.lines.length, 5); assert.doesNotMatch(brief.text, /pin|old-new|low|child|wait/);
  assert.deepEqual(todos[0], todo('pin', { importance: 'high', urgentUntil: iso(1) }), 'does not mutate inputs');
  assert.deepEqual(buildBrief('morning', todos.slice().reverse(), now), brief, 'input order does not change ranking');
  assert.match(buildBrief('morning', [todos[0]], now)!.text, /已钉紧急/);
});

test('morning counts matters waiting on the person from their stage, stays inside 100 code points, and alone is worth sending', () => {
  const stages = [todo('a', { stage: 'answer' }), todo('b', { stage: 'review' }), todo('c', { stage: 'blocked' }), todo('d', { stage: 'working' }), todo('e', { stage: 'idle' }), todo('f', { stage: null }), todo('g', { stage: 'answer', done: true }), todo('h', { stage: 'answer', proposed: true })];
  assert.deepEqual(buildBrief('morning', stages, now), { title: '早报', lines: ['3 件等你'], text: '3 件等你' });
  assert.equal(buildBrief('morning', stages.slice(3), now), null);
  assert.equal(buildBrief('evening', stages, now), null, 'the evening brief does not ask');
  const long = (id: string) => todo(id, { text: `${id}${'名'.repeat(80)}`, importance: 'high', dueAt: iso(-2), stage: 'answer' });
  const crowded = buildBrief('morning', [long('a'), long('b'), long('c'), long('d'), todo('wait', { lane: 'wait' }), todo('new', { proposed: true, createdAt: iso(-1) })], now)!;
  assert.ok([...`${crowded.title}\n${crowded.text}`].length <= 100, crowded.text);
  assert.ok(crowded.lines.includes('4 件等你'));
});

test('three important urgent roots need no remainder; zero needs no now heading or empty push', () => {
  const todos = [todo('explicit', { importance: 'high', dueAt: iso(1) }), todo('unweighed', { dueAt: iso(2) }), todo('agent-important', { owner: 'agent', importance: 'high', urgentUntil: iso(3) })];
  const brief = buildBrief('morning', todos, now)!;
  assert.equal(brief.title, '早报 · 3 件现在做'); assert.equal(brief.lines.length, 3); assert.doesNotMatch(brief.text, /…另/);
  const irrelevant = [todo('normal', { importance: 'normal', dueAt: iso(1) }), todo('agent-unweighed', { owner: 'agent', dueAt: iso(1) }), todo('later', { importance: 'high', dueAt: iso(49) }), todo('done', { importance: 'high', dueAt: iso(1), done: true })];
  assert.equal(buildBrief('morning', irrelevant, now), null);
  assert.deepEqual(buildBrief('morning', [...irrelevant, todo('wait', { lane: 'wait' })], now), { title: '早报', lines: ['等别人 1 件超 3 天'], text: '等别人 1 件超 3 天' });
});

test('evening counts completed roots today, untouched important matters, and the first urgent matter', () => {
  const evening = new Date(2026, 10, 1, 19, 0);
  const todos = [todo('first', { importance: 'high', dueAt: iso(5) }), todo('second', { importance: 'high' }), todo('third', { importance: 'high' }),
    todo('done', { done: true, state: 'done', doneAt: iso(6) }), todo('old-done', { done: true, doneAt: iso(-24) }), todo('child-done', { parentId: 'done', done: true, doneAt: iso(6) }),
    todo('touched', { importance: 'high', updatedAt: iso(3) })];
  const brief = buildBrief('evening', todos, evening)!;
  assert.equal(brief.lines[0], '今天完成1件'); assert.match(brief.lines[1], /first、second/); assert.doesNotMatch(brief.text, /third|touched|old-done|child-done/);
  assert.match(brief.lines[2], /明天先做：first/); assert.match(brief.text, /已逾期/);
});

test('Unicode cap keeps all sections and shortens names instead of losing the final information', () => {
  const todos = [0, 1, 2, 3].map(i => todo(String(i), { text: '𠮷非常长的事项名称'.repeat(40), importance: 'high', dueAt: iso(i) }));
  todos.push(todo('wait', { text: '等另一位同事回复'.repeat(20), lane: 'wait' }), todo('proposal', { proposed: true, createdAt: iso(0) }));
  for (const kind of ['morning', 'evening'] as const) {
    const brief = buildBrief(kind, todos, now)!;
    assert.ok([...`${brief.title}\n${brief.text}`].length <= 100); assert.ok(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(brief.text));
    assert.equal(brief.text, brief.lines.join('\n'));
    assert.match(brief.text, kind === 'morning' ? /新发现 1 条\n…另 1 件$/ : /明天先做/);
  }
});

test('empty, irrelevant and old completed plans return null; completed-only evening is meaningful', () => {
  for (const kind of ['morning', 'evening'] as const) {
    assert.equal(buildBrief(kind, [], now), null);
    assert.equal(buildBrief(kind, [todo('normal'), todo('done', { done: true, doneAt: iso(-48) })], now), null);
  }
  assert.equal(buildBrief('evening', [todo('done', { done: true, doneAt: iso(-1) })], now)?.text, '今天完成1件');
  assert.deepEqual(buildBrief('morning', [todo('wait', { lane: 'wait' })], now), { title: '早报', lines: ['等别人 1 件超 3 天'], text: '等别人 1 件超 3 天' });
  assert.equal(buildBrief('morning', [todo('first', { importance: 'high', dueAt: iso(1) })], now)?.title, '早报 · 1 件现在做');
});

test('child activity counts as progress; explicit lanes override waiting and three days is strict', () => {
  const parent = todo('parent', { importance: 'high', lane: 'wait' });
  const child = todo('child', { parentId: parent.id, updatedAt: iso(-1) });
  assert.equal(buildBrief('morning', [parent, child], now), null);
  assert.equal(buildBrief('evening', [parent, child], now), null);
  assert.equal(buildBrief('morning', [todo('not-waiting', { state: 'waiting', lane: 'doing' }), todo('exactly-three', { lane: 'wait', updatedAt: iso(-72) })], now), null);
  const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
  assert.deepEqual(buildBrief('morning', [todo('fresh', { proposed: true, createdAt: midnight.toISOString() })], now), { title: '早报', lines: ['新发现 1 条'], text: '新发现 1 条' });
});

test('48-hour reason, multiple stale waits, and associated conversation activity stay visible', () => {
  const brief = buildBrief('morning', [todo('soon', { importance: 'high', dueAt: iso(47) }), todo('wait-a', { lane: 'wait' }), todo('wait-b', { state: 'waiting' })], now)!;
  assert.match(brief.text, /48小时内截止/); assert.match(brief.text, /等别人 2 件超 3 天/);
  assert.equal(buildBrief('evening', [{ ...todo('talked', { importance: 'high' }), threads: [{ updatedAt: iso(-1) }] }], now), null);
});

function scheduleFixture() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-brief-')); const store = new Store(dir);
  let quiet = { start: '23:00', end: '08:00' };
  const schedule = new PersonalBriefSchedule(store, () => quiet);
  const sent: unknown[] = [];
  const push = { brief: (...args: unknown[]) => { sent.push(args); return {} as any; } };
  return { dir, store, schedule, sent, push, quiet: (v: typeof quiet) => { quiet = v; }, close() { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}
test('schedule persists settings and daily marks across restart, overlapping ticks, clock changes and edited times', () => {
  const h = scheduleFixture(); try {
    h.store.createTodo('first', '先办这件', null, { importance: 'high', dueAt: iso(1) });
    assert.deepEqual(h.schedule.settings(), { enabled: false, morning: '09:00', evening: '19:00' });
    h.schedule.tick(now, h.push); assert.equal(h.sent.length, 0);
    h.schedule.save({ enabled: true });
    for (let n = 0; n < 3; n++) h.schedule.tick(now, h.push);
    assert.equal(h.sent.length, 1);
    const reborn = new PersonalBriefSchedule(h.store, () => ({ start: '23:00', end: '08:00' }));
    reborn.tick(now, h.push); assert.equal(h.sent.length, 1);
    reborn.save({ morning: '10:00' }); reborn.tick(new Date(2026, 10, 1, 10, 0), h.push); assert.equal(h.sent.length, 1);
    reborn.tick(new Date(2026, 10, 1, 19, 0), h.push); assert.equal(h.sent.length, 2);
    reborn.tick(new Date(2026, 10, 2, 10, 0), h.push); assert.equal(h.sent.length, 3);
    reborn.tick(new Date(2026, 10, 1, 10, 0), h.push); assert.equal(h.sent.length, 3);
    assert.throws(() => reborn.save({ morning: '24:00' }), /HH:mm/); assert.throws(() => reborn.save({ enabled: 'true' }), /enabled/);
  } finally { h.close(); }
});

test('quiet windows, downtime, muted push and empty summaries do not dispatch', () => {
  const h = scheduleFixture(); try {
    h.schedule.save({ enabled: true }); h.schedule.tick(now, h.push); assert.equal(h.sent.length, 0);
    h.store.createTodo('first', '先办这件', null, { importance: 'high', dueAt: iso(1) });
    h.schedule.tick(now, h.push); assert.equal(h.sent.length, 0, 'empty occurrence already consumed');
    h.schedule.tick(new Date(2026, 10, 2, 11, 0), h.push); assert.equal(h.sent.length, 0, 'no catch-up');
    h.schedule.save({ morning: '23:30' }); h.schedule.tick(new Date(2026, 10, 2, 23, 30), h.push); assert.equal(h.sent.length, 0);
    h.quiet({ start: '12:00', end: '14:00' }); h.schedule.save({ morning: '12:30' }); h.schedule.tick(new Date(2026, 10, 2, 12, 30), h.push); assert.equal(h.sent.length, 0);
    h.quiet({ start: '00:00', end: '00:00' });
    h.schedule.tick(new Date(2026, 10, 2, 12, 30), { brief: () => null }); assert.equal(h.sent.length, 0);
    h.schedule.tick(new Date(2026, 10, 2, 12, 30), h.push); assert.equal(h.sent.length, 1, 'push switch may be enabled within the same minute');
  } finally { h.close(); }
});

test('HTTP preview/settings and real Scheduler tick reach the existing bot push records without any agent', async () => {
  const h = scheduleFixture();
  const log = join(h.dir, 'lark-calls'), config = join(h.dir, 'lark-config'); writeFileSync(log, ''); writeFileSync(config, '{}');
  const manager = new ThreadManager(h.store, new Map(), [h.dir], new AttachmentStore(h.dir));
  const push = new PushService(h.store, manager, { executable: resolve('test/fixtures/fake-lark-cli.mjs'), env: { ...process.env, FAKE_LARK_LOG: log, FAKE_LARK_CONFIG: config } });
  const scheduler = new Scheduler(h.store, manager, new Map(), push);
  const server = createHttpServer({ manager, push, scheduler, token: 'brief-test', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const request = (path: string, method = 'GET', body?: unknown) => fetch(base + '/api/personal/brief' + path, { method, headers: { Authorization: 'Bearer brief-test', 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  try {
    assert.equal((await fetch(base + '/api/personal/brief/settings')).status, 401);
    assert.equal((await fetch(base + '/api/personal/brief/last?kind=morning')).status, 401);
    assert.equal(await (await request('/last?kind=morning')).json(), null);
    assert.equal((await request('/last?kind=bad')).status, 400); assert.equal((await request('/last')).status, 400);
    assert.equal(await (await request('?kind=morning')).json(), null); assert.equal((await request('?kind=bad')).status, 400);
    const current = new Date(); const clock = `${String(current.getHours()).padStart(2, '0')}:${String(current.getMinutes()).padStart(2, '0')}`;
    assert.equal((await request('/settings', 'PUT', { enabled: true, morning: clock })).status, 200);
    assert.equal((await (await request('/settings')).json()).morning, clock); assert.equal((await request('/settings', 'PUT', { evening: 'noon' })).status, 400);
    h.store.createTodo('brief-first', '确认明天评审材料', null, { importance: 'high', dueAt: new Date(Date.now() + 3600000).toISOString() });
    const preview = await (await request('?kind=morning')).json(); assert.match(preview.text, /确认明天评审材料/);
    assert.equal(await (await request('/last?kind=morning')).json(), null, 'preview does not count as a delivery');
    h.store.saveHubSetting('personalReview', { ...manager.personal.review.status(), quietHours: { start: '00:00', end: '00:00' } });
    push.putSettings({ enabled: true, baseUrl: 'https://example.invalid' });
    await scheduler.tick(); await scheduler.tick();
    for (let n = 0; n < 200 && !(await push.status()).records.some(record => record.kind === 'morning' && record.result === 'sent'); n++) await new Promise(resolve => setTimeout(resolve, 10));
    const records = (await push.status()).records.filter(record => record.kind === 'morning'); assert.equal(records.length, 1); assert.equal(records[0].result, 'sent'); assert.equal(records[0].title, '早报 · 1 件现在做');
    const last = await (await request('/last?kind=morning')).json();
    assert.equal(last.text, `${preview.title}\n${preview.text}`); assert.ok(Date.parse(last.sentAt) >= Date.parse(records[0].at));
    h.store.updateTodo('brief-first', { text: '后来改了标题' });
    assert.notEqual((await (await request('?kind=morning')).json()).text, preview.text);
    assert.deepEqual(await (await request('/last?kind=morning')).json(), last, 'keeps the sent snapshot after todo edits');
    assert.equal(await (await request('/last?kind=evening')).json(), null);
    const calls = readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    const message = calls.find(call => call.args[0] === 'im'); assert.match(message.input, /早报/); assert.match(message.input, /确认明天评审材料/); assert.match(message.input, /\/#\/desk/); assert.doesNotMatch(message.input, /来自 agent/);
    assert.equal(h.store.listThreads().length, 0, 'no Router or worker needed');
  } finally { scheduler.dispose(); push.dispose(); await manager.shutdown(); await new Promise<void>(resolve => server.close(() => resolve())); h.close(); }
});

test('last briefs persist only successful delivery snapshots, independently for morning and evening', async () => {
  const h = scheduleFixture();
  const config = join(h.dir, 'config'), log = join(h.dir, 'calls'); writeFileSync(config, '{"fail":true}'); writeFileSync(log, '');
  const manager = new ThreadManager(h.store, new Map(), [h.dir], new AttachmentStore(h.dir));
  let deliveredAt = now.getTime();
  const push = new PushService(h.store, manager, { executable: resolve('test/fixtures/fake-lark-cli.mjs'), retryMs: 1, now: () => deliveredAt,
    env: { ...process.env, FAKE_LARK_LOG: log, FAKE_LARK_CONFIG: config } });
  const wait = async (fn: () => boolean) => { for (let n = 0; n < 500; n++) { if (fn()) return; await new Promise(resolve => setTimeout(resolve, 20)); } throw new Error('push did not settle'); };
  const brief = { title: '早报 · 1 件现在做', lines: ['先做 核对材料（今天截止）'], text: '先做 核对材料（今天截止）' };
  try {
    assert.equal(push.brief('morning', brief, 'muted'), null); assert.equal(h.schedule.last('morning'), null);
    push.putSettings({ enabled: true });
    const failedFirst = push.brief('morning', brief, 'failed-first')!;
    assert.equal(h.schedule.last('morning'), null, 'queued is not sent');
    await wait(() => failedFirst.result === 'failed'); assert.equal(h.schedule.last('morning'), null);

    writeFileSync(config, '{}');
    const expectedText = `${brief.title}\n${brief.text}`;
    const first = push.brief('morning', brief, 'first')!;
    brief.title = 'later title'; brief.text = 'later body';
    deliveredAt += 1000;
    await wait(() => first.result === 'sent');
    const morning = { text: expectedText, sentAt: new Date(deliveredAt).toISOString() };
    assert.deepEqual(h.schedule.last('morning'), morning, 'snapshot is copied when queued, time is recorded on success');
    assert.equal(h.schedule.last('evening'), null);

    deliveredAt += 1000;
    const evening = push.brief('evening', { title: '晚报', lines: ['今天完成1件'], text: '今天完成1件' }, 'evening')!;
    await wait(() => evening.result === 'sent');
    assert.equal(h.schedule.last('evening')?.text, '晚报\n今天完成1件');
    assert.deepEqual(h.schedule.last('morning'), morning);

    writeFileSync(config, '{"fail":true}');
    const failed = push.brief('morning', brief, 'failed-after-success')!;
    await wait(() => failed.result === 'failed'); assert.deepEqual(h.schedule.last('morning'), morning);
    for (let n = 0; n < 30; n++) push.test();
    assert.equal(push.brief('morning', brief, 'rate-limited')!.result, 'dropped');
    assert.deepEqual(h.schedule.last('morning'), morning);

    const reopened = new Store(h.dir);
    try {
      const restored = new PersonalBriefSchedule(reopened, () => ({ start: '23:00', end: '08:00' }));
      assert.deepEqual(restored.last('morning'), morning); assert.equal(restored.last('evening')?.text, '晚报\n今天完成1件');
    } finally { reopened.close(); }
  } finally { push.dispose(); await manager.shutdown(); h.close(); }
});
