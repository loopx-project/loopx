import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { BorrowingService } from '../src/borrowing.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import { PushService } from '../src/push.js';
import type { EventType } from '../src/events.js';

const wait = async (fn: () => boolean | Promise<boolean>) => { for (let i = 0; i < 500; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 10)); } throw new Error('timed out'); };

async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-snooze-'));
  const log = join(dir, 'calls'), config = join(dir, 'config'); writeFileSync(log, ''); writeFileSync(config, '{}');
  const store = new Store(dir);
  const adapters = new Map([['claude', new ClaudeAcpAdapter([process.execPath, resolve('test/fixtures/fake-acp.mjs')])]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'dirs')), new AttachmentStore(dir));
  const push = new PushService(store, manager, { executable: resolve('test/fixtures/fake-lark-cli.mjs'), timeoutMs: 1000, retryMs: 1,
    env: { ...process.env, FAKE_LARK_LOG: log, FAKE_LARK_CONFIG: config } });
  const service = new BorrowingService(store, manager, adapters, dir, join(dir, 'tools.sock'));
  const socket = borrowingServer(service); await listenPrivate(socket, service.socket);
  const server = createHttpServer({ manager, push, token: 'test', version: 'test', webDir: null });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const thread = await manager.createThread({ agent: 'claude', cwd: dir, title: 'Noisy' });
  const patch = (id: string, body: unknown) => fetch(`${base}/api/threads/${id}`, { method: 'PATCH', headers: { Authorization: 'Bearer test', 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const sent = () => readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(x => JSON.parse(x)).filter(x => x.args[0] === 'im');
  const emit = (type: EventType, data: Record<string, unknown> = {}, turnId = 'turn') => (manager as any).emitThreadEvent(thread.id, turnId, type, data);
  const enablePush = async () => { await fetch(`${base}/api/push/settings`, { method: 'PUT', headers: { Authorization: 'Bearer test', 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: true }) }); };
  const setGeneral = async (body: unknown) => { await fetch(`${base}/api/general-settings`, { method: 'PUT', headers: { Authorization: 'Bearer test', 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); };

  return { dir, store, manager, push, thread, emit, patch, sent, enablePush, setGeneral, async close() {
    push.dispose(); await service.dispose(); await manager.shutdown();
    await new Promise<void>(r => socket.close(() => r())); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}

test('snooze: only a future time is accepted, stored and returned', async () => {
  const h = await setup(); try {
    const until = new Date(Date.now() + 3600_000).toISOString();
    const ok = await h.patch(h.thread.id, { snoozedUntil: until });
    assert.equal(ok.status, 200);
    assert.equal((await ok.json()).snoozedUntil, until);
    assert.equal(h.store.getThread(h.thread.id)?.snoozedUntil, until);
    for (const bad of [new Date(Date.now() - 1000).toISOString(), 'not a date', 42]) {
      assert.equal((await h.patch(h.thread.id, { snoozedUntil: bad })).status, 400, String(bad));
    }
    assert.equal((await h.patch(h.thread.id, { snoozedUntil: null })).status, 200);
    assert.equal(h.store.getThread(h.thread.id)?.snoozedUntil, null);
  } finally { await h.close(); }
});

test('snooze: a waiting thread holds its pushes back, a failure pierces', async () => {
  const h = await setup(); try {
    await h.enablePush();
    await h.patch(h.thread.id, { snoozedUntil: new Date(Date.now() + 3600_000).toISOString() });
    h.emit('turn.started'); h.emit('approval.request', { approvalId: 'a' });
    await new Promise(r => setTimeout(r, 150));
    assert.equal(h.sent().length, 0, 'a snoozed thread holds its pushes back');
    h.emit('turn.started', {}, 'three'); h.emit('turn.error', { message: 'boom' }, 'three');
    await wait(() => h.sent().length === 1);
    const records = (await h.push.status()).records;
    assert.equal(records[0].kind, 'failure');
  } finally { await h.close(); }
});

test('wake: the due thread comes back unread; not-yet-due and archived stay put', async () => {
  const h = await setup(); try {
    await h.enablePush();
    const soon = new Date(Date.now() + 60_000).toISOString();
    const later = new Date(Date.now() + 3600_000).toISOString();
    await h.patch(h.thread.id, { snoozedUntil: soon });
    const other = await h.manager.createThread({ agent: 'claude', cwd: h.dir, title: 'Later' });
    await h.patch(other.id, { snoozedUntil: later });
    const archived = await h.manager.createThread({ agent: 'claude', cwd: h.dir, title: 'Archived' });
    await h.patch(archived.id, { snoozedUntil: soon });
    await h.patch(archived.id, { archived: true });
    await h.patch(h.thread.id, { unread: false });

    const woken = await h.manager.wakeSnoozedThreads(new Date(soon));
    assert.deepEqual(woken.map(t => t.id), [h.thread.id]);
    const view = h.manager.threadView(h.thread.id);
    assert.equal(view.snoozedUntil, null, 'woken');
    assert.equal(view.unread, true, 'back in the person attention');
    assert.equal(h.store.getThread(other.id)?.snoozedUntil, later, 'not yet due');
    assert.equal(h.store.getThread(archived.id)?.snoozedUntil, soon, 'an archived thread keeps its sleep');

    const pushed = h.push.snoozeWake(h.thread.id, soon);
    assert.equal(pushed?.kind, 'snooze');
  } finally { await h.close(); }
});

test('auto-archive: a thread finished N days ago goes even when touched later; a pin holds it', async () => {
  const h = await setup(); try {
    await h.patch(h.thread.id, { done: true, pinned: true });
    const plain = await h.manager.createThread({ agent: 'claude', cwd: h.dir, title: 'Plain' });
    await h.patch(plain.id, { done: true });
    await h.setGeneral({ startupPage: 'new-thread', autoArchiveDays: 1 });
    const later = new Date(Date.now() + 3 * 24 * 3600_000);
    const archived = await h.manager.autoArchiveOldThreads(later);
    assert.equal(h.store.getThread(h.thread.id)?.archivedAt, null, 'pinned stays even when done');
    assert.ok(h.store.getThread(plain.id)?.archivedAt, 'done N days ago is archived');
    assert.equal(archived, 1);
  } finally { await h.close(); }
});
