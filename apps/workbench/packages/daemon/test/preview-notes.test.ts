import { execFile } from 'node:child_process';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { BorrowingService } from '../src/borrowing.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { listenPrivate } from '../src/http.js';
import { sessionEnvironment } from '../src/adapters/types.js';
import { PreviewNotes } from '../src/preview-notes.js';

const input = {
  page: 'design.html', comment: 'Keep one dot',
  target: { selector: '#status', text: 'devbox', html: '<span id="status">devbox</span>', rect: { x: 10, y: 20, width: 30, height: 40 } },
  context: { title: 'Design', url: 'https://preview.example/design.html', theme: 'dark', variant: 'one-slot', revision: 'v2', viewport: { width: 390, height: 844 } },
};

test('preview annotations: authenticated drafts, scoped details, persistent acceptance and retry without duplicate turns', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-notes-'));
  const registry = new AgentRegistry([{ id: 'pi', adapter: 'pi', label: 'pi', command: [process.execPath, join(dirname(fileURLToPath(import.meta.url)), 'fixtures/fake-pi.mjs')] }], null);
  const store = new Store(join(dir, 'data'));
  const manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
  const socket = join(dir, 'tools.sock');
  const tools = new BorrowingService(store, manager, registry.adapters, dir, socket);
  const ipc = borrowingServer(tools);
  await listenPrivate(ipc, socket);
  const server = createHttpServer({ manager, token: 'preview-notes-test-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const thread = await manager.createThread({ agent: 'pi', cwd: dir });
  const other = await manager.createThread({ agent: 'pi', cwd: dir });
  const route = `/api/threads/${thread.id}/preview-notes`;
  const request = (path: string, method = 'GET', body?: unknown, auth = true) => fetch(base + path, { method, headers: { 'Content-Type': 'application/json', ...(auth ? { Authorization: 'Bearer preview-notes-test-token' } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  try {
    assert.equal((await request(route, 'POST', input, false)).status, 401);
    assert.equal((await request(route, 'POST', { ...input, comment: '' })).status, 400);
    const response = await request(route, 'POST', input);
    assert.equal(response.status, 201);
    const note = await response.json();
    assert.equal(note.target.html, undefined);
    const saved = new PreviewNotes(store, manager).list(thread.id, input.page);
    assert.equal(saved[0].id, note.id, 'draft survives recreation');
    assert.ok(!JSON.stringify(saved).includes('<span'), 'list never loads the HTML detail');
    assert.equal((await request(`/api/threads/${other.id}/preview-notes/${note.id}`)).status, 404);
    assert.throws(() => manager.previewNotes.read(thread.id, note.id, true), /not found/, 'agent cannot read unsubmitted draft');
    assert.equal((await (await request(`${route}/${note.id}`)).json()).html, input.target.html);
    assert.equal((await request(`${route}/send`, 'POST', { page: 'other.html', ids: [note.id] })).status, 400);
    assert.equal(manager.previewNotes.read(thread.id, note.id).receipt, null);

    const responses = await Promise.all([request(`${route}/send`, 'POST', { page: input.page, ids: [note.id] }), request(`${route}/send`, 'POST', { page: input.page, ids: [note.id] })]);
    assert.ok(responses.every(r => r.status === 202));
    const [first, again] = await Promise.all(responses.map(r => r.json()));
    assert.deepEqual(first, again);
    assert.equal(store.listEvents(thread.id, 0, -1).filter(e => e.type === 'turn.started').length, 1);
    assert.equal(manager.previewNotes.read(thread.id, note.id, true).html, input.target.html);
    const message = store.listEvents(thread.id, 0, -1).find(e => e.type === 'turn.started')!;
    assert.match(JSON.stringify(message), /Keep one dot/);
    assert.match(JSON.stringify(message), /calli preview-note/);
    assert.ok(!JSON.stringify(message).includes('<span'), 'agent receives summary first');
    const cli = await new Promise<string>((resolve, reject) => execFile(process.execPath, [join(dirname(fileURLToPath(import.meta.url)), '../bin/calli'), 'preview-note', note.id], { env: sessionEnvironment({}, tools.environment(thread.id)!) }, (err, stdout, stderr) => err ? reject(new Error(stderr)) : resolve(stdout)));
    assert.equal(JSON.parse(cli).html, input.target.html);
    assert.equal((await request(`${route}/${note.id}`, 'DELETE')).status, 409);
    assert.deepEqual(new PreviewNotes(store, manager).list(thread.id, input.page)[0].receipt, first);

    const draft = manager.previewNotes.create(other.id, input);
    const original = manager.startTurn;
    manager.startTurn = async () => { throw new Error('test refusal'); };
    await assert.rejects(manager.previewNotes.send(other.id, { page: input.page, ids: [draft.id] }), /test refusal/);
    assert.equal(manager.previewNotes.read(other.id, draft.id).receipt, null, 'failed sends remain drafts');
    manager.startTurn = original;
    manager.previewNotes.remove(other.id, draft.id);
    assert.deepEqual(manager.previewNotes.list(other.id, input.page), []);

    const slow = await manager.createThread({ agent: 'pi', cwd: dir });
    await manager.startTurn(slow.id, { text: 'slow' });
    const queuedNote = manager.previewNotes.create(slow.id, input);
    const queued = await manager.previewNotes.send(slow.id, { page: input.page, ids: [queuedNote.id] });
    assert.equal('queued' in queued, true);
    assert.deepEqual(manager.previewNotes.read(slow.id, queuedNote.id).receipt, queued);
    assert.deepEqual(await manager.previewNotes.send(slow.id, { page: input.page, ids: [queuedNote.id] }), queued);
  } finally {
    await tools.dispose();
    await manager.shutdown();
    await new Promise<void>(resolve => ipc.close(() => resolve()));
    await new Promise<void>(resolve => server.close(() => resolve()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  }
});
