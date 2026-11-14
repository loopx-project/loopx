import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTimeline, type StoredEvent } from '../src/timeline.js';

const event = (type: string, data = {}, seq = 1): StoredEvent => ({
  threadId: 'thread', turnId: 'turn', seq, type, data, ts: '2026-10-19T13:00:00.000Z',
});
const start = event('turn.started', { text: 'hello' });
const waiting = (events: StoredEvent[]) => buildTimeline(events).items.find(i => i.kind === 'waiting');

test('a received turn shows preparation immediately and waits for output across reloads', () => {
  assert.equal(waiting([start])?.phase, 'preparing');
  const sent = event('turn.phase', { phase: 'prompt_sent' }, 2);
  assert.equal(waiting([start, sent])?.phase, 'response');
  assert.equal(waiting([start, sent, event('usage.updated', { used: 123 })])?.phase, 'response');
  assert.equal(waiting([start, sent])?.startedAt, start.ts, 'the timer keeps the original start on reconnect');
  assert.equal(waiting([start, sent, event('turn.phase', { phase: 'image_check' })])?.phase, 'preparing');
});

test('visible activity and terminal states replace the waiting row', () => {
  for (const next of [event('reasoning.delta', { text: 'thinking' }), event('message.delta', { text: 'answer' }),
    event('tool.start', { id: 'tool', title: 'read' }), event('approval.request', { approvalId: 'a', options: [] }),
    event('turn.done'), event('turn.done', { stopReason: 'cancelled' }), event('turn.error', { message: 'failed' }),
    event('history.work', { count: 1, version: 3 })]) {
    assert.equal(waiting([start, next]), undefined, next.type);
  }
  assert.equal(waiting([event('turn.started', { imported: true })]), undefined);
  assert.equal(waiting([event('turn.started', { fromTerminal: true })]), undefined);
});

test('a later turn does not inherit the previous turn’s output or phase', () => {
  const next = { ...start, seq: 5, turnId: 'next' };
  assert.equal(waiting([start, event('message.delta', { text: 'answer' }), event('turn.done'), next])?.phase, 'preparing');
});

test('a turn the agent started on its own is marked as such and never waits', () => {
  const own = event('turn.started', { text: '', source: 'background' });
  assert.equal(waiting([own]), undefined);
  const items = buildTimeline([own, event('tool.start', { id: 'tool', title: 'read' }, 2)]).items;
  const user = items.find(i => i.kind === 'user');
  assert.equal(user?.kind === 'user' && user.background, true);
  assert.ok(items.some(i => i.kind === 'work'), 'its work is shown');
});
