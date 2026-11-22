import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ThreadSummary } from '@calli/extension-api';
import { emptyFilters, matchesFilters, parseFilters, FILTERS_VERSION } from '../src/columns';
import { goalLabel } from '../src/goal';

const thread = (goal?: ThreadSummary['goal']): ThreadSummary => ({ id: 'conversation', title: '[work] Proposal', agent: 'agent', project: '/workspace', scheduled: false, goal } as ThreadSummary);

test('Goal tags show the title and honest stored state, with no tag for ordinary conversations', () => {
  assert.equal(goalLabel(undefined, 'zh'), null);
  assert.equal(goalLabel({ goalId: 'g', title: 'Proposal', status: null }, 'en'), 'Proposal');
  assert.equal(goalLabel({ goalId: 'g', title: 'Proposal', status: 'active_state_user_gate' }, 'zh'), 'Proposal · 等你');
  assert.equal(goalLabel({ goalId: 'g', title: 'Proposal', status: 'stopped' }, 'ja'), 'Proposal · 一時停止');
  assert.equal(goalLabel({ goalId: 'g', title: 'Proposal', status: 'connected_without_run' }, 'en'), 'Proposal · Linked');
});

test('long-horizon filtering composes with existing filters and preserves ordinary default behavior', () => {
  const plain = thread(), bound = thread({ goalId: 'g', title: 'Proposal', status: null });
  assert.equal(matchesFilters(plain, emptyFilters()), true);
  assert.equal(matchesFilters(bound, emptyFilters()), true);
  const filters = { ...emptyFilters(), goalOnly: true };
  assert.equal(matchesFilters(plain, filters), false);
  assert.equal(matchesFilters(bound, filters), true);
  assert.equal(matchesFilters(bound, { ...filters, project: ['/other'] }), false);
  assert.equal(matchesFilters(bound, { ...filters, tags: ['work'], agent: ['agent'], scheduled: [false] }), true);
});

test('Goal filter shares persistence and accepts only an explicit boolean', () => {
  const filters = { ...emptyFilters(), goalOnly: true, agent: ['agent'] };
  assert.deepEqual(parseFilters(JSON.stringify({ ...filters, v: FILTERS_VERSION })), filters);
  assert.deepEqual(parseFilters('{"v":3,"agent":["agent"]}'), { ...emptyFilters(), agent: ['agent'] });
  for (const value of ['"true"', '1', '{}', 'null']) assert.equal(parseFilters(`{"goalOnly":${value}}`).goalOnly, false);
});
