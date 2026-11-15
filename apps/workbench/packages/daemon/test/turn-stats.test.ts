import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { buildTrace } from '../src/trace.js';
import { aggregateThreadStats, statsOfTrace } from '../src/turn-stats.js';
import type { EventType, StoredEvent } from '../src/events.js';

const BASE = Date.UTC(2026, 0, 1, 12, 0, 0);

/** A turn's log with controlled times: each entry is [type, data, offsetMs]. */
function log(entries: Array<[EventType, Record<string, unknown>, number?]>, turnId = 't1'): StoredEvent[] {
  return entries.map(([type, data, offset = 0], i) => ({
    threadId: 'th', seq: i + 1, turnId, type, data,
    ts: new Date(BASE + offset).toISOString(),
  }));
}

test('hub timing splits a turn into model segments: wait, generation, and step membership', () => {
  const trace = buildTrace(log([
    ['turn.started', { text: '查一下' }, 0],
    ['reasoning.delta', { text: '想' }, 500],
    ['message.delta', { text: '去看' }, 1_500],
    ['tool.start', { id: 'a', tool: 'Bash' }, 4_000],
    ['tool.end', { id: 'a', status: 'completed' }, 5_000],
    ['message.delta', { text: '结' }, 6_000],
    ['message.delta', { text: '果' }, 6_500],
    ['tool.start', { id: 'b', tool: 'Read' }, 8_000],
    ['turn.done', { stopReason: 'end_turn' }, 9_000],
  ]));
  assert.deepEqual(trace.segments.map((s) => [s.start, s.end, s.firstOutput, s.source]), [
    [new Date(BASE).toISOString(), new Date(BASE + 4_000).toISOString(), new Date(BASE + 500).toISOString(), 'hub'],
    [new Date(BASE + 5_000).toISOString(), new Date(BASE + 8_000).toISOString(), new Date(BASE + 6_000).toISOString(), 'hub'],
  ]);
  assert.deepEqual(trace.segments[0].steps, [2, 3, 4], 'thinking, answer and the tool the model called');
  assert.deepEqual(trace.segments[1].steps, [5, 6]);
  assert.equal(trace.segments[1].outputTokens, undefined, 'no sample, no tokens');
  const stats = statsOfTrace(trace, null);
  assert.deepEqual(stats, { timing: 'hub', segments: 2, steps: 6, waitMs: 1_500, genMs: 5_500, toolMs: 2_000 });
});

test('parallel tools are timed once: the union of their intervals, not the sum', () => {
  const trace = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['message.delta', { text: 'a' }, 200],
    ['tool.start', { id: 'a' }, 1_000],
    ['tool.start', { id: 'b' }, 1_200],
    ['tool.end', { id: 'a' }, 2_000],
    ['tool.end', { id: 'b' }, 3_000],
    ['message.delta', { text: 'b' }, 3_200],
    ['turn.done', { stopReason: 'end_turn' }, 4_000],
  ]));
  const stats = statsOfTrace(trace, null);
  assert.equal(stats.toolMs, 2_000, 'one span from 1000 to 3000, not 1000+2800');
  assert.equal(stats.segments, 2);
  assert.equal(stats.waitMs, 400);
  assert.equal(stats.genMs, 1_600);
});

test('a sample with native timing replaces the hub bounds; an estimated one only brings tokens', () => {
  const native = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['reasoning.delta', { text: 't' }, 100],
    ['tool.start', { id: 'a' }, 5_000],
    ['tool.end', { id: 'a' }, 5_100],
    ['message.delta', { text: 'a' }, 5_200],
    ['usage.sample', { id: 's1', scope: 'request', model: 'm', input: 100, output: 50, startedAt: new Date(BASE + 4_500).toISOString(), durationMs: 1_400, ttftMs: 200 }, 6_000],
    ['turn.done', { stopReason: 'end_turn' }, 6_100],
  ]));
  assert.equal(native.segments.length, 2);
  const seg = native.segments[1];
  assert.equal(seg.source, 'native');
  assert.equal(seg.start, new Date(BASE + 4_500).toISOString());
  assert.equal(seg.firstOutput, new Date(BASE + 4_700).toISOString());
  assert.equal(seg.end, new Date(BASE + 5_900).toISOString(), 'the native duration, not the closing event');
  assert.equal(seg.outputTokens, 50);
  assert.equal(native.segments[0].source, 'hub', 'the earlier segment had no sample');
  assert.equal(statsOfTrace(native, null).timing, 'hub', 'any hub segment makes the turn hub-timed');

  // pi reports duration and first-token wait without a start time; the sample lands at message_end.
  const piStyle = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['message.delta', { text: 'a' }, 300],
    ['usage.sample', { id: 's3', scope: 'request', output: 25, durationMs: 1_000, ttftMs: 200 }, 1_500],
    ['turn.done', { stopReason: 'end_turn' }, 1_600],
  ]));
  const piSeg = piStyle.segments[0];
  assert.equal(piSeg.source, 'native');
  assert.equal(piSeg.start, new Date(BASE + 500).toISOString());
  assert.equal(piSeg.firstOutput, new Date(BASE + 700).toISOString());
  assert.equal(piSeg.end, new Date(BASE + 1_500).toISOString(), 'the request ended where the sample arrived');
  assert.equal(piSeg.outputTokens, 25);

  const estimated = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['message.delta', { text: 'a' }, 400],
    ['usage.sample', { id: 's2', scope: 'request', input: 90, output: 30, total: 120, timing: 'estimated', startedAt: new Date(BASE).toISOString(), endedAt: new Date(BASE + 800).toISOString() }, 800],
    ['turn.done', { stopReason: 'end_turn' }, 1_000],
  ]));
  const seg2 = estimated.segments[0];
  assert.equal(seg2.source, 'hub', 'estimated intervals are batch bounds, not request bounds');
  assert.equal(seg2.start, new Date(BASE).toISOString());
  assert.equal(seg2.end, new Date(BASE + 1_000).toISOString());
  assert.equal(seg2.outputTokens, 30);
});

test('chained tools do not fake model time; update-scoped samples match no segment', () => {
  const trace = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['message.delta', { text: 'a' }, 300],
    ['tool.start', { id: 'a' }, 1_000],
    ['tool.end', { id: 'a' }, 2_000],
    ['tool.start', { id: 'b' }, 2_000],
    ['tool.end', { id: 'b' }, 2_500],
    ['message.delta', { text: 'b' }, 2_600],
    ['usage.sample', { id: 'u1', scope: 'update', output: 999 }, 2_700],
    ['turn.done', { stopReason: 'end_turn' }, 3_000],
  ]));
  assert.equal(trace.segments.length, 2, 'the zero-length gap between the chained tools is dropped');
  assert.equal(trace.segments.every((s) => s.outputTokens === undefined), true, 'update-scoped samples match no segment');
  assert.equal(statsOfTrace(trace, null).toolMs, 1_500);
});

test('a running turn keeps its open segment and tool open; a cancelled tail reads as wait', () => {
  const running = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['reasoning.delta', { text: 't' }, 500],
  ]));
  assert.equal(running.segments.length, 1);
  assert.equal(running.segments[0].end, null);
  assert.equal(running.segments[0].firstOutput, new Date(BASE + 500).toISOString());
  const live = statsOfTrace(running, new Date(BASE + 2_000).toISOString());
  assert.deepEqual(live, { timing: 'hub', segments: 1, steps: 2, waitMs: 500, genMs: 1_500, toolMs: 0 });

  const toolRunning = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['message.delta', { text: 'a' }, 400],
    ['tool.start', { id: 'a' }, 1_000],
  ]));
  assert.equal(statsOfTrace(toolRunning, new Date(BASE + 5_000).toISOString()).toolMs, 4_000, 'the open tool counts up to now');
  assert.equal(statsOfTrace(toolRunning, new Date(BASE + 5_000).toISOString()).segments, 1);

  const cancelled = buildTrace(log([
    ['turn.started', { text: 'x' }, 0],
    ['tool.start', { id: 'a' }, 1_000],
    ['tool.end', { id: 'a' }, 2_000],
    ['turn.done', { stopReason: 'cancelled' }, 5_000],
  ]));
  const tail = cancelled.segments[cancelled.segments.length - 1];
  assert.deepEqual([tail.firstOutput, tail.end], [null, new Date(BASE + 5_000).toISOString()]);
  // Waiting before the tool call (the call was the first output) and waiting after it, never answered.
  assert.deepEqual(statsOfTrace(cancelled, null), { timing: 'hub', segments: 2, steps: 2, waitMs: 4_000, genMs: 0, toolMs: 1_000 });
});

test('a turn with usage and no model time still sums tokens; the aggregate keeps unknown unknown', () => {
  const rows = new Map([
    ['t1', { timing: 'native' as const, segments: 2, steps: 6, waitMs: 300, genMs: 6_100, toolMs: 2_000 }],
    ['t2', { timing: 'hub' as const, segments: 1, steps: 3, waitMs: 100, genMs: 400, toolMs: 1_500 }],
  ]);
  const tokens = new Map([
    ['t1', { input: 100, output: 50, total: null, cacheRead: 80, cacheWrite: null, reasoning: null, measured: 1 }],
    ['t2', { input: null, output: 100, total: null, cacheRead: null, cacheWrite: null, reasoning: null, measured: 1 }],
  ]);
  const stats = aggregateThreadStats({
    threadId: 'th', checkedAt: new Date(BASE).toISOString(), running: false,
    turns: [{ turnId: 't1' }, { turnId: 't2' }, { turnId: 't3' }],
    rows, tokens,
    compactions: { count: 0, last: null },
  });
  assert.deepEqual(stats.compactions, { count: 0, last: null }, 'the summary passes through untouched');
  assert.equal(stats.turns, 3);
  assert.equal(stats.steps, 9);
  assert.deepEqual(stats.timing, {
    source: 'hub', turns: 2, segments: 3, waitMs: 400, avgWaitMs: 400 / 3, genMs: 6_500, modelMs: 6_900, toolMs: 3_500,
  });
  assert.deepEqual(stats.tokens, {
    turnsWithout: 1, input: 100, cacheRead: 80, cacheWrite: null, output: 150, reasoning: null, total: null,
  });
  assert.equal(stats.tokPerSec, 150 / 6.5, 'outputs and generation summed over the turns that measured both');
  assert.equal(stats.tokPerSecApprox, true, 't2 contributed with hub timing');
  assert.equal(stats.running, false);

  const nothing = aggregateThreadStats({
    threadId: 'th', checkedAt: new Date(BASE).toISOString(), running: false,
    turns: [{ turnId: 't3' }], rows: new Map(), tokens: new Map(),
    compactions: { count: 1, last: { ts: BASE, automatic: true } },
  });
  assert.equal(nothing.timing, null, 'nothing measured anywhere: no reading');
  assert.equal(nothing.tokens, null);
  assert.equal(nothing.tokPerSec, null);
});

test('the stats response counts completed native compactions for the context panel', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-compaction-stats-'));
  const store = new Store(dir);
  const adapter = new PiRpcAdapter(['echo'], {});
  const manager = new ThreadManager(store, new Map([['pi', adapter]]), [dir], new AttachmentStore(dir));
  const thread = await manager.createThread({ agent: 'pi', cwd: dir, title: 'compactions' });
  const id = thread.id;
  store.appendEvent(id, 't1', 'turn.started', { text: '干活' });
  store.appendEvent(id, 't1', 'context.compaction', { id: 'c1', status: 'in_progress', automatic: true });
  store.appendEvent(id, 't1', 'context.compaction', { id: 'c1', status: 'completed', automatic: true });
  store.appendEvent(id, 't1', 'context.compaction', { id: 'c1', status: 'completed' }); // a replayed terminal update
  store.appendEvent(id, 't1', 'context.compaction', { id: 'c2', status: 'failed', automatic: true }); // never finished
  const manual = store.appendEvent(id, 't1', 'context.compaction', { id: 'c3', status: 'completed' }); // no automatic flag
  store.appendEvent(id, 't1', 'turn.done', { stopReason: 'end_turn' });
  const stats = manager.threadStats(id);
  assert.equal(stats.compactions.count, 2, 'distinct completed ids only');
  assert.deepEqual(stats.compactions.last, { ts: manual.ts, automatic: false }, 'the latest completed one, manual when it said nothing');
});

test('per-turn stats are stored when a turn ends, backfilled for old ones, and never scanned per read', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-turn-stats-'));
  const store = new Store(dir);
  const adapter = new PiRpcAdapter(['echo'], {});
  const manager = new ThreadManager(store, new Map([['pi', adapter]]), [dir], new AttachmentStore(dir));
  const token = 'test-turn-stats-token';
  const server = createHttpServer({ manager, token, webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const get = (path: string) => fetch(base + path, { headers: { Authorization: `Bearer ${token}` } });
  const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
  try {
    assert.equal((await fetch(`${base}/api/threads/nope/stats`, { headers: { Authorization: `Bearer ${token}` } })).status, 404);
    const thread = await manager.createThread({ agent: 'pi', cwd: dir, title: 'stats' });
    const id = thread.id;
    // Turn 1: a real exchange with a native usage sample.
    store.appendEvent(id, 't1', 'turn.started', { text: '第一轮' });
    await sleep(4);
    store.appendEvent(id, 't1', 'reasoning.delta', { text: '想' });
    await sleep(4);
    store.appendEvent(id, 't1', 'tool.start', { id: 'a', tool: 'Bash' });
    await sleep(4);
    store.appendEvent(id, 't1', 'tool.end', { id: 'a', status: 'completed', output: 'ok' });
    await sleep(4);
    store.appendEvent(id, 't1', 'message.delta', { text: '答' });
    // Input as the adapters emit it after normalization: plain 1000 plus 900 of cache traffic.
    store.appendEvent(id, 't1', 'usage.sample', { id: 's1', scope: 'request', model: 'm-1', input: 1_900, output: 200, cacheRead: 800, cacheWrite: 100 });
    store.appendEvent(id, 't1', 'turn.done', { stopReason: 'end_turn' });
    await sleep(4);
    // Turn 2: only an ask, nothing measured (an old or imported turn reads the same way).
    store.appendEvent(id, 't2', 'turn.started', { text: '第二轮', imported: true });
    store.appendEvent(id, 't2', 'message.delta', { text: '好' });
    store.appendEvent(id, 't2', 'turn.done', { stopReason: 'end_turn', imported: true });

    const first = await (await get(`/api/threads/${id}/stats`)).json() as any;
    assert.equal(first.turns, 2);
    assert.equal(first.running, false);
    assert.ok(first.timing, 'both turns measurable from the event log');
    assert.equal(first.timing.source, 'hub');
    assert.ok(first.timing.segments >= 1);
    assert.ok(first.timing.modelMs > 0, 'real gaps between appends give real time');
    assert.deepEqual(
      [first.tokens.input, first.tokens.cacheRead, first.tokens.cacheWrite, first.tokens.output],
      [1_900, 800, 100, 200], 'input is normalized: plain input plus cache reads and writes',
    );
    assert.equal(first.tokens.turnsWithout, 1, 'the second turn has no usage data');
    assert.equal(first.tokPerSecApprox, true);

    // The read stored both turns; a second read answers from storage, byte-identical in shape.
    const stored = store.telemetry.turnStatsRows(id);
    assert.equal(stored.size, 2);
    assert.ok(stored.get('t1'));
    assert.ok(stored.get('t2'));
    const second = await (await get(`/api/threads/${id}/stats`)).json() as any;
    assert.equal(second.steps, first.steps);
    assert.equal(second.timing.modelMs, first.timing.modelMs);

    // A turn that ended while the daemon was down (no stored row) is recomputed once, same result.
    store.db.prepare('DELETE FROM telemetry_turn_stats WHERE thread_id=?').run(id);
    const third = await (await get(`/api/threads/${id}/stats`)).json() as any;
    assert.equal(third.timing.modelMs, first.timing.modelMs);
    assert.equal(store.telemetry.turnStatsRows(id).size, 2);

    // A running turn: read live, values grow, nothing is stored for it.
    store.appendEvent(id, 't3', 'turn.started', { text: '第三轮' });
    await sleep(4);
    store.appendEvent(id, 't3', 'message.delta', { text: '还在' });
    const live = await (await get(`/api/threads/${id}/stats`)).json() as any;
    assert.equal(live.running, true);
    assert.equal(live.turns, 3);
    assert.equal(store.telemetry.turnStatsRows(id).size, 2, 'the running turn is not stored');
    assert.equal(live.tokens.turnsWithout, 1, 'the running turn has not finished, so its verdict is deferred');
    store.appendEvent(id, 't3', 'turn.done', { stopReason: 'end_turn' });
    const done = await (await get(`/api/threads/${id}/stats`)).json() as any;
    assert.equal(done.running, false);
    assert.equal(store.telemetry.turnStatsRows(id).size, 3, 'ended turn gets stored by the read');
  } finally {
    server.close();
    await manager.shutdown();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('the trace carries the segments the timeline will draw', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-turn-stats-trace-'));
  const store = new Store(dir);
  const adapter = new PiRpcAdapter(['echo'], {});
  const manager = new ThreadManager(store, new Map([['pi', adapter]]), [dir], new AttachmentStore(dir));
  const token = 'test-turn-stats-token';
  const server = createHttpServer({ manager, token, webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const get = (path: string) => fetch(base + path, { headers: { Authorization: `Bearer ${token}` } });
  try {
    const thread = await manager.createThread({ agent: 'pi', cwd: dir, title: 'trace' });
    const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
    store.appendEvent(thread.id, 't1', 'turn.started', { text: '问' });
    await sleep(4);
    store.appendEvent(thread.id, 't1', 'message.delta', { text: '答' });
    await sleep(4);
    store.appendEvent(thread.id, 't1', 'tool.start', { id: 'a', tool: 'Read' });
    await sleep(4);
    store.appendEvent(thread.id, 't1', 'tool.end', { id: 'a', status: 'completed' });
    await sleep(4);
    store.appendEvent(thread.id, 't1', 'usage.sample', { id: 's1', scope: 'request', output: 12 });
    store.appendEvent(thread.id, 't1', 'turn.done', { stopReason: 'end_turn' });
    const trace = await (await get(`/api/threads/${thread.id}/trace`)).json() as any;
    assert.equal(trace.segments.length >= 2, true);
    assert.deepEqual(trace.segments[0].steps.includes(3), true, 'the tool the model called is part of its segment');
    const withTokens = trace.segments.find((s: any) => s.outputTokens === 12);
    assert.ok(withTokens, 'the request tokens land on the segment they belong to');
  } finally {
    server.close();
    await manager.shutdown();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
