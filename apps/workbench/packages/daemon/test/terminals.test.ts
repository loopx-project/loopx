import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { TerminalManager } from '../src/terminals.js';
import { ThreadManager } from '../src/threads.js';

const TOKEN = 'test-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };

let server: Server;
let base: string;
let dataDir: string;
let workdir: string;
let store: Store;
let manager: ThreadManager;
let terminals: TerminalManager;

async function api(path: string, init: RequestInit = {}, withAuth = true): Promise<Response> {
  const headers = { 'Content-Type': 'application/json', ...(withAuth ? auth : {}), ...(init.headers ?? {}) };
  return fetch(base + path, { ...init, headers });
}

async function waitFor<T>(fn: () => Promise<T | null | undefined>, timeoutMs = 10_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - start > timeoutMs) throw new Error('timed out waiting');
    await new Promise((r) => setTimeout(r, 25));
  }
}

interface Frame {
  type: string;
  data: { data?: string; code?: number };
}

/** Reads an SSE stream until a frame matches, then cancels. Frames are the terminal's own events. */
async function readStream(root: string, path: string, until: (f: Frame) => boolean, timeoutMs = 10_000): Promise<Frame[]> {
  const res = await fetch(root + path, { headers: auth });
  assert.equal(res.status, 200, `stream ${path} failed`);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  const frames: Frame[] = [];
  let buf = '';
  const deadline = Date.now() + timeoutMs;
  try {
    while (Date.now() < deadline) {
      const { value, done } = await Promise.race([reader.read(), new Promise<never>((_, rej) => setTimeout(() => rej(new Error('timed out in stream')), deadline - Date.now()))]);
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let split: number;
      while ((split = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, split);
        buf = buf.slice(split + 2);
        const type = /^event: (.+)$/m.exec(raw)?.[1];
        const payload = /^data: (.+)$/m.exec(raw)?.[1];
        if (!type || !payload) continue; // ": connected" and ": ping" comments
        const frame: Frame = { type, data: JSON.parse(payload) };
        frames.push(frame);
        if (until(frame)) return frames;
      }
    }
    throw new Error('stream ended before the expected frame');
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

/** All terminal text a stream delivered, decoded from the base64 frames. */
const textOf = (frames: Frame[]): string =>
  frames
    .filter((f) => f.type === 'out' || f.type === 'backlog')
    .map((f) => Buffer.from(f.data.data ?? '', 'base64').toString('utf8'))
    .join('');

const create = async (cwd = workdir) => {
  const res = await api('/api/terminals', { method: 'POST', body: JSON.stringify({ cwd }) });
  return { status: res.status, body: (await res.json()) as { id: string; cwd: string; cols: number; rows: number } };
};

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-term-data-'));
  workdir = mkdtempSync(join(tmpdir(), 'calli-term-work-'));
  store = new Store(dataDir);
  // The thread manager is only here because the HTTP server needs one; these tests never use threads.
  manager = new ThreadManager(store, new Map(), [workdir], new AttachmentStore(dataDir));
  // /bin/sh keeps the tests off the user's rc files and prompts; maxTerminals 2 exercises the cap.
  terminals = new TerminalManager({ workdirs: [workdir], shell: '/bin/sh', maxTerminals: 2, exitedGraceMs: 60_000 });
  server = createHttpServer({ manager, terminals, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  server.close();
  terminals.dispose();
  await manager.shutdown();
  store.close();
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
});

test('terminals need the token; creation validates the directory', async () => {
  assert.equal((await api('/api/terminals', {}, false)).status, 401);

  const made = await create();
  assert.equal(made.status, 201);
  assert.equal(made.body.cwd, workdir);
  assert.deepEqual([made.body.cols, made.body.rows], [80, 24]);
  const list = (await (await api('/api/terminals')).json()) as Array<{ id: string }>;
  assert.deepEqual(list.map((t) => t.id), [made.body.id]);

  assert.equal((await create('/')).status, 400, 'outside the workdirs');
  assert.equal((await create(join(workdir, 'missing'))).status, 400, 'does not exist');
  assert.equal((await api('/api/terminals', { method: 'POST', body: '{}' })).status, 400, 'no cwd');

  assert.equal((await api(`/api/terminals/${made.body.id}/stream`)).status, 200);
  assert.equal((await api('/api/terminals/nope/input', { method: 'POST', body: JSON.stringify({ data: 'x' }) })).status, 404);
  assert.equal((await api('/api/terminals/nope/stream')).status, 404);
  await api(`/api/terminals/${made.body.id}`, { method: 'DELETE' });
  await waitFor(async () => ((await (await api('/api/terminals')).json()) as unknown[]).length === 0 ? true : null);
});

test('the terminal cap is enforced while others are open', async () => {
  const a = await create();
  const b = await create();
  assert.equal((await create()).status, 409);
  await api(`/api/terminals/${a.body.id}`, { method: 'DELETE' });
  await api(`/api/terminals/${b.body.id}`, { method: 'DELETE' });
  await waitFor(async () => ((await (await api('/api/terminals')).json()) as unknown[]).length === 0 ? true : null);
});

test('input runs, output streams, and the exit frame carries the code', async () => {
  const t = await create();
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'echo calli-ok\r' }) });
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'exit\r' }) });
  // The stream is opened after the fact: the backlog and the exit must still arrive.
  const frames = await readStream(base, `/api/terminals/${t.body.id}/stream`, (f) => f.type === 'exit');
  assert.ok(textOf(frames).includes('calli-ok'), 'echo output must come through');
  assert.equal(frames.at(-1)!.type, 'exit');
  assert.equal(frames.at(-1)!.data.code, 0);
  assert.deepEqual(await (await api('/api/terminals')).json(), [], 'an exited terminal leaves the list');
});

test('a stream opened before typing receives live frames', async () => {
  const t = await create();
  const reading = readStream(base, `/api/terminals/${t.body.id}/stream`, (f) => f.type === 'exit');
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'echo live-ok\r' }) });
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'exit\r' }) });
  const frames = await reading;
  assert.ok(frames[0].type === 'backlog', 'the backlog comes first');
  assert.ok(frames.filter((f) => f.type === 'out').length > 0, 'then live output');
  assert.ok(textOf(frames).includes('live-ok'));
});

test('a split multi-byte character survives the chunk boundaries', async () => {
  const t = await create();
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'echo 你好\r' }) });
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'exit\r' }) });
  const frames = await readStream(base, `/api/terminals/${t.body.id}/stream`, (f) => f.type === 'exit');
  assert.ok(textOf(frames).includes('你好'), 'CJK must round-trip base64 frames');
});

test('resize reaches the pty: stty reports the new size', async () => {
  const t = await create();
  assert.equal((await api(`/api/terminals/${t.body.id}/size`, { method: 'POST', body: JSON.stringify({ cols: 0, rows: 24 }) })).status, 400);
  assert.equal((await api(`/api/terminals/${t.body.id}/size`, { method: 'POST', body: JSON.stringify({ cols: 'wide', rows: 24 }) })).status, 400);
  assert.equal((await api(`/api/terminals/${t.body.id}/size`, { method: 'POST', body: JSON.stringify({ cols: 101, rows: 33 }) })).status, 204);
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'stty size\r' }) });
  await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'exit\r' }) });
  const frames = await readStream(base, `/api/terminals/${t.body.id}/stream`, (f) => f.type === 'exit');
  assert.ok(textOf(frames).includes('33 101'), 'rows first, then cols');
});

test('killing a terminal ends its stream, and it refuses input afterwards', async () => {
  const t = await create();
  const reading = readStream(base, `/api/terminals/${t.body.id}/stream`, (f) => f.type === 'exit');
  assert.equal((await api(`/api/terminals/${t.body.id}`, { method: 'DELETE' })).status, 204);
  const frames = await reading;
  assert.equal(frames.at(-1)!.type, 'exit');
  const after = await api(`/api/terminals/${t.body.id}/input`, { method: 'POST', body: JSON.stringify({ data: 'x' }) });
  assert.equal(after.status, 409, 'exited but still remembered');
});

test('agent login starts a fresh terminal with the adapter command', async () => {
  const registry = new AgentRegistry([{ id: 'claude', adapter: 'claude', command: ['claude'] }], null);
  registry.adapters.get('claude')!.loginCommand = () => 'echo calli-login';
  const loginServer = createHttpServer({ manager, registry, terminals, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => loginServer.listen(0, '127.0.0.1', () => r()));
  const loginBase = `http://127.0.0.1:${(loginServer.address() as AddressInfo).port}`;
  try {
    const res = await fetch(loginBase + '/api/agents/claude/login-terminal', { method: 'POST', headers: auth });
    assert.equal(res.status, 201);
    const { terminalId } = (await res.json()) as { terminalId: string };
    const frames = await readStream(loginBase, `/api/terminals/${terminalId}/stream`, (f) => textOf([f]).includes('calli-login'));
    assert.match(textOf(frames), /calli-login/);
    await fetch(loginBase + `/api/terminals/${terminalId}`, { method: 'DELETE', headers: auth });
  } finally {
    loginServer.close();
  }
});

test('an idle terminal is reaped, and its tail stays readable for a while', async () => {
  const reaper = new TerminalManager({ workdirs: [workdir], shell: '/bin/sh', idleMs: 150, sweepMs: 40, exitedGraceMs: 60_000 });
  const reaperServer = createHttpServer({ manager, terminals: reaper, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => reaperServer.listen(0, '127.0.0.1', () => r()));
  const reaperBase = `http://127.0.0.1:${(reaperServer.address() as AddressInfo).port}`;
  try {
    const res = await fetch(reaperBase + '/api/terminals', { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' }, body: JSON.stringify({ cwd: workdir }) });
    const t = (await res.json()) as { id: string };
    await waitFor(async () => {
      const list = (await (await fetch(reaperBase + '/api/terminals', { headers: auth })).json()) as unknown[];
      return list.length === 0 ? true : null;
    }, 5_000);
    // Killed by the sweep, not deleted: the exit frame is still delivered to whoever connects.
    const frames = await readStream(reaperBase, `/api/terminals/${t.id}/stream`, (f) => f.type === 'exit');
    assert.equal(frames.at(-1)!.type, 'exit');
  } finally {
    reaperServer.close();
    reaper.dispose();
  }
});
