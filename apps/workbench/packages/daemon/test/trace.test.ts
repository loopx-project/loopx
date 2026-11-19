import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTrace } from '../src/trace.js';
import type { EventType, StoredEvent } from '../src/events.js';

/** A turn's log, one event per entry, timed a second apart. */
function log(entries: Array<[EventType, Record<string, unknown>]>, turnId = 't1'): StoredEvent[] {
  return entries.map(([type, data], i) => ({
    threadId: 'th', seq: i + 1, turnId, type, data,
    ts: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString(),
  }));
}

test('a turn reads back as its steps in order: ask, thinking, tools, requests, answer', () => {
  const trace = buildTrace(log([
    ['turn.started', { text: '列出文件' }],
    ['reasoning.delta', { text: '先' }],
    ['usage.updated', { used: 10 }], // between fragments, and not a step
    ['reasoning.delta', { text: '看看' }],
    ['tool.start', { id: 'c1', tool: 'Bash', title: 'Terminal', kind: 'execute', input: {} }],
    ['tool.update', { id: 'c1', title: 'ls' }],
    ['tool.end', { id: 'c1', status: 'completed', output: 'a.txt' }],
    ['tool.start', { id: 'c2', tool: 'mcp__github__create_issue', title: 'create_issue', input: { title: 'x' } }],
    ['tool.end', { id: 'c2', status: 'failed' }],
    ['usage.sample', { model: 'm-1', input: 100, output: 20, cacheRead: 80, startedAt: '2026-01-01T00:00:09.000Z', endedAt: '2026-01-01T00:00:10.000Z' }],
    ['message.delta', { text: '有 ' }],
    ['message.delta', { text: 'a.txt' }],
    ['turn.done', { stopReason: 'end_turn' }],
  ]));
  assert.deepEqual(trace.steps.map((s) => s.kind), ['user', 'thinking', 'tool', 'tool', 'request', 'assistant']);
  assert.equal(trace.steps[0].text, '列出文件');
  assert.equal(trace.steps[1].text, '先看看', 'fragments split by other events are one thought');
  assert.deepEqual([trace.steps[2].tool, trace.steps[2].title, trace.steps[2].status, trace.steps[2].output, trace.steps[2].input], ['Bash', 'ls', 'completed', 'a.txt', undefined]);
  assert.equal(trace.steps[3].server, 'github');
  assert.equal(trace.steps[3].input, '{\n  "title": "x"\n}');
  assert.deepEqual(trace.steps[4], { kind: 'request', ts: '2026-01-01T00:00:09.000Z', endTs: '2026-01-01T00:00:10.000Z', model: 'm-1', tokens: { input: 100, output: 20, cacheRead: 80, cacheWrite: undefined } });
  assert.equal(trace.steps[5].text, '有 a.txt');
  assert.deepEqual(trace.totals, { tools: 2, mcp: 1, requests: 1, input: 100, output: 20 });
  assert.deepEqual(trace.models, ['m-1']);
  assert.equal(trace.status, 'done');
  assert.equal(trace.endedAt, '2026-01-01T00:00:12.000Z');
});

test('an unfinished turn is running; a stopped or failed one says so, and its open tools end with it', () => {
  assert.equal(buildTrace(log([['turn.started', { text: 'x' }], ['tool.start', { id: 'a' }]])).status, 'running');
  assert.equal(buildTrace(log([['turn.started', { text: 'x' }], ['tool.start', { id: 'a' }]])).steps[1].endTs, undefined);
  assert.equal(buildTrace(log([['turn.started', { text: 'x' }], ['turn.done', { stopReason: 'cancelled' }]])).status, 'cancelled');
  const failed = buildTrace(log([
    ['turn.started', { text: 'x' }],
    ['tool.start', { id: 'a' }],
    ['context.compaction', { id: 'k', status: 'in_progress' }],
    ['context.compaction', { id: 'k', status: 'completed' }],
    ['turn.error', { message: 'provider exploded' }],
  ]));
  assert.equal(failed.status, 'error');
  assert.deepEqual(failed.steps.map((s) => s.kind), ['user', 'tool', 'context', 'error']);
  assert.equal(failed.steps[1].endTs, failed.endedAt);
  assert.equal(failed.steps[2].endTs, '2026-01-01T00:00:03.000Z', 'a compaction reported twice is one step');
  assert.equal(failed.steps[3].text, 'provider exploded');
});

test('a very long output is cut and marked; the thread keeps the whole of it', () => {
  const trace = buildTrace(log([['turn.started', { text: 'x' }], ['tool.start', { id: 'a', output: 'y'.repeat(30_000) }]]));
  assert.equal(trace.steps[1].cut, true);
  assert.ok(trace.steps[1].output!.length < 21_000);
});
