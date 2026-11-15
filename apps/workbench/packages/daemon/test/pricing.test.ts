import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { estimateCost, summarizeCosts, type CostEstimate } from '../src/pricing.js';
import { Store } from '../src/store.js';
import type { UsageSample } from '../src/telemetry.js';

const at = '2026-10-20T12:00:00.000Z';
const now = Date.parse(at);
const sample = (patch: Partial<UsageSample> = {}): UsageSample => ({
  id: 's', scope: 'request', source: 'native', model: 'glm-5.3', input: 1000000, output: 100000,
  total: 1100000, cacheRead: 900000, cacheWrite: 0, ...patch,
});
const estimate = (value: UsageSample, model: string | null = null, date = at) => {
  const result = estimateCost(value, model, date);
  assert.notEqual(typeof result, 'string', JSON.stringify(result));
  return result as CostEstimate;
};
const close = (actual: number, expected: number) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ≠ ${expected}`);

test('official token prices separate cached input and never double-count reasoning', () => {
  const value = estimate(sample({ model: 'GLM-5.3', reasoning: 90000 }));
  close(value.amount, .1 * 1.4 + .9 * .26 + .1 * 4.4);
  close(value.maxAmount, value.amount);
  assert.match(value.price.source, /^https:\/\/docs\.z\.ai\//);
  assert.deepEqual(value.assumptions, []);
  assert.equal(estimateCost(sample({ model: 'openrouter-4o' }), 'glm-5.3', at), 'unknown_price');
  assert.equal(estimateCost(sample({ model: undefined }), null, at), 'missing_model');
  assert.equal(estimateCost(sample({ input: undefined }), null, at), 'missing_tokens');
  assert.equal(estimateCost(sample({ cacheRead: 1100000 }), null, at), 'invalid_usage');
  assert.equal(estimateCost(sample({ total: 1 }), null, at), 'invalid_usage');
  close(estimate(sample({ model: 'claude-opus-5-5' })).amount, .1 * 4 + .9 * .2 + .1 * 20);
  close(estimate(sample({ model: 'claude-haiku-5-5' })).amount, .1 * .5 + .9 * .05 + .1 * 2.5);
  assert.equal(estimateCost(sample({ input: Number.MAX_VALUE, output: 0, total: undefined, model: 'gpt-6-astra' }), null, at), 'invalid_usage');
});

test('unknown cache partitions, TTL and merged request lengths produce bounds rather than fabricated zeros', () => {
  const base = sample({ model: 'gpt-6-astra', input: 100000, output: 10000, total: 110000, cacheRead: 90000, cacheWrite: undefined });
  const value = estimate(base);
  close(value.amount, .01 * 10 + .09 * 1 + .01 * 50);
  close(value.maxAmount, .01 * 12.5 + .09 * 1 + .01 * 50);
  assert.deepEqual(value.assumptions, ['cache_split']);
  const missingBoth = estimate({ ...base, cacheRead: undefined });
  close(missingBoth.amount, .1 * 1 + .01 * 50);
  close(missingBoth.maxAmount, .1 * 12.5 + .01 * 50);
  const long = sample({ model: 'gpt-6-astra' });
  close(estimate(long).amount, .1 * 20 + .9 * 2 + .1 * 75);
  const merged = estimate({ ...long, scope: 'update' });
  close(merged.amount, .1 * 10 + .9 * 1 + .1 * 50);
  close(merged.maxAmount, estimate(long).amount);
  assert.deepEqual(merged.assumptions, ['context_tier']);
  const ttl = estimate(sample({ model: 'claude-opus-4-6', cacheWrite: 100000 }));
  close(ttl.amount, .9 * .5 + .1 * 6.25 + .1 * 25);
  close(ttl.maxAmount, .9 * .5 + .1 * 10 + .1 * 25);
  assert.deepEqual(ttl.assumptions, ['cache_ttl']);
  close(estimate(sample({ input: 0, output: 0, total: 0, cacheRead: undefined, cacheWrite: undefined })).maxAmount, 0);
});

test('configured selectors remain explicit assumptions and known promotions end on schedule', () => {
  const base = sample({ model: undefined, source: 'agy.transcript' });
  const value = estimate(base, 'gemini-3.8-flash-high');
  assert.equal(value.model, 'gemini-3.8-flash');
  assert.deepEqual(value.assumptions, ['configured_model']);
  close(value.amount, .1 * .75 + .9 * .075 + .1 * 3.75);
  close(estimate(base, 'gemini-3.8-flash-high', '2027-01-01T00:00:00Z').amount, value.amount * 2);
  const fallback = estimate({ ...base, source: 'unknown' }, 'gemini-3.8-flash-high');
  assert.equal(fallback.model, 'gemini-3.8-flash-high');
  assert.deepEqual(fallback.assumptions, ['configured_model', 'family_fallback']);
  assert.equal(estimate(base, 'claude-opus-4-6-thinking').model, 'claude-opus-4-6');
  assert.equal(estimateCost({ ...base, model: 'unknown-upstream' }, 'gemini-3.8-flash', at), 'unknown_price');
  const deepseek = estimate(sample({ model: 'deepseek-v4.1-flash' }));
  close(deepseek.amount, .1 * .15 + .9 * .003 + .1 * .6);
  close(deepseek.maxAmount, deepseek.amount * 2);
  assert.deepEqual(deepseek.assumptions, ['time_tier']);
});

test('exact prices precede explicit family fallbacks, which preserve the original identity and currency', () => {
  const seed = estimate(sample({ model: 'Doubao-Seed-Evolving' }));
  assert.equal(seed.currency, 'CNY');
  assert.equal(seed.price.model, 'doubao-seed-evolving');
  assert.deepEqual(seed.assumptions, []);
  close(seed.amount, .1 * 6 + .9 * 1.2 + .1 * 30);
  const cases = [
    ['seed-evol', 'doubao-seed-2.1-pro'],
    ['doubao-seed-evolving-preview', 'doubao-seed-2.1-pro'],
    ['doubao-seed-2.1-pro-0915', 'doubao-seed-2.1-pro'],
    ['doubao-seed-turbo-preview', 'doubao-seed-2.1-turbo'],
    ['doubao-seed-lite-preview', 'doubao-seed-2.1-lite'],
    ['claude-opus-4-8', 'claude-opus-5-5'],
    ['claude-sonnet-4-8', 'claude-sonnet-5-5'],
    ['claude-haiku-4-8', 'claude-haiku-5-5'],
    ['gpt-5.6-sol', 'gpt-6.1-sol'],
    ['gpt-6-astra-preview', 'gpt-6-astra'],
    ['gpt-6-luna-preview', 'gpt-6-luna'],
    ['gemini-3.8-flash-preview', 'gemini-3.8-flash'],
    ['glm-5.3-preview', 'glm-5.3'],
    ['deepseek-v4.1-flash-preview', 'deepseek-flash'],
  ];
  for (const [model, reference] of cases) {
    const result = estimate(sample({ model }));
    assert.equal(result.model, model);
    assert.equal(result.price.model, reference);
    assert.ok(result.assumptions.includes('family_fallback'));
  }
  for (const model of ['unknown', 'not-claude-opus-4-7', 'gemini-3.8-flash-live', 'doubao-seedance-2.0', 'glm-embedding-5']) {
    assert.equal(estimateCost(sample({ model }), null, at), 'unknown_price');
  }
  const report = summarizeCosts([
    { sample: sample({ model: 'seed-evol' }), configuredModel: null, at, turnCost: false },
    { sample: sample({ model: 'Doubao-Seed-Evolving' }), configuredModel: null, at, turnCost: false },
    { sample: sample({ model: 'seed-evol', cost: { amount: 0, currency: 'CNY', estimated: true } }), configuredModel: null, at, turnCost: false },
  ], 0, now, true);
  assert.equal(report.familySamples, 1);
  assert.equal(report.estimatedSamples, 2);
  assert.equal(report.reportedSamples, 1);
  assert.equal(report.groups![0].model, 'seed-evol');
  assert.equal(report.groups![0].price!.model, 'doubao-seed-2.1-pro');
  close(report.totals.CNY.amount, seed.amount * 2);
});

test('native costs including zero take priority, currencies stay separate and details are opt-in', () => {
  const row = (s: UsageSample, turnCost = false) => ({ sample: s, configuredModel: null, at, turnCost });
  const rows = [
    row(sample(), true),
    row(sample({ scope: 'turn', cost: { amount: 0, currency: 'CNY', estimated: true } }), true),
    row(sample({ cost: { amount: 2, currency: 'USD', estimated: true } })),
    row(sample()),
    row(sample({ model: 'unlisted' })),
  ];
  const report = summarizeCosts(rows, 2, now);
  assert.equal(report.reportedSamples, 2); assert.equal(report.estimatedSamples, 1);
  assert.equal(report.unpricedSamples, 1); assert.equal(report.unmeasuredTurns, 2);
  assert.equal(report.totals.CNY.amount, 0);
  close(report.totals.USD.amount, 2 + estimate(sample()).amount);
  assert.equal(report.groups, undefined);
  const details = summarizeCosts(rows, 2, now, true);
  assert.equal(details.groups!.length, 2);
  assert.equal(details.groups![1].reason, 'unknown_price');
  assert.equal(summarizeCosts([], 0, now + 91 * 86400000).stale, true);
});

test('routing aliases use explicitly marked assumptions and do not overwrite native model identities', () => {
  const first = estimate(sample({ model: undefined, source: 'app-server.tokenUsage' }), 'openrouter-3o');
  assert.equal(first.model, 'claude-opus-4-6');
  assert.equal(first.alias, 'openrouter-3o');
  assert.deepEqual(first.assumptions, ['configured_model', 'model_alias']);
  const second = estimate(sample({ model: undefined, source: 'app-server.tokenUsage' }), 'openrouter-4o');
  assert.equal(second.model, 'claude-opus-4-7');
  close(first.amount, second.amount);
  const native = estimate(sample({ source: 'app-server.tokenUsage' }), 'openrouter-4o');
  assert.equal(native.model, 'glm-5.3');
  assert.equal(native.alias, undefined);
  assert.equal(estimateCost(sample({ model: 'openrouter-4o', source: 'other' }), null, at), 'unknown_price');
});

test('existing ledger rows gain read-time estimates without writes; filtering and out-of-window native totals are respected', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-price-'));
  const store = new Store(dir);
  try {
    for (const [id, agent, model] of [['a', 'codex', 'gpt-6-astra'], ['b', 'zcode', 'glm-5.3'], ['c', 'pi', 'glm-5.3']]) {
      store.createThread({ id, agent, cwd: dir, title: id, permissionMode: 'ask', model, effort: null });
      store.telemetry.observe({ threadId: id, turnId: 'one', type: 'turn.started', seq: 1, ts: at, data: {} });
    }
    const emit = (threadId: string, value: UsageSample, ts = at) => store.telemetry.observe({
      threadId, turnId: 'one', type: 'usage.sample', seq: 2, ts, data: { ...value },
    });
    emit('a', sample({ model: undefined, scope: 'update' }));
    emit('b', sample());
    emit('c', sample());
    // The native turn total is outside this query, but must still suppress fallback on its token rows.
    emit('c', { id: 'native-cost', source: 'acp.session.cost', scope: 'turn', cost: { amount: 3, currency: 'USD', estimated: true } }, new Date(now + 60000).toISOString());
    const report = store.telemetry.read(24, '', now);
    assert.equal(report.costSummary.estimatedSamples, 2);
    assert.equal(report.costSummary.groups, undefined);
    assert.equal(report.rows.flatMap(r => r.samples).some(s => s.cost), false);
    const codex = store.telemetry.costReport(24, 'codex', now);
    assert.equal(codex.groups!.length, 1);
    assert.deepEqual(codex.groups![0].assumptions, ['configured_model', 'context_tier']);
    close(store.telemetry.read(24, 'zcode', now).costSummary.totals.USD.amount, estimate(sample()).amount);
    assert.equal(store.telemetry.costReport(24, 'pi', now).estimatedSamples, 0);
    emit('b', sample({ id: 'native-cost', scope: 'turn', cost: { amount: 1, currency: 'USD', estimated: true } }));
    const updated = store.telemetry.read(24, 'zcode', now);
    assert.equal(updated.costSummary.estimatedSamples, 0);
    assert.equal(updated.costSummary.totals.USD.amount, 1);
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});
