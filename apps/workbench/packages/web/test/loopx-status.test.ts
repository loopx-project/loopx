import assert from 'node:assert/strict';
import test from 'node:test';

import { parseLoopxStatus, sortGoalTree } from '../src/loopx/status.js';

const payload = (goals: unknown[]) => ({ run_history: { goals }, attention_queue: { items: [] } });

test('the narrow slice parses goals and the attention rows', () => {
  const status = parseLoopxStatus(payload([
    { id: 'g1', display_name: '一个 Goal', status: 'active', activation_state: 'active', parent_goal_id: null },
    { id: 'g2', display_name: '子 Goal', status: 'active', activation_state: 'active', parent_goal_id: 'g1' },
  ]));
  assert.equal(status.goals.length, 2);
  assert.equal(status.goals[1]!.parentId, 'g1');
  const sorted = sortGoalTree(status.goals);
  assert.ok(sorted[0]!.goalId === 'g1' && sorted[1]!.goalId === 'g2', 'the parent sorts before its child');
});

test('run-history ghosts of a deleted Goal stay out of the workbench lists', () => {
  const status = parseLoopxStatus(payload([
    { id: 'gone', activation_state: 'active', status: 'legacy-runtime' },
    { id: 'kept', display_name: '还在', status: 'active', activation_state: 'active' },
  ]));
  assert.deepEqual(status.goals.map(goal => goal.goalId), ['kept']);
});

test('a missing goals array is refused, not guessed into empty', () => {
  assert.throws(() => parseLoopxStatus({ run_history: {}, attention_queue: { items: [] } }), /run_history.goals/);
});
