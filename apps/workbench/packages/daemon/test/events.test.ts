import { test } from 'node:test';
import assert from 'node:assert/strict';
import { coalesceToolEvents, compactDeltas, type StoredEvent } from '../src/events.js';

let seq = 0;
function ev(type: string, data: Record<string, unknown>, turnId = 't1'): StoredEvent {
  seq += 1;
  return { threadId: 'th', seq, turnId, type: type as StoredEvent['type'], ts: `2026-10-11T00:00:${String(seq).padStart(2, '0')}Z`, data };
}

test('a burst of updates and the end of one call collapses into one event', () => {
  const out = coalesceToolEvents([
    ev('turn.started', { text: 'go' }),
    ev('tool.start', { id: 'a', kind: 'execute', status: 'in_progress', title: 'npm test' }),
    ev('tool.update', { id: 'a', status: 'in_progress', content: [{ type: 'diff', path: 'a.ts', oldText: null, newText: 'one' }] }),
    ev('tool.update', { id: 'a', status: 'in_progress', content: [{ type: 'diff', path: 'a.ts', oldText: null, newText: 'one\ntwo' }] }),
    ev('tool.end', { id: 'a', status: 'completed', content: [{ type: 'diff', path: 'a.ts', oldText: null, newText: 'one\ntwo\nthree' }] }),
    ev('message.delta', { text: 'done' }),
  ]);
  const tools = out.filter((e) => e.type.startsWith('tool.'));
  // start, and one event standing for the whole update run plus the end
  assert.deepEqual(tools.map((e) => e.type), ['tool.start', 'tool.end']);
  const merged = tools[1];
  // The state the whole run leaves behind: every field from the event that last set it, and the
  // call's last seq and time, so durations read the same.
  assert.equal(merged.seq, 5);
  assert.equal(merged.data.status, 'completed');
  assert.deepEqual(merged.data.content, [{ type: 'diff', path: 'a.ts', oldText: null, newText: 'one\ntwo\nthree' }]);
});

test('a field only an early event set survives the merge', () => {
  const out = coalesceToolEvents([
    ev('tool.start', { id: 'a', kind: 'read' }),
    // The diffs came with this update; the end that follows says no more than a status.
    ev('tool.update', { id: 'a', content: [{ type: 'diff', path: 'b.ts', oldText: 'x', newText: 'y' }] }),
    ev('tool.end', { id: 'a', status: 'completed' }),
  ]);
  const merged = out.find((e) => e.type === 'tool.end');
  assert.deepEqual(merged.data.content, [{ type: 'diff', path: 'b.ts', oldText: 'x', newText: 'y' }]);
  assert.equal(merged.data.status, 'completed');
});

test('the output text is what the last event that said anything said', () => {
  // An event's text is its output, or when it has none, the text in its content — the same
  // choice the web timeline makes event by event.
  const out = coalesceToolEvents([
    ev('tool.start', { id: 'a', kind: 'execute' }),
    ev('tool.update', { id: 'a', output: 'halfway' }),
    ev('tool.update', { id: 'a', content: [{ type: 'text', text: 'all done' }] }),
    ev('tool.end', { id: 'a', status: 'completed' }),
  ]);
  const merged = out.find((e) => e.type === 'tool.end');
  assert.equal(merged.data.output, 'all done');
  assert.equal(merged.data.status, 'completed');
});

test('a call interrupted mid-update keeps its last update as the outcome', () => {
  const out = coalesceToolEvents([
    ev('tool.start', { id: 'a', kind: 'search' }),
    ev('tool.update', { id: 'a', status: 'in_progress', output: 'partial' }),
    ev('turn.error', { message: 'interrupted' }),
  ]);
  // Still marked running: the web reads "in progress" off the merged event, as off the last one.
  const merged = out.find((e) => e.type === 'tool.update');
  assert.equal(merged.data.output, 'partial');
  assert.equal(merged.data.status, 'in_progress');
});

test('interleaved calls and other events stay apart; each run merges on its own', () => {
  const out = coalesceToolEvents([
    ev('tool.start', { id: 'a' }),
    ev('tool.start', { id: 'b' }),
    ev('tool.update', { id: 'a', output: 'a1' }),
    ev('tool.update', { id: 'b', output: 'b1' }),
    ev('tool.update', { id: 'a', output: 'a2' }),
    ev('tool.end', { id: 'a', status: 'completed' }),
    ev('message.delta', { text: 'hi' }),
    ev('tool.end', { id: 'b', status: 'completed' }),
  ]);
  assert.deepEqual(
    out.filter((e) => e.type !== 'tool.start').map((e) => `${e.data.id ?? '-'}:${e.type}:${e.data.output ?? ''}`),
    ['a:tool.update:a1', 'b:tool.update:b1', 'a:tool.end:a2', '-:message.delta:', 'b:tool.end:'],
  );
  // a's second chunk merged with its end; b's end came alone and says only a status — the
  // timeline keeps b's output from the earlier update, which stays its own event.
  const aEnd = out.find((e) => e.type === 'tool.end' && e.data.id === 'a');
  assert.equal(aEnd.data.output, 'a2');
  assert.equal(aEnd.data.status, 'completed');
  const bEnd = out.find((e) => e.type === 'tool.end' && e.data.id === 'b');
  assert.equal(bEnd.data.status, 'completed');
  assert.equal(bEnd.data.output, undefined);
});

test('cursor replay merges tool snapshots before buffering text, preserving diff, output and turn boundaries', () => {
  const diff = [{ type: 'diff', path: 'a.ts', oldText: 'old', newText: 'new' }];
  const events = [
    ev('turn.started', { text: 'go' }),
    ev('reasoning.delta', { text: 'think' }),
    ev('reasoning.delta', { text: 'ing' }),
    ev('tool.start', { id: 'a', kind: 'edit' }),
    ev('tool.update', { id: 'a', title: 'Edit a.ts', content: diff }),
    ...Array.from({ length: 1000 }, (_, i) => ev('tool.update', { id: 'a', output: `progress ${i}` })),
    ev('tool.end', { id: 'a', status: 'completed' }),
    ev('message.delta', { text: 'all ' }),
    ev('message.delta', { text: 'done' }),
    ev('turn.done', {}),
    ev('turn.started', { text: 'next' }, 't2'),
    ev('tool.update', { id: 'a', output: 'new turn' }, 't2'),
  ];
  function* cursor() { yield* events; }
  const result = compactDeltas(coalesceToolEvents(cursor()));
  assert.deepEqual(result, coalesceToolEvents(compactDeltas(events)), 'changing the compaction order does not change replay');
  const end = result.find(e => e.type === 'tool.end')!;
  assert.deepEqual(end.data, { id: 'a', title: 'Edit a.ts', content: diff, output: 'progress 999', status: 'completed' });
  assert.deepEqual(result.at(-1), events.at(-1), 'another turn is never merged into the previous one');
  assert.equal(events.filter(e => e.type === 'tool.update').length, 1002, 'compaction does not mutate the input');
});
