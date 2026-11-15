import { test } from 'node:test';
import assert from 'node:assert/strict';
import { countCronFires, cronMatches, describeCron, nextCron, parseCron } from '../src/cron.js';

// Dates are built from local parts, the way the scheduler reads the clock.

test('cron: parses fields, names, steps, lists and ranges', () => {
  const f = parseCron('*/15 9-11 1,15 MAR-APR mon-fri');
  assert.deepEqual(f.minutes, [0, 15, 30, 45]);
  assert.deepEqual(f.hours, [9, 10, 11]);
  assert.deepEqual(f.daysOfMonth, [1, 15]);
  assert.deepEqual(f.months, [3, 4]);
  assert.deepEqual(f.daysOfWeek, [1, 2, 3, 4, 5]);
  assert.equal(f.domAny, false);
  assert.equal(f.dowAny, false);
});

test('cron: an asterisk day field is what makes a day unrestricted', () => {
  const f = parseCron('*/15 9-11 * * mon-fri');
  assert.equal(f.domAny, true);
  assert.equal(f.dowAny, false);
});

test('cron: both 0 and 7 are Sunday', () => {
  assert.deepEqual(parseCron('0 0 * * 7').daysOfWeek, [0]);
  assert.deepEqual(parseCron('0 0 * * SUN').daysOfWeek, [0]);
});

test('cron: refuses malformed expressions with something to say', () => {
  for (const bad of ['', '* * * *', '60 * * * *', '* 24 * * *', '*/0 * * * *', 'a * * * *', '1-2-3 * * * *', '* * 0 * *', '* * * 13 *']) {
    assert.throws(() => parseCron(bad), undefined, bad);
  }
});

test('cron: matches by local time', () => {
  const daily = parseCron('30 9 * * *');
  assert.equal(cronMatches(daily, new Date(2026, 9, 13, 9, 30, 12)), true);
  assert.equal(cronMatches(daily, new Date(2026, 9, 13, 9, 31)), false);
  const weekday = parseCron('0 9 * * mon-fri');
  // Oct 13 2026 is a Tuesday; Oct 11 is a Sunday.
  assert.equal(cronMatches(weekday, new Date(2026, 9, 13, 9, 0)), true);
  assert.equal(cronMatches(weekday, new Date(2026, 9, 11, 9, 0)), false);
});

test('cron: a day restricted in both fields matches on either', () => {
  const either = parseCron('0 0 13 * fri'); // the 13th, or any Friday
  assert.equal(cronMatches(either, new Date(2026, 9, 13, 0, 0)), true); // a Tuesday, but the 13th
  assert.equal(cronMatches(either, new Date(2026, 9, 16, 0, 0)), true); // a Friday
  assert.equal(cronMatches(either, new Date(2026, 9, 14, 0, 0)), false); // neither
});

test('cron: finds the next fire, across hour and day bounds', () => {
  const daily = parseCron('0 9 * * *');
  const from = new Date(2026, 9, 13, 10, 0); // after today's run
  assert.deepEqual(nextCron(daily, from), new Date(2026, 9, 14, 9, 0));
  const quarter = parseCron('*/15 * * * *');
  assert.deepEqual(nextCron(quarter, new Date(2026, 9, 13, 9, 3)), new Date(2026, 9, 13, 9, 15));
});

test('cron: a date that never comes has no next fire', () => {
  assert.equal(nextCron(parseCron('0 0 30 2 *'), new Date(2026, 0, 1)), null);
});

test('cron: counts the fires in a window, for what downtime missed', () => {
  const hourly = parseCron('0 * * * *');
  // 11:00 through 13:00 are between 10:30 and 14:00.
  assert.equal(countCronFires(hourly, new Date(2026, 9, 13, 10, 30), new Date(2026, 9, 13, 14, 0)), 3);
  assert.equal(countCronFires(hourly, new Date(2026, 9, 13, 10, 30), new Date(2026, 9, 13, 11, 0)), 0);
  const never = parseCron('0 0 30 2 *');
  assert.equal(countCronFires(never, new Date(2026, 0, 1), new Date(2026, 11, 31)), 0);
});

test('cron: describes the common shapes in words', () => {
  assert.equal(describeCron(parseCron('* * * * *')), '每分钟');
  assert.equal(describeCron(parseCron('*/30 * * * *')), '每 30 分钟');
  assert.equal(describeCron(parseCron('15 * * * *')), '每小时 15 分');
  assert.equal(describeCron(parseCron('30 9 * * *')), '每天 09:30');
  assert.equal(describeCron(parseCron('0 9,18 * * *')), '每天 09:00、18:00');
  assert.equal(describeCron(parseCron('0 9 * * mon-fri')), '每工作日 09:00');
  assert.equal(describeCron(parseCron('0 9 * * mon')), '每周一 09:00');
  assert.equal(describeCron(parseCron('0 9 * * sun,sat')), '每周日、周六 09:00');
  assert.equal(describeCron(parseCron('0 9 1 * *')), '每月 1 日 09:00');
  // Uncommon shapes are left to the caller's raw expression.
  assert.equal(describeCron(parseCron('5,35 9 1,15 * *')), '');
});

test('cron: matches by specified timezone (Asia/Shanghai vs UTC differ across day boundary)', () => {
  // 2026-10-13 18:30:00 UTC is 2026-10-14 02:30:00 in Asia/Shanghai (+8h).
  const instant = new Date('2026-10-13T18:30:00.000Z');

  // Shanghai: 14th day, 02:30
  const shanghaiCron = parseCron('30 2 14 10 *');
  assert.equal(cronMatches(shanghaiCron, instant, 'Asia/Shanghai'), true);
  assert.equal(cronMatches(shanghaiCron, instant, 'UTC'), false);

  // UTC: 13th day, 18:30
  const utcCron = parseCron('30 18 13 10 *');
  assert.equal(cronMatches(utcCron, instant, 'UTC'), true);
  assert.equal(cronMatches(utcCron, instant, 'Asia/Shanghai'), false);
});

test('cron: finds next fire in specified timezone', () => {
  const dailyShanghai = parseCron('0 9 * * *');
  // At 2026-10-13 00:30 UTC = 2026-10-13 08:30 in Shanghai. Next 09:00 in Shanghai is in 30 minutes!
  const from = new Date('2026-10-13T00:30:00.000Z');
  const next = nextCron(dailyShanghai, from, 'Asia/Shanghai');
  assert.ok(next);
  assert.equal(next.toISOString(), '2026-10-13T01:00:00.000Z'); // 09:00 CST is 01:00 UTC
});

