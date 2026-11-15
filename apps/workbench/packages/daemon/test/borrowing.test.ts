import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { HttpError } from '../src/errors.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { Workdirs } from '../src/workdirs.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { sessionEnvironment, type AgentAdapter } from '../src/adapters/types.js';
import { BorrowingService } from '../src/borrowing.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import { turnAnswer } from '../src/coordination.js';
import type { ToolJob } from '../src/tool-jobs.js';

const here = dirname(fileURLToPath(import.meta.url));
const cli = join(here, '../bin/calli');
const exec = promisify(execFile);
const wait = async (check: () => boolean, timeout = 15000) => {
  const until = Date.now() + timeout;
  while (!check()) { if (Date.now() > until) throw new Error('timed out'); await new Promise(r => setTimeout(r, 20)); }
};

async function setup(mode = 'success') {
  const dir = mkdtempSync(join(tmpdir(), 'calli-tools-'));
  const cwd = join(dir, 'project'); mkdirSync(cwd);
  const outside = join(cwd, 'private.png');
  writeFileSync(outside, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX2kAAAAASUVORK5CYII=', 'base64'));
  // Use a custom instance ID: capabilities belong to instances, not just adapter brands.
  const adapters = new Map<string, AgentAdapter>([
    ['art-account', new AgyAdapter([process.execPath, join(here, 'fixtures/fake-agy.mjs')], { FAKE_AGY_IMAGE: mode, FAKE_AGY_OUTSIDE: outside })],
    ['claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures/fake-acp.mjs')])],
  ]);
  const store = new Store(dir);
  const manager = new ThreadManager(store, adapters, new Workdirs([cwd], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir));
  const socket = join(dir, 'ipc/calli.sock');
  const service = new BorrowingService(store, manager, adapters, dir, socket);
  const local = borrowingServer(service); await listenPrivate(local, socket);
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const root = await manager.createThread({ agent: 'claude', cwd });
  const env = { ...process.env, ...service.environment(root.id) };
  const runCli = async (args: string[], extra = {}) => JSON.parse((await exec(process.execPath, [cli, ...args], { env: { ...env, ...extra } })).stdout);
  const call = (body: unknown) => service.command(root.id, body) as Promise<any>;
  const run = (requestId = 'image') => call({ op: 'tools.run', tool: 'art-account/image.generate', input: { prompt: 'paper fox' }, requestId });
  const done = async (id: string) => {
    await wait(() => !['queued', 'running'].includes(store.toolJobs.get(id)!.state));
    return call({ op: 'jobs.get', id });
  };
  const api = (body: unknown, token = 'test-token') => fetch(base + '/api/tools/command', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return { dir, cwd, adapters, store, manager, service, socket, root, runCli, call, run, done, api, base, async close() {
    await manager.borrowing?.dispose(); await manager.shutdown();
    await new Promise<void>(r => local.close(() => r())); await new Promise<void>(r => server.close(() => r()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}

test('Claude ACP process borrows native image generation through the scoped CLI, then downloads the real artifact', async () => {
  const h = await setup();
  try {
    const list = await h.runCli(['tools', 'search', '生图']);
    assert.equal(list[0].id, 'art-account/image.generate');
    assert.equal(list[0].provider, 'art-account');
    assert.equal(list[0].inputSchema, undefined);
    const spec = await h.runCli(['tools', 'describe', list[0].id]);
    assert.deepEqual(spec.inputSchema.required, ['prompt']);
    const started = await h.manager.startTurn(h.root.id, { text: 'borrow-image' });
    assert.ok('turnId' in started);
    await wait(() => h.manager.threadView(h.root.id).status !== 'running');
    const events = h.store.listEvents(h.root.id, 0, -1);
    const entry = events.find(e => e.type === 'turn.started')!;
    assert.equal(entry.data.displayText, 'borrow-image');
    assert.ok(String(entry.data.text).length < 180, 'only the short discovery hint is introduced');
    const again = await h.manager.startTurn(h.root.id, { text: 'next request' });
    assert.ok('turnId' in again);
    await wait(() => h.manager.threadView(h.root.id).status !== 'running');
    const second = h.store.listEventsOfType(h.root.id, 'turn.started').at(-1)!;
    assert.equal(second.data.text, 'next request', 'do not repeat the hint');
    assert.equal((await h.runCli(['tools', 'skill'])).name, 'calli-tools');
    const job = JSON.parse(turnAnswer(events, started.turnId));
    assert.equal(job.state, 'succeeded');
    assert.equal(h.manager.getThread(job.threadId).agent, 'art-account');
    assert.equal(h.service.environment(job.threadId), undefined, 'provider cannot recursively borrow');
    assert.equal(job.artifacts.length, 1);
    assert.equal(job.artifacts[0].path, undefined);
    const output = join(h.cwd, 'result.png');
    const fetched = await h.runCli(['jobs', 'fetch', job.id, job.artifacts[0].id, '--output', output]);
    assert.equal(fetched.sha256, job.artifacts[0].sha256);
    assert.equal(readFileSync(output).subarray(1, 4).toString(), 'PNG');
    const byName = await h.runCli(['jobs', 'fetch', job.id, job.artifacts[0].name, '--output', join(h.cwd, 'by-name.png')]);
    assert.equal(byName.sha256, fetched.sha256);
    await assert.rejects(h.runCli(['jobs', 'fetch', job.id, job.artifacts[0].id, '--output', output]), /EEXIST/);
    const providerStart = h.store.listEvents(job.threadId).find(e => e.type === 'turn.started');
    assert.ok(providerStart);
    assert.equal(h.manager.getThread(job.threadId).permissionMode, 'auto_edit');
  } finally { await h.close(); }
});

test('requests are idempotent, conflicting retries fail, and callers cannot read or cancel another thread’s job', async () => {
  const h = await setup();
  try {
    const [a, b] = await Promise.all([h.run('retry'), h.run('retry')]);
    assert.equal(a.id, b.id);
    await assert.rejects(h.call({ op: 'tools.run', tool: a.tool, input: { prompt: 'different' }, requestId: 'retry' }), /different request/);
    await assert.rejects(h.call({ op: 'tools.run', tool: a.tool, input: { prompt: 'fox', path: '/tmp/other' }, requestId: 'bad' }), /only accepts/);
    await assert.rejects(h.call({ op: 'tools.run', tool: 'missing/image.generate', input: {}, requestId: 'bad' }), /unknown tool/);
    await assert.rejects(h.service.command('other-thread', { op: 'jobs.get', id: a.id }), /no such/);
    await assert.rejects(h.service.command('other-thread', { op: 'jobs.cancel', id: a.id }), /no such/);
    await h.done(a.id);
    const job = h.store.toolJobs.get(a.id)!;
    assert.throws(() => h.service.artifact('other-thread', a.id, job.artifacts[0].id), /no such/);
    assert.equal((await h.run('retry')).id, a.id);
    assert.equal(h.store.toolJobs.list().length, 1);
    assert.throws(() => h.service.caller('bad-key'), /credentials/);
  } finally { await h.close(); }
});

for (const mode of ['missing', 'failed', 'no-evidence', 'outside', 'text', 'wrong-subagent']) {
  test(`image task rejects ${mode} artifacts rather than claiming success`, async () => {
    const h = await setup(mode);
    try {
      const job = await h.done((await h.run()).id);
      assert.equal(job.state, 'failed');
      assert.equal(job.error.code, 'artifact_unverified');
      assert.deepEqual(job.artifacts, []);
    } finally { await h.close(); }
  });
}

test('the native image-generator subagent is supported when the main agent has no direct generate_image tool', async () => {
  const h = await setup('subagent');
  try {
    const result = await h.done((await h.run()).id);
    assert.equal(result.state, 'succeeded');
    assert.equal(result.artifacts.length, 1);
  } finally { await h.close(); }
});

for (const mode of ['subagent-info', 'markdown-title']) {
  test(`image result supports native ${mode} protocol variations`, async () => {
    const h = await setup(mode);
    try {
      const result = await h.done((await h.run()).id);
      assert.equal(result.state, 'succeeded');
      assert.equal(result.artifacts.length, 1);
    } finally { await h.close(); }
  });
}

test('missing stored artifacts produce useful JSON errors over socket and HTTP without creating a download', async () => {
  const h = await setup();
  try {
    const job = await h.done((await h.run()).id);
    const artifact = job.artifacts[0];
    rmSync(h.service.artifact(h.root.id, job.id, artifact.id).path);
    const args = ['jobs', 'fetch', job.id, artifact.id, '--output', join(h.cwd, 'missing.png')];
    await assert.rejects(h.runCli(args), /artifact file unavailable/);
    await assert.rejects(h.runCli(args, { CALLI_TOOLS_SOCKET: '', CALLI_TOOLS_KEY: '', CALLI_URL: h.base, CALLI_TOKEN: 'test-token' }), /artifact file unavailable/);
    const response = await fetch(`${h.base}/api/tool-artifacts/${job.id}/${artifact.id}`, { headers: { Authorization: 'Bearer test-token' } });
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: 'artifact file unavailable' });
  } finally { await h.close(); }
});

test('CLI preserves plain-text errors from old servers or proxies in both transports', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-error-'));
  const socket = join(dir, 'error.sock');
  const local = createServer((_req, res) => res.writeHead(502).end('upstream unavailable'));
  const http = createServer((_req, res) => res.writeHead(502).end('upstream unavailable'));
  await listenPrivate(local, socket);
  await new Promise<void>(r => http.listen(0, '127.0.0.1', r));
  try {
    for (const env of [
      { CALLI_TOOLS_SOCKET: socket, CALLI_TOOLS_KEY: 'test' },
      { CALLI_TOOLS_SOCKET: '', CALLI_TOOLS_KEY: '', CALLI_URL: `http://127.0.0.1:${(http.address() as AddressInfo).port}`, CALLI_TOKEN: 'test' },
    ]) {
      await assert.rejects(exec(process.execPath, [cli, 'tools', 'search'], { env: { ...process.env, ...env } }), /upstream unavailable/);
    }
  } finally {
    await new Promise<void>(r => local.close(() => r())); await new Promise<void>(r => http.close(() => r()));
    rmSync(dir, { recursive: true, force: true });
  }
});
test('a finished task leaves the thread list for the archive; its result stays downloadable', async () => {
  const h = await setup();
  try {
    const started = await h.run('archived');
    const job = await h.done(started.id);
    assert.equal(job.state, 'succeeded');
    await wait(() => Boolean(h.store.getThread(job.threadId)?.archivedAt));
    assert.equal(h.store.getThread(h.root.id)?.archivedAt, null, 'the caller\'s own thread stays');
    const fetched = await h.runCli(['jobs', 'fetch', job.id, job.artifacts[0].id, '--output', join(h.cwd, 'after-archive.png')]);
    assert.equal(fetched.sha256, job.artifacts[0].sha256);
  } finally { await h.close(); }
});

test('a cancelled task is archived once its turn has stopped', async () => {
  const h = await setup('slow');
  try {
    const job = await h.run('cancel-archive');
    await wait(() => h.store.toolJobs.get(job.id)?.state === 'running');
    await h.call({ op: 'jobs.cancel', id: job.id });
    const threadId = h.store.toolJobs.get(job.id)!.threadId!;
    await wait(() => Boolean(h.store.getThread(threadId)?.archivedAt));
  } finally { await h.close(); }
});

test('cancellation works before dispatch and during generation; wait timeout does not cancel', async () => {
  const h = await setup('slow');
  try {
    const early = await h.run('early');
    assert.equal((await h.call({ op: 'jobs.cancel', id: early.id })).state, 'cancelled');
    const job = await h.run('late');
    await wait(() => h.store.toolJobs.get(job.id)?.state === 'running');
    await assert.rejects(h.runCli(['jobs', 'wait', job.id, '--timeout', '0.05']), (e: any) => {
      const result = JSON.parse(e.stdout);
      return e.code === 2 && result.timedOut === true && result.state === 'running';
    });
    assert.equal((await h.runCli(['jobs', 'cancel', job.id])).state, 'cancelled');
    assert.equal(h.store.toolJobs.get(early.id)?.state, 'cancelled');
  } finally { await h.close(); }
});

test('HTTP requires bearer auth and external CLI uses the same command/artifact contract', async () => {
  const h = await setup();
  try {
    assert.equal((await h.api({ op: 'tools.search' }, 'wrong')).status, 401);
    assert.equal((await h.api({ op: 'missing' })).status, 400);
    assert.equal((await h.api({ op: 'tools.run', tool: 'art-account/image.generate', input: {}, requestId: 'bad' })).status, 400);
    const input = join(h.cwd, 'request.json'); writeFileSync(input, '{"prompt":"a fox"}');
    const external = { CALLI_TOOLS_SOCKET: '', CALLI_TOOLS_KEY: '', CALLI_URL: h.base, CALLI_TOKEN: 'test-token' };
    const job = await h.runCli(['tools', 'run', 'art-account/image.generate', '--input-file', input, '--request-id', 'outside'], external);
    const finished = await h.done(job.id).catch(() => h.service.command('operator', { op: 'jobs.get', id: job.id })) as any;
    assert.equal(finished.state, 'succeeded');
    const denied = await fetch(`${h.base}/api/tool-artifacts/${job.id}/${finished.artifacts[0].id}`);
    assert.equal(denied.status, 401);
    const output = join(h.cwd, 'external.png');
    await h.runCli(['jobs', 'fetch', job.id, finished.artifacts[0].id, '--output', output], external);
    assert.ok(readFileSync(output).length);
  } finally { await h.close(); }
});

test('restart retains results and request IDs, marks unfinished jobs interrupted without replay', async () => {
  const h = await setup();
  try {
    const result = await h.done((await h.run()).id);
    await h.service.dispose();
    const first = h.store.toolJobs.get(result.id)!;
    // Simulate a crash after the terminal event was committed but before the job was finalized.
    h.store.toolJobs.save({ ...first, state: 'running', artifacts: [], summary: '' });
    const unfinished: ToolJob = { ...first, id: 'unfinished', requestId: 'unfinished', threadId: null, turnId: null, state: 'queued', artifacts: [] };
    h.store.toolJobs.save(unfinished);
    const resumed = new BorrowingService(h.store, h.manager, h.adapters, h.dir, h.socket);
    assert.equal(h.store.toolJobs.get('unfinished')?.state, 'interrupted');
    assert.equal((await resumed.command(h.root.id, { op: 'tools.run', tool: first.tool, input: first.input, requestId: first.requestId }) as any).id, first.id);
    assert.equal((await resumed.command(h.root.id, { op: 'jobs.get', id: first.id }) as any).state, 'succeeded');
    assert.equal(h.store.toolJobs.get(first.id)?.artifacts.length, 1);
    assert.equal(h.store.toolJobs.list().length, 2);
  } finally { await h.close(); }
});

test('runtime environments discard inherited scopes and administrator tokens', () => {
  const env = sessionEnvironment({ CALLI_TOOLS_KEY: 'parent', CALLI_TOOLS_SOCKET: 'parent', CALLI_TOKEN: 'admin', CALLI_TOKEN: 'admin' },
    { CALLI_TOOLS_KEY: 'child', CALLI_TOOLS_SOCKET: 'child', CALLI_BIN_DIR: '/example/bin' });
  assert.equal(env.CALLI_TOOLS_KEY, 'child');
  assert.equal(env.CALLI_TOKEN, undefined);
  assert.equal(env.CALLI_TOKEN, undefined);
  assert.ok(env.PATH?.startsWith('/example/bin:'));
});

test('signals commands expose archived provenance only to the scoped thread', async () => {
  const h = await setup();
  try {
    h.store.createTodo('m1', '事项 one', null, {});
    h.store.linkTodoThread('m1', h.root.id);
    const archive = (key: string, outcome: any, payload: Record<string, unknown> = { summary: '评审会已结束', source: { type: 'lark-calendar', ref: key } }) =>
      h.store.insertSignal(key, payload, outcome, '2026-11-01T02:00:00.000Z');
    archive('lark-calendar:evt1', { outcome: 'delivered', matterIds: ['m1'], deliveries: [{ matterId: 'm1', threadId: h.root.id }], threadId: h.root.id },
      { summary: '评审会已结束', source: { type: 'lark-calendar', ref: 'evt1' }, meeting: { event: 'ended', meetingId: 'mtg1' } });
    archive('lark-message:noise', { outcome: 'filtered', reason: 'not-a-request' });
    const shown = await h.call({ op: 'signals.show', key: 'lark-calendar:evt1' });
    assert.equal(shown.key, 'lark-calendar:evt1'); assert.equal(shown.payload.summary, '评审会已结束');
    assert.deepEqual(shown.payload.meeting, { event: 'ended', meetingId: 'mtg1' }); assert.equal(shown.material, undefined);
    assert.equal((await h.call({ op: 'signals.material', key: 'lark-calendar:evt1' })).material.error, 'lark_disabled');
    const cli = await h.runCli(['signals', 'show', 'lark-calendar:evt1']);
    assert.equal(cli.payload.summary, '评审会已结束');
    assert.equal((await h.runCli(['signals', 'material', 'lark-calendar:evt1'])).material.error, 'lark_disabled');
    const outsider = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    for (const [owner, key, status] of [
      ['operator', 'lark-calendar:evt1', 403],
      [outsider.id, 'lark-calendar:evt1', 404],
      [h.root.id, 'lark-message:noise', 404],
      [h.root.id, 'lark-calendar:absent', 404],
      [h.root.id, 'bad\nkey', 400],
    ] as const) {
      const error = await h.service.command(owner, { op: 'signals.show', key }).then(() => null, (e: any) => e);
      assert.ok(error instanceof HttpError && error.status === status, `${owner} ${key}`);
    }
    assert.equal((await h.api({ op: 'signals.show', key: 'lark-calendar:evt1' })).status, 403);
  } finally { await h.close(); }
});
