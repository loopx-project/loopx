import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Server } from 'node:http';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { createHttpServer } from '../src/http.js';
import { LocalSessions } from '../src/local-sessions.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };
let dataDir: string;
let workdir: string;
let outside: string;
let adapters: Map<string, AgentAdapter>;
let sessionsFile: string;
let imported: Map<string, string>;
let instances: Array<{ id: string; hidden: boolean; store: string }>;
let scanner: LocalSessions;

before(() => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-local-sessions-'));
  workdir = mkdtempSync(join(tmpdir(), 'calli-local-work-'));
  outside = mkdtempSync(join(tmpdir(), 'calli-local-out-'));
  sessionsFile = join(dataDir, 'acp-sessions.json');
  process.env.FAKE_ACP_SESSIONS_FILE = sessionsFile;
  adapters = new Map<string, AgentAdapter>();
  adapters.set('claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')]));
  imported = new Map<string, string>();
  instances = [{ id: 'claude', hidden: false, store: 'claude:' }];
  scanner = new LocalSessions({
    adapters,
    workdirs: () => [workdir],
    importedSessions: () => imported,
    instances: () => instances,
  });
});

after(() => {
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
  rmSync(outside, { recursive: true, force: true });
});

/** The fake agent's session list: two in the workdir, one outside it, one in a removed directory. */
function givenSessions(): void {
  mkdirSync(join(workdir, 'app'), { recursive: true });
  imported = new Map<string, string>();
  writeFileSync(sessionsFile, JSON.stringify([
    { sessionId: 's-old', cwd: workdir, title: 'Older work', updatedAt: '2026-03-01T10:00:00.000Z' },
    { sessionId: 's-new', cwd: join(workdir, 'app'), title: 'Newer work', updatedAt: '2026-03-02T10:00:00.000Z' },
    { sessionId: 's-tied', cwd: workdir, title: 'Same second, other agent file order', updatedAt: '2026-03-02T10:00:00.000Z' },
    { sessionId: 's-out', cwd: outside, title: 'Elsewhere', updatedAt: '2026-03-03T10:00:00.000Z' },
    { sessionId: 's-gone', cwd: join(workdir, 'deleted'), title: 'Directory removed', updatedAt: '2026-03-04T10:00:00.000Z' },
    { sessionId: 's-usage', cwd: workdir, title: '/usage', updatedAt: '2026-03-05T10:00:00.000Z' },
    { sessionId: 's-nodate', cwd: workdir, title: 'No date', updatedAt: null },
  ]));
}

test('the scan lists this machine\'s sessions: newest first, only in existing allowed directories', async () => {
  givenSessions();
  await scanner.scan();
  const page = scanner.page(100, null);
  // Newest first; the two sharing a second stay apart by id, and the one with no date goes last.
  assert.deepEqual(page.sessions.map((s) => s.sessionId), ['s-tied', 's-new', 's-old', 's-nodate']);
  assert.equal(page.nextCursor, null);
  assert.ok(page.scannedAt);
  // The quota probe's leftover `/usage` sessions are not offered as conversations.
  assert.ok(!page.sessions.some((s) => s.sessionId === 's-usage'));
  // Every row names its agent instance, so importing knows where to ask.
  assert.ok(page.sessions.every((s) => s.agent === 'claude'));
});

test('pages continue at the cursor, and one timestamp shared by two rows splits them', async () => {
  givenSessions();
  await scanner.scan();
  const first = scanner.page(2, null);
  assert.deepEqual(first.sessions.map((s) => s.sessionId), ['s-tied', 's-new']);
  assert.ok(first.nextCursor);
  const second = scanner.page(2, first.nextCursor);
  assert.deepEqual(second.sessions.map((s) => s.sessionId), ['s-old', 's-nodate']);
  // The whole list fit in two pages: no cursor, no more to read.
  assert.equal(second.nextCursor, null);
});

test('a session that becomes a thread leaves the list without waiting for the next scan', async () => {
  givenSessions();
  await scanner.scan();
  imported.set('s-new', 'claude');
  const page = scanner.page(100, null);
  assert.equal(page.sessions.some((s) => s.sessionId === 's-new'), false);
  assert.equal(page.sessions.some((s) => s.sessionId === 's-old'), true);
});

test('hidden instances are skipped, and one session store is asked once', async () => {
  givenSessions();
  const asked: string[] = [];
  const stub = (id: string): unknown => ({
    capabilities: { history: true },
    listSessions: async () => {
      asked.push(id);
      return [
        { id: `${id}-1`, cwd: workdir, title: `Work of ${id}`, updatedAt: '2026-03-05T10:00:00.000Z' },
      ];
    },
  });
  const stores = new LocalSessions({
    adapters: new Map<string, AgentAdapter>([
      ['claude', stub('claude') as AgentAdapter],
      ['claude-2', stub('claude-2') as AgentAdapter],
      ['pi', stub('pi') as AgentAdapter],
    ]),
    workdirs: () => [workdir],
    importedSessions: () => new Map<string, string>(),
    // claude and claude-2 share the default store; claude-3 has an account env but is hidden.
    instances: () => [
      { id: 'claude', hidden: false, store: 'claude:' },
      { id: 'claude-2', hidden: false, store: 'claude:' },
      { id: 'claude-3', hidden: true, store: 'claude:other' },
      { id: 'pi', hidden: false, store: 'pi' },
    ],
  });
  await stores.scan();
  // One ask per store, from its first instance; the hidden instance is nobody's representative.
  assert.deepEqual(asked.sort(), ['claude', 'pi']);
  const page = stores.page(100, null);
  assert.deepEqual(page.sessions.map((s) => s.agent).sort(), ['claude', 'pi']);
  assert.equal(page.sessions.some((s) => s.agent === 'claude-2'), false);
});

test('a hub that does not scan this machine runs nothing and lists nothing', async () => {
  givenSessions();
  const off = new LocalSessions({
    adapters: new Map<string, AgentAdapter>([['claude', {
      capabilities: { history: true },
      listSessions: () => Promise.reject(new Error('must not be asked')),
    } as unknown as AgentAdapter]]),
    workdirs: () => [workdir],
    importedSessions: () => new Map<string, string>(),
    instances: () => [{ id: 'claude', hidden: false, store: 'claude:' }],
  }, { enabled: false });
  off.start();
  off.ensureFresh();
  await off.scan();
  assert.deepEqual(off.page(100, null), { sessions: [], nextCursor: null, scannedAt: null });
});

test('a request rescans only when the list has gone stale', async () => {
  givenSessions();
  let clock = Date.parse('2026-03-02T12:00:00.000Z');
  let calls = 0;
  const settle = () => new Promise((r) => setTimeout(r, 30));
  const counting = new LocalSessions({
    adapters: new Map<string, AgentAdapter>([['claude', {
      capabilities: { history: true },
      listSessions: async () => {
        calls += 1;
        return (adapters.get('claude')!.listSessions!());
      },
    } as unknown as AgentAdapter]]),
    workdirs: () => [workdir],
    importedSessions: () => new Map<string, string>(),
    instances: () => [{ id: 'claude', hidden: false, store: 'claude:' }],
    now: () => clock,
  });
  await counting.scan();
  assert.equal(calls, 1);
  counting.ensureFresh(); // the list is fresh: nothing starts
  await settle();
  assert.equal(calls, 1);
  clock += 6 * 60_000;
  counting.ensureFresh(); // stale now: one scan, shared by any request that follows
  await counting.scan();
  assert.equal(calls, 2);
});

test('a failing agent keeps its last list; the rest of the scan is unaffected', async () => {
  givenSessions();
  await scanner.scan();
  writeFileSync(sessionsFile, 'not json');
  await scanner.scan();
  assert.equal(scanner.page(100, null).sessions.length, 4);
});

test('GET /api/local-sessions serves a page and keeps the scan fresh', async () => {
  givenSessions();
  const server = createHttpServer({ manager: fakeManager(), token: TOKEN, webDir: null, version: 'test', localSessions: scanner });
  await listen(server);
  const root = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  try {
    const denied = await fetch(root + '/api/local-sessions', {});
    assert.equal(denied.status, 401);
    const res = await fetch(root + '/api/local-sessions?limit=2', { headers: auth });
    assert.equal(res.status, 200);
    const page = await res.json() as { sessions: Array<{ sessionId: string }>; nextCursor: string };
    assert.equal(page.sessions.length, 2);
    const next = await fetch(root + `/api/local-sessions?limit=100&cursor=${encodeURIComponent(page.nextCursor)}`, { headers: auth });
    const rest = await next.json() as { sessions: Array<{ sessionId: string }>; nextCursor: string | null };
    assert.equal(rest.sessions.length, 2);
    assert.equal(rest.nextCursor, null);
  } finally {
    server.close();
  }
});

test('without the scanner the route says so', async () => {
  const server = createHttpServer({ manager: fakeManager(), token: TOKEN, webDir: null, version: 'test' });
  await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/api/local-sessions`, { headers: auth });
    assert.equal(res.status, 404);
  } finally {
    server.close();
  }
});

/** The routes used here need no manager; a stub keeps the server construction honest. */
function fakeManager(): any {
  return {
    listThreads: () => [],
    subscribe: () => () => undefined,
    generalSettings: () => ({}),
    workdirsConfig: () => ({ workdirs: [] }),
    allowedWorkdirs: [],
  };
}

function listen(server: Server): Promise<void> {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
}
