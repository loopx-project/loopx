import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';
import { ZcodeAdapter } from '../src/adapters/zcode.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { AgentRegistry } from '../src/agents.js';
import type { AgentAdapter, TurnHooks, UserInputRequest } from '../src/adapters/types.js';
import type { EmittedEvent } from '../src/events.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { UserInputs } from '../src/user-input.js';
const here = dirname(fileURLToPath(import.meta.url));
const fixture = (name: string) => [process.execPath, join(here, 'fixtures', `fake-${name}.mjs`)];
const adapters = () => [new CodexAppServerAdapter(fixture('codex')), new TraexAcpAdapter(fixture('acp')), new ClaudeAcpAdapter(fixture('acp')), new ZcodeAdapter(fixture('zcode'), { ZCODE_DATA_BASE_DIR: here }), new AgyAdapter(fixture('agy'))];
const input = (text: string) => ({ text, images: [] });
const hooks = (events: EmittedEvent[]): TurnHooks => ({ emit: e => events.push(e), requestApproval: async () => ({ cancelled: true }) });

for (const adapter of adapters()) {
  test(`${adapter.id}: native planning preserves the session, model and execution permissions`, { timeout: 10000 }, async () => {
    const s = await adapter.start({ cwd: here, workMode: 'plan', permissionMode: 'full_access' });
    const id = s.agentSessionId;
    const events: EmittedEvent[] = [];
    try {
      await s.setPermissionMode?.('full_access');
      assert.equal((await s.prompt(input('plan-draft'), hooks(events))).stopReason, 'end_turn');
      // agy has no plan of its own, so what it answers in plan mode comes back as the plan.
      assert.match(events.filter(e => e.type === 'message.delta' || e.type === 'plan.completed').map(e => e.data.text).join(''), /mode:plan/);
      if (adapter.id === 'agy') assert.ok(events.some(e => e.type === 'plan.completed'));
      await s.setWorkMode!('execute');
      events.length = 0;
      assert.equal((await s.prompt(input('plan-draft'), hooks(events))).stopReason, 'end_turn');
      const text = events.filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
      assert.doesNotMatch(text, /mode:plan/);
      assert.equal(s.agentSessionId, id);
      if (['codex', 'traex'].includes(adapter.id)) { assert.match(text, /approval:never/); assert.match(text, /model:gpt-big/); }
      if (adapter.id === 'claude') assert.match(text, /mode:bypassPermissions/);
      if (adapter.id === 'agy') assert.match(text, /mode:always-proceed/);
    } finally { await s.close(); }
  });
  if (adapter.id === 'agy') continue;
  test(`${adapter.id}: native questions preserve answer field identities`, { timeout: 10000 }, async () => {
    const s = await adapter.start({ cwd: here, workMode: 'plan' });
    const events: EmittedEvent[] = [];
    let asked = 0;
    try {
      const result = await s.prompt(input('plan-question'), { ...hooks(events), requestInput: async r => {
        asked++; assert.match(r.questions[0].question, /支持哪一端|范围/);
        return { answers: Object.fromEntries(r.questions.map(q => [q.id, q.options.length ? [q.options[0].value] : ['补充测试']])) };
      } });
      assert.equal(result.stopReason, 'end_turn'); assert.equal(asked, 1);
      const text = events.filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
      assert.match(text, /桌面和手机/);
      if (['codex', 'traex'].includes(adapter.id)) assert.ok(events.some(e => e.type === 'plan.completed' && String(e.data.text).includes('完成改动')));
    } finally { await s.close(); }
  });
}

for (const adapter of adapters().filter(a => ['claude', 'zcode'].includes(a.id))) {
  test(`${adapter.id}: native plan approval updates the UI mode without another prompt`, { timeout: 10000 }, async () => {
    const changes: string[] = [];
    const s = await adapter.start({ cwd: here, workMode: 'plan', permissionMode: 'full_access', onWorkMode: m => changes.push(m) });
    let carried: object | undefined;
    try {
      const result = await s.prompt(input('plan-approval'), { ...hooks([]), requestInput: async r => {
        assert.equal(r.kind, 'plan'); assert.match(r.plan!, /实施计划/);
        // Full access answers Claude's plan gate itself, so the request arrives with the decision
        // attached; zcode has no permission levels of its own and keeps asking for a person.
        carried = r.auto;
        return r.auto ?? { answers: { [r.questions[0].id]: [r.questions[0].options[0].value] } };
      } });
      assert.equal(result.stopReason, 'end_turn'); assert.ok(changes.includes('execute'));
      if (adapter.id === 'claude') assert.deepEqual(carried, { answers: { approval: ['exit-plan-bypass'] } });
      else assert.equal(carried, undefined);
    } finally { await s.close(); }
  });
}

test('claude: a full-access plan gate is recorded and resolved in one event, never asked', { timeout: 10000 }, async () => {
  const events: EmittedEvent[] = [];
  const pending = new UserInputs((_thread, _turn, type, data) => events.push({ type, data }));
  const s = await new ClaudeAcpAdapter(fixture('acp')).start({ cwd: here, workMode: 'plan', permissionMode: 'full_access' });
  try {
    const result = await s.prompt(input('plan-approval'), { ...hooks(events), requestInput: r => pending.request('thread', 'turn', r), cancelInput: id => pending.cancel('thread', id) });
    assert.equal(result.stopReason, 'end_turn');
    // The request says both what was planned and that the decision is already made; nothing waits.
    const request = events.find(e => e.type === 'input.request');
    assert.ok(request);
    assert.equal(request!.data.kind, 'plan'); assert.match(String(request!.data.plan), /实施计划/);
    assert.deepEqual(request!.data.auto, { answers: { approval: ['exit-plan-bypass'] } });
    assert.equal(events.filter(e => e.type === 'input.resolved').length, 0);
    assert.equal(pending.waiting('thread'), false);
    assert.match(events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), /mode:bypassPermissions/);
  } finally { await s.close(); }
});

test('older Codex rejects plan mode visibly and keeps ordinary turns usable', async () => {
  const a = new CodexAppServerAdapter(fixture('codex'), { FAKE_CODEX_NO_PLAN: '1' });
  await assert.rejects(a.start({ cwd: here, workMode: 'plan' }), /不支持原生计划/);
  const s = await a.start({ cwd: here, workMode: 'execute' });
  try { assert.equal((await s.prompt(input('hello'), hooks([]))).stopReason, 'end_turn'); } finally { await s.close(); }
});

test('native Codex question expiry releases the pending input', { timeout: 10000 }, async () => {
  const events: any[] = [];
  const pending = new UserInputs((_thread, _turn, type, data) => events.push({ type, data }));
  const s = await adapters()[0].start({ cwd: here, workMode: 'plan' });
  try {
    await s.prompt(input('plan-question-expired'), { ...hooks([]), requestInput: r => pending.request('thread', 'turn', r), cancelInput: id => pending.cancel('thread', id) });
    assert.equal(events.filter(e => e.type === 'input.request').length, 1);
    assert.equal(events.filter(e => e.type === 'input.resolved').length, 1);
    assert.equal(pending.waiting('thread'), false);
  } finally { await s.close(); }
});

test('zcode cannot resume an active goal in plan mode', async () => {
  const adapter = new ZcodeAdapter(fixture('zcode'), { ZCODE_DATA_BASE_DIR: here, FAKE_ZCODE_GOAL: '1' });
  await assert.rejects(adapter.start({ cwd: here, resumeId: 'saved-goal', workMode: 'plan' }), /先暂停当前目标/);
});

test('queued planning messages retain their mode after native approval starts execution', { timeout: 10000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-plan-queue-'));
  const store = new Store(dir);
  const adapter = new ZcodeAdapter(fixture('zcode'), { ZCODE_DATA_BASE_DIR: here });
  const manager = new ThreadManager(store, new Map([['zcode', adapter]]), [dir], new AttachmentStore(dir));
  const waitFor = async (f: () => boolean) => { for (let i = 0; i < 200; i++) { if (f()) return; await new Promise(r => setTimeout(r, 20)); } assert.fail('condition timed out'); };
  try {
    const thread = await manager.createThread({ agent: 'zcode', cwd: dir, workMode: 'plan' });
    await manager.startTurn(thread.id, { text: 'plan-approval' });
    await waitFor(() => manager.threadView(thread.id).waiting);
    assert.equal(manager.threadView(thread.id).waitingReason, 'plan');
    await manager.startTurn(thread.id, { text: 'plan-draft', queue: true });
    const request = store.listEvents(thread.id).find(e => e.type === 'input.request')!;
    const question = (request.data.questions as UserInputRequest['questions'])[0];
    manager.inputs.answer(String(request.data.inputId), { answers: { [question.id]: [question.options[0].value] } });
    await waitFor(() => store.listEvents(thread.id).filter(e => e.type === 'turn.done').length === 2);
    assert.equal(store.getThread(thread.id)?.workMode, 'plan');
    const events = store.listEvents(thread.id);
    assert.ok(events.filter(e => e.type === 'turn.started').every(e => e.data.workMode === 'plan'));
    assert.ok(events.some(e => e.type === 'message.delta' && String(e.data.text).includes('mode:plan')));
  } finally { await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('input reannouncements, validation, cancellation and duplicate responses', async () => {
  const events: any[] = [];
  const pending = new UserInputs((_thread, _turn, type, data) => events.push({ type, data }));
  const req: UserInputRequest = { id: 'native', title: 'Question', questions: [{ id: 'q', question: 'Pick', options: [{ value: 'one', label: 'One' }] }] };
  const first = pending.request('thread', 'turn', req);
  assert.equal(pending.request('thread', 'turn', req), first);
  const id = events[0].data.inputId;
  assert.throws(() => pending.answer(id, { answers: { q: ['invented'] } }), /选项/);
  assert.throws(() => pending.answer(id, { answers: { other: ['one'] } }), /不存在/);
  assert.throws(() => pending.answer(id, { answers: { q: ['one', 'one'] } }), /一个/);
  assert.equal(pending.answer(id, { answers: { q: ['one'] } }), true);
  assert.equal(pending.answer(id, { answers: { q: ['one'] } }), false);
  assert.deepEqual((await first as any).answers.q, ['one']);
  const next = pending.request('thread', 'turn2', req);
  pending.cancel('thread', 'native'); assert.deepEqual(await next, { cancelled: true });
});

test('HTTP mode and input lifecycle: persistence, invalid modes, busy changes, reload and cancellation', { timeout: 15000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-plan-'));
  const store = new Store(dir);
  const registry = new AgentRegistry([{ id: 'codex', adapter: 'codex', command: fixture('codex') }, { id: 'pi', adapter: 'pi', command: fixture('pi') }], null);
  const manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
  const token = 'plan-test-token';
  const server = createHttpServer({ manager, registry, token, webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const call = (path: string, method = 'GET', body?: unknown, auth = true) => fetch(base + path, { method, headers: { ...(auth ? { Authorization: `Bearer ${token}` } : {}), 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const waitFor = async (f: () => boolean) => { for (let i = 0; i < 200; i++) { if (f()) return; await new Promise(r => setTimeout(r, 20)); } assert.fail('condition timed out'); };
  try {
    // pi's answer in plan mode is the plan; once it is in, the thread is back in execute mode and
    // the next turn tells pi the planning is over.
    const piCreated = await call('/api/threads', 'POST', { agent: 'pi', cwd: dir, workMode: 'plan' });
    assert.equal(piCreated.status, 201);
    const pi = await piCreated.json() as any;
    assert.equal((await call(`/api/threads/${pi.id}/turns`, 'POST', { text: '设计登录页' })).status, 202);
    await waitFor(() => store.getThread(pi.id)?.workMode === 'execute' && manager.threadView(pi.id).status !== 'running');
    const piEvents = await (await call(`/api/threads/${pi.id}/events`)).json() as any[];
    assert.ok(piEvents.some(e => e.type === 'plan.completed' && /设计方案/.test(e.data.text)));
    assert.ok(!piEvents.some(e => e.type === 'message.delta' && /设计方案/.test(e.data.text)));
    assert.ok(piEvents.some(e => e.type === 'thread.updated'));
    assert.equal((await call(`/api/threads/${pi.id}/turns`, 'POST', { text: 'inspect-work-mode' })).status, 202);
    await waitFor(() => manager.threadView(pi.id).status !== 'running');
    const after = (await (await call(`/api/threads/${pi.id}/events`)).json() as any[]).filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
    assert.match(after, /Hub work mode: execute/);
    // Sending with workMode switches the thread for that turn: how a plan is sent back for changes.
    assert.equal((await call(`/api/threads/${pi.id}/turns`, 'POST', { text: 'inspect-work-mode', workMode: 'plan' })).status, 202);
    await waitFor(() => manager.threadView(pi.id).status !== 'running');
    const replanned = await (await call(`/api/threads/${pi.id}/events`)).json() as any[];
    assert.equal((replanned.filter(e => e.type === 'turn.started').at(-1) as any).data.workMode, 'plan');
    assert.equal((await call(`/api/threads/${pi.id}/turns`, 'POST', { text: 'bad mode', workMode: 'invalid' })).status, 400);
    assert.equal((await call(`/api/threads/${pi.id}`, 'PATCH', { workMode: 'execute' })).status, 200);
    const created = await call('/api/threads', 'POST', { agent: 'codex', cwd: dir, workMode: 'plan', permissionMode: 'full_access' });
    assert.equal(created.status, 201);
    const th = await created.json() as any;
    assert.equal(th.workMode, 'plan'); assert.equal(store.getThread(th.id)?.workMode, 'plan');
    assert.equal((await call(`/api/threads/${th.id}`, 'PATCH', { workMode: 'invalid' })).status, 400);
    const reopened = new Store(dir); assert.equal(reopened.getThread(th.id)?.workMode, 'plan'); reopened.close();
    for (const cancel of [false, true]) {
      if (store.getThread(th.id)?.workMode !== 'plan') assert.equal((await call(`/api/threads/${th.id}`, 'PATCH', { workMode: 'plan' })).status, 200);
      assert.equal((await call(`/api/threads/${th.id}/turns`, 'POST', { text: 'plan-question' })).status, 202);
      await waitFor(() => manager.threadView(th.id).waiting);
      assert.equal((await (await call(`/api/threads/${th.id}`)).json() as any).waitingReason, 'question');
      const list = await (await call('/api/threads')).json() as any[];
      assert.equal(list.find(t => t.id === th.id).waitingReason, 'question');
      const events = await (await call(`/api/threads/${th.id}/events`)).json() as any[];
      const question = events.filter(e => e.type === 'input.request').at(-1);
      const route = `/api/inputs/${question.data.inputId}`;
      assert.equal((await call(`/api/threads/${th.id}`, 'PATCH', { workMode: 'execute' })).status, 409);
      assert.equal((await call(route, 'POST', { answers: { scope: ['桌面和手机'] } }, false)).status, 401);
      if (cancel) await call(`/api/threads/${th.id}/cancel`, 'POST', {});
      else assert.equal((await call(route, 'POST', { answers: { scope: ['桌面和手机'] } })).status, 204);
      await waitFor(() => manager.threadView(th.id).status !== 'running');
      assert.equal((await call(route, 'POST', { answers: { scope: ['桌面和手机'] } })).status, 404);
      assert.equal(manager.threadView(th.id).waiting, false);
      assert.equal(manager.threadView(th.id).waitingReason, null);
      // A finished plan ends plan mode; a stopped one does not.
      assert.equal(store.getThread(th.id)?.workMode, cancel ? 'plan' : 'execute');
    }
    assert.equal((await call(`/api/threads/${th.id}`, 'PATCH', { workMode: 'execute' })).status, 200);
    assert.equal(store.getThread(th.id)?.permissionMode, 'full_access');
  } finally { await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('pending input reasons follow the oldest request and clear on cancellation', async () => {
  const events: any[] = [];
  const pending = new UserInputs((_thread, _turn, type, data) => events.push({ type, data }));
  const request: UserInputRequest = { title: 'Question', questions: [] };
  const question = pending.request('thread', 'turn', { ...request, id: 'question' });
  const plan = pending.request('thread', 'turn', { ...request, id: 'plan', kind: 'plan' });
  assert.equal(pending.waitingReason('thread'), 'question');
  assert.equal(pending.waitingReason('other'), null);
  pending.cancel('thread', 'question');
  await question;
  assert.equal(pending.waitingReason('thread'), 'plan');
  pending.cancel('thread');
  await plan;
  assert.equal(pending.waitingReason('thread'), null);
  assert.equal(pending.waiting('thread'), false);
  // A turn running out under a question says so in the log, apart from the user skipping it.
  const ended = pending.request('thread', 'turn2', { ...request, id: 'question' });
  pending.cancel('thread', undefined, true);
  assert.deepEqual(await ended, { cancelled: true });
  const last = events.filter(e => e.type === 'input.resolved').at(-1);
  assert.equal(last.data.reason, 'ended');
  const skipped = pending.request('thread', 'turn3', { ...request, id: 'question' });
  pending.cancel('thread');
  await skipped;
  assert.equal(events.filter(e => e.type === 'input.resolved').at(-1).data.reason, undefined);
});
