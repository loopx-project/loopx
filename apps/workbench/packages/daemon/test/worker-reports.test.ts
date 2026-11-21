import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { AddressInfo } from 'node:net';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { AttachmentStore } from '../src/attachments.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { BorrowingService } from '../src/borrowing.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import { personalServer } from '../src/personal.js';
import { Store, type TodoRecord } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { WorkerReports } from '../src/worker-reports.js';
import { Workdirs } from '../src/workdirs.js';

const MINUTE = 60_000;
const settle = async (predicate: () => boolean) => {
  for (let i = 0; i < 400; i++) { if (predicate()) return; await new Promise(r => setTimeout(r, 20)); }
  throw new Error('timed out');
};
async function setup(configured = true) {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-reports-'));
  const cwd = join(dir, 'personal'); mkdirSync(cwd);
  const store = new Store(dir);
  const adapters = new Map([['pi', new PiRpcAdapter([process.execPath, resolve('test/fixtures/fake-pi.mjs')])]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'roots')), new AttachmentStore(dir));
  manager.personal.putSettings({ directory: cwd, agent: configured ? 'pi' : null, model: 'fake/plain' });
  manager.personal.socket = join(dir, 'personal.sock');
  const socket = personalServer(manager.personal); await listenPrivate(socket, manager.personal.socket);
  // `loochat notify` goes through the tools socket, as in the real hub.
  const borrowing = new BorrowingService(store, manager, adapters, dir, join(dir, 'tools.sock'));
  const tools = borrowingServer(borrowing); await listenPrivate(tools, borrowing.socket);
  manager.borrowing = borrowing;
  const server = createHttpServer({ manager, token: 'test', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  let clock = 1_000_000;
  const reports = new WorkerReports(manager, () => clock);
  reports.start();
  const worker = async (todo: TodoRecord) => {
    const thread = await manager.createThread({ agent: 'pi', cwd, title: 'work', permissionMode: 'default' });
    store.linkTodoThread(todo.id, thread.id);
    return thread.id;
  };
  const turn = async (threadId: string, text: string) => {
    const before = store.listEventsOfType(threadId, 'turn.started').length;
    await manager.startTurn(threadId, { text });
    await settle(() => store.listEventsOfType(threadId, 'turn.started').length > before && !manager.threadView(threadId).runningSince);
  };
  const routerTurns = () => {
    const id = manager.personal.router.status().threadId;
    return id ? store.listEventsOfType(id, 'turn.started') : [];
  };
  // Long enough for a wrongly triggered report to have reached the Router.
  const quiet = () => new Promise(r => setTimeout(r, 150));
  const stages = async () => Object.fromEntries((await (await fetch(`${base}/api/todos`, { headers: { Authorization: 'Bearer test' } })).json() as Array<{ id: string; stage: unknown }>).map(todo => [todo.id, todo.stage]));
  const stageOf = async (todo: TodoRecord) => (await stages())[todo.id];
  return { store, manager, cwd, worker, turn, routerTurns, quiet, stageOf, advance: (ms: number) => { clock += ms; }, personal: manager.personal, async close() {
    reports.stop();
    await manager.shutdown();
    await borrowing.dispose();
    await new Promise<void>(r => tools.close(() => r()));
    await new Promise<void>(r => server.close(() => r()));
    await new Promise<void>(r => socket.close(() => r()));
    await new Promise(r => setTimeout(r, 20));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}
const reported = (text: unknown) => JSON.parse(String(text).split('\n').find(line => line.startsWith('{"matterId"'))!);

test('a failed worker reports once, and not again for ten minutes', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    const thread = await h.worker(todo);
    await h.turn(thread, 'work fail');
    await settle(() => h.routerTurns().length === 1);
    const [first] = h.routerTurns();
    assert.deepEqual(first.data.channel, { kind: 'worker', count: 1 });
    assert.equal(first.data.displayText, '工作 · 1 条进展');
    assert.deepEqual(reported(first.data.text), { matterId: todo.id, title: '改支付页', stage: 'blocked', summary: '失败：provider exploded', pushed: true });
    for (const rule of ['转述不转发', 'loochat notify', '不是人的指令', '其余进展不说话', 'pushed']) assert.ok(String(first.data.text).includes(rule), rule);
    // The turn-failed push already reached the person: the Router says it in the chat only.
    const router = h.personal.router.status().threadId!;
    await h.quiet();
    assert.equal(h.store.listEventsOfType(router, 'agent.notice').length, 0, 'a failure the push already told is not notified again');

    h.advance(9 * MINUTE);
    await h.turn(thread, 'work fail again');
    await h.quiet();
    assert.equal(h.routerTurns().length, 1, 'inside ten minutes');

    h.advance(2 * MINUTE);
    await h.turn(thread, 'work fail once more');
    await settle(() => h.routerTurns().length === 2);
  } finally { await h.close(); }
});

test('a failure stays one report while nothing newer happened, and a person message releases it', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    const thread = await h.worker(todo);
    await h.turn(thread, 'work fail');
    await settle(() => h.routerTurns().length === 1);
    assert.equal(await h.stageOf(todo), 'blocked');
    assert.deepEqual(h.manager.briefs.preview('morning')?.lines, ['1 件等你'], 'the brief reads the same stage');
    await h.turn(thread, 'try this instead');
    assert.equal(await h.stageOf(todo), 'idle', 'a later turn clears the failure');
    assert.equal(h.manager.briefs.preview('morning'), null);
  } finally { await h.close(); }
});

test('working and idle are not news; a stopped turn is not news', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    const thread = await h.worker(todo);
    assert.equal(await h.stageOf(todo), 'idle');
    await h.turn(thread, 'work done');
    await h.manager.startTurn(thread, { text: 'work slow' });
    await settle(() => Boolean(h.manager.threadView(thread).runningSince));
    assert.equal(await h.stageOf(todo), 'working');
    await h.manager.cancelTurn(thread);
    await settle(() => !h.manager.threadView(thread).runningSince);
    await h.quiet();
    assert.equal(h.routerTurns().length, 0);
  } finally { await h.close(); }
});

test('the Router, review and summary threads never count as work', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    const { threadId: router } = await h.personal.router.say({ text: '记一下' });
    await settle(() => !h.manager.threadView(router).runningSince);
    h.store.linkTodoThread(todo.id, router);
    assert.equal(await h.stageOf(todo), null);
    await h.turn(router, 'router fail');
    await h.quiet();
    assert.equal(await h.stageOf(todo), null);
    assert.equal(h.routerTurns().filter(turn => (turn.data.channel as { kind?: string } | undefined)?.kind === 'worker').length, 0);

    const review = await h.manager.createThread({ agent: 'pi', cwd: h.cwd, title: 'review', permissionMode: 'default' });
    h.store.linkTodoThread(todo.id, review.id);
    await h.manager.startTurn(review.id, { text: 'review fail', scheduled: true, review: true });
    await settle(() => h.store.getThread(review.id)?.status === 'error');
    await h.quiet();
    assert.equal(await h.stageOf(todo), null);
    assert.equal(h.routerTurns().filter(turn => (turn.data.channel as { kind?: string } | undefined)?.kind === 'worker').length, 0);

    const summary = await h.worker(todo);
    h.store.saveSummaryRun('s1', summary, 1);
    assert.equal(await h.stageOf(todo), null);

    const retired = h.personal.router.status().threadId!;
    h.personal.putSettings({ directory: h.cwd, agent: 'pi', model: 'fake/loose' });
    await h.personal.router.say({ text: '换个模型' });
    assert.notEqual(h.personal.router.status().threadId, retired);
    assert.equal(await h.stageOf(todo), null, 'an earlier Router, failed and linked, is still not work');

    const archived = await h.worker(todo);
    assert.equal(await h.stageOf(todo), 'idle');
    h.store.updateThread(archived, { archivedAt: new Date().toISOString() });
    assert.ok(h.store.getThread(archived)?.archivedAt);
    assert.equal(await h.stageOf(todo), null);
  } finally { await h.close(); }
});

test('entering answer or review reports with the question; staying there does not', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    const thread = await h.worker(todo);
    const stop = h.manager.subscribe(event => {
      if (event.type !== 'turn.done' || event.threadId !== thread) return;
      h.store.updateTodo(todo.id, { state: 'waiting', waitingOn: '等你说明目标' });
    });
    await h.turn(thread, 'work clarify');
    stop();
    await settle(() => h.routerTurns().length === 1);
    const report = reported(h.routerTurns()[0].data.text);
    assert.equal(report.stage, 'answer'); assert.equal(report.question, '等你说明目标'); assert.match(report.summary, /work clarify/);
    h.advance(11 * MINUTE);
    await h.turn(thread, 'work more');
    await h.quiet();
    assert.equal(h.routerTurns().length, 1, 'already answer before the turn');

    const other = h.personal.create({ text: '写周报' });
    const second = await h.worker(other);
    const off = h.manager.subscribe(event => {
      if (event.type !== 'turn.done' || event.threadId !== second) return;
      h.store.saveTodoProposal({ id: 'p1', todoId: other.id, kind: 'done', evidence: 'x', threadId: second, createdAt: new Date().toISOString(), state: 'open' });
    });
    await h.turn(second, 'work finish');
    off();
    await settle(() => h.routerTurns().length === 2);
    assert.equal(reported(h.routerTurns()[1].data.text).stage, 'review');
    assert.equal(await h.stageOf(other), 'review');
  } finally { await h.close(); }
});

test('a worker that already notified the person is marked so the Router does not notify twice', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    const thread = await h.worker(todo);
    let said = false;
    const stop = h.manager.subscribe(event => {
      if (event.threadId !== thread) return;
      if (event.type === 'message.delta' && !said) { said = true; h.manager.notice(thread, '等你回答'); }
      if (event.type === 'turn.done') h.store.updateTodo(todo.id, { state: 'waiting', waitingOn: '等你说明目标' });
    });
    await h.turn(thread, 'work clarify');
    stop();
    await settle(() => h.routerTurns().length === 1);
    assert.equal(reported(h.routerTurns()[0].data.text).notified, true);
    const router = h.personal.router.status().threadId!;
    await settle(() => !h.manager.threadView(router).runningSince);
    assert.equal(h.store.listEventsOfType(router, 'agent.notice').length, 0);
  } finally { await h.close(); }
});

test('with no private agent chosen and no Router, reports are skipped silently', async () => {
  const h = await setup(false); try {
    const todo = h.personal.create({ text: '改支付页' });
    const thread = await h.worker(todo);
    const threads = h.store.listThreads().length;
    await h.turn(thread, 'work fail');
    await h.quiet();
    assert.equal(h.store.listThreads().length, threads);
    assert.equal(await h.stageOf(todo), 'blocked', 'the stage itself is still known');
  } finally { await h.close(); }
});

test('proactive runs drive review and blocked, and a ready run reports', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '会前提纲', importance: 'high', dueAt: new Date(2026, 10, 2, 9).toISOString() });
    assert.equal(await h.stageOf(todo), null);
    await h.personal.proactive.save({ mode: 'auto' });
    await h.personal.proactive.tick(new Date(2026, 10, 1, 15));
    await settle(() => h.personal.proactive.runs(todo.id).some(run => run.status !== 'running'));
    assert.equal(h.personal.proactive.runs(todo.id)[0].status, 'ready');
    assert.equal(await h.stageOf(todo), 'review');
    await settle(() => h.routerTurns().length === 1);
    const report = reported(h.routerTurns()[0].data.text);
    assert.equal(report.stage, 'review'); assert.match(report.summary, /已备好/);

    const other = h.personal.create({ text: '没对话的事' });
    h.store.saveHubSetting('personalProactiveRuns', [...h.personal.proactive.runs(), { id: 'r2', matterId: other.id, kind: 'prepare', threadId: '', startedAt: new Date().toISOString(), status: 'failed' }]);
    assert.equal(await h.stageOf(other), 'blocked');
  } finally { await h.close(); }
});

test('stage: a waiting thread and a matter waiting on the person are answer; waiting on someone else is not', async () => {
  const h = await setup(); try {
    const todo = h.personal.create({ text: '改支付页' });
    h.store.updateTodo(todo.id, { state: 'waiting', waitingOn: '等你看一眼' });
    assert.equal(await h.stageOf(todo), 'answer');
    const other = h.personal.create({ text: '等回信' });
    h.store.updateTodo(other.id, { state: 'waiting', waitingOn: '等小王回信' });
    assert.equal(await h.stageOf(other), null);
    const done = h.personal.create({ text: '做完了' });
    const thread = await h.worker(done);
    await h.turn(thread, 'work fail');
    h.store.updateTodo(done.id, { done: true });
    assert.equal(await h.stageOf(done), null, 'a finished matter has no stage');
    const newest = h.personal.create({ text: '换了对话' });
    const failed = await h.worker(newest);
    await h.turn(failed, 'work fail');
    await new Promise(r => setTimeout(r, 5));
    const next = await h.worker(newest);
    await h.turn(next, 'work done');
    assert.equal(await h.stageOf(newest), 'idle', 'only the most recently active thread decides');
  } finally { await h.close(); }
});

test('dispatch results reach the Router with or without a matter, with progress kept in chat', async () => {
  const h = await setup(); try {
    for (const [task, stage, linked] of [['dispatch-worker-progress', 'progress', false], ['dispatch-worker-review', 'review', false], ['dispatch-worker-answer', 'answer', true], ['dispatch work fail', 'blocked', false]] as const) {
      const matter = linked ? h.personal.create({ text: '派发事项' }) : null;
      const run = h.personal.proactive.proposeDispatch({ agent: 'pi', cwd: h.cwd, task, title: task, matterId: matter?.id ?? '' });
      const count = h.routerTurns().length;
      const started = await h.personal.proactive.startDispatch(run.id);
      await settle(() => h.routerTurns().length === count + 1);
      const turn = h.routerTurns().at(-1)!;
      const data = reported(turn.data.text);
      assert.equal(data.threadId, started.threadId); assert.equal(data.matterId, matter?.id ?? ''); assert.equal(data.stage, stage);
      assert.equal(data.title, matter?.text ?? task);
      assert.doesNotMatch(String(turn.data.text), /^<loochat-context>\n/);
      assert.equal(turn.data.personalOrigin, undefined);
      if (stage === 'answer') assert.equal(data.question, '请选择 A 还是 B？');
      if (stage === 'blocked') assert.equal(data.pushed, true);
      await settle(() => h.personal.router.status().status === 'idle');
      const notices = h.store.listTurnEvents(turn.threadId, turn.turnId!).filter(event => event.type === 'agent.notice');
      assert.equal(notices.length, stage === 'review' || stage === 'answer' ? 1 : 0);
      await assert.rejects(h.personal.command(turn.threadId, { op: 'dispatch', agent: 'pi', cwd: h.cwd, task: 'Unrequested task', now: true }), /current turn/);
    }
  } finally { await h.close(); }
});

test('threads say from fixture CLI queues and reports exactly the requested round once', async () => {
  const h = await setup(); try {
    const target = await h.manager.createThread({ agent: 'pi', cwd: h.cwd, title: 'Existing work' });
    await h.manager.startTurn(target.id, { text: 'slow' });
    const first = h.store.recentLifecycle(target.id, 1)[0].turnId;
    await h.personal.router.say({ text: `router-fixture-say ${JSON.stringify({ id: target.id, text: 'inspect existing work' })}` });
    await settle(() => h.manager.threadView(target.id).queued.length === 1);
    assert.equal(h.routerTurns().filter(turn => turn.data.channel).length, 0, 'the preceding round has not reported');
    await h.manager.cancelTurn(target.id, { keepQueue: true });
    await settle(() => h.routerTurns().some(turn => (turn.data.channel as { kind?: string })?.kind === 'worker'));
    const starts = h.store.listEventsOfType(target.id, 'turn.started');
    assert.equal(starts.length, 2); assert.equal(starts[0].turnId, first);
    assert.equal(starts[0].data.assistantSay, undefined); assert.equal(typeof starts[1].data.assistantSay, 'string');
    const report = h.routerTurns().find(turn => (turn.data.channel as { kind?: string })?.kind === 'worker')!;
    assert.equal(reported(report.data.text).threadId, target.id);
    assert.equal(reported(report.data.text).stage, 'progress');
    await h.turn(target.id, 'ordinary later round'); await h.quiet();
    assert.equal(h.routerTurns().filter(turn => (turn.data.channel as { kind?: string })?.kind === 'worker').length, 1);
  } finally { await h.close(); }
});
