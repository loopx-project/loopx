import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Thread } from '../src/api';
import { goalConversation, goalsBoardTarget, legacyGoalRoute } from '../src/loopx/goal-navigation';
import { extensionStorage } from '../src/extensions/host';
import { parseFilters } from '@calli/ext-board/filters';

test('legacy Goal list, details and all four old tabs resolve without decoding errors', () => {
  assert.deepEqual(legacyGoalRoute('#/goals'), { goalId: null });
  assert.deepEqual(legacyGoalRoute('#/goals/'), { goalId: null });
  for (const tab of ['', '/overview', '/tasks', '/chat', '/files']) assert.deepEqual(legacyGoalRoute(`#/goals/goal%20one${tab}`), { goalId: 'goal one' });
  assert.deepEqual(legacyGoalRoute('#/goals/%bad'), { goalId: null });
  assert.equal(legacyGoalRoute('#/board'), null);
});

test('All Goals opens the board with the persistent long-horizon filter and clears stale narrowing', () => {
  const values = new Map<string, string>([['calli.ext.board.filters.v1', '{"v":3,"project":["/old"]}']]);
  const storage = extensionStorage('board', { getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: key => { values.delete(key); } });
  assert.equal(goalsBoardTarget(storage), '#/board');
  assert.deepEqual(parseFilters(values.get('calli.ext.board.filters.v1')!), { goalOnly: true, tags: [], project: [], agent: [], scheduled: [], machine: [] });
  assert.equal(goalsBoardTarget(extensionStorage('board', null)), '#/board', 'blocked device storage never breaks navigation');
});

test('Goal navigation reuses a live conversation and never chooses deleted or archived bindings', () => {
  const bound = (id: string, patch: Partial<Thread> = {}) => ({ id, loopxInfo: { goalId: 'g', goalTitle: 'Proposal' }, ...patch } as Thread);
  const live = bound('live');
  assert.equal(goalConversation('g', [bound('gone', { loopxInfo: { goalId: 'g', goalTitle: 'Proposal', missing: true } as Thread['loopxInfo'] }), bound('archived', { archivedAt: 'date' }), live]), live);
  assert.equal(goalConversation('missing', [live]), null);
});
