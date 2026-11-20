import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { get, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { gunzipSync } from 'node:zlib';
import { AgentRegistry, defaultInstances } from '../src/agents.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore, sniffImageType, sniffSvg } from '../src/attachments.js';
import { createHttpServer, tokenMatches } from '../src/http.js';
import { LocalSessions } from '../src/local-sessions.js';
import { Store } from '../src/store.js';
import { matchTurns, ThreadManager } from '../src/threads.js';
import { contentHash } from '../src/writing.js';
import type { ThreadOutputs } from '../src/outputs.js';
import { Workdirs } from '../src/workdirs.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-0123456789';

let server: Server;
let base: string;
let dataDir: string;
let workdir: string;
let manager: ThreadManager;
let store: Store;

const auth = { Authorization: `Bearer ${TOKEN}` };

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

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'loochat-test-data-'));
  // The fake ACP agent appends the ids it is asked to delete to this file.
  process.env.FAKE_ACP_DELETE_LOG = join(dataDir, 'acp-deleted.log');
  process.env.FAKE_ACP_PERMISSION_LOG = join(dataDir, 'acp-permissions.jsonl');
  // Sessions the fake agents claim to have already. Tests fill these in; they start out empty.
  process.env.FAKE_ACP_SESSIONS_FILE = join(dataDir, 'acp-sessions.json');
  process.env.PI_CODING_AGENT_DIR = join(dataDir, 'pi-agent');
  process.env.LOOCHAT_AGY_HOME = join(dataDir, 'agy-home');
  workdir = mkdtempSync(join(tmpdir(), 'loochat-test-work-'));
  const adapters = new Map<string, AgentAdapter>();
  adapters.set('claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')]));
  adapters.set('pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')]));
  adapters.set('agy', new AgyAdapter([process.execPath, join(here, 'fixtures', 'fake-agy.mjs')]));
  store = new Store(dataDir);
  manager = new ThreadManager(store, adapters, new Workdirs([workdir], join(dataDir, 'workdirs.json'), join(dataDir, 'scratch')), new AttachmentStore(dataDir));
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const { port } = server.address() as AddressInfo;
  base = `http://127.0.0.1:${port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  store.close();
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
});

test('tokenMatches compares in constant time and rejects wrong or missing tokens', () => {
  assert.equal(tokenMatches(TOKEN, TOKEN), true);
  assert.equal(tokenMatches(TOKEN + 'x', TOKEN), false);
  assert.equal(tokenMatches(undefined, TOKEN), false);
  assert.equal(tokenMatches('', TOKEN), false);
});

test('static files: hashed assets cache immutably, everything else revalidates', async () => {
  const webDir = mkdtempSync(join(tmpdir(), 'loochat-test-web-'));
  mkdirSync(join(webDir, 'assets'));
  writeFileSync(join(webDir, 'index.html'), '<!doctype html><script type="module" src="/assets/index-abc123.js"></script>');
  writeFileSync(join(webDir, 'assets', 'index-abc123.js'), 'console.log(1)');
  writeFileSync(join(webDir, 'favicon.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
  const staticServer = createHttpServer({ manager, token: TOKEN, webDir, version: 'test' });
  await new Promise<void>((r) => staticServer.listen(0, '127.0.0.1', () => r()));
  const root = `http://127.0.0.1:${(staticServer.address() as AddressInfo).port}`;
  try {
    const revalidate = 'no-cache';
    const immutable = 'public, max-age=31536000, immutable';
    assert.equal((await fetch(root + '/')).headers.get('cache-control'), revalidate);
    assert.equal((await fetch(root + '/favicon.svg')).headers.get('cache-control'), revalidate);
    // A deep link falls back to index.html, which must revalidate like the root does.
    assert.equal((await fetch(root + '/thread/abc')).headers.get('cache-control'), revalidate);
    assert.equal((await fetch(root + '/assets/index-abc123.js')).headers.get('cache-control'), immutable);
  } finally {
    staticServer.close();
    rmSync(webDir, { recursive: true, force: true });
  }
});

test('health is open, other API routes need the bearer token', async () => {
  const health = await api('/api/health', {}, false);
  assert.equal(health.status, 200);
  const denied = await api('/api/threads', {}, false);
  assert.equal(denied.status, 401);
  const wrong = await fetch(base + '/api/threads', { headers: { Authorization: 'Bearer nope' } });
  assert.equal(wrong.status, 401);
});

test('agents list reports availability and capabilities', async () => {
  const res = await api('/api/agents');
  const agents = (await res.json()) as Array<{ id: string; available: boolean; capabilities: { resume: boolean } }>;
  assert.deepEqual(agents.map((a) => a.id).sort(), ['agy', 'claude', 'pi']);
  assert.ok(agents.every((a) => a.available && a.capabilities.resume));
});

test('agents carry their own mark, and instances of one adapter share it', async () => {
  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; icon?: string }>;
  // A path the page can draw as it is: nothing but path commands and numbers.
  for (const a of agents) assert.match(a.icon ?? '', /^[Mm][\d\s.,a-zA-Z-]+$/, a.id);
  assert.equal(new Set(agents.map((a) => a.icon)).size, agents.length, 'different agents, different marks');

  const registry = new AgentRegistry(
    [
      { id: 'claude', adapter: 'claude', command: ['claude'] },
      { id: 'claude-work', adapter: 'claude', label: 'Claude 工作号' },
    ],
    null,
  );
  const [first, second] = [registry.adapters.get('claude')!, registry.adapters.get('claude-work')!];
  assert.equal(second.label, 'Claude 工作号');
  assert.ok(first.icon && first.icon === second.icon);
});

test('config exposes allowed workdirs', async () => {
  const res = await api('/api/config');
  assert.deepEqual(await res.json(), { workdirs: [workdir], roots: [{ path: workdir, removable: false }], scratch: true });
});

test('general settings default to a new thread and persist across clients and store reopen', async () => {
  const path = '/api/general-settings';
  assert.equal((await api(path, {}, false)).status, 401);
  assert.equal((await api(path, { method: 'PUT', body: JSON.stringify({ startupPage: 'recent-thread' }) }, false)).status, 401);
  assert.deepEqual(await (await api(path)).json(), { startupPage: 'new-thread', autoArchiveDays: null, autoImportLocal: true });
  for (const value of [null, {}, { startupPage: 'unknown', autoArchiveDays: null }, { startupPage: true, autoArchiveDays: null }, { startupPage: 'new-thread', autoArchiveDays: 2 }, { startupPage: 'new-thread', autoArchiveDays: '7' }]) {
    assert.equal((await api(path, { method: 'PUT', body: JSON.stringify(value) })).status, 400);
  }
  const saved = await api(path, { method: 'PUT', body: JSON.stringify({ startupPage: 'recent-thread', autoArchiveDays: 7 }) });
  assert.equal(saved.status, 200);
  assert.deepEqual(await saved.json(), { startupPage: 'recent-thread', autoArchiveDays: 7, autoImportLocal: true });
  assert.deepEqual(await (await api(path)).json(), { startupPage: 'recent-thread', autoArchiveDays: 7, autoImportLocal: true });
  const reopened = new Store(dataDir);
  try { assert.deepEqual(reopened.hubSetting('general.settings', null), { startupPage: 'recent-thread', autoArchiveDays: 7, autoImportLocal: true }); }
  finally { reopened.close(); }
  assert.deepEqual(await (await api(path, { method: 'PUT', body: JSON.stringify({ startupPage: 'new-thread', autoArchiveDays: null }) })).json(), { startupPage: 'new-thread', autoArchiveDays: null, autoImportLocal: true });
  // The local-session switch is its own field: a save that leaves it out keeps it, a non-boolean is refused.
  const off = await api(path, { method: 'PUT', body: JSON.stringify({ startupPage: 'new-thread', autoImportLocal: false }) });
  assert.equal((await off.json()).autoImportLocal, false);
  assert.equal((await (await api(path, { method: 'PUT', body: JSON.stringify({ startupPage: 'new-thread', autoArchiveDays: 3 }) })).json()).autoImportLocal, false);
  assert.equal((await api(path, { method: 'PUT', body: JSON.stringify({ startupPage: 'new-thread', autoImportLocal: 'no' }) })).status, 400);
});

test('model preferences persist by instance and model; reads never discard unavailable choices', async () => {
  const path = '/api/draft-model-preferences';
  const edit = async (value: unknown) => {
    const response = await api(path, { method: 'PATCH', body: JSON.stringify(value) });
    assert.equal(response.status, 200);
    return response.json();
  };
  assert.equal((await api(path, {}, false)).status, 401);
  assert.equal((await api(path, { method: 'PATCH', body: JSON.stringify({ agent: 'pi', effort: 'high' }) }, false)).status, 401);
  for (const body of [null, {}, { agent: 'ghost', model: null }, { agent: 'pi' }, { agent: 'pi', effort: 9 }, { agent: 'pi', model: 'missing' }]) {
    assert.equal((await api(path, { method: 'PATCH', body: JSON.stringify(body) })).status, 400);
  }
  let choice = await edit({ agent: 'pi', model: 'fake/think', effort: 'high' });
  assert.equal(choice.effort, 'high');
  await edit({ agent: 'pi', model: 'fake/plain' });
  choice = await edit({ agent: 'pi', model: 'fake/think' });
  assert.equal(choice.effort, 'high');
  await edit({ agent: 'pi', effort: null });
  await edit({ agent: 'pi', model: 'fake/plain' });
  assert.equal((await edit({ agent: 'pi', model: 'fake/think' })).effort, null);
  // Different devices editing different instances cannot replace each other's map entries.
  await Promise.all([edit({ agent: 'pi', effort: 'high' }), edit({ agent: 'claude', model: null })]);
  const saved = await (await api(path)).json();
  assert.equal(saved.pi.effort, 'high');
  assert.ok(saved.claude);
  const reopened = new Store(dataDir);
  try { assert.deepEqual(reopened.hubSetting('draft.model.preferences', null), saved); }
  finally { reopened.close(); }
  const unavailable = { pi: { model: 'fake/missing', effort: 'high', speed: null } };
  store.saveHubSetting('draft.model.preferences', unavailable);
  assert.equal((await (await api(path)).json()).pi.model, 'fake/missing');
  assert.deepEqual(store.hubSetting('draft.model.preferences', null), unavailable, 'GET is read-only');
  // Legacy PUT still works, preserving entries it does not mention.
  await edit({ agent: 'claude', model: null });
  const put = await api(path, { method: 'PUT', body: JSON.stringify({ pi: { model: 'fake/think', effort: 'high' } }) });
  assert.equal(put.status, 200);
  assert.ok((await put.json()).claude);
});

test('only explicit composer edits update future preferences; old threads keep their settings', async () => {
  const path = '/api/draft-model-preferences';
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir, model: 'fake/think', effort: 'low' }) });
  assert.equal(created.status, 201);
  const thread = await created.json();
  const before = await (await api(path)).json();
  await api(`/api/threads/${thread.id}`);
  await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ effort: 'max' }) });
  assert.deepEqual(await (await api(path)).json(), before, 'internal edits and reads do not affect defaults');
  const edit = async (body: object) => {
    const response = await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ ...body, rememberModelChoice: true }) });
    assert.equal(response.status, 200);
    return response.json();
  };
  await edit({ effort: 'low' });
  assert.equal((await (await api(path)).json()).pi.effort, 'low');
  await edit({ model: 'fake/plain' });
  assert.equal((await edit({ model: 'fake/think' })).effort, 'low');
  await api(path, { method: 'PATCH', body: JSON.stringify({ agent: 'pi', model: 'fake/plain' }) });
  assert.equal((await (await api(`/api/threads/${thread.id}`)).json()).model, 'fake/think');
  const failed = await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ model: 'missing', rememberModelChoice: true }) });
  assert.equal(failed.status, 400);
  assert.equal((await (await api(path)).json()).pi.model, 'fake/plain');
});

test('project directory browsing is authenticated, read-only, and independent of file access', async () => {
  assert.equal((await api('/api/project-directories', {}, false)).status, 401);
  const before = await (await api('/api/config')).json();
  const res = await api('/api/project-directories');
  assert.equal(res.status, 200);
  const body = await res.json() as { path: string; directories: Array<{ name: string; path: string }>; allowed: boolean; selectable: boolean };
  assert.equal(body.path, homedir());
  assert.equal(body.allowed, false);
  assert.equal(body.selectable, false);
  assert.ok(body.directories.every((d) => Object.keys(d).sort().join(',') === 'name,path'));
  assert.equal((await api(`/api/directories?path=${encodeURIComponent(homedir())}`)).status, 403);
  assert.deepEqual(await (await api('/api/config')).json(), before);
  assert.equal((await api('/api/project-directories?path=relative')).status, 400);
  assert.equal((await api('/api/project-directories?path=/')).status, 403);
  assert.equal((await api(`/api/project-directories?path=${encodeURIComponent(workdir)}`)).status, 200);
});

test('project roots can be added and removed from the page, within limits, and are kept', async () => {
  type Config = { workdirs: string[]; roots: Array<{ path: string; removable: boolean }> };
  const add = (path: unknown) => api('/api/workdirs', { method: 'POST', body: JSON.stringify({ path }) });
  const remove = (path: string) => api(`/api/workdirs?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
  const other = mkdtempSync(join(tmpdir(), 'loochat-test-root-'));
  try {
    // Before it is added, nothing can start there.
    assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: other }) })).status, 400);
    const added = await add(other);
    assert.equal(added.status, 200);
    assert.deepEqual(((await added.json()) as Config).roots, [{ path: workdir, removable: false }, { path: other, removable: true }]);
    assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: other }) })).status, 201);
    assert.equal((await api(`/api/directory-tree?root=${encodeURIComponent(other)}`)).status, 200, 'browsable at once');
    // Kept next to the data, and read back by a hub starting later.
    assert.deepEqual(JSON.parse(readFileSync(join(dataDir, 'workdirs.json'), 'utf8')), [other]);
    assert.deepEqual(new Workdirs([workdir], join(dataDir, 'workdirs.json')).roots(), [workdir, other]);

    // Refused: relative, missing, too wide, already covered.
    for (const bad of ['relative/dir', join(other, 'missing'), '/', homedir(), join(workdir), 42]) {
      assert.equal((await add(bad)).status, 400, String(bad));
    }
    mkdirSync(join(other, 'inner'));
    assert.match(((await (await add(join(other, 'inner'))).json()) as { error: string }).error, /already allowed/);

    // The hub's own roots stay; an added one goes, and with it the right to start there.
    assert.equal((await remove(workdir)).status, 400);
    assert.deepEqual(((await (await remove(other)).json()) as Config).workdirs, [workdir]);
    assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: other }) })).status, 400);
    assert.equal((await remove(other)).status, 400, 'already gone');
  } finally {
    rmSync(other, { recursive: true, force: true });
  }
});

test('a new project is a folder in a root, a git repository with a first commit when asked', async () => {
  const create = (body: Record<string, unknown>) => api('/api/projects', { method: 'POST', body: JSON.stringify(body) });
  // In a folder of its own, so other tests listing the workdir do not see these.
  const root = join(workdir, 'projects-test');
  mkdirSync(root);
  const made = await create({ parent: root, name: '新项目', git: true });
  assert.equal(made.status, 201);
  const { path } = (await made.json()) as { path: string };
  assert.equal(path, join(root, '新项目'));
  assert.equal(execFileSync('git', ['log', '--format=%s'], { cwd: path, encoding: 'utf8' }).trim(), 'Initial commit');
  // So a thread can start in a worktree of it right away.
  const offer = (await (await api(`/api/worktree-options?cwd=${encodeURIComponent(path)}`)).json()) as { available: boolean };
  assert.equal(offer.available, true);

  const plain = (await (await create({ parent: root, name: 'plain' })).json()) as { path: string; git: boolean };
  assert.deepEqual([existsSync(plain.path), existsSync(join(plain.path, '.git')), plain.git], [true, false, false]);

  assert.equal((await create({ parent: root, name: 'plain' })).status, 409, 'taken');
  for (const name of ['', 'a/b', '..', '.hidden', 'x'.repeat(101)]) assert.equal((await create({ parent: root, name })).status, 400, name);
  assert.equal((await create({ parent: tmpdir(), name: 'outside' })).status, 400);
  assert.equal((await create({ name: 'no-parent' })).status, 400);
  rmSync(root, { recursive: true, force: true });
});

test('a thread in no project gets a scratch folder of its own', async () => {
  const res = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', scratch: true, cwd: '/ignored' }) });
  assert.equal(res.status, 201);
  const thread = (await res.json()) as { id: string; cwd: string; scratch: boolean };
  assert.equal(thread.scratch, true);
  assert.equal(dirname(thread.cwd), join(dataDir, 'scratch'));
  assert.match(thread.cwd, new RegExp(`\\d{4}-\\d{2}-\\d{2}-${thread.id.slice(0, 8)}$`));
  assert.ok(existsSync(thread.cwd));
  // It runs there like any other.
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  await finishedEvents(thread.id);
  // Not offered as a project root.
  assert.deepEqual(((await (await api('/api/config')).json()) as { workdirs: string[] }).workdirs, [workdir]);
  assert.equal(((await newThread({ agent: 'pi' })) as unknown as { scratch: boolean }).scratch, false);
});

test('threads can start in an allowed workdir or below it, and nowhere else', async () => {
  const res = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: '/' }) });
  assert.equal(res.status, 400);

  // A subdirectory of an allowed workdir is fine: that is what the directory picker offers.
  const sub = join(workdir, 'packages', 'app');
  mkdirSync(sub, { recursive: true });
  const inside = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: sub }) });
  assert.equal(inside.status, 201);
  assert.equal(((await inside.json()) as { cwd: string }).cwd, sub);
  assert.equal((await api(`/api/agents/claude/commands?cwd=${encodeURIComponent(sub)}`)).status, 200);

  // A sibling whose name merely starts the same way is not inside it.
  assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: workdir + '-other' }) })).status, 400);
  // ".." does not get out.
  assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: join(workdir, '..') }) })).status, 400);
  // Neither does a symlink that lives inside the workdir but points outside it.
  const escape = join(workdir, 'escape');
  symlinkSync(dataDir, escape);
  assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: escape }) })).status, 400);
  assert.equal((await api(`/api/directory-tree?root=${encodeURIComponent(escape)}`)).status, 403);
  assert.equal((await api(`/api/directories?path=${encodeURIComponent(escape)}`)).status, 403);

  // The tree leaves out hidden folders and node_modules.
  mkdirSync(join(workdir, '.git'));
  mkdirSync(join(workdir, 'node_modules'));
  const tree = (await (await api(`/api/directory-tree?root=${encodeURIComponent(workdir)}`)).json()) as { children: Array<{ name: string }> };
  assert.deepEqual(tree.children.map((c) => c.name), ['packages']);
  const bad = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'nope', cwd: workdir }) });
  assert.equal(bad.status, 400);
});

test('claude thread: send a turn, get streamed events, title set from first message', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: workdir }) });
  assert.equal(created.status, 201);
  const thread = (await created.json()) as { id: string; title: string };
  assert.equal(thread.title, 'New thread');

  const turn = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'list files' }) });
  assert.equal(turn.status, 202);

  const events = await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string }>;
    return list.some((e) => e.type === 'turn.done') ? list : null;
  });
  const types = events.map((e) => e.type);
  assert.equal(types[0], 'thread.created');
  assert.ok(types.includes('turn.started'));
  assert.ok(types.includes('message.delta'));
  assert.ok(types.includes('tool.end'));
  assert.equal(events.at(-1)?.type, 'thread.updated');

  const after = (await (await api(`/api/threads/${thread.id}`)).json()) as { title: string; status: string; agentSessionId: string };
  assert.equal(after.title, 'list files');
  assert.equal(after.status, 'idle');
  assert.equal(after.agentSessionId, 'acp-session-1');
});

test('a turn reads back as a trace: the last one by default, any one by id', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  type Trace = { turnId: string; status: string; agent: string; steps: Array<{ kind: string; text?: string; tool?: string; output?: string }>; totals: { tools: number }; turns: Array<{ turnId: string }> };
  for (const text of ['first ask', 'second ask']) {
    await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
    await waitFor(async () => {
      const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
      return t.status === 'idle' ? t : null;
    });
  }
  const last = (await (await api(`/api/threads/${thread.id}/trace`)).json()) as Trace;
  assert.equal(last.turns.length, 2);
  assert.equal(last.turnId, last.turns[1].turnId);
  assert.equal(last.status, 'done');
  assert.equal(last.agent, 'pi');
  assert.equal(last.steps[0].kind, 'user');
  assert.equal(last.steps[0].text, 'second ask');
  const tool = last.steps.find((s) => s.kind === 'tool');
  assert.equal(tool?.output, 'a.txt');
  assert.equal(last.totals.tools, 1);
  assert.ok(last.steps.some((s) => s.kind === 'thinking'));
  assert.match(last.steps.at(-1)?.text ?? '', /there second ask/);

  const first = (await (await api(`/api/threads/${thread.id}/trace?turn=${last.turns[0].turnId}`)).json()) as Trace;
  assert.equal(first.steps[0].text, 'first ask');
  assert.equal((await api(`/api/threads/${thread.id}/trace?turn=nope`)).status, 404);
  assert.equal((await api('/api/threads/does-not-exist/trace')).status, 404);
});

test('a second turn while one is running is refused with 409', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  const first = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow job' }) });
  assert.equal(first.status, 202);
  const second = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'again' }) });
  assert.equal(second.status, 409);
  const cancel = await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' });
  assert.equal(cancel.status, 200);
  assert.deepEqual(await cancel.json(), { dropped: [] });
  await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string }>;
    return list.some((e) => e.type === 'turn.done') ? list : null;
  });
});

test('approval: request appears as an event and the decision reaches the agent', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'please approve' }) });

  const request = await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string; data: any }>;
    return list.find((e) => e.type === 'approval.request') ?? null;
  });
  assert.equal(request.data.options.length, 2);

  // An option the agent never offered is refused, and the approval stays open.
  const bogus = await api(`/api/approvals/${request.data.approvalId}`, {
    method: 'POST',
    body: JSON.stringify({ optionId: 'allow-once' }),
  });
  assert.equal(bogus.status, 400);

  const decide = await api(`/api/approvals/${request.data.approvalId}`, {
    method: 'POST',
    body: JSON.stringify({ optionId: 'allow' }),
  });
  assert.equal(decide.status, 204);

  const finished = await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string; data: any }>;
    return list.some((e) => e.type === 'turn.done') ? list : null;
  });
  assert.ok(finished.some((e) => e.type === 'approval.resolved'));
  const reply = finished.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');
  assert.ok(reply.includes('approved:allow'));

  const again = await api(`/api/approvals/${request.data.approvalId}`, { method: 'POST', body: JSON.stringify({ optionId: 'allow' }) });
  assert.equal(again.status, 404);
});

test('SSE stream replays the backlog after a given seq and then pushes live events', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello sse' }) });
  await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string }>;
    return list.some((e) => e.type === 'turn.done') ? list : null;
  });

  const res = await api(`/api/threads/${thread.id}/stream?after=1`);
  assert.equal(res.status, 200);
  assert.match(res.headers.get('content-type') ?? '', /text\/event-stream/);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let text = '';
  const deadline = Date.now() + 5000;
  while (!text.includes('event: turn.done') && Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
  }
  assert.ok(text.includes('event: turn.started'), 'backlog should include turn.started');
  assert.ok(text.includes('event: turn.done'));
  assert.ok(!text.includes('event: thread.created'), 'events at or before the cursor must be skipped');
  await reader.cancel();
});

test('unknown thread returns 404 and malformed JSON returns 400', async () => {
  const missing = await api('/api/threads/does-not-exist');
  assert.equal(missing.status, 404);
  const bad = await fetch(base + '/api/threads', {
    method: 'POST',
    headers: { ...auth, 'Content-Type': 'application/json' },
    body: '{not json',
  });
  assert.equal(bad.status, 400);
});

test('agents report permission modes and effort levels; pi has no permission modes', async () => {
  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; capabilities: any }>;
  const claude = agents.find((a) => a.id === 'claude')!;
  const pi = agents.find((a) => a.id === 'pi')!;
  assert.deepEqual(claude.capabilities.permissionModes, ['ask', 'auto_edit', 'full_access']);
  assert.ok(claude.capabilities.effortLevels.includes('max'));
  assert.deepEqual(pi.capabilities.permissionModes, []);
});

test('model and command lists are served per agent; unknown agents are 404', async () => {
  const models = (await (await api('/api/agents/claude/models')).json()) as Array<{ id: string }>;
  assert.deepEqual(models.map((m) => m.id), ['default', 'big', 'small', 'broken']);
  const commands = (await (await api('/api/agents/claude/commands')).json()) as Array<{ name: string }>;
  assert.deepEqual(commands.map((c) => c.name), ['compact', 'review', 'goal', 'cost', 'stats', 'context']);
  assert.equal((await api('/api/agents/nope/models')).status, 404);
});

test('new threads default to auto_edit; settings are validated and stored', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: workdir }) });
  const thread = (await created.json()) as { id: string; permissionMode: string; model: string | null; effort: string | null };
  assert.equal(thread.permissionMode, 'auto_edit');
  assert.equal(thread.model, null);
  assert.equal(thread.effort, null);

  const patch = (body: unknown) => api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify(body) });
  const ok = await patch({ permissionMode: 'full_access', model: 'big', effort: 'high' });
  assert.equal(ok.status, 200);
  assert.deepEqual(
    (({ permissionMode, model, effort }) => ({ permissionMode, model, effort }))((await ok.json()) as any),
    { permissionMode: 'full_access', model: 'big', effort: 'high' },
  );

  assert.equal((await patch({ permissionMode: 'root' })).status, 400);
  assert.equal((await patch({ model: 'does-not-exist' })).status, 400);
  assert.equal((await patch({ effort: 'ludicrous' })).status, 400);

  const cleared = (await (await patch({ model: null, effort: null })).json()) as { model: null; effort: null };
  assert.equal(cleared.model, null);
  assert.equal(cleared.effort, null);
});

test('settings chosen at creation reach the agent on the first turn', async () => {
  const created = await api('/api/threads', {
    method: 'POST',
    body: JSON.stringify({ agent: 'claude', cwd: workdir, permissionMode: 'full_access', model: 'big', effort: 'high' }),
  });
  const thread = (await created.json()) as { id: string };
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'model?' }) });
  const events = await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string; data: any }>;
    return list.some((e) => e.type === 'turn.done') ? list : null;
  });
  const reply = events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');
  // The fake echoes the prompt first; the settings line is what matters here.
  assert.ok(reply.endsWith('model:big;mode:bypassPermissions;effort:high'), reply);
});

test('pi rejects a permission mode, since it has none', async () => {
  const res = await api('/api/threads', {
    method: 'POST',
    body: JSON.stringify({ agent: 'pi', cwd: workdir, permissionMode: 'ask' }),
  });
  assert.equal(res.status, 201, 'pi ignores the permission mode at creation');
  const thread = (await res.json()) as { id: string };
  const patched = await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ permissionMode: 'ask' }) });
  assert.equal(patched.status, 400);
});

test('deleting a thread removes it and its events; a running thread cannot be deleted', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow job' }) });
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'DELETE' })).status, 409);
  await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' });
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
    return t.status !== 'running' ? t : null;
  });
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'DELETE' })).status, 204);
  assert.equal((await api(`/api/threads/${thread.id}`)).status, 404);
  assert.equal((await api(`/api/threads/${thread.id}/events`)).status, 404);
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'DELETE' })).status, 404);
});

test('effort is checked against the model: a model without effort rejects it, and switching clears it', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  const patch = async (body: unknown) => {
    const res = await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify(body) });
    return { status: res.status, body: (await res.json()) as { model: string | null; effort: string | null } };
  };

  // Pi reports effort capabilities in its catalog, without selecting each model.
  assert.equal((await patch({ effort: 'max' })).body.effort, 'max');
  const toLoose = await patch({ model: 'fake/loose' });
  assert.deepEqual([toLoose.body.model, toLoose.body.effort], ['fake/loose', null], 'unsupported effort is dropped with the model change');

  assert.equal((await patch({ effort: 'max' })).status, 400);
  assert.equal((await patch({ effort: 'high' })).body.effort, 'high');

  const toPlain = await patch({ model: 'fake/plain' });
  assert.deepEqual([toPlain.body.model, toPlain.body.effort], ['fake/plain', null]);
  assert.equal((await patch({ effort: 'low' })).status, 400);

  // Model and effort in one request are checked together.
  assert.equal((await patch({ model: 'fake/loose', effort: 'max' })).status, 400);
  assert.equal((await patch({ model: null, effort: 'max' })).body.effort, 'max');
});

test('Claude validates unknown model capabilities when used; known catalog restrictions reject creation', async () => {
  for (const choice of [{ model: 'small', effort: 'high' }, { model: 'broken' }]) {
    const created = await api('/api/threads', {
      method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: workdir, ...choice }),
    });
    assert.equal(created.status, 201, 'creating a thread does not probe model access or unknown capabilities');
    const thread = (await created.json()) as { id: string };
    assert.equal((await api(`/api/threads/${thread.id}/turns`, {
      method: 'POST', body: JSON.stringify({ text: 'hi' }),
    })).status, 202);
    const failed = await waitFor(async () => {
      const events = (await (await api(`/api/threads/${thread.id}/events`)).json()) as StoredEvent[];
      return events.some(event => event.type === 'turn.error') ? events : null;
    });
    assert.equal(failed.some(event => event.type === 'message.delta'), false, 'the rejected configuration must not run a prompt');
  }
  const piPlain = await api('/api/threads', {
    method: 'POST',
    body: JSON.stringify({ agent: 'pi', cwd: workdir, model: 'fake/plain', effort: 'high' }),
  });
  assert.equal(piPlain.status, 400);
  const piThink = await api('/api/threads', {
    method: 'POST',
    body: JSON.stringify({ agent: 'pi', cwd: workdir, model: 'fake/think', effort: 'high' }),
  });
  assert.equal(piThink.status, 201);
});

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
// Only the header matters to the daemon, which reads the type from the first bytes.
const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01]).toString('base64');

test('sniffSvg recognises an SVG document by how it starts', () => {
  const yes = ['<svg xmlns="http://www.w3.org/2000/svg">', '<svg>', '\uFEFF<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n<!-- a -->\n<!-- b -->\n<SVG width="1">'];
  const no = ['', '<html><svg>', 'text then <svg>', '<svgfoo>', '<!-- never closed <svg>', '<?xml version="1.0"?><plist>'];
  for (const text of yes) assert.equal(sniffSvg(Buffer.from(text)), true, text);
  for (const text of no) assert.equal(sniffSvg(Buffer.from(text)), false, text);
  assert.equal(sniffImageType(Buffer.from(yes[0])), null, 'not one of the formats an agent can be sent');
});

test('sniffImageType recognises the four image formats and nothing else', () => {
  assert.equal(sniffImageType(Buffer.from(PNG, 'base64')), 'image/png');
  assert.equal(sniffImageType(Buffer.from(JPEG, 'base64')), 'image/jpeg');
  assert.equal(sniffImageType(Buffer.from('GIF89a......', 'latin1')), 'image/gif');
  assert.equal(sniffImageType(Buffer.from('RIFF\x00\x00\x00\x00WEBPVP8 ', 'latin1')), 'image/webp');
  assert.equal(sniffImageType(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>')), null);
  assert.equal(sniffImageType(Buffer.from('<script>alert(1)</script>')), null);
  assert.equal(sniffImageType(Buffer.alloc(0)), null);
});

async function newThread(body: Record<string, unknown>): Promise<{ id: string; title: string }> {
  const res = await api('/api/threads', { method: 'POST', body: JSON.stringify({ cwd: workdir, ...body }) });
  assert.equal(res.status, 201);
  return (await res.json()) as { id: string; title: string };
}

async function finishedEvents(threadId: string): Promise<Array<{ type: string; data: any }>> {
  return waitFor(async () => {
    const list = (await (await api(`/api/threads/${threadId}/events`)).json()) as Array<{ type: string; data: any }>;
    return list.some((e) => e.type === 'turn.done' || e.type === 'turn.error') ? list : null;
  });
}

test('a turn records prompt_sent once the session is ready and before the agent says anything', async () => {
  const thread = await newThread({ agent: 'claude' });
  assert.equal((await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) })).status, 202);
  const list = await finishedEvents(thread.id);
  const at = (match: (e: { type: string; data: any }) => boolean) => list.findIndex(match);
  const ready = at((e) => e.type === 'turn.phase' && e.data.phase === 'session_ready');
  const sent = at((e) => e.type === 'turn.phase' && e.data.phase === 'prompt_sent');
  const said = at((e) => e.type === 'message.delta' || e.type === 'message.replace');
  assert.ok(ready >= 0 && sent > ready, 'prompt_sent follows session_ready');
  assert.ok(said < 0 || said > sent, 'nothing the agent said comes before prompt_sent');
});

test('a turn can carry images: stored on disk, listed in the event, served back, sent to the agent', async () => {
  const thread = await newThread({ agent: 'claude' });
  const sent = await api(`/api/threads/${thread.id}/turns`, {
    method: 'POST',
    // The second image claims to be a PNG but is a JPEG: the bytes decide.
    body: JSON.stringify({ text: 'what is this', images: [{ data: PNG, mimeType: 'image/png' }, { data: `data:image/png;base64,${JPEG}`, mimeType: 'image/png' }] }),
  });
  assert.equal(sent.status, 202);
  const events = await finishedEvents(thread.id);
  const started = events.find((e) => e.type === 'turn.started')!;
  assert.equal(started.data.text, 'what is this');
  assert.deepEqual(started.data.images.map((i: any) => i.mimeType), ['image/png', 'image/jpeg']);
  assert.equal(JSON.stringify(started.data).includes(PNG), false, 'image bytes stay out of the event log');

  const reply = events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');
  assert.ok(reply.startsWith('images:2:image/png,image/jpeg;'), reply);

  const first = started.data.images[0];
  const file = await api(`/api/threads/${thread.id}/attachments/${first.id}`);
  assert.equal(file.status, 200);
  assert.equal(file.headers.get('content-type'), 'image/png');
  assert.equal(file.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(Buffer.from(await file.arrayBuffer()).toString('base64'), PNG);

  assert.equal((await api(`/api/threads/${thread.id}/attachments/${first.id}`, {}, false)).status, 401);
  assert.equal((await api(`/api/threads/${thread.id}/attachments/not-an-id`)).status, 404);
  assert.equal((await api(`/api/threads/${thread.id}/attachments/..%2F..%2Fhub.db`)).status, 404);
  const other = await newThread({ agent: 'claude' });
  assert.equal((await api(`/api/threads/${other.id}/attachments/${first.id}`)).status, 404, 'ids do not cross threads');

  // Deleting the thread removes its files too.
  assert.equal(existsSync(join(dataDir, 'attachments', thread.id)), true);
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'DELETE' })).status, 204);
  assert.equal(existsSync(join(dataDir, 'attachments', thread.id)), false);
});

test('an image with no text is a valid turn; an empty turn is not', async () => {
  const thread = await newThread({ agent: 'claude' });
  assert.equal((await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '  ' }) })).status, 400);
  const sent = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ images: [{ data: PNG }] }) });
  assert.equal(sent.status, 202);
  await finishedEvents(thread.id);
  const after = (await (await api(`/api/threads/${thread.id}`)).json()) as { title: string };
  assert.equal(after.title, '图片');
});

test('bad images are rejected and leave the thread idle with nothing stored', async () => {
  const thread = await newThread({ agent: 'claude' });
  const post = (images: unknown) =>
    api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'x', images }) });
  const notImage = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').toString('base64');
  assert.equal((await post([{ data: notImage, mimeType: 'image/png' }])).status, 400);
  assert.equal((await post([{ mimeType: 'image/png' }])).status, 400);
  assert.equal((await post(Array.from({ length: 7 }, () => ({ data: PNG })))).status, 400);
  // One bad image rejects the whole message, including the good one before it.
  assert.equal((await post([{ data: PNG }, { data: notImage }])).status, 400);
  const tooBig = Buffer.concat([Buffer.from(PNG, 'base64'), Buffer.alloc(5 * 1024 * 1024)]).toString('base64');
  assert.equal((await post([{ data: tooBig }])).status, 413);

  assert.equal(existsSync(join(dataDir, 'attachments', thread.id)), false);
  const state = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
  assert.equal(state.status, 'idle');
  const events = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string }>;
  assert.equal(events.some((e) => e.type === 'turn.started'), false);
});

test('a model that cannot read images refuses them; the same thread works after switching model', async () => {
  const thread = await newThread({ agent: 'pi', model: 'fake/plain' });
  const post = () => api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'look', images: [{ data: PNG }] }) });
  const refused = await post();
  assert.equal(refused.status, 400);
  assert.match(((await refused.json()) as { error: string }).error, /Plain cannot read images/);

  await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ model: 'fake/think' }) });
  assert.equal((await post()).status, 202);
  const events = await finishedEvents(thread.id);
  assert.ok(events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('').startsWith('images:1:image/png;'));

  // No override means pi's own default model, which the fake reports as "think" (reads images).
  const byDefault = await newThread({ agent: 'pi' });
  const viaDefault = await api(`/api/threads/${byDefault.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'look', images: [{ data: PNG }] }) });
  assert.equal(viaDefault.status, 202);
  await finishedEvents(byDefault.id);
});

test('pi effort levels come from the model: the default model takes only what it maps', async () => {
  const thread = await newThread({ agent: 'pi' });
  const patch = async (body: unknown) => (await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify(body) })).status;
  assert.equal(await patch({ effort: 'medium' }), 400, 'the default model (think) maps medium to null');
  assert.equal(await patch({ effort: 'max' }), 200);
  assert.equal(await patch({ model: 'fake/loose', effort: 'medium' }), 200);
  assert.equal(await patch({ effort: 'max' }), 400, 'loose has no explicit max mapping');
});

test('commands can be asked for a directory, which must be an allowed workdir', async () => {
  const ok = await api(`/api/agents/pi/commands?cwd=${encodeURIComponent(workdir)}`);
  assert.equal(ok.status, 200);
  const list = (await ok.json()) as Array<{ name: string; kind: string }>;
  // pi's built-ins that its RPC answers come first, then what pi lists for the directory.
  assert.deepEqual(list.map((c) => [c.name, c.kind]), [
    ['compact', 'command'],
    ['session', 'command'],
    ['fix-tests', 'prompt'],
    ['skill:pdf', 'skill'],
    ['deploy', 'command'],
  ]);
  assert.equal((await api(`/api/agents/pi/commands?cwd=${encodeURIComponent('/')}`)).status, 400);
  assert.equal((await api('/api/agents/claude/commands')).status, 200);
});

test('file changes from an edit arrive as diff content on the tool events', async () => {
  const thread = await newThread({ agent: 'claude' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'please edit file' }) });
  const events = await finishedEvents(thread.id);
  const withDiff = events.find((e) => e.type.startsWith('tool.') && e.data.id === 'e1' && Array.isArray(e.data.content) && e.data.content.length);
  assert.ok(withDiff, 'expected a tool event carrying diff content');
  assert.equal(withDiff!.data.content[0].type, 'diff');
  assert.match(withDiff!.data.content[0].path, /src\/app\.ts$/);
});

test('a command named only by a later update keeps its name and input once its result is stored', async () => {
  const thread = await newThread({ agent: 'claude' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'run a streamed command' }) });
  const events = (await finishedEvents(thread.id)).filter((e) => e.type.startsWith('tool.') && e.data.id === 's1');
  assert.equal(events[0].type, 'tool.start');
  assert.equal(events[0].data.title, 'Terminal', 'the call surfaced before its command was known');
  // The update that named the command is superseded by the result, but what it said is kept.
  const last = events.at(-1)!;
  assert.equal(last.type, 'tool.end');
  assert.equal(last.data.title, 'npm test -- --grep upload');
  assert.equal(last.data.kind, 'execute');
  assert.deepEqual(last.data.input, { command: 'npm test -- --grep upload', description: 'Run the upload tests' });
  assert.equal(last.data.output, '2 passing');
  // The page's first screen of the thread reads the same merged record.
  const page = (await (await api(`/api/threads/${thread.id}/events?tail`)).json()) as { events?: StoredEvent[] } | StoredEvent[];
  const replayed = (Array.isArray(page) ? page : page.events ?? []).filter((e) => e.type.startsWith('tool.') && e.data.id === 's1');
  assert.equal(replayed.at(-1)!.data.title, 'npm test -- --grep upload');
  // Opening the step reads its detail from the history projection, which has the command too.
  const start = events[0] as StoredEvent & { seq: number; turnId: string };
  const detail = await (await api(`/api/threads/${thread.id}/history/detail?turn=${encodeURIComponent(start.turnId)}&id=${start.seq}`)).json() as { input?: unknown };
  assert.deepEqual(detail.input, { command: 'npm test -- --grep upload', description: 'Run the upload tests' });
});

const patchThread = async (id: string, body: unknown) => {
  const res = await api(`/api/threads/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
  return { status: res.status, body: (await res.json()) as any };
};
const listIds = async (query = '') => ((await (await api(`/api/threads${query}`)).json()) as Array<{ id: string }>).map((t) => t.id);

test('archiving hides a thread without losing it, and restoring brings it back as it was', async () => {
  const thread = await newThread({ agent: 'claude' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'remember this' }) });
  const before = await finishedEvents(thread.id);

  const archived = await patchThread(thread.id, { archived: true });
  assert.equal(archived.status, 200);
  assert.ok(archived.body.archivedAt);
  assert.equal((await listIds()).includes(thread.id), false, 'gone from the normal list');
  assert.equal((await listIds('?archived=1')).includes(thread.id), true, 'present in the archive');

  // An archived thread keeps its history but takes no new turns.
  const events = (await (await api(`/api/threads/${thread.id}/events`)).json()) as unknown[];
  assert.ok(events.length >= before.length);
  const refused = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello?' }) });
  assert.equal(refused.status, 409);

  const restored = await patchThread(thread.id, { archived: false });
  assert.equal(restored.body.archivedAt, null);
  assert.equal((await listIds()).includes(thread.id), true);
  // It resumes the same agent session rather than starting a new conversation.
  assert.equal(restored.body.agentSessionId, archived.body.agentSessionId);
  const again = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'still there?' }) });
  assert.equal(again.status, 202);
  await waitFor(async () => {
    const list = (await (await api(`/api/threads/${thread.id}/events`)).json()) as Array<{ type: string }>;
    return list.filter((e) => e.type === 'turn.done').length >= 2 ? list : null;
  });
});

test('a running thread cannot be archived until it is stopped', async () => {
  const thread = await newThread({ agent: 'pi' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow job' }) });
  assert.equal((await patchThread(thread.id, { archived: true })).status, 409);
  await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' });
  await finishedEvents(thread.id);
  assert.equal((await patchThread(thread.id, { archived: true })).status, 200);
});

test('sections: create, file threads under them, rename, and delete without losing threads', async () => {
  const make = await api('/api/sections', { method: 'POST', body: JSON.stringify({ name: '  支付重构 ' }) });
  assert.equal(make.status, 201);
  const section = (await make.json()) as { id: string; name: string; position: number };
  assert.equal(section.name, '支付重构');
  const second = (await (await api('/api/sections', { method: 'POST', body: JSON.stringify({ name: '调研' }) })).json()) as { id: string; position: number };
  assert.ok(second.position > section.position, 'new sections go last');
  assert.equal((await api('/api/sections', { method: 'POST', body: JSON.stringify({ name: '   ' }) })).status, 400);
  assert.equal((await api('/api/sections', { method: 'POST', body: JSON.stringify({ name: 'x'.repeat(41) }) })).status, 400);

  const thread = await newThread({ agent: 'claude' });
  assert.equal((await patchThread(thread.id, { sectionId: section.id })).body.sectionId, section.id);
  assert.equal((await patchThread(thread.id, { sectionId: 'no-such-section' })).status, 400);

  const renamed = await api(`/api/sections/${section.id}`, { method: 'PATCH', body: JSON.stringify({ name: '支付重构二期' }) });
  assert.equal(((await renamed.json()) as { name: string }).name, '支付重构二期');
  const names = ((await (await api('/api/sections')).json()) as Array<{ name: string }>).map((x) => x.name);
  assert.deepEqual(names.slice(-2), ['支付重构二期', '调研']);

  assert.equal((await api(`/api/sections/${section.id}`, { method: 'DELETE' })).status, 204);
  assert.equal((await api(`/api/sections/${section.id}`, { method: 'DELETE' })).status, 404);
  const after = (await (await api(`/api/threads/${thread.id}`)).json()) as { sectionId: string | null };
  assert.equal(after.sectionId, null, 'the thread survives and is unfiled');
  assert.equal((await patchThread(thread.id, { sectionId: null })).status, 200);
});

test('filing, pinning or renaming a thread does not move it up the recent list', async () => {
  const older = await newThread({ agent: 'claude' });
  await api(`/api/threads/${older.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'first' }) });
  await finishedEvents(older.id);
  await new Promise((r) => setTimeout(r, 15));
  const newer = await newThread({ agent: 'claude' });
  await api(`/api/threads/${newer.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'second' }) });
  await finishedEvents(newer.id);

  const order = async () => (await listIds()).filter((id) => id === older.id || id === newer.id);
  assert.deepEqual(await order(), [newer.id, older.id]);
  await patchThread(older.id, { title: 'renamed', model: 'big' });
  assert.deepEqual(await order(), [newer.id, older.id], 'settings and renames keep the order');
  // A new message does move it up.
  await api(`/api/threads/${older.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'again' }) });
  assert.deepEqual(await order(), [older.id, newer.id]);
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${older.id}`)).json()) as { status: string };
    return t.status === 'idle' ? t : null;
  });
});

test('threads carry a terminal resume command once the agent session exists', async () => {
  const thread = await newThread({ agent: 'pi' });
  const fresh = (await (await api(`/api/threads/${thread.id}`)).json()) as { resumeCommand: string | null };
  assert.equal(fresh.resumeCommand, null);
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(thread.id);
  const after = (await (await api(`/api/threads/${thread.id}`)).json()) as { resumeCommand: string };
  assert.equal(after.resumeCommand, `cd ${workdir} && pi --session pi-session-1`);

  const claude = await newThread({ agent: 'claude' });
  await api(`/api/threads/${claude.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(claude.id);
  const view = (await (await api(`/api/threads/${claude.id}`)).json()) as { resumeCommand: string };
  assert.equal(view.resumeCommand, `cd ${workdir} && claude --resume acp-session-1`);
});

test('deleting for good can also remove the agent session, where the agent allows it', async () => {
  const log = process.env.FAKE_ACP_DELETE_LOG!;
  const deleted = () => (existsSync(log) ? readFileSync(log, 'utf8').split('\n').filter(Boolean) : []);

  const keep = await newThread({ agent: 'claude' });
  await api(`/api/threads/${keep.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(keep.id);
  const countBefore = deleted().length;
  assert.equal((await api(`/api/threads/${keep.id}`, { method: 'DELETE' })).status, 204);
  assert.equal(deleted().length, countBefore, 'by default the agent session is left alone');

  const both = await newThread({ agent: 'claude' });
  await api(`/api/threads/${both.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(both.id);
  assert.equal((await api(`/api/threads/${both.id}?agentSession=1`, { method: 'DELETE' })).status, 204);
  assert.equal(deleted().length, countBefore + 1);
  assert.equal(deleted().at(-1), 'acp-session-1');

  // pi cannot delete sessions: the request is refused and the thread is still there.
  const pi = await newThread({ agent: 'pi' });
  await api(`/api/threads/${pi.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(pi.id);
  assert.equal((await api(`/api/threads/${pi.id}?agentSession=1`, { method: 'DELETE' })).status, 400);
  assert.equal((await api(`/api/threads/${pi.id}`)).status, 200);
  assert.equal((await api(`/api/threads/${pi.id}`, { method: 'DELETE' })).status, 204);

  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; capabilities: { deleteSession: boolean } }>;
  assert.deepEqual(agents.map((a) => [a.id, a.capabilities.deleteSession]).sort(), [['agy', false], ['claude', true], ['pi', false]]);
});

test('threads report their directory with symlinks resolved, as agents report file paths', async () => {
  const thread = await newThread({ agent: 'claude' });
  const view = (await (await api(`/api/threads/${thread.id}`)).json()) as { cwd: string; cwdReal: string };
  assert.equal(view.cwd, workdir);
  assert.equal(view.cwdReal, realpathSync(workdir));

  // A workdir reached through a symlink keeps its own spelling and also gets the real one.
  const real = join(dataDir, 'real-project');
  const link = join(dataDir, 'linked-project');
  mkdirSync(real);
  symlinkSync(real, link);
  const adapters = new Map<string, AgentAdapter>([['claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')])]]);
  const linked = new ThreadManager(store, adapters, [link], new AttachmentStore(dataDir));
  const made = await linked.createThread({ agent: 'claude', cwd: link });
  assert.equal(made.cwd, link);
  assert.equal(made.cwdReal, realpathSync(real));
});

const events = async (threadId: string) =>
  (await (await api(`/api/threads/${threadId}/events`)).json()) as Array<{ seq: number; type: string; turnId: string | null; data: any }>;

test('import: Codex reads just the chosen session and rechecks its directory and duplicate status', async () => {
  const file = join(dataDir, 'codex-import-sessions.json');
  const log = join(dataDir, 'codex-import-rpc.jsonl');
  const elsewhere = mkdtempSync(join(tmpdir(), 'loochat-test-codex-elsewhere-'));
  writeFileSync(file, JSON.stringify([
    { id: 'hist-indexed', cwd: workdir, name: 'Indexed session' },
    { id: 'hist-outside', cwd: elsewhere, name: 'Outside' },
    { id: 'hist-home', cwd: homedir(), name: 'In home' },
    { id: 'hist-removed', cwd: join(workdir, 'missing-project'), name: 'Removed' },
    { id: 'hist-subagent', cwd: workdir, name: 'Scratch', source: { subAgent: 'review' } },
  ]));
  const adapter = new CodexAppServerAdapter([process.execPath, join(here, 'fixtures', 'fake-codex.mjs')], {
    FAKE_CODEX_SESSIONS_FILE: file, FAKE_CODEX_RPC_LOG: log,
  });
  const m = new ThreadManager(store, new Map([['codex', adapter]]), [workdir], new AttachmentStore(dataDir));
  const srv = createHttpServer({ manager: m, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r));
  const root = `http://127.0.0.1:${(srv.address() as AddressInfo).port}`;
  const requestImport = (sessionId: string) => fetch(root + '/api/threads/import', {
    method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent: 'codex', sessionId }),
  });
  try {
    for (const id of ['hist-removed', 'hist-missing', 'hist-subagent']) {
      assert.equal((await requestImport(id)).status, 404, id);
    }
    // The home directory itself is too wide to become a workdir, so its session cannot come in.
    assert.equal((await requestImport('hist-home')).status, 400);
    assert.ok(!m.workdirsConfig().workdirs.includes(homedir()));
    const imported = await requestImport('hist-indexed');
    assert.equal(imported.status, 201);
    const thread = await imported.json() as { id: string; title: string; agentSessionId: string };
    assert.equal(thread.title, 'Indexed session');
    assert.equal(thread.agentSessionId, 'hist-indexed');
    assert.equal((await events(thread.id)).filter((e) => e.type === 'turn.started').length, 2);
    assert.equal((await requestImport('hist-indexed')).status, 409);
    const calls = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    assert.equal(calls.filter((c) => c.method === 'thread/list').length, 0);
    assert.equal(calls.filter((c) => c.method === 'thread/turns/list').length, 1, 'only the valid import reads history');

    // A session from outside the workdirs comes in when the person imports it, and brings its directory.
    const outside = await requestImport('hist-outside');
    assert.equal(outside.status, 201);
    assert.equal(((await outside.json()) as { cwd: string }).cwd, elsewhere);
    assert.ok(m.workdirsConfig().workdirs.includes(elsewhere));
    // An import nobody clicked (the LoopX Goal sync) never widens the workdirs.
    writeFileSync(file, JSON.stringify([{ id: 'hist-quiet', cwd: join(dataDir), name: 'Quiet' }]));
    await assert.rejects(() => m.importSession({ agent: 'codex', sessionId: 'hist-quiet' }), /outside the allowed workdirs/);
    assert.ok(!m.workdirsConfig().workdirs.includes(dataDir));
  } finally {
    srv.close();
    await m.shutdown();
    rmSync(elsewhere, { recursive: true, force: true });
  }
});

test('import: a failed import leaves the workdirs as they were; hidden directories are not offered', async () => {
  const base = mkdtempSync(join(tmpdir(), 'loochat-test-import-fail-'));
  const project = join(base, 'project');
  const child = join(project, 'child');
  const hidden = join(base, 'hidden');
  mkdirSync(child, { recursive: true });
  mkdirSync(hidden);
  const sessions = [
    { id: 'in-project', cwd: project, title: 'Unreadable history', updatedAt: '2026-03-02T10:00:00.000Z' },
    { id: 'in-hidden', cwd: hidden, title: 'Kept out', updatedAt: '2026-03-01T10:00:00.000Z' },
  ];
  const adapter = {
    id: 'stub', capabilities: { history: true },
    listSessions: async () => sessions,
    loadHistory: async () => { throw new Error('history unreadable'); },
  } as unknown as AgentAdapter;
  const dirs = new Workdirs([workdir], join(base, 'workdirs.json'));
  dirs.add(child);
  const m = new ThreadManager(store, new Map([['stub', adapter]]), dirs, new AttachmentStore(dataDir));
  m.hiddenSessionDirs = [hidden];
  try {
    // The per-agent list follows the sidebar's rule: the hidden directory's session is not offered.
    assert.deepEqual((await m.listAgentSessions('stub')).map((s) => s.id), ['in-project']);
    // Its history cannot be read: no thread, and the added child is not replaced by its parent.
    await assert.rejects(() => m.importSession({ agent: 'stub', sessionId: 'in-project', addDirectory: true }), /history unreadable/);
    assert.deepEqual(m.workdirsConfig().workdirs, [workdir, child]);
    assert.deepEqual(JSON.parse(readFileSync(join(base, 'workdirs.json'), 'utf8')), [child]);
    assert.equal(store.findThreadByAgentSession('stub', 'in-project'), null);
  } finally {
    await m.shutdown();
    rmSync(base, { recursive: true, force: true });
  }
});

test('import: sessions the agent already has are listed from every existing directory', async () => {
  const outside = mkdtempSync(join(tmpdir(), 'loochat-test-outside-'));
  try {
    writeFileSync(
      process.env.FAKE_ACP_SESSIONS_FILE!,
      JSON.stringify([
        { sessionId: 'hist-a', cwd: workdir, title: 'Fix the login bug', updatedAt: '2026-03-01T10:00:00.000Z' },
        { sessionId: 'hist-b', cwd: join(workdir, 'packages', 'app'), title: 'Tidy imports', updatedAt: '2026-03-02T10:00:00.000Z' },
        { sessionId: 'hist-elsewhere', cwd: outside, title: 'Not ours', updatedAt: '2026-03-03T10:00:00.000Z' },
        { sessionId: 'hist-gone', cwd: join(workdir, 'deleted-since'), title: 'Directory removed', updatedAt: null },
        { sessionId: 'hist-c', cwd: workdir, title: '', updatedAt: null },
      ]),
    );
    const res = await api('/api/agents/claude/sessions');
    assert.equal(res.status, 200);
    const list = (await res.json()) as Array<{ id: string; cwd: string; title: string; threadId: string | null }>;
    // Five sessions over three pages; the one whose directory is gone is left out, the one outside
    // the workdirs is offered (importing it adds its directory). Newest first, and the one with no date last.
    assert.deepEqual(list.map((s) => s.id), ['hist-elsewhere', 'hist-b', 'hist-a', 'hist-c']);
    assert.ok(list.every((s) => s.threadId === null));

    assert.equal((await api('/api/agents/nope/sessions')).status, 404);
    assert.equal((await api('/api/agents/claude/sessions', {}, false)).status, 401);

    assert.equal((await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'claude', sessionId: 'no-such' }) })).status, 404);
    assert.equal((await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'nope', sessionId: 'hist-a' }) })).status, 400);
    assert.equal((await api('/api/threads/import', { method: 'POST', body: JSON.stringify({}) })).status, 400);
  } finally {
    rmSync(outside, { recursive: true, force: true });
  }
});

test('import: a claude session becomes a thread with its past turns, and carries on in the same session', async () => {
  const res = await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'claude', sessionId: 'hist-a' }) });
  assert.equal(res.status, 201);
  const thread = (await res.json()) as { id: string; title: string; cwd: string; agentSessionId: string; status: string; resumeCommand: string };
  assert.equal(thread.title, 'Fix the login bug');
  assert.equal(thread.cwd, workdir);
  assert.equal(thread.agentSessionId, 'hist-a');
  assert.equal(thread.status, 'idle');
  assert.equal(thread.resumeCommand, `cd ${workdir} && claude --resume hist-a`);

  const log = await events(thread.id);
  assert.deepEqual(
    log.map((e) => e.type),
    [
      'thread.created',
      'turn.started', 'reasoning.delta', 'tool.start', 'tool.end', 'message.delta', 'turn.done',
      'turn.started', 'message.delta', 'turn.done',
    ],
  );
  assert.deepEqual(log.map((e) => e.seq), log.map((_, i) => i + 1));
  const started = log.filter((e) => e.type === 'turn.started');
  assert.equal(started[0].data.text, 'first question');
  assert.equal(started[0].data.imported, true);
  assert.notEqual(started[0].turnId, started[1].turnId, 'each past turn has its own id');
  assert.equal(log[3].turnId, started[0].turnId);

  // The picture in the second turn is stored like an uploaded one and served back.
  const [image] = started[1].data.images as Array<{ id: string; mimeType: string }>;
  assert.equal(image.mimeType, 'image/png');
  const served = await api(`/api/threads/${thread.id}/attachments/${image.id}`);
  assert.equal(served.status, 200);
  assert.equal(served.headers.get('content-type'), 'image/png');
  await served.arrayBuffer();

  // Listed as a thread now, and a second import is refused.
  const list = (await (await api('/api/agents/claude/sessions')).json()) as Array<{ id: string; threadId: string | null }>;
  assert.equal(list.find((s) => s.id === 'hist-a')?.threadId, thread.id);
  assert.equal(list.find((s) => s.id === 'hist-b')?.threadId, null);
  const again = await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'claude', sessionId: 'hist-a' }) });
  assert.equal(again.status, 409);

  // Still refused once archived: the thread exists, it is just hidden.
  await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ archived: true }) });
  assert.equal((await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'claude', sessionId: 'hist-a' }) })).status, 409);
  await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ archived: false }) });

  // A new message goes to the same agent session; the replay on load is not shown twice.
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  const after = await waitFor(async () => {
    const list = await events(thread.id);
    return list.filter((e) => e.type === 'turn.done').length === 3 ? list : null;
  });
  assert.equal(after.at(-1)!.data.imported, undefined, 'only past turns are marked as imported');
  const answers = after.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('|');
  assert.match(answers, /^first answer\|second answer\|/);
  assert.equal(answers.split('first answer').length, 2, 'history appears once');
  const view = (await (await api(`/api/threads/${thread.id}`)).json()) as { agentSessionId: string; title: string };
  assert.equal(view.agentSessionId, 'hist-a');
  assert.equal(view.title, 'Fix the login bug', 'the first new message does not rename an imported thread');
});

test('import: two requests for the same session at once make one thread', async () => {
  const body = JSON.stringify({ agent: 'claude', sessionId: 'hist-b' });
  const [a, b] = await Promise.all([
    api('/api/threads/import', { method: 'POST', body }),
    api('/api/threads/import', { method: 'POST', body }),
  ]);
  assert.deepEqual([a.status, b.status].sort(), [201, 409]);
  const made = (await (a.status === 201 ? a : b).json()) as { id: string; cwd: string };
  assert.equal(made.cwd, join(workdir, 'packages', 'app'));
  const all = (await (await api('/api/threads')).json()) as Array<{ agentSessionId: string | null }>;
  assert.equal(all.filter((t) => t.agentSessionId === 'hist-b').length, 1);
});

test('import: a session with no title is named after its first message', async () => {
  const res = await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'claude', sessionId: 'hist-c' }) });
  assert.equal(res.status, 201);
  assert.equal(((await res.json()) as { title: string }).title, 'first question');
});

test('local sessions: listed as external rows, and an import turns one into a normal thread', async () => {
  // A second server over the same manager, carrying the local-session scanner; the page's list
  // comes from its cache, not from starting an agent per request.
  const adapters = new Map<string, AgentAdapter>();
  adapters.set('claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')]));
  const scanner = new LocalSessions({
    adapters,
    workdirs: () => [workdir],
    importedSessions: () => store.agentSessionOwners(),
    instances: () => [{ id: 'claude', hidden: false, store: 'claude:' }],
  });
  await scanner.scan();
  const srv = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test', localSessions: scanner });
  try {
    await new Promise<void>((r) => srv.listen(0, '127.0.0.1', () => r()));
    const root = `http://127.0.0.1:${(srv.address() as AddressInfo).port}`;
    const call = async (path: string) => fetch(root + path, { headers: auth });

    // hist-b is a thread already (imported above); hist-a became one too. hist-elsewhere is
    // outside the workdirs. What is left of the fake agent's sessions: the undated hist-c… which
    // the import just above also took. Everything the scan began with is accounted for.
    const page = await (await call('/api/local-sessions?limit=100')).json() as { sessions: Array<{ sessionId: string; agent: string }>; nextCursor: string | null };
    for (const row of page.sessions) {
      assert.equal(store.findThreadByAgentSession(row.agent, row.sessionId), null, `${row.sessionId} is not a thread`);
      assert.notEqual(row.sessionId, 'hist-elsewhere', 'outside the allowed directories');
    }

    // A fresh session shows up, imports through the same endpoint the page uses, and leaves the
    // external list for the threads list.
    writeFileSync(process.env.FAKE_ACP_SESSIONS_FILE!, JSON.stringify([
      { sessionId: 'hist-external', cwd: workdir, title: 'From a terminal', updatedAt: '2026-03-05T09:00:00.000Z' },
    ]));
    await scanner.scan();
    const listed = await (await call('/api/local-sessions')).json() as { sessions: Array<{ sessionId: string }> };
    assert.deepEqual(listed.sessions.map((s) => s.sessionId), ['hist-external']);
    const made = await fetch(`${root}/api/threads/import`, {
      method: 'POST', headers: auth, body: JSON.stringify({ agent: 'claude', sessionId: 'hist-external' }),
    });
    assert.equal(made.status, 201);
    const thread = await made.json() as { id: string };
    const after = await (await call('/api/local-sessions')).json() as { sessions: Array<{ sessionId: string }> };
    assert.deepEqual(after.sessions, [], 'no longer external');
    const threads = await (await call('/api/threads')).json() as Array<{ id: string }>;
    assert.ok(threads.some((t) => t.id === thread.id), 'listed as the thread it became');
  } finally {
    srv.close();
  }
});

test('import: pi sessions are found from its files and imported with tool calls and pictures', async () => {
  const dir = join(process.env.PI_CODING_AGENT_DIR!, 'sessions', '--work--');
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, 'one.jsonl'),
    [{ type: 'session', id: 'hist-pi', cwd: workdir }, { type: 'message', message: { role: 'user', content: 'run the tests' } }]
      .map((l) => JSON.stringify(l))
      .join('\n'),
  );
  writeFileSync(join(dir, 'two.jsonl'), JSON.stringify({ type: 'session', id: 'hist-pi-outside', cwd: '/' }));

  const list = (await (await api('/api/agents/pi/sessions')).json()) as Array<{ id: string; title: string; threadId: string | null }>;
  assert.deepEqual(list.map((s) => [s.id, s.title, s.threadId]), [['hist-pi', 'run the tests', null]]);

  const res = await api('/api/threads/import', { method: 'POST', body: JSON.stringify({ agent: 'pi', sessionId: 'hist-pi' }) });
  assert.equal(res.status, 201);
  const thread = (await res.json()) as { id: string; agent: string; title: string; resumeCommand: string };
  assert.equal(thread.agent, 'pi');
  assert.equal(thread.title, 'run the tests');
  assert.equal(thread.resumeCommand, `cd ${workdir} && pi --session hist-pi`);

  const log = await events(thread.id);
  assert.deepEqual(
    log.map((e) => e.type),
    [
      'thread.created',
      'turn.started', 'reasoning.delta', 'tool.start', 'tool.end', 'message.delta', 'turn.done',
      'turn.started', 'message.delta', 'turn.done',
    ],
  );
  assert.equal(log[4].data.output, '12 passed');
  assert.equal(log[7].data.images.length, 1);
});

test('fork: the copy has the same history and pictures, its own agent session, and goes its own way', async () => {
  const thread = await newThread({ agent: 'claude', model: 'big', effort: 'high', permissionMode: 'ask' });
  assert.equal((await api(`/api/threads/${thread.id}/fork`, { method: 'POST' })).status, 400, 'nothing to fork before the first message');

  const section = (await (await api('/api/sections', { method: 'POST', body: JSON.stringify({ name: 'Forks' }) })).json()) as { id: string };
  await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ sectionId: section.id }) });
  await api(`/api/threads/${thread.id}/turns`, {
    method: 'POST',
    body: JSON.stringify({ text: 'look at this', images: [{ data: PNG, mimeType: 'image/png' }] }),
  });
  const original = await finishedEvents(thread.id);

  const res = await api(`/api/threads/${thread.id}/fork`, { method: 'POST' });
  assert.equal(res.status, 201);
  const copy = (await res.json()) as Record<string, any>;
  const source = (await (await api(`/api/threads/${thread.id}`)).json()) as Record<string, any>;
  assert.notEqual(copy.id, thread.id);
  assert.equal(copy.title, `${source.title}（分叉）`);
  assert.equal(copy.agentSessionId, `${source.agentSessionId}-fork`);
  for (const key of ['agent', 'cwd', 'model', 'effort', 'permissionMode', 'sectionId']) {
    assert.equal(copy[key], source[key], `${key} carries over`);
  }
  assert.equal(copy.pinned, false);
  assert.equal(copy.forkedFrom, thread.id);
  assert.equal(source.forkedFrom, null);

  const copied = await events(copy.id);
  assert.deepEqual(copied.slice(0, original.length).map((e) => [e.type, e.data]), original.map((e) => [e.type, e.data]));
  const image = copied.find((e) => e.type === 'turn.started')!.data.images[0];
  const served = await api(`/api/threads/${copy.id}/attachments/${image.id}`);
  assert.equal(served.status, 200);
  await served.arrayBuffer();

  // A message to the copy leaves the original as it was.
  await api(`/api/threads/${copy.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'only in the fork' }) });
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${copy.id}`)).json()) as { status: string };
    return t.status === 'idle' ? t : null;
  });
  assert.equal((await events(thread.id)).length, original.length);
  assert.ok((await events(copy.id)).some((e) => e.type === 'turn.started' && e.data.text === 'only in the fork'));

  // Deleting the copy takes its pictures with it and leaves the original's alone.
  assert.equal((await api(`/api/threads/${copy.id}`, { method: 'DELETE' })).status, 204);
  const mine = original.find((e) => e.type === 'turn.started')!.data.images[0];
  const still = await api(`/api/threads/${thread.id}/attachments/${mine.id}`);
  assert.equal(still.status, 200);
  await still.arrayBuffer();
  await api(`/api/sections/${section.id}`, { method: 'DELETE' });
});

test('fork: refused while a turn is running, and a failure on the agent side leaves no thread behind', async () => {
  const busy = await newThread({ agent: 'claude' });
  await api(`/api/threads/${busy.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(busy.id);
  await api(`/api/threads/${busy.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow' }) });
  assert.equal((await api(`/api/threads/${busy.id}/fork`, { method: 'POST' })).status, 409);
  await api(`/api/threads/${busy.id}/cancel`, { method: 'POST' });
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${busy.id}`)).json()) as { status: string };
    return t.status === 'idle' ? t : null;
  });

  const before = ((await (await api('/api/threads')).json()) as unknown[]).length;
  const stuck = await manager.createThread({ agent: 'claude', cwd: workdir });
  store.updateThread(stuck.id, { agentSessionId: 'unforkable' });
  const res = await api(`/api/threads/${stuck.id}/fork`, { method: 'POST' });
  assert.equal(res.status, 502);
  assert.match(((await res.json()) as { error: string }).error, /cannot fork/);
  assert.equal(((await (await api('/api/threads')).json()) as unknown[]).length, before + 1, 'only the thread made for this test was added');

  assert.equal((await api('/api/threads/no-such-thread/fork', { method: 'POST' })).status, 404);
});

test('fork works for pi too, and agents say whether they can import and fork', async () => {
  const thread = await newThread({ agent: 'pi' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(thread.id);
  const res = await api(`/api/threads/${thread.id}/fork`, { method: 'POST' });
  assert.equal(res.status, 201);
  assert.equal(((await res.json()) as { agentSessionId: string }).agentSessionId, 'pi-session-1-clone');

  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; capabilities: { history: boolean; fork: boolean } }>;
  assert.deepEqual(agents.map((a) => [a.id, a.capabilities.history, a.capabilities.fork]).sort(), [['agy', false, false], ['claude', true, true], ['pi', true, true]]);
});

test('fork at a turn: the copy ends where that turn ended, on the hub and on the agent', async () => {
  const turnsOf = async (id: string) => (await events(id)).filter((e) => e.type === 'turn.started');
  const ask = async (id: string, text: string) => {
    await api(`/api/threads/${id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
    await waitFor(async () => {
      const t = (await (await api(`/api/threads/${id}`)).json()) as { status: string };
      return t.status === 'idle' ? t : null;
    });
  };
  const forkAt = (id: string, turnId: string) => api(`/api/threads/${id}/fork`, { method: 'POST', body: JSON.stringify({ turnId }) });

  // Three turns here; the agent's own history (the fake's canned one) knows the first two.
  const thread = await newThread({ agent: 'pi' });
  for (const text of ['run the tests', 'and this picture?', 'never reached the agent']) await ask(thread.id, text);
  store.updateThread(thread.id, { agentSessionId: 'hist-forkat' });
  const [first, second, third] = await turnsOf(thread.id);

  // After the first turn: pi forks from the second turn's message, the hub copies the log up to it.
  const res = await forkAt(thread.id, first.turnId!);
  assert.equal(res.status, 201);
  const copy = (await res.json()) as Record<string, any>;
  assert.equal(copy.agentSessionId, 'hist-forkat-fork-e2');
  assert.equal(copy.forkedFrom, thread.id);
  const copied = await events(copy.id);
  assert.deepEqual(copied.filter((e) => e.type === 'turn.started').map((e) => e.data.text), ['run the tests']);
  // Everything before the next turn, nothing of it; then only the note that this thread is a fork.
  const before = (await events(thread.id)).filter((e) => e.seq < second.seq);
  assert.deepEqual(copied.slice(0, before.length).map((e) => [e.seq, e.type, e.data]), before.map((e) => [e.seq, e.type, e.data]));
  assert.deepEqual(copied.slice(before.length).map((e) => e.type), ['thread.updated']);
  assert.equal(copied.some((e) => e.turnId === first.turnId && e.type === 'turn.done'), true);

  // The copy goes its own way from there, and the original keeps all three turns.
  await ask(copy.id, 'a different second turn');
  assert.deepEqual((await turnsOf(copy.id)).map((e) => e.data.text), ['run the tests', 'a different second turn']);
  assert.equal((await turnsOf(thread.id)).length, 3);

  // The agent has nothing after the second turn, so its whole conversation is the copy; the hub
  // still leaves out the turn that came after.
  const whole = (await (await forkAt(thread.id, second.turnId!)).json()) as Record<string, any>;
  assert.equal(whole.agentSessionId, 'hist-forkat-clone');
  assert.deepEqual((await turnsOf(whole.id)).map((e) => e.data.text), ['run the tests', 'and this picture?']);

  // The last turn is the whole thread: no history is read for it.
  const last = (await (await forkAt(thread.id, third.turnId!)).json()) as Record<string, any>;
  assert.equal(last.agentSessionId, 'hist-forkat-clone');
  assert.equal((await turnsOf(last.id)).length, 3);

  assert.equal((await forkAt(thread.id, 'no-such-turn')).status, 404);

  // While a new turn is still running, a finished history boundary is safe to copy; the unfinished
  // current turn is not.
  const running = (await (await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow while forking' }) })).json()) as { turnId: string };
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
    return t.status === 'running' ? t : null;
  });
  const pi = (manager as unknown as { adapters: Map<string, AgentAdapter> }).adapters.get('pi')!;
  const loadHistory = pi.loadHistory!;
  let historyReads = 0;
  pi.loadHistory = async (...args) => {
    const history = await loadHistory.call(pi, ...args);
    return ++historyReads === 1 ? [] : history.slice(0, 1);
  };
  try {
    const historyFork = await forkAt(thread.id, first.turnId!);
    assert.equal(historyFork.status, 201);
    assert.equal(((await historyFork.json()) as Record<string, any>).agentSessionId, 'hist-forkat-fork-e2');
    assert.equal(historyReads, 2, 'an active writer history snapshot that is one turn behind is retried');
  } finally {
    pi.loadHistory = loadHistory;
  }
  assert.equal((await api(`/api/threads/${thread.id}/fork`, { method: 'POST' })).status, 409);
  assert.equal((await forkAt(thread.id, running.turnId)).status, 409);
  await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' });
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
    return t.status === 'idle' ? t : null;
  });

  // A turn the user stopped is no place to end a copy, wherever it stands.
  const stopping = await newThread({ agent: 'pi' });
  await ask(stopping.id, 'first');
  const slow = (await (await api(`/api/threads/${stopping.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow' }) })).json()) as { turnId: string };
  await api(`/api/threads/${stopping.id}/cancel`, { method: 'POST' });
  await waitFor(async () => ((await turnsOf(stopping.id)).length === 2 && (await events(stopping.id)).some((e) => e.type === 'turn.done' && e.turnId === slow.turnId) ? true : null));
  await ask(stopping.id, 'after the stop');
  const stoppedFork = await forkAt(stopping.id, slow.turnId);
  assert.equal(stoppedFork.status, 409);
  assert.match(((await stoppedFork.json()) as { error: string }).error, /stopped/);

  // A thread whose turns the agent has no record of: there is nowhere on its side to stop.
  const lost = await newThread({ agent: 'pi' });
  await ask(lost.id, 'one');
  await ask(lost.id, 'two');
  const [lostFirst] = await turnsOf(lost.id);
  const refused = await forkAt(lost.id, lostFirst.turnId!);
  assert.equal(refused.status, 409);
  assert.match(((await refused.json()) as { error: string }).error, /not in pi's own history/);

  // Claude is told the last message to keep.
  const claude = await newThread({ agent: 'claude' });
  await ask(claude.id, 'first question');
  await ask(claude.id, 'second question');
  store.updateThread(claude.id, { agentSessionId: 'hist-forkat' });
  const [claudeFirst] = await turnsOf(claude.id);
  const claudeCopy = (await (await forkAt(claude.id, claudeFirst.turnId!)).json()) as Record<string, any>;
  assert.equal(claudeCopy.agentSessionId, 'hist-forkat-fork-at-m1b');
  assert.deepEqual((await turnsOf(claudeCopy.id)).map((e) => e.data.text), ['first question']);

  // Claude noted a stop as a user message of its own; it is no turn, and the turns after it still line up.
  store.updateThread(claude.id, { agentSessionId: 'hist-interrupted' });
  const afterStop = (await (await forkAt(claude.id, claudeFirst.turnId!)).json()) as Record<string, any>;
  assert.equal(afterStop.agentSessionId, 'hist-interrupted-fork-at-m1b');

  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; capabilities: { forkAt: boolean } }>;
  assert.deepEqual(agents.map((a) => [a.id, a.capabilities.forkAt]).sort(), [['agy', false], ['claude', true], ['pi', true]]);
});

test('new thread in a worktree: offered for a git directory, made under .worktrees with a branch of its own', async () => {
  const git = (cwd: string, ...args: string[]) =>
    execFileSync('git', ['-c', 'user.name=Tester', '-c', 'user.email=tester@example.com', ...args], { cwd, encoding: 'utf8' }).trim();
  const optionsFor = async (cwd: string) => (await (await api(`/api/worktree-options?cwd=${encodeURIComponent(cwd)}`)).json()) as Record<string, unknown>;

  const repo = join(workdir, 'fresh-project');
  mkdirSync(join(repo, 'web'), { recursive: true });
  git(repo, 'init', '--quiet', '--initial-branch=main');
  writeFileSync(join(repo, 'web', 'index.ts'), 'export {};\n');
  git(repo, 'add', '.');
  git(repo, 'commit', '--quiet', '-m', 'first');
  writeFileSync(join(repo, 'draft.md'), 'work in progress\n');

  // Offered for the repository and anywhere inside it; not for a plain directory or outside the workdirs.
  assert.deepEqual(await optionsFor(repo), { available: true, branch: 'main', changes: 1 });
  assert.equal((await optionsFor(join(repo, 'web'))).available, true);
  const plain = join(workdir, 'not-a-repo');
  mkdirSync(plain, { recursive: true });
  assert.deepEqual(await optionsFor(plain), { available: false, reason: 'not_git' });
  assert.equal((await api(`/api/worktree-options?cwd=${encodeURIComponent(tmpdir())}`)).status, 400);
  assert.equal((await api('/api/worktree-options')).status, 400);

  // Any agent: there is no conversation to move. Started in a subdirectory, the thread stays in it.
  const res = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: join(repo, 'web'), worktree: true }) });
  assert.equal(res.status, 201);
  const thread = (await res.json()) as Record<string, any>;
  const name = thread.id.slice(0, 8);
  const root = join(repo, '.worktrees', name);
  assert.equal(thread.cwd, join(root, 'web'));
  assert.deepEqual(thread.worktree, { root, branch: `loochat/${name}`, of: join(repo, 'web'), ofReal: realpathSync(join(repo, 'web')) });
  assert.equal(git(root, 'symbolic-ref', '--short', 'HEAD'), `loochat/${name}`);
  assert.equal(readFileSync(join(root, 'draft.md'), 'utf8'), 'work in progress\n', 'uncommitted work comes along, as with a fork');
  assert.equal(git(repo, 'status', '--porcelain'), '?? draft.md', 'the repository itself is as it was');

  // Without a repository, asking for one is refused and nothing is made.
  const refused = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: plain, worktree: true }) });
  assert.equal(refused.status, 400);
  assert.match(((await refused.json()) as { error: string }).error, /not in a git repository/);
  assert.equal(existsSync(join(plain, '.worktrees')), false);

  // Deleted with its worktree, like a fork's: the carried-over file exists nowhere else, so it takes force.
  assert.equal((await api(`/api/threads/${thread.id}?worktree=1`, { method: 'DELETE' })).status, 409);
  assert.equal((await api(`/api/threads/${thread.id}?worktree=1&force=1`, { method: 'DELETE' })).status, 204);
  assert.equal(existsSync(root), false);
});

test('fork into a worktree: a checkout and a branch of its own, the uncommitted work brought along', async () => {
  const git = (cwd: string, ...args: string[]) =>
    execFileSync('git', ['-c', 'user.name=Tester', '-c', 'user.email=tester@example.com', ...args], { cwd, encoding: 'utf8' }).trim();
  const idle = (id: string) =>
    waitFor(async () => {
      const t = (await (await api(`/api/threads/${id}`)).json()) as { status: string };
      return t.status === 'idle' ? t : null;
    });
  const optionsOf = async (id: string) => (await (await api(`/api/threads/${id}/fork-options`)).json()) as { worktree: Record<string, unknown> };
  const forkInto = (id: string, body: Record<string, unknown> = {}) =>
    api(`/api/threads/${id}/fork`, { method: 'POST', body: JSON.stringify({ worktree: true, ...body }) });

  // A repository inside the allowed directory, with work in progress: one change, one new file, one ignored.
  const repo = join(workdir, 'project');
  mkdirSync(join(repo, 'app'), { recursive: true });
  git(repo, 'init', '--quiet', '--initial-branch=main');
  writeFileSync(join(repo, '.gitignore'), 'node_modules/\n');
  writeFileSync(join(repo, 'README.md'), 'first\n');
  writeFileSync(join(repo, 'app', 'main.ts'), 'export {};\n');
  git(repo, 'add', '.');
  git(repo, 'commit', '--quiet', '-m', 'first');
  writeFileSync(join(repo, 'README.md'), 'edited by the agent\n');
  writeFileSync(join(repo, 'notes.md'), 'new\n');
  mkdirSync(join(repo, 'node_modules'));
  writeFileSync(join(repo, 'node_modules', 'dep.js'), '');

  const thread = await newThread({ agent: 'claude', cwd: repo });
  assert.deepEqual(await optionsOf(thread.id), { worktree: { available: true, branch: 'main', changes: 2 } });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(thread.id);
  const source = (await (await api(`/api/threads/${thread.id}`)).json()) as Record<string, any>;
  assert.equal(source.worktree, null);

  const res = await forkInto(thread.id);
  assert.equal(res.status, 201);
  const copy = (await res.json()) as Record<string, any>;
  const name = copy.id.slice(0, 8);
  const root = join(repo, '.worktrees', name);
  assert.equal(copy.cwd, root);
  assert.deepEqual(copy.worktree, { root, branch: `loochat/${name}`, of: repo, ofReal: realpathSync(repo) });
  assert.equal(copy.forkedFrom, thread.id);
  // The agent made the copy for that directory.
  assert.equal(copy.agentSessionId, `${source.agentSessionId}-fork-in-${name}`);
  // The work in progress is there; what git ignores is not; the original directory is as it was.
  assert.equal(readFileSync(join(root, 'README.md'), 'utf8'), 'edited by the agent\n');
  assert.equal(readFileSync(join(root, 'notes.md'), 'utf8'), 'new\n');
  assert.equal(existsSync(join(root, 'node_modules')), false);
  assert.equal(git(root, 'symbolic-ref', '--short', 'HEAD'), `loochat/${name}`);
  assert.equal(git(repo, 'status', '--porcelain'), 'M README.md\n?? notes.md');
  // The log ends with the note the page draws its line from, and it names the branch.
  assert.deepEqual((await events(copy.id)).at(-1)!.data, { forkedFrom: thread.id, worktree: { branch: `loochat/${name}` } });

  // The copy runs there.
  await api(`/api/threads/${copy.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'in the worktree' }) });
  await idle(copy.id);
  assert.match(((await (await api(`/api/threads/${copy.id}`)).json()) as { resumeCommand: string }).resumeCommand, new RegExp(`^cd .*\\.worktrees/${name}`));

  // Forked again in place, the second copy shares the worktree and is listed under the same project.
  const twin = (await (await api(`/api/threads/${copy.id}/fork`, { method: 'POST' })).json()) as Record<string, any>;
  assert.equal(twin.cwd, root);
  assert.deepEqual(twin.worktree, copy.worktree);
  assert.equal((await events(twin.id)).at(-1)!.data.worktree, undefined, 'no new worktree was made for it');

  // Forked from inside the worktree into another: beside the first, still listed under the project.
  const third = (await (await forkInto(copy.id)).json()) as Record<string, any>;
  assert.equal(third.cwd, join(repo, '.worktrees', third.id.slice(0, 8)));
  assert.equal(third.worktree.of, repo);
  assert.equal(readFileSync(join(third.cwd, 'notes.md'), 'utf8'), 'new\n');

  // Deleting: the worktree only goes when asked, when no other thread stands in it, and never
  // with work that exists nowhere else unless that was confirmed.
  const remove = (id: string, query = '') => api(`/api/threads/${id}${query}`, { method: 'DELETE' });
  const shared = await remove(copy.id, '?worktree=1');
  assert.equal(shared.status, 409);
  assert.equal(((await shared.json()) as { code: string }).code, 'worktree_shared');
  assert.equal((await remove(twin.id)).status, 204);
  assert.equal(existsSync(root), true, 'deleting a thread leaves its worktree alone');

  const dirty = await remove(copy.id, '?worktree=1');
  assert.equal(dirty.status, 409);
  assert.deepEqual(await dirty.json(), { code: 'worktree_dirty', changes: 2, commits: 0, error: 'the worktree holds work that exists nowhere else' });
  assert.equal((await api(`/api/threads/${copy.id}`)).status, 200, 'nothing was deleted');
  assert.equal((await remove(copy.id, '?worktree=1&force=1')).status, 204);
  assert.equal(existsSync(root), false);
  assert.equal(git(repo, 'branch', '--list', `loochat/${name}`), '');

  // A clean worktree needs no second confirmation; commits only its branch has count as work.
  git(third.cwd, 'add', '.');
  git(third.cwd, 'commit', '--quiet', '-m', 'kept on the branch');
  const unmerged = await remove(third.id, '?worktree=1');
  assert.deepEqual([unmerged.status, ((await unmerged.json()) as any).commits], [409, 1]);
  git(repo, 'stash', '--include-untracked', '--quiet');
  git(repo, 'merge', '--quiet', `loochat/${third.id.slice(0, 8)}`);
  assert.equal((await remove(third.id, '?worktree=1')).status, 204);
  assert.equal(existsSync(third.cwd), false);

  // From a directory inside the repository, the copy lands in the same place inside the worktree.
  const deep = await newThread({ agent: 'pi', cwd: join(repo, 'app') });
  await api(`/api/threads/${deep.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(deep.id);
  const deepCopy = (await (await forkInto(deep.id)).json()) as Record<string, any>;
  assert.equal(deepCopy.cwd, join(repo, '.worktrees', deepCopy.id.slice(0, 8), 'app'));
  assert.equal(deepCopy.worktree.of, join(repo, 'app'));
  // pi copied the session into that directory itself, and can run it there.
  assert.equal(deepCopy.agentSessionId, 'pi-session-1-in-app');
  await api(`/api/threads/${deepCopy.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'still here' }) });
  await idle(deepCopy.id);
  assert.equal(((await (await api(`/api/threads/${deepCopy.id}`)).json()) as { status: string }).status, 'idle');

  // Where it cannot be offered: outside a repository, and with an agent that cannot fork at all.
  const loose = await newThread({ agent: 'claude' });
  assert.deepEqual(await optionsOf(loose.id), { worktree: { available: false, reason: 'not_git' } });
  await api(`/api/threads/${loose.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hi' }) });
  await finishedEvents(loose.id);
  const refused = await forkInto(loose.id);
  assert.equal(refused.status, 400);
  assert.match(((await refused.json()) as { error: string }).error, /not in a git repository/);
  assert.equal((await remove(loose.id, '?worktree=1')).status, 400, 'no worktree to remove');
  const agy = await newThread({ agent: 'agy', cwd: repo });
  assert.deepEqual(await optionsOf(agy.id), { worktree: { available: false, reason: 'agent' } });

  // A failure on the agent's side takes the half-made worktree away again.
  const stuck = await manager.createThread({ agent: 'claude', cwd: repo });
  store.updateThread(stuck.id, { agentSessionId: 'unforkable' });
  const trees = git(repo, 'worktree', 'list').split('\n').length;
  assert.equal((await forkInto(stuck.id)).status, 502);
  assert.equal(git(repo, 'worktree', 'list').split('\n').length, trees);
  assert.equal(git(repo, 'branch', '--list', 'loochat/*').split('\n').filter(Boolean).length, trees - 1);

  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; capabilities: { forkInto: boolean } }>;
  assert.deepEqual(agents.map((a) => [a.id, a.capabilities.forkInto]).sort(), [['agy', false], ['claude', true], ['pi', true]]);
});

test('matchTurns pairs the two histories from the start and gives up when they disagree', () => {
  assert.deepEqual(matchTurns(['a', 'b'], ['a', 'b', 'c'], 3), { hubToAgent: [0, 1, -1], agentNext: 2 });
  // A hub turn the agent never recorded is passed over; the agent's extra turn is new.
  assert.deepEqual(matchTurns(['a', 'c', 'd'], ['a', 'b', 'c'], 3), { hubToAgent: [0, -1, 1], agentNext: 2 });
  // The same question asked twice pairs in order.
  assert.deepEqual(matchTurns(['go', 'go'], ['go', 'go'], 3), { hubToAgent: [0, 1], agentNext: 2 });
  assert.equal(matchTurns(['x'], ['a', 'b'], 3), null);
  assert.equal(matchTurns(['e'], ['a', 'b', 'c', 'd', 'e'], 3), null, 'further apart than the tolerance');
  assert.deepEqual(matchTurns([], ['a'], 3), { hubToAgent: [-1], agentNext: 0 });
});

test('agy: two permission levels, no approvals, no images; settings are flags of its process', async () => {
  const agents = (await (await api('/api/agents')).json()) as Array<{ id: string; label: string; available: boolean; capabilities: any }>;
  const agy = agents.find((a) => a.id === 'agy')!;
  assert.equal(agy.label, 'Antigravity');
  assert.equal(agy.available, true);
  assert.deepEqual(agy.capabilities.permissionModes, ['auto_edit', 'full_access']);
  assert.deepEqual([agy.capabilities.approvals, agy.capabilities.images, agy.capabilities.effortLevels.length], [false, true, 0]);
  const models = (await (await api('/api/agents/agy/models')).json()) as Array<{ id: string; efforts: string[] }>;
  assert.deepEqual(models.map((m) => m.id), ['fast-low', 'fast-high', 'big-thinking']);

  // It cannot ask, so "ask" is not offered, and a new thread starts with full access: at the lower
  // level every command is refused. The lower level can still be chosen; other agents keep theirs.
  assert.equal(agy.capabilities.defaultPermissionMode, 'full_access');
  const ask = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'agy', cwd: workdir, permissionMode: 'ask' }) });
  assert.equal(ask.status, 400);
  assert.equal((await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'agy', cwd: workdir, effort: 'high' }) })).status, 400);
  assert.equal(((await newThread({ agent: 'agy' })) as { permissionMode?: string }).permissionMode, 'full_access');
  assert.equal(((await newThread({ agent: 'claude' })) as { permissionMode?: string }).permissionMode, 'auto_edit');
  const thread = (await newThread({ agent: 'agy', model: 'fast-low', permissionMode: 'auto_edit' })) as { id: string; permissionMode?: string };
  assert.equal(thread.permissionMode, 'auto_edit');

  const replyOf = (list: Array<{ type: string; turnId: string | null; data: any }>, turnId: string | null) =>
    list.filter((e) => e.type === 'message.delta' && e.turnId === turnId).map((e) => e.data.text).join('').trim();
  const sendAndWait = async (text: string) => {
    const res = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
    assert.equal(res.status, 202);
    const { turnId } = (await res.json()) as { turnId: string };
    const list = await waitFor(async () => {
      const all = await events(thread.id);
      return all.some((e) => e.turnId === turnId && (e.type === 'turn.done' || e.type === 'turn.error')) ? all : null;
    });
    return { turnId, list, end: list.find((e) => e.turnId === turnId && (e.type === 'turn.done' || e.type === 'turn.error'))! };
  };

  const first = await sendAndWait('use a tool');
  assert.equal(first.end.type, 'turn.done');
  assert.equal(replyOf(first.list, first.turnId), 'echo:use a tool;model:fast-low;mode:accept-edits;conv:agy-conv-1');
  assert.ok(first.list.some((e) => e.type === 'tool.end' && e.data.output === 'hi\r\n'));
  const view = (await (await api(`/api/threads/${thread.id}`)).json()) as { agentSessionId: string; resumeCommand: string };
  assert.equal(view.agentSessionId, 'agy-conv-1');
  assert.equal(view.resumeCommand, `cd ${workdir} && agy --conversation agy-conv-1`);

  // A command needs a permission it cannot ask for: the turn ends with an explanation.
  const refused = await sendAndWait('run a command');
  assert.equal(refused.end.type, 'turn.error');
  assert.match(refused.end.data.message, /full access/);
  assert.deepEqual([refused.end.data.code, refused.end.data.refused], ['permission_refused', ['RunCommand']]);

  // Raising the level and changing the model both carry on in the same conversation.
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ permissionMode: 'full_access', model: 'big-thinking' }) })).status, 200);
  const allowed = await sendAndWait('run a command');
  assert.equal(allowed.end.type, 'turn.done');
  assert.equal(replyOf(allowed.list, allowed.turnId), 'echo:run a command;model:big-thinking;mode:always-proceed;conv:agy-conv-1');

  // Pictures go in as paths to the stored files; agy opens them itself.
  const image = await sendAndWait('look at this');
  assert.equal(image.end.type, 'turn.done');
  const pictured = await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'look', images: [{ data: PNG, mimeType: 'image/png' }] }) });
  assert.equal(pictured.status, 202);
  const { turnId: picturedTurn } = (await pictured.json()) as { turnId: string };
  const withPicture = await waitFor(async () => {
    const all = await events(thread.id);
    return all.some((e) => e.turnId === picturedTurn && e.type === 'turn.done') ? all : null;
  });
  const stored = withPicture.find((e) => e.turnId === picturedTurn && e.type === 'turn.started')!.data.images[0];
  const said = withPicture.filter((e) => e.turnId === picturedTurn && e.type === 'message.delta').map((e) => e.data.text).join('');
  assert.ok(said.includes(join(dataDir, 'attachments', thread.id, `${stored.id}.png`)), said);

  // What it cannot do is refused up front.
  assert.equal((await api(`/api/threads/${thread.id}/fork`, { method: 'POST' })).status, 400);
  assert.equal((await api('/api/agents/agy/sessions')).status, 400);
});

test('agy: stopping a turn leaves the thread idle and usable', async () => {
  const thread = await newThread({ agent: 'agy' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow' }) });
  await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'message.delta'));
  assert.equal((await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' })).status, 200);
  const done = await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'turn.done'));
  assert.equal(done.data.stopReason, 'cancelled');
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  const list = await waitFor(async () => {
    const all = await events(thread.id);
    return all.filter((e) => e.type === 'turn.done').length === 2 ? all : null;
  });
  assert.ok(list.some((e) => e.type === 'message.delta' && String(e.data.text).startsWith('echo:hello;')));
  const view = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
  assert.equal(view.status, 'idle');
});

const patchRaw = (id: string, body: Record<string, unknown>) => api(`/api/threads/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
type View = { id: string; status: string; unread: boolean; waiting: boolean; waitingReason: 'approval' | 'question' | 'plan' | null; updatedAt: string; queued: Array<{ id: string; text: string; images: number }> };
const viewOf = async (id: string) => (await (await api(`/api/threads/${id}`)).json()) as View;
const post = (path: string, body?: unknown) => api(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
const untilIdle = (id: string) =>
  waitFor(async () => {
    const t = await viewOf(id);
    return t.status !== 'running' && t.queued.length === 0 ? t : null;
  });
const turnTexts = async (id: string) => (await events(id)).filter((e) => e.type === 'turn.started').map((e) => e.data.text);

test('unread: a finished turn marks the thread, reading clears it, and a stopped turn does not mark it', async () => {
  const thread = await newThread({ agent: 'claude' });
  assert.equal((await viewOf(thread.id)).unread, false);
  await post(`/api/threads/${thread.id}/turns`, { text: 'hi' });
  await finishedEvents(thread.id);
  const done = await waitFor(async () => {
    const t = await viewOf(thread.id);
    return t.status === 'idle' ? t : null;
  });
  assert.equal(done.unread, true);

  // Marking it read is not activity: the thread keeps its place in the list.
  const read = (await (await patchRaw(thread.id, { unread: false })).json()) as View;
  assert.deepEqual([read.unread, read.updatedAt], [false, done.updatedAt]);
  assert.equal(((await (await patchRaw(thread.id, { unread: true })).json()) as View).unread, true, 'it can be marked unread by hand');
  assert.equal((await patchRaw(thread.id, { unread: 'yes' })).status, 400);
  await patchRaw(thread.id, { unread: false });

  await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
  await post(`/api/threads/${thread.id}/cancel`);
  const stopped = await untilIdle(thread.id);
  assert.equal(stopped.unread, false, 'the user stopped it, so there is nothing new to look at');

  // An error is news too.
  const pi = await newThread({ agent: 'pi' });
  await post(`/api/threads/${pi.id}/turns`, { text: 'please fail' });
  const failed = await waitFor(async () => {
    const t = await viewOf(pi.id);
    return t.status === 'error' ? t : null;
  });
  assert.equal(failed.unread, true);
});

test('waiting: a thread says when its agent is waiting for approval, in the list too', async () => {
  const thread = await newThread({ agent: 'claude', permissionMode: 'ask' });
  assert.equal((await viewOf(thread.id)).waiting, false);
  await post(`/api/threads/${thread.id}/turns`, { text: 'please approve' });
  const request = await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'approval.request'));
  assert.equal((await viewOf(thread.id)).waiting, true);
  const list = (await (await api('/api/threads')).json()) as View[];
  assert.equal(list.find((t) => t.id === thread.id)?.waiting, true);
  assert.equal(list.find((t) => t.id === thread.id)?.waitingReason, 'approval');
  assert.equal((await viewOf(thread.id)).waitingReason, 'approval');
  assert.equal(list.filter((t) => t.waiting).length, 1);

  await post(`/api/approvals/${request.data.approvalId}`, { optionId: 'allow' });
  const after = await untilIdle(thread.id);
  assert.equal(after.waiting, false);
  assert.equal(after.waitingReason, null);
});

const permissionOutcomes = () => {
  const file = process.env.FAKE_ACP_PERMISSION_LOG!;
  return existsSync(file) ? readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line) as { scenario: string; outcome: { outcome: string } }) : [];
};

test('approval lifecycle: a permission arriving after cancel cannot reopen a stopped turn', { timeout: 5000 }, async () => {
  const thread = await newThread({ agent: 'claude', permissionMode: 'ask' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'approval lifecycle after-cancel' });
  await waitFor(async () => (await events(thread.id)).find(e => e.type === 'message.delta' && e.data.text === 'waiting-for-cancel'));
  assert.equal((await post(`/api/threads/${thread.id}/cancel`)).status, 200);
  const view = await untilIdle(thread.id);
  assert.deepEqual([view.status, view.waiting, view.waitingReason], ['idle', false, null]);
  const list = await events(thread.id);
  assert.equal(list.filter(e => e.type === 'approval.request').length, 0);
  assert.ok(list.some(e => e.type === 'turn.done' && e.data.stopReason === 'cancelled'));
  const outcome = await waitFor(async () => permissionOutcomes().find(p => p.scenario === 'after-cancel'));
  assert.equal(outcome.outcome.outcome, 'cancelled', 'the adapter answers the late native permission, letting cancellation finish');
});

for (const scenario of ['error', 'end', 'exit'] as const) {
  test(`approval lifecycle: ${scenario} clears pending decisions and rejects obsolete approval ids`, { timeout: 5000 }, async () => {
    const thread = await newThread({ agent: 'claude', permissionMode: 'ask' });
    await post(`/api/threads/${thread.id}/turns`, { text: `approval lifecycle ${scenario}` });
    const view = await untilIdle(thread.id);
    assert.deepEqual([view.status, view.waiting, view.waitingReason], [scenario === 'end' ? 'idle' : 'error', false, null]);
    const list = await events(thread.id);
    const request = list.find(e => e.type === 'approval.request')!;
    assert.ok(request, 'the permission is received before the turn ends');
    const resolved = list.filter(e => e.type === 'approval.resolved' && e.data.approvalId === request.data.approvalId);
    assert.equal(resolved.length, 1);
    assert.deepEqual(resolved[0].data.decision, { cancelled: true });
    assert.equal((await post(`/api/approvals/${request.data.approvalId}`, { optionId: 'allow' })).status, 404);
    assert.equal((await post(`/api/approvals/${request.data.approvalId}`, { cancel: true })).status, 404);
    const threads = (await (await api('/api/threads')).json()) as View[];
    assert.deepEqual([threads.find(t => t.id === thread.id)?.waiting, threads.find(t => t.id === thread.id)?.waitingReason], [false, null]);
    const history = await (await api(`/api/threads/${thread.id}/history`)).text();
    assert.ok(history.includes(request.data.approvalId), 'the resolved decision remains in the conversation');
    assert.equal(history.includes('APPROVAL_LIFECYCLE_SECRET'), false, 'obsolete decision payloads are no longer loaded into history');
    if (scenario !== 'exit') {
      const outcome = await waitFor(async () => permissionOutcomes().find(p => p.scenario === scenario));
      assert.equal(outcome.outcome.outcome, 'cancelled', 'cleanup sends the native cancellation answer');
    }
  });
}

test('the thread-list stream reports approvals, so a waiting thread shows up without polling', async () => {
  const thread = await newThread({ agent: 'claude', permissionMode: 'ask' });
  const ctrl = new AbortController();
  const res = await fetch(base + '/api/stream', { headers: auth, signal: ctrl.signal });
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let seen = '';
  const until = async (needle: string) => {
    const deadline = Date.now() + 10_000;
    while (!seen.includes(needle)) {
      if (Date.now() > deadline) throw new Error(`stream never sent ${needle}`);
      const { value, done } = await reader.read();
      if (done) break;
      seen += decoder.decode(value, { stream: true });
    }
  };
  await post(`/api/threads/${thread.id}/turns`, { text: 'please approve' });
  await until('event: turn.started'); // a turn started from anywhere shows the thread as running
  await until('event: approval.request');
  assert.equal(seen.includes('event: message.delta'), false, 'only what the list needs is sent');
  const request = (await events(thread.id)).find((e) => e.type === 'approval.request')!;
  await post(`/api/approvals/${request.data.approvalId}`, { optionId: 'allow' });
  await until('event: approval.resolved');
  ctrl.abort();
  await untilIdle(thread.id);
});

test('queue: a message sent while a turn runs waits, and goes out by itself when the turn ends', async () => {
  const thread = await newThread({ agent: 'claude', permissionMode: 'ask' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'please approve first' });
  const request = await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'approval.request'));

  // Without asking for it, a second message is still refused.
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { text: 'no queue' })).status, 409);
  const queued = await post(`/api/threads/${thread.id}/turns`, { text: 'second', queue: true });
  assert.equal(queued.status, 202);
  const { id: queuedId, queued: isQueued } = (await queued.json()) as { id: string; queued: boolean };
  assert.equal(isQueued, true);
  await post(`/api/threads/${thread.id}/turns`, { text: 'third', queue: true, images: [{ data: PNG, mimeType: 'image/png' }] });
  assert.deepEqual((await viewOf(thread.id)).queued.map((q) => [q.text, q.images]), [['second', 0], ['third', 1]]);
  // Taking one back (to edit it) returns its pictures too, and it can go back in line as it was.
  const thirdId = (await viewOf(thread.id)).queued[1].id;
  const taken = (await (await api(`/api/threads/${thread.id}/queue/${thirdId}`, { method: 'DELETE' })).json()) as { text: string; images: unknown[] };
  assert.deepEqual(taken, { text: 'third', images: [{ data: PNG, mimeType: 'image/png' }] });
  await post(`/api/threads/${thread.id}/turns`, { ...taken, queue: true });
  assert.equal((await viewOf(thread.id)).queued[0].id, queuedId);
  assert.deepEqual(await turnTexts(thread.id), ['please approve first'], 'nothing is sent yet');

  // What would be refused later is refused now, and does not take a place in the queue.
  const bad = await post(`/api/threads/${thread.id}/turns`, { text: 'x', queue: true, images: [{ data: Buffer.from('not an image').toString('base64') }] });
  assert.equal(bad.status, 400);
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { text: '', queue: true })).status, 400);
  assert.equal((await viewOf(thread.id)).queued.length, 2);

  await post(`/api/approvals/${request.data.approvalId}`, { optionId: 'allow' });
  await untilIdle(thread.id);
  assert.deepEqual(await turnTexts(thread.id), ['please approve first', 'second', 'third']);
  const log = await events(thread.id);
  assert.equal(log.filter((e) => e.type === 'turn.done').length, 3);
  assert.equal(log.filter((e) => e.type === 'turn.started').at(-1)!.data.images.length, 1, 'the picture went with its message');

  // With nothing running, "queue" simply sends.
  const direct = await post(`/api/threads/${thread.id}/turns`, { text: 'fourth', queue: true });
  assert.ok(((await direct.json()) as { turnId?: string }).turnId);
  await untilIdle(thread.id);
});

test('queue: a waiting message can be taken back, the queue has a limit, and stopping hands the rest back unsent', async () => {
  const thread = await newThread({ agent: 'claude' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
  const ids: string[] = [];
  for (const text of ['a', 'b', 'c', 'd', 'e']) {
    ids.push(((await (await post(`/api/threads/${thread.id}/turns`, { text, queue: true })).json()) as { id: string }).id);
  }
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { text: 'f', queue: true })).status, 409, 'five is the limit');

  const taken = await api(`/api/threads/${thread.id}/queue/${ids[1]}`, { method: 'DELETE' });
  assert.equal(taken.status, 200);
  assert.deepEqual(await taken.json(), { text: 'b', images: [] }, 'the message comes back, for editing');
  assert.equal((await api(`/api/threads/${thread.id}/queue/${ids[1]}`, { method: 'DELETE' })).status, 404);
  assert.equal((await api(`/api/threads/no-such/queue/${ids[0]}`, { method: 'DELETE' })).status, 404);
  assert.deepEqual((await viewOf(thread.id)).queued.map((q) => q.text), ['a', 'c', 'd', 'e']);

  const stop = await post(`/api/threads/${thread.id}/cancel`);
  assert.deepEqual(await stop.json(), { dropped: ['a', 'c', 'd', 'e'] });
  const after = await untilIdle(thread.id);
  assert.deepEqual(after.queued, []);
  await new Promise((r) => setTimeout(r, 150));
  assert.deepEqual(await turnTexts(thread.id), ['slow'], 'stopping sends nothing');
});

test('queue: "send now" interrupts the running turn and puts that message first', async () => {
  const thread = await newThread({ agent: 'pi' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'slow start' });
  await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'message.delta'));
  await post(`/api/threads/${thread.id}/turns`, { text: 'later', queue: true });
  const urgent = (await (await post(`/api/threads/${thread.id}/turns`, { text: 'urgent', queue: true })).json()) as { id: string };

  assert.equal((await post(`/api/threads/${thread.id}/queue/nope/now`)).status, 404);
  assert.equal((await post(`/api/threads/${thread.id}/queue/${urgent.id}/now`)).status, 204);
  await untilIdle(thread.id);
  assert.deepEqual(await turnTexts(thread.id), ['slow start', 'urgent', 'later']);
  const ends = (await events(thread.id)).filter((e) => e.type === 'turn.done').map((e) => e.data.stopReason);
  assert.deepEqual(ends, ['cancelled', 'end_turn', 'end_turn']);
});

test('claude: work the agent starts between prompts is a turn of its own, and messages wait behind it', async () => {
  const thread = await newThread({ agent: 'claude' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'background later' });
  await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'turn.started' && e.data.source === 'background'));
  assert.equal((await (await api(`/api/threads/${thread.id}`)).json() as { status: string }).status, 'running');
  await post(`/api/threads/${thread.id}/turns`, { text: 'next', queue: true });
  await untilIdle(thread.id);
  await waitFor(async () => (await turnTexts(thread.id)).length === 3 ? true : null);
  await untilIdle(thread.id);

  const log = await events(thread.id);
  assert.deepEqual(await turnTexts(thread.id), ['background later', '', 'next']);
  const background = log.find((e) => e.type === 'turn.started' && e.data.source === 'background')!.turnId;
  const own = log.filter((e) => e.turnId === background).map((e) => e.type);
  assert.ok(own.includes('tool.start') && own.includes('tool.end'), 'the tool it ran is shown in its turn');
  assert.equal(log.filter((e) => e.turnId === background && e.type === 'message.delta').map((e) => e.data.text).join(''), 'background done');
  assert.equal(log.find((e) => e.turnId === background && e.type === 'turn.done')?.data.stopReason, 'end_turn');
  const order = log.filter((e) => e.type === 'turn.started' || e.type === 'turn.done').map((e) => `${e.type}:${e.data.text ?? e.data.stopReason}`);
  assert.deepEqual(order, ['turn.started:background later', 'turn.done:end_turn', 'turn.started:', 'turn.done:end_turn', 'turn.started:next', 'turn.done:end_turn']);
});

test('claude: "send now" interrupts work the agent started on its own', async () => {
  const thread = await newThread({ agent: 'claude' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'background later hold' });
  await waitFor(async () => (await events(thread.id)).find((e) => e.type === 'tool.start' && e.data.title === 'Read task output'));
  const urgent = (await (await post(`/api/threads/${thread.id}/turns`, { text: 'urgent', queue: true })).json()) as { id: string };
  assert.equal((await post(`/api/threads/${thread.id}/queue/${urgent.id}/now`)).status, 204);
  await waitFor(async () => (await turnTexts(thread.id)).includes('urgent') ? true : null);
  await untilIdle(thread.id);
  const ends = (await events(thread.id)).filter((e) => e.type === 'turn.done').map((e) => e.data.stopReason);
  assert.deepEqual(ends, ['end_turn', 'cancelled', 'end_turn']);
});

test('queue: deleting a thread drops what was waiting in it', async () => {
  const thread = await newThread({ agent: 'claude' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'never sent', queue: true });
  await post(`/api/threads/${thread.id}/cancel`);
  await untilIdle(thread.id);
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'DELETE' })).status, 204);
  assert.equal((await api(`/api/threads/${thread.id}`)).status, 404);
});

test('usage and plan: what the agent reports reaches the event log', async () => {
  const claude = await newThread({ agent: 'claude' });
  await post(`/api/threads/${claude.id}/turns`, { text: 'make a plan and report usage' });
  await untilIdle(claude.id);
  const log = await events(claude.id);
  assert.deepEqual(log.find((e) => e.type === 'usage.updated')?.data, { used: 52000, size: 200000, cost: { amount: 0.42, currency: 'USD' } });
  const plan = log.find((e) => e.type === 'plan.updated')!;
  assert.deepEqual(plan.data.entries.map((e: any) => [e.content, e.status]), [
    ['Read the code', 'completed'],
    ['Write the fix', 'in_progress'],
    ['Run the tests', 'pending'],
  ]);

  const pi = await newThread({ agent: 'pi' });
  await post(`/api/threads/${pi.id}/turns`, { text: 'hi' });
  await untilIdle(pi.id);
  const piLog = await events(pi.id);
  assert.deepEqual(piLog.find((e) => e.type === 'usage.updated')?.data, { used: 1200, size: 200000, cost: { amount: 0.02, currency: 'USD' } });
  assert.ok(piLog.findIndex((e) => e.type === 'usage.updated') < piLog.findIndex((e) => e.type === 'turn.done'), 'usage arrives before the turn is reported done');
});

test('videos upload as original files, stay scoped to a thread, and survive queue editing and forks', async () => {
  const thread = await newThread({ agent: 'pi', model: 'fake/plain' });
  const other = await newThread({ agent: 'pi' });
  const bytes = readFileSync(join(here, 'fixtures', 'sample-video.mp4'));
  const upload = (id: string, body: Buffer = bytes, authenticated = true) => api(`/api/threads/${id}/videos`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent('手机录屏.mp4') }, body }, authenticated);
  assert.equal((await upload(thread.id, bytes, false)).status, 401);
  assert.equal((await upload('missing')).status, 404);
  assert.equal((await upload(thread.id, Buffer.from('fake MP4'))).status, 400);
  const uploaded = await upload(thread.id);
  assert.equal(uploaded.status, 201);
  const video = await uploaded.json();
  assert.equal(video.mimeType, 'video/mp4');
  assert.equal(video.name, '手机录屏.mp4');
  const downloaded = await api(`/api/threads/${thread.id}/attachments/${video.id}`);
  assert.equal(downloaded.headers.get('content-type'), 'video/mp4');
  assert.deepEqual(Buffer.from(await downloaded.arrayBuffer()), bytes);
  assert.equal((await api(`/api/threads/${other.id}/attachments/${video.id}`)).status, 404);
  assert.equal((await post(`/api/threads/${other.id}/turns`, { videos: [video.id] })).status, 404);
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { videos: [video.id, video.id] })).status, 400);
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { text: '/compact', videos: [video.id] })).status, 400);
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { videos: ['/etc/passwd'] })).status, 400);
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { videos: [video.id] })).status, 202, 'video-only works even with a text model');
  await untilIdle(thread.id);
  const log = await events(thread.id);
  const start = log.find(e => e.type === 'turn.started')!;
  assert.equal(start.data.displayText, '');
  assert.deepEqual(start.data.videos, [video]);
  assert.match(start.data.text, /The user attached 1 video file/);
  const answer = log.filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
  assert.ok(answer.includes(join(dataDir, 'attachments', thread.id, `${video.id}.mp4`)), 'the agent gets a readable original-file path');
  await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
  const queued = await (await post(`/api/threads/${thread.id}/turns`, { text: '分析视频', videos: [video.id], queue: true })).json();
  assert.equal((await (await api(`/api/threads/${thread.id}`)).json()).queued[0].videos, 1);
  const taken = await (await api(`/api/threads/${thread.id}/queue/${queued.id}`, { method: 'DELETE' })).json();
  assert.deepEqual(taken.videos, [video]);
  assert.equal((await post(`/api/threads/${thread.id}/turns`, { ...taken, queue: true })).status, 202);
  await manager.cancelTurn(thread.id, { keepQueue: true });
  await waitFor(async () => (await events(thread.id)).filter(e => e.type === 'turn.started').length === 3 ? true : null);
  await untilIdle(thread.id);
  const fork = await (await post(`/api/threads/${thread.id}/fork`, {})).json();
  assert.equal((await api(`/api/threads/${fork.id}/attachments/${video.id}`)).status, 200);
  await api(`/api/threads/${thread.id}`, { method: 'DELETE' });
  assert.deepEqual(Buffer.from(await (await api(`/api/threads/${fork.id}/attachments/${video.id}`)).arrayBuffer()), bytes);
});

test('any file can be attached: kept under its own name, served only as bytes, and given to the agent by path', async () => {
  const thread = await newThread({ agent: 'pi', model: 'fake/plain' });
  const other = await newThread({ agent: 'pi' });
  const bytes = Buffer.from('<script>alert(1)</script>\n会议记录');
  const upload = (id: string, name: string, body: Buffer = bytes, authenticated = true) => api(`/api/threads/${id}/files`, { method: 'POST', headers: { 'Content-Type': 'text/html', 'X-File-Name': encodeURIComponent(name) }, body }, authenticated);
  assert.equal((await upload(thread.id, 'a.txt', bytes, false)).status, 401);
  assert.equal((await upload(thread.id, 'a.txt', Buffer.alloc(0))).status, 400);
  const uploaded = await upload(thread.id, '../../transcript.txt');
  assert.equal(uploaded.status, 201);
  const file = await uploaded.json();
  assert.deepEqual(file, { id: file.id, mimeType: 'application/octet-stream', size: bytes.length, name: 'transcript.txt' });
  const stored = join(dataDir, 'attachments', thread.id, file.id, 'transcript.txt');
  assert.deepEqual(readFileSync(stored), bytes, 'the name cannot climb out of its folder');
  const downloaded = await api(`/api/threads/${thread.id}/attachments/${file.id}`);
  assert.equal(downloaded.headers.get('content-type'), 'application/octet-stream', 'the claimed type is never served back');
  assert.deepEqual(Buffer.from(await downloaded.arrayBuffer()), bytes);
  assert.equal((await api(`/api/threads/${other.id}/attachments/${file.id}`)).status, 404);
  assert.equal((await post(`/api/threads/${other.id}/turns`, { videos: [file.id] })).status, 404);

  assert.equal((await post(`/api/threads/${thread.id}/turns`, { videos: [file.id] })).status, 202);
  await untilIdle(thread.id);
  const log = await events(thread.id);
  const start = log.find(e => e.type === 'turn.started')!;
  assert.deepEqual(start.data.videos, [file]);
  assert.match(start.data.text, /The user attached 1 file\(s\)/);
  assert.doesNotMatch(start.data.text, /video/);
  const answer = log.filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
  assert.ok(answer.includes(stored), 'the agent gets the path with the original name');
  assert.equal((await api(`/api/threads/${thread.id}`).then(r => r.json())).title, 'transcript.txt');

  await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
  const queued = await (await post(`/api/threads/${thread.id}/turns`, { text: '总结', videos: [file.id], queue: true })).json();
  const waiting = (await (await api(`/api/threads/${thread.id}`)).json()).queued[0];
  assert.equal(waiting.videos, 0);
  assert.equal(waiting.files, 1);
  const taken = await (await api(`/api/threads/${thread.id}/queue/${queued.id}`, { method: 'DELETE' })).json();
  assert.deepEqual(taken.videos, [file]);
  await manager.cancelTurn(thread.id);
  await untilIdle(thread.id);
  const fork = await (await post(`/api/threads/${thread.id}/fork`, {})).json();
  await api(`/api/threads/${thread.id}`, { method: 'DELETE' });
  assert.deepEqual(Buffer.from(await (await api(`/api/threads/${fork.id}/attachments/${file.id}`)).arrayBuffer()), bytes);
});

test('files: an image in the thread directory is served; anything else, or anywhere else, is not', async () => {
  const thread = await newThread({ agent: 'claude' });
  const png = Buffer.from(PNG, 'base64');
  mkdirSync(join(workdir, 'out'), { recursive: true });
  writeFileSync(join(workdir, 'out', 'chart.png'), png);
  writeFileSync(join(workdir, 'out', 'notes.txt'), 'just text');
  writeFileSync(join(workdir, 'out', 'fake.png'), 'named like an image, but text');
  writeFileSync(join(dataDir, 'outside.png'), png);
  symlinkSync(join(dataDir, 'outside.png'), join(workdir, 'out', 'link.png'));
  const get = (path?: string) => api(`/api/threads/${thread.id}/files${path === undefined ? '' : `?path=${encodeURIComponent(path)}`}`);

  const relative = await get('out/chart.png');
  assert.equal(relative.status, 200);
  assert.equal(relative.headers.get('content-type'), 'image/png');
  assert.equal(relative.headers.get('x-content-type-options'), 'nosniff');
  assert.deepEqual(Buffer.from(await relative.arrayBuffer()), png);
  for (const path of [join(workdir, 'out', 'chart.png'), `file://${join(workdir, 'out', 'chart.png')}`, './out/../out/chart.png']) {
    const res = await get(path);
    assert.equal(res.status, 200, path);
    await res.arrayBuffer();
  }

  // An SVG is served too, told by how it starts, and locked down in case it is opened on its own.
  const svg = '<?xml version="1.0" encoding="UTF-8"?>\n<!-- drawn by an agent -->\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script></svg>\n';
  writeFileSync(join(workdir, 'out', 'drawing.svg'), svg);
  writeFileSync(join(workdir, 'out', 'page.svg'), '<html><body><svg></svg></body></html>');
  const drawing = await get('out/drawing.svg');
  assert.equal(drawing.status, 200);
  assert.equal(drawing.headers.get('content-type'), 'image/svg+xml');
  assert.match(drawing.headers.get('content-security-policy') ?? '', /^sandbox;/);
  assert.equal(await drawing.text(), svg);
  assert.equal((await get('out/page.svg')).status, 415, 'named like an SVG, but a web page');
  // What an agent draws cannot be sent to one as a picture: models take bitmaps.
  const asAttachment = await post(`/api/threads/${thread.id}/turns`, { text: 'look', images: [{ data: Buffer.from(svg).toString('base64') }] });
  assert.equal(asAttachment.status, 400);

  assert.equal((await get('out/notes.txt')).status, 415, 'not an image');
  assert.equal((await get('out/fake.png')).status, 415, 'the bytes decide, not the name');
  assert.equal((await get('out/missing.png')).status, 404);
  assert.equal((await get('out')).status, 404, 'a directory');
  assert.equal((await get(join(dataDir, 'outside.png'))).status, 403, 'outside the thread directory');
  assert.equal((await get('../' + 'outside.png')).status, 404);
  assert.equal((await get('out/link.png')).status, 404, 'a link that leads outside');
  assert.equal((await get()).status, 400);
  assert.equal((await get('')).status, 400);
  assert.equal((await api(`/api/threads/${thread.id}/files?path=out/chart.png`, {}, false)).status, 401);
  assert.equal((await api('/api/threads/nope/files?path=out/chart.png')).status, 404);
});

test('files: files outside the thread are readable once this thread’s completed answers name them', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-artifacts-'));
  const thread = await newThread({ agent: 'claude' });
  const other = await newThread({ agent: 'pi' });
  const path = join(dir, '截图 (1).png');
  const notes = join(dir, 'report.md');
  const get = (id: string, route: string, file: string) => api(`/api/threads/${id}/${route}?path=${encodeURIComponent(file)}`);
  const answer = (id: string, turn: string, text: string) => {
    store.appendEvent(id, turn, 'turn.started', { text: 'draw a chart' });
    // A destination split across chunks must work once the answer is assembled.
    const split = Math.floor(text.length / 2);
    store.appendEvent(id, turn, 'message.delta', { text: text.slice(0, split) });
    store.appendEvent(id, turn, 'message.delta', { text: text.slice(split) });
    store.appendEvent(id, turn, 'turn.done', {});
  };
  try {
    writeFileSync(path, Buffer.from(PNG, 'base64'));
    writeFileSync(notes, '# Report\nTemporary artifact\n');
    writeFileSync(join(dir, 'neighbour.png'), Buffer.from(PNG, 'base64'));
    symlinkSync(path, join(dir, 'redirect.png'));
    symlinkSync(dir, join(dir, 'redirect-dir'));
    symlinkSync('/etc/passwd', join(dir, 'outside.txt'));
    assert.equal((await get(thread.id, 'files', path)).status, 403);
    store.appendEvent(thread.id, 'user-only', 'turn.started', { text: `[截图](<${path}>)` });
    store.appendEvent(thread.id, 'user-only', 'turn.done', {});
    assert.equal((await get(thread.id, 'files', path)).status, 403, 'a user prompt alone is not an artifact');
    answer(other.id, 'other', `[截图](<${path}>)`);
    assert.equal((await get(thread.id, 'files', path)).status, 403, 'another thread cannot grant access');
    answer(thread.id, 'output', `[截图](<${path}>)\n[报告](file://${notes}#L2)\n目录：${dir}\n[重定向](${dir}/redirect.png)\n[目录链接](${dir}/redirect-dir/截图%20(1).png)\n[外部](${dir}/outside.txt)\n[外部](/etc/passwd)`);
    for (const file of [path, `file://${encodeURI(path)}`]) {
      const response = await get(thread.id, 'files', file);
      assert.equal(response.status, 200, file);
      assert.equal(response.headers.get('content-type'), 'image/png');
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), Buffer.from(PNG, 'base64'));
    }
    const text = await get(thread.id, 'text', notes);
    assert.equal(text.status, 200);
    assert.equal((await text.json()).text, '# Report\nTemporary artifact\n');
    assert.equal((await get(thread.id, 'files', notes)).status, 415, 'image bytes are still checked');
    assert.equal((await get(thread.id, 'files', join(dir, 'neighbour.png'))).status, 403, 'a neighbour is not granted');
    // Links beneath a shared temp folder are refused, even ones the answer names.
    for (const file of [join(dir, 'redirect.png'), join(dir, 'redirect-dir', '截图 (1).png'), dir]) {
      assert.equal((await get(thread.id, 'files', file)).status, 404, file);
    }
    for (const file of [join(dir, 'outside.txt'), dir]) assert.equal((await get(thread.id, 'text', file)).status, 404);
    assert.equal((await get(thread.id, 'text', '/etc/passwd')).status, 200, 'a named file is readable wherever it is');
    assert.equal((await get(thread.id, 'tree', dir)).status, 404, 'a file grant never opens the folder');
    assert.equal((await get(thread.id, 'preview-link', notes)).status, 404, 'no directory grant for HTML subresources');
    assert.equal((await get(other.id, 'text', notes)).status, 403);
    assert.equal((await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(path)}`, {}, false)).status, 401);
    // Authorization survives reopening the database; existing answers need no migration or rewrite.
    const reopened = new Store(dataDir);
    try { assert.equal(reopened.referencesFile(thread.id, path), true); } finally { reopened.close(); }
    rmSync(path);
    assert.equal((await get(thread.id, 'files', path)).status, 404, 'a cleaned temporary file is gone');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("files: an agent's own folder for the session is served too, and only that session's", async () => {
  const brain = join(process.env.LOOCHAT_AGY_HOME!, 'brain');
  mkdirSync(join(brain, 'abcdef12-0001'), { recursive: true });
  mkdirSync(join(brain, 'abcdef12-0002'), { recursive: true });
  writeFileSync(join(brain, 'abcdef12-0001', 'made.png'), Buffer.from(PNG, 'base64'));
  writeFileSync(join(brain, 'abcdef12-0002', 'other.png'), Buffer.from(PNG, 'base64'));

  const thread = await newThread({ agent: 'agy' });
  const mine = join(brain, 'abcdef12-0001', 'made.png');
  assert.equal((await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(mine)}`)).status, 403, 'no session yet');
  store.updateThread(thread.id, { agentSessionId: 'abcdef12-0001' });
  const res = await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(mine)}`);
  assert.equal(res.status, 200);
  await res.arrayBuffer();
  const other = join(brain, 'abcdef12-0002', 'other.png');
  assert.equal((await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(other)}`)).status, 403);

  // A claude thread has no such folder, whatever its session is called.
  const claude = await newThread({ agent: 'claude' });
  store.updateThread(claude.id, { agentSessionId: 'abcdef12-0001' });
  assert.equal((await api(`/api/threads/${claude.id}/files?path=${encodeURIComponent(mine)}`)).status, 403);
});

test('browse: folders and text files in the thread directory can be read, and nothing outside it', async () => {
  // A folder of its own, so the other tests' files do not show up in the listings.
  const dir = join(workdir, 'browse-test');
  mkdirSync(join(dir, 'src', 'b'), { recursive: true });
  mkdirSync(join(dir, 'node_modules', 'x'), { recursive: true });
  execFileSync('git', ['init', '-q'], { cwd: dir });
  writeFileSync(join(dir, '.gitignore'), 'node_modules/\n');
  writeFileSync(join(dir, 'src', 'a10.ts'), 'export const ten = 10;\n');
  writeFileSync(join(dir, 'src', 'a2.ts'), 'export const two = 2;\n');
  writeFileSync(join(dir, 'blob.bin'), Buffer.from([1, 2, 0, 3]));
  writeFileSync(join(dir, 'big.txt'), 'line of text\n'.repeat(100_000));
  writeFileSync(join(dataDir, 'secret.txt'), 'not for the page');
  symlinkSync(join(dataDir, 'secret.txt'), join(dir, 'leak.txt'));
  symlinkSync(join(dataDir, 'missing'), join(dir, 'dangling'));
  try {
    const thread = await newThread({ agent: 'claude', cwd: dir });
    const tree = (path?: string) => api(`/api/threads/${thread.id}/tree${path === undefined ? '' : `?path=${encodeURIComponent(path)}`}`);
    const text = (path: string) => api(`/api/threads/${thread.id}/text?path=${encodeURIComponent(path)}`);
    const real = realpathSync(dir);

    const top = (await (await tree()).json()) as { root: string; path: string; entries: Array<{ name: string; type: string; ignored?: boolean; size?: number }> };
    assert.equal(top.root, real);
    assert.equal(top.path, real);
    assert.deepEqual(
      top.entries.map((e) => [e.name, e.type, Boolean(e.ignored)]),
      [
        ['node_modules', 'dir', true],
        ['src', 'dir', false],
        ['.gitignore', 'file', false],
        ['big.txt', 'file', false],
        ['blob.bin', 'file', false],
        ['leak.txt', 'file', false],
      ],
      'folders first, .git and dangling links left out, ignored ones marked',
    );
    const src = (await (await tree('src')).json()) as typeof top;
    assert.deepEqual(src.entries.map((e) => e.name), ['b', 'a2.ts', 'a10.ts'], 'numbers sort as numbers');
    assert.equal(src.entries[1].size, 22);

    const a2 = (await (await text(join(dir, 'src', 'a2.ts'))).json()) as { root: string; path: string; text: string | null; truncated: boolean; size: number };
    assert.deepEqual(a2, { root: real, path: join(real, 'src', 'a2.ts'), size: 22, text: 'export const two = 2;\n', truncated: false, hash: contentHash('export const two = 2;\n'), editable: false });
    const blob = (await (await text('blob.bin')).json()) as typeof a2;
    assert.equal(blob.text, null, 'a binary file has no text');
    const big = (await (await text('big.txt')).json()) as typeof a2;
    assert.ok(big.truncated);
    assert.ok(big.text!.length <= 1024 * 1024 && big.text!.endsWith('line of text\n'), 'cut at a whole line');

    const find = async (q: string) => ((await (await api(`/api/threads/${thread.id}/find?q=${encodeURIComponent(q)}`)).json()) as { paths: string[] }).paths;
    writeFileSync(join(dir, 'src', 'b', 'two-notes.md'), '# notes\n');
    assert.deepEqual(await find('a2'), ['src/a2.ts']);
    assert.deepEqual(await find('TWO'), ['src/b/two-notes.md'], 'case does not matter; file contents are not searched');
    assert.deepEqual(await find('src ts'), ['src/a2.ts', 'src/a10.ts'], 'every word must match');
    assert.deepEqual(await find('node_modules'), [], 'ignored files are not offered');
    assert.deepEqual(await find(''), []);

    assert.equal((await text('leak.txt')).status, 404, 'a link that leads outside');
    assert.equal((await text(join(dataDir, 'secret.txt'))).status, 403);
    assert.equal((await text('../browse-test/../../' + 'x')).status, 404);
    assert.equal((await text('src')).status, 404, 'a folder is not a file');
    assert.equal((await text('')).status, 400);
    assert.equal((await tree('src/a2.ts')).status, 404, 'a file is not a folder');
    const up = (await tree('..')).json() as Promise<{ root: string }>;
    assert.equal((await up).root, realpathSync(workdir), 'the project root above the thread is itself a browse root');
    assert.equal((await tree('../..')).status, 404, 'above the project root is outside');
    assert.equal((await tree(dataDir)).status, 404);
    assert.equal((await api(`/api/threads/${thread.id}/tree`, {}, false)).status, 401);
    assert.equal((await api(`/api/threads/${thread.id}/text?path=src/a2.ts`, { method: 'POST' })).status, 404, 'read only');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('browse: the file browser stands on the thread directory, project directories and the home directory', async () => {
  const second = mkdtempSync(join(tmpdir(), 'loochat-second-root-'));
  const homeCorner = mkdtempSync(join(homedir(), '.loochat-home-browse-'));
  const corner = mkdirSync(join(workdir, 'browse-roots'), { recursive: true });
  writeFileSync(join(second, 'util.ts'), 'export const u = 1;\n');
  writeFileSync(join(corner, 'local.txt'), 'thread file\n');
  writeFileSync(join(homeCorner, 'note.md'), '# home note\n');
  try {
    assert.equal((await api('/api/workdirs', { method: 'POST', body: JSON.stringify({ path: second }) })).status, 200);
    const thread = await newThread({ agent: 'claude', cwd: corner });
    const tree = (path?: string) => api(`/api/threads/${thread.id}/tree${path === undefined ? '' : `?path=${encodeURIComponent(path)}`}`);
    const roots = (await (await api(`/api/threads/${thread.id}/roots`)).json()) as { roots: Array<{ path: string; kind: string }> };
    assert.deepEqual(roots.roots, [
      { path: realpathSync(corner), kind: 'thread' },
      { path: realpathSync(workdir), kind: 'project' },
      { path: realpathSync(second), kind: 'project' },
      { path: realpathSync(homedir()), kind: 'home' },
    ], 'most specific first, the thread directory deduped against the project root');

    const project = (await (await tree(second)).json()) as { entries: Array<{ name: string }> };
    assert.ok(project.entries.some((e) => e.name === 'util.ts'), 'another project root is browsable');
    const found = (await (await api(`/api/threads/${thread.id}/find?q=util&path=${encodeURIComponent(second)}`)).json()) as { paths: string[] };
    assert.deepEqual(found.paths, ['util.ts'], 'the filter searches the root it is given');
    const local = (await (await api(`/api/threads/${thread.id}/find?q=local`)).json()) as { paths: string[] };
    assert.deepEqual(local.paths, ['local.txt'], 'without a path it searches the thread directory');

    const homeTree = (await (await tree(homeCorner)).json()) as typeof project;
    assert.ok(homeTree.entries.some((e) => e.name === 'note.md'), 'the home directory is browsable');
    const note = (await (await api(`/api/threads/${thread.id}/text?path=${encodeURIComponent(join(homeCorner, 'note.md'))}`)).json()) as { text: string };
    assert.equal(note.text, '# home note\n');

    assert.equal((await tree(tmpdir())).status, 404, 'the temp root is not a browse root');
    assert.equal((await tree('/etc')).status, 404, 'nor is the system');

    // A removed project root stops being browsable at once.
    assert.equal((await api(`/api/workdirs?path=${encodeURIComponent(second)}`, { method: 'DELETE' })).status, 200);
    assert.equal((await tree(second)).status, 404);
  } finally {
    rmSync(second, { recursive: true, force: true });
    rmSync(homeCorner, { recursive: true, force: true });
    rmSync(corner, { recursive: true, force: true });
  }
});

test('browse: ~ paths open, as answers and the file browser spell them', async () => {
  const corner = mkdtempSync(join(homedir(), '.loochat-tilde-'));
  const file = join(corner, 'answer.md');
  writeFileSync(file, '# written home\n');
  try {
    const thread = await newThread({ agent: 'claude' });
    const spelled = `~/${relative(homedir(), file)}`;
    const text = (path: string) => api(`/api/threads/${thread.id}/text?path=${encodeURIComponent(path)}`);
    const read = await text(spelled);
    assert.equal(read.status, 200, 'a ~ path reaches the file under the home root');
    assert.equal(((await read.json()) as { text: string }).text, '# written home\n');
    const tree = await api(`/api/threads/${thread.id}/tree?path=${encodeURIComponent(spelled.replace(/\/[^/]+$/, ''))}`);
    assert.equal(tree.status, 200, 'a ~ path works for the tree as well');
    const home = await api(`/api/threads/${thread.id}/tree?path=~`);
    assert.equal(home.status, 200, 'a bare ~ is the home directory');
  } finally {
    rmSync(corner, { recursive: true, force: true });
  }
});

test('download serves any readable file whole or by range, with the name it has', async () => {
  const dir = join(workdir, 'download-test');
  mkdirSync(dir, { recursive: true });
  try {
    const bytes = readFileSync(join(here, 'fixtures', 'sample-video.mp4'));
    writeFileSync(join(dir, '火鸡与Claude神.mp4'), bytes);
    writeFileSync(join(dir, 'notes.txt'), 'hello');
    writeFileSync(join(dataDir, 'outside.txt'), 'not for the page');
    const thread = await newThread({ agent: 'claude', cwd: dir });
    const url = (path: string, extra = '') => `/api/threads/${thread.id}/download?path=${encodeURIComponent(path)}${extra}`;

    assert.equal((await api(url('notes.txt'), {}, false)).status, 401, 'needs the token');
    assert.equal((await api(url('notes.txt'), {}, true)).status, 200);

    // Whole file: the bytes, the type, and the name to save it under.
    const whole = await api(url('火鸡与Claude神.mp4'));
    assert.equal(whole.status, 200);
    assert.equal(whole.headers.get('content-type'), 'video/mp4');
    assert.equal(whole.headers.get('accept-ranges'), 'bytes');
    assert.equal(whole.headers.get('x-content-type-options'), 'nosniff');
    assert.equal(Number(whole.headers.get('content-length')), bytes.length);
    assert.match(whole.headers.get('content-disposition') ?? '', /^inline;/);
    assert.match(whole.headers.get('content-disposition') ?? '', /filename\*=UTF-8''%E7%81%AB/);
    assert.deepEqual(Buffer.from(await whole.arrayBuffer()), bytes);

    // `?dl=1` asks for the save prompt rather than an inline open.
    assert.match((await api(url('notes.txt', '&dl=1'))).headers.get('content-disposition') ?? '', /^attachment;/);

    // A range: only those bytes, and the browser is told which ones.
    const part = await api(url('notes.txt'), { headers: { Range: 'bytes=1-3' } });
    assert.equal(part.status, 206);
    assert.equal(part.headers.get('content-range'), 'bytes 1-3/5');
    assert.equal(await part.text(), 'ell');
    const tail = await api(url('notes.txt'), { headers: { Range: 'bytes=-2' } });
    assert.equal(tail.status, 206);
    assert.equal(tail.headers.get('content-range'), 'bytes 3-4/5');
    assert.equal(await tail.text(), 'lo');
    const open = await api(url('notes.txt'), { headers: { Range: 'bytes=2-' } });
    assert.equal(open.headers.get('content-range'), 'bytes 2-4/5');
    assert.equal(await open.text(), 'llo');
    // Past the end, and a range that cannot be met, are refused rather than served wrong.
    const past = await api(url('notes.txt'), { headers: { Range: 'bytes=9-20' } });
    assert.equal(past.status, 416);
    assert.equal(past.headers.get('content-range'), 'bytes */5');

    assert.equal((await api(url('missing.txt'))).status, 404);
    assert.equal((await api('/api/threads/nope/download?path=x')).status, 404);
    assert.equal((await api(url(''))).status, 400, 'a path is required');
    // A file outside the thread that no answer named is not downloadable.
    assert.equal((await api(url(join(dataDir, 'outside.txt')))).status, 403);
    assert.equal((await api(`/api/threads/${thread.id}/download?path=${encodeURIComponent(dir)}`)).status, 404, 'a folder is not a file');

    // A signed link: what a <video> tag or a save link uses, since neither can send the token.
    const link = (await (await api(`/api/threads/${thread.id}/download-link?path=${encodeURIComponent('火鸡与Claude神.mp4')}&dl=1`)).json()) as { url: string; name: string; size: number; mimeType: string };
    assert.equal(link.name, '火鸡与Claude神.mp4');
    assert.equal(link.size, bytes.length);
    assert.equal(link.mimeType, 'video/mp4');
    // Fetched with no Authorization header at all, exactly as the browser would.
    const signed = await fetch(base + link.url, { headers: { Range: 'bytes=0-99' } });
    assert.equal(signed.status, 206);
    assert.equal(signed.headers.get('content-range'), `bytes 0-99/${bytes.length}`);
    assert.match(signed.headers.get('content-disposition') ?? '', /^attachment;/);
    assert.deepEqual(Buffer.from(await signed.arrayBuffer()), bytes.subarray(0, 100));
    // The link names one file: another path under the same signature is refused.
    const retargeted = link.url.replace(encodeURIComponent('火鸡与Claude神.mp4'), encodeURIComponent('notes.txt'));
    assert.equal((await fetch(base + retargeted)).status, 403);
    // A tampered signature, and one that ran out, are refused too.
    assert.equal((await fetch(base + link.url.replace(/sig=[0-9a-f]+/, 'sig=' + 'a'.repeat(64)))).status, 403);
    assert.equal((await fetch(base + link.url.replace(/exp=\d+/, 'exp=1'))).status, 403);
    assert.equal((await api(`/api/threads/${thread.id}/download-link?path=${encodeURIComponent('missing.txt')}`)).status, 404, 'a link is only minted for a file that is there');
    assert.equal((await api(`/api/threads/${thread.id}/download-link?path=${encodeURIComponent(join(dataDir, 'outside.txt'))}`)).status, 403);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('commands: agy lists and answers its own commands; the answer is a normal turn in the thread', async () => {
  const list = (await (await api(`/api/agents/agy/commands?cwd=${encodeURIComponent(workdir)}`)).json()) as Array<{ name: string; kind: string }>;
  assert.ok(list.some((c) => c.name === 'usage' && c.kind === 'command'));
  assert.ok(list.some((c) => c.name === 'make-video' && c.kind === 'skill'));
  assert.ok(!list.some((c) => c.name === 'model'));

  const thread = await newThread({ agent: 'agy' });
  await post(`/api/threads/${thread.id}/turns`, { text: '/usage' });
  await untilIdle(thread.id);
  const log = await events(thread.id);
  assert.deepEqual(log.filter((e) => e.type.startsWith('turn.')).map((e) => e.type), ['turn.started', 'turn.phase', 'turn.phase', 'turn.phase', 'turn.done']);
  assert.match(log.find((e) => e.type === 'message.delta')!.data.text, /\|Fast models\|Weekly Limit Remaining\|97%\|/);
});

test('a model that cannot see accepts pictures when the agent has a way to show them to it', async () => {
  process.env.FAKE_PI_VISION = '1';
  const pi = new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')]);
  try {
    await pi.models(); // the probe runs now, while the extension is "installed"
  } finally {
    delete process.env.FAKE_PI_VISION;
  }
  const withVision = new ThreadManager(store, new Map<string, AgentAdapter>([['pi', pi]]), [workdir], new AttachmentStore(dataDir));
  const thread = await withVision.createThread({ agent: 'pi', cwd: workdir, model: 'fake/plain' });
  const { turnId } = (await withVision.startTurn(thread.id, { text: 'look', images: [{ data: PNG }] })) as { turnId: string };
  const log = await waitFor(async () => {
    const all = withVision.listEvents(thread.id);
    return all.some((e) => e.turnId === turnId && e.type === 'turn.done') ? all : null;
  });
  const said = log.filter((e) => e.type === 'message.delta').map((e) => String(e.data.text)).join('');
  assert.match(said, /The user attached an image/);
  await withVision.shutdown();
});

test('a new thread can be filed into a section right away', async () => {
  const section = (await (await api('/api/sections', { method: 'POST', body: JSON.stringify({ name: '支付重构' }) })).json()) as { id: string };
  const bad = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir, sectionId: 'nope' }) });
  assert.equal(bad.status, 400);
  const thread = await newThread({ agent: 'pi', cwd: workdir, sectionId: section.id });
  assert.equal((await viewOf(thread.id)).sectionId, section.id);
});

test('sync: turns the terminal added appear, are marked, and warn until the hub writes again', async () => {
  const messagesFile = join(dataDir, 'pi-terminal-messages.json');
  process.env.FAKE_PI_MESSAGES_FILE = messagesFile;
  try {
    const thread = await newThread({ agent: 'pi', cwd: workdir });
    await post(`/api/threads/${thread.id}/turns`, { text: 'hello' });
    await untilIdle(thread.id);
    const view = await viewOf(thread.id);
    assert.equal(view.agentSessionId, 'pi-session-1');
    assert.equal(view.terminalActivity, false);

    // The terminal carries on where the hub left off: same first turn, one more on top.
    writeFileSync(
      messagesFile,
      JSON.stringify({
        'pi-session-1': [
          { role: 'user', content: 'hello' },
          { role: 'assistant', content: [{ type: 'text', text: 'hi there hello' }] },
          { role: 'user', content: 'and from the terminal?' },
          { role: 'assistant', content: [{ type: 'text', text: 'done in the terminal' }] },
        ],
      }),
    );
    const res = await api(`/api/threads/${thread.id}/sync`, { method: 'POST' });
    assert.equal(res.status, 200);
    const { added, terminalActivity } = (await res.json()) as { added: number; terminalActivity: boolean };
    assert.equal(added, 1);
    assert.equal(terminalActivity, true);

    const log = await events(thread.id);
    const imported = log.filter((e) => e.data.fromTerminal === true);
    assert.deepEqual(imported.map((e) => e.type), ['turn.started', 'turn.done']);
    assert.equal(imported[0].data.text, 'and from the terminal?');
    assert.equal((await viewOf(thread.id)).terminalActivity, true);

    // Nothing new since the last sync: a second call adds nothing.
    assert.equal(((await (await api(`/api/threads/${thread.id}/sync`, { method: 'POST' })).json()) as { added: number }).added, 0);

    // Writing from here clears the warning.
    await post(`/api/threads/${thread.id}/turns`, { text: 'back on the web' });
    await untilIdle(thread.id);
    assert.equal((await viewOf(thread.id)).terminalActivity, false);
  } finally {
    delete process.env.FAKE_PI_MESSAGES_FILE;
  }
});

test('sync: a hub turn sent while the history is being read is not copied back as a terminal turn', async () => {
  const messagesFile = join(dataDir, 'pi-terminal-messages-race.json');
  process.env.FAKE_PI_MESSAGES_FILE = messagesFile;
  process.env.FAKE_PI_MESSAGES_DELAY_MS = '600';
  try {
    const thread = await newThread({ agent: 'pi', cwd: workdir });
    await post(`/api/threads/${thread.id}/turns`, { text: 'hello' });
    await untilIdle(thread.id);

    const syncing = api(`/api/threads/${thread.id}/sync`, { method: 'POST' });
    await new Promise((r) => setTimeout(r, 200));
    // The person sends while the sync is still reading; the agent records the message at once.
    await post(`/api/threads/${thread.id}/turns`, { text: 'slow, sent mid-sync' });
    writeFileSync(
      messagesFile,
      JSON.stringify({
        'pi-session-1': [
          { role: 'user', content: 'hello' },
          { role: 'assistant', content: [{ type: 'text', text: 'hi there hello' }] },
          { role: 'user', content: 'slow, sent mid-sync' },
        ],
      }),
    );
    const { added } = (await (await syncing).json()) as { added: number };
    assert.equal(added, 0);
    assert.equal((await events(thread.id)).filter((e) => e.data.fromTerminal === true).length, 0);
    await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' });
    await untilIdle(thread.id);
  } finally {
    delete process.env.FAKE_PI_MESSAGES_FILE;
    delete process.env.FAKE_PI_MESSAGES_DELAY_MS;
  }
});

test('sync: a hub turn the agent never recorded is skipped, not treated as divergence', async () => {
  const messagesFile = join(dataDir, 'pi-terminal-messages-2.json');
  process.env.FAKE_PI_MESSAGES_FILE = messagesFile;
  try {
    const thread = await newThread({ agent: 'pi', cwd: workdir });
    await post(`/api/threads/${thread.id}/turns`, { text: 'slow' }); // cancelled before the agent settled
    await waitFor(async () => ((await viewOf(thread.id)).status === 'running' ? true : null));
    await api(`/api/threads/${thread.id}/cancel`, { method: 'POST' });
    await untilIdle(thread.id);
    assert.deepEqual(await turnTexts(thread.id), ['slow']);

    writeFileSync(
      messagesFile,
      JSON.stringify({
        'pi-session-1': [
          { role: 'user', content: 'slow' },
          { role: 'assistant', content: [{ type: 'text', text: 'was interrupted' }] },
          { role: 'user', content: 'terminal follow-up' },
          { role: 'assistant', content: [{ type: 'text', text: 'answered in terminal' }] },
        ],
      }),
    );
    const { added } = (await (await api(`/api/threads/${thread.id}/sync`, { method: 'POST' })).json()) as { added: number };
    // 'slow' matches, so the follow-up is new; the hub's own cancelled turn is not in the agent's list.
    assert.equal(added, 1);
    assert.equal((await turnTexts(thread.id)).at(-1), 'terminal follow-up');
  } finally {
    delete process.env.FAKE_PI_MESSAGES_FILE;
  }
});

test('sync: histories that truly disagree append nothing rather than something wrong', async () => {
  const messagesFile = join(dataDir, 'pi-terminal-messages-3.json');
  process.env.FAKE_PI_MESSAGES_FILE = messagesFile;
  try {
    const thread = await newThread({ agent: 'pi', cwd: workdir });
    for (const text of ['one', 'two', 'three', 'four']) {
      await post(`/api/threads/${thread.id}/turns`, { text });
      await untilIdle(thread.id);
    }

    // The agent's side tells a different story from the second turn on.
    writeFileSync(
      messagesFile,
      JSON.stringify({
        'pi-session-1': [
          { role: 'user', content: 'one' },
          { role: 'assistant', content: [{ type: 'text', text: 'a' }] },
          { role: 'user', content: 'something else entirely' },
          { role: 'assistant', content: [{ type: 'text', text: 'b' }] },
        ],
      }),
    );
    const { added } = (await (await api(`/api/threads/${thread.id}/sync`, { method: 'POST' })).json()) as { added: number };
    assert.equal(added, 0);
    assert.equal((await turnTexts(thread.id)).length, 4);
  } finally {
    delete process.env.FAKE_PI_MESSAGES_FILE;
  }
});

test('sync: threads without a session, or with an agent that cannot read history, are refused', async () => {
  const thread = await newThread({ agent: 'pi', cwd: workdir });
  assert.equal((await api(`/api/threads/${thread.id}/sync`, { method: 'POST' })).status, 400);
  const agy = await newThread({ agent: 'agy', cwd: workdir });
  await post(`/api/threads/${agy.id}/turns`, { text: 'hi' });
  await untilIdle(agy.id);
  assert.equal((await api(`/api/threads/${agy.id}/sync`, { method: 'POST' })).status, 400);
});

test('agents: instances beyond the built-ins are listed, and PATH discovery offers extras', async () => {
  const registry = new AgentRegistry(
    [
      ...defaultInstances({
        claudeCommand: [process.execPath, join(here, 'fixtures', 'fake-acp.mjs')],
        piCommand: [process.execPath, join(here, 'fixtures', 'fake-pi.mjs')],
        agyCommand: [process.execPath, join(here, 'fixtures', 'fake-agy.mjs')],
        traexCommand: [process.execPath, join(here, 'fixtures', 'fake-acp.mjs'), 'acp', 'serve'],
      }),
    ],
    null,
  );
  const withRegistryManager = new ThreadManager(store, registry.adapters, [workdir], new AttachmentStore(dataDir));
  const withRegistry = createHttpServer({ manager: withRegistryManager, registry, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => withRegistry.listen(0, '127.0.0.1', () => r()));
  const { port } = withRegistry.address() as AddressInfo;
  const regBase = `http://127.0.0.1:${port}`;
  const regApi = (path: string, init: RequestInit = {}) =>
    fetch(regBase + path, { ...init, headers: { 'Content-Type': 'application/json', ...auth, ...(init.headers ?? {}) } });

  // A second pi account, same CLI, its own environment.
  const added = await regApi('/api/agents', {
    method: 'POST',
    body: JSON.stringify({ id: 'pi2', adapter: 'pi', label: 'pi 备用', command: [process.execPath, join(here, 'fixtures', 'fake-pi.mjs')], env: { FAKE_PI_LABEL: 'pi2' } }),
  });
  assert.equal(added.status, 201);
  const listed = (await (await regApi('/api/agents')).json()) as Array<{ id: string; label: string; available: boolean }>;
  const second = listed.find((a) => a.id === 'pi2');
  assert.ok(second?.available);
  assert.equal(second.label, 'pi 备用');

  // The instance runs with its own environment: the fake pi says so in its reply.
  const made = (await (await regApi('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi2', cwd: workdir }) })).json()) as { id: string };
  await regApi(`/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  const said = await waitFor(async () => {
    const log = (await (await regApi(`/api/threads/${made.id}/events`)).json()) as Array<{ type: string; data: any }>;
    const done = log.some((e) => e.type === 'turn.done');
    return done ? log.filter((e) => e.type === 'message.delta').map((e) => String(e.data.text)).join('') : null;
  });
  assert.match(said, /\[pi2\] there hello/);

  // Supported agents: returns all built-in adapters with labels and install guides
  const supported = (await (await regApi('/api/agents/supported')).json()) as Array<{ adapter: string; label: string; installGuide?: string }>;
  assert.ok(supported.length >= 6);
  assert.ok(supported.some((s) => s.adapter === 'claude' && s.label === 'Claude Code' && s.installGuide));
  assert.ok(supported.some((s) => s.adapter === 'pi' && s.installGuide));

  // PATH discovery: an agy2 shim is offered; a lookalike is not.
  const bin = join(dataDir, 'discover-bin');
  mkdirSync(bin);
  for (const name of ['agy2', 'ping']) {
    writeFileSync(join(bin, name), `#!/bin/sh\nexec "${process.execPath}" "${join(here, 'fixtures', 'fake-agy.mjs')}" "$@"\n`, { mode: 0o755 });
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${bin}:${oldPath}`;
  try {
    const found = (await (await regApi('/api/agents/discover')).json()) as Array<{ id: string; adapter: string; command: string; label?: string; icon?: string }>;
    const ours = found.filter((f) => f.command.startsWith(bin));
    assert.deepEqual(ours.map((f) => f.id), ['agy2']);
    assert.equal(ours[0].adapter, 'agy');
    assert.equal(ours[0].label, 'Antigravity');
    assert.ok(ours[0].icon);
  } finally {
    process.env.PATH = oldPath;
  }

  // Account discovery: a signed-in second account is offered ready to link, id and label included.
  const home = join(dataDir, 'accounts-home');
  mkdirSync(join(home, '.claude-2'), { recursive: true });
  const claudeBin = join(home, 'claude');
  writeFileSync(claudeBin, '#!/bin/sh\necho \'{"loggedIn":true,"email":"second@example.com"}\'\n', { mode: 0o755 });
  const oldBin = process.env.LOOCHAT_CLAUDE_BIN;
  const oldHome = process.env.HOME;
  process.env.LOOCHAT_CLAUDE_BIN = claudeBin;
  process.env.HOME = home;
  try {
    const accounts = (await (await regApi('/api/agents/accounts/discover')).json()) as Array<{
      adapter: string; email: string; env: Record<string, string>; id: string; label: string;
    }>;
    assert.deepEqual(accounts, [
      {
        adapter: 'claude',
        email: 'second@example.com',
        env: { CLAUDE_CONFIG_DIR: join(home, '.claude-2') },
        id: 'claude-2',
        label: 'Claude Code 2',
      },
    ]);

    // Linking it is the existing add-instance route with the env the discovery built.
    const linked = await regApi('/api/agents', {
      method: 'POST',
      body: JSON.stringify({ id: accounts[0].id, adapter: 'claude', label: accounts[0].label, env: accounts[0].env }),
    });
    assert.equal(linked.status, 201);
    const empty = (await (await regApi('/api/agents/accounts/discover')).json()) as unknown[];
    assert.deepEqual(empty, []);
  } finally {
    if (oldBin === undefined) delete process.env.LOOCHAT_CLAUDE_BIN;
    else process.env.LOOCHAT_CLAUDE_BIN = oldBin;
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
  }
  withRegistry.close();
  await withRegistryManager.shutdown();
});

test('agents: rename, hide, restore and removal leave a used instance in place', async () => {
  const data = mkdtempSync(join(tmpdir(), 'loochat-agents-manage-api-'));
  const ownStore = new Store(data);
  const registry = new AgentRegistry(defaultInstances({
    claudeCommand: [process.execPath, join(here, 'fixtures', 'fake-acp.mjs')],
    piCommand: [process.execPath, join(here, 'fixtures', 'fake-pi.mjs')],
    agyCommand: [process.execPath, join(here, 'fixtures', 'fake-agy.mjs')],
    traexCommand: [process.execPath, join(here, 'fixtures', 'fake-acp.mjs'), 'acp', 'serve'],
    codexCommand: [process.execPath, join(here, 'fixtures', 'fake-codex.mjs')],
  }), null);
  const ownManager = new ThreadManager(ownStore, registry.adapters, [workdir], new AttachmentStore(data), () => registry.list());
  const own = createHttpServer({ manager: ownManager, registry, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => own.listen(0, '127.0.0.1', () => r()));
  const ownBase = `http://127.0.0.1:${(own.address() as AddressInfo).port}`;
  const call = (path: string, init: RequestInit = {}) => fetch(ownBase + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}`, ...(init.headers ?? {}) },
  });
  try {
    const renamed = await call('/api/agents/pi', { method: 'PATCH', body: JSON.stringify({ label: 'pi 工作号' }) });
    assert.equal(renamed.status, 200);
    assert.equal(((await renamed.json()) as { label: string }).label, 'pi 工作号');

    // A thread, archived or not, keeps the instance from disappearing under it.
    const made = await call('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) });
    assert.equal(made.status, 201);
    const thread = (await made.json()) as { id: string };
    for (const hidden of [true, false]) {
      const patched = await call('/api/agents/pi', { method: 'PATCH', body: JSON.stringify({ hidden }) });
      assert.equal(patched.status, 200);
      assert.equal(((await patched.json()) as { hidden: boolean }).hidden, hidden);
      const catalog = (await (await call('/api/agents')).json()) as Array<{ id: string; hidden?: boolean; label: string }>;
      assert.equal(catalog.find(agent => agent.id === 'pi')?.hidden, hidden);
      assert.equal(catalog.find(agent => agent.id === 'pi')?.label, 'pi 工作号');
      assert.equal((await call(`/api/threads/${thread.id}`)).status, 200);
      assert.equal(registry.adapters.has('pi'), true);
    }
    for (const patch of [{}, { hidden: 'true' }, { label: '', hidden: true }]) {
      assert.equal((await call('/api/agents/pi', { method: 'PATCH', body: JSON.stringify(patch) })).status, 400);
    }
    assert.equal((await call('/api/agents/nope', { method: 'PATCH', body: JSON.stringify({ hidden: true }) })).status, 404);
    const refs = (await (await call('/api/agents/pi/references')).json()) as { threads: number };
    assert.ok(refs.threads >= 1);
    const blocked = await call('/api/agents/pi', { method: 'DELETE' });
    assert.equal(blocked.status, 409);
    const body = (await blocked.json()) as { references: { threads: number } };
    assert.ok(body.references.threads >= 1);

    // An unused instance goes, and the list no longer offers it.
    assert.equal((await call('/api/agents/agy', { method: 'DELETE' })).status, 204);
    const listed = (await (await call('/api/agents')).json()) as Array<{ id: string }>;
    assert.equal(listed.some((a) => a.id === 'agy'), false);
    assert.equal((await call('/api/agents/nope', { method: 'DELETE' })).status, 404);
  } finally {
    own.close();
    await ownManager.shutdown();
    ownStore.close();
    rmSync(data, { recursive: true, force: true });
  }
});

/** Appends events straight to the log, the way a turn would have, without running an agent. */
function writeLog(threadId: string, turnId: string | null, entries: Array<[type: Parameters<Store['appendEvent']>[2], data: Record<string, unknown>]>) {
  store.transaction(() => {
    for (const [type, data] of entries) store.appendEvent(threadId, turnId, type, data);
  });
}

type Logged = { seq: number; type: string; turnId: string | null; data: Record<string, any> };

test('events endpoint: a page begins where a turn begins, and streamed fragments come merged', async () => {
  const thread = await newThread({ agent: 'pi', cwd: workdir });
  const page = async (query: string) => (await (await api(`/api/threads/${thread.id}/events?${query}`)).json()) as Logged[];

  // A short turn, then one far longer than a page reaches back.
  writeLog(thread.id, 'a', [
    ['turn.started', { text: 'first' }],
    ['reasoning.delta', { text: 'th' }],
    ['reasoning.delta', { text: 'ink' }],
    ['reasoning.delta', { text: 'ing' }],
    ['tool.start', { id: 't1', title: 'ls', kind: 'execute' }],
    ['tool.end', { id: 't1', status: 'completed' }],
    ['message.delta', { text: 'one ' }],
    ['message.delta', { text: 'two' }],
    ['turn.done', { stopReason: 'end_turn' }],
  ]);
  writeLog(thread.id, 'b', [
    ['turn.started', { text: 'second' }],
    ...Array.from({ length: 21_000 }, (_, i): ['reasoning.delta', { text: string }] => ['reasoning.delta', { text: i % 2 ? 'b' : 'a' }]),
  ]);

  // The newest turn is longer than the budget and still running: it comes whole, from its
  // question, and its fragments come as the first one plus the rest.
  const running = await page('tail=1');
  assert.deepEqual(running.map((e) => e.type), ['turn.started', 'reasoning.delta', 'reasoning.delta']);
  assert.equal(running[0].data.text, 'second');
  assert.equal(running.slice(1).map((e) => e.data.text).join(''), 'ab'.repeat(10_500));
  const all = (await (await api(`/api/threads/${thread.id}/events?after=${running[0].seq}`)).json()) as Logged[];
  assert.equal(running[1].seq, all[0].seq, 'the first fragment keeps its own seq and time');
  assert.equal(running.at(-1)!.seq, running[0].seq + 21_000, 'the merged rest carries the last seq, so a stream can resume after it');

  writeLog(thread.id, 'b', [['turn.done', { stopReason: 'end_turn' }]]);
  writeLog(thread.id, 'c', [
    ['turn.started', { text: 'third' }],
    ['message.delta', { text: 'short' }],
    ['turn.done', { stopReason: 'end_turn' }],
  ]);

  // Now the long turn is out of reach of the tail: the page is the last turn alone.
  const tail = await page('tail=1');
  assert.deepEqual(tail.map((e) => [e.type, e.turnId]), [['turn.started', 'c'], ['message.delta', 'c'], ['turn.done', 'c']]);

  // Paging back gives the turn before, whole.
  const second = await page(`before=${tail[0].seq}`);
  assert.deepEqual(second.map((e) => e.type), ['turn.started', 'reasoning.delta', 'reasoning.delta', 'turn.done']);
  assert.equal(second.at(-1)!.seq, tail[0].seq - 1, 'pages meet without a gap');

  // The first turn brings what precedes it, back to the start of the log; fragments of
  // different kinds, or with something between them, are not merged into each other.
  const first = await page(`before=${second[0].seq}`);
  assert.equal(first[0].seq, 1);
  assert.deepEqual(
    first.filter((e) => e.turnId === 'a').map((e) => [e.type, e.data.text ?? e.data.id ?? null]),
    [
      ['turn.started', 'first'],
      ['reasoning.delta', 'th'],
      ['reasoning.delta', 'inking'],
      ['tool.start', 't1'],
      ['tool.end', 't1'],
      ['message.delta', 'one '],
      ['message.delta', 'two'],
      ['turn.done', null],
    ],
  );
  assert.deepEqual(await page(`before=${first[0].seq}`), [], 'nothing before the start');

  // The whole log in one answer, for copying the conversation: every turn, fragments merged.
  const whole = await page('all=1');
  assert.deepEqual(whole.filter((e) => e.type === 'turn.started').map((e) => e.data.text), ['first', 'second', 'third']);
  assert.ok(whole.length < 30, `21,000 fragments come as a handful of events (${whole.length})`);
  assert.equal((await api('/api/threads/nope/events?tail=1')).status, 404);
});

test('history pages limit short turns too, and paging back preserves every event needed for replay', async () => {
  const thread = await newThread({ agent: 'pi', cwd: workdir });
  for (let i = 0; i < 8; i++) {
    writeLog(thread.id, `turn-${i}`, [
      ['turn.started', { text: `question ${i}` }],
      ['tool.start', { id: `tool-${i}`, kind: 'execute', title: 'check', status: 'in_progress' }],
      ...Array.from({ length: 30 }, (_, n): ['tool.update', Record<string, unknown>] => ['tool.update', { id: `tool-${i}`, output: `snapshot ${n}\n${'output\n'.repeat(1000)}` }]),
      ['tool.end', { id: `tool-${i}`, status: 'completed' }],
      ['message.delta', { text: `answer ${i}` }],
      ['turn.done', { stopReason: 'end_turn' }],
    ]);
  }
  const page = async (query: string) => (await (await api(`/api/threads/${thread.id}/events?${query}`)).json()) as Logged[];
  let current = await page('tail=1');
  assert.deepEqual(current.filter(e => e.type === 'turn.started').map(e => e.data.text), ['question 5', 'question 6', 'question 7']);
  let complete = current;
  while (current[0].seq > 1) {
    const older = await page(`before=${current[0].seq}`);
    assert.ok(older.length > 0);
    assert.ok(older.filter(e => e.type === 'turn.started').length <= 3);
    assert.equal(older.at(-1)!.seq + 1, current[0].seq, 'page boundaries have no gaps');
    complete = [...older, ...complete];
    current = older;
  }
  assert.deepEqual(complete, await page('all=1'), 'paging reconstructs the same complete replay');
  const tools = complete.filter(e => e.type === 'tool.end');
  assert.equal(tools.length, 8);
  assert.ok(tools.every(e => e.data.output.startsWith('snapshot 29\n') && e.data.status === 'completed'));
  // Snapshots collapse at write time: one stored update per call, the newest (see
  // docs/tool-output-storage-design.md); the merged replay above reads the same text.
  const storedUpdates = store.listEventsOfType(thread.id, 'tool.update');
  assert.equal(storedUpdates.length, 8, 'one stored snapshot per call');
  assert.ok(storedUpdates.every(e => String(e.data.output).startsWith('snapshot 29\n')));
});

test('SSE stream replays everything after the cursor, however long, before going live', async () => {
  const thread = await newThread({ agent: 'pi', cwd: workdir });
  // More than one page of the stored log, none of it mergeable.
  writeLog(thread.id, 'a', [
    ['turn.started', { text: 'long' }],
    ...Array.from({ length: 6000 }, (_, i): ['tool.update', { id: string }] => ['tool.update', { id: `u${i}` }]),
    ['turn.done', { stopReason: 'end_turn' }],
  ]);
  const res = await api(`/api/threads/${thread.id}/stream?after=1`);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let text = '';
  const deadline = Date.now() + 10_000;
  while (!text.includes('event: turn.done') && Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
  }
  await reader.cancel();
  assert.equal(text.split('event: tool.update').length - 1, 6000, 'no hole between the replay and the live events');
  assert.ok(text.includes('event: turn.done'));
});

test('sync: a hub turn deep in a long log is still known, and is not appended again', async () => {
  const messagesFile = join(dataDir, 'pi-terminal-messages.json');
  process.env.FAKE_PI_MESSAGES_FILE = messagesFile;
  try {
    const thread = await newThread({ agent: 'pi', cwd: workdir });
    await post(`/api/threads/${thread.id}/turns`, { text: 'hello' });
    await untilIdle(thread.id);
    // A long stretch of output, then a second turn far past the first page of the log.
    writeLog(thread.id, null, Array.from({ length: 6000 }, (): ['thread.updated', {}] => ['thread.updated', {}]));
    await post(`/api/threads/${thread.id}/turns`, { text: 'much later' });
    await untilIdle(thread.id);
    const sessionId = (await viewOf(thread.id)).agentSessionId!;

    writeFileSync(
      messagesFile,
      JSON.stringify({
        [sessionId]: [
          { role: 'user', content: 'hello' },
          { role: 'assistant', content: [{ type: 'text', text: 'hi there hello' }] },
          { role: 'user', content: 'much later' },
          { role: 'assistant', content: [{ type: 'text', text: 'hi there much later' }] },
        ],
      }),
    );
    const { added } = (await (await api(`/api/threads/${thread.id}/sync`, { method: 'POST' })).json()) as { added: number };
    assert.equal(added, 0, 'both turns are the hub\'s own');
    assert.equal(store.listEventsOfType(thread.id, 'turn.started').length, 2);
  } finally {
    delete process.env.FAKE_PI_MESSAGES_FILE;
  }
});

test('restart: a turn the last daemon left running is ended as interrupted', async () => {
  const cut = await newThread({ agent: 'pi', cwd: workdir });
  writeLog(cut.id, 'open', [
    ['turn.started', { text: 'never finished' }],
    ['message.delta', { text: 'half an ans' }],
  ]);
  store.updateThread(cut.id, { status: 'running' });
  // Marked running, but its turn did end: only the mark is stale.
  const stale = await newThread({ agent: 'pi', cwd: workdir });
  writeLog(stale.id, 'closed', [
    ['turn.started', { text: 'finished' }],
    ['turn.done', { stopReason: 'end_turn' }],
  ]);
  store.updateThread(stale.id, { status: 'running' });
  const before = (await events(stale.id)).length;

  manager.recoverInterrupted();

  const last = (await events(cut.id)).at(-1)!;
  assert.deepEqual([last.type, last.turnId, last.data.code], ['turn.error', 'open', 'interrupted']);
  assert.equal((await viewOf(cut.id)).status, 'error');
  assert.equal((await viewOf(stale.id)).status, 'idle');
  assert.equal((await events(stale.id)).length, before, 'a turn that ended gets no second ending');

  // The thread works again afterwards.
  await post(`/api/threads/${cut.id}/turns`, { text: 'carry on' });
  await untilIdle(cut.id);
  assert.equal((await viewOf(cut.id)).status, 'idle');
});

test('a page of history collapses a tool call\'s update burst, and the payload travels gzipped', async () => {
  const thread = await newThread({ agent: 'pi', cwd: workdir });
  writeLog(thread.id, 'a1', [
    ['turn.started', { text: 'edit it' }],
    ['tool.start', { id: 'c1', kind: 'edit', title: 'Edit a.ts', status: 'in_progress' }],
    ...Array.from({ length: 40 }, (_, i) => [
      'tool.update',
      {
        id: 'c1',
        status: 'in_progress',
        content: [{ type: 'diff', path: 'a.ts', oldText: null, newText: 'line\n'.repeat(i + 1) }],
      },
    ] as ['tool.update', Record<string, unknown>]),
    ['tool.end', { id: 'c1', status: 'completed' }],
    ['message.delta', { text: 'done' }],
    ['turn.done', { stopReason: 'end_turn' }],
  ]);

  const list = (await (await api(`/api/threads/${thread.id}/events?tail=1`)).json()) as Logged[];
  const tools = list.filter((e) => e.type.startsWith('tool.'));
  assert.deepEqual(tools.map((e) => e.type), ['tool.start', 'tool.end'], 'the burst became one event');
  const merged = tools[1];
  assert.equal(merged.data.status, 'completed');
  // The last update set the diffs (40 lines); the end after it said only a status, so they travel on.
  assert.equal(String((merged.data.content as any[])[0].newText).split('line').length - 1, 40);
  assert.ok(merged.seq > tools[0].seq && merged.seq < list.find((e) => e.type === 'message.delta')!.seq, 'the merged event carries the run\'s last seq');

  // Same request with gzip asked for: the answer arrives compressed.
  const raw = await new Promise<{ status: number; headers: Record<string, string | string[] | undefined>; body: Buffer }>((resolve, reject) => {
    get(new URL(`/api/threads/${thread.id}/events?tail=1`, base), { headers: { ...auth, 'Accept-Encoding': 'gzip' } }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body: Buffer.concat(chunks) }));
    }).on('error', reject);
  });
  assert.equal(raw.status, 200);
  assert.equal(raw.headers['content-encoding'], 'gzip');
  const unzipped = JSON.parse(gunzipSync(raw.body).toString()) as Logged[];
  assert.equal(unzipped.filter((e) => e.type === 'tool.update').length, 0);
});

test('handover: the agent writes the summary; the log stands in when it cannot', async () => {
  const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) })).json()) as { id: string };
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  await untilIdle(thread.id);

  // A successor must exist and cannot be the thread's own agent.
  assert.equal((await api(`/api/threads/${thread.id}/handover`, { method: 'POST', body: JSON.stringify({ target: 'pi' }) })).status, 400);
  assert.equal((await api(`/api/threads/${thread.id}/handover`, { method: 'POST', body: JSON.stringify({ target: 'nobody' }) })).status, 404);

  // The handover turn runs like any other; the fake pi echoes the prompt back.
  const started = (await (await api(`/api/threads/${thread.id}/handover`, { method: 'POST', body: JSON.stringify({ target: 'agy' }) })).json()) as { turnId: string };
  const written = await waitFor(async () => {
    const r = (await (await api(`/api/threads/${thread.id}/handover?turn=${started.turnId}&target=agy`)).json()) as
      | { pending: true }
      | { summary: string; source: string };
    return 'summary' in r ? r : null;
  }, 15_000);
  assert.equal(written.source, 'agent');
  assert.match(written.summary, /hi there/);
  assert.match(written.summary, /Antigravity/, 'the prompt names the successor by its label');

  // Without the turn: the hub synthesizes from the log, the brief carrying the last real ask.
  const synthesized = (await (await api(`/api/threads/${thread.id}/handover?turn=&target=agy`)).json()) as { summary: string; source: string };
  assert.equal(synthesized.source, 'log');
  assert.match(synthesized.summary, /最近的要求：hello/);
  // The hub-injected handover turn is not one of the asks.
  assert.doesNotMatch(synthesized.summary, /请写一份交接摘要/);
});

test('the sidebar preview line: the newest ask, not a hub-injected handover turn', async () => {
  const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) })).json()) as { id: string; preview: string | null };
  assert.equal(thread.preview, null);
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '第一个问题' }) });
  await untilIdle(thread.id);
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '第二个问题' }) });
  await untilIdle(thread.id);
  let listed = (await (await api('/api/threads')).json()) as Array<{ id: string; preview: string | null }>;
  assert.equal(listed.find((t) => t.id === thread.id)?.preview, '第二个问题');

  // The handover prompt is the hub asking, not the user: the preview stays on the last real ask.
  await api(`/api/threads/${thread.id}/handover`, { method: 'POST', body: JSON.stringify({ target: 'agy' }) });
  await untilIdle(thread.id);
  listed = (await (await api('/api/threads')).json()) as Array<{ id: string; preview: string | null }>;
  assert.equal(listed.find((t) => t.id === thread.id)?.preview, '第二个问题');
});

test('title and preview are what was asked, not the notes or quote in front of it', async () => {
  const ask = async (text: string) => {
    const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir }) })).json()) as { id: string };
    await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
    await untilIdle(thread.id);
    const view = (await (await api(`/api/threads/${thread.id}`)).json()) as { title: string; preview: string | null };
    return [view.title, view.preview];
  };
  assert.deepEqual(await ask('[注释 1]\n> 输入 0.042 美元\n评论：核实了吗\n\n[注释 2]\n> 输出免费\n\n这两处再查一下'), ['这两处再查一下', '这两处再查一下']);
  assert.deepEqual(await ask('[注释 1]\n> 输出免费\n评论：官网这么写的吗'), ['官网这么写的吗', '官网这么写的吗']);
  assert.deepEqual(await ask('[注释 1]\n> 输出免费'), ['输出免费', '输出免费']);
  assert.deepEqual(await ask('> scripts/restart.sh\n\n重启一下'), ['重启一下', '重启一下']);
  assert.deepEqual(await ask('评论：不是注释'), ['评论：不是注释', '评论：不是注释']);
  assert.deepEqual(await ask('[注释 1]\n文件：writing/思考是人类的事情.md\n> 我的大脑真的在全心全意地思考\n评论：删'), ['删', '删'], 'a note on a file');
  assert.deepEqual(await ask('文件：不是注释'), ['文件：不是注释', '文件：不是注释']);
});

test('GET /api/search finds what was said in any thread, and needs the token', async () => {
  const thread = await newThread({ agent: 'claude', title: '搜索用的对话' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '请看一下独角鲸迁徙路线' }) });
  await finishedEvents(thread.id);
  const hits = (await (await api(`/api/search?q=${encodeURIComponent('独角鲸迁徙')}`)).json()) as Array<Record<string, unknown>>;
  // The fixture answers with the question in it, so the answer may be found as well.
  const asked = hits.find((h) => h.role === 'user')!;
  assert.deepEqual([asked.threadId, asked.title, asked.archived], [thread.id, '搜索用的对话', false]);
  assert.match(String(asked.snippet), /独角鲸迁徙路线/);
  assert.ok(hits.every((h) => h.threadId === thread.id));
  assert.deepEqual(await (await api('/api/search?q=x')).json(), []);
  assert.deepEqual(await (await api('/api/search')).json(), []);
  assert.equal((await api('/api/search?q=anything', {}, false)).status, 401);
});

test('edit a message: the current history starts there, the old turns are hidden, one thread stays', async () => {
  const messagesFile = join(dataDir, 'pi-edit-messages.json');
  process.env.FAKE_PI_MESSAGES_FILE = messagesFile;
  try {
    const thread = await newThread({ agent: 'pi' });
    const ask = async (text: string) => {
      await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
      await waitFor(async () => {
        const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
        return t.status === 'idle' ? t : null;
      });
    };
    await ask('one');
    await ask('two');
    await ask('three');
    const view = (await (await api(`/api/threads/${thread.id}`)).json()) as Record<string, any>;
    const starts = store.listEventsOfType(thread.id, 'turn.started');
    assert.equal(starts.length, 3);
    // What the agent's own session holds, as it was sent.
    writeFileSync(messagesFile, JSON.stringify({
      [view.agentSessionId]: starts.flatMap((ev) => [
        { role: 'user', content: ev.data.text },
        { role: 'assistant', content: [{ type: 'text', text: `answer ${ev.data.text}` }] },
      ]),
    }));

    const revision = (await (await api(`/api/threads/${thread.id}/history?head=1`)).json()).snapshotSeq as number;
    const res = await api(`/api/threads/${thread.id}/turns/${starts[1].turnId}/edit`, {
      method: 'POST',
      body: JSON.stringify({ text: 'two, clearer', images: [], revision, requestId: 'edit-request-1' }),
    });
    assert.equal(res.status, 202);
    const body = (await res.json()) as { turnId: string };
    await waitFor(async () => {
      const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
      return t.status === 'idle' ? t : null;
    });

    // One thread, same id; the agent continues the copy made just before the edited turn.
    const list = (await (await api('/api/threads')).json()) as Array<Record<string, any>>;
    assert.equal(list.filter((t) => t.id === thread.id).length, 1);
    assert.equal(list.length, (await (await api('/api/threads')).json() as any[]).length);
    const after = (await (await api(`/api/threads/${thread.id}`)).json()) as Record<string, any>;
    assert.equal(after.id, thread.id);
    assert.notEqual(after.agentSessionId, view.agentSessionId);
    assert.match(after.agentSessionId, /-fork-e2$/);

    // The current history is the first turn and the edited one; the third is gone from it.
    const current = store.listEventsOfType(thread.id, 'turn.started');
    assert.deepEqual(current.map((e) => e.data.displayText ?? e.data.text), ['one', 'two, clearer']);
    assert.deepEqual(current.map((e) => e.turnId), [starts[0].turnId, body.turnId]);
    assert.equal(store.listEventsOfType(thread.id, 'turn.started').some((e) => e.turnId === starts[2].turnId), false);
    // Search does not keep the old turns either.
    assert.deepEqual((await (await api('/api/search?q=three')).json() as any[]).filter((h) => h.threadId === thread.id), []);

    // Retrying the same request does not make a second conversation or a second turn.
    const again = await api(`/api/threads/${thread.id}/turns/${starts[1].turnId}/edit`, {
      method: 'POST',
      body: JSON.stringify({ text: 'two, clearer', images: [], revision, requestId: 'edit-request-1' }),
    });
    assert.equal(again.status, 202);
    assert.equal(((await again.json()) as { turnId: string }).turnId, body.turnId);
    assert.equal(store.listEventsOfType(thread.id, 'turn.started').length, 2);
  } finally {
    delete process.env.FAKE_PI_MESSAGES_FILE;
  }
});

test('edit refuses a stale page, a running turn, and an agent with no way to copy a prefix', async () => {
  const thread = await newThread({ agent: 'pi' });
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
    return t.status === 'idle' ? t : null;
  });
  const [first] = store.listEventsOfType(thread.id, 'turn.started');
  const revision = (await (await api(`/api/threads/${thread.id}/history?head=1`)).json()).snapshotSeq as number;
  const edit = (body: Record<string, unknown>) =>
    api(`/api/threads/${thread.id}/turns/${first.turnId}/edit`, { method: 'POST', body: JSON.stringify({ text: 'x', images: [], revision, requestId: 'edit-request-2', ...body }) });

  assert.equal((await edit({ revision: revision + 1 })).status, 409, 'a page that read another history');
  assert.equal((await edit({ requestId: 'short' })).status, 400);
  assert.equal((await api(`/api/threads/${thread.id}/turns/no-such-turn/edit`, { method: 'POST', body: JSON.stringify({ text: 'x', images: [], revision, requestId: 'edit-request-3' }) })).status, 404);

  // An agent that cannot copy a conversation before a turn says so instead of copying everything.
  const agy = await newThread({ agent: 'agy' });
  await api(`/api/threads/${agy.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  const [agyFirst] = store.listEventsOfType(agy.id, 'turn.started');
  const refused = await api(`/api/threads/${agy.id}/turns/${agyFirst.turnId}/edit`, {
    method: 'POST',
    body: JSON.stringify({ text: 'x', images: [], revision: 1, requestId: 'edit-request-4' }),
  });
  assert.equal(refused.status, 400);
  assert.match(((await refused.json()) as { error: string }).error, /cannot edit/);
});

test('outputs gather what a thread changed and the pictures and files its answers name', async () => {
  const created = await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: workdir }) });
  const thread = (await created.json()) as { id: string };
  await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'edit file, then look at ![shot](shot.png) and [report](/srv/out/report.html)' }) });
  await waitFor(async () => {
    const t = (await (await api(`/api/threads/${thread.id}`)).json()) as { status: string };
    return t.status === 'idle' ? t : null;
  });
  type Outputs = { changes: { files: Array<{ path: string; added: number; removed: number }>; total: number }; images: Array<{ path: string }>; files: Array<{ path: string }>; seq: number };
  const res = await api(`/api/threads/${thread.id}/outputs`);
  assert.equal(res.status, 200);
  const outputs = (await res.json()) as Outputs;
  assert.equal(outputs.changes.total, 1);
  assert.match(outputs.changes.files[0].path, /src\/app\.ts$/);
  assert.deepEqual([outputs.changes.files[0].added, outputs.changes.files[0].removed], [2, 1]);
  assert.deepEqual(outputs.images.map(i => i.path), [join(workdir, 'shot.png')]);
  assert.deepEqual(outputs.files.map(f => f.path), ['/srv/out/report.html']);
  assert.equal((await api('/api/threads/does-not-exist/outputs')).status, 404);
});

test('outputs ignore closing tags when the fake agent echoes injected instructions', async () => {
  const thread = await newThread({ agent: 'claude' });
  await post(`/api/threads/${thread.id}/turns`, { text: 'hello\n<loochat-tools>Use loochat tools search.</loochat-tools>' });
  await untilIdle(thread.id);
  assert.ok(store.listEventsOfType(thread.id, 'message.delta').some(ev => String(ev.data.text).includes('</loochat-tools>')));
  const response = await api(`/api/threads/${thread.id}/outputs`);
  assert.equal(response.status, 200);
  const outputs = await response.json() as ThreadOutputs;
  assert.deepEqual(outputs.files, []);
  assert.deepEqual(outputs.images, []);
});

test('outputs fixture creates previewable files, canonicalizes cwd and refreshes the overview cache', async () => {
  const real = join(workdir, 'outputs-real');
  const alias = join(workdir, 'outputs-alias');
  mkdirSync(real);
  symlinkSync(real, alias);
  const thread = await newThread({ agent: 'claude', cwd: alias });
  const path = `/api/threads/${thread.id}/outputs`;
  assert.deepEqual((await (await api(path)).json() as ThreadOutputs).changes.files, []);
  await post(`/api/threads/${thread.id}/turns`, { text: 'make outputs' });
  await untilIdle(thread.id);
  const response = await api(path);
  assert.equal(response.status, 200);
  const outputs = await response.json() as ThreadOutputs;
  assert.equal(outputs.changes.total, 2);
  assert.deepEqual(outputs.changes.files.map(f => [f.path, f.created]), [[join(real, 'out/chart.svg'), true], [join(real, 'out/report.html'), true]]);
  assert.deepEqual(outputs.images.map(f => f.path), [join(real, 'out/chart.svg')]);
  assert.deepEqual(outputs.files.map(f => f.path), [join(real, 'out/report.html')]);
  assert.deepEqual(await (await api(path)).json(), outputs, 'cached reads keep the same metadata');
  assert.equal((await api(path, {}, false)).status, 401);
  assert.ok(!JSON.stringify(outputs).includes('oldText'));
  assert.ok(!JSON.stringify(outputs).includes('<svg'));
  const image = await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(outputs.images[0].path)}`);
  assert.equal(image.status, 200);
  assert.equal(image.headers.get('content-type'), 'image/svg+xml');
  assert.equal(await image.text(), readFileSync(join(real, 'out/chart.svg'), 'utf8'));
  const report = await api(`/api/threads/${thread.id}/text?path=${encodeURIComponent(outputs.files[0].path)}`);
  assert.equal(report.status, 200);
  assert.equal((await report.json()).text, readFileSync(join(real, 'out/report.html'), 'utf8'));
  const fork = await post(`/api/threads/${thread.id}/fork`, {});
  assert.equal(fork.status, 201);
  const forked = await fork.json() as { id: string };
  const copied = await (await api(`/api/threads/${forked.id}/outputs`)).json() as ThreadOutputs;
  assert.deepEqual(copied.changes, outputs.changes);
  assert.deepEqual(copied.images, outputs.images);
  assert.deepEqual(copied.files, outputs.files);
});

test('outputs include imported edits and every turn beyond the history page', async () => {
  const sessions = existsSync(process.env.FAKE_ACP_SESSIONS_FILE!) ? JSON.parse(readFileSync(process.env.FAKE_ACP_SESSIONS_FILE!, 'utf8')) as unknown[] : [];
  writeFileSync(process.env.FAKE_ACP_SESSIONS_FILE!, JSON.stringify([...sessions, { sessionId: 'hist-outputs', cwd: workdir, title: 'outputs' }]));
  const imported = await post('/api/threads/import', { agent: 'claude', sessionId: 'hist-outputs' });
  assert.equal(imported.status, 201);
  const thread = await imported.json() as { id: string };
  const read = async () => await (await api(`/api/threads/${thread.id}/outputs`)).json() as ThreadOutputs;
  assert.deepEqual((await read()).changes.files.map(f => [f.path, f.added, f.removed, f.created]), [[join(workdir, 'notes.md'), 1, 1, false]]);
  for (let i = 0; i < 12; i++) {
    const turn = `output-${i}`;
    store.appendEvent(thread.id, turn, 'turn.started', { text: 'generate' });
    store.appendEvent(thread.id, turn, 'tool.update', { id: 'edit', content: [{ type: 'diff', path: `out/${i}.md`, oldText: null, newText: 'line\n' }] });
    store.appendEvent(thread.id, turn, 'message.delta', { text: `[file](out/${i}.md)` });
    store.appendEvent(thread.id, turn, 'turn.done', {});
  }
  const outputs = await read();
  assert.equal(outputs.changes.total, 13);
  assert.equal(outputs.files.length, 12);
  assert.equal(outputs.files.at(-1)?.path, join(workdir, 'out/0.md'));
});

test('outputs cap lists after aggregation without dropping older generated images', async () => {
  const thread = await newThread({ agent: 'claude' });
  store.appendEvent(thread.id, 't', 'turn.started', { text: 'generate' });
  for (let i = 0; i < 205; i++) {
    store.appendEvent(thread.id, 't', 'tool.update', { id: `e${i}`, content: [{ type: 'diff', path: i === 0 ? 'old.png' : `out/${i}.txt`, oldText: null, newText: 'line\n' }] });
  }
  store.appendEvent(thread.id, 't', 'turn.done', {});
  const outputs = await (await api(`/api/threads/${thread.id}/outputs`)).json() as ThreadOutputs;
  assert.equal(outputs.changes.total, 205);
  assert.equal(outputs.changes.added, 205);
  assert.equal(outputs.changes.files.length, 200);
  assert.equal(outputs.files.length, 200);
  assert.deepEqual(outputs.images.map(f => f.path), [join(workdir, 'old.png')]);
});

test('outputs refresh only disk state after 30 seconds and rebuild when the log changes', async t => {
  const now = Date.now();
  t.mock.timers.enable({ apis: ['Date'], now });
  const thread = await newThread({ agent: 'claude' });
  const present = join(workdir, 'outputs-present.png');
  const absent = join(workdir, 'outputs-restored.html');
  const directory = join(workdir, 'outputs-directory');
  writeFileSync(present, 'image');
  mkdirSync(directory);
  store.appendEvent(thread.id, 't', 'turn.started', { text: 'report' });
  store.appendEvent(thread.id, 't', 'tool.update', { id: 'e', content: [present, absent, directory].map(path => ({ type: 'diff', path, oldText: null, newText: 'line' })) });
  store.appendEvent(thread.id, 't', 'message.delta', { text: [present, absent, directory].map(path => `[file](${path})`).join(' ') });
  store.appendEvent(thread.id, 't', 'turn.done', {});
  const sources = t.mock.method(store, 'outputSources');
  const read = async () => {
    const res = await api(`/api/threads/${thread.id}/outputs`);
    assert.equal(res.status, 200);
    return await res.json() as ThreadOutputs;
  };
  const [first, parallel] = await Promise.all([read(), read()]);
  assert.deepEqual(parallel, first);
  assert.equal(sources.mock.callCount(), 1, 'concurrent readers share one build');
  assert.equal(Object.hasOwn(first.images[0], 'missing'), false);
  assert.deepEqual(first.files.map(f => f.missing), [true, true]);
  assert.equal(first.changes.files.find(f => f.path === absent)?.missing, true);
  assert.equal(first.changes.files.find(f => f.path === directory)?.missing, true);
  writeFileSync(absent, 'restored');
  rmSync(present);
  t.mock.timers.setTime(now + 29_999);
  assert.deepEqual(await read(), first, 'fresh metadata avoids checking the disk again');
  t.mock.timers.setTime(now + 30_001);
  const refreshed = await read();
  assert.equal(refreshed.seq, first.seq);
  assert.equal(refreshed.images[0].missing, true);
  assert.equal(Object.hasOwn(refreshed.files.find(f => f.path === absent)!, 'missing'), false);
  assert.equal(refreshed.changes.files.find(f => f.path === present)?.missing, true);
  assert.equal(Object.hasOwn(refreshed.changes.files.find(f => f.path === absent)!, 'missing'), false);
  assert.equal(sources.mock.callCount(), 1, 'expiry reuses summaries when the log is unchanged');
  store.appendEvent(thread.id, 'next', 'turn.started', { text: 'follow-up' });
  store.appendEvent(thread.id, 'next', 'message.delta', { text: '[new](new.md)' });
  store.appendEvent(thread.id, 'next', 'turn.done', {});
  const latest = await read();
  assert.ok(latest.seq > refreshed.seq);
  assert.equal(latest.files[0].path, join(workdir, 'new.md'));
  assert.equal(latest.files[0].missing, true);
  assert.equal(sources.mock.callCount(), 2);
});

test('outputs expand home paths and their exact referenced images can be previewed', async () => {
  const dir = mkdtempSync(join(homedir(), 'outputs-home-'));
  const path = join(dir, 'chart.png');
  const sibling = join(dir, 'sibling.png');
  writeFileSync(path, Buffer.from(PNG, 'base64'));
  writeFileSync(sibling, Buffer.from(PNG, 'base64'));
  try {
    const thread = await newThread({ agent: 'claude' });
    store.appendEvent(thread.id, 't', 'turn.started', { text: 'generate' });
    store.appendEvent(thread.id, 't', 'message.delta', { text: `![chart](~/${path.slice(homedir().length + 1)})` });
    store.appendEvent(thread.id, 't', 'turn.done', {});
    const outputs = await (await api(`/api/threads/${thread.id}/outputs`)).json() as ThreadOutputs;
    assert.equal(outputs.images.length, 1);
    assert.equal(outputs.images[0].path, path);
    assert.equal(Object.hasOwn(outputs.images[0], 'missing'), false);
    assert.equal((await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(path)}`)).status, 200);
    // The home directory is a browse root, so a neighbour there is readable in its own right.
    assert.equal((await api(`/api/threads/${thread.id}/files?path=${encodeURIComponent(sibling)}`)).status, 200);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('outputs diff references read each call through history detail using the canonical file path', async () => {
  const dir = join(workdir, 'outputs-diff-real');
  const alias = join(workdir, 'outputs-diff-alias');
  mkdirSync(dir);
  symlinkSync(dir, alias);
  const thread = await newThread({ agent: 'claude', cwd: alias });
  const expected = [
    { turn: 'first', id: 'a', path: 'report.html', oldText: null, newText: 'first body' },
    { turn: 'first', id: 'b', path: join(alias, 'report.html'), oldText: 'first body', newText: 'second body' },
    { turn: 'second', id: 'a', path: join(dir, 'report.html'), oldText: 'second body', newText: 'final body' },
  ];
  const refs: ThreadOutputs['changes']['files'][number]['details'] = [];
  let previousTurn = '';
  for (const edit of expected) {
    if (edit.turn !== previousTurn) {
      if (previousTurn) store.appendEvent(thread.id, previousTurn, 'turn.done', {});
      store.appendEvent(thread.id, edit.turn, 'turn.started', { text: 'edit' });
      previousTurn = edit.turn;
    }
    const start = store.appendEvent(thread.id, edit.turn, 'tool.start', { id: edit.id, kind: 'edit' });
    store.appendEvent(thread.id, edit.turn, 'tool.update', { id: edit.id, content: [{ type: 'diff', path: edit.path, oldText: edit.oldText, newText: 'partial' }] });
    const update = store.appendEvent(thread.id, edit.turn, 'tool.update', { id: edit.id, content: [
      { type: 'diff', path: edit.path, oldText: edit.oldText, newText: edit.newText },
      { type: 'diff', path: 'other.txt', oldText: 'unrelated old', newText: 'unrelated new' },
    ], output: 'unrelated tool output' });
    store.appendEvent(thread.id, edit.turn, 'tool.end', { id: edit.id, status: 'completed' });
    refs.push({ turnId: edit.turn, id: start.seq, version: update.seq });
  }
  store.appendEvent(thread.id, previousTurn, 'turn.done', {});
  const res = await api(`/api/threads/${thread.id}/outputs`);
  assert.equal(res.status, 200);
  const outputs = await res.json() as ThreadOutputs;
  const file = outputs.changes.files.find(f => f.path === join(dir, 'report.html'))!;
  assert.equal(file.missing, true, 'recorded diffs survive missing disk files');
  assert.deepEqual(file.details, refs);
  assert.equal(Object.hasOwn(file, 'detailsTotal'), false);
  assert.ok(!JSON.stringify(outputs).includes('oldText'));
  for (const [i, ref] of file.details.entries()) {
    const detail = await api(`/api/threads/${thread.id}/history/detail?turn=${ref.turnId}&id=${ref.id}&path=${encodeURIComponent(file.path)}`);
    assert.equal(detail.status, 200);
    const body = await detail.json() as { content: unknown[]; output?: string };
    const edit = expected[i];
    assert.deepEqual(body.content, [{ type: 'diff', path: edit.path, oldText: edit.oldText, newText: edit.newText }]);
    assert.equal(body.output, undefined);
  }
});
