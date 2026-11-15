import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

// Plan-page CRUD over HTTP. Threads exist only as rows: linking never starts an agent, so a
// stub adapter is enough.

const stubAdapter: AgentAdapter = {
  label: 'Stub',
  available: async () => true,
  capabilities: {
    resume: false,
    approvals: false,
    permissionModes: [],
    effortLevels: [],
    images: false,
    deleteSession: false,
    history: false,
    fork: false,
  },
  async commands() {
    return [];
  },
  async start() {
    throw new Error('not used here');
  },
} as unknown as AgentAdapter;

const TOKEN = 'test-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

let server: Server;
let base: string;
let dataDir: string;
let workroot: string;
let manager: ThreadManager;
let store: Store;

const api = async (path: string, init: RequestInit = {}): Promise<Response> =>
  fetch(base + path, { ...init, headers: { ...auth, ...(init.headers ?? {}) } });

interface TodoJson {
  id: string;
  text: string;
  done: boolean;
  parentId: string | null;
  position: number;
  threads: Array<{ id: string; title: string }>;
}

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-todo-test-'));
  workroot = mkdtempSync(join(tmpdir(), 'calli-todo-work-'));
  store = new Store(dataDir);
  manager = new ThreadManager(store, new Map([['pi', stubAdapter]]), [workroot], new AttachmentStore(dataDir));
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
  rmSync(workroot, { recursive: true, force: true });
});

test('todos: create, list, edit, strike through, delete', async () => {
  const created = await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '  重构支付  ' }) });
  assert.equal(created.status, 201);
  const todo = (await created.json()) as TodoJson;
  assert.equal(todo.text, '重构支付');
  assert.equal(todo.done, false);

  // Empty and over-long texts are refused, so the list never holds blank rows.
  assert.equal((await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '   ' }) })).status, 400);
  assert.equal((await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: 'x'.repeat(301) }) })).status, 400);

  const patched = await api(`/api/todos/${todo.id}`, { method: 'PATCH', body: JSON.stringify({ done: true }) });
  const after = (await patched.json()) as TodoJson;
  assert.equal(after.done, true);
  assert.ok(after.doneAt);

  const untoggled = await api(`/api/todos/${todo.id}`, { method: 'PATCH', body: JSON.stringify({ done: false }) });
  assert.equal(((await untoggled.json()) as TodoJson).done, false);

  const listed = await api('/api/todos');
  const page = (await listed.json()) as Array<TodoJson & { stage: unknown }>;
  assert.deepEqual(page.map((t) => t.id), [todo.id]);
  assert.equal(page[0].stage, null, 'a matter with no work thread has no stage');

  assert.equal((await api(`/api/todos/${todo.id}`, { method: 'DELETE' })).status, 204);
  assert.equal((await api(`/api/todos/${todo.id}`, { method: 'PATCH', body: JSON.stringify({ done: true }) })).status, 404);
});

test('todos: sub-items nest one level under a real plan, never deeper', async () => {
  const parent = (await (await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '计划页' }) })).json()) as TodoJson;
  const child = (await (
    await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '折叠已完成', parentId: parent.id }) })
  ).json()) as TodoJson;
  assert.equal(child.parentId, parent.id);

  // A child is not a valid parent.
  const deep = await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '太深', parentId: child.id }) });
  assert.equal(deep.status, 400);
  const ghost = await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '悬空', parentId: 'nope' }) });
  assert.equal(ghost.status, 404);

  // Deleting the plan takes its sub-item with it.
  await api(`/api/todos/${parent.id}`, { method: 'DELETE' });
  const listed = (await (await api('/api/todos')).json()) as TodoJson[];
  assert.equal(listed.find((t) => t.id === child.id), undefined);
});

test('todos: move reorders a level, indents under a plan and back out, one level only', async () => {
  const make = async (text: string, parentId?: string) =>
    (await (await api('/api/todos', { method: 'POST', body: JSON.stringify({ text, parentId }) })).json()) as TodoJson;
  const move = (id: string, body: Record<string, unknown>) =>
    api(`/api/todos/${id}/move`, { method: 'POST', body: JSON.stringify(body) });
  const order = async (parentId: string | null, ids: string[]) =>
    ((await (await api('/api/todos')).json()) as TodoJson[])
      .filter((t) => t.parentId === parentId && ids.includes(t.id))
      .sort((x, y) => x.position - y.position)
      .map((t) => t.text);

  const a = await make('甲');
  const b = await make('乙');
  const c = await make('丙');
  const ids = [a.id, b.id, c.id];

  // Reorder: 丙 before 甲, then 甲 to the end.
  assert.equal((await move(c.id, { parentId: null, before: a.id })).status, 204);
  assert.deepEqual(await order(null, ids), ['丙', '甲', '乙']);
  assert.equal((await move(a.id, { parentId: null, before: null })).status, 204);
  assert.deepEqual(await order(null, ids), ['丙', '乙', '甲']);

  // Tab: 乙 goes under 丙, last among its sub-items; Shift+Tab: back out, right after 丙.
  assert.equal((await move(b.id, { parentId: c.id })).status, 204);
  assert.deepEqual(await order(c.id, ids), ['乙']);
  assert.deepEqual(await order(null, ids), ['丙', '甲']);
  assert.equal((await move(b.id, { parentId: null, before: a.id })).status, 204);
  assert.deepEqual(await order(null, ids), ['丙', '乙', '甲']);

  // One level: a plan with sub-items stays a plan, a sub-item is no parent, nothing goes under itself.
  const kid = await make('丁', c.id);
  assert.equal((await move(c.id, { parentId: a.id })).status, 400);
  assert.equal((await move(a.id, { parentId: kid.id })).status, 400);
  assert.equal((await move(a.id, { parentId: a.id })).status, 400);
  // `before` has to be in the list the item lands in.
  assert.equal((await move(a.id, { parentId: null, before: kid.id })).status, 400);
  assert.equal((await move('nope', { parentId: null })).status, 404);
});

test('todos: threads link and unlink, and vanish with the thread', async () => {
  const workdir = join(workroot, 'proj');
  mkdirSync(workdir, { recursive: true });
  const thread = (await (
    await fetch(base + '/api/threads', {
      method: 'POST',
      headers: auth,
      body: JSON.stringify({ agent: 'pi', cwd: workdir, title: '支付重构' }),
    })
  ).json()) as { id: string; title: string };
  assert.ok(thread.id, 'thread was created');
  const todo = (await (await api('/api/todos', { method: 'POST', body: JSON.stringify({ text: '支付重构收尾' }) })).json()) as TodoJson;

  assert.equal((await api(`/api/todos/${todo.id}/threads`, { method: 'POST', body: JSON.stringify({ threadId: thread.id }) })).status, 204);
  const linked = (await (await api('/api/todos')).json()) as TodoJson[];
  assert.deepEqual(linked.find((t) => t.id === todo.id)?.threads.map((t) => t.id), [thread.id]);

  // Unknown todos and threads are refused.
  assert.equal((await api('/api/todos/nope/threads', { method: 'POST', body: JSON.stringify({ threadId: thread.id }) })).status, 404);
  assert.equal((await api(`/api/todos/${todo.id}/threads`, { method: 'POST', body: JSON.stringify({ threadId: 'nope' }) })).status, 404);

  // Deleting the thread removes the link, not the plan.
  await fetch(`${base}/api/threads/${thread.id}`, { method: 'DELETE', headers: auth });
  const after = (await (await api('/api/todos')).json()) as TodoJson[];
  assert.deepEqual(after.find((t) => t.id === todo.id)?.threads, []);

  await api(`/api/todos/${todo.id}`, { method: 'DELETE' });
  rmSync(workdir, { recursive: true, force: true });
});
