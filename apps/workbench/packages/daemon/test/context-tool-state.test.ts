import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mapAcpUpdate } from '../src/adapters/acp.js';
import { Store } from '../src/store.js';

function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-tool-state-'));
  let store = new Store(dir);
  const create = (id: string) => store.createThread({ id, agent: 'stub', cwd: dir, title: id, agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  create('source');
  const add = (type: 'tool.start' | 'tool.update' | 'tool.end', data: Record<string, unknown>) => store.appendEvent('source', 'turn', type, { id: 'tool', ...data });
  const read = (upper = store.history.latest('source')) => store.toolHistory.read('source', 'turn', 'tool', upper)?.[0].data;
  return { get store() { return store; }, create, add, read, reopen() { store.close(); store = new Store(dir); }, close() { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('an ACP status-only end preserves full input, title, text content and output', () => {
  const h = setup();
  try {
    h.add('tool.start', { input: { command: 'read' }, title: 'Original title' });
    const content = [{ type: 'content', content: { type: 'text', text: 'result'.repeat(4000) } }];
    h.add('tool.update', { content });
    const end = mapAcpUpdate({ sessionUpdate: 'tool_call_update', toolCallId: 'tool', status: 'completed' })!;
    h.add('tool.end', end.data);
    assert.deepEqual(h.read()?.input, { command: 'read' });
    assert.equal(h.read()?.title, 'Original title');
    assert.deepEqual(h.read()?.content, content);
    assert.equal(h.read()?.output, 'result'.repeat(4000));
    assert.equal(h.read()?.status, 'completed');
  } finally { h.close(); }
});

test('structured tool output is preserved, while text updates do not erase earlier diffs', () => {
  const h = setup();
  try {
    const output = { structuredContent: { rows: ['字', 'another'] }, content: [{ type: 'text', text: 'two rows' }] };
    const diff = { type: 'diff', path: 'file.ts', oldText: 'old', newText: 'new' };
    h.add('tool.start', { input: { query: 'all' } });
    h.add('tool.update', { content: [diff] });
    h.add('tool.end', { output, content: [{ type: 'text', text: 'finished' }] });
    assert.deepEqual(h.read()?.output, output);
    assert.ok((h.read()?.content as Array<unknown>).some(part => JSON.stringify(part) === JSON.stringify(diff)));
    assert.ok((h.read()?.content as Array<unknown>).some(part => JSON.stringify(part) === JSON.stringify({ type: 'text', text: 'finished' })));
  } finally { h.close(); }
});

test('only boundaries between updates retain a version; rollback restores payloads, versions and events', () => {
  const h = setup();
  try {
    h.add('tool.start', { input: {} });
    for (let i = 0; i < 20; i++) h.add('tool.update', { output: `update-${i}` });
    assert.equal(h.store.db.prepare('SELECT COUNT(*) AS n FROM context_tool_versions').get()?.n, 0);
    const boundary = h.store.history.latest('source');
    h.store.toolHistory.pin('source', boundary);
    h.add('tool.update', { output: 'after-boundary' });
    const before = h.store.listEvents('source', 0, -1);
    assert.equal(h.store.db.prepare('SELECT COUNT(*) AS n FROM context_tool_versions').get()?.n, 1);
    assert.throws(() => h.store.transaction(() => {
      h.store.toolHistory.pin('source', h.store.history.latest('source'));
      h.add('tool.end', { output: 'rolled-back' });
      throw new Error('acceptance failed');
    }), /acceptance failed/);
    assert.equal(h.read()?.output, 'after-boundary');
    assert.equal(h.read(boundary)?.output, 'update-19');
    assert.deepEqual(h.store.listEvents('source', 0, -1), before);
    assert.equal(h.store.db.prepare('SELECT COUNT(*) AS n FROM context_tool_versions').get()?.n, 1);
    assert.equal(h.store.db.prepare('SELECT COUNT(*) AS n FROM context_tool_boundaries').get()?.n, 1);
  } finally { h.close(); }
});

test('editing a suffix removes its full tool payloads and pins without changing the retained prefix or copies', () => {
  const h = setup();
  try {
    h.store.appendEvent('source', 'turn', 'turn.started', { text: 'retained' });
    h.add('tool.end', { output: 'retained full output' });
    const from = h.store.appendEvent('source', 'abandoned', 'turn.started', { text: 'edit this' }).seq;
    const update = h.store.appendEvent('source', 'abandoned', 'tool.update', { id: 'tool', output: 'abandoned full output' }).seq;
    h.store.toolHistory.pin('source', update);
    h.store.appendEvent('source', 'abandoned', 'tool.end', { id: 'tool', output: 'abandoned final output' });
    h.create('copy');
    h.store.transaction(() => h.store.copyEvents('source', 'copy'));
    h.store.transaction(() => h.store.messageEdits.archive('source', 'edit-fixture', from));
    assert.equal(h.store.toolHistory.read('source', 'abandoned', 'tool', Number.MAX_SAFE_INTEGER), null);
    assert.equal(h.store.db.prepare("SELECT COUNT(*) AS n FROM context_tool_versions WHERE thread_id='source'").get()?.n, 0);
    assert.equal(h.store.db.prepare("SELECT COUNT(*) AS n FROM context_tool_boundaries WHERE thread_id='source' AND seq>=?").get(from)?.n, 0);
    assert.equal(h.read()?.output, 'retained full output');
    assert.equal(h.store.toolHistory.read('copy', 'abandoned', 'tool', Number.MAX_SAFE_INTEGER)?.[0].data.output, 'abandoned final output');
  } finally { h.close(); }
});

test('a failed history edit restores the full tools and their boundaries with the event log', () => {
  const h = setup();
  try {
    const from = h.store.appendEvent('source', 'turn', 'turn.started', { text: 'edit rollback' }).seq;
    const boundary = h.add('tool.update', { output: 'before' }).seq;
    h.store.toolHistory.pin('source', boundary);
    h.add('tool.end', { output: 'final' });
    const before = h.store.listEvents('source', 0, -1);
    assert.throws(() => h.store.transaction(() => {
      h.store.messageEdits.archive('source', 'failed-edit-fixture', from);
      throw new Error('edit failed');
    }), /edit failed/);
    assert.deepEqual(h.store.listEvents('source', 0, -1), before);
    assert.equal(h.read()?.output, 'final');
    assert.equal(h.read(boundary)?.output, 'before');
    assert.equal(h.store.db.prepare('SELECT COUNT(*) AS n FROM context_tool_boundaries').get()?.n, 1);
  } finally { h.close(); }
});

test('a pinned fork cut and full payload survive restart and are removed with their thread', () => {
  const h = setup();
  try {
    const output = '字😀'.repeat(20000);
    h.store.appendEvent('source', 'turn', 'turn.started', { text: 'a real turn' });
    h.add('tool.start', { input: { command: 'watch' } });
    const cut = h.add('tool.update', { output }).seq;
    h.store.toolHistory.pin('source', cut);
    h.add('tool.end', { output: 'after cut' });
    h.create('copy');
    h.store.transaction(() => h.store.copyEvents('source', 'copy', cut));
    h.reopen();
    assert.equal(h.read(cut)?.output, output);
    assert.equal(h.read()?.output, 'after cut');
    assert.equal(h.store.toolHistory.read('copy', 'turn', 'tool', cut)?.[0].data.output, output);
    h.store.deleteThread('copy');
    assert.equal(h.store.db.prepare("SELECT COUNT(*) AS n FROM context_tool_current WHERE thread_id='copy'").get()?.n, 0);
    assert.equal(h.store.db.prepare("SELECT COUNT(*) AS n FROM context_tool_boundaries WHERE thread_id='copy'").get()?.n, 0);
  } finally { h.close(); }
});
