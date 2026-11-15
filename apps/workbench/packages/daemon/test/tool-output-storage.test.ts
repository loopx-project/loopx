import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { AttachmentStore } from '../src/attachments.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import type { EventType } from '../src/events.js';
import { HttpError } from '../src/errors.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';

// Tool outputs used to be stored once per streamed snapshot, each a full accumulated copy
// (docs/tool-output-storage-design.md). These tests pin the collapse, the cap, the on-demand
// read back from the agent session, and the one-time migration of old logs.

function storeFixture() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-tool-out-'));
  const store = new Store(dir);
  store.createThread({ id: 'thread', agent: 'stub', cwd: dir, title: 'thread', agentSessionId: 'sess-1', permissionMode: 'default', model: null, effort: null });
  const add = (turn: string | null, type: EventType, data: Record<string, unknown>) => store.appendEvent('thread', turn, type, data);
  return { store, add, dir, close: () => { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

const storedRows = (store: Store, type: string) =>
  store.listEvents('thread').filter((e) => e.type === type);

test('repeated snapshots of one call collapse to the last, and detail reads it back', async () => {
  const f = storeFixture();
  try {
    f.add('t1', 'turn.started', { text: 'run it' });
    f.add('t1', 'tool.start', { id: 'exec-1', kind: 'execute', input: { command: 'ls' } });
    f.add('t1', 'tool.update', { id: 'exec-1', status: 'in_progress', output: 'first' });
    f.add('t1', 'tool.update', { id: 'exec-1', status: 'in_progress', output: 'first\nsecond' });
    f.add('t1', 'tool.update', { id: 'exec-1', status: 'in_progress', output: 'first\nsecond\nthird' });
    // Mid-run, the one stored snapshot is the newest; a refresh reads exactly this.
    const updates = storedRows(f.store, 'tool.update');
    assert.equal(updates.length, 1);
    assert.equal(updates[0].data.output, 'first\nsecond\nthird');
    // The end carries the final output, so it replaces the stored snapshot entirely.
    f.add('t1', 'tool.end', { id: 'exec-1', status: 'completed', output: 'first\nsecond\nthird\nfinal' });
    assert.equal(storedRows(f.store, 'tool.update').length, 0);
    const detail = await f.store.history.detail('thread', 't1', storedRows(f.store, 'tool.start')[0].seq);
    assert.equal(detail.output, 'first\nsecond\nthird\nfinal');
  } finally {
    f.close();
  }
});

test('an oversized output is capped head and tail with an honest marker', async () => {
  const f = storeFixture();
  try {
    f.add('t1', 'turn.started', { text: 'big' });
    f.add('t1', 'tool.start', { id: 'exec-big', kind: 'execute', input: {} });
    const big = 'x'.repeat(200_000);
    f.add('t1', 'tool.update', { id: 'exec-big', status: 'in_progress', output: big });
    const row = storedRows(f.store, 'tool.update')[0];
    assert.equal(row.data.outputTruncated, true);
    assert.equal(row.data.outputOmitted, 200_000 - 96 * 1024 - 32 * 1024);
    assert.ok(String(row.data.output).includes('[truncated, '));
    assert.ok(String(row.data.output).length < 140_000);
    // Diffs are never capped: the read model counts added and removed lines from them.
    const diff = { type: 'diff', path: 'a.ts', oldText: 'one\n', newText: `two\n${'line\n'.repeat(80_000)}` };
    f.add('t1', 'tool.start', { id: 'exec-diff', kind: 'edit', input: {} });
    f.add('t1', 'tool.update', { id: 'exec-diff', status: 'in_progress', content: [diff] });
    const starts = storedRows(f.store, 'tool.start');
    const diffDetail = await f.store.history.detail('thread', 't1', starts[1].seq);
    assert.equal((diffDetail.content as Array<{ path: string }>)[0].path, 'a.ts');
  } finally {
    f.close();
  }
});

test('a snapshot that drops a field the stored one carries does not replace it', async () => {
  const f = storeFixture();
  try {
    f.add('t1', 'turn.started', { text: 'patch' });
    f.add('t1', 'tool.start', { id: 'exec-patch', kind: 'edit', input: {} });
    f.add('t1', 'tool.update', { id: 'exec-patch', status: 'in_progress', content: [{ type: 'diff', path: 'b.ts', oldText: 'a\n', newText: 'b\n' }] });
    // An end that carries the output but no diffs: the stored diffs would be lost, so the
    // update row stays and the end is kept alongside it.
    f.add('t1', 'tool.end', { id: 'exec-patch', status: 'completed', output: 'patched' });
    assert.equal(storedRows(f.store, 'tool.update').length, 1);
    const detail = await f.store.history.detail('thread', 't1', storedRows(f.store, 'tool.start')[0].seq);
    assert.equal((detail.content as Array<{ path: string }>)[0].path, 'b.ts');
    assert.equal(detail.output, 'patched');
  } finally {
    f.close();
  }
});

test('the migration collapses old logs once and a rerun changes nothing', async () => {
  const f = storeFixture();
  try {
    f.add('t1', 'turn.started', { text: 'old style' });
    f.add('t1', 'tool.start', { id: 'legacy', kind: 'execute', input: {} });
    // Old-style rows written straight through a second connection, the way every snapshot
    // used to land before the collapse existed.
    const raw = new DatabaseSync(join(f.dir, 'hub.db'));
    const insert = raw.prepare('INSERT INTO events (thread_id, seq, turn_id, type, ts, data) VALUES (?, ?, ?, ?, ?, ?)');
    let seq = (raw.prepare("SELECT MAX(seq) AS n FROM events WHERE thread_id = 'thread'").get() as { n: number }).n;
    for (let i = 1; i <= 5; i++) {
      seq += 1;
      insert.run('thread', seq, 't1', 'tool.update', new Date().toISOString(), JSON.stringify({ id: 'legacy', status: 'in_progress', output: 'grow\n'.repeat(i * 100) }));
    }
    seq += 1;
    insert.run('thread', seq, 't1', 'tool.end', new Date().toISOString(), JSON.stringify({ id: 'legacy', status: 'completed', output: 'grow\n'.repeat(500) + 'done' }));
    // A second call whose snapshots were never followed by an end; the big output also
    // exercises the cap during migration.
    for (let i = 1; i <= 3; i++) {
      seq += 1;
      insert.run('thread', seq, 't1', 'tool.update', new Date().toISOString(), JSON.stringify({ id: 'legacy-open', status: 'in_progress', output: 'y'.repeat(50_000 * i) }));
    }
    raw.close();
    f.add('t1', 'turn.done', {});
    f.store.collapseStoredToolOutputs();
    // The finished call keeps only its end; the still-open one keeps its newest snapshot, capped.
    assert.equal(storedRows(f.store, 'tool.update').length, 1);
    const open = storedRows(f.store, 'tool.update')[0];
    assert.equal(open.data.id, 'legacy-open');
    assert.equal(open.data.outputTruncated, true);
    const end = storedRows(f.store, 'tool.end')[0];
    assert.ok(String(end.data.output).endsWith('done'));
    f.store.collapseStoredToolOutputs(); // the marker keeps a rerun from touching anything
    assert.equal(storedRows(f.store, 'tool.update').length, 1);
  } finally {
    f.close();
  }
});

function managerFixture(adapter: AgentAdapter) {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-tool-mgr-'));
  const workdir = mkdtempSync(join(tmpdir(), 'calli-tool-work-'));
  const store = new Store(dataDir);
  const manager = new ThreadManager(store, new Map([['stub', adapter]]), new Workdirs([workdir], join(dataDir, 'workdirs.json'), join(dataDir, 'scratch')), new AttachmentStore(dataDir));
  return { manager, store, workdir, close: () => { store.close(); rmSync(dataDir, { recursive: true, force: true }); rmSync(workdir, { recursive: true, force: true }); } };
}

const historyAdapter = (session: Array<{ text: string; events: Array<{ type: string; data: Record<string, unknown> }> }>) => ({
  label: 'Stub',
  available: async () => true,
  capabilities: { resume: false, approvals: false, permissionModes: [], effortLevels: [], images: false, deleteSession: false, history: true, fork: false },
  resumeCommand: (id: string) => `echo resume ${id}`,
  async commands() { return []; },
  async loadHistory() { return session; },
}) as unknown as AgentAdapter;

test('a capped output is read back whole from the agent session', async () => {
  const session = [{ text: 'run it', events: [{ type: 'tool.end', data: { id: 'exec-1', status: 'completed', output: 'WHOLE OUTPUT' } }] }];
  const f = managerFixture(historyAdapter(session));
  try {
    const thread = await f.manager.createThread({ agent: 'stub', cwd: f.workdir });
    f.store.updateThread(thread.id, { agentSessionId: 'sess-1' });
    const t = f.store.appendEvent(thread.id, 't1', 'turn.started', { text: 'run it' });
    const start = f.store.appendEvent(thread.id, t.turnId, 'tool.start', { id: 'exec-1', kind: 'execute', input: {} });
    f.store.appendEvent(thread.id, t.turnId, 'tool.update', { id: 'exec-1', status: 'in_progress', output: 'x'.repeat(200_000) });
    f.store.appendEvent(thread.id, t.turnId, 'tool.end', { id: 'exec-1', status: 'completed', output: 'x'.repeat(200_000) });
    f.store.appendEvent(thread.id, t.turnId, 'turn.done', {});
    assert.equal(f.manager.canReadToolOutput(thread.id), true);
    const { output } = await f.manager.threadToolOutput(thread.id, t.turnId!, start.seq);
    assert.equal(output, 'WHOLE OUTPUT');
  } finally {
    f.close();
  }
});

test('reading back refuses clearly when the agent keeps no readable history', async () => {
  const f = managerFixture({
    label: 'Stub', available: async () => true,
    capabilities: { resume: false, approvals: false, permissionModes: [], effortLevels: [], images: false, deleteSession: false, history: false, fork: false },
    resumeCommand: (id: string) => id, async commands() { return []; },
  } as unknown as AgentAdapter);
  try {
    const thread = await f.manager.createThread({ agent: 'stub', cwd: f.workdir });
    f.store.updateThread(thread.id, { agentSessionId: 'sess-9' });
    const t = f.store.appendEvent(thread.id, 't1', 'turn.started', { text: 'q' });
    const start = f.store.appendEvent(thread.id, t.turnId, 'tool.start', { id: 'e', kind: 'execute', input: {} });
    f.store.appendEvent(thread.id, t.turnId, 'tool.update', { id: 'e', status: 'in_progress', output: 'part' });
    f.store.appendEvent(thread.id, t.turnId, 'tool.end', { id: 'e', status: 'completed', output: 'part' });
    f.store.appendEvent(thread.id, t.turnId, 'turn.done', {});
    assert.equal(f.manager.canReadToolOutput(thread.id), false);
    await assert.rejects(
      () => f.manager.threadToolOutput(thread.id, t.turnId!, start.seq),
      (err: unknown) => err instanceof HttpError && err.status === 400,
    );
  } finally {
    f.close();
  }
});
