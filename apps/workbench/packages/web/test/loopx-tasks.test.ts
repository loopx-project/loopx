import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_BROWSE, laneTasks, owners, parseGoalTasks } from '../src/loopx/tasks.js';

const todo = (id: string, over: Record<string, unknown> = {}) => ({ todo_id: id, text: `[P2] task ${id}`, title: `task ${id}`, status: 'open', done: false, priority: 'P2', claimed_by: 'codex', task_class: 'advancement_task', archive_state: 'active', updated_at: '2026-11-16T01:00:00Z', ...over });
const payload = (agent: unknown[], user: unknown[], counts: { agent?: Record<string, number>; user?: Record<string, number> } = {}) => ({
  goal_filter: 'g', served_at: '2026-11-16T02:00:00Z',
  attention_queue: { items: [{ goal_id: 'g', status: 'ok',
    agent_todos: { items: agent, total_count: agent.length, open_count: agent.filter((t: any) => !t.done).length, done_count: agent.filter((t: any) => t.done).length, ...counts.agent },
    user_todos: { items: user, total_count: user.length, open_count: user.length, done_count: 0, ...counts.user } }] },
});

test('rows parse with their state, priority and owner; a foreign or missing scope is refused', () => {
  const g = parseGoalTasks(payload([todo('a'), todo('b', { status: 'blocked', blocked_reason: 'waiting for CI' }), todo('c', { done: true, status: 'done' }), todo('d', { claimed_by: null, priority: null, text: '[P0] urgent', title: undefined })], []), 'g');
  assert.deepEqual(g.agent.tasks.map(t => [t.id, t.state, t.priority, t.owner]), [['a', 'open', 'P2', 'codex'], ['b', 'blocked', 'P2', 'codex'], ['c', 'done', 'P2', 'codex'], ['d', 'open', 'P0', null]]);
  assert.equal(g.agent.tasks[1].blockedReason, 'waiting for CI');
  assert.equal(g.agent.tasks[3].title, 'urgent');
  assert.throws(() => parseGoalTasks(payload([], []), 'other'));
  assert.throws(() => parseGoalTasks({ goal_filter: 'g', attention_queue: { items: [] } }, 'g'));
});

test('the rows the core counted but did not list are counted, open and done apart', () => {
  const g = parseGoalTasks(payload([todo('a'), todo('c', { done: true, status: 'done' })], [], { agent: { total_count: 20, open_count: 14, done_count: 6 } }), 'g');
  assert.equal(g.agent.hiddenOpen, 13);
  assert.equal(g.agent.hiddenDone, 5);
  const whole = parseGoalTasks(payload([todo('a')], []), 'g');
  assert.deepEqual([whole.agent.hiddenOpen, whole.agent.hiddenDone], [0, 0]);
});

test('lanes: your open tasks, agent work most urgent first, done newest first; monitors and archived rows are not work', () => {
  const g = parseGoalTasks(payload([
    todo('low', { priority: 'P3' }), todo('high', { priority: 'P0' }), todo('mon', { task_class: 'continuous_monitor' }), todo('old', { archive_state: 'archived' }),
    todo('done1', { done: true, status: 'done', updated_at: '2026-11-16T01:00:00Z' }), todo('done2', { done: true, status: 'done', updated_at: '2026-11-16T01:30:00Z' }),
  ], [todo('mine', { claimed_by: null, task_class: 'user_action' })]), 'g');
  const lanes = laneTasks(g, DEFAULT_BROWSE);
  assert.deepEqual(lanes.needsYou.map(t => t.id), ['mine']);
  assert.deepEqual(lanes.todo.map(t => t.id), ['high', 'low']);
  assert.deepEqual(lanes.done.map(t => t.id), ['done2', 'done1']);
});

test('filters by state, owner and search narrow the lanes', () => {
  const g = parseGoalTasks(payload([
    todo('a', { claimed_by: 'codex' }), todo('b', { claimed_by: 'claude-code', title: 'write docs' }), todo('c', { status: 'blocked' }), todo('d', { status: 'deferred', claimed_by: null }),
  ], []), 'g');
  const ids = (b: Partial<typeof DEFAULT_BROWSE>) => laneTasks(g, { ...DEFAULT_BROWSE, ...b }).todo.map(t => t.id).sort();
  assert.deepEqual(ids({ filter: 'open' }), ['a', 'b']);
  assert.deepEqual(ids({ filter: 'blocked' }), ['c', 'd']);
  assert.deepEqual(ids({ owner: 'claude-code' }), ['b']);
  assert.deepEqual(ids({ owner: '' }), ['d']);
  assert.deepEqual(ids({ query: 'DOCS' }), ['b']);
  assert.deepEqual(ids({ filter: 'done' }), []);
  assert.deepEqual(owners(g), { ids: ['claude-code', 'codex'], unassigned: true });
});
