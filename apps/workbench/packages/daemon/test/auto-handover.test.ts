import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';
import type { AgentAdapter, PlanQuota } from '../src/adapters/types.js';
import { CoordinationManager } from '../src/coordination.js';
import { createHttpServer } from '../src/http.js';
import { freshQuota, modelWindows } from '../src/auto-handover.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const wait = async (check: () => unknown, timeout = 8000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};
async function setup(summaryFail = false, hidden?: string) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-auto-test-'));
  const store = new Store(dir);
  const adapters = new Map<string, AgentAdapter>();
  for (const id of ['agy', 'agy2', 'agy3']) {
    writeFileSync(join(dir, id + '.json'), JSON.stringify({ 'Gemini Models': 10, 'Claude Models': 20 }));
    adapters.set(id, new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], {
      FAKE_HANDOVER_QUOTA: join(dir, id + '.json'), ...(summaryFail && id === 'agy' ? { FAKE_HANDOVER_SUMMARY_FAIL: '1' } : {}),
    }));
  }
  adapters.set('offline', new AgyAdapter(['nonexistent-handover-fixture']));
  // A healthy instance of a different product: it passes every availability and quota check, so
  // only the same-adapter default can keep an automatic handover from crossing products.
  writeFileSync(join(dir, 'other.json'), JSON.stringify({ 'Gemini Models': 10 }));
  adapters.set('other', new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], { FAKE_HANDOVER_QUOTA: join(dir, 'other.json') }));
  // A bridge to a third-party model: the same adapter, healthy, but with no plan to read usage from.
  writeFileSync(join(dir, 'bridge.json'), '{}');
  adapters.set('bridge', new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], { FAKE_HANDOVER_QUOTA: join(dir, 'bridge.json') }));
  const kinds: Record<string, string> = { other: 'traex' };
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: kinds[id] ?? 'agy', ...(id === hidden ? { hidden: true } : {}) })));
  Object.defineProperty(manager.autoHandover, 'delayMs', { value: 80 });
  const team = new CoordinationManager(store, manager, join(dir, 'calli.sock'));
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + path, { method, headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const thread = await manager.createThread({ agent: 'agy', cwd: dir, model: 'gemini-pro' });
  const config = (targets = [{ agent: 'agy2', model: 'gemini-pro' }]) => manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: targets } });
  // A coordination reply can start the next turn right after the current one ends, in the gap
  // between the status cleanup and the queued turn's own running flag. Settling means the
  // not-running observation survives a beat. Turns that stay queued without starting are fine:
  // they do not make a takeover busy.
  const done = async (id = thread.id) => {
    for (;;) {
      await wait(() => manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id));
      await new Promise(r => setTimeout(r, 50));
      if (manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id)) return;
    }
  };
  return { dir, store, manager, team, thread, api, config, done, adapters,
    async close() { team.dispose(); await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('quota windows require fresh data and explicit model coverage', () => {
  const q = { checkedAt: new Date().toISOString(), windows: [{ label: '7D', scope: 'unknown', usedPercent: 99 }, { label: '5H', scope: 'Gemini', modelIds: ['gemini'], usedPercent: 95 }] };
  assert.equal(modelWindows(q, 'claude').length, 0);
  assert.equal(modelWindows(q, 'gemini').length, 1);
  assert.equal(freshQuota(q), true);
  assert.equal(freshQuota({ ...q, checkedAt: '2000-01-01' }), false);
  assert.equal(freshQuota({ ...q, checkedAt: 'unknown' }), false);
});

test('a hidden instance is never an automatic handover target', async () => {
  const h = await setup(false, 'agy2');
  try {
    h.config([{ agent: 'agy2' }, { agent: 'agy3' }]);
    const targets = await h.manager.autoHandover.targets(h.thread.id);
    assert.ok(targets.length > 0);
    assert.ok(targets.every(t => t.agent !== 'agy2'));
  } finally { await h.close(); }
});

test('settings API validates and persists machine config; thread overrides are strict', async () => {
  const h = await setup();
  try {
    assert.equal((await (await h.api('/api/handover-settings')).json()).enabled, false);
    const settings = { enabled: true, threshold: 93, orders: { agy: [{ agent: 'agy2', model: 'gemini-pro' }] } };
    assert.equal((await h.api('/api/handover-settings', 'PUT', settings)).status, 200);
    assert.deepEqual(h.store.hubSetting('handover.settings', null), { ...settings, resume: false, method: 'resume' });
    assert.equal((await h.api('/api/handover-settings', 'PUT', { ...settings, resume: 'yes' })).status, 400);
    for (const bad of [{ ...settings, threshold: 100 }, { ...settings, enabled: 'true' }, { ...settings, orders: { agy: [{ agent: 'missing' }] } }]) assert.equal((await h.api('/api/handover-settings', 'PUT', bad)).status, 400);
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { enabled: 'false' })).status, 400);
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { enabled: false })).status, 200);
    const result = await (await h.api(`/api/threads/${h.thread.id}/auto-handover`)).json();
    assert.equal(result.enabled, false); assert.equal(result.source, 'thread'); assert.equal(result.targets[0].agent, 'agy2');
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'DELETE')).status, 200);
  } finally { await h.close(); }
});

test('fresh near-quota -> agent summary -> countdown -> successor; queued work follows it', async () => {
  const h = await setup();
  try {
    h.config();
    // The summary path: the brief is written even for another account of the same agent.
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: [{ agent: 'agy2', model: 'gemini-pro' }] }, method: 'summary' });
    await h.manager.agentQuota('agy'); // The fresh turn must not use this 10% cached value.
    const seen: string[] = [];
    const off = h.manager.subscribe(() => {
      const v = h.manager.autoHandover.view(h.thread.id);
      const mark = v.state ? `${v.state}${v.step ? `:${v.step}` : ''}` : '';
      if (mark && seen.at(-1) !== mark) seen.push(mark);
    });
    await h.manager.startTurn(h.thread.id, { text: 'quota-near' });
    await h.manager.startTurn(h.thread.id, { text: 'queued followup', queue: true });
    await wait(() => h.manager.autoHandover.view(h.thread.id).state === 'countdown');
    const events = h.store.listEvents(h.thread.id, 0, -1);
    assert.equal(events.filter(e => e.type === 'turn.started').length, 2);
    assert.ok(events.find(e => e.type === 'turn.started' && e.data.handover));
    await h.done();
    off();
    // The person is told which step it is on; once the move itself has begun it cannot be called off.
    assert.deepEqual(seen.filter(m => m !== 'checking'), ['preparing', 'preparing:summary', 'countdown', 'preparing:handover']);
    const next = h.manager.listThreads().find(t => t.handover?.sourceId === h.thread.id)!;
    assert.ok(next); assert.equal(next.handover?.mode, 'continue'); await h.done(next.id);
    assert.equal(next.agent, 'agy2'); assert.equal(next.model, 'gemini-pro');
    const starts = h.store.listEvents(next.id, 0, -1).filter(e => e.type === 'turn.started');
    assert.match(String(starts[0].data.text), /请写一份交接摘要/);
    assert.equal(starts[1].data.displayText ?? starts[1].data.text, 'queued followup');
  } finally { await h.close(); }
});

test('same instance changes model in the same native conversation without a summary', async () => {
  const h = await setup();
  try {
    h.config([{ agent: 'agy', model: 'claude-sonnet' }]);
    await h.manager.startTurn(h.thread.id, { text: 'quota-near' }); await h.done();
    assert.equal(h.manager.listThreads().length, 1);
    assert.equal(h.manager.getThread(h.thread.id).model, 'claude-sonnet');
    const session = h.manager.getThread(h.thread.id).agentSessionId;
    assert.equal(h.store.listEvents(h.thread.id, 0, -1).filter(e => e.type === 'turn.started').length, 1);
    assert.ok(h.store.listEvents(h.thread.id, 0, -1).find(e => e.type === 'handover.model'));
    await h.manager.startTurn(h.thread.id, { text: 'continue' }); await h.done();
    assert.equal(h.manager.getThread(h.thread.id).agentSessionId, session);
    assert.match(h.store.listEvents(h.thread.id, 0, -1).filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), /claude-sonnet/);
  } finally { await h.close(); }
});

test('exhausted fallback skips unavailable and near-quota targets and forwards the failed prompt', async () => {
  const h = await setup();
  try {
    h.manager.personal.putSettings({ directory: h.dir, agent: 'agy', model: 'gemini-pro' });
    const todo = h.manager.personal.create({ text: 'Continue across quota exhaustion', owner: 'agent' }, h.thread.id);
    h.manager.personal.writeNote(todo.id, { rev: todo.rev, text: 'Persistent continuation evidence' }, h.thread.id);
    writeFileSync(join(h.dir, 'agy2.json'), JSON.stringify({ 'Gemini Models': 95 }));
    h.config([{ agent: 'offline', model: 'gemini-pro' }, { agent: 'agy2', model: 'gemini-pro' }, { agent: 'agy3', model: 'gemini-pro' }]);
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' }); await h.done();
    assert.equal(h.store.listEvents(h.thread.id, 0, -1).filter(e => e.type === 'turn.started').length, 1);
    const next = h.manager.listThreads().find(t => t.handover?.sourceId === h.thread.id)!;
    assert.equal(h.store.todoThreadIds(todo.id)[0], next.id);
    assert.equal(next.agent, 'agy3'); await h.done(next.id);
    const start = h.store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')!;
    assert.match(String(start.data.text), /Persistent continuation evidence/);
    assert.match(String(start.data.text), /hub 从记录整理/); assert.match(String(start.data.text), /继续未完成的这一轮\n\nquota-exhausted/);
  } finally { await h.close(); }
});

test('cancel suppresses this quota episode, and summary failure uses the log fallback', async () => {
  const h = await setup(true);
  try {
    h.config(); await h.manager.startTurn(h.thread.id, { text: 'quota-near' });
    await wait(() => h.manager.autoHandover.view(h.thread.id).state === 'countdown');
    await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'DELETE'); await h.done();
    assert.equal(h.manager.listThreads().length, 1);
    await h.manager.startTurn(h.thread.id, { text: 'same episode' }); await h.done();
    assert.equal(h.manager.listThreads().length, 1);
    // Observed recovery rearms this model; a later near-quota episode can hand over.
    writeFileSync(join(h.dir, 'agy.json'), JSON.stringify({ 'Gemini Models': 10 }));
    await h.manager.startTurn(h.thread.id, { text: 'recovered' }); await h.done();
    await h.manager.startTurn(h.thread.id, { text: 'quota-near' }); await h.done();
    const next = h.manager.listThreads().find(t => t.handover?.sourceId === h.thread.id)!; assert.ok(next); await h.done(next.id);
    assert.match(String(h.store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')?.data.text), /hub 从记录整理/);
  } finally { await h.close(); }
});

test('collaboration default, collaboration override, and conversation override are independent of global', async () => {
  const h = await setup();
  try {
    await h.team.enable(h.thread.id);
    const job = await h.team.command(h.thread.id, { op: 'spawn', name: 'worker', agent: 'agy2', model: 'gemini-pro', text: 'hello', requestId: 'spawn' }) as { to: string };
    await h.done(job.to);
    assert.equal(h.manager.autoHandover.view(job.to).enabled, true);
    assert.equal(h.manager.autoHandover.view(h.thread.id).enabled, false);
    assert.equal((await h.api(`/api/threads/${h.thread.id}/collaboration-handover`, 'PATCH', { enabled: false })).status, 200);
    assert.equal(h.manager.autoHandover.view(job.to).enabled, false);
    h.manager.autoHandover.setOverride(job.to, true);
    assert.equal(h.manager.autoHandover.view(job.to).source, 'thread'); assert.equal(h.manager.autoHandover.view(job.to).enabled, true);
    h.manager.autoHandover.setOverride(job.to, null);
    assert.equal(h.manager.autoHandover.view(job.to).enabled, false);
  } finally { await h.close(); }
});

test('member handover preserves its name and routes lead messages and results to its successor', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: false, threshold: 90, orders: { agy: [{ agent: 'agy2', model: 'gemini-pro' }] } });
    const root = await h.manager.createThread({ agent: 'agy3', cwd: h.dir, model: 'gemini-pro' });
    await h.team.enable(root.id);
    const job = await h.team.command(root.id, { op: 'spawn', name: 'worker', agent: 'agy', model: 'gemini-pro', text: 'quota-near', requestId: 'spawn' }) as { to: string };
    const sourceId = job.to;
    await wait(() => h.team.view(root.id)?.members[0].id !== sourceId);
    const next = h.team.view(root.id)!.members[0]; assert.equal(next.name, 'worker'); assert.equal(next.thread?.agent, 'agy2'); await h.done(next.id);
    await h.team.command(root.id, { op: 'send', target: 'worker', text: 'after handover', requestId: 'followup' });
    await wait(() => h.store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.started' && String(e.data.text).includes('after handover')));
    await h.done(next.id);
    assert.ok(h.team.view(root.id)!.messages.some(d => d.text.includes('成员 worker 已从 agy 交接给 agy2')));
    assert.ok(h.team.view(root.id)!.messages.some(d => d.from === next.id && d.kind === 'result'));
    assert.equal(h.team.view(root.id)!.members.length, 1);
    assert.equal(h.manager.threadView(sourceId).coordination?.retired, true);
    assert.equal(h.manager.threadView(next.id).coordination?.retired, undefined);
    const detached = await h.api(`/api/threads/${sourceId}/handover-link`, 'DELETE');
    assert.equal(detached.status, 200);
    assert.deepEqual((await detached.json() as any).detached, [next.id]);
    assert.equal(h.manager.threadView(sourceId).coordination, undefined, 'the old session leaves collaboration');
    assert.equal(h.manager.threadView(next.id).coordination?.retired, undefined, 'the successor keeps the member identity');
    assert.equal(h.team.view(root.id)!.members[0].id, next.id);
    assert.equal(h.manager.autoHandover.view(sourceId).override, false);
    assert.equal(h.manager.autoHandover.view(next.id).override, null);
    await h.team.command(root.id, { op: 'send', target: 'worker', text: 'after split', requestId: 'after-split' });
    await wait(() => h.store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.started' && String(e.data.text).includes('after split')));
    assert.ok(!h.store.listEvents(sourceId, 0, -1).some(e => e.type === 'turn.started' && String(e.data.text).includes('after split')));
    await h.manager.startTurn(sourceId, { text: 'old session is independent' }); await h.done(sourceId);
    assert.ok(h.store.listEvents(sourceId, 0, -1).some(e => e.type === 'turn.started' && e.data.text === 'old session is independent'));
  } finally { await h.close(); }
});

test('only known plan-limit messages classify as exhaustion', () => {
  const agy = new AgyAdapter(); const trae = new TraexAcpAdapter(['traex']);
  assert.equal(agy.planExhausted('You have exhausted your quota on this model.'), true);
  assert.equal(trae.planExhausted("You've hit your usage limit for model. Switch to another model now,"), true);
  for (const msg of ['429 rate limit', 'disk quota exceeded', 'image generation quota exceeded', 'network timeout']) {
    assert.equal(agy.planExhausted(msg), false); assert.equal(trae.planExhausted(msg), false);
  }
});

test('exhausted same-instance model handover retries the failed turn before queued work', async () => {
  const h = await setup();
  try {
    h.config([{ agent: 'agy', model: 'claude-sonnet' }]);
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' });
    await h.manager.startTurn(h.thread.id, { text: 'queued after exhausted', queue: true });
    await h.done();
    const events = h.store.listEvents(h.thread.id, 0, -1);
    assert.equal(h.manager.getThread(h.thread.id).model, 'claude-sonnet');
    assert.equal(h.manager.listThreads().length, 1);
    assert.deepEqual(events.filter(e => e.type === 'turn.started').map(e => e.data.displayText ?? e.data.text), ['quota-exhausted', 'quota-exhausted', 'queued after exhausted']);
    assert.equal(events.filter(e => e.type === 'turn.error').length, 1);
  } finally { await h.close(); }
});

test('no successor stops queued work, and a previously exhausted source is not selected again', async () => {
  const h = await setup();
  try {
    h.config([{ agent: 'offline', model: 'gemini-pro' }]);
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' });
    await h.manager.startTurn(h.thread.id, { text: 'must wait', queue: true }); await h.done();
    assert.equal(h.manager.autoHandover.view(h.thread.id).state, 'stopped');
    assert.equal(h.manager.threadView(h.thread.id).queued.length, 1);
    const other = await h.manager.createThread({ agent: 'agy2', cwd: h.dir, model: 'gemini-pro' });
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy2: [{ agent: 'agy' }, { agent: 'agy', model: 'gemini-pro' }] } });
    assert.deepEqual(await h.manager.autoHandover.targets(other.id), []);
  } finally { await h.close(); }
});

test('the default order skips a bridge without a plan; an order that names it still uses it', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    // Every subscription account is used up, so only the bridge is left.
    for (const id of ['agy2', 'agy3']) writeFileSync(join(h.dir, id + '.json'), JSON.stringify({ 'Gemini Models': 95 }));
    assert.deepEqual(await h.manager.autoHandover.targets(h.thread.id), []);
    // A healthy subscription account is found as before.
    writeFileSync(join(h.dir, 'agy3.json'), JSON.stringify({ 'Gemini Models': 10 }));
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['agy3']);
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: [{ agent: 'bridge' }] } });
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['bridge']);
  } finally { await h.close(); }
});

test('a stale self-report is read again, and an account whose usage cannot be read is not ruled out', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    writeFileSync(join(h.dir, 'agy2.json'), JSON.stringify({ 'Gemini Models': 95 }));
    const observe = (agent: string, usedPercent: number) => (h.manager as unknown as { observeQuota(agent: string, quota: PlanQuota): void })
      .observeQuota(agent, { checkedAt: new Date(Date.now() - 60_000).toISOString(), windows: [{ label: '7D', usedPercent }] });
    // agy3 told its usage a minute ago while answering: too old to decide on, too recent to be probed
    // unasked. The handover reads it again instead of passing it over.
    observe('agy3', 50);
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['agy3']);
    // Its usage endpoint fails now: the last reading stands, and only a real limit rules it out.
    h.adapters.get('agy3')!.fetchQuota = async () => { throw new Error('usage endpoint down'); };
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['agy3']);
    observe('agy3', 95);
    assert.deepEqual(await h.manager.autoHandover.targets(h.thread.id), []);
  } finally { await h.close(); }
  const fresh = await setup();
  try {
    fresh.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    // Never read at all: an account that cannot be read is not taken for a bridge without a plan.
    fresh.adapters.get('agy2')!.fetchQuota = async () => { throw new Error('usage endpoint down'); };
    assert.deepEqual((await fresh.manager.autoHandover.targets(fresh.thread.id)).map(t => t.agent), ['agy2']);
  } finally { await fresh.close(); }
});

test('a hub that never saved the setting starts with it on, and a choice made later stays', async () => {
  const h = await setup();
  try {
    assert.equal(h.manager.autoHandover.settings().enabled, false);
    h.manager.autoHandover.seedDefault();
    assert.equal(h.manager.autoHandover.settings().enabled, true);
    h.manager.autoHandover.configure({ enabled: false, threshold: 90, orders: {} });
    h.manager.autoHandover.seedDefault();
    assert.equal(h.manager.autoHandover.settings().enabled, false);
  } finally { await h.close(); }
});

test('the default order hands over within the same product only', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    writeFileSync(join(h.dir, 'agy2.json'), JSON.stringify({ 'Gemini Models': 95 }));
    writeFileSync(join(h.dir, 'agy3.json'), JSON.stringify({ 'Gemini Models': 95 }));
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' }); await h.done();
    assert.equal(h.manager.autoHandover.view(h.thread.id).state, 'stopped');
    assert.equal(h.manager.listThreads().length, 1);
    // An explicitly listed cross-product target is still honored as configured.
    const second = await h.manager.createThread({ agent: 'agy2', cwd: h.dir, model: 'gemini-pro' });
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy2: [{ agent: 'other' }] } });
    await h.manager.startTurn(second.id, { text: 'quota-exhausted' }); await h.done(second.id);
    const next = h.manager.listThreads().find(t => t.handover?.sourceId === second.id);
    assert.equal(next?.agent, 'other');
    await h.done(next!.id);
  } finally { await h.close(); }
});

test('TRAE fixture produces the real usage-limit message through its app-server', async () => {
  const adapter = new TraexAcpAdapter([process.execPath, join(fixtures, 'fake-acp.mjs')]);
  const session = await adapter.start({ cwd: tmpdir() });
  try {
    const result = await session.prompt({ text: 'quota-exhausted', images: [] }, { emit() {}, requestApproval: async () => ({ cancelled: true }) });
    assert.equal(result.stopReason, 'error');
    assert.ok(adapter.planExhausted(result.error ?? ''));
  } finally { await session.close(); }
});

test('one exhausted instance can hand over separate member conversations without selecting it again', async () => {
  const h = await setup();
  try {
    h.config();
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' }); await h.done();
    const second = await h.manager.createThread({ agent: 'agy', model: 'gemini-pro', cwd: h.dir });
    await h.manager.startTurn(second.id, { text: 'quota-exhausted' }); await h.done(second.id);
    assert.equal(h.manager.listThreads().filter(t => t.handover).length, 2);
  } finally { await h.close(); }
});

test('a lead handover preserves the collaboration root and its existing members', async () => {
  const h = await setup();
  try {
    h.config(); await h.team.enable(h.thread.id);
    const job = await h.team.command(h.thread.id, { op: 'spawn', name: 'worker', agent: 'agy3', model: 'gemini-pro', text: 'hello', requestId: 'spawn-root' }) as { to: string };
    await h.done(job.to); await h.done();
    await h.manager.startTurn(h.thread.id, { text: 'quota-near', queue: true });
    await wait(() => h.manager.listThreads().some(t => t.handover?.sourceId === h.thread.id)); await h.done();
    const next = h.manager.listThreads().find(t => t.handover?.sourceId === h.thread.id)!; await h.done(next.id);
    assert.equal(h.team.view(job.to)?.rootId, next.id);
    assert.equal(h.team.view(next.id)?.members[0].name, 'worker');
    assert.equal(h.store.coordinationRecords().length, 1);
  } finally { await h.close(); }
});

test('an unmapped pool retires the instance but cannot select another model on it', async () => {
  const h = await setup();
  try {
    h.adapters.get('agy')!.fetchQuota = async () => ({ checkedAt: new Date().toISOString(), windows: [{ label: '7D', scope: 'unknown pool', usedPercent: 95 }, { label: '7D', scope: 'Claude', modelIds: ['claude-sonnet'], usedPercent: 10 }] });
    h.config([{ agent: 'agy', model: 'claude-sonnet' }, { agent: 'agy2', model: 'gemini-pro' }]);
    await h.manager.startTurn(h.thread.id, { text: 'finish this turn' }); await h.done();
    const next = h.manager.listThreads().find(t => t.handover?.sourceId === h.thread.id);
    assert.equal(next?.agent, 'agy2');
  } finally { await h.close(); }
});

test('unknown and stale quota never trigger a proactive summary', async () => {
  const h = await setup();
  try {
    h.config();
    for (const q of [{ checkedAt: '2000-01-01', windows: [{ label: '7D', usedPercent: 99 }] }, { checkedAt: new Date().toISOString(), windows: [] }]) {
      h.adapters.get('agy')!.fetchQuota = async () => q;
      await h.manager.startTurn(h.thread.id, { text: 'complete with uncertain quota' }); await h.done();
    }
    assert.equal(h.manager.listThreads().length, 1);
    assert.equal(h.store.listEvents(h.thread.id, 0, -1).some(e => e.data.handover), false);
  } finally { await h.close(); }
});

test('quota checks do not interrupt a running turn awaiting approval', async () => {
  const h = await setup();
  let release!: () => void;
  try {
    h.config();
    const adapter = h.adapters.get('agy')!;
    adapter.start = async () => ({ agentSessionId: 'approval-session', async prompt(_input, hooks) {
      await hooks.requestApproval({ title: 'Approve action', options: [{ optionId: 'allow', name: 'Allow', kind: 'allow_once' }], toolCall: {} });
      return { stopReason: 'cancelled' };
    }, async cancel() { release?.(); }, async close() {} });
    adapter.fetchQuota = async () => ({ checkedAt: new Date().toISOString(), windows: [{ label: '7D', usedPercent: 95 }] });
    await h.manager.startTurn(h.thread.id, { text: 'approval needed' });
    await wait(() => h.manager.threadView(h.thread.id).waiting);
    await new Promise(r => setTimeout(r, 150));
    assert.equal(h.manager.listThreads().length, 1);
    assert.equal(h.store.listEvents(h.thread.id, 0, -1).filter(e => e.type === 'turn.started').length, 1);
    await h.manager.cancelTurn(h.thread.id); await h.done();
    assert.equal(h.manager.listThreads().length, 1);
  } finally { await h.close(); }
});

test('automatic selection does not broaden an approval-required conversation', async () => {
  const h = await setup();
  try {
    h.config();
    h.store.updateThread(h.thread.id, { permissionMode: 'ask' });
    assert.deepEqual(await h.manager.autoHandover.targets(h.thread.id), []);
  } finally { await h.close(); }
});

test('takeover: an exhausted lead hands its place, failed ask and note to the chosen agent', async () => {
  const h = await setup();
  try {
    await h.team.enable(h.thread.id);
    const job = await h.team.command(h.thread.id, { op: 'spawn', name: 'worker', agent: 'agy3', model: 'gemini-pro', text: 'hello', requestId: 'spawn-take' }) as { to: string };
    await h.done(job.to); await h.done();
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' }); await h.done();
    assert.equal(h.manager.listThreads().filter(t => t.handover).length, 0, 'automatic handover is off');
    for (const bad of [{}, { agent: 'missing' }, { agent: 'agy2', note: 7 }]) assert.equal((await h.api(`/api/threads/${h.thread.id}/takeover`, 'POST', bad)).status, 400);
    const res = await h.api(`/api/threads/${h.thread.id}/takeover`, 'POST', { agent: 'agy2', note: '先挑三道更难的题' });
    assert.equal(res.status, 201);
    const next = await res.json() as { id: string; agent: string; handover: { mode: string; sourceId: string } };
    assert.equal(next.agent, 'agy2'); assert.equal(next.handover.mode, 'continue'); assert.equal(next.handover.sourceId, h.thread.id);
    await h.done(next.id);
    assert.equal(h.team.view(job.to)?.rootId, next.id);
    assert.equal(h.team.view(next.id)?.members[0].name, 'worker');
    const text = String(h.store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')!.data.text);
    assert.match(text, new RegExp(`calli history ${h.thread.id}`));
    assert.match(text, /你现在是本次协作的主控/);
    assert.match(text, /## 继续未完成的这一轮\n\nquota-exhausted/);
    assert.match(text, /## 用户补充\n\n先挑三道更难的题/);
    // The brief leaves the full coordinates and the long synthesized summary to `calli`.
    assert.doesNotMatch(text, /Session 文件地址|# 交接摘要/);
    assert.ok(text.length < 1500, `brief handover is ${text.length} chars`);
  } finally { await h.close(); }
});
