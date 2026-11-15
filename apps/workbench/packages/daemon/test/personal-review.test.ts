import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { PersonalReview, watchFacts, type ReviewState } from '../src/personal-review.js';
import type { ProactiveRun } from '../src/proactive.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';
import { Scheduler } from '../src/scheduler.js';
import { HttpError } from '../src/errors.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter, AgentSession, TurnInput } from '../src/adapters/types.js';
import { personalServer } from '../src/personal.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { BorrowingService } from '../src/borrowing.js';
import { listenPrivate } from '../src/http.js';
import { PushService } from '../src/push.js';

const exec = promisify(execFile);
const MINUTE = 60_000, DAY = 24 * 60 * MINUTE;
const at = (hour: number, minute = 0, day = 12) => new Date(2030, 2, day, hour, minute).getTime();
const iso = (time: number) => new Date(time).toISOString();
const wait = async (fn: () => boolean | Promise<boolean>) => {
  for (let i = 0; i < 500; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 20)); }
  throw new Error('timed out');
};
function stateFixture() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-review-state-'));
  const store = new Store(dir), review = new PersonalReview(store);
  const save = (patch: Partial<ReviewState>) => store.saveHubSetting('personalReview', { ...review.status(), ...patch });
  return { dir, store, review, save, close() { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

async function setup(options: { fake?: 'notify' | 'quiet'; startError?: boolean } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-review-'));
  const cwd = join(dir, 'personal'), outside = join(dir, 'outside'); mkdirSync(cwd); mkdirSync(outside);
  const store = new Store(dir);
  let release: (() => void) | undefined, gate: Promise<void> | undefined;
  let onPrompt: ((input: TurnInput) => void) | undefined;
  const session = (): AgentSession => ({ agentSessionId: 'review-fixture', async prompt(input) {
    onPrompt?.(input); if (gate) await gate; return { stopReason: 'end_turn' };
  }, async cancel() { release?.(); }, async close() {} });
  const stub = new PiRpcAdapter([process.execPath, resolve('test/fixtures/fake-pi.mjs')], options.fake ? { FAKE_PERSONAL_REVIEW: options.fake } : {});
  if (!options.fake) stub.start = async () => { if (options.startError) throw new Error('fixture background startup failure'); return session(); };
  const adapters = new Map<string, AgentAdapter>([['pi', stub]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'dirs')), new AttachmentStore(dir));
  const service = manager.personal, review = service.review; service.socket = join(dir, 'personal.sock');
  service.putSettings({ directory: cwd, agent: 'pi', model: 'fake/think' });
  const socket = personalServer(service); await listenPrivate(socket, service.socket);
  const borrowing = new BorrowingService(store, manager, adapters, dir, join(dir, 'tools.sock'));
  const tools = borrowingServer(borrowing); await listenPrivate(tools, borrowing.socket);
  const log = join(dir, 'lark-log'), config = join(dir, 'lark-config'); writeFileSync(log, ''); writeFileSync(config, '{}');
  const push = new PushService(store, manager, { executable: resolve('test/fixtures/fake-lark-cli.mjs'), retryMs: 1,
    env: { ...process.env, FAKE_LARK_LOG: log, FAKE_LARK_CONFIG: config } });
  const scheduler = new Scheduler(store, manager, adapters); scheduler.start();
  const thread = await manager.createThread({ agent: 'pi', cwd });
  const save = (patch: Partial<ReviewState>) => store.saveHubSetting('personalReview', { ...review.status(), ...patch });
  const due = () => { review.command(thread.id, 'review.on'); save({ nextReviewAt: iso(Date.now() - 1000), quietHours: { start: '00:00', end: '00:00' } }); };
  const cli = async (args: string[], owner = thread.id) => JSON.parse((await exec(process.execPath, [resolve('bin/calli'), ...args], {
    env: { ...process.env, ...service.environment(owner), ...borrowing.environment(owner) },
  })).stdout);
  return { dir, cwd, outside, store, manager, service, review, scheduler, adapters, thread, push, save, due, cli,
    hold() { gate = new Promise<void>(r => { release = r; }); },
    release() { release?.(); gate = undefined; },
    onPrompt(fn: (input: TurnInput) => void) { onPrompt = fn; },
    async close() {
      release?.(); push.dispose(); scheduler.dispose(); await borrowing.dispose(); await manager.shutdown();
      await new Promise<void>(r => tools.close(() => r())); await new Promise<void>(r => socket.close(() => r()));
      store.close(); rmSync(dir, { recursive: true, force: true });
    },
  };
}

test('review defaults off, rounds on up after thirty minutes, and persists settings across restart', t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10, 10) });
  const h = stateFixture(); try {
    assert.equal(h.review.status().reviewEnabled, false);
    assert.deepEqual(h.review.status().quietHours, { start: '23:00', end: '08:00' });
    assert.equal(h.review.command('owner', 'review.on').nextReviewAt, iso(at(11)));
    assert.equal(h.review.status().enabledByThreadId, 'owner');
    t.mock.timers.setTime(at(10, 50));
    assert.equal(h.review.command('owner', 'review.on').nextReviewAt, iso(at(11)), 'enabling twice preserves an already chosen time');
    h.review.command('owner', 'review.off'); assert.equal(h.review.status().nextReviewAt, null);
    t.mock.timers.setTime(at(10, 40));
    assert.equal(h.review.command('owner', 'review.on').nextReviewAt, iso(at(12)));
    h.save({ runsToday: 2, unansweredReviews: 2, lastThreadId: 'last' });
    const reopened = new Store(h.dir);
    try { assert.deepEqual(new PersonalReview(reopened).status(), h.review.status()); } finally { reopened.close(); }
    t.mock.timers.setTime(at(10, 40, 13));
    assert.equal(h.review.status().runsToday, 0); assert.equal(h.review.status().unansweredReviews, 2);
  } finally { h.close(); }
});

test('review next validates ISO bounds and adjusts quiet hours, daily limit and unanswered backoff', t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = stateFixture(); try {
    for (const value of ['tomorrow', '2030-03-12', iso(at(10, 29)), iso(at(10) + 3 * DAY + 1)]) {
      assert.throws(() => h.review.command('owner', 'review.next', value), { status: 400 });
    }
    assert.equal(h.review.command('owner', 'review.next', iso(at(10, 30))).nextReviewAt, iso(at(10, 30)));
    assert.equal(h.review.command('owner', 'review.next', iso(at(10) + 3 * DAY)).nextReviewAt, iso(at(10) + 3 * DAY));
    let result = h.review.command('owner', 'review.next', iso(at(23))) as ReviewState & { reasons: string[] };
    assert.equal(result.nextReviewAt, iso(at(8, 0, 13))); assert.deepEqual(result.reasons, ['quiet_hours']);
    h.save({ runsToday: 4 });
    result = h.review.command('owner', 'review.next', iso(at(11))) as typeof result;
    assert.equal(result.nextReviewAt, iso(at(8, 0, 13))); assert.deepEqual(result.reasons, ['daily_limit']);
    h.save({ runsToday: 0, unansweredReviews: 3 });
    result = h.review.command('owner', 'review.next', iso(at(11))) as typeof result;
    assert.equal(result.nextReviewAt, iso(at(10, 0, 13))); assert.deepEqual(result.reasons, ['idle_backoff']);
    h.save({ unansweredReviews: 0, quietHours: { start: '12:00', end: '14:00' } });
    assert.equal(h.review.command('owner', 'review.next', iso(at(12))).nextReviewAt, iso(at(14)));
  } finally { h.close(); }
});

test('overdue reviews after downtime obey quiet hours and daily limits before firing', t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(23, 10) });
  const h = stateFixture(); try {
    h.save({ reviewEnabled: true, nextReviewAt: iso(at(22)) });
    assert.equal(h.review.due(), null); assert.equal(h.review.status().nextReviewAt, iso(at(8, 0, 13)));
    t.mock.timers.setTime(at(10, 0, 13)); h.save({ runsToday: 4, nextReviewAt: iso(at(9, 0, 13)) });
    assert.equal(h.review.due(), null); assert.equal(h.review.status().nextReviewAt, iso(at(8, 0, 14)));
    t.mock.timers.setTime(at(10, 0, 14)); assert.equal(h.review.due(), iso(at(8, 0, 14)));
  } finally { h.close(); }
});

test('due review launches one dated private thread with summary only, clears next and defaults to nine', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = await setup(); try {
    h.hold();
    const todo = h.service.create({ text: 'Review this commitment', owner: 'agent' });
    h.service.writeNote(todo.id, { text: '## 欠谁什么\nFull detail not to load\n## 下一步\nUseful preview' });
    h.store.linkTodoThread(todo.id, h.thread.id);
    h.due(); await Promise.all([h.scheduler.reviews(), h.scheduler.reviews(), h.scheduler.tick()]);
    const state = h.review.status(), thread = h.store.getThread(state.lastThreadId!)!;
    assert.equal(state.nextReviewAt, null); assert.equal(state.runsToday, 1); assert.ok(state.active);
    assert.equal(thread.title, '盘点 · 2030-03-12'); assert.equal(thread.cwd, h.cwd);
    assert.equal(thread.agent, 'pi'); assert.equal(thread.model, 'fake/think');
    assert.equal(thread.permissionMode, h.adapters.get('pi')!.capabilities.defaultPermissionMode ?? 'auto_edit');
    const started = h.store.listEventsOfType(thread.id, 'turn.started')[0]; assert.equal(started.data.review, true);
    const prompt = String(started.data.text);
    for (const phrase of ['只读、只问', '一条 calli notify', 'calli quiet', 'calli review next', 'calli todo delegate', 'Useful preview', 'followUpPaused', '"running": false']) assert.ok(prompt.includes(phrase), phrase);
    assert.ok(!prompt.includes('Full detail not to load'));
    assert.equal(h.store.listThreads().length, 2);
    h.release(); await wait(() => !h.review.status().active);
    assert.equal(h.review.status().nextReviewAt, iso(at(9, 0, 13)));
    assert.equal(h.review.status().unansweredReviews, 1);
    assert.equal((await h.push.status()).records.length, 0);
  } finally { await h.close(); }
});

test('automatic review allows one notice and no delegate until a human reply; next survives completion', async () => {
  const h = await setup(); try {
    const todo = h.service.create({ text: 'Prepare a draft' });
    h.hold(); h.due(); await h.scheduler.reviews();
    const id = h.review.status().lastThreadId!;
    await wait(() => h.store.getThread(id)?.status === 'running');
    h.manager.notice(id, 'One combined question');
    assert.throws(() => h.manager.notice(id, 'Second question'), { status: 409 });
    await assert.rejects(h.service.command(id, { op: 'todo.delegate', id: todo.id }), { status: 409 });
    const next = h.review.command(id, 'review.next', iso(Date.now() + 60 * MINUTE)).nextReviewAt;
    h.release(); await wait(() => !h.review.status().active && h.store.getThread(id)?.status !== 'running');
    assert.equal(h.review.status().nextReviewAt, next); assert.equal(h.review.status().unansweredReviews, 0);
    await assert.rejects(h.service.command(id, { op: 'todo.delegate', id: todo.id }), { status: 409 });
    h.save({ unansweredReviews: 2 }); h.hold(); await h.manager.startTurn(id, { text: 'Start the draft' });
    assert.equal(h.review.status().unansweredReviews, 0);
    const delegated = await h.service.command(id, { op: 'todo.delegate', id: todo.id });
    assert.equal(h.store.todoThreadIds(todo.id)[0], (delegated as { id: string }).id);
    h.manager.notice(id, 'Reply question one'); h.manager.notice(id, 'Reply question two');
    h.release();
  } finally { await h.close(); }
});

test('three quiet reviews archive and lengthen even an already-set next time; human replies reset the streak', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = await setup(); try {
    h.due();
    h.onPrompt(() => {
      const id = h.review.status().active!.threadId;
      h.review.command(id, 'review.next', iso(Date.now() + 60 * MINUTE)); h.manager.quiet(id);
    });
    for (let count = 1; count <= 3; count++) {
      await h.scheduler.reviews();
      const id = h.review.status().lastThreadId!;
      await wait(() => Boolean(h.store.getThread(id)?.archivedAt));
      assert.equal(h.review.status().unansweredReviews, count); assert.equal(h.store.getThread(id)?.unread, false);
      assert.equal(h.store.listEventsOfType(id, 'agent.notice').length, 0);
      if (count < 3) t.mock.timers.setTime(Date.parse(h.review.status().nextReviewAt!));
    }
    assert.equal(h.review.status().nextReviewAt, iso(Date.now() + DAY));
    assert.equal((await h.push.status()).records.length, 0);
    const reopened = new Store(h.dir);
    try { assert.equal(new PersonalReview(reopened).status().unansweredReviews, 3); } finally { reopened.close(); }
    h.onPrompt(() => {});
    await h.manager.updateThread(h.review.status().lastThreadId!, { archived: false });
    await h.manager.startTurn(h.review.status().lastThreadId!, { text: 'Here is my answer' });
    assert.equal(h.review.status().unansweredReviews, 0);
  } finally { await h.close(); }
});

test('review synchronous launch failures back off, delete empty threads, notify once and recover', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = await setup(); try {
    h.due(); const expected = h.review.status().nextReviewAt;
    const start = t.mock.method(h.manager, 'startTurn', async () => { throw new HttpError(503, 'private fixture error must not leak'); });
    const create = t.mock.method(h.manager, 'createThread');
    for (let i = 0; i <= 20; i++) { t.mock.timers.setTime(at(10) + i * 30_000); await h.scheduler.reviews(); }
    assert.equal(create.mock.callCount(), 3); assert.equal(start.mock.callCount(), 3);
    assert.equal(h.store.listThreads().length, 1); assert.equal(h.store.listThreads({ archived: true }).length, 0);
    assert.equal(h.review.status().nextReviewAt, expected); assert.equal(h.review.status().runsToday, 0);
    const notices = h.store.listEventsOfType(h.thread.id, 'agent.notice');
    assert.equal(notices.length, 1); assert.equal(notices[0].data.confirmation, undefined);
    assert.equal(notices[0].data.text, '盘点没能启动：无法启动 agent，稍后自动重试');
    for (const minutes of [36, 96, 156]) {
      t.mock.timers.setTime(at(10) + (minutes - 1) * MINUTE); await h.scheduler.reviews();
      const attempts = start.mock.callCount();
      t.mock.timers.setTime(at(10) + minutes * MINUTE); await h.scheduler.reviews();
      assert.equal(start.mock.callCount(), attempts + 1);
    }
    assert.equal(start.mock.callCount(), 6); assert.equal(h.store.listEventsOfType(h.thread.id, 'agent.notice').length, 1);
    start.mock.restore(); t.mock.timers.setTime(at(10) + 216 * MINUTE);
    await h.scheduler.reviews(); await wait(() => !h.review.status().active);
    assert.equal(h.review.status().runsToday, 1); assert.equal(h.store.listThreads().length, 2);
    assert.equal(h.review.status().nextReviewAt, iso(at(9, 0, 13)));
  } finally { await h.close(); }
});

test('background review startup error ends once and schedules default nine without retries', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = await setup({ startError: true }); try {
    h.push.putSettings({ enabled: true, scheduled: true }); h.due(); await h.scheduler.reviews();
    const id = h.review.status().lastThreadId!;
    await wait(() => !h.review.status().active && h.store.getThread(id)?.status !== 'running');
    assert.equal(h.store.listEventsOfType(id, 'turn.error').length, 1);
    assert.equal(h.review.status().nextReviewAt, iso(at(9, 0, 13)));
    for (let i = 1; i <= 20; i++) { t.mock.timers.setTime(at(10) + i * 30_000); await h.scheduler.tick(); }
    assert.equal(h.store.listThreads().length, 2); assert.equal(h.review.status().runsToday, 1);
    await wait(async () => (await h.push.status()).records[0]?.result === 'sent');
    assert.equal((await h.push.status()).records.length, 1); assert.equal((await h.push.status()).records[0].kind, 'failure');
  } finally { await h.close(); }
});

test('review recovery settles an interrupted active turn and preserves the chosen next time', t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = stateFixture(); try {
    h.save({ reviewEnabled: true, active: { threadId: 'review', turnId: 'interrupted', notified: false } });
    h.review.recover(); assert.equal(h.review.status().active, null);
    assert.equal(h.review.status().nextReviewAt, iso(at(9, 0, 13))); assert.equal(h.review.status().unansweredReviews, 1);
    h.save({ active: { threadId: 'review', turnId: 'interrupted-again', notified: true }, nextReviewAt: iso(at(12)) });
    h.review.recover(); assert.equal(h.review.status().nextReviewAt, iso(at(12))); assert.equal(h.review.status().unansweredReviews, 0);
    h.review.recover(); assert.equal(h.review.status().unansweredReviews, 0);
  } finally { h.close(); }
});

test('real CLI review commands require personal credentials and delegate only accepted unfinished items', async () => {
  const h = await setup(); try {
    assert.equal((await h.cli(['review', 'status'])).reviewEnabled, false);
    assert.equal((await h.cli(['review', 'on'])).reviewEnabled, true);
    const next = await h.cli(['review', 'next', iso(Date.now() + 60 * MINUTE)]); assert.ok(next.nextReviewAt); assert.ok(Array.isArray(next.reasons));
    await assert.rejects(h.cli(['review', 'next', 'tomorrow']), /ISO 8601/);
    assert.equal((await h.cli(['review', 'off'])).nextReviewAt, null);
    const outside = await h.manager.createThread({ agent: 'pi', cwd: h.outside });
    await assert.rejects(h.service.command(outside.id, { op: 'review.on' }), { status: 403 });
    await assert.rejects(h.service.command(outside.id, { op: 'todo.delegate', id: 'any' }), { status: 403 });
    await assert.rejects(h.cli(['review', 'status'], outside.id));
    const proposed = h.service.create({ text: 'Suggestion', owner: 'me' }, h.thread.id);
    await assert.rejects(h.cli(['todo', 'delegate', proposed.id]), /accept an open plan/);
    const done = h.service.create({ text: 'Done' }); h.service.update(done.id, { done: true });
    await assert.rejects(h.cli(['todo', 'delegate', done.id]), /accept an open plan/);
    const todo = h.service.create({ text: 'Prepare an outline' });
    const delegated = await h.cli(['todo', 'delegate', todo.id]);
    assert.equal(delegated.cwd, h.cwd); assert.equal(delegated.model, 'fake/think');
    assert.equal(h.store.todoThreadIds(todo.id)[0], delegated.id);
  } finally { await h.close(); }
});

test('fake Pi uses only scoped CLI for review, notify, next and a human-authorized delegate', async () => {
  const h = await setup({ fake: 'notify' }); try {
    h.push.putSettings({ enabled: true, scheduled: true });
    const todo = h.service.create({ text: 'Prepare a one-page outline' });
    h.due(); await h.scheduler.reviews();
    const id = h.review.status().lastThreadId!;
    await wait(() => !h.review.status().active && h.store.getThread(id)?.status !== 'running');
    assert.equal(h.store.listEventsOfType(id, 'turn.error').length, 0);
    assert.equal(h.store.listEventsOfType(id, 'agent.notice').length, 1);
    assert.equal(h.review.status().unansweredReviews, 0);
    assert.ok(Date.parse(h.review.status().nextReviewAt!) > Date.now() + 30 * MINUTE);
    assert.equal(h.store.todoThreadIds(todo.id).length, 0);
    await h.manager.startTurn(id, { text: `开始 ${todo.id}` });
    await wait(() => h.store.todoThreadIds(todo.id).length === 1 && h.store.getThread(id)?.status !== 'running');
    const work = h.store.getThread(h.store.todoThreadIds(todo.id)[0])!;
    assert.notEqual(work.id, id); assert.equal(work.cwd, h.cwd);
    assert.match(String(h.store.listEventsOfType(work.id, 'turn.started')[0].data.text), /这是一件新交给你的事，不是交接/);
    await wait(async () => (await h.push.status()).records.some(record => record.kind === 'notice' && record.result === 'sent'));
    assert.equal((await h.push.status()).records.filter(record => record.threadId === id).length, 1);
  } finally { await h.close(); }
});

test('fake Pi quiet review is archived without unread or routine scheduled push', async () => {
  const h = await setup({ fake: 'quiet' }); try {
    h.push.putSettings({ enabled: true, scheduled: true }); h.due(); await h.scheduler.reviews();
    const id = h.review.status().lastThreadId!;
    await wait(() => Boolean(h.store.getThread(id)?.archivedAt));
    assert.equal(h.store.getThread(id)?.unread, false); assert.equal(h.review.status().unansweredReviews, 1);
    assert.ok(h.review.status().nextReviewAt); assert.equal((await h.push.status()).records.length, 0);
  } finally { await h.close(); }
});

test('a running human reply in a review thread blocks another review, and disabling during creation cancels launch', async t => {
  const h = await setup(); try {
    h.due(); await h.scheduler.reviews();
    const previous = h.review.status().lastThreadId!;
    await wait(() => !h.review.status().active && h.store.getThread(previous)?.status !== 'running');
    h.hold(); await h.manager.startTurn(previous, { text: 'Let us clarify this' });
    h.due(); await h.scheduler.reviews();
    assert.equal(h.store.listThreads().length, 2); assert.equal(h.review.status().runsToday, 1);
    h.release(); await wait(() => h.store.getThread(previous)?.status !== 'running');
    const original = h.manager.createThread.bind(h.manager);
    const create = t.mock.method(h.manager, 'createThread', async (...args: Parameters<typeof h.manager.createThread>) => {
      const created = await original(...args); h.review.command(h.thread.id, 'review.off'); return created;
    });
    await h.scheduler.reviews();
    assert.equal(create.mock.callCount(), 1); assert.equal(h.store.listThreads().length, 2);
    assert.equal(h.review.status().active, null); assert.equal(h.review.status().reviewEnabled, false);
    assert.equal(h.store.listEventsOfType(previous, 'agent.notice').length, 0);
  } finally { await h.close(); }
});

test('four review launches consume the daily allowance and a fifth waits until tomorrow', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: at(10) });
  const h = await setup(); try {
    h.onPrompt(() => h.manager.notice(h.review.status().active!.threadId, 'A question for this round'));
    for (let count = 1; count <= 4; count++) {
      t.mock.timers.setTime(at(9 + count)); h.due(); await h.scheduler.reviews();
      await wait(() => !h.review.status().active);
      assert.equal(h.review.status().runsToday, count);
    }
    h.save({ nextReviewAt: iso(Date.now() - 1000), quietHours: { start: '23:00', end: '08:00' } });
    await h.scheduler.reviews(); assert.equal(h.review.status().runsToday, 4);
    assert.equal(h.review.status().nextReviewAt, iso(at(8, 0, 13)));
    t.mock.timers.setTime(at(8, 0, 13)); await h.scheduler.reviews();
    await wait(() => !h.review.status().active); assert.equal(h.review.status().runsToday, 1);
  } finally { await h.close(); }
});

const WATCH_NOW = Date.parse('2030-03-12T12:00:00.000Z');
const hoursAgo = (hours: number) => new Date(WATCH_NOW - hours * 3_600_000).toISOString();
const calm = { threads: [], noteUpdatedAt: null, turnEnds: [], runs: [] };

test('watch: a thread running over two hours is listed; a shorter or idle one is not', () => {
  const thread = (status: string, hours: number) => ({ id: 't', status, since: hoursAgo(hours) });
  assert.deepEqual(watchFacts({ ...calm, threads: [thread('running', 2.5)] }, WATCH_NOW), [{ kind: 'long-running', threadId: 't', since: hoursAgo(2.5) }]);
  assert.deepEqual(watchFacts({ ...calm, threads: [thread('running', 2)] }, WATCH_NOW), [], 'exactly two hours is not over');
  assert.deepEqual(watchFacts({ ...calm, threads: [thread('idle', 9), thread('error', 9)] }, WATCH_NOW), []);
  assert.deepEqual(watchFacts({ ...calm, threads: [{ id: 't', status: 'running', since: null }] }, WATCH_NOW), []);
});

test('watch: three rounds ending after the note last changed are listed, in any order', () => {
  const turnEnds = [hoursAgo(1), hoursAgo(5), hoursAgo(3), hoursAgo(2)];
  assert.deepEqual(watchFacts({ ...calm, turnEnds, noteUpdatedAt: hoursAgo(4) }, WATCH_NOW), [{ kind: 'note-unchanged', rounds: 3, noteUpdatedAt: hoursAgo(4), sinceRoundEndedAt: hoursAgo(3) }]);
  assert.deepEqual(watchFacts({ ...calm, turnEnds, noteUpdatedAt: hoursAgo(2.5) }, WATCH_NOW), [], 'changed after the oldest of the last three');
  assert.deepEqual(watchFacts({ ...calm, turnEnds: turnEnds.slice(0, 2), noteUpdatedAt: hoursAgo(9) }, WATCH_NOW), [], 'two rounds are not enough');
  assert.equal(watchFacts({ ...calm, turnEnds }, WATCH_NOW)[0].kind, 'note-unchanged', 'a note never written has not changed');
});

test('watch: consecutive proactive failures count per repo; a success resets, unfinished runs do not', () => {
  const run = (repo: string | undefined, status: ProactiveRun['status']) => ({ repo, status });
  assert.deepEqual(watchFacts({ ...calm, runs: [run('a', 'failed'), run('a', 'failed')] }, WATCH_NOW), [{ kind: 'repeated-failure', repo: 'a', failures: 2 }]);
  assert.deepEqual(watchFacts({ ...calm, runs: [run('a', 'failed'), run('b', 'failed')] }, WATCH_NOW), [], 'different repos');
  assert.deepEqual(watchFacts({ ...calm, runs: [run('a', 'failed'), run('a', 'failed'), run('a', 'ready')] }, WATCH_NOW), [], 'the latest one worked');
  assert.deepEqual(watchFacts({ ...calm, runs: [run('a', 'failed'), run('a', 'ready'), run('a', 'failed')] }, WATCH_NOW), [], 'not consecutive');
  assert.equal(watchFacts({ ...calm, runs: [run('a', 'failed'), run('a', 'observed'), run('a', 'failed')] }, WATCH_NOW).length, 1, 'an observed run tried nothing');
  assert.deepEqual(watchFacts({ ...calm, runs: [run(undefined, 'failed'), run(undefined, 'failed')] }, WATCH_NOW), [], 'preparation has no repo');
});

test('the review prompt carries watch facts only for suspicious matters, read from work threads', t => {
  const h = stateFixture(); t.after(() => h.close());
  const summary = (at = Date.now()) => JSON.parse(h.review.prompt(at).split('未完成事项摘要：\n').at(-1)!) as Array<{ id: string; watch?: Array<{ kind: string }> }>;
  const watchOf = (id: string, at?: number) => summary(at).find(todo => todo.id === id)!.watch?.map(fact => fact.kind);
  const thread = (id: string) => { h.store.createThread({ id, agent: 'pi', cwd: h.dir, title: id, permissionMode: 'default', model: null, effort: null }); return id; };
  const rounds = (id: string, count: number, end: Record<string, unknown> = { stopReason: 'end_turn' }, type: 'turn.done' | 'turn.error' = 'turn.done', started: Record<string, unknown> = {}) => {
    for (let i = 0; i < count; i++) { const turn = `${id}-${i}`; h.store.appendEvent(id, turn, 'turn.started', { text: 'work', ...started }); h.store.appendEvent(id, turn, type, end); }
  };
  const link = (todo: string, id: string) => h.store.linkTodoThread(todo, id);

  h.store.createTodo('normal', 'normal', null); link('normal', thread('quiet')); rounds('quiet', 2);
  h.store.createTodo('idle', 'idle', null); link('idle', thread('spinning')); rounds('spinning', 3);
  h.store.createTodo('long', 'long', null); link('long', thread('stuck'));
  h.store.updateThread('stuck', { status: 'running', hubTurnAt: new Date().toISOString() });
  h.store.createTodo('flaky', 'flaky', null);
  h.store.saveHubSetting('personalProactiveRuns', [
    { id: 'a', matterId: 'flaky', kind: 'sandbox', repo: '/repo', threadId: '', startedAt: hoursAgo(2), status: 'failed' },
    { id: 'b', matterId: 'flaky', kind: 'sandbox', repo: '/repo', threadId: '', startedAt: hoursAgo(1), status: 'failed' },
    { id: 'c', matterId: 'normal', kind: 'sandbox', repo: '/repo', threadId: '', startedAt: hoursAgo(1), status: 'failed' },
  ]);

  const later = Date.now() + 3 * 3_600_000;
  assert.equal(watchOf('normal', later), undefined, 'a normal matter has no watch field');
  assert.deepEqual(watchOf('idle', later), ['note-unchanged']);
  assert.deepEqual(watchOf('long', later), ['long-running']);
  assert.equal(watchOf('long'), undefined, 'just started');
  assert.deepEqual(watchOf('flaky', later), ['repeated-failure']);
  const prompt = h.review.prompt(later);
  assert.ok(prompt.includes('带 watch 字段的事项列出了可疑事实（长时间运行、多轮无进展、连续失败）；逐个判断是否空转或跑偏，值得人介入的写进 notify 并点名，正常的不提。'));
  for (const sample of ['"watch": [', '"kind": "note-unchanged"', '"kind": "long-running"', '"kind": "repeated-failure"', '"repo": "/repo"']) assert.ok(prompt.includes(sample), sample);

  h.store.writeTodoNote('idle', 'moved on', null, h.store.getTodo('idle')!.rev);
  assert.equal(watchOf('idle', later), undefined, 'a note written after those rounds clears it');

  // Stopped or cut-off turns are not rounds of work.
  h.store.createTodo('stopped', 'stopped', null); link('stopped', thread('halted')); rounds('halted', 3, { stopReason: 'cancelled' });
  h.store.createTodo('restarted', 'restarted', null); link('restarted', thread('cut')); rounds('cut', 3, { message: 'x', code: 'interrupted' }, 'turn.error');
  assert.equal(watchOf('stopped', later), undefined);
  assert.equal(watchOf('restarted', later), undefined);
  h.store.createTodo('broken', 'broken', null); link('broken', thread('erring')); rounds('erring', 3, { message: 'x' }, 'turn.error');
  assert.deepEqual(watchOf('broken', later), ['note-unchanged'], 'failed rounds are still rounds');

  // Neither the Router, a review, a summary nor an archived thread is a worker.
  const notWork = (name: string, prepare: (id: string) => void) => { h.store.createTodo(name, name, null); const id = thread(`${name}-thread`); link(name, id); prepare(id); assert.equal(watchOf(name, later), undefined, name); };
  notWork('router', id => { rounds(id, 3, undefined, 'turn.done', { hiddenPrompt: true, text: 'hi\n\n<calli-personal-router>\nrules' }); h.store.updateThread(id, { status: 'running', hubTurnAt: new Date().toISOString() }); });
  notWork('review', id => rounds(id, 3, undefined, 'turn.done', { review: true }));
  notWork('summary', id => { rounds(id, 3); h.store.saveSummaryRun('s1', id, 1); });
  notWork('archived', id => { rounds(id, 3); h.store.updateThread(id, { archivedAt: new Date().toISOString() }); });
});
