import type { UsageSample } from './telemetry.js';
import { pricingModel } from './adapters/pricing-model.js';

export const PRICE_CHECKED_AT = '2026-10-20';
const OPENAI = 'https://developers.openai.com/api/docs/models/';
const ANTHROPIC = 'https://platform.claude.com/docs/en/about-claude/pricing';
const GOOGLE = 'https://ai.google.dev/gemini-api/docs/pricing';
const ZAI = 'https://docs.z.ai/guides/overview/pricing';
const ARK = 'https://docs.volcengine.com/docs/ark/model-pricing?lang=en&redirect=1';

export interface Rates {
  input: number; output: number; cacheRead: number;
  cacheWrite?: number; cacheWriteMax?: number;
}
export interface ModelPrice {
  model: string; currency: string; source: string; rates: Rates;
  longContext?: { above: number; rates: Rates };
  peakRates?: Rates;
  after?: { date: string; rates: Rates };
}
const openai = (model: string, input: number, cacheRead: number, output: number): ModelPrice => ({
  model, currency: 'USD', source: OPENAI + model, rates: { input, cacheRead, output, cacheWrite: input * 1.25 },
  longContext: { above: 272000, rates: { input: input * 2, cacheRead: cacheRead * 2, output: output * 1.5, cacheWrite: input * 2.5 } },
});
const claude = (model: string, input: number, output: number, cacheMultiplier = .1): ModelPrice => ({
  model, currency: 'USD', source: ANTHROPIC, rates: { input, output, cacheRead: input * cacheMultiplier, cacheWrite: input * 1.25, cacheWriteMax: input * 2 },
});
// Reviewed manufacturer list prices per million tokens. No network request is made while reading usage.
export const MODEL_PRICES: ModelPrice[] = [
  openai('gpt-6-astra', 10, 1, 50),
  openai('gpt-6.1-sol', 2, .1, 10),
  openai('gpt-6-sol', 2, .2, 10),
  openai('gpt-6-luna', .1, .01, .5),
  claude('claude-opus-4-6', 5, 25),
  claude('claude-opus-4-7', 5, 25),
  claude('claude-sonnet-4-6', 3, 15),
  claude('claude-opus-5-5', 4, 20, .05),
  claude('claude-sonnet-5-5', 2, 10, .05),
  { ...claude('claude-haiku-5-5', .1, .5), longContext: { above: 100000, rates: claude('claude-haiku-5-5', .5, 2.5).rates } },
  ...['gemini-3.8-flash', 'gemini-3.6-flash'].map(model => ({
    model, currency: 'USD', source: GOOGLE, rates: { input: .75, cacheRead: .075, output: 3.75 },
    after: { date: '2027-01-01', rates: { input: 1.5, cacheRead: .15, output: 7.5 } },
  })),
  ...['glm-5.3', 'glm-5.2', 'glm-5.1'].map(model => ({
    model, currency: 'USD', source: ZAI, rates: { input: 1.4, cacheRead: .26, output: 4.4 },
  })),
  ...['deepseek-flash', 'deepseek-v4.1-flash'].map(model => ({
    model, currency: 'USD', source: 'https://api-docs.deepseek.com/quick_start/pricing/',
    rates: { input: .15, cacheRead: .003, output: .6 },
    peakRates: { input: .3, cacheRead: .006, output: 1.2 },
  })),
  ...['doubao-seed-evolving', 'doubao-seed-2.1-pro'].map(model => ({
    model, currency: 'CNY', source: ARK, rates: { input: 6, cacheRead: 1.2, output: 30 },
  })),
  { model: 'doubao-seed-2.1-turbo', currency: 'CNY', source: ARK, rates: { input: 3, cacheRead: .6, output: 15 } },
  { model: 'doubao-seed-2.1-lite', currency: 'CNY', source: ARK, rates: { input: .8, cacheRead: .16, output: 2.7 } },
];
const prices = new Map(MODEL_PRICES.map(price => [price.model, price]));

// Explicit text-model families only. Preserve the requested identity and expose the borrowed price.
const FAMILY_PRICES: Array<[RegExp, string]> = [
  [/^claude-opus(?:-\d+)*(?:-(?:latest|thinking|preview))?$/, 'claude-opus-5-5'],
  [/^claude-sonnet(?:-\d+)*(?:-(?:latest|thinking|preview))?$/, 'claude-sonnet-5-5'],
  [/^claude-haiku(?:-\d+)*(?:-(?:latest|thinking|preview))?$/, 'claude-haiku-5-5'],
  [/^gpt-\d+(?:\.\d+)?-astra(?:-(?:latest|preview|\d{4}-\d{2}-\d{2}))?$/, 'gpt-6-astra'],
  [/^gpt-\d+(?:\.\d+)?-sol(?:-(?:latest|preview|\d{4}-\d{2}-\d{2}))?$/, 'gpt-6.1-sol'],
  [/^gpt-\d+(?:\.\d+)?-luna(?:-(?:latest|preview|\d{4}-\d{2}-\d{2}))?$/, 'gpt-6-luna'],
  [/^gemini-\d+(?:\.\d+)?-flash(?:-(?:preview|latest|low|medium|high|\d{2,8}))*$/, 'gemini-3.8-flash'],
  [/^glm-\d+(?:\.\d+)?(?:-(?:preview|latest|\d{4,8}))?$/, 'glm-5.3'],
  [/^deepseek-(?:v\d+(?:[.-]\d+)?-)?flash(?:-(?:preview|latest|\d{4,8}))?$/, 'deepseek-flash'],
  [/^(?:doubao-)?seed-(?:(?:\d+[.-])*(?:pro|code)|evol(?:ving)?)(?:-[a-z0-9]+)*$/, 'doubao-seed-2.1-pro'],
  [/^(?:doubao-)?seed-(?:\d+[.-])*turbo(?:-[a-z0-9]+)*$/, 'doubao-seed-2.1-turbo'],
  [/^(?:doubao-)?seed-(?:\d+[.-])*lite(?:-[a-z0-9]+)*$/, 'doubao-seed-2.1-lite'],
];

export type CostReason = 'missing_model' | 'unknown_price' | 'missing_tokens' | 'invalid_usage';
export type CostAssumption = 'configured_model' | 'cache_split' | 'cache_ttl' | 'context_tier' | 'model_alias' | 'time_tier' | 'family_fallback';
export interface CostRange { amount: number; maxAmount: number }
export interface CostEstimate extends CostRange {
  currency: string; model: string; alias?: string; price: ModelPrice; assumptions: CostAssumption[];
}
const valid = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0;

/** Bound unknown cache partitions instead of silently treating unreported counters as zero. */
function amount(sample: UsageSample, rates: Rates, maximum: boolean): number {
  const read = sample.cacheRead ?? 0, write = sample.cacheWrite ?? 0;
  const writeRate = maximum ? rates.cacheWriteMax ?? rates.cacheWrite ?? rates.input : rates.cacheWrite ?? rates.input;
  const choices = [rates.input];
  if (sample.cacheRead === undefined) choices.push(rates.cacheRead);
  if (sample.cacheWrite === undefined) choices.push(writeRate);
  const remainderRate = maximum ? Math.max(...choices) : Math.min(...choices);
  return (read * rates.cacheRead + write * writeRate + (sample.input! - read - write) * remainderRate + sample.output! * rates.output) / 1e6;
}

export function estimateCost(sample: UsageSample, configuredModel: string | null, at: string): CostEstimate | CostReason {
  const resolution = pricingModel(sample, configuredModel);
  if (!resolution) return 'missing_model';
  const { model, assumedAlias: alias } = resolution;
  const listed = prices.get(model) ?? prices.get(FAMILY_PRICES.find(([pattern]) => pattern.test(model))?.[1] ?? '');
  if (!listed) return 'unknown_price';
  if (!valid(sample.input) || !valid(sample.output)) return 'missing_tokens';
  if ([sample.cacheRead, sample.cacheWrite].some(n => n !== undefined && !valid(n))
    || (sample.cacheRead ?? 0) + (sample.cacheWrite ?? 0) > sample.input
    || (sample.total !== undefined && (!valid(sample.total) || sample.input + sample.output !== sample.total))) return 'invalid_usage';
  const rates = listed.after && at >= listed.after.date ? listed.after.rates : listed.rates;
  const price = { ...listed, rates };
  const assumptions: CostAssumption[] = [];
  if (!sample.model) assumptions.push('configured_model');
  if (alias) assumptions.push('model_alias');
  if (listed.model !== model) assumptions.push('family_fallback');
  if (sample.input > 0 && (sample.cacheRead === undefined || rates.cacheWrite !== undefined && sample.cacheWrite === undefined)) assumptions.push('cache_split');
  if ((sample.cacheWrite ?? 0) > 0 && rates.cacheWriteMax !== undefined) assumptions.push('cache_ttl');
  let lower = rates, upper = rates;
  if (price.peakRates) {
    upper = price.peakRates;
    assumptions.push('time_tier');
  }
  if (price.longContext && sample.input > price.longContext.above) {
    upper = price.longContext.rates;
    if (sample.scope === 'request') lower = upper;
    else assumptions.push('context_tier');
  }
  const low = amount(sample, lower, false), high = amount(sample, upper, true);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return 'invalid_usage';
  return { model, alias, currency: price.currency, price, assumptions, amount: low, maxAmount: high };
}

export interface CostRow { sample: UsageSample; configuredModel: string | null; at: string; turnCost: boolean }
export interface CostGroup {
  model: string | null; alias?: string; reason?: CostReason; samples: number; currency?: string;
  amount: number; maxAmount: number; price?: ModelPrice; assumptions: CostAssumption[];
}
export interface CostSummary {
  totals: Record<string, CostRange>; reported: Record<string, CostRange>; estimated: Record<string, CostRange>;
  estimatedSamples: number; familySamples: number; reportedSamples: number; unpricedSamples: number; unmeasuredTurns: number;
  priceCheckedAt: string; stale: boolean;
}

export function summarizeCosts(rows: CostRow[], unmeasuredTurns: number, now: number, details = false) {
  const summary: CostSummary = {
    totals: {}, reported: {}, estimated: {}, estimatedSamples: 0, familySamples: 0, reportedSamples: 0, unpricedSamples: 0,
    unmeasuredTurns, priceCheckedAt: PRICE_CHECKED_AT, stale: now - Date.parse(PRICE_CHECKED_AT) > 90 * 86400000,
  };
  const groups = new Map<string, CostGroup>();
  const add = (target: Record<string, CostRange>, currency: string, value: CostRange) => {
    const previous = target[currency] ?? { amount: 0, maxAmount: 0 };
    target[currency] = { amount: previous.amount + value.amount, maxAmount: previous.maxAmount + value.maxAmount };
  };
  for (const row of rows) {
    const { sample } = row;
    // A separate native turn cost includes its request rows, even when the cost lies outside the query window.
    if (row.turnCost && sample.scope !== 'turn') continue;
    if (sample.cost) {
      const value = { amount: sample.cost.amount, maxAmount: sample.cost.amount };
      add(summary.reported, sample.cost.currency, value); add(summary.totals, sample.cost.currency, value);
      summary.reportedSamples++;
      continue;
    }
    if (row.turnCost) continue;
    if (sample.input === undefined && sample.output === undefined && sample.total === undefined) continue;
    const result = estimateCost(sample, row.configuredModel, row.at);
    const reason = typeof result === 'string' ? result : undefined;
    const estimate = typeof result === 'string' ? undefined : result;
    if (estimate) {
      add(summary.estimated, estimate.currency, estimate); add(summary.totals, estimate.currency, estimate);
      summary.estimatedSamples++;
      if (estimate.assumptions.includes('family_fallback')) summary.familySamples++;
    } else summary.unpricedSamples++;
    if (!details) continue;
    const model = estimate?.model ?? sample.model ?? row.configuredModel;
    const key = JSON.stringify([model, estimate?.alias, reason, estimate?.price.rates]);
    let group = groups.get(key);
    if (!group) {
      group = { model, alias: estimate?.alias, reason, samples: 0, amount: 0, maxAmount: 0, assumptions: [],
        ...(estimate ? { currency: estimate.currency, price: estimate.price } : {}) };
      groups.set(key, group);
    }
    group.samples++;
    if (estimate) {
      group.amount += estimate.amount; group.maxAmount += estimate.maxAmount;
      for (const assumption of estimate.assumptions) if (!group.assumptions.includes(assumption)) group.assumptions.push(assumption);
    }
  }
  return { ...summary, ...(details ? { groups: [...groups.values()] } : {}) };
}
