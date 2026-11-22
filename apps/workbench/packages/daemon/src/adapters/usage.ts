import type { UsageSample } from '../telemetry.js';

const count = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;

/** Cumulative counters are differenced only against a known baseline, never against zero on resume. */
export function usageDifference(before: any, after: any, fields: Record<string, keyof UsageSample>): Partial<UsageSample> {
  const out: Record<string, number> = {};
  for (const [native, field] of Object.entries(fields)) {
    if (count(before?.[native]) && count(after?.[native]) && after[native] >= before[native]) out[field] = after[native] - before[native];
  }
  return out;
}

export function piUsage(message: any, id: string, ttftMs?: number): UsageSample | null {
  if (message?.role !== 'assistant' || !message.usage) return null;
  const usage = message.usage;
  // Pi's input excludes cache reads/writes; normalize input to include them, like Codex does.
  const input = [usage.input, usage.cacheRead, usage.cacheWrite].every(count) ? usage.input + usage.cacheRead + usage.cacheWrite : undefined;
  return {
    id: message.responseId || id, scope: 'request', source: 'pi.message_end',
    input, output: usage.output, total: usage.totalTokens, cacheRead: usage.cacheRead, cacheWrite: usage.cacheWrite,
    reasoning: usage.reasoning, model: message.responseModel || message.model, provider: message.provider,
    durationMs: message.durationMs, ttftMs,
    ...(count(usage.cost?.total) ? { cost: { amount: usage.cost.total, currency: 'USD', estimated: true } } : {}),
    ...(message.stopReason === 'error' ? { error: message.errorMessage || '模型请求失败' } : {}),
  };
}

const codexFields = { inputTokens: 'input', outputTokens: 'output', totalTokens: 'total', cachedInputTokens: 'cacheRead', reasoningOutputTokens: 'reasoning' } as const;

export class CodexUsage {
  private previous: any = null;
  sample(usage: any): UsageSample | null {
    const total = usage?.total;
    if (!count(total?.totalTokens)) return null;
    const previous = this.previous;
    this.previous = { ...total };
    if (previous && total.totalTokens <= previous.totalTokens) return null;
    // The first notification on a resumed thread includes its entire lifetime in total.
    const values = previous ? usageDifference(previous, total, codexFields) : Object.fromEntries(
      Object.entries(codexFields).filter(([key]) => count(usage.last?.[key])).map(([key, field]) => [field, usage.last[key]]),
    );
    if (!count(values.total)) return null;
    return { id: `tokens-${total.totalTokens}`, scope: 'update', source: 'app-server.tokenUsage', ...values };
  }
}

export function zcodeUsage(before: any, after: any): Partial<UsageSample> {
  return usageDifference(before, after, { inputTokens: 'input', outputTokens: 'output', totalTokens: 'total',
    reasoningTokens: 'reasoning', cacheReadTokens: 'cacheRead', cacheCreationTokens: 'cacheWrite' });
}

/** ACP prompt usage is per prompt. Model breakdowns include subagents; do not add the main-loop tally again. */
export function acpUsage(response: any, id: string, cost?: UsageSample['cost']): UsageSample[] {
  const models = response?._meta?.quota?.model_usage;
  const rows: Array<{ model?: string; usage: any }> = Array.isArray(models) && models.some(row => count(row?.token_count?.totalTokens) && row.token_count.totalTokens > 0)
    ? models.map(row => ({ model: row.model, usage: { ...row.token_count, cachedReadTokens: row.token_count?.cachedInputTokens } }))
    : response?.usage ? [{ usage: response.usage }] : [];
  const samples: UsageSample[] = rows.map(({ model, usage }, index) => ({
    id: `${id}-${index}`, scope: 'turn', source: 'acp.prompt.usage', model,
    input: [usage.inputTokens, usage.cachedReadTokens, usage.cachedWriteTokens].every(count)
      ? usage.inputTokens + usage.cachedReadTokens + usage.cachedWriteTokens : undefined,
    output: usage.outputTokens, total: usage.totalTokens, cacheRead: usage.cachedReadTokens, cacheWrite: usage.cachedWriteTokens,
  }));
  // Session cost can include sidechains; never attribute its whole amount to one model row.
  if (cost) samples.push({ id: `${id}-cost`, scope: 'turn', source: 'acp.session.cost', cost });
  return samples;
}
