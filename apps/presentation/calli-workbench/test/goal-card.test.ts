import { test } from 'node:test';
import assert from 'node:assert/strict';
import { goalCardActions, goalElapsedMs, goalIsActive, type GoalCardProps } from '../src/components/GoalCard.js';
import type { GoalView } from '../src/api.js';

const view = (over: Partial<GoalView>): GoalView => ({
  objective: '为我赚钱',
  status: 'active',
  startedAt: '2026-01-01T10:00:00Z',
  at: '2026-01-01T10:00:00Z',
  activeMs: 0,
  ...over,
});

test('elapsed time counts on while active and freezes when it is not', () => {
  const now = Date.parse('2026-01-01T10:02:30Z');
  assert.equal(goalElapsedMs(view({}), now), 150_000);
  assert.equal(goalElapsedMs(view({ activeMs: 5000, at: '2026-01-01T10:00:10Z' }), now), 145_000);
  assert.equal(goalElapsedMs(view({ status: 'paused', activeMs: 9000, at: '2026-01-01T10:00:09Z' }), now), 9000);
});

test('only an active goal is working', () => {
  assert.equal(goalIsActive('active'), true);
  assert.equal(goalIsActive('paused'), false);
  assert.equal(goalIsActive('complete'), false);
});

const codex: Pick<GoalCardProps, 'actions' | 'controls'> = { actions: ['set', 'show', 'pause', 'resume', 'clear'], controls: ['show', 'pause', 'clear'] };
const zcode: Pick<GoalCardProps, 'actions' | 'controls'> = { actions: ['set', 'show', 'pause', 'resume', 'clear'], controls: ['pause'] };

test('the card offers what the agent supports right now', () => {
  assert.deepEqual(goalCardActions(view({ status: 'active' }), false, codex.actions, codex.controls), ['pause', 'clear']);
  assert.deepEqual(goalCardActions(view({ status: 'paused' }), false, codex.actions, codex.controls), ['resume', 'clear']);
  assert.deepEqual(goalCardActions(view({ status: 'complete' }), false, codex.actions, codex.controls), ['clear']);
  assert.deepEqual(goalCardActions(view({ status: 'failed' }), false, codex.actions, codex.controls), ['clear']);
  // While a turn runs, only the agent's running controls may act: zcode can pause but not clear.
  assert.deepEqual(goalCardActions(view({ status: 'active' }), true, zcode.actions, zcode.controls), ['pause']);
  // Nothing supported, nothing offered; the clock and the details still stand.
  assert.deepEqual(goalCardActions(view({}), false), []);
});
