import { test } from 'node:test';
import assert from 'node:assert/strict';
import { takeLinkToken } from '../src/link-auth.js';
import { sidebarGoals } from '../src/loopx/goal-picks.js';
import type { LoopxAttention, LoopxGoal } from '../src/loopx/status.js';

test('takeLinkToken reads ?token= and scrubs it from the rest', () => {
  const found = takeLinkToken('http://x:8796/?token=abc');
  assert.deepEqual(found, { token: 'abc', rest: '/' });
});

test('takeLinkToken reads the fragment (never sent to a server): bare, and the hash route query', () => {
  assert.deepEqual(takeLinkToken('http://x:8796/#token=abc'), { token: 'abc', rest: '/' });
  const routed = takeLinkToken('http://x:8796/#/goals?token=def');
  assert.deepEqual(routed, { token: 'def', rest: '/#/goals' });
  const kept = takeLinkToken('http://x:8796/#/goals?x=1&token=def');
  assert.deepEqual(kept, { token: 'def', rest: '/#/goals?x=1' });
});

test('a query token wins over a fragment one; an empty token is dropped, not kept', () => {
  assert.equal(takeLinkToken('http://x/?token=a#token=b')?.token, 'a');
  // An empty token param still counts as "a token was here" (the link gets scrubbed) — the
  // capture step is what refuses to keep it.
  assert.equal(takeLinkToken('http://x/?token=')?.token, '');
  assert.equal(takeLinkToken('http://x/#/goals'), null);
});

const goal = (id: string, over: Partial<LoopxGoal> = {}): LoopxGoal => ({
  goalId: id, parentId: null, title: id, objective: null, status: 'active', activationState: 'active',
  lifecyclePhase: null, updatedAt: null, agents: [], ...over,
});

const attends = (goalId: string, over: Partial<LoopxAttention> = {}): LoopxAttention => ({
  goalId, status: null, waitingOn: null, severity: null, recommendedAction: null,
  nextAction: null, topTodo: null, lastActivityAt: null,
  userTodos: { open: 0, done: 0, total: 0 }, agentTodos: { open: 0, done: 0, total: 0 }, ...over,
});

test('the sidebar picks five at most: pinned first, then needs-you, then recent', () => {
  const goals = [goal('a'), goal('b'), goal('c'), goal('d', { updatedAt: '2026-11-10T00:00:00Z' }), goal('e', { updatedAt: '2026-11-15T00:00:00Z' }), goal('f', { parentId: 'a' }), goal('g', { activationState: 'stopped' })];
  const attention = [attends('c', { userTodos: { open: 2, done: 0, total: 2 } })];
  // No pins: the needs-you one first, then by recency (a stamp beats none); children and stopped never take a slot.
  assert.deepEqual(sidebarGoals(goals, attention, []).map(g => g.goalId), ['c', 'e', 'd', 'a', 'b']);
  // A pin always holds its slot, in pin order, ahead of the rest.
  assert.deepEqual(sidebarGoals(goals, attention, ['b', 'a']).map(g => g.goalId), ['b', 'a', 'c', 'e', 'd']);
  // A pinned goal that stops or disappears loses its slot rather than holding it empty.
  assert.deepEqual(sidebarGoals(goals, attention, ['g', 'gone']).map(g => g.goalId), ['c', 'e', 'd', 'a', 'b']);
});

test('recency falls back through todo activity when the goal itself has no stamp', () => {
  const goals = [goal('quiet'), goal('busy')];
  const attention = [attends('busy', { lastActivityAt: '2026-11-16T00:00:00Z' })];
  assert.deepEqual(sidebarGoals(goals, attention, []).map(g => g.goalId), ['busy', 'quiet']);
});
