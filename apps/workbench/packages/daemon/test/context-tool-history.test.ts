import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { BorrowingService } from '../src/borrowing.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

const here = dirname(fileURLToPath(import.meta.url));
async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-context-tools-'));
  const registry = new AgentRegistry([{ id: 'pi', adapter: 'pi', command: [process.execPath, join(here, 'fixtures/fake-pi.mjs')] }], null);
  const store = new Store(join(dir, 'data'));
  const manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
  const socket = join(dir, 'tools.sock');
  const tools = new BorrowingService(store, manager, registry.adapters, dir, socket);
  const ipc = borrowingServer(tools);
  const server = createHttpServer({ manager, token: 'context-tools-fixture', webDir: null, version: 'test' });
  await listenPrivate(ipc, socket);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const post = async (path: string, body: unknown) => {
    const response = await fetch(base + path, { method: 'POST', headers: { Authorization: 'Bearer context-tools-fixture', 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    assert.ok(response.ok, await response.clone().text());
    return response.json();
  };
  const mk = (title: string) => post('/api/threads', { title, agent: 'pi', cwd: dir });
  const send = async (id: string, text: string, references: string[] = []) => {
    const result = await post(`/api/threads/${id}/turns`, { text, references });
    for (let i = 0; i < 300 && manager.getThread(id).status === 'running'; i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.notEqual(manager.getThread(id).status, 'running');
    return store.listTurnEvents(id, result.turnId).filter(event => event.type === 'message.delta').map(event => event.data.text).join('');
  };
  const execute = async (id: string, command: string) => {
    const result = JSON.parse(await send(id, `CONTEXT_READ \`${command}\``));
    const read = result.reads.find((value: { command: string }) => value.command === command);
    assert.ok(read, 'run the returned command in the existing recipient process');
    assert.equal(read.code, 0, JSON.stringify(read));
    return read.value;
  };
  const whole = async (id: string, command: string) => {
    let text = '';
    let page;
    do {
      page = await execute(id, command);
      text += page.text;
      if (page.command) command = page.command;
    } while (page.command);
    return JSON.parse(text) as Array<{ type: string; data: Record<string, unknown> }>;
  };
  return { dir, store, manager, post, mk, send, execute, whole, async close() {
    await tools.dispose(); await manager.shutdown();
    await Promise.all([server, ipc].map(value => new Promise<void>(resolve => value.close(() => resolve()))));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}

const dataOf = (events: Array<{ data: Record<string, unknown> }>) => Object.assign({}, ...events.map(event => event.data));

test('context tool commands read full input and final output from the right turn, without starting the source', async () => {
  const h = await setup();
  try {
    const source = await h.mk('Full tools source');
    const recipient = await h.mk('Reader');
    const input = { command: 'printf ' + '字😀'.repeat(11_000) };
    const output = 'FULL_BEGIN\n' + '工具结果😀\n'.repeat(12_000) + '\nFULL_END';
    for (const turn of ['old-turn', 'wanted-turn']) {
      h.store.appendEvent(source.id, turn, 'turn.started', { text: turn, userOrigin: 'person' });
      h.store.appendEvent(source.id, turn, 'tool.start', { id: 'reused-id', kind: 'execute', input: turn === 'wanted-turn' ? input : { command: 'old' } });
      h.store.appendEvent(source.id, turn, 'tool.update', { id: 'reused-id', output: 'partial' });
      h.store.appendEvent(source.id, turn, 'tool.end', { id: 'reused-id', status: 'completed', output: turn === 'wanted-turn' ? output : 'OLD_WRONG_OUTPUT' });
      h.store.appendEvent(source.id, turn, 'turn.done', {});
    }
    const before = h.store.listEvents(source.id, 0, -1);
    const reference = await h.post('/api/context-references', { kind: 'thread', sourceId: source.id, pointer: true });
    const accepted = JSON.parse(await h.send(recipient.id, 'CONTEXT_READ full tools', [reference.id]));
    assert.ok(accepted.reads.length);
    const page = await h.execute(recipient.id, `loochat references ${reference.id}`);
    assert.ok(!JSON.stringify(page).includes('FULL_BEGIN'), 'overview does not read or transfer full output');
    const command = page.turns.find((turn: { turnId: string }) => turn.turnId === 'wanted-turn').tools[0].command;
    const tool = dataOf(await h.whole(recipient.id, command));
    assert.deepEqual(tool.input, input);
    assert.equal(tool.output, output);
    assert.equal(tool.outputTruncated, undefined);
    assert.deepEqual(h.store.listEvents(source.id, 0, -1), before);
    assert.equal(h.manager.getThread(source.id).agentSessionId, null);
    assert.equal(h.manager.getThread(source.id).cwd, h.dir);
  } finally { await h.close(); }
});

test('a reader snapshot pins the tool version before collapse, including later chunk reads', async () => {
  const h = await setup();
  try {
    const source = await h.mk('Live tool source');
    const recipient = await h.mk('Snapshot reader');
    const firstOutput = 'OLD_SNAPSHOT\n' + '字😀'.repeat(18_000);
    h.store.appendEvent(source.id, 'live-turn', 'turn.started', { text: 'watch a tool', userOrigin: 'person' });
    h.store.appendEvent(source.id, 'live-turn', 'tool.start', { id: 'live-tool', input: { command: 'watch' } });
    h.store.appendEvent(source.id, 'live-turn', 'tool.update', { id: 'live-tool', output: firstOutput });
    const reference = await h.post('/api/context-references', { kind: 'thread', sourceId: source.id, pointer: true });
    await h.send(recipient.id, 'accept live pointer', [reference.id]);
    const page = await h.execute(recipient.id, `loochat references ${reference.id}`);
    const command = page.turns[0].tools[0].command;
    h.store.appendEvent(source.id, 'live-turn', 'tool.end', { id: 'live-tool', status: 'completed', output: 'NEW_LIVE_OUTPUT' });
    h.store.appendEvent(source.id, 'live-turn', 'turn.done', {});
    const tool = dataOf(await h.whole(recipient.id, command));
    assert.equal(tool.output, firstOutput, 'read the pinned update, not the later end or its preview');
    const fresh = await h.execute(recipient.id, `loochat references ${reference.id}`);
    assert.equal(dataOf(await h.whole(recipient.id, fresh.turns[0].tools[0].command)).output, 'NEW_LIVE_OUTPUT');
  } finally { await h.close(); }
});

test('a fixed reference keeps full tool content from its creation boundary', async () => {
  const h = await setup();
  try {
    const source = await h.mk('Fixed tool source');
    const recipient = await h.mk('Fixed reader');
    const output = 'FIXED_OUTPUT\n' + '版本😀'.repeat(12_000);
    h.store.appendEvent(source.id, 'fixed-turn', 'turn.started', { text: 'fixed', userOrigin: 'person' });
    h.store.appendEvent(source.id, 'fixed-turn', 'tool.start', { id: 'fixed-tool', input: { command: 'fixed' } });
    h.store.appendEvent(source.id, 'fixed-turn', 'tool.update', { id: 'fixed-tool', output });
    const reference = await h.post('/api/context-references', { kind: 'thread', sourceId: source.id });
    h.store.appendEvent(source.id, 'fixed-turn', 'tool.end', { id: 'fixed-tool', output: 'AFTER_FIXED_REFERENCE' });
    await h.send(recipient.id, 'fixed pointer', [reference.id]);
    const page = await h.execute(recipient.id, `loochat references ${reference.id}`);
    assert.equal(dataOf(await h.whole(recipient.id, page.turns[0].tools[0].command)).output, output);
  } finally { await h.close(); }
});
