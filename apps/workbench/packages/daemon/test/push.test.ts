import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
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
const exec = promisify(execFile);
const wait = async (fn: () => boolean | Promise<boolean>) => { for (let i = 0; i < 500; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 10)); } throw new Error('timed out'); };
async function setup(options: { executable?: string; timeoutMs?: number } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-push-'));
  const log = join(dir, 'calls'), config = join(dir, 'config'); writeFileSync(log, ''); writeFileSync(config, '{}');
  const store = new Store(dir);
  const adapters = new Map([['claude', new ClaudeAcpAdapter([process.execPath, resolve('test/fixtures/fake-acp.mjs')])]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'dirs')), new AttachmentStore(dir));
  let now = Date.now();
  const push = new PushService(store, manager, { executable: resolve('test/fixtures/fake-lark-cli.mjs'), timeoutMs: 1000, retryMs: 1, ...options,
    env: { ...process.env, FAKE_LARK_LOG: log, FAKE_LARK_CONFIG: config, CALLI_TOKEN: 'secret', CALLI_TOKEN: 'secret', CALLI_TOOLS_KEY: 'secret', CALLI_OTHER: 'secret' }, now: () => now });
  const service = new BorrowingService(store, manager, adapters, dir, join(dir, 'tools.sock'));
  const socket = borrowingServer(service); await listenPrivate(socket, service.socket);
  const server = createHttpServer({ manager, push, token: 'test', version: 'test', webDir: null });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const thread = await manager.createThread({ agent: 'claude', cwd: dir, title: 'Reminder' });
  const calls = () => readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(x => JSON.parse(x));
  const sent = () => calls().filter(x => x.args[0] === 'im');
  const emit = (type: EventType, data: Record<string, unknown> = {}, turnId = 'turn') => (manager as any).emitThreadEvent(thread.id, turnId, type, data);
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + '/api/push/' + path, { method, headers: { Authorization: 'Bearer test', 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const cli = (args: string[], extra = {}) => exec(process.execPath, [resolve('bin/calli'), ...args], { env: { ...process.env, ...service.environment(thread.id), ...extra } });
  return { dir, store, manager, push, service, thread, emit, api, cli, calls, sent, config, advance: (ms: number) => { now += ms; }, async close() {
    push.dispose(); await service.dispose(); await manager.shutdown();
    await new Promise<void>(r => socket.close(() => r())); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}
test('push settings persist, test uses bot argv/stdin, safe links, scrubbed environment and metadata only', async () => {
  const h = await setup(); try {
    assert.equal((await (await h.api('settings')).json()).enabled, false);
    assert.equal((await (await h.api('settings')).json()).recipient, 'test-recipient');
    assert.equal((await h.api('settings', 'PUT', { baseUrl: 'https://example.invalid/?token=secret' })).status, 400);
    assert.equal((await h.api('settings', 'PUT', { enabled: true, baseUrl: 'https://example.invalid' })).status, 200);
    h.emit('turn.started'); h.emit('approval.request', { approvalId: 'a', title: 'private command' });
    await wait(() => h.sent().length === 1);
    const call = h.sent()[0]; assert.deepEqual(call.args.slice(0, 8), ['im', '+messages-send', '--as', 'bot', '--user-id', 'test-recipient', '--markdown', '-']);
    assert.match(call.input, /等你批准/); assert.match(call.input, new RegExp(`/#/thread/${h.thread.id}`)); assert.ok(!call.input.includes('private command'));
    assert.ok(h.calls().every(c => c.credentials.length === 0));
    assert.equal((await h.api('test', 'POST')).status, 202); await wait(() => h.sent().length === 2);
    assert.equal(h.store.hubSetting<any>('push', {}).enabled, true);
    assert.equal((await (await h.api('status?summary=true')).json()).records.length, 1);
    assert.equal((await (await h.api('status')).json()).records.length, 2);
  } finally { await h.close(); }
});
test('live trigger matrix excludes normal completion, historical/terminal/control/cancelled/interrupted/member failures', async () => {
  const h = await setup(); try {
    h.push.putSettings({ enabled: true, scheduled: true });
    for (const [i, origin, type, end] of [
      [0, {}, 'turn.done', {}], [1, { imported: true }, 'turn.error', {}], [2, { fromTerminal: true }, 'turn.error', {}],
      [3, { control: true }, 'turn.error', {}], [4, {}, 'turn.done', { stopReason: 'cancelled' }],
      [5, {}, 'turn.error', { code: 'interrupted' }], [6, { coordination: {} }, 'turn.error', {}],
    ] as Array<[number, Record<string, unknown>, EventType, Record<string, unknown>]>) {
      h.emit('turn.started', origin, String(i)); h.emit(type, end, String(i));
    }
    assert.equal((await h.push.status()).records.length, 0);
    h.emit('turn.started', {}, 'error'); h.emit('turn.error', { message: 'private error' }, 'error');
    h.emit('turn.started', { scheduled: true }, 'schedule'); h.emit('turn.done', {}, 'schedule');
    h.emit('turn.started', {}, 'input'); h.emit('input.request', { inputId: 'q' }, 'input');
    h.emit('input.request', { inputId: 'q' }, 'input'); h.emit('input.resolved', { inputId: 'q' }, 'input');
    h.emit('input.request', { inputId: 'p', kind: 'plan' }, 'input');
    await wait(() => h.sent().length === 4);
    assert.deepEqual((await h.push.status()).records.map(r => r.kind), ['plan', 'question', 'scheduled', 'failure']);
    assert.ok(h.sent().every(c => !c.input.includes('private error')));
  } finally { await h.close(); }
});
test('notice and global limits drop excess work and retain only twenty records', async () => {
  const h = await setup(); try {
    h.push.putSettings({ enabled: true }); h.manager.notice(h.thread.id, 'hello'); h.manager.notice(h.thread.id, 'again');
    assert.equal((await h.push.status()).dropped, 1);
    h.advance(60001); h.manager.notice(h.thread.id, 'next minute');
    for (let i = 0; i < 30; i++) h.push.test();
    const status = await h.push.status(); assert.equal(status.dropped, 3); assert.equal(status.records.length, 20);
  } finally { await h.close(); }
});
for (const mode of ['fail', 'timeout']) test(`push ${mode} retries twice with identical idempotency key`, async () => {
  const h = await setup({ timeoutMs: mode === 'timeout' ? 250 : 1000 }); try {
    writeFileSync(h.config, JSON.stringify({ [mode]: true })); h.push.test();
    await wait(() => h.sent().length === 3); await wait(async () => (await h.push.status()).records[0].result === 'failed');
    assert.equal(new Set(h.sent().map(c => c.args.at(-1))).size, 1);
    assert.ok(!(await h.push.status()).records[0].reason!.includes('untrusted'));
  } finally { await h.close(); }
});
test('missing CLI and unavailable bot are explicit and never send; restart never replays history', async () => {
  const h = await setup({ executable: '/nonexistent/lark-cli' }); try {
    assert.equal((await h.push.status()).cli, false); h.push.test();
    await wait(async () => (await h.push.status()).records[0].result === 'failed'); assert.equal(h.sent().length, 0);
  } finally { await h.close(); }
  const j = await setup(); try {
    writeFileSync(j.config, '{"ready":false}'); j.push.test();
    await wait(async () => (await j.push.status()).records[0].result === 'failed'); assert.equal(j.sent().length, 0);
    j.push.dispose(); const fresh = new PushService(j.store, j.manager); fresh.dispose();
  } finally { await j.close(); }
});
test('real notify CLI authenticates its own thread, enforces Unicode length, accepts stdin and marks unread', async () => {
  const h = await setup(); try {
    const file = join(h.dir, 'notice'); writeFileSync(file, 'hello'); await h.cli(['notify', '--text-file', file]);
    assert.equal(h.store.getThread(h.thread.id)?.unread, true); assert.equal(h.store.listEventsOfType(h.thread.id, 'agent.notice')[0].data.text, 'hello');
    await assert.rejects(h.cli(['notify', '--text-file', file], { CALLI_TOOLS_KEY: 'wrong' }), /credentials/);
    const fromStdin = await new Promise<string>((resolveInput, reject) => {
      const child = execFile(process.execPath, [resolve('bin/calli'), 'notify', '--text-file', '-'], { env: { ...process.env, ...h.service.environment(h.thread.id) } }, (error, stdout) => error ? reject(error) : resolveInput(stdout));
      child.stdin!.end('stdin notice');
    });
    assert.equal(JSON.parse(fromStdin).data.text, 'stdin notice');
    writeFileSync(file, ' '); await assert.rejects(h.cli(['notify', '--text-file', file]), /1–500/);
    writeFileSync(file, '😀'.repeat(501)); await assert.rejects(h.cli(['notify', '--text-file', file]), /1–500/);
    writeFileSync(file, '😀'.repeat(500)); await h.cli(['notify', '--text-file', file]);
    await assert.rejects(h.service.command('operator', { op: 'notify', text: 'bad' }), /thread-scoped/);
    assert.equal(h.sent().length, 0);
  } finally { await h.close(); }
});

test('a background question neither waits nor fails out loud', async () => {
  const h = await setup(); try {
    h.push.putSettings({ enabled: true, waiting: true, failure: true });
    h.emit('turn.started', { background: true });
    h.emit('approval.request', { approvalId: 'a' });
    h.emit('input.request', { inputId: 'i' });
    h.emit('turn.error', { message: 'the naming agent hung' });
    await new Promise(r => setTimeout(r, 150));
    assert.equal(h.sent().length, 0, 'nothing a background turn does is pushed');
  } finally { await h.close(); }
});

test('category switches, quiet completion and imported waiting do not enqueue; notices use explicit agent attribution', async () => {
  const h = await setup(); try {
    h.push.putSettings({ enabled: true, waiting: false, failure: false, notice: false, scheduled: false });
    h.emit('turn.started'); h.emit('approval.request', { approvalId: 'a' }); h.emit('turn.error'); h.manager.notice(h.thread.id, 'muted');
    h.emit('turn.started', { scheduled: true }); h.emit('turn.done');
    assert.equal((await h.push.status()).records.length, 0);
    h.push.putSettings({ waiting: true, scheduled: true, notice: true });
    h.emit('turn.started', { imported: true }, 'historical'); h.emit('input.request', { inputId: 'old' }, 'historical');
    h.emit('turn.started', { scheduled: true }, 'quiet'); h.emit('turn.done', { quiet: true }, 'quiet');
    assert.equal((await h.push.status()).records.length, 0);
    h.manager.notice(h.thread.id, 'Read this'); await wait(() => h.sent().length === 1);
    assert.match(h.sent()[0].input, /通知你/); assert.match(h.sent()[0].input, /来自 agent：Read this/);
    assert.ok(!h.sent()[0].input.includes('打开：'));
  } finally { await h.close(); }
});
