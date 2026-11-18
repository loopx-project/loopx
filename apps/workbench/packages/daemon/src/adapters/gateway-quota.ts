import type { PlanQuota, QuotaBalance, QuotaWindow } from './types.js';

/**
 * A gateway's own quota report (`GET /v1/magpie/quotas`): each subscription, plan and API key it
 * routes to, with windows for plans and a balance for pay-as-you-go keys. The gateway asks the
 * vendors with credentials it keeps, so the hub never holds or copies a vendor key.
 */
interface GatewayQuotaEntry {
  provider?: unknown;
  name?: unknown;
  kind?: unknown;
  plan?: unknown;
  user?: unknown;
  windows?: Array<{ name?: unknown; used?: unknown; resetsAt?: unknown; unlimited?: unknown }>;
  balance?: unknown;
  error?: unknown;
}

const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '');

/** "5 hours" → 5H, "Weekly" → 7D; other names stay as the gateway wrote them. */
export function gatewayWindowLabel(name: string): string {
  const n = name.trim().toLowerCase();
  if (n === 'weekly') return '7D';
  if (n === 'daily') return '1D';
  const span = /^(\d+)\s*(h|hours?|d|days?)$/.exec(n);
  return span ? `${span[1]}${span[2].startsWith('h') ? 'H' : 'D'}` : name.trim();
}

/**
 * The report narrowed to the providers this instance's catalog can reach, judging by the
 * `provider/` prefix of its model ids. Without a catalog every entry is kept, unmapped.
 */
export function gatewayQuota(payload: unknown, catalog: string[] | null, checkedAt = new Date().toISOString()): PlanQuota {
  const data = (payload as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) throw new Error('gateway quota report is not a list');
  const reachable = (provider: string) => catalog?.filter(id => id.startsWith(`${provider}/`));
  const entries = (data as GatewayQuotaEntry[]).filter(entry => {
    const provider = text(entry?.provider);
    return provider && (!catalog || reachable(provider)!.length > 0);
  });
  const scopes = entries.map(entry => text(entry.plan) || text(entry.name) || text(entry.provider));
  const windows: QuotaWindow[] = [];
  const balances: QuotaBalance[] = [];
  const failures: string[] = [];
  entries.forEach((entry, index) => {
    const user = text(entry.user);
    // Two accounts of one plan need telling apart; a lone one keeps the plain name.
    const scope = scopes.indexOf(scopes[index]) !== scopes.lastIndexOf(scopes[index]) && user ? `${scopes[index]} · ${user}` : scopes[index];
    const modelIds = reachable(text(entry.provider));
    const pool = modelIds ? { modelIds } : {};
    let read = false;
    for (const window of Array.isArray(entry.windows) ? entry.windows : []) {
      const name = text(window?.name);
      if (!name || window.unlimited === true || typeof window.used !== 'number' || !Number.isFinite(window.used)) continue;
      const reset = typeof window.resetsAt === 'string' ? new Date(window.resetsAt) : null;
      windows.push({
        label: gatewayWindowLabel(name), scope, ...pool, usedPercent: Math.max(0, Math.min(100, window.used)),
        ...(reset && Number.isFinite(reset.getTime()) ? { resetsAt: reset.toISOString() } : {}),
      });
      read = true;
    }
    const balance = text(entry.balance);
    if (balance) { balances.push({ scope, amount: balance, ...pool }); read = true; }
    if (!read && text(entry.error)) failures.push(`${scope}: ${text(entry.error).slice(0, 160)}`);
  });
  return {
    windows, ...(balances.length ? { balances } : {}), checkedAt,
    ...(failures.length ? { note: failures.join('；') } : !entries.length ? { note: '网关没有报告当前可用模型的额度' } : {}),
  };
}

/** The gateway's report, or null when it has none (an ordinary Anthropic-compatible endpoint). */
export async function fetchGatewayQuota(baseUrl: string, headers: Record<string, string>, catalog: string[] | null): Promise<PlanQuota | null> {
  const root = baseUrl.replace(/\/$/, '').replace(/\/v1$/, '');
  const response = await fetch(`${root}/v1/magpie/quotas`, { headers, signal: AbortSignal.timeout(20_000), redirect: 'error' });
  if (response.status === 404 || response.status === 405) return null;
  if (!response.ok) throw new Error(`gateway quota read failed (HTTP ${response.status})`);
  return gatewayQuota(await response.json(), catalog);
}
