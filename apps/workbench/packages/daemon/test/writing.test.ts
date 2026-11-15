import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, statSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { contentHash, habitsPath, isEditableArticle, newestArticle, WRITING_STAGES, writingInstructions } from '../src/writing.js';

// Writing mode: the stage's way of working rides along with each message, and the person may edit
// the article under writing/ as plain text, over the version they opened only.

const prompts: string[] = [];
/** What the agent does during its next turn, besides answering. */
const work: Array<() => void> = [];
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
        work.shift()?.();
        hooks.emit({ type: 'message.delta', data: { text: 'ok' } });
        return { stopReason: 'end_turn' };
      },
      async cancel() {},
      async close() {},
    };
  },
} as unknown as AgentAdapter;

const TOKEN = 'test-token-0123456789';
let server: Server, base: string, dataDir: string, workdir: string, outside: string, notes: string, manager: ThreadManager, store: Store;
const api = (path: string, init: RequestInit = {}) =>
  fetch(base + path, { ...init, headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json', ...(init.headers ?? {}) } });

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-writing-test-'));
  workdir = mkdtempSync(join(tmpdir(), 'calli-writing-work-'));
  outside = mkdtempSync(join(tmpdir(), 'calli-writing-outside-'));
  notes = mkdtempSync(join(tmpdir(), 'calli-writing-notes-'));
  process.env.CALLI_NOTES_DIR = notes;
  store = new Store(dataDir);
  manager = new ThreadManager(store, new Map([['echo', echoAdapter]]), [workdir], new AttachmentStore(dataDir));
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  store.close();
  for (const dir of [dataDir, workdir, outside, notes]) rmSync(dir, { recursive: true, force: true });
  delete process.env.CALLI_NOTES_DIR;
});

async function turn(threadId: string, text: string): Promise<string> {
  const before = prompts.length;
  const res = await api(`/api/threads/${threadId}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
  assert.equal(res.status, 202, await res.clone().text());
  for (let i = 0; i < 200 && (prompts.length === before || manager.getThread(threadId).status === 'running'); i++) await new Promise((r) => setTimeout(r, 10));
  return prompts[prompts.length - 1];
}

test('each stage has its own way of working, appended once, with an edit mentioned', () => {
  const texts = WRITING_STAGES.map((stage) => writingInstructions('hi', { stage, file: null }));
  assert.equal(new Set(texts).size, WRITING_STAGES.length);
  for (const text of texts) {
    assert.ok(text.startsWith('hi\n\n<calli-writing>') && text.endsWith('</calli-writing>'));
    assert.equal(writingInstructions(text, { stage: 'draft', file: null }), text, 'never appended twice');
  }
  assert.match(texts[0], /只记录、只采访/);
  assert.match(texts[0], /【待补/);
  assert.match(texts[1], /〔补〕/);
  for (const text of texts) {
    assert.ok(text.includes(`先读 ${join(notes, 'writing.md')}`), 'every stage writes by the habits in the person’s notes');
    assert.match(text, /「复盘」「总结一下」[\s\S]*〔新〕[\s\S]*请我确认/, 'a review adds habits marked new, for the person to confirm');
  }
  assert.doesNotMatch(texts[0], /被我直接改过/);
  assert.match(writingInstructions('hi', { stage: 'third', file: 'writing/a.md', edited: true }), /writing\/a\.md在你上一轮之后被我直接改过/);
});

test('only Markdown under writing/ is editable', () => {
  assert.equal(isEditableArticle('/w/writing/a.md', '/w'), true);
  assert.equal(isEditableArticle('/w/writing/a/b.MD', '/w'), true);
  assert.equal(isEditableArticle('/w/writing/a.txt', '/w'), false);
  assert.equal(isEditableArticle('/w/notes/a.md', '/w'), false);
  assert.equal(isEditableArticle('/w/writing-old/a.md', '/w'), false);
  assert.equal(isEditableArticle('/other/writing/a.md', '/w'), false);
  assert.equal(isEditableArticle('/w/writing', '/w'), false);
});

test('writing mode rides along with messages, follows the article and takes the person’s edits', async () => {
  const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'echo', cwd: workdir, title: '写作' }) })).json()) as { id: string };
  const id = thread.id;

  assert.doesNotMatch(await turn(id, '还没开'), /<calli-writing>/);
  assert.equal((await (await api(`/api/threads/${id}/writing`, { method: 'PUT', body: JSON.stringify({ stage: 'nope' }) })).json()).error?.includes('stage'), true);
  let res = await api(`/api/threads/${id}/writing`, { method: 'PUT', body: JSON.stringify({ stage: 'draft' }) });
  assert.equal(res.status, 200);
  assert.deepEqual(((await res.json()) as { writing: unknown }).writing, { stage: 'draft', file: null, habits: join(notes, 'writing.md') });

  // The agent starts the article during the turn; the hub finds it when the turn ends.
  const article = join(workdir, 'writing', '思考是人类的事情.md');
  work.push(() => { mkdirSync(join(workdir, 'writing'), { recursive: true }); writeFileSync(article, '# 思考是人类的事情\n\n初稿\n'); });
  const first = await turn(id, '今天写 PR 的时候');
  assert.match(first, /<calli-writing>[\s\S]*现在是初稿/);
  assert.deepEqual(store.getThread(id)!.writing, { stage: 'draft', file: 'writing/思考是人类的事情.md' });
  assert.match(await turn(id, '/compact'), /^\/compact$/, 'slash commands carry nothing extra');

  res = await api(`/api/threads/${id}/writing`, { method: 'PUT', body: JSON.stringify({ stage: 'second' }) });
  assert.equal(((await res.json()) as { writing: { stage: string } }).writing.stage, 'second');

  // Reading gives the hash an edit starts from.
  const path = encodeURIComponent('writing/思考是人类的事情.md');
  const read = (await (await api(`/api/threads/${id}/text?path=${path}`)).json()) as { hash: string; editable: boolean; text: string };
  assert.equal(read.editable, true);
  assert.equal(read.hash, contentHash(readFileSync(article)));

  // The agent changed it meanwhile: the person's save is refused, with what is there now.
  writeFileSync(article, '# 思考是人类的事情\n\n初稿，agent 改过\n');
  res = await api(`/api/threads/${id}/writing/text?path=${path}`, { method: 'PUT', body: JSON.stringify({ text: '我的版本', baseHash: read.hash }) });
  assert.equal(res.status, 409);
  const conflict = (await res.json()) as { hash: string };
  assert.equal(conflict.hash, contentHash(readFileSync(article)));
  assert.match(readFileSync(article, 'utf8'), /agent 改过/, 'nothing overwritten');

  // Overwriting on purpose: from the hash the conflict reported.
  res = await api(`/api/threads/${id}/writing/text?path=${path}`, { method: 'PUT', body: JSON.stringify({ text: '我的版本\n', baseHash: conflict.hash }) });
  assert.equal(res.status, 200);
  assert.equal(((await res.json()) as { hash: string }).hash, contentHash('我的版本\n'));
  assert.equal(readFileSync(article, 'utf8'), '我的版本\n');
  assert.equal(store.getThread(id)!.writing?.edited, true);

  // The agent hears about the edit once.
  assert.match(await turn(id, '继续'), /直接改过：先重读再动手/);
  assert.equal(store.getThread(id)!.writing?.edited, undefined);
  assert.doesNotMatch(await turn(id, '再继续'), /被我直接改过/);

  res = await api(`/api/threads/${id}/writing`, { method: 'DELETE' });
  assert.equal(((await res.json()) as { writing: unknown }).writing, null);
  assert.doesNotMatch(await turn(id, '结束了'), /<calli-writing>/);
  assert.equal(readFileSync(article, 'utf8'), '我的版本\n', 'the article stays');
});

test('saving refuses anything but an article in the thread, stale hashes and oversized text', async () => {
  const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'echo', cwd: workdir, title: '越权' }) })).json()) as { id: string };
  const save = (path: string, body: Record<string, unknown>) =>
    api(`/api/threads/${thread.id}/writing/text?path=${encodeURIComponent(path)}`, { method: 'PUT', body: JSON.stringify(body) });
  writeFileSync(join(workdir, 'README.md'), 'readme');
  mkdirSync(join(workdir, 'writing'), { recursive: true });
  writeFileSync(join(workdir, 'writing', 'notes.txt'), 'notes');
  writeFileSync(join(outside, 'secret.md'), 'secret');
  symlinkSync(join(outside, 'secret.md'), join(workdir, 'writing', 'escape.md'));
  symlinkSync(join(workdir, 'README.md'), join(workdir, 'writing', 'readme-link.md'));
  const hash = (text: string) => contentHash(text);

  assert.equal((await save('README.md', { text: 'x', baseHash: hash('readme') })).status, 403);
  assert.equal((await save('writing/notes.txt', { text: 'x', baseHash: hash('notes') })).status, 403);
  assert.equal((await save('writing/escape.md', { text: 'x', baseHash: hash('secret') })).status, 404, 'a link out of the thread is not there');
  assert.equal((await save('writing/readme-link.md', { text: 'x', baseHash: hash('readme') })).status, 403, 'a link to a file outside writing/');
  assert.equal((await save(join(outside, 'secret.md'), { text: 'x', baseHash: hash('secret') })).status, 404);
  assert.equal((await save('writing/missing.md', { text: 'x', baseHash: hash('') })).status, 404);
  assert.equal(readFileSync(join(outside, 'secret.md'), 'utf8'), 'secret');
  assert.equal(readFileSync(join(workdir, 'README.md'), 'utf8'), 'readme');

  writeFileSync(join(workdir, 'writing', 'a.md'), 'a');
  assert.equal((await save('writing/a.md', { text: 'x' })).status, 400, 'no baseHash');
  assert.equal((await save('writing/a.md', { text: 'x', baseHash: 'stale' })).status, 409);
  assert.equal((await save('writing/a.md', { text: '长'.repeat(400 * 1024), baseHash: hash('a') })).status, 413);
  assert.equal(readFileSync(join(workdir, 'writing', 'a.md'), 'utf8'), 'a');
  // The file panel sends the absolute path the text endpoint returned; that works the same.
  const absolute = (await (await api(`/api/threads/${thread.id}/text?path=writing/a.md`)).json()) as { path: string; hash: string; editable: boolean };
  assert.equal(absolute.editable, true);
  assert.equal((await save(absolute.path, { text: 'b', baseHash: absolute.hash })).status, 200);
  assert.equal(readFileSync(join(workdir, 'writing', 'a.md'), 'utf8'), 'b');

  const readme = (await (await api(`/api/threads/${thread.id}/text?path=README.md`)).json()) as { editable: boolean; hash: string };
  assert.equal(readme.editable, false);
  assert.equal(readme.hash, hash('readme'), 'every whole text file has a hash');
});

test('the person’s notes open and save from any thread; nothing else outside it does', async () => {
  const thread = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'echo', cwd: workdir, title: '习惯' }) })).json()) as { id: string };
  const habits = habitsPath();
  writeFileSync(habits, '# 写作习惯\n\n- 〔新〕不用「你」说教\n');
  writeFileSync(join(notes, 'list.txt'), 'not markdown');
  symlinkSync(join(outside, 'secret.md'), join(notes, 'escape.md'));
  const read = (path: string) => api(`/api/threads/${thread.id}/text?path=${encodeURIComponent(path)}`);
  const save = (path: string, body: Record<string, unknown>) =>
    api(`/api/threads/${thread.id}/writing/text?path=${encodeURIComponent(path)}`, { method: 'PUT', body: JSON.stringify(body) });

  const opened = (await (await read(habits)).json()) as { editable: boolean; hash: string; text: string };
  assert.equal(opened.editable, true);
  assert.equal(manager.getThreadDownload(thread.id, habits).path, habits, 'readable notes can also be downloaded');
  assert.throws(() => manager.getThreadDownload(thread.id, join(notes, 'escape.md')), /no answer|no such file/);
  assert.match(opened.text, /〔新〕/);
  assert.equal((await save(habits, { text: '# 写作习惯\n\n- 不用「你」说教\n', baseHash: opened.hash })).status, 200);
  assert.equal(readFileSync(habits, 'utf8'), '# 写作习惯\n\n- 不用「你」说教\n');
  assert.equal((await save(habits, { text: 'x', baseHash: opened.hash })).status, 409, 'an edit over a stale version is refused');

  assert.equal((await read(join(notes, 'list.txt'))).status, 403, 'only Markdown among the notes');
  assert.equal((await save(join(notes, 'escape.md'), { text: 'x', baseHash: contentHash('secret') })).status, 404, 'a link out of the notes is not one');
  assert.equal(readFileSync(join(outside, 'secret.md'), 'utf8'), 'secret');
  assert.equal(store.getThread(thread.id)!.writing, null, 'saving a note is not writing an article');
});


test('article discovery ignores symlinks outside writing, including a linked directory', () => {
  const cwd = join(workdir, 'discovery');
  mkdirSync(join(cwd, 'writing'), { recursive: true });
  writeFileSync(join(cwd, 'elsewhere.md'), 'outside the writing directory');
  symlinkSync(join(cwd, 'elsewhere.md'), join(cwd, 'writing', 'escape.md'));
  assert.equal(newestArticle(cwd), null);
  const linked = join(workdir, 'linked-writing');
  mkdirSync(linked);
  symlinkSync(outside, join(linked, 'writing'));
  assert.equal(newestArticle(linked), null);
});

test('competing saves cannot share a base hash and saving preserves file permissions', async () => {
  const thread = await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'echo', cwd: workdir, title: 'Concurrent saves' }) })).json() as { id: string };
  const path = join(workdir, 'writing', 'race.md');
  writeFileSync(path, 'original');
  chmodSync(path, 0o600);
  const save = (text: string) => api(`/api/threads/${thread.id}/writing/text?path=${encodeURIComponent(path)}`, {
    method: 'PUT', body: JSON.stringify({ text, baseHash: contentHash('original') }),
  });
  const results = await Promise.all([save('first'), save('second')]);
  assert.deepEqual(results.map(r => r.status).sort(), [200, 409]);
  assert.equal(statSync(path).mode & 0o777, 0o600);
  writeFileSync(path, Buffer.from([0, 1, 2]));
  assert.equal(manager.readThreadText(thread.id, path).editable, false);
  assert.throws(() => manager.saveWritingText(thread.id, path, 'text', contentHash(Buffer.from([0, 1, 2]))), /only text/);
});
