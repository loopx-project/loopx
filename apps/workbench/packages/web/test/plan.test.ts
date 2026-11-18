import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { PersonalPlan } from '../src/api.js';
import { planIsEmpty, planWhenOptions, readPlanRange } from '../src/plan.js';

const plan = (over: Partial<PersonalPlan> = {}): PersonalPlan => ({
  range: 'day', from: '2026-11-13T00:00:00.000Z', to: '2026-11-14T00:00:00.000Z',
  meetings: { state: 'ok', items: [] }, urgent: [], due: [], goals: [], needsYou: [], running: [], ...over,
});

test('the picked range survives only when it is one of the four', () => {
  assert.equal(readPlanRange('week'), 'week');
  assert.equal(readPlanRange('year'), 'day');
  assert.equal(readPlanRange(null), 'day');
});

test('a range is empty only when the calendar was read and everything is empty', () => {
  assert.equal(planIsEmpty(plan()), true);
  assert.equal(planIsEmpty(plan({ meetings: { state: 'off', items: [] } })), false, 'no calendar is not "no meetings"');
  assert.equal(planIsEmpty(plan({ meetings: { state: 'unavailable', error: 'timeout', items: [] } })), false);
  assert.equal(planIsEmpty(plan({ running: [{ id: 't', title: 'x', line: '', at: '' }] })), true, 'running sessions are the strip\'s, not the plan\'s');
  assert.equal(planIsEmpty(plan({ goals: [{ id: 'g', text: 'ship', dueAt: null, importance: null, horizon: 'week', parentId: null, stage: null }] })), false);
});

test('a week names the day, a day only the clock', () => {
  assert.equal(planWhenOptions('day').weekday, undefined);
  assert.equal(planWhenOptions('week').weekday, 'short');
});
