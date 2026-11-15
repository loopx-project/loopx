import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.js';
import { cleanSample } from '../src/telemetry.js';
import { CodexUsage, piUsage, zcodeUsage, acpUsage } from '../src/adapters/usage.js';
import type { EventType } from '../src/events.js';

test('usage normalization excludes cumulative history, duplicate updates, counter resets and fabricated reasoning', () => {
  const codex = new CodexUsage();
  assert.equal(codex.sample({ total: { totalTokens: 900000 }, last: { totalTokens: 150, inputTokens: 120, outputTokens: 30 } })?.total, 150);
  assert.equal(codex.sample({ total: { totalTokens: 900000 }, last: { totalTokens: 150 } }), null);
  assert.equal(codex.sample({ total: { totalTokens: 900200 }, last: { totalTokens: 200 } })?.total, 200);
  assert.equal(codex.sample({ total: { totalTokens: 1 }, last: { totalTokens: 1 } }), null);
  assert.equal(codex.sample({ total: { totalTokens: 11 } })?.total, 10);
  assert.deepEqual(zcodeUsage(null, { totalTokens: 900000 }), {});
  assert.deepEqual(zcodeUsage({ totalTokens: 900000 }, { totalTokens: 900150 }), { total: 150 });
  assert.deepEqual(zcodeUsage({ totalTokens: 900000 }, { totalTokens: 1 }), {});
  const sample = piUsage({ role: 'assistant', provider: 'test', model: 'chosen', responseModel: 'served', usage: { input: 100, output: 20, cacheRead: 40, cacheWrite: 10, totalTokens: 170, cost: { total: .01 } } }, 'p');
  assert.equal(sample?.input, 150);
  assert.equal(sample?.model, 'served');
  const acp = acpUsage({ usage: { totalTokens: 9 }, _meta: { quota: { model_usage: [{ model: 'actual', token_count: { inputTokens: 100, outputTokens: 20, cachedInputTokens: 40, cachedWriteTokens: 10, totalTokens: 170, reasoningOutputTokens: 0 } }] } } }, 'a');
  assert.equal(acp.length, 1);
  assert.equal(acp[0].total, 170);
  assert.equal(acp[0].input, 150);
  assert.equal(acp[0].reasoning, undefined);
  const costs = acpUsage({ usage: { totalTokens: 170 } }, 'cost', { amount: .4, currency: 'USD', estimated: true });
  assert.equal(costs.length, 2);
  assert.equal(costs[0].cost, undefined);
  assert.equal(costs[1].total, undefined);
  assert.equal(costs[1].model, undefined);
  assert.equal(cleanSample({ id: 'x', scope: 'request', input: -1, output: NaN })?.output, undefined);
});

test('ledger persists, deduplicates, rolls TPM over 60 seconds, filters instances and excludes imports/forks', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-telemetry-'));
  let store = new Store(dir);
  const now = Date.now();
  const create = (id: string, agent = 'pi') => store.createThread({ id, agent, cwd: dir, title: id, permissionMode: 'ask', model: 'chosen', effort: null });
  const emit = (threadId: string, turnId: string, type: EventType, data: Record<string, unknown>, ago = 0) => store.telemetry.observe({ threadId, turnId, type, data, seq: 1, ts: new Date(now - ago).toISOString() });
  try {
    create('a'); create('b', 'codex'); create('import'); create('fork');
    emit('a', 'one', 'turn.started', { linkedAccount: 'linked' }, 90000);
    emit('a', 'one', 'message.delta', { text: 'hello' }, 88000);
    emit('a', 'one', 'usage.sample', { id: 'old', scope: 'request', total: 100, input: 80, output: 20 }, 60000);
    emit('a', 'one', 'usage.sample', { id: 'new', scope: 'request', total: 30, cost: { amount: 1, currency: 'USD', estimated: true } }, 1000);
    emit('a', 'one', 'usage.sample', { id: 'new', scope: 'request', total: 30 }, 1000);
    emit('a', 'one', 'turn.done', {});
    emit('b', 'two', 'turn.started', {}, 2000);
    emit('b', 'two', 'usage.sample', { id: 'c', scope: 'update', total: 50, cost: { amount: 2, currency: 'CNY' } }, 1000);
    emit('b', 'two', 'turn.error', { message: 'upstream failed' });
    emit('import', 'old', 'turn.started', { imported: true });
    emit('import', 'old', 'usage.sample', { id: 'ignored', scope: 'request', total: 900000 });
    store.appendEvent('a', 'copied', 'turn.started', { text: 'copy' });
    store.appendEvent('a', 'copied', 'usage.sample', { id: 'p', scope: 'request', total: 5 });
    store.copyEvents('a', 'fork');
    const report = store.telemetry.read(24, '', now + 1000);
    assert.equal(report.totals.total, 185);
    assert.equal(report.tpm, 85);
    assert.equal(report.errors, 1);
    assert.equal(report.turns, 3);
    assert.equal(report.costs.USD.amount, 1);
    assert.equal(report.costs.CNY.amount, 2);
    assert.equal(report.rows.some(r => r.threadId === 'fork' || r.threadId === 'import'), false);
    assert.ok(Math.abs(report.rows.find(r => r.turnId === 'one')!.firstOutputMs! - 2000) < 1);
    assert.equal(store.telemetry.read(24, 'codex', now + 1000).tpm, 50);
    assert.equal(store.telemetry.read(24, '', now + 61000).tpm, 0);
    store.close(); store = new Store(dir);
    assert.equal(store.telemetry.read(24, '', now + 100000).totals.total, 185);
    store.deleteThread('b');
    assert.equal(store.telemetry.read(24, '', now + 100000).totals.total, 135);
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('TPM series averages clock-aligned buckets and shortens the range edges', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-telemetry-'));
  const store = new Store(dir);
  const now = Date.UTC(2026, 9, 15, 12, 5);
  const emit = (type: EventType, data: Record<string, unknown>, at: number, threadId = 'a') => store.telemetry.observe({ threadId, turnId: 'one', type, data, seq: 1, ts: new Date(at).toISOString() });
  try {
    store.createThread({ id: 'a', agent: 'pi', cwd: dir, title: 'a', permissionMode: 'ask', model: null, effort: null });
    store.createThread({ id: 'b', agent: 'codex', cwd: dir, title: 'b', permissionMode: 'ask', model: null, effort: null });
    store.createThread({ id: 'idle', agent: 'zcode', cwd: dir, title: 'idle', permissionMode: 'ask', model: null, effort: null });
    emit('turn.started', {}, now - 86000000);
    emit('turn.started', {}, now - 86000000, 'idle');
    emit('usage.sample', { id: 'edge', scope: 'request', total: 300 }, Date.UTC(2026, 9, 14, 12, 6));
    emit('usage.sample', { id: 'full', scope: 'request', total: 600 }, Date.UTC(2026, 9, 15, 11, 52));
    emit('usage.sample', { id: 'full-2', scope: 'turn', total: 400 }, Date.UTC(2026, 9, 15, 11, 58));
    emit('usage.sample', { id: 'cost', scope: 'update', cost: { amount: 1, currency: 'USD', estimated: false } }, Date.UTC(2026, 9, 15, 11, 59));
    emit('usage.sample', { id: 'live', scope: 'request', total: 500 }, Date.UTC(2026, 9, 15, 12, 4));
    emit('turn.started', {}, Date.UTC(2026, 9, 15, 11, 50), 'b');
    emit('usage.sample', { id: 'other', scope: 'request', total: 1000 }, Date.UTC(2026, 9, 15, 11, 51), 'b');
    const all = store.telemetry.read(24, '', now).series;
    // Instances get their own lines; one that reported nothing gets none.
    assert.deepEqual(all.agents.map(a => a.agent).sort(), ['codex', 'pi']);
    const codex = all.agents.find(a => a.agent === 'codex')!.tpm;
    assert.equal(codex.length, 145);
    assert.deepEqual([codex.at(-2), all.points.at(-2)!.tpm], [100, 200]);
    const day = store.telemetry.read(24, 'pi', now).series;
    assert.deepEqual(day.agents.map(a => a.agent), ['pi']);
    assert.equal(day.stepMinutes, 10);
    assert.equal(day.points.length, 145);
    assert.equal(day.points[0].at, '2026-10-14T12:00:00.000Z');
    // 300 tokens over the 5 minutes of the first bucket that fall inside the range.
    assert.equal(day.points[0].tpm, 60);
    assert.deepEqual(day.points.slice(-2).map(({ at, tpm }) => ({ at, tpm })), [{ at: '2026-10-15T11:50:00.000Z', tpm: 100 }, { at: '2026-10-15T12:00:00.000Z', tpm: 100 }]);
    assert.equal(day.points.reduce((sum, p) => sum + p.tpm, 0), 260);
    const hour = store.telemetry.read(1, 'pi', now).series;
    assert.equal(hour.stepMinutes, 1);
    assert.equal(hour.points.length, 60);
    assert.deepEqual(hour.points.filter(p => p.tpm).map(p => [p.at.slice(11, 16), p.tpm]), [['11:52', 600], ['11:58', 400], ['12:04', 500]]);
    const week = store.telemetry.read(168, '', now).series;
    assert.equal(week.stepMinutes, 60);
    assert.equal(week.points.length, 169);
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('estimated timing spreads measured tokens across buckets and the rolling minute without changing totals', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-rate-'));
  const store = new Store(dir);
  const now = Date.UTC(2026, 9, 15, 12, 5);
  try {
    store.createThread({ id: 'a', agent: 'claude', cwd: dir, title: 'a', permissionMode: 'ask', model: null, effort: null });
    const emit = (type: EventType, data: Record<string, unknown>, at: number) => store.telemetry.observe({ threadId: 'a', turnId: 't', type, data, seq: 1, ts: new Date(at).toISOString() });
    emit('turn.started', {}, now - 120000);
    emit('usage.sample', { id: 'a', scope: 'turn', total: 600, output: 120, timing: 'estimated', startedAt: new Date(now - 120000).toISOString(), endedAt: new Date(now).toISOString() }, now);
    const report = store.telemetry.read(1, '', now);
    assert.equal(report.totals.total, 600);
    assert.equal(report.tpm, 300); assert.equal(report.outputTpm, 60);
    assert.deepEqual(report.series.points.slice(-2).map(p => [p.tpm, p.outputTpm]), [[300, 60], [300, 60]]);
    assert.equal(report.series.estimated, true);
    assert.equal(store.telemetry.read(1, '', now + 30000).tpm, 150);
    assert.equal(store.telemetry.read(1, '', now + 60000).tpm, 0);
    assert.equal(report.rows[0].samples.length, 1);
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('daily token activity groups by local day, keeps unmeasured days unknown and filters instances', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-telemetry-daily-'));
  const store = new Store(dir);
  const now = Date.now();
  const localDay = (ms: number) => { const d = new Date(ms); return new Date(ms - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const create = (id: string, agent: string) => store.createThread({ id, agent, cwd: dir, title: id, permissionMode: 'ask', model: null, effort: null });
  const emit = (threadId: string, turnId: string, type: EventType, data: Record<string, unknown>, at: number) => store.telemetry.observe({ threadId, turnId, type, data, seq: 1, ts: new Date(at).toISOString() });
  try {
    create('a', 'pi'); create('b', 'codex');
    const today = now - 1000, before = now - 3 * 86400000, old = now - 400 * 86400000;
    emit('a', 'old', 'turn.started', {}, old);
    emit('a', 'old', 'usage.sample', { id: 'o', scope: 'request', total: 999 }, old);
    emit('a', 'one', 'turn.started', {}, before);
    emit('a', 'one', 'usage.sample', { id: 'x', scope: 'request', total: 100, output: 20 }, before);
    emit('a', 'one', 'usage.sample', { id: 'y', scope: 'request', total: 50 }, before);
    emit('b', 'two', 'turn.started', {}, today);
    emit('b', 'two', 'usage.sample', { id: 'z', scope: 'request', total: 7 }, today);
    emit('a', 'three', 'turn.started', {}, today);
    const report = store.telemetry.daily('', undefined, now);
    assert.equal(report.since, localDay(old), 'recording began with the first turn, even outside the year');
    assert.deepEqual(report.days, [
      { date: localDay(before), total: 150, output: 20, turns: 1 },
      { date: localDay(today), total: 7, output: null, turns: 2 },
    ]);
    const pi = store.telemetry.daily('pi', undefined, now);
    assert.deepEqual(pi.days.find(d => d.date === localDay(today)), { date: localDay(today), total: null, output: null, turns: 1 }, 'a turn without a report is unknown, not zero');
    assert.equal(store.telemetry.daily('nobody', undefined, now).since, null);
    // The viewer's zone names the day: 20:00 UTC is already tomorrow in Shanghai, still today in New York.
    create('c', 'zone');
    const evening = Date.UTC(2026, 9, 20, 20), at = evening + 86400000 * 2;
    emit('c', 'late', 'turn.started', {}, evening);
    emit('c', 'late', 'usage.sample', { id: 'l', scope: 'request', total: 9 }, evening);
    assert.deepEqual(store.telemetry.daily('zone', 'Asia/Shanghai', at).days.map(d => d.date), ['2026-10-21']);
    assert.deepEqual(store.telemetry.daily('zone', 'America/New_York', at).days.map(d => d.date), ['2026-10-20']);
    assert.equal(store.telemetry.daily('zone', 'Asia/Kolkata', at).since, '2026-10-21', 'half-hour zones land on the right side of midnight');
    assert.throws(() => store.telemetry.daily('zone', 'Mars/Olympus', at), RangeError);
  } finally {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
