import assert from 'node:assert/strict';
import test from 'node:test';

import { applyManagerEvent, parseManagerHistory, parseSseFrame, proxyPathOfCorePath, type ManagerTurn } from '../src/loopx/manager.js';

test('the manager history keeps user and agent text and drops blank rows', () => {
  const messages = parseManagerHistory([
    { message_id: 'm1', role: 'user', text: '现在有几个 Goal？', created_at: '2026-11-15T10:00:00Z', turn_id: 't1' },
    { message_id: 'm2', role: 'agent', text: '4 个。', created_at: '2026-11-15T10:00:05Z', turn_id: 't1' },
    { message_id: 'm3', role: 'system', text: 'not a conversation row' },
    { message_id: 'm4', role: 'user', text: '   ' },
  ]);
  assert.equal(messages.length, 2);
  assert.equal(messages[0]!.role, 'user');
  assert.equal(messages[0]!.text, '现在有几个 Goal？');
  assert.equal(messages[1]!.turnId, 't1');
});

test('the history of an unknown shape is empty, not a crash', () => {
  assert.deepEqual(parseManagerHistory(undefined), []);
  assert.deepEqual(parseManagerHistory({}), []);
});

test('one SSE frame becomes one event; heartbeats stay null', () => {
  const event = parseSseFrame('id: 12\nevent: answer.delta\ndata: {"schema_version":"loopx_chat_event_v1","kind":"answer.delta","payload":{"text":"你好"}}');
  assert.ok(event);
  assert.equal(event.id, '12');
  assert.equal(event.kind, 'answer.delta');
  assert.equal((event.payload as { text: string }).text, '你好');
  assert.equal(parseSseFrame('id: 3\nevent: heartbeat\ndata: {"kind":"heartbeat"}'), null);
  assert.equal(parseSseFrame(''), null);
});

test('a frame without an event field but with a payload kind is still only what the frame said', () => {
  const event = parseSseFrame('id: 5\ndata: {"kind":"agent.phase","payload":{"label":"思考"}}');
  assert.equal(event, null, 'no event field is not a conversation event');
});

const idle: ManagerTurn = { turnId: 't', eventsPath: '/api/loopx/api/chat/sessions/x/turns/t/events', state: 'streaming', steps: [], answer: '', error: null };

test('activity events append one step per label, folding repeats', () => {
  let turn = idle;
  turn = applyManagerEvent(turn, { id: '1', kind: 'agent.phase', payload: { label: 'Agent 已开始处理' } });
  turn = applyManagerEvent(turn, { id: '2', kind: 'agent.phase', payload: { label: 'Agent 已开始处理' } });
  turn = applyManagerEvent(turn, { id: '3', kind: 'agent.phase', payload: { label: 'Agent 正在思考' } });
  assert.deepEqual(turn.steps, ['Agent 已开始处理', 'Agent 正在思考']);
});

test('deltas accumulate and the final answer replaces the stream text', () => {
  let turn = idle;
  turn = applyManagerEvent(turn, { id: '1', kind: 'answer.delta', payload: { text: 'LoopX ' } });
  turn = applyManagerEvent(turn, { id: '2', kind: 'answer.delta', payload: { text: '管家管着 4 个。' } });
  assert.equal(turn.answer, 'LoopX 管家管着 4 个。');
  turn = applyManagerEvent(turn, { id: '3', kind: 'answer.final', payload: { response: { message: 'LoopX 管家目前管着 4 个 Goal。' } } });
  assert.equal(turn.answer, 'LoopX 管家目前管着 4 个 Goal。');
});

test('completion rests the turn; failure keeps the partial answer and says why', () => {
  let done = applyManagerEvent(idle, { id: '1', kind: 'answer.delta', payload: { text: '部分回答' } });
  done = applyManagerEvent(done, { id: '2', kind: 'turn.completed', payload: {} });
  assert.equal(done.state, 'idle');
  assert.deepEqual(done.steps, []);
  let failed = applyManagerEvent(idle, { id: '1', kind: 'turn.failed', payload: { error: 'executor closed' } });
  assert.equal(failed.state, 'idle');
  assert.equal(failed.error, 'executor closed');
});

test('an unknown event kind passes through unchanged', () => {
  const before = idle;
  const after = applyManagerEvent(before, { id: '9', kind: 'some.future.kind', payload: { anything: true } });
  assert.equal(after, before);
});

test('the core event URL becomes the proxy path', () => {
  assert.equal(proxyPathOfCorePath('/api/chat/sessions/x/turns/t/events'), '/api/loopx/api/chat/sessions/x/turns/t/events');
  assert.equal(proxyPathOfCorePath('/status.json'), '/status.json');
});
