import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { parseProgress, progressInstructions } from '../src/progress-report.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

// The board's progress line: asked for only with consent, read back at the end of a turn.

const prompts: string[] = [];
const replies: string[] = [];
const echoAdapter = {
  label: 'Echo',
  available: async () => true,
  capabilities: { resume: false, approvals: false, permissionModes: [], effortLevels: [], images: false, deleteSession: false, history: false, fork: false },
  resumeCommand: (id: string) => `echo resume ${id}`,
  async commands() { return []; },
  async start() {
    return {
      agentSessionId: 'echo-session',
      async prompt(input: { text: string }, hooks: { emit(event: { type: string; data: Record<string, unknown> }): void }) {
        prompts.push(input.text);
        hooks.emit({ type: 'message.delta', data: { text: replies.shift() ?? 'ok' } });
        return { stopReason: 'end_turn' };
      },
      async cancel() {},
      async close() {},
    };
  },
} as unknown as AgentAdapter;

const TOKEN = 'test-token-0123456789';
let server: Server, base: string, dataDir: string, workdir: string, manager: ThreadManager, store: Store;
let wanted = false;
const api = (path: string, init: RequestInit = {}) =>
  fetch(base + path, { ...init, headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json', ...(init.headers ?? {}) } });

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-progress-test-'));
  workdir = mkdtempSync(join(tmpdir(), 'calli-progress-work-'));
  store = new Store(dataDir);
  manager = new ThreadManager(store, new Map([['echo', echoAdapter]]), [workdir], new AttachmentStore(dataDir));
  manager.progressWanted = () => wanted;
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  store.close();
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
});

test('progress lines are read leniently and only the last one counts', () => {
  assert.equal(parseProgress('done the work'), null);
  assert.deepEqual(parseProgress('改好了。\n\n[进度] 待验收：等 CI'), { stage: 'review', note: '等 CI' });
  assert.equal(parseProgress('[进度] 以后再做'), null, 'later is the person\'s word, never the agent\'s');
  assert.deepEqual(parseProgress('**[进度] 需要你 - 缺少密钥**'), { stage: 'blocked', note: '缺少密钥' });
  assert.deepEqual(parseProgress('[进度] 需要你\n...\n[进度] 进行中: 还差测试'), { stage: 'doing', note: '还差测试' });
  assert.equal(parseProgress('[进度] 已完成：全部做完'), null, 'agents cannot declare the work done');
  assert.equal(parseProgress('引用 [进度] 待验收 的写法'), null, 'only a line of its own counts');
  assert.equal(parseProgress(`[进度] 待验收：${'长'.repeat(300)}`)?.note.length, 200);
  const once = progressInstructions('hi');
  assert.ok(once.startsWith('hi\n\n<calli-progress>'));
  assert.equal(progressInstructions(once), once, 'never appended twice');
});

test('with consent agents are asked, their answers move the thread, and the person marks it done', async () => {
  const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'echo', cwd: workdir, title: '进度' }) })).json()) as { id: string };
  const read = () => store.getThread(thread.id)!;
  const turn = async (text: string, reply: string) => {
    replies.push(reply);
    const before = prompts.length;
    assert.equal((await api(`/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) })).status, 202);
    for (let i = 0; i < 200 && (prompts.length === before || read().status === 'running'); i++) await new Promise((r) => setTimeout(r, 10));
    return prompts.at(-1)!;
  };

  // Without consent the agent is told nothing, and its words change nothing.
  assert.equal(await turn('你好', '[进度] 待验收'), '你好');
  assert.equal(read().report, null);

  wanted = true;
  const asked = await turn('开始', '做完了\n[进度] 待验收：请看 diff');
  assert.ok(asked.startsWith('开始\n\n<calli-progress>'));
  assert.equal(store.listEventsOfType(thread.id, 'turn.started').at(-1)!.data.displayText, '开始', 'the person sees what they wrote');
  assert.deepEqual([read().report?.stage, read().report?.note], ['review', '请看 diff']);
  const view = (await (await api(`/api/threads/${thread.id}`)).json()) as { report: unknown; doneAt: unknown };
  assert.deepEqual([(view.report as { stage: string }).stage, view.doneAt], ['review', null]);

  // Slash commands are left alone and do not withdraw; an asked, silent turn does.
  assert.equal(await turn('/compact', 'compacted'), '/compact');
  assert.equal(read().report?.stage, 'review');
  await turn('再改一下', '改好了');
  assert.equal(read().report, null);

  await turn('继续', '[进度] 需要你：缺密钥');
  assert.equal(read().report?.stage, 'blocked');
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ done: 'yes' }) })).status, 400);
  assert.equal((await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ done: true }) })).status, 200);
  assert.ok(read().doneAt, 'marked done');
  assert.equal(read().report, null, 'the person\'s word replaces the agent\'s');
  const updatedAt = read().updatedAt;
  await api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ done: true }) });
  assert.equal(read().updatedAt, updatedAt, 'marking does not reorder recent threads');

  // The person can also place it in another column; that reopens it and says who placed it.
  const place = (body: unknown) => api(`/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify(body) });
  assert.equal((await place({ stage: 'done' })).status, 400);
  assert.equal((await place({ stage: 'review', done: false })).status, 400);
  assert.equal((await place({ stage: 'blocked' })).status, 200);
  assert.deepEqual([read().doneAt, read().report?.stage, read().report?.by, read().report?.note], [null, 'blocked', 'user', '']);
  assert.equal(read().updatedAt, updatedAt, 'placing does not reorder recent threads');
  assert.equal((await place({ stage: 'backlog' })).status, 200);
  assert.deepEqual([read().doneAt, read().report?.stage, read().report?.by], [null, 'backlog', 'user']);
  await place({ done: true });

  // New work reopens it.
  await turn('还有一处', '[进度] 进行中：等 CI');
  assert.deepEqual([read().doneAt, read().report?.stage], [null, 'doing']);
  wanted = false;
});
