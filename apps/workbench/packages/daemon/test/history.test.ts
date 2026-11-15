import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.js';
import type { EventType } from '../src/events.js';

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-history-'));
  const store = new Store(dir);
  const make = (id: string) => store.createThread({ id, agent: 'pi', cwd: dir, title: id, agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  make('thread');
  const add = (turn: string, type: EventType, data: Record<string, unknown>) => store.appendEvent('thread', turn, type, data);
  return { store, add, make, close: () => { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('eight turns of answers exclude tools and intermediate text; work and detail are separate', async () => {
  const f = fixture();
  try {
    for (let i = 0; i < 12; i++) {
      const turn = `t${i}`;
      f.add(turn, 'turn.started', { text: `question ${i}` });
      f.add(turn, 'message.delta', { text: 'intermediate note' });
      f.add(turn, 'reasoning.delta', { text: 'private thinking '.repeat(1000) });
      f.add(turn, 'tool.start', { id: 'call', kind: 'execute', input: { command: 'secret argument' }, title: 'run test' });
      f.add(turn, 'tool.update', { id: 'call', output: 'SUPER_LARGE_RESULT'.repeat(3000), content: [{ type: 'diff', path: 'a.ts', oldText: 'one\n', newText: 'two\nthree\n' }] });
      f.add(turn, 'tool.end', { id: 'call', status: 'completed' });
      f.add(turn, 'message.delta', { text: `final answer ${i}` });
      f.add(turn, 'turn.done', {});
    }
    const page = await f.store.history.page('thread');
    assert.equal(page.events.filter(e => e.type === 'turn.started').length, 8);
    assert.equal(page.hasOlder, true);
    assert.deepEqual(page.turnIds, Array.from({ length: 12 }, (_, i) => `t${i}`));
    const json = JSON.stringify(page);
    for (const text of ['SUPER_LARGE_RESULT', 'secret argument', 'private thinking', 'intermediate note']) assert.ok(!json.includes(text), text);
    const work = page.events.find(e => e.type === 'history.work')!;
    assert.equal(work.data.count, 1);
    assert.equal((work.data.files as any[])[0].added, 2);
    const { events: entries } = await f.store.history.work('thread', work.turnId!, work.seq);
    assert.ok(JSON.stringify(entries).includes('intermediate note'));
    assert.ok(!JSON.stringify(entries).includes('SUPER_LARGE_RESULT'));
    const tool = entries.find(e => e.type === 'history.tool.start')!;
    const detail = await f.store.history.detail('thread', tool.turnId!, (tool.data.detail as any).id);
    assert.equal((detail.input as any).command, 'secret argument');
    assert.ok(String(detail.output).startsWith('SUPER_LARGE_RESULT'));
    assert.equal((detail.content as any[])[0].newText, 'two\nthree\n');
    const older = await f.store.history.page('thread', page.nextCursor!);
    assert.equal(older.events.filter(e => e.type === 'turn.started').length, 4);
    assert.equal(older.hasOlder, false);
    assert.equal(older.turnIds, undefined);
    assert.equal(older.nextCursor, null);
    assert.equal(page.snapshotSeq, f.store.history.latest('thread'));
  } finally { f.close(); }
});

test('cold copied history rebuilds and detail identifiers are scoped to a turn', async () => {
  const f = fixture();
  try {
    f.add('a', 'turn.started', { text: 'hi' });
    f.add('a', 'reasoning.delta', { text: 'first ' });
    f.add('a', 'reasoning.delta', { text: 'second' });
    f.add('a', 'message.delta', { text: 'answer' });
    f.add('a', 'turn.done', {});
    f.make('copy'); f.store.copyEvents('thread', 'copy');
    const page = await f.store.history.page('copy');
    const work = page.events.find(e => e.type === 'history.work')!;
    const { events: entries } = await f.store.history.work('copy', 'a', work.seq);
    const thought = entries.find(e => e.type === 'history.thought')!;
    const detail = await f.store.history.detail('copy', 'a', (thought.data.detail as any).id);
    assert.equal(detail.text, 'first second');
    await assert.rejects(f.store.history.detail('copy', 'no-such-turn', (thought.data.detail as any).id), /turn not found/);
    assert.deepEqual(await f.store.history.page('copy'), page);
  } finally { f.close(); }
});

test('reconnect replaces affected turns with summaries and keeps a final remark after a tool', async () => {
  const f = fixture();
  try {
    f.add('a', 'turn.started', { text: 'hi' });
    const before = f.add('a', 'message.delta', { text: 'last remark' }).seq;
    f.add('a', 'tool.start', { id: 't', title: 'run' });
    f.add('a', 'tool.end', { id: 't', status: 'completed', output: 'hidden output' });
    f.add('a', 'turn.done', {});
    const reconnect = await f.store.history.since('thread', before);
    assert.deepEqual(reconnect.turns, ['a']);
    assert.ok(reconnect.events.some(e => e.type === 'message.delta' && e.data.text === 'last remark'));
    assert.ok(!JSON.stringify(reconnect).includes('hidden output'));
    assert.equal(reconnect.snapshotSeq, 5);
  } finally { f.close(); }
});

test('history HTTP APIs validate scope/cursors and SSE catches up lightly then sends complete live events', async () => {
  const { createHttpServer } = await import('../src/http.js');
  const { ThreadManager } = await import('../src/threads.js');
  const { Workdirs } = await import('../src/workdirs.js');
  const { AttachmentStore } = await import('../src/attachments.js');
  const f = fixture();
  const dir = mkdtempSync(join(tmpdir(), 'calli-history-api-'));
  const manager = new ThreadManager(f.store, new Map(), new Workdirs([dir], join(dir, 'workdirs.json'), join(dir, 'scratch')), new AttachmentStore(dir));
  const server = createHttpServer({ manager, token: 'history-test-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}`;
  const headers = { Authorization: 'Bearer history-test-token' };
  const request = (path: string) => fetch(base + path, { headers });
  try {
    f.add('a', 'turn.started', { text: 'question' });
    f.add('a', 'tool.start', { id: 'x', input: { private: true }, title: 'run' });
    f.add('a', 'tool.end', { id: 'x', status: 'completed', output: 'private output' });
    f.add('a', 'message.delta', { text: 'answer' });
    f.add('a', 'turn.done', {});
    assert.equal((await fetch(base + '/api/threads/thread/history')).status, 401);
    assert.equal((await request('/api/threads/thread/history?before=-1')).status, 400);
    assert.equal((await request('/api/threads/missing/history')).status, 404);
    const page = await (await request('/api/threads/thread/history')).json() as any;
    const work = page.events.find((e: any) => e.type === 'history.work');
    const { events: entries } = await (await request(`/api/threads/thread/history/work?turn=a&id=${work.seq}`)).json() as any;
    const id = entries.find((e: any) => e.type === 'history.tool.start').data.detail.id;
    assert.equal((await request(`/api/threads/thread/history/detail?turn=elsewhere&id=${id}`)).status, 404);
    const detail = await (await request(`/api/threads/thread/history/detail?turn=a&id=${id}`)).json() as any;
    assert.equal(detail.output, 'private output');
    const since = f.store.history.since.bind(f.store.history);
    f.store.history.since = async (...args) => {
      const snapshot = await since(...args);
      (manager as any).emitThreadEvent('thread', 'gap', 'turn.started', { text: 'snapshot gap' });
      (manager as any).emitThreadEvent('thread', 'gap', 'tool.start', { id: 'gap-tool', input: { command: 'GAP_INPUT' } });
      (manager as any).emitThreadEvent('thread', 'gap', 'tool.end', { id: 'gap-tool', output: 'GAP_OUTPUT', status: 'completed' });
      return snapshot;
    };
    const controller = new AbortController();
    const stream = await fetch(base + '/api/threads/thread/stream?view=history&after=0', { headers, signal: controller.signal });
    const reader = stream.body!.getReader();
    const first = new TextDecoder().decode((await reader.read()).value);
    assert.ok(first.includes('history.reset'));
    assert.ok(first.includes('answer'));
    assert.ok(!first.includes('private output'));
    let gap = first;
    while (!gap.includes('GAP_OUTPUT')) gap += new TextDecoder().decode((await reader.read()).value);
    assert.equal(gap.split('GAP_OUTPUT').length - 1, 1);
    assert.ok(gap.includes('GAP_INPUT'));
    // The initial history omits outputs; an uninterrupted connection receives them in full.
    (manager as any).emitThreadEvent('thread', 'live', 'turn.started', { text: 'next' });
    (manager as any).emitThreadEvent('thread', 'live', 'tool.start', { id: 'live-tool', input: { command: 'LIVE_INPUT' } });
    (manager as any).emitThreadEvent('thread', 'live', 'tool.end', { id: 'live-tool', status: 'completed', output: 'LIVE_OUTPUT' });
    let live = '';
    while (!live.includes('LIVE_OUTPUT')) live += new TextDecoder().decode((await reader.read()).value);
    assert.ok(live.includes('event: tool.start'));
    assert.ok(live.includes('LIVE_INPUT'));
    assert.ok(live.includes('event: tool.end'));
    controller.abort();
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    f.close(); rmSync(dir, { recursive: true, force: true });
  }
});

test('large work blocks and text details paginate with a stable version; file reads omit tool output', async () => {
  const f = fixture();
  try {
    f.add('a', 'turn.started', { text: 'question' });
    f.store.transaction(() => {
      for (let i = 0; i < 205; i++) {
        f.add('a', 'tool.start', { id: String(i), title: `tool ${i}` });
        f.add('a', 'tool.end', { id: String(i), status: 'completed', output: i === 0 ? 'abc'.repeat(40000) : 'small', content: i === 0 ? [{ type: 'diff', path: 'file.ts', oldText: null, newText: 'new' }] : undefined });
      }
    });
    const page = await f.store.history.page('thread');
    const block = page.events.find(e => e.type === 'history.work')!;
    assert.equal(page.events.filter(e => e.type.startsWith('history.tool.')).length, 0);
    const work = await f.store.history.work('thread', 'a', block.seq);
    const first = work.events.find(e => e.type === 'history.tool.start')!;
    assert.equal(work.events.length, 101);
    assert.equal(work.nextCursor, 100);
    const second = await f.store.history.work('thread', 'a', first.seq, work.nextCursor!, work.version);
    assert.equal(second.events.length, 101);
    assert.equal(second.nextCursor, 200);
    const third = await f.store.history.work('thread', 'a', first.seq, second.nextCursor!, work.version);
    assert.equal(third.events.length, 6);
    assert.equal(third.nextCursor, null);
    const id = (first.data.detail as any).id;
    const detail = await f.store.history.detail('thread', 'a', id);
    assert.equal(String(detail.output).length, 65536);
    const last = await f.store.history.detail('thread', 'a', id, Number(detail.nextCursor), Number(detail.version));
    assert.equal(String(last.output).length, 120000 - 65536);
    assert.equal(String(detail.output) + String(last.output), 'abc'.repeat(40000));
    assert.equal(last.nextCursor, null);
    const file = await f.store.history.detail('thread', 'a', id, 0, undefined, 'file.ts');
    assert.equal(file.output, undefined);
    assert.equal((file.content as any[])[0].newText, 'new');
    f.add('a', 'tool.update', { id: '0', output: 'new version' });
    await assert.rejects(f.store.history.detail('thread', 'a', id, 65536, Number(detail.version)), /detail changed/);
    // Work continuation survives a version bump: the entry list only grows, so a stale cursor
    // still names the same window and the answer carries the current version.
    const shifted = await f.store.history.work('thread', 'a', first.seq, 100, work.version);
    assert.equal(shifted.version > Number(work.version), true);
    assert.equal(shifted.events.filter(e => e.type === 'history.tool.start').length, 100);
    assert.equal(shifted.nextCursor, 200);
  } finally { f.close(); }
});

test('batched thinking replay keeps a stable reference and transactions roll projections back', async () => {
  const f = fixture();
  try {
    f.add('a', 'turn.started', { text: 'question' });
    const a = f.add('a', 'reasoning.delta', { text: 'a' });
    const b = f.add('a', 'reasoning.delta', { text: 'b' });
    f.add('a', 'reasoning.delta', { text: 'c' });
    assert.equal((f.store.history.projected(a).data.detail as any).id, a.seq);
    assert.equal((f.store.history.projected(b).data.detail as any).id, a.seq);
    assert.throws(() => f.store.transaction(() => { f.add('a', 'tool.start', { id: 'rolled-back' }); throw new Error('rollback'); }), /rollback/);
    f.add('a', 'message.delta', { text: 'answer' });
    f.add('a', 'turn.done', {});
    const page = await f.store.history.page('thread');
    assert.ok(!JSON.stringify(page).includes('rolled-back'));
    assert.equal((await f.store.history.detail('thread', 'a', a.seq)).text, 'abc');
  } finally { f.close(); }
});

test('only pending approvals expose decision payloads and usage survives pagination', async () => {
  const f = fixture();
  try {
    f.add('old', 'turn.started', { text: 'older' });
    f.add('old', 'usage.updated', { used: 123, size: 1000 });
    f.add('old', 'turn.done', {});
    for (let i = 0; i < 8; i++) {
      f.add(`t${i}`, 'turn.started', { text: 'question' });
      f.add(`t${i}`, 'approval.request', { approvalId: `approve-${i}`, title: 'edit', toolCall: { content: [{ type: 'diff', path: 'file', newText: 'APPROVAL_SECRET' }] }, options: [] });
      if (i < 7) {
        f.add(`t${i}`, 'approval.resolved', { approvalId: `approve-${i}`, decision: { optionId: 'allow' } });
        f.add(`t${i}`, 'turn.done', {});
      }
    }
    let page = await f.store.history.page('thread');
    assert.equal(page.events.find(e => e.type === 'usage.updated')?.data.used, 123);
    assert.equal(page.events.filter(e => e.type === 'approval.request' && e.data.toolCall).length, 1);
    f.add('t7', 'approval.resolved', { approvalId: 'approve-7', decision: { optionId: 'allow' } });
    f.add('t7', 'turn.done', {});
    page = await f.store.history.page('thread');
    assert.ok(!JSON.stringify(page).includes('APPROVAL_SECRET'));
  } finally { f.close(); }
});

test('a missing coverage marker does not trigger repair on the summary path', async () => {
  const f = fixture();
  try {
    f.add('a', 'turn.started', { text: 'question' });
    f.add('a', 'tool.start', { id: 'tool' });
    f.add('a', 'tool.end', { id: 'tool', status: 'completed', output: 'output' });
    f.add('a', 'turn.done', {});
    const db = (f.store as any).db as import('node:sqlite').DatabaseSync;
    db.prepare('DELETE FROM history_coverage WHERE thread_id=?').run('thread');
    await f.store.history.page('thread');
    const coverage = db.prepare('SELECT start_seq, end_seq FROM history_coverage WHERE thread_id=?').get('thread');
    assert.equal(coverage, undefined);
    await f.store.history.work('thread', 'a', 2);
    assert.equal(db.prepare('SELECT end_seq FROM history_coverage WHERE thread_id=?').get('thread')?.end_seq, 4);
  } finally { f.close(); }
});

test('a forked side chat reads only the new turns after its inherited boundary', async () => {
  const f = fixture();
  try {
    f.add('inherited', 'turn.started', { text: 'inherited question' });
    f.add('inherited', 'message.delta', { text: 'inherited answer' });
    f.add('inherited', 'turn.done', {});
    const boundary = f.store.history.latest('thread');
    assert.deepEqual((await f.store.history.page('thread', undefined, boundary)).events, []);
    f.add('side', 'turn.started', { text: 'side question' });
    f.add('side', 'message.delta', { text: 'side answer' });
    f.add('side', 'turn.done', {});
    const page = await f.store.history.page('thread', undefined, boundary);
    assert.ok(page.events.every(e => e.seq > boundary));
    assert.equal(page.hasOlder, false);
    assert.equal(page.snapshotSeq, 6);
  } finally { f.close(); }
});


test('cold summary and reconnect read no tool/thinking payload and write no projection rows', async () => {
  const f = fixture();
  try {
    const db = (f.store as any).db as import('node:sqlite').DatabaseSync;
    const put = db.prepare('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)');
    const add = (seq: number, type: string, data: string) => put.run('thread', seq, 'cold', type, new Date(seq * 1000).toISOString(), data);
    add(1, 'turn.started', '{"text":"question"}');
    add(2, 'message.delta', '{"text":"intermediate"}');
    // Invalid payloads make accidental JSON parsing fail, even for filtering. No payload is
    // required to return an answer, its status/duration, or the number of tool calls.
    add(3, 'reasoning.delta', 'UNREAD_THINKING');
    add(4, 'tool.start', 'UNREAD_INPUT');
    add(5, 'tool.update', 'UNREAD_OUTPUT');
    add(6, 'tool.end', 'UNREAD_OUTPUT');
    add(7, 'message.delta', '{"text":"final "}');
    add(8, 'message.delta', '{"text":"answer"}');
    add(9, 'turn.done', '{}');
    const writes = Number(db.prepare('SELECT total_changes() AS n').get()!.n);
    const page = await f.store.history.page('thread');
    assert.equal(page.events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), 'final answer');
    assert.equal(page.events.find(e => e.type === 'history.work')!.data.count, 1);
    assert.equal(page.events[0].ts, new Date(1000).toISOString());
    assert.equal(page.events.at(-1)!.ts, new Date(9000).toISOString());
    assert.ok(!JSON.stringify(page).includes('UNREAD'));
    assert.ok(!JSON.stringify(page).includes('intermediate'));
    const reconnect = await f.store.history.since('thread', 2);
    assert.deepEqual(reconnect.events, page.events);
    assert.equal(Number(db.prepare('SELECT total_changes() AS n').get()!.n), writes);
    for (const table of ['history_rows', 'history_details', 'history_turns', 'history_coverage']) assert.equal(db.prepare(`SELECT count(*) AS n FROM ${table}`).get()!.n, 0);
  } finally { f.close(); }
});

test('a work placeholder says how long its stretch ran, including events that arrived after their turn ended', async () => {
  const f = fixture();
  try {
    const ts = (s: string) => `2026-10-16T${s}.000Z`;
    const { partitionHistory } = await import('../src/history.js');
    const ev = (seq: number, type: string, at: string, data: Record<string, unknown>, turnId = 'A') =>
      ({ threadId: 'thread', seq, turnId, type, ts: ts(at), data });
    const { summary } = partitionHistory([
      ev(1, 'turn.started', '09:22:49', { text: 'go' }),
      ev(2, 'history.tool.start', '09:23:00', { id: 'c1', kind: 'execute' }),
      ev(3, 'history.tool.end', '09:23:40', { id: 'c1', status: 'completed' }),
      // The daemon restarts under the turn; the ending is written, then more of the same turn arrives.
      ev(4, 'turn.error', '09:34:56', { message: 'interrupted', code: 'interrupted' }),
      ev(5, 'history.tool.start', '09:35:10', { id: 'c3', kind: 'execute' }),
      ev(6, 'history.tool.end', '09:40:27', { id: 'c3', status: 'completed' }),
    ]);
    const blocks = summary.filter(e => e.type === 'history.work');
    assert.equal(blocks.length, 2);
    assert.equal(blocks[0].data.endedAt, ts('09:23:40'), 'a stretch ends where its last tool ended');
    assert.equal(blocks[1].data.startedAt ?? blocks[1].ts, ts('09:35:10'), 'the placeholder itself starts the stretch');
    assert.equal(blocks[1].data.endedAt, ts('09:40:27'), 'events past the turn ending still measure their stretch');
  } finally { f.close(); }
});

test('the cold path also says how long each stretch ran, without waiting for projections', async () => {
  const f = fixture();
  try {
    const a = 'turn';
    f.add(a, 'turn.started', { text: 'go' });
    f.add(a, 'tool.start', { id: 'c1', kind: 'execute', title: 'run' });
    f.add(a, 'tool.update', { id: 'c1', output: 'working' });
    f.add(a, 'tool.end', { id: 'c1', status: 'completed' });
    f.add(a, 'turn.done', {});
    const events = [...f.store.iterateEvents('thread', 0)];
    const lastTool = events.filter(e => e.type === 'tool.end').at(-1)!;
    const page = await f.store.history.page('thread');
    const work = page.events.find(e => e.type === 'history.work')!;
    assert.equal(work.data.endedAt, lastTool.ts, 'the placeholder ends where the call ended, not where it began');
  } finally { f.close(); }
});

test('merged history defers only the recipient opening prompt across cold/warm pages, work and reconnect', async () => {
  const f = fixture();
  try {
    const recipient = f.store.createThread({ id: 'recipient', agent: 'pi', cwd: '/project', title: 'recipient', agentSessionId: null, permissionMode: 'default', model: null, effort: null,
      handover: { sourceId: 'thread', agent: 'pi', title: 'source', mode: 'continue', sourceSeq: 2 } });
    f.add('failed', 'turn.started', { text: 'first attempt' });
    f.add('failed', 'turn.error', { code: 'plan_exhausted', message: 'limit' });
    const prompt = 'LONG_HANDOVER_BODY'.repeat(5000);
    const start = f.store.appendEvent(recipient.id, 'first', 'turn.started', { text: prompt });
    f.store.appendEvent(recipient.id, 'first', 'tool.start', { id: 'tool', title: 'Read file', kind: 'read' });
    f.store.appendEvent(recipient.id, 'first', 'tool.end', { id: 'tool', status: 'completed' });
    f.store.appendEvent(recipient.id, 'first', 'message.delta', { text: 'recipient answer' });
    f.store.appendEvent(recipient.id, 'first', 'turn.done', {});
    f.store.appendEvent(recipient.id, 'second', 'turn.started', { text: 'ordinary follow-up' });
    f.store.appendEvent(recipient.id, 'second', 'turn.done', {});
    for (const warm of [false, true]) {
      if (!warm) f.store.db.exec('DELETE FROM history_coverage');
      const page = await f.store.history.page(recipient.id, undefined, 0, true);
      assert.ok(!JSON.stringify(page).includes('LONG_HANDOVER_BODY'));
      assert.deepEqual(page.events.find(e => e.type === 'turn.started')?.data, { text: '', handoverCollapsed: true });
      assert.ok(JSON.stringify(page).includes('ordinary follow-up'));
      assert.ok(JSON.stringify(page).includes('recipient answer'));
      const work = page.events.find(e => e.type === 'history.work')!;
      const details = await f.store.history.work(recipient.id, work.turnId!, work.seq, 0, undefined, true);
      assert.ok(!JSON.stringify(details).includes('LONG_HANDOVER_BODY'));
      assert.ok(!JSON.stringify(await f.store.history.since(recipient.id, 0, true)).includes('LONG_HANDOVER_BODY'));
    }
    assert.ok(JSON.stringify(await f.store.history.page(recipient.id)).includes(prompt));
    assert.deepEqual(f.store.history.handoverEvent(start).data, { text: '', handoverCollapsed: true });
    assert.equal(f.store.history.handoverInfo(recipient.id).reason, 'plan_exhausted');
    assert.equal('text' in f.store.history.handoverInfo(recipient.id), false);
    assert.equal(f.store.history.handoverInfo(recipient.id, true).text, prompt);
    f.add('recovered', 'turn.started', { text: 'recovered' });
    f.add('recovered', 'turn.done', {});
    assert.equal(f.store.history.handoverInfo(recipient.id).reason, null, 'only the final source turn provides the reason');
    assert.throws(() => f.store.history.handoverInfo('thread'), /no handover source/);
    f.store.deleteThread('thread');
    assert.equal(f.store.history.handoverInfo(recipient.id).source.sourceId, 'thread');
    assert.equal(f.store.history.handoverInfo(recipient.id, true).text, prompt, 'deleted source keeps the received text');
  } finally { f.close(); }
});


test('handover summary turns defer prompt and answer in snapshots and live events', async () => {
  const f = fixture();
  try {
    const start = f.add('summary', 'turn.started', { text: 'PROMPT_SECRET', handover: 'pi2', handoverLabel: 'Agent 2' });
    const delta = f.add('summary', 'message.delta', { text: 'SUMMARY_SECRET'.repeat(2000) });
    assert.equal(f.store.history.summaryEvent(start).type, 'history.handover');
    assert.equal(f.store.history.summaryEvent(delta).data.done, false);
    assert.ok(!JSON.stringify(f.store.history.summaryEvent(delta)).includes('SUMMARY_SECRET'));
    const approval = f.add('summary', 'approval.request', { approvalId: 'decision', title: 'Approve', options: [] });
    assert.deepEqual(f.store.history.summaryEvent(approval), approval);
    const end = f.add('summary', 'turn.done', {});
    assert.equal(f.store.history.summaryEvent(end).data.done, true);
    for (const chain of [false, true]) {
      const page = await f.store.history.page('thread', undefined, 0, chain);
      assert.equal(page.events[0].type, 'history.handover');
      assert.equal(page.events[0].data.target, 'Agent 2');
      assert.ok(page.events.some(e => e.type === 'approval.request'));
      assert.ok(!JSON.stringify(page).includes('SECRET'));
      assert.ok(!JSON.stringify(await f.store.history.since('thread', start.seq, chain)).includes('SECRET'));
    }
    const text = f.store.history.summaryText('thread', 'summary');
    assert.equal(text.prompt, 'PROMPT_SECRET'); assert.equal(text.text.length, 16000);
    assert.equal(text.nextOffset, 16000);
    const rest = f.store.history.summaryText('thread', 'summary', text.nextOffset!, text.snapshotSeq);
    assert.equal(text.text + rest.text, 'SUMMARY_SECRET'.repeat(2000));
    f.add('ordinary', 'turn.started', { text: 'hello' });
    assert.throws(() => f.store.history.summaryText('thread', 'ordinary'), /not found/);
  } finally { f.close(); }
});

test('a forward read pages a conversation from its beginning, eight turns at a time, without gaps', async () => {
  const f = fixture();
  try {
    for (let i = 0; i < 20; i++) {
      f.add(`t${i}`, 'turn.started', { text: `question ${i}` });
      f.add(`t${i}`, 'message.delta', { text: `answer ${i}` });
      f.add(`t${i}`, 'turn.done', {});
    }
    const turnsOf = (page: Awaited<ReturnType<typeof f.store.history.page>>) => page.events.filter(e => e.type === 'turn.started').map(e => e.turnId);
    const first = await f.store.history.page('thread', undefined, 0, false, true);
    assert.deepEqual(turnsOf(first), Array.from({ length: 8 }, (_, i) => `t${i}`));
    assert.equal(first.hasNewer, true);
    assert.equal(first.hasOlder, false);
    const second = await f.store.history.page('thread', undefined, first.nextCursor!, false, true);
    assert.deepEqual(turnsOf(second), Array.from({ length: 8 }, (_, i) => `t${i + 8}`));
    assert.equal(second.hasNewer, true);
    const last = await f.store.history.page('thread', undefined, second.nextCursor!, false, true);
    assert.deepEqual(turnsOf(last), ['t16', 't17', 't18', 't19']);
    assert.equal(last.hasNewer, false);
    assert.equal(last.nextCursor, null);
    // The way it was read before is unchanged: the newest eight, with an older cursor.
    const newest = await f.store.history.page('thread');
    assert.deepEqual(turnsOf(newest), Array.from({ length: 8 }, (_, i) => `t${i + 12}`));
    assert.equal(newest.hasOlder, true);
    assert.equal(newest.hasNewer, undefined);
  } finally { f.close(); }
});

test('the handover of a successor says how the work was carried over, and previews it without reading all of it', () => {
  const f = fixture();
  try {
    f.make('next');
    f.store.updateThread('next', {});
    // The link is what the hub writes when it creates the successor.
    (f.store as any).db.prepare('UPDATE threads SET handover = ? WHERE id = ?').run(JSON.stringify({ sourceId: 'thread', title: 'thread', agent: 'pi', mode: 'continue', method: 'summary' }), 'next');
    const brief = `# 接手\n\n你接手 pi 在 hub 线程 thread 里的任务。\n\n工作目录 /work。\n\n## 任务\n${'写文章。'.repeat(400)}`;
    f.store.appendEvent('next', 'turn-1', 'turn.started', { text: brief, displayText: brief });
    const folded = f.store.history.handoverInfo('next');
    assert.equal(folded.method, 'summary');
    assert.equal(folded.size, brief.length);
    assert.ok(folded.preview.startsWith('写文章。写文章。'));
    assert.ok(folded.preview.length <= 161);
    assert.equal('text' in folded, false);
    assert.equal(f.store.history.handoverInfo('next', true).text, brief);
  } finally { f.close(); }
});

test('a manual handover knows whether its source went on, and read as a chain hides its first turn like an automatic one', async () => {
  const f = fixture();
  try {
    f.make('next');
    f.add('t1', 'turn.started', { text: 'work on the source' });
    f.add('t1', 'turn.done', {});
    const sourceSeq = f.store.history.latest('thread');
    const db = (f.store as any).db;
    db.prepare('UPDATE threads SET handover = ? WHERE id = ?').run(JSON.stringify({ sourceId: 'thread', title: 'thread', agent: 'pi', mode: 'branch', sourceSeq }), 'next');
    f.store.appendEvent('next', 'n1', 'turn.started', { text: 'BRIEF given to the new conversation' });
    f.store.appendEvent('next', 'n1', 'message.delta', { text: 'working on it' });
    f.store.appendEvent('next', 'n1', 'turn.done', {});
    assert.equal(f.store.handoverSourceMoved(f.store.getThread('next')!), false, 'the source has not started anything since');
    const turns = async (chain: boolean) => (await f.store.history.page('next', undefined, 0, chain)).events.filter(e => e.type === 'turn.started').map(e => e.data.text);
    assert.deepEqual(await turns(false), ['BRIEF given to the new conversation'], 'read on its own, nothing is hidden');
    assert.deepEqual(await turns(true), [''], 'read as part of a chain, the brief is the folded card');
    f.add('t2', 'turn.started', { text: 'the source goes on' });
    assert.equal(f.store.handoverSourceMoved(f.store.getThread('next')!), true);
    // A detached relay is independent, chain or not.
    db.prepare("UPDATE threads SET handover = json_set(handover, '$.detached', json('true')) WHERE id = ?").run('next');
    assert.deepEqual(await turns(true), ['BRIEF given to the new conversation']);
    assert.equal(f.store.handoverSourceMoved({ ...f.store.getThread('next')!, handover: null }), null);
  } finally { f.close(); }
});
