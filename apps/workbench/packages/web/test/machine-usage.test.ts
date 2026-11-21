import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  USAGE_KINDS, currentPerMinute, hitRate, hitRuns, offlineRuns, perMinute, percentText, stackLayers,
  todayTokens, totalTokens, type UsageKind,
} from '../src/machine-usage.js';
import type { MachineUsageReport, UsagePoint } from '../src/api.js';

const point = (t: number, over: Partial<UsagePoint> = {}): UsagePoint => ({
  t, input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0, gap: false, ...over,
});

const report = (points: UsagePoint[], step: MachineUsageReport['step'] = 60): MachineUsageReport => ({
  hours: 1, step, total: { input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0 },
  points, models: [], launchers: [],
});

test('totalTokens counts every kind, cache reads included', () => {
  assert.equal(totalTokens({ input: 100, cacheWrite: 20, cacheRead: 300, output: 50, replies: 1 }), 470);
});

test('hitRate is cache read over the prompt tokens, unknown without any', () => {
  assert.equal(hitRate({ input: 25, cacheWrite: 25, cacheRead: 50 }), 0.5);
  assert.equal(hitRate({ input: 0, cacheWrite: 0, cacheRead: 0 }), null);
  assert.equal(percentText(0.623), '62%');
  assert.equal(percentText(null), '—');
});

test('perMinute divides a step by its minutes', () => {
  assert.equal(perMinute(600, 60), 600);
  assert.equal(perMinute(600, 600), 60);
  assert.equal(perMinute(6000, 3600), 100);
});

test('stackLayers stacks the kinds bottom-up in the fixed order', () => {
  const layers = stackLayers([point(0, { input: 100, output: 50 }), point(60, { input: 200, output: 70 })], 60, USAGE_KINDS);
  assert.deepEqual(layers.map(l => l.kind), USAGE_KINDS);
  // in at the bottom of both steps; out sits on top of in.
  assert.deepEqual(layers[0].values.map(v => [v.y0, v.y1]), [[0, 100], [0, 200]]);
  assert.deepEqual(layers[1].values.map(v => [v.y0, v.y1]), [[100, 150], [200, 270]]);
  // The higher steps keep the minutes out of it: a 10-minute step is a rate.
  assert.deepEqual(stackLayers([point(0, { input: 100 })], 600, ['in'])[0].values[0], { t: 0, y0: 0, y1: 10 });
});

test('offlineRuns span each run of gaps from start to end', () => {
  const points = [point(0), point(60, { gap: true }), point(120, { gap: true }), point(180), point(240, { gap: true }), point(300)];
  assert.deepEqual(offlineRuns(points, 60), [[60, 180], [240, 300]]);
  // A run that ends the series ends with its last step.
  assert.deepEqual(offlineRuns([point(0), point(60, { gap: true })], 60), [[60, 120]]);
});

test('hitRuns break the line at gaps and under three replies', () => {
  const points = [
    point(0, { cacheRead: 90, input: 10, replies: 5 }),
    point(60, { cacheRead: 50, input: 50, replies: 4 }),
    point(120, { cacheRead: 50, input: 50, replies: 2 }), // too few replies
    point(180, { cacheRead: 50, input: 50, replies: 6, gap: true }), // offline
    point(240, { cacheRead: 80, input: 20, replies: 9 }),
  ];
  const runs = hitRuns(points);
  assert.equal(runs.length, 2);
  assert.deepEqual(runs[0], [{ t: 0, v: 0.9 }, { t: 60, v: 0.5 }]);
  assert.deepEqual(runs[1], [{ t: 240, v: 0.8 }]);
});

test('todayTokens sums the steps on this side of local midnight, by where a step mostly lies', () => {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const from = midnight.getTime() / 1000;
  const day = report([
    point(from - 3600, { input: 1000 }), // yesterday: the step's midpoint is before midnight
    point(from - 25, { input: 1000 }),    // reaches over midnight, but most of it is today
    point(from, { input: 400, output: 100, replies: 2 }),
    point(from + 60, { input: 600, cacheRead: 300, replies: 3 }),
  ], 60);
  const today = todayTokens(day);
  assert.deepEqual(today, { input: 2000, cacheWrite: 0, cacheRead: 300, output: 100, replies: 5 });
});

test('currentPerMinute averages the newest few live steps', () => {
  // The newest step is still filling in; the average of the last three is the number shown.
  const now = Math.floor(Date.now() / 1000);
  const hour = report([
    point(now - 300, { input: 6000 }),
    point(now - 240, { input: 6000 }),
    point(now - 180, { input: 6000 }),
    point(now - 120, { gap: true }), // a gap breaks nothing: only live steps are averaged
    point(now - 60, { input: 900 }),
  ], 60);
  assert.equal(currentPerMinute(hour), Math.round((6000 + 6000 + 900) / 3));
  assert.equal(currentPerMinute(report([point(now, { gap: true })])), 0);
});

test('USAGE_KINDS stay the one order the palette was validated in', () => {
  const kinds: UsageKind[] = ['in', 'out', 'cacheWrite', 'cacheRead'];
  assert.deepEqual(USAGE_KINDS, kinds);
});
