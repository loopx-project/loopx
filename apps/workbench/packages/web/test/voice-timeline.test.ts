import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTimeline, type StoredEvent } from '../src/timeline.js';
import type { VoiceTranscript } from '../src/voice-timeline.js';

const event = (seq: number, type: string, data: Record<string, unknown>, turnId: string | null = 'task'): StoredEvent =>
  ({ seq, type, data, turnId, threadId: 'thread', ts: `2026-01-01T00:00:${String(seq).padStart(2, '0')}Z` });
const speech = (itemId: string, role: 'user' | 'assistant', text: string, afterSeq = 0): VoiceTranscript =>
  ({ itemId, role, text, afterSeq, at: '2026-01-01T00:00:00Z', final: false, streaming: true });

test('live speakers are separate main conversation items and do not create a running task', () => {
  const timeline = buildTimeline([], [
    speech('u1', 'user', 'Hello'), speech('a1', 'assistant', 'Hi'),
    speech('u2', 'user', 'Find news'), speech('a2', 'assistant', 'Looking'),
  ]);
  assert.deepEqual(timeline.items.map(i => i.kind), ['user', 'assistant', 'user', 'assistant']);
  assert.equal(timeline.running, false);
  assert.equal(timeline.lastSeq, 0);
  assert.equal(timeline.items[1].kind === 'assistant' && timeline.items[1].done, false);
});

test('canonical speech replaces a draft once, including interrupted speech after reload', () => {
  const draft = speech('u1', 'user', 'Find newz');
  const final = event(8, 'voice.message', { ...draft, text: 'Find news', final: true }, null);
  const timeline = buildTimeline([final], [draft]);
  assert.equal(timeline.items.length, 1);
  assert.equal(timeline.items[0].kind === 'user' && timeline.items[0].text, 'Find news');
  const partial = buildTimeline([event(9, 'voice.message', { ...speech('a1', 'assistant', 'Partial reply') }, null)]);
  assert.equal(partial.items[0].kind === 'assistant' && partial.items[0].done, true);
  assert.equal(partial.running, false);
  const cleared = buildTimeline([final, event(10, 'voice.message', { ...draft, text: '', final: true }, null)], [draft]);
  assert.equal(cleared.items.length, 0);
});

test('finals arriving out of order preserve speaking order even within one millisecond', () => {
  const user = { ...speech('u', 'user', 'Question', 3), order: 2 };
  const assistant = { ...speech('a', 'assistant', 'Answer', 3), order: 3 };
  const timeline = buildTimeline([
    event(4, 'voice.message', assistant, null),
    event(5, 'voice.message', user, null),
  ]);
  assert.deepEqual(timeline.items.map(i => i.kind), ['user', 'assistant']);
});

test('speech interleaves with work without folding speech, losing approval, or inventing a fork', () => {
  const events = [
    event(1, 'turn.started', { text: '', source: 'voice' }),
    event(2, 'tool.start', { id: 'search', title: 'Search', status: 'in_progress' }),
    event(3, 'approval.request', { approvalId: 'approval', title: 'Run', options: [] }),
  ];
  const messages = [speech('u1', 'user', 'Find news', 0), speech('a1', 'assistant', 'Still here', 2)];
  const live = buildTimeline(events, messages);
  assert.equal(live.running, true);
  assert.equal(live.awaitingApproval, true);
  const work = live.items.find(i => i.kind === 'work');
  assert.ok(work && !JSON.stringify(work).includes('Still here'));
  const done = buildTimeline([...events,
    event(4, 'approval.resolved', { approvalId: 'approval', decision: { optionId: 'accept' } }),
    event(5, 'tool.end', { id: 'search', status: 'completed' }),
    event(6, 'message.delta', { text: 'Draft newz' }),
    event(7, 'message.replace', { previous: 'Draft newz', text: '## News\n\n**Final**\n\n[Source](https://example.com)', id: 'answer' }),
    event(8, 'turn.done', {}),
  ], messages.map(m => ({ ...m, streaming: false })));
  const answers = done.items.filter(i => i.kind === 'assistant');
  assert.deepEqual(answers.map(i => i.text), ['Still here', '## News\n\n**Final**\n\n[Source](https://example.com)']);
  const actions = done.items.filter(i => i.kind === 'actions');
  assert.equal(actions.length, 1);
  assert.equal(actions[0].turnId, 'task');
  assert.equal(done.running, false);
});

test('an authoritative corrected remark keeps its place when later work folds it', () => {
  const timeline = buildTimeline([
    event(1, 'turn.started', { text: 'go' }),
    event(2, 'message.delta', { text: 'Wrong' }),
    event(3, 'message.replace', { previous: 'Wrong', text: '**Correct**', id: 'remark' }),
    event(4, 'tool.start', { id: 'tool', title: 'Tool' }),
    event(5, 'message.delta', { text: 'Final' }),
    event(6, 'turn.done', {}),
  ]);
  const work = timeline.items.find(i => i.kind === 'work');
  assert.ok(work?.kind === 'work');
  assert.equal(work.entries.find(e => e.kind === 'note')?.text, '**Correct**');
  assert.equal(timeline.items.filter(i => i.kind === 'assistant').length, 1);
});


test('closed sessions fold live and persisted speech without absorbing task answers', () => {
  const closed = event(4, 'voice.closed', { sessionId: 'call', startedAt: '2026-01-01T00:00:00Z', endedAt: '2026-01-01T00:02:03Z', count: 1 }, null);
  const timeline = buildTimeline([
    event(1, 'voice.message', { ...speech('call:u1', 'user', 'spoken'), sessionId: 'call' }, null),
    event(2, 'turn.started', { text: 'task' }), event(3, 'message.delta', { text: 'result' }), closed,
  ], [speech('call:u1', 'user', 'spoken')]);
  assert.equal(timeline.items.filter(i => i.kind === 'user' && i.text === 'spoken').length, 0);
  assert.ok(timeline.items.some(i => i.kind === 'assistant' && i.text === 'result'));
  const recap = timeline.items.find(i => i.kind === 'voice-recap');
  assert.equal(recap?.seconds, 123);
  assert.equal(recap?.count, 1);
});


test('a failed recap request leaves the actual background task running', () => {
  const timeline = buildTimeline([event(1, 'turn.started', { text: 'work' }), event(2, 'voice.summary.error', {}, null)]);
  assert.equal(timeline.running, true);
  assert.equal(timeline.items.filter(i => i.kind === 'error').length, 1);
});
