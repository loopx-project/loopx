import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bucketLabel, compactLabel, heatGrid, niceTicks, numberLabel, stepLabel, timeTicks, type DailyUsage, type UsageSeries } from '../src/observability.js';

test('compactLabel shortens token counts to three significant digits', () => {
  assert.deepEqual([0, 170, 999, 48318, 906470, 999950, 1028871, 12917704, 2.5e9].map(compactLabel),
    ['0', '170', '999', '48.3K', '906K', '1M', '1.03M', '12.9M', '2.5B']);
});

test('unknown counts stay unknown instead of zero', () => {
  assert.equal(compactLabel(null), '未知');
  assert.equal(compactLabel(undefined), '未知');
  assert.equal(numberLabel(1028871), '1,028,871');
});

test('niceTicks start at zero and end on a round step at or above the peak', () => {
  assert.deepEqual(niceTicks(1028871), [0, 500000, 1000000, 1500000]);
  assert.deepEqual(niceTicks(170), [0, 100, 200]);
  assert.deepEqual(niceTicks(1000), [0, 500, 1000]);
  assert.deepEqual(niceTicks(0), [0, 1]);
});

// Points built from local times, so the expectations hold in any timezone.
const series = (start: Date, stepMinutes: number, count: number): UsageSeries => ({
  stepMinutes, points: Array.from({ length: count }, (_, i) => ({ at: new Date(start.getTime() + i * stepMinutes * 60000).toISOString(), tpm: 0 })),
});

test('timeTicks land on round local times and thin out to fit', () => {
  const day = series(new Date(2026, 9, 14, 12, 0), 10, 145);
  const wide = timeTicks(day, 8);
  assert.equal(wide.every, 240);
  assert.deepEqual(wide.indexes.map(i => new Date(day.points[i].at).getHours()), [12, 16, 20, 0, 4, 8, 12]);
  assert.ok(wide.indexes.length <= 8);
  assert.equal(timeTicks(day, 4).every, 720);
  const week = series(new Date(2026, 9, 8, 12, 0), 60, 169);
  const daily = timeTicks(week, 8);
  assert.equal(daily.every, 1440);
  assert.ok(daily.indexes.every(i => new Date(week.points[i].at).getHours() === 0));
  // A tick is never finer than one point.
  assert.ok(timeTicks(series(new Date(2026, 9, 15, 11, 5), 1, 60), 100).every >= 1);
  assert.equal(timeTicks(day, 0).indexes.length, 0);
});

test('bucket and step labels name the span a point covers', () => {
  assert.equal(bucketLabel(new Date(2026, 9, 15, 14, 20).toISOString(), 10), '10/15 14:20–14:30');
  assert.equal(bucketLabel(new Date(2026, 9, 15, 23, 0).toISOString(), 60), '10/15 23:00–00:00');
  assert.equal(bucketLabel(new Date(2026, 9, 15, 9, 5).toISOString(), 1), '10/15 09:05');
  assert.deepEqual([1, 10, 60, 120].map(stepLabel), ['每分钟', '每 10 分钟平均', '每小时平均', '每 2 小时平均']);
});

test('chart keeps every instance selectable, distinguishes repeated colours and tolerates older daemons', async () => {
  const { chartLines } = await import('../src/observability.js');
  const agents = Array.from({ length: 6 }, (_, i) => ({ id: `a${i}`, label: `实例 ${i}` }));
  const data: UsageSeries = { stepMinutes: 1, points: [{ at: new Date().toISOString(), tpm: 600, outputTpm: 60 }],
    agents: agents.map(a => ({ agent: a.id, tpm: [100], outputTpm: [10] })) };
  const lines = chartLines(data, agents);
  assert.equal(lines.length, 7);
  assert.equal(lines[1].color, lines[5].color);
  assert.notEqual(lines[1].dash, lines[5].dash);
  assert.deepEqual(chartLines(data, agents, 'output').map(l => l.tpm), [[60], [10], [10], [10], [10], [10], [10]]);
  assert.equal(chartLines({ ...data, agents: [data.agents![4]] }, agents)[1].color, lines[5].color);
  assert.equal(chartLines({ ...data, agents: undefined }, agents).length, 1);
  assert.equal(chartLines({ ...data, agents: [{ agent: 'a0', tpm: [10] }] }, agents, 'output').length, 1);
});

test('the heatmap lays weeks out Monday first up to today, and keeps unknown days apart from empty ones', () => {
  // Wednesday 2026-10-21: the last column runs Monday to Wednesday.
  const today = new Date(2026, 9, 21, 15);
  const daily: DailyUsage = { checkedAt: '', since: '2026-10-06', days: [
    { date: '2026-10-06', total: 10, output: 1, turns: 1 },
    { date: '2026-10-13', total: 40, output: null, turns: 2 },
    { date: '2026-10-19', total: null, output: null, turns: 3 },
    { date: '2026-10-21', total: 1000, output: 5, turns: 4 },
  ] };
  const grid = heatGrid(daily, 'total', 3, today);
  assert.deepEqual(grid.columns.map(c => c.length), [7, 7, 3]);
  assert.equal(grid.columns[0][0].date, '2026-10-05');
  assert.equal(grid.columns[2][2].date, '2026-10-21');
  const at = (date: string) => grid.columns.flat().find(c => c.date === date)!;
  assert.equal(at('2026-10-05').level, null, 'before recording began the day is unknown');
  assert.equal(at('2026-10-05').recorded, false);
  assert.equal(at('2026-10-07').level, 0, 'a recorded day with no turns is empty');
  assert.equal(at('2026-10-19').level, null, 'turns that reported nothing stay unknown');
  assert.equal(at('2026-10-19').turns, 3);
  assert.equal(at('2026-10-21').level, 4);
  assert.ok(at('2026-10-06').level! < at('2026-10-21').level!);
  assert.equal(grid.total, 1050);
  assert.equal(grid.activeDays, 3);
  assert.equal(heatGrid(daily, 'output', 3, today).columns.flat().find(c => c.date === '2026-10-13')!.level, null, 'output follows its own reports');
  assert.equal(heatGrid({ ...daily, since: null }, 'total', 3, today).activeDays, 0);
  const year = heatGrid(daily, 'total', 53, today);
  assert.equal(year.columns.length, 53);
  assert.ok(year.months.length >= 11);
  assert.ok(year.months.every(m => m.date.getDate() === 1 && year.columns[m.column].some(c => c.date.endsWith('-01') && Number(c.date.slice(5, 7)) === m.date.getMonth() + 1)));
});
