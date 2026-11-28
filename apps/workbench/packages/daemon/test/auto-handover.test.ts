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

async function setup(hidden?: string) {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-auto-test-'));
  const store = new Store(dir);
  const adapters = new Map<string, AgentAdapter>();
  for (const id of ['agy', 'agy2', 'agy3']) {
    writeFileSync(join(dir, id + '.json'), JSON.stringify({ 'Gemini Models': 10, 'Claude Models': 20 }));
    adapters.set(id, new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], {
      FAKE_HANDOVER_QUOTA: join(dir, id + '.json'),
    }));
  }
  adapters.set('offline', new AgyAdapter(['nonexistent-handover-fixture']));
  writeFileSync(join(dir, 'other.json'), JSON.stringify({ 'Gemini Models': 10 }));
  adapters.set('other', new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], { FAKE_HANDOVER_QUOTA: join(dir, 'other.json') }));
  writeFileSync(join(dir, 'bridge.json'), '{}');
  adapters.set('bridge', new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], { FAKE_HANDOVER_QUOTA: join(dir, 'bridge.json') }));
  const kinds: Record<string, string> = { other: 'traex' };
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: kinds[id] ?? 'agy', ...(id === hidden ? { hidden: true } : {}) })));
  const team = new CoordinationManager(store, manager, join(dir, 'calli.sock'));
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + path, { method, headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const thread = await manager.createThread({ agent: 'agy', cwd: dir, model: 'gemini-pro', title: '主线任务' });
  const config = (targets = [{ agent: 'agy2', model: 'gemini-pro' }]) => manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: targets } });
  const done = async (id = thread.id) => {
    for (;;) {
      await wait(() => manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id));
      await new Promise(r => setTimeout(r, 50));
      if (manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id)) return;
    }
  };
  return {
    dir, store, manager, team, thread, api, config, done, adapters,
    async close() { team.dispose(); await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); },
  };
}

test('quota windows require fresh data and explicit model coverage', () => {
  const q = { checkedAt: new Date().toISOString(), windows: [{ label: '7D', scope: 'unknown', usedPercent: 99 }, { label: '5H', scope: 'Gemini', modelIds: ['gemini'], usedPercent: 95 }] };
  assert.equal(modelWindows(q, 'claude').length, 0);
  assert.equal(modelWindows(q, 'gemini').length, 1);
  assert.equal(freshQuota(q), true);
  assert.equal(freshQuota({ ...q, checkedAt: '2000-01-01' }), false);
  assert.equal(freshQuota({ ...q, checkedAt: 'unknown' }), false);
});

test('a hidden instance is never an automatic target', async () => {
  const h = await setup('agy2');
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
    for (const bad of [{ ...settings, threshold: 100 }, { ...settings, enabled: 'true' }, { ...settings, orders: { agy: [{ agent: 'missing' }] } }]) {
      assert.equal((await h.api('/api/handover-settings', 'PUT', bad)).status, 400);
    }
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { enabled: 'false' })).status, 400);
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { enabled: false })).status, 200);
    const result = await (await h.api(`/api/threads/${h.thread.id}/auto-handover`)).json();
    assert.equal(result.enabled, false); assert.equal(result.source, 'thread'); assert.equal(result.targets[0].agent, 'agy2');
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'DELETE')).status, 200);
  } finally { await h.close(); }
});

test('healthy near-quota turns are never interrupted; only plan_exhausted spawns a 0-token lightweight fork', async () => {
  const h = await setup();
  try {
    h.config([{ agent: 'agy2', model: 'gemini-pro' }]);
    await h.manager.startTurn(h.thread.id, { text: 'quota-near' });
    await h.done();
    assert.equal(h.manager.listThreads().length, 1, 'healthy near-quota turn stays on current thread');
    assert.equal(h.store.listEvents(h.thread.id, 0, -1).some(e => e.data.handover), false, 'no summary turn spent');

    // Now actually hit plan_exhausted with a queued follow-up -> instant grouped lightweight fork!
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' });
    await h.manager.startTurn(h.thread.id, { text: 'queued followup', queue: true });
    await h.done();

    const fork = h.manager.listThreads().find(t => t.forkedFrom === h.thread.id)!;
    assert.ok(fork, 'auto-continue created a grouped lightweight fork');
    assert.equal(fork.agent, 'agy2');
    assert.equal(fork.model, 'gemini-pro');
    assert.equal(fork.forkGroup, h.thread.id);
    assert.equal(fork.forkTag, '自动续跑');
    assert.equal(h.manager.autoHandover.view(h.thread.id).state, 'forked');
    assert.equal(h.manager.autoHandover.view(h.thread.id).forkedThreadId, fork.id);
    await h.done(fork.id);

    const starts = h.store.listEventsOfType(fork.id, 'turn.started');
    assert.equal(starts.length, 2);
    assert.match(String(starts[0].data.text), /<loochat-fork-anchor>/);
    assert.match(String(starts[0].data.text), /quota-near/);
    assert.equal(String(starts[0].data.displayText ?? starts[0].data.text), 'quota-exhausted');
    assert.equal(String(starts[1].data.displayText ?? starts[1].data.text), 'queued followup');
  } finally { await h.close(); }
});

test('exhausted fallback skips unavailable and near-quota targets and forks onto the first healthy target', async () => {
  const h = await setup();
  try {
    writeFileSync(join(h.dir, 'agy2.json'), JSON.stringify({ 'Gemini Models': 95 }));
    h.config([{ agent: 'offline', model: 'gemini-pro' }, { agent: 'agy2', model: 'gemini-pro' }, { agent: 'agy3', model: 'gemini-pro' }]);
    await h.manager.startTurn(h.thread.id, { text: '先完成前半段' }); await h.done();
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' }); await h.done();
    assert.equal(h.store.listEventsOfType(h.thread.id, 'turn.started').length, 2, 'zero extra summary turns on source');
    const fork = h.manager.listThreads().find(t => t.forkedFrom === h.thread.id)!;
    assert.ok(fork);
    assert.equal(fork.agent, 'agy3');
    await h.done(fork.id);
    const start = h.store.listEventsOfType(fork.id, 'turn.started')[0];
    assert.match(String(start.data.text), /<loochat-fork-anchor>/);
    assert.match(String(start.data.text), /先完成前半段/);
    assert.match(String(start.data.text), /quota-exhausted/);
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

test('the default order skips bridges, stays within product, and honors explicit orders', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    for (const id of ['agy2', 'agy3']) writeFileSync(join(h.dir, id + '.json'), JSON.stringify({ 'Gemini Models': 95 }));
    assert.deepEqual(await h.manager.autoHandover.targets(h.thread.id), []);
    writeFileSync(join(h.dir, 'agy3.json'), JSON.stringify({ 'Gemini Models': 10 }));
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['agy3']);
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: [{ agent: 'bridge' }] } });
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['bridge']);

    const second = await h.manager.createThread({ agent: 'agy2', cwd: h.dir, model: 'gemini-pro' });
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy2: [{ agent: 'other' }] } });
    await h.manager.startTurn(second.id, { text: 'quota-exhausted' }); await h.done(second.id);
    const fork = h.manager.listThreads().find(t => t.forkedFrom === second.id);
    assert.equal(fork?.agent, 'other');
    await h.done(fork!.id);
  } finally { await h.close(); }
});

test('stale self-reports are refreshed and unreadable quota does not disqualify a candidate', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    writeFileSync(join(h.dir, 'agy2.json'), JSON.stringify({ 'Gemini Models': 95 }));
    const observe = (agent: string, usedPercent: number) => (h.manager as unknown as { observeQuota(agent: string, quota: PlanQuota): void })
      .observeQuota(agent, { checkedAt: new Date(Date.now() - 60_000).toISOString(), windows: [{ label: '7D', usedPercent }] });
    observe('agy3', 50);
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['agy3']);
    h.adapters.get('agy3')!.fetchQuota = async () => { throw new Error('usage endpoint down'); };
    assert.deepEqual((await h.manager.autoHandover.targets(h.thread.id)).map(t => t.agent), ['agy3']);
    observe('agy3', 95);
    assert.deepEqual(await h.manager.autoHandover.targets(h.thread.id), []);
  } finally { await h.close(); }
});

test('collaboration default, collaboration override, and conversation override remain independent', async () => {
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
    assert.equal(h.manager.autoHandover.view(job.to).source, 'thread');
    assert.equal(h.manager.autoHandover.view(job.to).enabled, true);
    h.manager.autoHandover.setOverride(job.to, null);
    assert.equal(h.manager.autoHandover.view(job.to).enabled, false);
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

test('manual POST /takeover still preserves collaboration lead and failed ask for legacy callers', async () => {
  const h = await setup();
  try {
    await h.team.enable(h.thread.id);
    const job = await h.team.command(h.thread.id, { op: 'spawn', name: 'worker', agent: 'agy3', model: 'gemini-pro', text: 'hello', requestId: 'spawn-take' }) as { to: string };
    await h.done(job.to); await h.done();
    await h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' }); await h.done();
    const res = await h.api(`/api/threads/${h.thread.id}/takeover`, 'POST', { agent: 'agy2', note: '继续处理' });
    assert.equal(res.status, 201);
    const next = await res.json() as { id: string; agent: string; handover: { mode: string; sourceId: string } };
    assert.equal(next.agent, 'agy2');
    assert.equal(next.handover.mode, 'continue');
    await h.done(next.id);
    assert.equal(h.team.view(job.to)?.rootId, next.id);
  } finally { await h.close(); }
});
