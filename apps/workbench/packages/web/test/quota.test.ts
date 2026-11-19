import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { PlanQuota, QuotaWindow } from '../src/api.js';
import { balanceEmpty, formatReset, periodName, quotaColumns, quotaGroups, quotaLeft, quotaLevel, quotaRows, quotaSlots, shortReset } from '../src/quota.js';

const win = (over: Partial<QuotaWindow> = {}): QuotaWindow => ({ label: '5H', usedPercent: 0, ...over });

test('legacy account-wide weekly readings pair with short readings without merging model-specific pools', () => {
  const short = win(), week = win({ label: '7D', scope: '全部模型', usedPercent: 95 });
  const opus = win({ label: '7D', scope: 'Opus', usedPercent: 71 });
  const groups = quotaGroups([week, opus, short]);
  assert.deepEqual(groups, [{ scope: undefined, windows: [week, short] }, { scope: 'Opus', windows: [opus] }]);
  assert.deepEqual(quotaSlots(groups[0].windows), [{ label: '5H', window: short }, { label: '7D', window: week }],
    'both original readings are visible without a false missing period');
  assert.deepEqual([quotaLeft(short), quotaLeft(week), quotaLeft(opus)], [100, 5, 29]);
});

test('quota previews retain both periods and distinguish missing windows from zero remaining', () => {
  const short = win({ usedPercent: 100 }), week = win({ label: '7D', usedPercent: 0 });
  assert.deepEqual(quotaSlots([week, short]), [{ label: '5H', window: short }, { label: '7D', window: week }]);
  assert.deepEqual(quotaSlots([week]), [{ label: '5H' }, { label: '7D', window: week }]);
  assert.equal(quotaLeft(quotaSlots([short])[0].window!), 0);
  assert.deepEqual(quotaSlots([]), []);
  const other = win({ label: '1H' });
  assert.deepEqual(quotaSlots([other]), [{ label: '1H', window: other }], 'unrelated periods retain their actual duration');
  assert.deepEqual(quotaSlots([other, week, short]).map(slot => slot.label), ['5H', '7D', '1H'], 'additional periods never displace the short and weekly pair');
  const pools = quotaGroups([week, { ...week, scope: 'Review', usedPercent: 100 }]);
  assert.deepEqual(pools.map(pool => quotaSlots(pool.windows)[1].window?.usedPercent), [0, 100], 'model pools are never averaged or substituted');
});

test('quotaLevel splits at half and at nearly full', () => {
  assert.deepEqual([0, 49, 50, 79, 80, 100].map(quotaLevel), ['ok', 'ok', 'warn', 'warn', 'danger', 'danger']);
});

test('formatReset is short today and names the day later', () => {
  const now = new Date('2026-10-10T09:00:00');
  const today = win({ resetsAt: '2026-10-10T14:40:00Z' });
  const later = win({ resetsAt: '2026-10-12T23:00:00Z' });
  // The test host's timezone decides the exact wording, but it always stays short.
  assert.match(formatReset(today, now), /^(\d{2}:\d{2}|\d+月\d+日 \d{2}:\d{2}) 重置$/);
  assert.match(formatReset(later, now), /^\d+月\d+日 \d{2}:\d{2} 重置$/);
  assert.equal(formatReset(win(), now), '');
  assert.equal(formatReset(win({ resetsAt: 'not a date' }), now), '');
});

test('the board shows what is left, columns by window length, and groups by model', () => {
  assert.deepEqual([0, 53, 100, 120, 33.33].map((usedPercent) => quotaLeft(win({ usedPercent }))), [100, 47, 0, 0, 66.7]);
  const quota = (labels: string[]): PlanQuota => ({ windows: labels.map((label) => win({ label })), checkedAt: '2026-10-10T00:00:00Z' });
  assert.deepEqual(quotaColumns([quota(['7D', '5H']), null, quota(['5H', 'Pro', '1D'])]), ['5H', '1D', '7D', 'Pro']);
  assert.deepEqual(quotaColumns([]), []);
  assert.deepEqual(['5H', '1H', '7D', '3D', 'Pro'].map(periodName), ['5 小时', '1 小时', '每周', '3 天', 'Pro']);
  const groups = quotaGroups([win({ label: '7D', scope: 'Gemini' }), win({ label: '5H' }), win({ label: '5H', scope: 'Gemini' }), win({ label: '7D', scope: 'Claude' })]);
  assert.deepEqual(groups.map((g) => [g.scope, g.windows.map((w) => w.label)]), [[undefined, ['5H']], ['Gemini', ['7D', '5H']], ['Claude', ['7D']]]);
});

test('shortReset fits a table cell: the time today, the date later', () => {
  const now = new Date(2026, 9, 10, 9, 0);
  assert.equal(shortReset(win({ resetsAt: new Date(2026, 9, 10, 14, 5).toISOString() }), now), '14:05');
  assert.equal(shortReset(win({ resetsAt: new Date(2026, 9, 12, 7, 53).toISOString() }), now), '10/12');
  assert.equal(shortReset(win(), now), '');
  assert.equal(shortReset(win({ resetsAt: 'not a date' }), now), '');
});

test('quotaRows keeps agents that report quota and puts the one in use first', () => {
  const agents = [['claude', true], ['codex', true], ['plain', false], ['traex', true]].map(([id, quota]) => ({ id: id as string, capabilities: { quota: quota as boolean } }));
  const ids = (current?: string) => quotaRows(agents, current).map((a) => a.id);
  assert.deepEqual(ids(), ['claude', 'codex', 'traex']);
  assert.deepEqual(ids('traex'), ['traex', 'claude', 'codex']);
  assert.deepEqual(ids('plain'), ['claude', 'codex', 'traex']);
  const hidden = agents.map(agent => ({ ...agent, hidden: agent.id === 'claude' }));
  assert.deepEqual(quotaRows(hidden, 'claude').map(agent => agent.id), ['codex', 'traex'], 'a hidden current agent is not brought back to the board');
  assert.deepEqual(quotaRows(hidden.map(agent => ({ ...agent, hidden: true }))), []);
  assert.deepEqual(quotaRows(hidden.map(agent => ({ ...agent, hidden: false })), 'claude').map(agent => agent.id), ['claude', 'codex', 'traex']);
});

test('a balance runs out only when its number is zero or below; an unreadable amount is not empty', () => {
  const balance = (amount: string) => balanceEmpty({ scope: 'DeepSeek', amount });
  assert.deepEqual(['¥60.52', '$0.00', '¥-1.20', '1,024 credits', 'unknown'].map(balance), [false, true, true, false, false]);
});
