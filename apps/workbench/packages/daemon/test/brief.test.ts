import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { approvalFacts, failureSignature, namedPaths } from '../src/adapters/brief.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { firstSentence, type ThreadBrief } from '../src/brief.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-brief';

let server: Server;
let base: string;
let dataDir: string;
let workdir: string;
let manager: ThreadManager;
let store: Store;

const api = (path: string, init: RequestInit = {}) =>
  fetch(base + path, { ...init, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}`, ...(init.headers ?? {}) } });

async function until<T>(check: () => T | null | undefined | false, timeoutMs = 10_000): Promise<T> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const value = check();
    if (value) return value;
    if (Date.now() > end) throw new Error('timed out waiting');
    await new Promise(r => setTimeout(r, 20));
  }
}

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-brief-data-'));
  workdir = realpathSync(mkdtempSync(join(tmpdir(), 'calli-brief-work-')));
  const adapters = new Map<string, AgentAdapter>([['claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')])]]);
  store = new Store(dataDir);
  manager = new ThreadManager(store, adapters, new Workdirs([workdir], join(dataDir, 'workdirs.json'), join(dataDir, 'scratch')), new AttachmentStore(dataDir));
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  store.close();
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
});

const newThread = async () => (await manager.createThread({ agent: 'claude', cwd: workdir })).id;
const settled = (id: string) => until(() => manager.threadView(id).status !== 'running' && !manager.threadView(id).waiting);
const brief = async (id: string): Promise<ThreadBrief> => {
  const res = await api(`/api/threads/${id}/brief`);
  assert.equal(res.status, 200);
  return await res.json() as ThreadBrief;
};

test('the brief needs a token and a thread that exists', async () => {
  assert.equal((await fetch(`${base}/api/threads/nope/brief`)).status, 401);
  assert.equal((await api('/api/threads/nope/brief')).status, 404);
});

test('a thread waiting on an approval says what the step would run', async () => {
  const id = await newThread();
  await manager.startTurn(id, { text: 'approve install' });
  await until(() => manager.threadView(id).waitingReason === 'approval');
  const waiting = await brief(id);
  assert.equal(waiting.kind, 'approval');
  assert.equal(waiting.approval?.action, 'run');
  assert.equal(waiting.approval?.command, 'npm install lodash', 'only the first line of the command');
  assert.equal(waiting.approval?.pending, 1);
  assert.equal(waiting.updatedAt, manager.getThread(id).updatedAt);

  // Once it is decided there is nothing to approve: the brief reads the finished turn.
  const approvalId = String(store.latestApprovalRequest(id, 0)!.data.approvalId);
  assert.equal((await api(`/api/approvals/${approvalId}`, { method: 'POST', body: JSON.stringify({ optionId: 'allow' }) })).status, 204);
  await settled(id);
  assert.equal((await brief(id)).kind, 'result');
});

test('an approval to edit names the file, relative to the thread', async () => {
  const id = await newThread();
  await manager.startTurn(id, { text: 'approve lockfile' });
  await until(() => manager.threadView(id).waitingReason === 'approval');
  const waiting = await brief(id);
  assert.equal(waiting.approval?.action, 'edit');
  assert.deepEqual(waiting.approval?.paths, ['package-lock.json']);
  assert.equal(waiting.approval?.moreFiles, 0);
});

test('a thread that stopped says whether the failed turn changed files and how often it failed the same way', async () => {
  const id = await newThread();
  await manager.startTurn(id, { text: 'edit file flaky' });
  await settled(id);
  const first = await brief(id);
  assert.equal(first.kind, 'failure');
  assert.deepEqual({ edits: first.failure?.edits, files: first.failure?.files, repeats: first.failure?.repeats, resume: first.failure?.resume }, { edits: 1, files: 1, repeats: 1, resume: false });

  // Sending the ask again fails again; the request numbers differ, the error is the same.
  for (const repeats of [2, 3]) {
    assert.equal((await api(`/api/threads/${id}/retry`, { method: 'POST' })).status, 200);
    await settled(id);
    const again = await brief(id);
    assert.equal(again.failure?.repeats, repeats);
    assert.equal(again.failure?.edits, 1);
  }
});

test('a stop that changed nothing says so, and a turn that went well in between starts the count over', async () => {
  const id = await newThread();
  await manager.startTurn(id, { text: 'flaky' });
  await settled(id);
  assert.equal((await api(`/api/threads/${id}/retry`, { method: 'POST' })).status, 200);
  await settled(id);
  assert.equal((await brief(id)).failure?.repeats, 2);

  await manager.startTurn(id, { text: 'all fine' });
  await settled(id);
  assert.equal((await brief(id)).kind, 'result');
  await manager.startTurn(id, { text: 'flaky' });
  await settled(id);
  const failed = await brief(id);
  assert.equal(failed.kind, 'failure');
  assert.equal(failed.failure?.repeats, 1);
  assert.equal(failed.failure?.edits, 0);
  assert.equal(failed.failure?.files, 0);
});

test('a finished thread gives the first sentence of its last reply and what the turn changed', async () => {
  const id = await newThread();
  await manager.startTurn(id, { text: 'Notes are tidy. Then more words. edit file' });
  await settled(id);
  const done = await brief(id);
  assert.equal(done.kind, 'result');
  assert.equal(done.result?.first, 'echo:Notes are tidy.');
  assert.equal(done.result?.edits, 1);
  assert.equal(done.result?.files, 1);

  const plain = await newThread();
  await manager.startTurn(plain, { text: 'just words' });
  await settled(plain);
  const unchanged = await brief(plain);
  assert.deepEqual({ edits: unchanged.result?.edits, files: unchanged.result?.files }, { edits: 0, files: 0 });
});

test('after many turns the brief still reads the last one, not an earlier one', async () => {
  const id = await newThread();
  await manager.startTurn(id, { text: 'One. edit file' });
  await settled(id);
  await manager.startTurn(id, { text: 'Two.' });
  await settled(id);
  const second = await brief(id);
  assert.equal(second.result?.first, 'echo:Two.');
  assert.deepEqual([second.result?.edits, second.result?.files], [0, 0], 'the edit was in an earlier turn');

  await manager.startTurn(id, { text: 'Three. edit file' });
  await settled(id);
  const third = await brief(id);
  assert.equal(third.result?.first, 'echo:Three.');
  assert.deepEqual([third.result?.edits, third.result?.files], [1, 1]);

  // A pending approval is found past all of them, and the failure after them counts from its own start.
  await manager.startTurn(id, { text: 'approve install' });
  await until(() => manager.threadView(id).waitingReason === 'approval');
  assert.equal((await brief(id)).approval?.command, 'npm install lodash');
  const approvalId = String(store.latestApprovalRequest(id, 0)!.data.approvalId);
  await api(`/api/approvals/${approvalId}`, { method: 'POST', body: JSON.stringify({ optionId: 'allow' }) });
  await settled(id);
  await manager.startTurn(id, { text: 'flaky' });
  await settled(id);
  const failed = await brief(id);
  assert.deepEqual([failed.kind, failed.failure?.edits, failed.failure?.repeats], ['failure', 0, 1]);
});

test('a thread with nothing to say has no section', async () => {
  const id = await newThread();
  const fresh = await brief(id);
  assert.equal(fresh.kind, null);
  assert.equal(fresh.approval, undefined);
  assert.equal(fresh.failure, undefined);
  assert.equal(fresh.result, undefined);
});

test('the first sentence skips markup and code, and is cut short', () => {
  assert.equal(firstSentence('## Done\nignored'), 'Done');
  assert.equal(firstSentence('```\ncode. here\n```\n\n- **Fixed** the login bug. Next.'), 'Fixed the login bug.');
  assert.equal(firstSentence('改好了。还有一处没动。'), '改好了。');
  assert.equal(firstSentence('v1.2 shipped to prod and went well'), 'v1.2 shipped to prod and went well');
  assert.equal(firstSentence('x'.repeat(200))?.length, 80);
  assert.equal(firstSentence('```\nonly code\n```'), null);
  assert.equal(firstSentence(''), null);
});

test('agents word approvals differently; the facts come out the same', () => {
  // ACP: kind and rawInput. Codex: its own kinds, command and changes at the top.
  assert.deepEqual(approvalFacts('Run', { kind: 'execute', rawInput: { command: 'ls -la' } }), { action: 'run', command: 'ls -la', paths: [], title: 'Run' });
  assert.deepEqual(approvalFacts('运行命令', { kind: 'command', command: 'npm test', cwd: '/x', rawInput: { command: 'npm test' } }), { action: 'run', command: 'npm test', paths: [], title: '运行命令' });
  assert.deepEqual(approvalFacts('修改文件', { kind: 'fileChange', changes: [{ path: '/a/b.ts' }, { path: '/a/c.ts' }] }).paths, ['/a/b.ts', '/a/c.ts']);
  assert.equal(approvalFacts('修改文件', { kind: 'fileChange', changes: [] }).action, 'edit');
  assert.equal(approvalFacts('x', { kind: 'delete', locations: [{ path: '/a/old.ts' }] }).action, 'delete');
  assert.equal(approvalFacts('zcode asks', { title: 'Bash', rawInput: { command: ['git', 'push'] } }).command, 'git push');
  assert.deepEqual(approvalFacts('Permission requested', { toolCallId: 't1' }), { action: 'other', command: null, paths: [], title: 'Permission requested' });
});

test('paths come from wherever the agent put them', () => {
  assert.deepEqual(namedPaths({ input: { file_path: '/a.ts' } }), ['/a.ts']);
  assert.deepEqual(namedPaths({ input: { changes: [{ path: '/b.ts', kind: 'update' }] } }), ['/b.ts']);
  assert.deepEqual(namedPaths({ content: [{ type: 'diff', path: '/c.ts' }, { type: 'content' }] }), ['/c.ts']);
  assert.deepEqual(namedPaths({ input: {} }), []);
});

test('the same error compares equal across attempts, a different one does not', () => {
  const a = failureSignature('Internal error: upstream connection reset (request 12345)');
  assert.equal(a, failureSignature('Internal error: upstream connection reset (request 98765)'));
  assert.equal(a, failureSignature('Error: Upstream  connection reset (request 7)\nstack…'));
  assert.notEqual(a, failureSignature('Internal error: invalid api key'));
  assert.equal(failureSignature('id 3f2504e0-4f89-11d3-9a0c-0305e82c3301 gone'), failureSignature('id 11111111-2222-3333-4444-555555555555 gone'));
});
