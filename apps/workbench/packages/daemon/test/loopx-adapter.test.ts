import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { goalAgentFor, LoopxBindings, permissionAvailable, type LoopxBinding } from '../src/adapters/loopx/binding.js';
import { goalContextBlock } from '../src/adapters/loopx/context.js';
import { goalCreateRequest, workspaceRef } from '../src/adapters/loopx/core.js';
import { GoalStateCache, projectGoalState } from '../src/adapters/loopx/status.js';

const payload = (over: Record<string, unknown> = {}) => ({
  goal_filter: 'g1',
  attention_queue: { items: [{
    goal_id: 'g1', status: 'connected_without_run', waiting_on: 'codex', activation_state: 'active', recommended_action: 'Start',
    goal_channel_projection: { display_name: 'My goal', source_refs: { active_state_updated_at: '2026-11-15T00:00:00Z' } },
    agent_todos: { total_count: 2, open_count: 2, items: [{ todo_id: 'a1', text: 'first', status: 'open' }, { todo_id: 'a2', text: 'second', status: 'open' }] },
    user_todos: { total_count: 1, open_count: 1, items: [] },
    ...over,
  }] },
});

test('status projection keeps what the core said and flags what it did not list', () => {
  const s = projectGoalState(payload(), 'g1');
  assert.equal(s.title, 'My goal');
  assert.equal(s.agentTodos.open, 2);
  assert.equal(s.agentTodos.complete, true);
  assert.equal(s.userTodos.complete, false);
  assert.equal(s.updatedAt, '2026-11-15T00:00:00Z');
  assert.throws(() => projectGoalState(payload(), 'other'), /invalid_status_scope/);
  assert.throws(() => projectGoalState({ goal_filter: 'g1', attention_queue: { items: [] } }, 'g1'), /goal_not_found/);
});

test('state reads are shared, remembered briefly and never cache a failure', async () => {
  let reads = 0, fail = false, clock = 0;
  const cache = new GoalStateCache(async () => { reads++; if (fail) throw new Error('down'); return projectGoalState(payload(), 'g1'); }, 1000, () => clock);
  await Promise.all([cache.get('g1'), cache.get('g1')]);
  assert.equal(reads, 1);
  await cache.get('g1');
  assert.equal(reads, 1);
  clock = 2000; fail = true;
  await assert.rejects(cache.get('g1'), /down/);
  fail = false;
  await cache.get('g1');
  assert.equal(reads, 3);
  await cache.get('g1', true);
  assert.equal(reads, 4);
});

test('only codex may write inside the workspace; only codex and claude can hold a Goal', () => {
  assert.deepEqual(permissionAvailable('codex', 'workspace_auto'), { ok: true });
  assert.equal(permissionAvailable('claude', 'workspace_auto').ok, false);
  assert.equal(permissionAvailable('claude', 'read_only').ok, true);
  assert.equal(goalAgentFor('codex'), 'codex');
  assert.equal(goalAgentFor('claude'), 'claude-code');
  assert.equal(goalAgentFor('pi'), null);
});

test('bindings survive a restart and are found again by the proposal that made them', () => {
  const file = join(mkdtempSync(join(tmpdir(), 'loopx-b-')), 'b.json');
  const binding: LoopxBinding = { goalId: 'g1', goalAgentId: 'codex', managedAgentId: 'm1', goalInstanceId: null, workspace: '/w', permission: 'read_only', proposalId: 'p1' };
  new LoopxBindings(file).set('t1', binding);
  const again = new LoopxBindings(file);
  assert.deepEqual(again.get('t1'), binding);
  assert.equal(again.byProposal('p1'), 't1');
  assert.deepEqual(again.threadsOf('g1'), ['t1']);
  again.delete('t1');
  assert.equal(new LoopxBindings(file).get('t1'), null);
  assert.equal(JSON.parse(readFileSync(file, 'utf8')).t1, undefined);
});

test('the Goal context names the boundary and says so when the state could not be read', () => {
  const binding: LoopxBinding = { goalId: 'g1', goalAgentId: 'codex', managedAgentId: null, goalInstanceId: null, workspace: '/w', permission: 'workspace_auto' };
  const withState = goalContextBlock(binding, projectGoalState(payload(), 'g1'));
  assert.match(withState, /<loochat-loopx>/);
  assert.match(withState, /工作区内自动/);
  assert.match(withState, /- first/);
  const without = goalContextBlock(binding, null, 'loopx_unavailable');
  assert.match(without, /读不到（loopx_unavailable）/);
  assert.doesNotMatch(without, /Agent 待办/);
});

test('the core review request opens no session and uses the dashboard\'s workspace token', () => {
  assert.equal(workspaceRef('/data00/home/liuhuadong.hans/code/loopx-workbench/loopx'), 'workspace-006c61f94ff5');
  const request = goalCreateRequest({ goalId: 'goal-1', title: 'T', objective: ' do   it ', completion: 'tests pass', boundary: '', goalAgentId: 'codex', workspaceRef: 'workspace-x', permission: 'read_only', operationId: 'op' });
  assert.equal(request.action_kind, 'goal.create');
  assert.equal(request.normalized_parameters.open_session, false);
  assert.equal(request.normalized_parameters.completion_criteria, 'tests pass');
  assert.match(request.normalized_parameters.objective, /^do it 完成条件：tests pass$/);
  assert.equal(request.idempotency_key, 'workbench-goal.create-op');
});
