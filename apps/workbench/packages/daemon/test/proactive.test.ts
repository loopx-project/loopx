import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store, type TodoRecord } from '../src/store.js';
import { Workdirs } from '../src/workdirs.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import { personalServer } from '../src/personal.js';
import { advancePrompt, parseAdvance, transientError } from '../src/proactive-advance.js';
import { preparationCandidate, ProactiveService, type ProactiveRun } from '../src/proactive.js';
import { ProactiveSandbox } from '../src/proactive-sandbox.js';
import { PushService } from '../src/push.js';
import { Scheduler } from '../src/scheduler.js';

const exec = promisify(execFile);
const now = new Date(2026, 10, 1, 12);
const at = (hour: number, day = 1) => new Date(2026, 10, day, hour).toISOString();
const wait = async (fn: () => boolean) => {
  for (let i = 0; i < 500; i++) { if (fn()) return; await new Promise(r => setTimeout(r, 20)); }
  throw new Error('timed out');
};
const historical = (id: string, kind: ProactiveRun['kind'], startedAt = at(8)): ProactiveRun => ({ id, kind, matterId: `old-${id}`, threadId: '', startedAt, endedAt: startedAt, status: 'discarded' });
async function setup(mode = '') {
  const dir = mkdtempSync(join(tmpdir(), 'calli-proactive-'));
  const cwd = join(dir, 'personal'); mkdirSync(cwd);
  const store = new Store(dir);
  const adapters = new Map<string, AgentAdapter>([['claude', new ClaudeAcpAdapter([process.execPath, resolve('test/fixtures/fake-acp.mjs')], mode ? { FAKE_PROACTIVE_MODE: mode } : {})]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'roots'), join(dir, 'scratch')), new AttachmentStore(dir));
  const personal = manager.personal, service = personal.proactive;
  personal.putSettings({ directory: cwd, agent: 'claude' });
  personal.socket = join(dir, 'personal.sock');
  const socket = personalServer(personal); await listenPrivate(socket, personal.socket);
  const server = createHttpServer({ manager, token: 'test', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + path, { method, headers: { Authorization: 'Bearer test', 'Content-Type': 'application/json' }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const git = async (repo: string, ...args: string[]) => (await exec('git', ['-C', repo, '-c', 'user.name=Tester', '-c', 'user.email=tester@localhost', '-c', 'commit.gpgsign=false', ...args])).stdout.trim();
  const repository = async (name = 'repo') => {
    const repo = join(dir, name); mkdirSync(repo);
    await git(repo, 'init', '-q', '-b', 'main');
    writeFileSync(join(repo, 'base.txt'), 'base\n'); await git(repo, 'add', '.'); await git(repo, 'commit', '-qm', 'initial');
    return repo;
  };
  const todo = (input: Record<string, unknown> = {}) => personal.create({ text: '会前提纲', importance: 'high', dueAt: at(15), ...input });
  const settled = async (id?: string) => {
    await wait(() => service.runs().some(run => (!id || run.id === id) && run.status !== 'running'));
    await wait(() => store.listThreads().every(thread => thread.status !== 'running'));
    await service.flush(); await personal.flushMirror();
    return service.runs().find(run => !id || run.id === id)!;
  };
  return { dir, cwd, store, manager, personal, service, adapters, api, git, repository, todo, settled, async close() {
    await manager.shutdown(); await service.flush(); await personal.flushMirror();
    await new Promise<void>(r => server.close(() => r())); await new Promise<void>(r => socket.close(() => r()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}

test('settings are opt-in, validate the whole list, canonicalize symlinks and persist', async () => {
  const h = await setup(), outside = mkdtempSync(join(tmpdir(), 'calli-proactive-outside-'));
  try {
    assert.deepEqual(await (await h.api('/api/personal/proactive')).json(), { settings: { mode: 'off', repos: [], enabled: false }, runs: [] });
    const repo = await h.repository();
    const link = join(h.dir, 'repo-link'); symlinkSync(repo, link);
    // The old switch still works: turning it on lands in observe, not in auto.
    const saved = await h.api('/api/personal/proactive/settings', 'PUT', { enabled: true, repos: [repo, link] });
    assert.equal(saved.status, 200); assert.deepEqual(await saved.json(), { mode: 'observe', repos: [repo], enabled: true });
    for (const [repos, status] of [[['relative'], 400], [[h.cwd], 400], [[join(h.dir, 'missing')], 400], [[outside], 403], [[repo, h.cwd], 400], ['bad', 400]] as const) {
      assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', { mode: 'off', repos })).status, status);
      assert.deepEqual(h.service.settings(), { mode: 'observe', repos: [repo], enabled: true }, 'an invalid list never partially saves the mode or repos');
    }
    symlinkSync(outside, join(h.dir, 'escape'));
    assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', { repos: [join(h.dir, 'escape')] })).status, 403);
    mkdirSync(join(repo, 'nested'));
    assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', { repos: [join(repo, 'nested')] })).status, 400);
    assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', { enabled: 'yes' })).status, 400);
    assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', { mode: 'whenever' })).status, 400);
    for (const body of [null, [], 'settings']) assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', body)).status, 400);
    const reborn = new ProactiveService(h.store, h.manager, h.personal, h.adapters);
    assert.deepEqual(reborn.settings(), h.service.settings());
    assert.equal((await h.api('/api/personal/proactive/settings', 'PUT', { repos: [] })).status, 200);
    assert.deepEqual(h.service.settings(), { mode: 'observe', repos: [], enabled: true });
  } finally { await h.close(); rmSync(outside, { recursive: true, force: true }); }
});

test('legacy switches migrate on read; the old switch writes observe, an explicit mode wins', async () => {
  const h = await setup(); try {
    h.store.saveHubSetting('personalProactiveSettings', { enabled: true, repos: [h.cwd] });
    assert.deepEqual(h.service.settings(), { mode: 'auto', repos: [h.cwd], enabled: true }, 'an old on switch was acting, so it stays auto');
    h.store.saveHubSetting('personalProactiveSettings', { enabled: false, repos: [] });
    assert.deepEqual(h.service.settings(), { mode: 'off', repos: [], enabled: false });
    h.store.saveHubSetting('personalProactiveSettings', {});
    assert.deepEqual(h.service.settings(), { mode: 'off', repos: [], enabled: false });
    assert.deepEqual(await h.service.save({ enabled: true }), { mode: 'observe', repos: [], enabled: true }, 'newly opened starts in observe');
    assert.deepEqual(await h.service.save({ enabled: true, mode: 'auto' }), { mode: 'auto', repos: [], enabled: true }, 'an explicit mode wins over the switch');
    assert.deepEqual(await h.service.save({ enabled: false }), { mode: 'off', repos: [], enabled: false });
    await assert.rejects(h.service.save({ mode: 'whenever' }), (error: { status?: number }) => error.status === 400);
    assert.deepEqual(h.service.settings(), { mode: 'off', repos: [], enabled: false }, 'a rejected mode writes nothing');
  } finally { await h.close(); }
});

test('preparation candidates require an accepted open matter, timing and no prepared note', async () => {
  const h = await setup(); try {
    const base = h.todo();
    const candidate = (patch: Partial<TodoRecord>, note = '') => preparationCandidate({ ...base, ...patch }, note, now);
    assert.equal(candidate({}), true);
    for (const patch of [{ done: true }, { state: 'done' as const }, { proposed: true }, { dueAt: at(13, 3) }, { dueAt: null }, { importance: 'normal' as const, dueAt: null, urgentUntil: at(13) }]) assert.equal(candidate(patch), false);
    assert.equal(candidate({}, '## 做到哪\n已备好会前提纲'), false);
    assert.equal(candidate({ dueAt: at(12, 3) }), true, 'inclusive 48-hour boundary');
    assert.equal(candidate({ importance: 'normal', dueAt: at(10) }), true, 'overdue is urgent');
    assert.equal(candidate({ importance: 'normal', dueAt: null, source: { type: 'lark-calendar', ref: 'meeting', at: at(15) } }), true);
    assert.equal(candidate({ dueAt: null, source: { type: 'lark-calendar', ref: 'meeting', at: at(15, 2) } }), false);
  } finally { await h.close(); }
});

test('idle gates: disabled, empty, shared quiet hours, busy threads, interval and daily limit', async () => {
  const h = await setup(); try {
    await h.service.tick(now); assert.equal(h.service.runs().length, 0);
    await h.service.save({ mode: 'auto' }); await h.service.tick(now); assert.equal(h.store.listThreads().length, 0);
    const todo = h.todo();
    await h.service.save({ mode: 'off' }); await h.service.tick(now); assert.equal(h.service.runs().length, 0);
    await h.service.save({ mode: 'auto' });
    await h.service.tick(new Date(2026, 10, 1, 23)); await h.service.tick(new Date(2026, 10, 1, 7, 59));
    assert.equal(h.service.runs().length, 0);
    const review = h.personal.review.status();
    h.store.saveHubSetting('personalReview', { ...review, quietHours: { start: '11:00', end: '13:00' } });
    await h.service.tick(now); assert.equal(h.service.runs().length, 0, 'uses the edited brief/review window');
    h.store.saveHubSetting('personalReview', review);
    const busy = await h.manager.createThread({ agent: 'claude', cwd: h.cwd }); h.store.updateThread(busy.id, { status: 'running' });
    h.store.linkTodoThread(todo.id, busy.id);
    await h.service.tick(now); assert.equal(h.service.runs().length, 0);
    h.store.updateThread(busy.id, { status: 'idle' }); h.store.unlinkTodoThread(todo.id, busy.id);
    h.store.saveHubSetting('personalProactiveRuns', [historical('recent', 'sandbox', new Date(now.getTime() - 3599999).toISOString())]);
    await h.service.tick(now); assert.equal(h.service.runs().length, 1, 'interval includes other kinds');
    h.store.saveHubSetting('personalProactiveRuns', Array.from({ length: 5 }, (_, i) => historical(String(i), 'prepare', at(5 + i))));
    await h.service.tick(now); assert.equal(h.service.runs().length, 5);
    h.store.saveHubSetting('personalProactiveRuns', [historical('exact-hour', 'prepare', at(11))]);
    await Promise.all([h.service.tick(now), h.service.tick(now)]); await h.settled();
    assert.equal(h.service.runs().length, 2, 'exactly one hour and overlapping ticks start one run');
  } finally { await h.close(); }
});

test('L1 fake agent writes artifacts, updates the note, commits only materials and records a quiet notice', async () => {
  const h = await setup();
  const push = new PushService(h.store, h.manager);
  try {
    push.putSettings({ enabled: true, recipient: 'test-user', scheduled: true });
    const todo = h.todo(); await h.personal.flushMirror();
    writeFileSync(join(h.cwd, 'unrelated.txt'), 'keep staged\n'); await h.git(h.cwd, 'add', '--', 'unrelated.txt');
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    const run = await h.settled();
    assert.equal(run.status, 'ready', run.summary); assert.equal(run.kind, 'prepare'); assert.equal(run.matterId, todo.id);
    assert.ok(run.threadId); assert.ok(run.endedAt); assert.match(run.summary!, /已备好会前提纲/);
    assert.equal(run.files?.length, 1); assert.match(run.files![0].path, /artifacts\/outline\.md$/);
    assert.equal(run.files![0].additions, 3); assert.equal(run.files![0].deletions, 0);
    assert.match(readFileSync(join(h.cwd, run.files![0].path), 'utf8'), /会前提纲/);
    assert.match(h.store.todoNote(todo.id).text, /已备好/);
    assert.equal(h.store.todoThreadIds(todo.id).includes(run.threadId), true);
    const log = await h.git(h.cwd, 'log', '--format=%an %s', '--', run.files![0].path);
    assert.match(log, new RegExp(`Calli .*prepare.*${todo.id}|Calli .*${todo.id}.*prepare`));
    assert.equal(await h.git(h.cwd, 'diff', '--cached', '--name-only'), 'unrelated.txt');
    const notices = h.store.listEventsOfType(run.threadId, 'agent.notice');
    assert.equal(notices.length, 1); assert.equal(notices[0].data.proactive, true);
    assert.equal((await push.status()).records.length, 0, 'neither notice nor routine completion pushes');
    await h.service.save({ mode: 'off' }); await h.service.tick(new Date(2026, 10, 1, 14)); assert.equal(h.service.runs().length, 1);
    const counts = await (await h.api('/api/personal/proactive?summary=true')).json(); assert.deepEqual(counts.counts, { [todo.id]: 1 }); assert.equal('runs' in counts, false);
    const todos = await (await h.api('/api/todos')).json(); assert.equal(todos[0].proactiveRunCount, 1); assert.equal('files' in todos[0], false);
    assert.deepEqual((await (await h.api(`/api/personal/proactive?matterId=${todo.id}`)).json()).runs, [run]);
    assert.deepEqual((await (await h.api('/api/personal/proactive?matterId=missing')).json()).runs, []);
    assert.equal((await h.api(`/api/personal/proactive/runs/${run.id}/merge`, 'POST')).status, 400);
    assert.equal((await h.api(`/api/personal/proactive/runs/${run.id}/diff`)).status, 400);
  } finally { push.dispose(); await h.close(); }
});

test('no-op preparation ends quietly; worker failure is visible and counted', async () => {
  for (const mode of ['quiet', 'error']) {
    const h = await setup(mode); try {
      h.todo(); await h.service.save({ mode: 'auto' }); await h.service.tick(now);
      const run = await h.settled();
      assert.equal(run.status, mode === 'quiet' ? 'discarded' : 'failed', run.summary);
      if (mode === 'error') assert.match(run.summary!, /fixture proactive failed/);
      assert.equal(h.store.listEventsOfType(run.threadId, 'agent.notice').length, 0);
      await h.service.tick(new Date(now.getTime() + 1000)); assert.equal(h.service.runs().length, 1);
    } finally { await h.close(); }
  }
});

test('observe records what would be done and why, starting no thread and no sandbox', async () => {
  const h = await setup(); try {
    const repo = await h.repository();
    const todo = h.todo({ importance: 'normal', dueAt: null }); // not worth preparing; but started in the repo
    const work = await h.manager.createThread({ agent: 'claude', cwd: repo }); h.store.linkTodoThread(todo.id, work.id);
    await h.service.save({ mode: 'observe', repos: [repo] });
    await h.service.tick(now);
    const observation = h.service.runs()[0];
    assert.equal(observation.status, 'observed'); assert.equal(observation.kind, 'sandbox');
    assert.equal(observation.matterId, todo.id); assert.equal(observation.repo, repo);
    assert.equal(observation.threadId, ''); assert.equal(observation.branch, undefined);
    assert.equal(observation.summary, `会在 ${repo.split('/').pop()} 的沙盒里继续「${todo.text}」：你之前开始过这件事`);
    assert.equal(existsSync(join(repo, '.worktrees')), false, 'no sandbox was prepared');
    assert.deepEqual(h.store.todoThreadIds(todo.id), [work.id], 'no thread was opened on the matter');
  } finally { await h.close(); }
});

test('observations are free of the daily limit but keep the hour, one per matter per day', async () => {
  const h = await setup(); try {
    const todo = h.todo();
    await h.service.save({ mode: 'observe' });
    const observed = (id: string, startedAt: string): ProactiveRun =>
      ({ ...historical(id, 'prepare', startedAt), status: 'observed', summary: '旧的观察' });
    h.store.saveHubSetting('personalProactiveRuns', Array.from({ length: 5 }, (_, i) => observed(String(i), at(5 + i))));
    await h.service.tick(now);
    const first = h.service.runs().at(-1)!;
    assert.equal(h.service.runs().length, 6, 'five observations today do not consume the daily limit');
    assert.equal(first.status, 'observed'); assert.equal(first.matterId, todo.id);
    assert.equal(first.summary, `会为「${todo.text}」提前准备材料：重要，今天截止`);
    await h.service.tick(new Date(2026, 10, 1, 14));
    assert.equal(h.service.runs().length, 6, 'the same candidate is observed once per day');
    h.store.saveHubSetting('personalProactiveRuns', [observed('recent', new Date(now.getTime() - 3599999).toISOString())]);
    await h.service.tick(now);
    assert.equal(h.service.runs().length, 1, 'an observation still waits a full hour');
    h.store.saveHubSetting('personalProactiveRuns', [observed('hour-edge', at(11))]);
    await h.service.tick(now);
    assert.equal(h.service.runs().length, 2, 'a full hour admits the next observation');
  } finally { await h.close(); }
});

test('approve marks an observation as read; other states refuse and a verdict mutes the candidate for a day', async () => {
  const h = await setup(); try {
    const todo = h.todo({ dueAt: at(15, 2) });
    await h.service.save({ mode: 'observe' });
    await h.service.tick(now);
    const observation = h.service.runs()[0];
    // Through the route: a 200 with the whole run; the same call again is a 409 the page can show.
    const marked = await h.api(`/api/personal/proactive/runs/${observation.id}/approve`, 'POST');
    assert.equal(marked.status, 200);
    assert.equal((await marked.json()).status, 'approved');
    assert.equal((await h.api(`/api/personal/proactive/runs/${observation.id}/approve`, 'POST')).status, 409);
    h.store.saveHubSetting('personalProactiveRuns', h.service.runs().map(run => run.id === observation.id ? { ...run, status: 'observed' } : run));
    assert.equal(h.service.approve(observation.id).status, 'approved');
    assert.throws(() => h.service.approve(observation.id),
      (error: { status: number; message: string }) => error.status === 409 && /只有观察记录可以标记/.test(error.message), 'only observed can be approved');
    h.store.saveHubSetting('personalProactiveRuns', [...h.service.runs(), { ...historical('done', 'prepare'), status: 'ready' }]);
    assert.throws(() => h.service.approve('done'),
      (error: { status: number; message: string }) => error.status === 409 && /只有观察记录可以标记/.test(error.message), 'a ready run is not an observation');
    assert.throws(() => h.service.approve('missing'), (error: { status: number }) => error.status === 404);
    assert.equal(h.service.counts()[todo.id], 1, 'approved observations stay counted');
    await h.service.tick(new Date(2026, 10, 1, 14));
    assert.equal(h.service.runs().length, 2, 'a verdict does not trigger execution and does not unblock a new record');
    await h.service.tick(new Date(2026, 10, 2, 13));
    assert.equal(h.service.runs().length, 3, 'a day later the same candidate is observed again');
    assert.equal(h.service.runs().filter(run => run.matterId === todo.id).length, 2);
  } finally { await h.close(); }
});

test('switching from observe to auto starts the very candidate that was recorded', async () => {
  const h = await setup(); try {
    const todo = h.todo();
    await h.service.save({ mode: 'observe' });
    await h.service.tick(now);
    assert.equal(h.service.runs()[0].status, 'observed');
    assert.equal(h.store.listThreads().length, 0);
    await h.service.save({ mode: 'auto' });
    await h.service.tick(new Date(2026, 10, 1, 14));
    const run = await h.settled(h.service.runs().at(-1)!.id);
    assert.equal(run.status, 'ready', run.summary); assert.equal(run.kind, 'prepare'); assert.equal(run.matterId, todo.id);
    assert.ok(run.threadId);
    assert.equal(h.store.todoThreadIds(todo.id).includes(run.threadId), true);
  } finally { await h.close(); }
});

test('a sandbox round with failing tests ends silently: audit kept, no notice, no card', async () => {
  const h = await setup(); try {
    const repo = await h.repository();
    const todo = h.todo({ importance: 'normal', dueAt: null });
    const work = await h.manager.createThread({ agent: 'claude', cwd: repo }); h.store.linkTodoThread(todo.id, work.id);
    await h.service.save({ mode: 'auto', repos: [repo] });
    // The fixture passes its check; doctor the answer the way an honest failing run would report it.
    const answers = h.store.voiceAnswerEvents.bind(h.store);
    h.store.voiceAnswerEvents = (threadId: string, turnId: string | null) => {
      const events = answers(threadId, turnId); const last = events.at(-1);
      return last ? [...events, { ...last, type: 'message.delta' as const, data: { text: '\n2 个用例失败' } }] : events;
    };
    await h.service.tick(now);
    const failed = await h.settled(h.service.runs().at(-1)!.id);
    assert.equal(failed.status, 'discarded'); assert.equal(failed.discardReason, '测试未通过');
    assert.equal(failed.summary, '补好沙盒示例。');
    assert.match(failed.testReport!, /失败/);
    assert.deepEqual(failed.files, [{ path: 'proactive-fixture.txt', additions: 1, deletions: 0 }]);
    assert.equal(h.store.listEventsOfType(failed.threadId, 'agent.notice').length, 0, 'no notice went out');
    assert.deepEqual(h.service.counts(), {}, 'a silent trial does not light the desk');
    assert.equal(existsSync(join(repo, 'proactive-fixture.txt')), false, 'the main workspace was never touched');
  } finally { await h.close(); }
});

test('a sandbox round with nothing to review ends silently too', async () => {
  const h = await setup('quiet'); try {
    const repo = await h.repository();
    const todo = h.todo({ importance: 'normal', dueAt: null });
    const work = await h.manager.createThread({ agent: 'claude', cwd: repo }); h.store.linkTodoThread(todo.id, work.id);
    await h.service.save({ mode: 'auto', repos: [repo] });
    await h.service.tick(now);
    const run = await h.settled(h.service.runs().at(-1)!.id);
    assert.equal(run.status, 'discarded'); assert.equal(run.discardReason, '没有可审阅的改动');
    assert.equal(run.testReport, '工作线程未报告测试结果');
    assert.deepEqual(run.files, []);
    assert.equal(h.store.listEventsOfType(run.threadId, 'agent.notice').length, 0);
    assert.deepEqual(h.service.counts(), {});
  } finally { await h.close(); }
});

test('L2 requires a prior linked work thread inside an explicitly allowed repo and respects its daily limit', async () => {
  const h = await setup(); try {
    const repo = await h.repository();
    const todo = h.todo({ importance: 'normal', dueAt: null });
    const plain = join(h.dir, 'plain'); mkdirSync(plain);
    const other = await h.manager.createThread({ agent: 'claude', cwd: plain }); h.store.linkTodoThread(todo.id, other.id);
    await h.service.save({ mode: 'auto', repos: [repo] });
    await h.service.tick(now); assert.deepEqual(h.service.runs(), []);
    const work = await h.manager.createThread({ agent: 'claude', cwd: repo }); h.store.linkTodoThread(todo.id, work.id);
    await h.service.save({ repos: [] }); await h.service.tick(now); assert.equal(h.service.runs()[0].needsRepo, true);
    await h.service.save({ repos: [repo] });
    h.store.saveHubSetting('personalProactiveRuns', [historical('one', 'sandbox', at(8)), historical('two', 'sandbox', at(9))]);
    await h.service.tick(now); assert.equal(h.service.runs().length, 2);
    h.store.saveHubSetting('personalProactiveRuns', [historical('yesterday', 'sandbox', at(9, 0))]);
    await h.service.tick(now); const run = await h.settled(h.service.runs().at(-1)!.id);
    assert.equal(run.status, 'ready', run.summary); assert.equal(run.kind, 'sandbox'); assert.equal(run.repo, repo);
    assert.equal(run.branch, `calli/proactive/${todo.id}`); assert.notEqual(h.store.getThread(run.threadId)!.cwd, repo);
    assert.equal(existsSync(join(repo, 'proactive-fixture.txt')), false);
    assert.equal(run.summary, '补好沙盒示例。'); assert.equal(run.testReport, 'fixture check: passed');
    assert.deepEqual(run.files, [{ path: 'proactive-fixture.txt', additions: 1, deletions: 0 }]);
    await h.service.tick(new Date(2026, 10, 1, 14)); assert.equal(h.service.runs().length, 2, 'ready work awaits the person');
  } finally { await h.close(); }
});

test('sandbox API: real git diff, refused merge preserves status, merge/revert/discard and invalid transitions', async () => {
  const h = await setup(); try {
    const repo = await h.repository(); const sandbox = new ProactiveSandbox();
    const seed = async (matterId: string) => {
      const tree = await sandbox.prepare(repo, matterId);
      writeFileSync(join(tree.root, `${matterId}.txt`), `${matterId}\n`);
      await h.git(tree.root, 'add', '.'); await h.git(tree.root, 'commit', '-qm', `change\n\nCalli-Proactive: ${matterId}`);
      const run: ProactiveRun = { ...historical(matterId, 'sandbox'), matterId, repo, branch: tree.branch, status: 'ready' };
      h.store.saveHubSetting('personalProactiveRuns', [...h.service.runs(), run]); return run;
    };
    const run = await seed('merge-me'); const endpoint = `/api/personal/proactive/runs/${run.id}`;
    assert.deepEqual(await (await h.api(`${endpoint}/diff`)).json(), { files: [{ path: 'merge-me.txt', additions: 1, deletions: 0 }] });
    assert.equal((await h.api(`${endpoint}/revert`, 'POST')).status, 409);
    await h.git(repo, 'switch', '-qc', 'feature');
    const refused = await h.api(`${endpoint}/merge`, 'POST'); assert.equal(refused.status, 409); assert.match((await refused.json()).error, /默认分支/);
    assert.equal(h.service.runs()[0].status, 'ready'); await h.git(repo, 'switch', 'main');
    const merged = await h.api(`${endpoint}/merge`, 'POST'); assert.equal(merged.status, 200); assert.equal((await merged.json()).status, 'merged');
    assert.equal(readFileSync(join(repo, 'merge-me.txt'), 'utf8'), 'merge-me\n');
    assert.match(await h.git(repo, 'log', '-1', '--format=%B'), /Calli-Proactive: merge-me/);
    assert.equal((await h.api(`${endpoint}/discard`, 'POST')).status, 409);
    assert.equal((await h.api(`${endpoint}/merge`, 'POST')).status, 409);
    const reverted = await h.api(`${endpoint}/revert`, 'POST'); assert.equal(reverted.status, 200); assert.equal((await reverted.json()).status, 'reverted');
    assert.equal(existsSync(join(repo, 'merge-me.txt')), false);
    assert.equal((await h.api(`${endpoint}/revert`, 'POST')).status, 409);
    const discard = await seed('discard-me');
    assert.equal((await h.api(`/api/personal/proactive/runs/${discard.id}/discard`, 'POST')).status, 200);
    assert.equal(h.service.runs().find(r => r.id === discard.id)?.status, 'discarded');
    assert.equal((await h.api(`/api/personal/proactive/runs/${discard.id}/diff`)).status, 409);
    assert.equal((await h.api('/api/personal/proactive/runs/missing/merge', 'POST')).status, 404);
    assert.equal((await h.api('/api/personal/proactive/runs/missing/diff')).status, 404);
  } finally { await h.close(); }
});

test('merge and revert succeed on a machine with no git identity, leaving a clean worktree', async () => {
  const h = await setup(); try {
    const repo = await h.repository(); const sandbox = new ProactiveSandbox();
    // Neutralize the host's global and system git config: the sandbox must work on a blank machine.
    const blank = join(h.dir, 'blank.gitconfig'); writeFileSync(blank, '');
    const savedGlobal = process.env.GIT_CONFIG_GLOBAL, savedSystem = process.env.GIT_CONFIG_SYSTEM;
    process.env.GIT_CONFIG_GLOBAL = blank; process.env.GIT_CONFIG_SYSTEM = '/dev/null';
    try {
      const matterId = 'no-identity';
      const tree = await sandbox.prepare(repo, matterId);
      writeFileSync(join(tree.root, `${matterId}.txt`), `${matterId}\n`);
      await h.git(tree.root, 'add', '.'); await h.git(tree.root, 'commit', '-qm', `change\n\nCalli-Proactive: ${matterId}`);
      const run: ProactiveRun = { ...historical(matterId, 'sandbox'), matterId, repo, branch: tree.branch, status: 'ready' };
      h.store.saveHubSetting('personalProactiveRuns', [...h.service.runs(), run]);
      const endpoint = `/api/personal/proactive/runs/${run.id}`;
      assert.equal((await h.api(`${endpoint}/merge`, 'POST')).status, 200, 'merge brings its own identity');
      assert.equal(readFileSync(join(repo, `${matterId}.txt`), 'utf8'), `${matterId}\n`);
      const reverted = await h.api(`${endpoint}/revert`, 'POST');
      assert.equal(reverted.status, 200, 'revert brings its own identity instead of retrying a half-done one');
      assert.equal((await reverted.json()).status, 'reverted');
      assert.equal(existsSync(join(repo, `${matterId}.txt`)), false);
      assert.equal(await h.git(repo, 'status', '--porcelain'), '', 'no REVERT_HEAD or merge state is left behind');
    } finally {
      if (savedGlobal === undefined) delete process.env.GIT_CONFIG_GLOBAL; else process.env.GIT_CONFIG_GLOBAL = savedGlobal;
      if (savedSystem === undefined) delete process.env.GIT_CONFIG_SYSTEM; else process.env.GIT_CONFIG_SYSTEM = savedSystem;
    }
  } finally { await h.close(); }
});

test('startup/tick recovery fails ended or missing workers even while disabled, preserves live ones', async () => {
  const h = await setup(); try {
    const live = await h.manager.createThread({ agent: 'claude', cwd: h.cwd }); h.store.updateThread(live.id, { status: 'running' });
    const ended = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    h.store.saveHubSetting('personalProactiveRuns', [
      { ...historical('missing', 'prepare'), status: 'running' },
      { ...historical('ended', 'prepare'), status: 'running', threadId: ended.id },
      { ...historical('live', 'sandbox'), status: 'running', threadId: live.id },
    ]);
    const reborn = new ProactiveService(h.store, h.manager, h.personal, h.adapters);
    reborn.recover(now);
    assert.deepEqual(reborn.runs().map(run => run.status), ['failed', 'failed', 'running']);
    h.store.updateThread(live.id, { status: 'idle' }); await reborn.tick(now);
    assert.deepEqual(reborn.runs().map(run => run.status), ['failed', 'failed', 'failed']);
    assert.ok(reborn.runs().every(run => run.endedAt === now.toISOString()));
  } finally { await h.close(); }
});

test('history retains 200 records and the scheduler invokes proactive on its shared tick', async () => {
  const h = await setup('quiet'); try {
    h.todo();
    h.store.saveHubSetting('personalProactiveRuns', Array.from({ length: 200 }, (_, i) => historical(String(i), 'prepare', at(8, 0))));
    await h.service.save({ mode: 'auto' }); await h.service.tick(now); await h.settled(h.service.runs().at(-1)!.id);
    assert.equal(h.service.runs().length, 200); assert.equal(h.service.runs()[0].id, '1');
    const scheduler = new Scheduler(h.store, h.manager, h.adapters);
    const ticks: Date[] = []; h.service.tick = async now => { ticks.push(now!); };
    await scheduler.tick(); scheduler.dispose(); assert.equal(ticks.length, 1); assert.ok(ticks[0] instanceof Date);
  } finally { await h.close(); }
});

test('turn completion may collect files asynchronously; disabling prevents new turns and lets this one settle', async () => {
  const h = await setup(); let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; }); let collecting = false;
  try {
    h.todo();
    const commit = h.personal.commitArtifacts.bind(h.personal);
    h.personal.commitArtifacts = async id => { collecting = true; await gate; return commit(id); };
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    await wait(() => collecting);
    await h.service.tick(new Date(2026, 10, 1, 14));
    assert.equal(h.service.runs()[0].status, 'running', 'an ending being collected is not a stale run');
    assert.equal(h.service.runs().length, 1);
    await h.service.save({ mode: 'off' }); release();
    const run = await h.settled(); assert.equal(run.status, 'ready', run.summary);
    await h.service.tick(new Date(2026, 10, 1, 15)); assert.equal(h.service.runs().length, 1);
  } finally { release(); await h.close(); }
});

test('preparation failure is recorded, and activity arriving during async sandbox setup prevents a new worker', async () => {
  const h = await setup(); try {
    const repo = await h.repository(); const todo = h.todo({ dueAt: null });
    const work = await h.manager.createThread({ agent: 'claude', cwd: repo }); h.store.linkTodoThread(todo.id, work.id);
    await h.service.save({ mode: 'auto', repos: [repo] });
    const sandbox = new ProactiveSandbox();
    sandbox.prepare = async () => { throw new Error('仓库没有初始提交'); };
    const service = new ProactiveService(h.store, h.manager, h.personal, h.adapters, sandbox);
    await service.tick(now);
    assert.equal(service.runs()[0].status, 'failed'); assert.match(service.runs()[0].summary!, /初始提交/);
    assert.equal(h.store.listThreads().length, 1);
    sandbox.prepare = async () => {
      h.store.updateThread(work.id, { status: 'running' });
      return { root: repo, branch: `calli/proactive/${todo.id}` };
    };
    await service.tick(new Date(2026, 10, 1, 14));
    assert.equal(service.runs().at(-1)?.status, 'failed'); assert.match(service.runs().at(-1)!.summary!, /正在运行/);
    assert.equal(service.runs().at(-1)?.branch, `calli/proactive/${todo.id}`, 'a prepared sandbox stays discardable when its worker cannot start');
    assert.equal(h.store.listThreads().length, 1);
    h.store.updateThread(work.id, { status: 'idle' });
  } finally { await h.close(); }
});

test('counts skip discarded runs but keep failures, running, observations and outcomes', async () => {
  const h = await setup(); try {
    const ready = h.todo(), dropped = h.todo(), troubled = h.todo(), watched = h.todo();
    const run = (id: string, matterId: string, status: ProactiveRun['status']): ProactiveRun =>
      ({ id, kind: 'prepare', matterId, threadId: '', startedAt: at(9), status });
    h.store.saveHubSetting('personalProactiveRuns', [
      run('r1', ready.id, 'ready'), run('d1', dropped.id, 'discarded'), run('d2', dropped.id, 'discarded'),
      run('f1', troubled.id, 'failed'), run('f2', troubled.id, 'running'),
      run('o1', watched.id, 'observed'), run('o2', watched.id, 'approved'),
    ]);
    assert.deepEqual(h.service.counts(), { [ready.id]: 1, [troubled.id]: 2, [watched.id]: 2 }, 'observations count too: they await a verdict');
    const todos = await (await h.api('/api/todos')).json();
    const count = (id: string) => todos.find((todo: { id: string }) => todo.id === id)?.proactiveRunCount;
    assert.equal(count(ready.id), 1);
    assert.equal(count(dropped.id), 0, 'a discarded-only matter has nothing to show');
    assert.equal(count(troubled.id), 2, 'failures and a live run stay visible');
    assert.equal(count(watched.id), 2, 'observed and approved records stay visible');
  } finally { await h.close(); }
});

test('a worker thread whose turn never starts is removed when the run fails', async () => {
  const h = await setup(); try {
    const repo = await h.repository(); const todo = h.todo({ dueAt: null });
    const work = await h.manager.createThread({ agent: 'claude', cwd: repo }); h.store.linkTodoThread(todo.id, work.id);
    await h.service.save({ mode: 'auto', repos: [repo] });
    const original = h.manager.createThread.bind(h.manager);
    // Only the worker's own thread, not the archived one a pre-proposal check asks in.
    h.manager.createThread = async input => { const thread = await original(input); if (!input.scratch) h.store.updateThread(work.id, { status: 'running' }); return thread; };
    await h.service.tick(now);
    h.manager.createThread = original;
    const run = h.service.runs().at(-1)!;
    assert.equal(run.status, 'failed'); assert.match(run.summary!, /正在运行/);
    assert.ok(run.threadId, 'the run records the thread it briefly created');
    assert.equal(h.store.getThread(run.threadId), null, 'a thread whose turn never started is deleted');
    assert.deepEqual(h.store.todoThreadIds(todo.id), [work.id], 'the matter keeps only its work thread');
  } finally { await h.close(); }
});

test('sandbox decisions serialize per repository, reject running workers and preserve the state on git errors', async () => {
  const h = await setup(); let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  try {
    const repo = await h.repository(); const thread = await h.manager.createThread({ agent: 'claude', cwd: repo });
    const run: ProactiveRun = { ...historical('one', 'sandbox'), status: 'ready', repo, threadId: thread.id };
    const another: ProactiveRun = { ...historical('two', 'sandbox'), status: 'ready', repo };
    h.store.saveHubSetting('personalProactiveRuns', [run, another]);
    const sandbox = new ProactiveSandbox(); let called = 0;
    sandbox.merge = async () => { called++; await gate; return { commit: 'commit' }; };
    sandbox.discard = async () => { throw new Error('沙盒有未提交改动，请先查看'); };
    const service = new ProactiveService(h.store, h.manager, h.personal, h.adapters, sandbox);
    h.store.updateThread(thread.id, { status: 'running' });
    await assert.rejects(service.act('one', 'merge'), /仍在运行/); assert.equal(called, 0);
    h.store.updateThread(thread.id, { status: 'idle' });
    const merging = service.act('one', 'merge');
    await assert.rejects(service.act('one', 'merge'), /另一项操作/);
    await assert.rejects(service.act('two', 'discard'), /另一项操作/);
    release(); assert.equal((await merging).status, 'merged'); assert.equal(called, 1);
    await assert.rejects(service.act('two', 'discard'), /未提交改动/); assert.equal(service.runs()[1].status, 'ready');
    sandbox.discard = async () => {};
    h.store.saveHubSetting('personalProactiveRuns', [service.runs()[0], { ...another, status: 'failed' }]);
    assert.equal((await service.act('two', 'discard')).status, 'discarded');
  } finally { release(); await h.close(); }
});

test('regression: an observed first candidate does not starve the next matter', async () => {
  const h = await setup(); try {
    const one = h.todo(), two = h.todo();
    await h.service.save({ mode: 'observe' });
    await h.service.tick(now);
    await h.service.tick(new Date(now.getTime() + 2 * 3600_000));
    assert.deepEqual(new Set(h.service.runs().map(run => run.matterId)), new Set([one.id, two.id]));
    assert.equal(h.store.listThreads().length, 0);
  } finally { await h.close(); }
});

test('regression: yesterday is not an upcoming deadline, today starts at local midnight', async () => {
  const h = await setup(); try {
    const todo = h.todo();
    assert.equal(preparationCandidate({ ...todo, dueAt: at(23, 0) }, '', now), false);
    assert.equal(preparationCandidate({ ...todo, dueAt: at(0) }, '', now), true);
    assert.equal(preparationCandidate({ ...todo, dueAt: at(12, 3) }, '', now), true);
    assert.equal(preparationCandidate({ ...todo, dueAt: at(13, 3) }, '', now), false);
  } finally { await h.close(); }
});

test('advance rules distinguish transient service errors and validate model evidence', () => {
  for (const error of ['408', '429', '500', '503', '599', 'overloaded', 'rate limit', 'Connection error', 'timed out', 'ECONNRESET', 'fetch failed', 'socket hang up']) assert.equal(transientError(null, error), true, error);
  for (const error of ['401 unauthorized', '429 quota exhausted', '500 context too long', '额度耗尽 429', 'insufficient credits', 'permission denied']) assert.equal(transientError(null, error), false, error);
  assert.equal(transientError('plan_exhausted', '429'), false);
  assert.equal(parseAdvance('bad JSON', '').phase, 'unsure');
  assert.equal(parseAdvance('null', '').phase, 'unsure');
  assert.equal(parseAdvance('{"phase":"blocked","on":"user","line":"等你","question":{"text":"invented"}}', 'real').phase, 'unsure');
  const question = parseAdvance(JSON.stringify({ phase: 'blocked', on: 'user', line: '长'.repeat(60), question: { text: ' Q? ', options: ['长'.repeat(60), 'A', 'B', 'C', 'D'] } }), 'Here Q?');
  assert.equal(question.line.length, 30); assert.equal(question.question!.options.length, 4); assert.equal(question.question!.options[0].length, 40);
  for (const [input, expected] of [[1, 2], [999, 240], [10, 10]]) {
    const parsed = parseAdvance(JSON.stringify({ phase: 'blocked', on: 'external', line: '等 CI', wait: { what: 'CI', then: '验证', checkMinutes: input } }), '');
    assert.equal(parsed.checkMinutes, expected);
  }
  const prompt = advancePrompt('matter', 'n'.repeat(3000), 'request', 'x'.repeat(4000), now);
  assert.equal(/\bfail\b/.test(prompt), false);
  assert.ok(prompt.includes('可选提议不算等人')); assert.ok(prompt.length < 6500);
});

async function workTurn(h: Awaited<ReturnType<typeof setup>>, marker = 'advance-user', error?: string, extra: Record<string, unknown> = {}) {
  const todo = h.todo({ text: marker, dueAt: null });
  const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
  h.store.linkTodoThread(todo.id, thread.id);
  const turnId = `turn-${todo.id}`;
  h.store.appendEvent(thread.id, turnId, 'turn.started', { text: marker, ...extra });
  h.store.appendEvent(thread.id, turnId, 'message.delta', { text: `${marker} 请选择 A 还是 B？` });
  h.store.appendEvent(thread.id, turnId, error ? 'turn.error' : 'turn.done', error ? { message: error, ...extra } : { ...extra });
  h.store.db.prepare('UPDATE events SET ts=? WHERE thread_id=?').run(new Date(now.getTime() - 20 * 60_000).toISOString(), thread.id);
  return { todo, thread, turnId };
}

test('advance fixture classifies user, done, external, unsure and invalid output through archived askOnce', async () => {
  for (const [marker, status, reason] of [['advance-user', 'asking', 'user'], ['advance-done', 'asking', 'done'], ['advance-external', 'waiting', 'external'], ['advance-unsure', 'discarded', undefined], ['advance-invalid', 'discarded', undefined]] as const) {
    const h = await setup(); try {
      const { thread } = await workTurn(h, marker);
      await h.service.save({ mode: 'auto' }); await h.service.tick(now);
      const run = h.service.runs()[0]; assert.equal(run.status, status, run.summary); assert.equal(run.reason, reason);
      assert.equal(h.store.listTurns(thread.id).length, 1, 'classification does not send to the work thread');
      assert.equal(h.store.listThreads({ archived: true }).length, 1, 'askOnce is archived');
      assert.equal(h.store.listEventsOfType(thread.id, 'agent.notice').length, status === 'asking' ? 1 : 0);
      const summary = await (await h.api('/api/personal/proactive?summary=true')).json();
      assert.deepEqual(summary.asking, status === 'asking' ? { [run.matterId]: 1 } : {});
      await h.service.tick(new Date(now.getTime() + 60_000));
      assert.equal(h.service.runs().filter(run => run.kind === 'advance').length, 1, 'thread+turn is classified only once');
    } finally { await h.close(); }
  }
});

test('observe keeps user and done cards actionable, only observes automatic sends, and discards unsure', async () => {
  for (const [marker, error, status] of [
    ['advance-user', undefined, 'asking'], ['advance-done', undefined, 'asking'],
    ['advance-external', undefined, 'observed'], ['transient', '503 overloaded', 'observed'],
    ['advance-unsure', undefined, 'discarded'],
  ] as const) {
    const h = await setup(); try {
      const { thread, todo } = await workTurn(h, marker, error);
      const startTurn = h.manager.startTurn.bind(h.manager);
      let sends = 0;
      h.manager.startTurn = (...args) => {
        if (args[0] === thread.id) sends++;
        return startTurn(...args);
      };
      const unrelated = await h.manager.createThread({ agent: 'claude', cwd: h.cwd }); h.store.updateThread(unrelated.id, { status: 'running' });
      await h.service.save({ mode: 'observe' }); await h.service.tick(now);
      const run = h.service.runs()[0]; assert.equal(run.status, status, marker);
      assert.equal(sends, 0, 'classification never calls startTurn on the work thread');
      assert.equal(h.store.listTurns(thread.id).length, 1, 'observation never automatically continues a work thread');
      assert.equal(h.store.listEventsOfType(thread.id, 'agent.notice').length, status === 'asking' ? 1 : 0);
      assert.deepEqual(h.service.asking(), status === 'asking' ? { [todo.id]: 1 } : {});
      if (marker === 'advance-user') {
        const response = await h.api(`/api/personal/proactive/runs/${run.id}/answer`, 'POST', { answer: 'A' });
        assert.equal(response.status, 200); assert.equal((await response.json()).status, 'answered');
        assert.equal(h.store.listTurns(thread.id).length, 2, 'a person can authorize an answer while observing');
        assert.equal(sends, 1);
      } else if (marker === 'advance-done') {
        const response = await h.api(`/api/personal/proactive/runs/${run.id}/close-matter`, 'POST');
        assert.equal(response.status, 200); assert.equal(h.store.getTodo(todo.id)!.done, true);
      } else {
        await h.service.tick(new Date(now.getTime() + 20 * 60_000));
        assert.equal(h.store.listTurns(thread.id).length, 1, 'a recorded wake does not send later in observe');
        assert.equal(sends, 0, 'an observed wake never calls startTurn');
      }
      h.store.updateThread(unrelated.id, { status: 'idle' });
    } finally { await h.close(); }
  }
});

test('transient advances wait, retry without scheduled, respect two-minute spacing and stop after three attempts', async () => {
  const h = await setup(); try {
    const { todo, thread } = await workTurn(h, 'transient', '503 overloaded');
    h.manager.askOnce = async () => { throw new Error('transient should not call the model'); };
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    let run = h.service.runs()[0]; assert.equal(run.status, 'waiting'); assert.equal(run.wakeAt, new Date(now.getTime() + 60_000).toISOString());
    await h.service.tick(new Date(now.getTime() + 30_000)); assert.equal(h.store.listTurns(thread.id).length, 1);
    await h.service.tick(new Date(now.getTime() + 60_000));
    run = h.service.runs()[0]; assert.equal(run.status, 'continued'); assert.equal(run.attempts, 1);
    const started = h.store.listEventsOfType(thread.id, 'turn.started').at(-1)!;
    assert.equal(String(started.data.text).split('\n')[0], '继续'); assert.equal(started.data.scheduled, undefined); assert.equal(started.data.proactive, true);
    await wait(() => h.store.getThread(thread.id)?.status !== 'running');
    const next = 'third-error';
    h.store.appendEvent(thread.id, next, 'turn.started', { text: '继续', proactive: true });
    h.store.appendEvent(thread.id, next, 'turn.error', { message: '503 overloaded' });
    h.store.db.prepare('UPDATE events SET ts=? WHERE thread_id=? AND turn_id=?').run(new Date(now.getTime() - 20 * 60_000).toISOString(), thread.id, next);
    h.store.saveHubSetting('personalProactiveRuns', [{ ...run, attempts: 3 }]);
    await h.service.tick(new Date(now.getTime() + 3 * 60_000));
    const card = h.service.runs().at(-1)!; assert.equal(card.status, 'asking'); assert.equal(card.reason, 'transient'); assert.equal(card.matterId, todo.id);
    assert.deepEqual(card.question!.options, ['再试一次', '先放着']);
  } finally { await h.close(); }
});

test('new APIs validate missing and incompatible cards, answer normally, close and dismiss', async () => {
  const h = await setup(); try {
    const { todo, thread } = await workTurn(h, 'advance-user');
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    const run = h.service.runs()[0], endpoint = `/api/personal/proactive/runs/${run.id}`;
    for (const action of ['answer', 'close-matter', 'dismiss', 'allow-repo']) assert.equal((await h.api(`/api/personal/proactive/runs/missing/${action}`, 'POST', { answer: 'A' })).status, 404);
    assert.equal((await h.api(`${endpoint}/close-matter`, 'POST')).status, 409);
    assert.equal((await h.api(`${endpoint}/allow-repo`, 'POST')).status, 409);
    assert.equal((await h.api(`${endpoint}/answer`, 'POST', { answer: '' })).status, 400);
    h.store.updateThread(thread.id, { status: 'running' });
    assert.equal((await h.api(`${endpoint}/answer`, 'POST', { answer: 'A' })).status, 409);
    h.store.updateThread(thread.id, { status: 'idle' });
    const response = await h.api(`${endpoint}/answer`, 'POST', { answer: 'A' }); assert.equal(response.status, 200); assert.equal((await response.json()).status, 'answered');
    assert.equal(String(h.store.listEventsOfType(thread.id, 'turn.started').at(-1)!.data.text).split('\n')[0], '关于你上面的问题：请选择 A 还是 B？ → A');
    assert.equal((await h.api(`${endpoint}/answer`, 'POST', { answer: 'B' })).status, 409);
    assert.equal(h.store.getTodo(todo.id)!.done, false);
    const completed = await workTurn(h, 'advance-done');
    await h.service.tick(now); const done = h.service.runs().at(-1)!;
    assert.equal((await h.api(`/api/personal/proactive/runs/${done.id}/close-matter`, 'POST')).status, 200);
    assert.equal(h.store.getTodo(completed.todo.id)!.done, true); assert.equal(h.service.runs().at(-1)!.status, 'closed');
    const pending = await workTurn(h, 'advance-external'); await h.service.tick(now);
    const waiting = h.service.runs().at(-1)!;
    assert.equal(waiting.matterId, pending.todo.id);
    assert.equal((await h.api(`/api/personal/proactive/runs/${waiting.id}/dismiss`, 'POST')).status, 200);
    assert.equal(h.service.runs().at(-1)!.status, 'discarded');
  } finally { await h.close(); }
});

test('plans prepare plan.md once in seven days and repo cards authorize canonical roots', async () => {
  const h = await setup(); try {
    const todo = h.todo({ text: '把整个项目梳理一下', dueAt: null });
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    const plan = await h.settled(); assert.equal(plan.variant, 'plan'); assert.equal(plan.status, 'ready', plan.summary); assert.ok(plan.files?.some(file => file.path.endsWith('/plan.md')));
    assert.equal(plan.matterId, todo.id);
    const repo = await h.repository(); const nested = join(repo, 'nested'); mkdirSync(nested);
    const sandboxTodo = h.todo({ dueAt: null }); const thread = await h.manager.createThread({ agent: 'claude', cwd: nested }); h.store.linkTodoThread(sandboxTodo.id, thread.id);
    await h.service.tick(new Date(now.getTime() + 2 * 3600_000));
    const card = h.service.runs().at(-1)!; assert.equal(card.needsRepo, true); assert.equal(card.repo, repo);
    await h.service.tick(new Date(now.getTime() + 3 * 3600_000)); assert.equal(h.service.runs().length, 2, 'repo cards deduplicate for seven days');
    const allowed = await h.api(`/api/personal/proactive/runs/${card.id}/allow-repo`, 'POST'); assert.equal(allowed.status, 200); assert.equal((await allowed.json()).status, 'approved');
    assert.deepEqual(h.service.settings().repos, [repo]);
    assert.equal((await h.api(`/api/personal/proactive/runs/${card.id}/allow-repo`, 'POST')).status, 409);
  } finally { await h.close(); }
});

test('advance candidates exclude imported, archived, recent, old, busy and proactive worker turns', async () => {
  const h = await setup(); try {
    const imported = await workTurn(h, 'advance-done', undefined, { imported: true });
    const terminal = await workTurn(h, 'advance-done', undefined, { fromTerminal: true });
    const archived = await workTurn(h, 'advance-done'); h.store.updateThread(archived.thread.id, { archivedAt: now.toISOString() });
    const recent = await workTurn(h, 'advance-done'); h.store.db.prepare('UPDATE events SET ts=? WHERE thread_id=?').run(now.toISOString(), recent.thread.id);
    const old = await workTurn(h, 'advance-done'); h.store.db.prepare('UPDATE events SET ts=? WHERE thread_id=?').run(at(12, -7), old.thread.id);
    const busy = await workTurn(h, 'advance-done'); h.store.updateThread(busy.thread.id, { status: 'running' });
    const own = await workTurn(h, 'advance-done'); h.store.saveHubSetting('personalProactiveRuns', [{ ...historical('worker', 'prepare'), threadId: own.thread.id }]);
    await h.service.save({ mode: 'observe' }); await h.service.tick(now);
    assert.equal(h.service.runs().filter(run => run.kind === 'advance').length, 0);
    assert.equal(h.store.listThreads({ archived: true }).length, 1, 'no classifier was opened');
    assert.ok(imported && terminal); h.store.updateThread(busy.thread.id, { status: 'idle' });
  } finally { await h.close(); }
});

test('automatic send rechecks settings and latest turn in startTurn claim; new turns invalidate cards', async () => {
  for (const change of ['mode', 'quiet', 'turn', 'busy', 'done'] as const) {
    const h = await setup(); try {
      const { thread, todo } = await workTurn(h, 'transient', '429 rate limit');
      await h.service.save({ mode: 'auto' }); await h.service.tick(now);
      const before = h.manager.startTurn.bind(h.manager);
      h.manager.startTurn = async (...args) => {
        if (change === 'mode') await h.service.save({ mode: 'observe' });
        if (change === 'quiet') h.store.saveHubSetting('personalReview', { ...h.personal.review.status(), quietHours: { start: '11:00', end: '13:00' } });
        if (change === 'turn') { h.store.appendEvent(thread.id, 'new', 'turn.started', { text: 'new' }); h.store.appendEvent(thread.id, 'new', 'turn.done', {}); }
        if (change === 'busy') h.store.updateThread(thread.id, { status: 'running' });
        if (change === 'done') h.personal.update(todo.id, { done: true });
        return before(...args);
      };
      await h.service.tick(new Date(now.getTime() + 60_000));
      const run = h.service.runs()[0]; assert.equal(run.status, 'discarded', change); assert.ok(run.discardReason);
      assert.equal(h.store.listTurns(thread.id).length, change === 'turn' ? 2 : 1);
      h.store.updateThread(thread.id, { status: 'idle' });
    } finally { await h.close(); }
  }
  const h = await setup(); try {
    const { thread } = await workTurn(h);
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    assert.equal(h.service.runs()[0].status, 'asking');
    await h.manager.startTurn(thread.id, { text: '人自己接着说' });
    assert.equal(h.service.runs()[0].status, 'discarded'); assert.equal(h.service.runs()[0].discardReason, '线程已继续');
  } finally { await h.close(); }
});

test('advance respects model/send daily budgets, send spacing and quiet hours independently of the opening interval', async () => {
  const h = await setup(); try {
    const { thread } = await workTurn(h, 'transient', '503 overloaded');
    await h.service.save({ mode: 'auto' });
    h.store.saveHubSetting('personalProactiveRuns', [historical('just-opened', 'prepare', now.toISOString())]);
    await h.service.tick(now); const run = h.service.runs().at(-1)!; assert.equal(run.status, 'waiting', 'advance ignores the 60-minute interval');
    const day = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
    h.store.saveHubSetting('personalProactiveBudget', { day, sends: 20, models: 0, sent: {} });
    await h.service.tick(new Date(now.getTime() + 60_000)); assert.equal(h.store.listTurns(thread.id).length, 1);
    h.store.saveHubSetting('personalProactiveBudget', { day, sends: 0, models: 0, sent: { [thread.id]: now.getTime() } });
    await h.service.tick(new Date(now.getTime() + 60_000)); assert.equal(h.store.listTurns(thread.id).length, 1);
    h.store.saveHubSetting('personalReview', { ...h.personal.review.status(), quietHours: { start: '11:00', end: '13:00' } });
    await h.service.tick(new Date(now.getTime() + 2 * 60_000)); assert.equal(h.store.listTurns(thread.id).length, 1);
    h.store.saveHubSetting('personalReview', { ...h.personal.review.status(), quietHours: { start: '23:00', end: '08:00' } });
    await h.service.tick(new Date(now.getTime() + 2 * 60_000)); assert.equal(h.store.listTurns(thread.id).length, 2);
    await wait(() => h.store.getThread(thread.id)?.status !== 'running');
    await workTurn(h, 'advance-done');
    h.store.saveHubSetting('personalProactiveBudget', { day, sends: 20, models: 30, sent: {} });
    const calls = h.store.listThreads({ archived: true }).length;
    await h.service.tick(new Date(now.getTime() + 3 * 60_000));
    assert.equal(h.store.listThreads({ archived: true }).length, calls, 'model daily budget is independent');
  } finally { await h.close(); }
});

test('external wakes double to one hour, stop at six and can explicitly wait again', async () => {
  const h = await setup(); try {
    const { thread, todo } = await workTurn(h, 'advance-external', undefined, { proactive: true });
    const previous: ProactiveRun = { ...historical('previous', 'advance', at(10)), matterId: todo.id, threadId: thread.id, status: 'continued', reason: 'external', attempts: 2, wakeAt: at(10).replace(':00:00.', ':40:00.') };
    h.store.saveHubSetting('personalProactiveRuns', [previous]);
    await h.service.save({ mode: 'auto' }); await h.service.tick(now);
    let run = h.service.runs().at(-1)!; assert.equal(run.status, 'waiting'); assert.equal(run.attempts, 2); assert.equal(run.wakeAt, at(13));
    h.store.saveHubSetting('personalProactiveRuns', [{ ...previous, attempts: 6 }]);
    await h.service.tick(now); run = h.service.runs().at(-1)!; assert.equal(run.status, 'asking'); assert.deepEqual(run.question!.options, ['继续等', '不等了']);
    const response = await h.api(`/api/personal/proactive/runs/${run.id}/answer`, 'POST', { answer: '继续等' });
    assert.equal(response.status, 200); assert.equal((await response.json()).status, 'waiting'); assert.equal(h.store.listTurns(thread.id).length, 1);
    h.store.saveHubSetting('personalProactiveRuns', [{ ...run, status: 'asking' }]);
    assert.equal((await h.api(`/api/personal/proactive/runs/${run.id}/answer`, 'POST', { answer: '不等了' })).status, 200);
    assert.equal(h.service.runs()[0].status, 'discarded');
  } finally { await h.close(); }
});

test('two active proactive workers cap openings; unrelated activity permits preparation', async () => {
  const h = await setup(); try {
    h.todo(); await h.service.save({ mode: 'observe' });
    const threads = await Promise.all([h.manager.createThread({ agent: 'claude', cwd: h.cwd }), h.manager.createThread({ agent: 'claude', cwd: h.cwd })]);
    for (const thread of threads) h.store.updateThread(thread.id, { status: 'running' });
    h.store.saveHubSetting('personalProactiveRuns', threads.map((thread, i) => ({ ...historical(String(i), 'prepare'), status: 'running', threadId: thread.id })));
    await h.service.tick(now); assert.equal(h.service.runs().length, 2);
    h.store.updateThread(threads[0].id, { status: 'idle' });
    await h.service.tick(now); assert.equal(h.service.runs().length, 3, 'one unrelated active worker leaves capacity');
    h.store.updateThread(threads[1].id, { status: 'idle' });
  } finally { await h.close(); }
});

test('plans have a seven-day window and prioritize the latest unstarted matter', async () => {
  const h = await setup(); try {
    const old = h.todo({ text: '梳理旧项目的结构', dueAt: null });
    const newer = h.todo({ text: '整理新项目的方案', dueAt: null });
    h.store.db.prepare('UPDATE todos SET updated_at=? WHERE id=?').run(at(10, 0), old.id);
    await h.service.save({ mode: 'observe' }); await h.service.tick(now);
    assert.equal(h.service.runs()[0].matterId, newer.id); assert.equal(h.service.runs()[0].variant, 'plan');
    await h.service.tick(new Date(now.getTime() + 2 * 3600_000)); assert.equal(h.service.runs()[1].matterId, old.id);
    await h.service.tick(new Date(now.getTime() + 6 * 86400_000)); assert.equal(h.service.runs().length, 2);
    await h.service.tick(new Date(now.getTime() + 7 * 86400_000)); assert.equal(h.service.runs().length, 3);
  } finally { await h.close(); }
});

test('external wake sends its named condition and next step, and user retry remains a normal turn', async () => {
  for (const reason of ['external', 'transient'] as const) {
    const h = await setup(); try {
      const { thread } = await workTurn(h, reason === 'external' ? 'advance-external' : 'transient', reason === 'transient' ? '503 overloaded' : undefined);
      await h.service.save({ mode: 'auto' }); await h.service.tick(now);
      const run = h.service.runs()[0];
      if (reason === 'external') {
        await h.service.tick(new Date(now.getTime() + 10 * 60_000));
        assert.equal(h.service.runs()[0].status, 'continued');
        const text = String(h.store.listEventsOfType(thread.id, 'turn.started').at(-1)!.data.text);
        assert.match(text, /CI 结束/); assert.match(text, /检查结果/); assert.match(text, /不做其他未经人确认的对外动作/);
      } else {
        h.store.saveHubSetting('personalProactiveRuns', [{ ...run, status: 'asking', question: { text: '连续出错', options: ['再试一次', '先放着'] } }]);
        await h.service.save({ mode: 'off' });
        const response = await h.api(`/api/personal/proactive/runs/${run.id}/answer`, 'POST', { answer: '再试一次' });
        assert.equal(response.status, 200); assert.equal((await response.json()).status, 'answered');
        const start = h.store.listEventsOfType(thread.id, 'turn.started').at(-1)!;
        assert.equal(String(start.data.text).split('\n')[0], '继续'); assert.equal(start.data.scheduled, undefined); assert.equal(start.data.proactive, undefined);
      }
    } finally { await h.close(); }
  }
});

test('model result is discarded if the person continues or completes the matter during classification', async () => {
  for (const change of ['turn', 'done'] as const) {
    const h = await setup(); try {
      const { todo, thread } = await workTurn(h);
      h.manager.askOnce = async () => {
        if (change === 'done') h.personal.update(todo.id, { done: true });
        else { h.store.appendEvent(thread.id, 'new-turn', 'turn.started', { text: 'changed' }); h.store.appendEvent(thread.id, 'new-turn', 'turn.done', {}); }
        return JSON.stringify({ phase: 'done', line: '做完了' });
      };
      await h.service.save({ mode: 'auto' }); await h.service.tick(now);
      assert.equal(h.service.runs()[0].status, 'discarded'); assert.ok(h.service.runs()[0].discardReason);
      assert.equal(h.store.listEventsOfType(thread.id, 'agent.notice').length, 0);
    } finally { await h.close(); }
  }
});

test('advance starts from recent sessions, newest first, and reads the agent\'s own [进度] line before any model', async () => {
  const h = await setup(); try {
    const session = async (title: string, minutesAgo: number, stage: 'blocked' | 'review', note: string) => {
      const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd, title });
      const turnId = `turn-${thread.id}`, ended = new Date(now.getTime() - minutesAgo * 60_000).toISOString();
      h.store.appendEvent(thread.id, turnId, 'turn.started', { text: title });
      h.store.appendEvent(thread.id, turnId, 'message.delta', { text: `${note}\n[进度] ${stage === 'blocked' ? '需要你' : '待验收'}：${note}` });
      h.store.appendEvent(thread.id, turnId, 'turn.done', {});
      h.store.db.prepare('UPDATE events SET ts=? WHERE thread_id=?').run(ended, thread.id);
      h.store.setThreadProgress(thread.id, { report: { stage, note, at: ended } });
      return thread;
    };
    const old = await session('上周的会话', 4 * 24 * 60, 'blocked', '要不要合入？');
    const reviewed = await session('两小时前的会话', 120, 'review', '修复已完成');
    const newest = await session('刚才的会话', 20, 'blocked', '先部署到预发还是生产？');
    await h.service.save({ mode: 'observe' });
    await h.service.tick(now);
    const first = h.service.runs().at(-1)!;
    assert.equal(first.threadId, newest.id, 'the newest session comes first');
    assert.deepEqual([first.status, first.reason, first.matterId, first.threadTitle, first.question?.text], ['asking', 'user', '', '刚才的会话', '先部署到预发还是生产？']);
    assert.equal(h.store.listThreads({ archived: true }).length, 0, 'the [进度] line decided it; no model was asked');
    assert.deepEqual((await (await h.api('/api/personal/proactive?summary=true')).json()).asking, { '': 1 });
    await h.service.tick(new Date(now.getTime() + 60_000));
    const second = h.service.runs().at(-1)!;
    assert.equal(second.threadId, reviewed.id);
    assert.equal(second.status, 'discarded', 'a finished session with no matter raises no card');
    await h.service.tick(new Date(now.getTime() + 120_000));
    assert.equal(h.service.runs().some(run => run.threadId === old.id), false, 'sessions left alone for days are not swept up');
    const answered = await h.api(`/api/personal/proactive/runs/${first.id}/answer`, 'POST', { answer: '先到预发' });
    assert.equal(answered.status, 200); assert.equal((await answered.json()).status, 'answered');
  } finally { await h.close(); }
});

test('repo cards name the main repository for a worktree, skip the personal directory and ask once per matter', async () => {
  const h = await setup(); try {
    const repo = await h.repository();
    const tree = join(h.dir, 'tree'); await h.git(repo, 'worktree', 'add', '-q', '-b', 'side', tree);
    await h.git(h.cwd, 'init', '-q', '-b', 'main');
    const todo = h.todo({ dueAt: null });
    for (const cwd of [h.cwd, tree, repo]) h.store.linkTodoThread(todo.id, (await h.manager.createThread({ agent: 'claude', cwd })).id);
    h.store.saveHubSetting('personalProactiveRuns', [{ ...historical('plan', 'prepare', now.toISOString()), matterId: todo.id, status: 'ready' }]);
    await h.service.save({ mode: 'observe' });
    for (let i = 0; i < 4; i++) await h.service.tick(new Date(now.getTime() + i * 60_000));
    const cards = h.service.runs().filter(run => run.needsRepo);
    assert.deepEqual(cards.map(card => card.repo), [repo]);
  } finally { await h.close(); }
});

test('before proposing, it checks recent sessions: done offers to close the matter, doing stays quiet, invented evidence does not count', async () => {
  for (const [text, expected] of [['precheck-done 给看板加团队', 'done'], ['precheck-doing 给看板加团队', 'doing'], ['precheck-bogus 给看板加团队', 'todo'], ['precheck-none 给看板加团队', 'todo']] as const) {
    const h = await setup(); try {
      await h.manager.createThread({ agent: 'claude', cwd: h.cwd, title: 'precheck-evidence' });
      const todo = h.todo({ text, dueAt: null });
      await h.service.save({ mode: 'observe' }); await h.service.tick(now);
      const check = h.service.runs().find(run => run.precheck);
      if (expected === 'todo') assert.ok(!check && h.store.hubSetting<Record<string, string>>('personalProactivePrechecks', {})[todo.id], 'nothing found is remembered, not recorded as a run');
      else assert.equal(check?.precheck, expected, text);
      const plans = () => h.service.runs().filter(run => run.kind === 'prepare' && run.variant === 'plan');
      if (expected === 'done' && check) {
        assert.deepEqual([check.status, check.reason, check.matterId], ['asking', 'done', todo.id]);
        assert.match(check.summary!, /看起来已经做过了/);
        await h.service.tick(new Date(now.getTime() + 2 * 3600_000));
        assert.equal(plans().length, 0, 'no breakdown is proposed for work already done');
        // "Not done yet" lets the proposal through, without asking again.
        assert.equal((await h.api(`/api/personal/proactive/runs/${check.id}/dismiss`, 'POST')).status, 200);
        await h.service.tick(new Date(now.getTime() + 4 * 3600_000));
        assert.equal(plans().length, 1);
        assert.equal(h.service.runs().filter(run => run.precheck).length, 1, 'checked once a week');
      } else if (expected === 'doing') {
        assert.equal(check?.status, 'discarded');
        await h.service.tick(new Date(now.getTime() + 2 * 3600_000));
        assert.equal(plans().length, 0, 'work under way elsewhere is left alone');
      } else {
        assert.equal(plans().length, 1, 'no evidence: the proposal goes ahead');
      }
    } finally { await h.close(); }
  }
});

test('a precheck done card closes the matter without any work turn', async () => {
  const h = await setup(); try {
    await h.manager.createThread({ agent: 'claude', cwd: h.cwd, title: 'precheck-evidence' });
    const todo = h.todo({ text: 'precheck-done 收尾', dueAt: null });
    await h.service.save({ mode: 'observe' }); await h.service.tick(now);
    const check = h.service.runs().find(run => run.precheck)!;
    const closed = await h.api(`/api/personal/proactive/runs/${check.id}/close-matter`, 'POST');
    assert.equal(closed.status, 200); assert.equal((await closed.json()).status, 'closed');
    assert.equal(h.store.getTodo(todo.id)?.done, true);
  } finally { await h.close(); }
});
