import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PlanReply } from '../src/adapters/plan-reply.js';
import type { EmittedEvent } from '../src/events.js';

test('a plan-mode reply: words before a tool are an aside, the last words are the plan', () => {
  const events: EmittedEvent[] = [];
  const reply = new PlanReply(e => events.push(e), 'plan');
  reply.add('先看看代码。');
  reply.aside();
  reply.aside(); // nothing held: nothing sent
  reply.add('方案：');
  reply.add('1. 改样式');
  reply.finish(true);
  assert.deepEqual(events, [
    { type: 'message.delta', data: { text: '先看看代码。' } },
    { type: 'plan.completed', data: { id: 'plan', text: '方案：1. 改样式' } },
  ]);
});

test('a stopped or failed turn gives back its words as a message, and blank words are no plan', () => {
  const events: EmittedEvent[] = [];
  const reply = new PlanReply(e => events.push(e), 'plan');
  reply.add('写到一半');
  reply.finish(false);
  reply.add('  \n');
  reply.finish(true);
  assert.deepEqual(events, [
    { type: 'message.delta', data: { text: '写到一半' } },
    { type: 'message.delta', data: { text: '  \n' } },
  ]);
});
