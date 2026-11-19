import assert from 'node:assert/strict';
import test from 'node:test';

import { parseGoalDetail } from '../src/loopx/goal-detail.js';

const goalId = 'goal-26a400f5-e498-4100-8e7f-292fc9dd1c88';

/** The scoped status the core serves for one Goal; rows carry the todo items themselves. */
const scoped = (row: Record<string, unknown>) => ({
  ok: true,
  goal_filter: goalId,
  attention_queue: { items: [{ goal_id: goalId, ...row }] },
});

test('the detail carries the todo rows, not only the counts', () => {
  const detail = parseGoalDetail(scoped({
    status: 'active_state_agent_todo',
    activation_state: 'active',
    waiting_on: 'user',
    recommended_action: '确认推进方向',
    goal_channel_projection: { display_name: 'v10 验证' },
    user_todos: { open_count: 1, done_count: 2, total_count: 4, items: [{ todo_id: 'u1', text: '看一眼报告', status: 'open' }] },
    agent_todos: { open_count: 2, done_count: 0, total_count: 2, items: [{ todo_id: 'a1', text: '继续跑构建', status: 'open' }, { todo_id: 'a2', text: '整理输出', status: 'open' }] },
  }), goalId);
  assert.equal(detail.title, 'v10 验证');
  assert.equal(detail.activationState, 'active');
  assert.equal(detail.userTodos.open, 1);
  assert.deepEqual(detail.userTodos.items.map(item => item.text), ['看一眼报告']);
  assert.equal(detail.userTodos.complete, false, 'one row of four listed');
  assert.equal(detail.agentTodos.complete, true);
});

test('missing rows still count: the core said how many, it just did not list them', () => {
  const detail = parseGoalDetail(scoped({
    activation_state: 'active',
    user_todos: { open_count: 3, total_count: 3 },
    agent_todos: {},
  }), goalId);
  assert.deepEqual(detail.userTodos.items, []);
  assert.equal(detail.userTodos.open, 3);
  assert.equal(detail.userTodos.complete, false);
  assert.deepEqual(detail.agentTodos, { total: 0, open: 0, done: 0, items: [], complete: true });
});

test('a status row that failed collection does not stand in for the Goal', () => {
  const payload = {
    goal_filter: goalId,
    attention_queue: { items: [
      { goal_id: goalId, status: 'status_collection_failed', user_todos: {}, agent_todos: {} },
      { goal_id: goalId, activation_state: 'active', user_todos: { open_count: 0, total_count: 0 }, agent_todos: {} },
    ] },
  };
  const detail = parseGoalDetail(payload, goalId);
  assert.equal(detail.activationState, 'active');
});

test('an answer scoped elsewhere, or missing the Goal, is an error — never a guess', () => {
  assert.throws(() => parseGoalDetail({ goal_filter: 'goal-other' }, goalId), /not scoped/);
  assert.throws(() => parseGoalDetail({ goal_filter: goalId, attention_queue: { items: [] } }, goalId), /attention queue/);
  assert.throws(() => parseGoalDetail('nope', goalId));
});
