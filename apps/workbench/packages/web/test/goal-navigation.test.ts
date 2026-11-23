import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Thread } from '../src/api';
import { goalConversation, goalDestination, goalOverviewHash, goalOverviewRequest, hasGoalConversations, goalsBoardTarget, legacyGoalRoute } from '../src/loopx/goal-navigation';
import { extensionStorage } from '../src/extensions/host';
import { parseFilters } from '@calli/ext-board/filters';

test('legacy Goal list, details and all four old tabs resolve without decoding errors', () => {
  assert.deepEqual(legacyGoalRoute('#/goals'), { goalId: null });
  assert.deepEqual(legacyGoalRoute('#/goals/'), { goalId: null });
  const sections: Record<string, string | null> = { '': null, '/overview': 'overview', '/tasks': 'tasks', '/chat': null, '/files': 'files' };
  for (const [tab, section] of Object.entries(sections)) assert.deepEqual(legacyGoalRoute(`#/goals/goal%20one${tab}`), { goalId: 'goal one', section }, `the old ${tab || 'overview'} tab`);
  assert.deepEqual(legacyGoalRoute('#/goals/%bad'), { goalId: null });
  assert.equal(legacyGoalRoute('#/board'), null);
});

test('the Goal conversation shortcut opens the board with the persistent long-horizon filter and clears stale narrowing', () => {
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

test('bookmarks select a bound conversation, an unbound Core Goal reader, or the full inventory', () => {
  const bound = { id: 'bound', loopxInfo: { goalId: 'bound-goal', goalTitle: 'Bound Goal' } } as Thread;
  assert.deepEqual(goalDestination('bound-goal', [bound]), { view: 'thread', threadId: 'bound', goalId: 'bound-goal' });
  assert.deepEqual(goalDestination('core-only', [bound]), { view: 'steward', threadId: null, goalId: 'core-only' });
  assert.deepEqual(goalDestination(null, [bound]), { view: 'steward', threadId: null, goalId: null });
  const hash = goalOverviewHash('core goal/8');
  assert.equal(hash, '#/steward?goal=core%20goal%2F8');
  assert.deepEqual(goalOverviewRequest(hash), { goalId: 'core goal/8', section: null });
  assert.deepEqual(goalOverviewRequest('#/goals/core%20goal%2F8/tasks'), { goalId: 'core goal/8', section: 'tasks' });
  // An old files link keeps its section on the new address, and a reload reads it back.
  const files = goalOverviewRequest('#/goals/core%20goal%2F8/files')!;
  const moved = goalOverviewHash(files.goalId, files.section);
  assert.equal(moved, '#/steward?goal=core%20goal%2F8&section=files');
  assert.deepEqual(goalOverviewRequest(moved), { goalId: 'core goal/8', section: 'files' });
  assert.deepEqual(goalOverviewRequest('#/steward?goal=g&section=nonsense'), { goalId: 'g', section: null });
  assert.deepEqual(goalOverviewRequest('#/goals'), { goalId: null });
  assert.equal(goalOverviewHash(null), '#/steward?goals');
  assert.deepEqual(goalOverviewRequest(goalOverviewHash(null)), { goalId: null });
  assert.equal(goalOverviewRequest('#/steward'), null, 'ordinary steward entry preserves its original behavior');
  assert.equal(goalOverviewRequest('#/board'), null);
});

test('the board shortcut exists only when a Core Goal has a card in the shared projection', () => {
  const goals = [{ goalId: 'core-only' }, { goalId: 'bound-goal' }];
  const bound = { id: 'bound', agent: 'agent', cwd: '/workspace', title: 'Conversation', archivedAt: null, loopxInfo: { goalId: 'bound-goal', goalTitle: 'Bound Goal' } } as Thread;
  const plain = { ...bound, id: 'plain', loopxInfo: null };
  assert.equal(hasGoalConversations(goals, [plain]), false);
  assert.equal(hasGoalConversations(goals, [plain, bound]), true);
  assert.equal(hasGoalConversations(goals, [{ ...bound, archivedAt: 'archived' }]), false);
  assert.equal(hasGoalConversations(goals, [{ ...bound, loopxInfo: { ...bound.loopxInfo!, missing: true } }]), false);
  assert.equal(hasGoalConversations([{ goalId: 'core-only' }], [bound]), false);
  const member = { ...bound, coordination: { rootId: 'plain', children: 0, paused: false } } as Thread;
  assert.equal(hasGoalConversations(goals, [plain, member]), false, 'a hidden team member is not a board card');
});
