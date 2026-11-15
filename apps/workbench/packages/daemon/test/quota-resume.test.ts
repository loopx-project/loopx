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
import type { AgentAdapter } from '../src/adapters/types.js';
import { createHttpServer } from '../src/http.js';
import type { ResumeView } from '../src/quota-resume.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const wait = async (check: () => unknown, timeout = 8000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};

/** One daemon with fake agy agents whose quota file a test rewrites as the "provider". */
async function setup(opts: { exhaustOnce?: boolean; exhaustDelayMs?: number } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-resume-test-'));
  const store = new Store(dir);
  const adapters = new Map<string, AgentAdapter>();
  const quotaFiles: Record<string, string> = {};
  for (const id of ['agy', 'agy2']) {
    // agy starts exhausted with a long-past reset; agy2 is healthy, so it can receive handovers.
    quotaFiles[id] = join(dir, `quota-${id}.json`);
    writeFileSync(quotaFiles[id], JSON.stringify({ 'Gemini Models': { used: id === 'agy' ? 100 : 10, resetsAt: '2000-01-01T00:00:00Z' } }));
    adapters.set(id, new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], {
      FAKE_HANDOVER_QUOTA: quotaFiles[id],
      ...(opts.exhaustDelayMs && id === 'agy' ? { FAKE_HANDOVER_EXHAUST_DELAY_MS: String(opts.exhaustDelayMs) } : {}),
      ...(opts.exhaustOnce && id === 'agy' ? { FAKE_HANDOVER_EXHAUST_ONCE: join(dir, 'exhaust-once') } : {}),
    }));
  }
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: 'agy' })));
  Object.defineProperty(manager.autoHandover, 'delayMs', { value: 80 });
  Object.defineProperty(manager.quotaResume, 'backoffMs', { value: [10] });
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) =>
    fetch(base + path, { method, headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const thread = await manager.createThread({ agent: 'agy', cwd: dir, model: 'gemini-pro' });
  const idle = (id = thread.id) => wait(() => manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id));
  return {
    dir, store, manager, adapters, quotaFiles, thread, api, idle,
    /** Runs a turn that dies of plan exhaustion and waits for the thread to park. */
    exhaust: async (id = thread.id) => {
      await manager.startTurn(id, { text: 'quota-exhausted' });
      await idle(id);
      const last = store.listTurns(id).at(-1)!.turnId;
      assert.ok(store.listTurnEvents(id, last).some(e => e.type === 'turn.error' && e.data.code === 'plan_exhausted'), 'turn should die of plan exhaustion');
    },
    /** The person's click on the card: PATCH the thread's resume flag. */
    arm: async (id: string): Promise<ResumeView> => {
      const res = await api(`/api/threads/${id}/auto-handover`, 'PATCH', { resume: true });
      assert.equal(res.status, 200);
      const body = await res.json() as { resume: ResumeView };
      assert.ok(body.resume, 'arming returns the watch');
      return body.resume;
    },
    setQuota: (agent: string, value: number | { used: number; resetsAt: string }) =>
      writeFileSync(quotaFiles[agent], JSON.stringify({ 'Gemini Models': value })),
    async close() {
      try { await manager.shutdown(); } catch { /* already down */ }
      await new Promise<void>(r => server.close(() => r()));
      try { store.close(); } catch { /* already closed */ }
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

test('arming validates the parked state; the watch shows in views', async () => {
  const h = await setup();
  try {
    // Not exhausted: a plain turn that ends fine cannot be armed.
    await h.manager.startTurn(h.thread.id, { text: 'hello' });
    await h.idle();
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { resume: true })).status, 409);
    assert.equal((await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { resume: 'yes' })).status, 400);
    assert.equal((await (await h.api(`/api/threads/${h.thread.id}/auto-handover`)).json()).resume, null);
    // Now park it and arm through the API.
    await h.exhaust();
    const view = await h.arm(h.thread.id);
    assert.equal(view.origin, 'user');
    assert.equal(h.manager.threadView(h.thread.id).autoHandover.resume?.origin, 'user');
    // Disarm.
    const off = await h.api(`/api/threads/${h.thread.id}/auto-handover`, 'PATCH', { resume: false });
    assert.equal(off.status, 200);
    assert.equal(h.manager.quotaResume.view(h.thread.id), null);
  } finally { await h.close(); }
});

test('still-exhausted backs off; once fresh and under threshold the failed turn is resent, queue follows', async () => {
  const h = await setup({ exhaustOnce: true, exhaustDelayMs: 400 });
  try {
    // Park with auto-handover on but no target: the stop notice holds the queue.
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: [] }, resume: false });
    const dying = h.manager.startTurn(h.thread.id, { text: 'quota-exhausted' });
    // A follow-up waits behind the turn that is about to die of plan exhaustion.
    await wait(() => h.manager.threadView(h.thread.id).status === 'running');
    const followup = await h.manager.startTurn(h.thread.id, { text: 'followup after reset', queue: true });
    assert.ok('queued' in followup);
    await dying;
    await h.idle();
    assert.equal(h.manager.autoHandover.view(h.thread.id).state, 'stopped');
    assert.equal(h.manager.threadView(h.thread.id).queued.length, 1);
    await h.arm(h.thread.id);

    // First probe: the provider still says exhausted -> backoff, keep waiting.
    await h.manager.quotaResume.check();
    assert.equal(h.manager.quotaResume.view(h.thread.id)?.lastError, '额度还没有恢复');

    // The reset lands: the retry goes out, succeeds, and the queued message follows it.
    h.setQuota('agy', 10);
    await new Promise(r => setTimeout(r, 20)); // let the backoff window pass
    await h.manager.quotaResume.check();
    await h.idle();
    await wait(() => h.manager.threadView(h.thread.id).queued.length === 0);
    const events = h.store.listEventsOfType(h.thread.id, 'handover.resume');
    assert.equal(events.length, 1);
    assert.equal(h.store.listTurns(h.thread.id).length, 3); // exhausted ask, resent ask, queued followup
    assert.equal(h.manager.quotaResume.view(h.thread.id), null);
    assert.notEqual(h.manager.autoHandover.view(h.thread.id).state, 'stopped');
  } finally { await h.close(); }
});

test('a watch survives a daemon restart and then resumes', async () => {
  const h = await setup({ exhaustOnce: true });
  try {
    await h.exhaust();
    await h.arm(h.thread.id);
    await h.manager.shutdown();
    h.store.close();
    // A fresh daemon on the same data still knows the wait.
    const store = new Store(h.dir);
    const manager = new ThreadManager(store, h.adapters, [h.dir], new AttachmentStore(h.dir), () => [{ id: 'agy', adapter: 'agy' }, { id: 'agy2', adapter: 'agy' }]);
    try {
      assert.equal(manager.quotaResume.view(h.thread.id)?.origin, 'user');
      h.setQuota('agy', 10);
      await manager.quotaResume.check();
      await wait(() => manager.threadView(h.thread.id).status !== 'running');
      assert.equal(store.listTurns(h.thread.id).length, 2); // the ask was resent
      assert.equal(manager.quotaResume.view(h.thread.id), null);
    } finally { await manager.shutdown(); store.close(); }
  } finally { await h.close(); }
});

test('the global switch adopts parked threads, and turning it off keeps only clicked watches', async () => {
  const h = await setup();
  try {
    await h.exhaust();
    assert.equal(h.manager.quotaResume.view(h.thread.id), null); // off by default: nothing armed
    h.manager.autoHandover.configure({ enabled: false, threshold: 90, orders: {}, resume: true });
    await wait(() => h.manager.quotaResume.view(h.thread.id) != null);
    assert.equal(h.manager.quotaResume.view(h.thread.id)?.origin, 'global');

    // A clicked watch is the person's own: the global switch must not take it away.
    const other = await h.manager.createThread({ agent: 'agy', cwd: h.dir, model: 'gemini-pro' });
    await h.exhaust(other.id);
    await h.arm(other.id);
    h.manager.autoHandover.configure({ enabled: false, threshold: 90, orders: {}, resume: false });
    assert.equal(h.manager.quotaResume.view(h.thread.id), null);
    assert.equal(h.manager.quotaResume.view(other.id)?.origin, 'user');
  } finally { await h.close(); }
});

test('a park while the global switch is on arms by itself; a handover that moves the work does not', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: false, threshold: 90, orders: {}, resume: true });
    await h.exhaust();
    await wait(() => h.manager.quotaResume.view(h.thread.id) != null);
    assert.equal(h.manager.quotaResume.view(h.thread.id)?.origin, 'global');

    // With a target configured, a fresh thread's work moves away and no watch may appear on it.
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { agy: [{ agent: 'agy2' }] }, resume: true });
    const second = await h.manager.createThread({ agent: 'agy', cwd: h.dir, model: 'gemini-pro' });
    await h.exhaust(second.id);
    await wait(() => h.manager.listThreads().some(t => t.agent === 'agy2'));
    assert.equal(h.manager.quotaResume.view(second.id), null);
  } finally { await h.close(); }
});

test('a manual takeover drops the watch', async () => {
  const h = await setup();
  try {
    await h.exhaust();
    await h.arm(h.thread.id);
    await h.manager.takeOver(h.thread.id, 'agy2', undefined, undefined);
    assert.equal(h.manager.quotaResume.view(h.thread.id), null);
  } finally { await h.close(); }
});

test('a watch that keeps finding the quota exhausted gives up after enough probes', async () => {
  const h = await setup();
  try {
    await h.exhaust();
    await h.arm(h.thread.id);
    for (let i = 0; i < 12; i++) { await new Promise(r => setTimeout(r, 20)); await h.manager.quotaResume.check(); }
    assert.equal(h.manager.quotaResume.view(h.thread.id), null);
  } finally { await h.close(); }
});

test('a future reset time defers the first probe', async () => {
  const h = await setup();
  try {
    await h.exhaust();
    h.setQuota('agy', { used: 100, resetsAt: new Date(Date.now() + 3600_000).toISOString() });
    await h.arm(h.thread.id);
    const view = h.manager.quotaResume.view(h.thread.id)!;
    assert.ok(Date.parse(view.resetsAt!) > Date.now());
    await h.manager.quotaResume.check();
    assert.equal(h.manager.quotaResume.view(h.thread.id)?.nextCheckAt, view.nextCheckAt); // untouched
  } finally { await h.close(); }
});

test('a stopped thread says why, and 「接着做」 sends the failed ask again', async () => {
  const h = await setup({ exhaustOnce: true });
  try {
    // Nothing has failed yet: there is no failure to show and nothing to retry.
    await h.manager.startTurn(h.thread.id, { text: 'hello' });
    await h.idle();
    assert.equal(h.manager.threadView(h.thread.id).failure, null);
    assert.equal((await h.api(`/api/threads/${h.thread.id}/retry`, 'POST')).status, 409);

    await h.exhaust();
    const stopped = await (await h.api(`/api/threads/${h.thread.id}`)).json() as { status: string; failure: { code: string | null; message: string; at: string } | null };
    assert.equal(stopped.status, 'error');
    assert.equal(stopped.failure?.code, 'plan_exhausted');
    assert.match(stopped.failure?.message ?? '', /quota/);
    assert.ok(Date.parse(stopped.failure!.at) > 0);
    await h.arm(h.thread.id);

    const res = await h.api(`/api/threads/${h.thread.id}/retry`, 'POST');
    assert.equal(res.status, 200);
    // The watch is moot once the person has asked again.
    assert.equal(h.manager.quotaResume.view(h.thread.id), null);
    await h.idle();
    const view = h.manager.threadView(h.thread.id);
    assert.equal(view.status, 'idle');
    assert.equal(view.failure, null);
    const last = h.store.listTurns(h.thread.id).at(-1)!.turnId;
    const started = h.store.listTurnEvents(h.thread.id, last).find(e => e.type === 'turn.started');
    assert.equal(started?.data.text, 'quota-exhausted');
  } finally { await h.close(); }
});
