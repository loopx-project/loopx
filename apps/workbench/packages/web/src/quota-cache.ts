import type { PlanQuota } from './api';

const KEY = 'calli.quota.v1';
const MAX_READINGS = 100;

/** Only successful readings are stored; their original timestamps stay visible while refreshing. */
function readings(): Record<string, PlanQuota> {
  try {
    const data = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
    return Object.fromEntries(Object.entries(data).filter(([, value]) => {
      const q = value as PlanQuota | null;
      return q && typeof q.checkedAt === 'string' && Number.isFinite(Date.parse(q.checkedAt))
        && (q.note === undefined || typeof q.note === 'string') && Array.isArray(q.windows)
        && (q.balances === undefined || Array.isArray(q.balances) && q.balances.every(b => b && typeof b.scope === 'string' && typeof b.amount === 'string'))
        && (q.plan === undefined || (typeof q.plan?.id === 'string' && typeof q.plan?.label === 'string'))
        && q.windows.every(w => w && typeof w.label === 'string' && Number.isFinite(w.usedPercent)
          && w.usedPercent >= 0 && w.usedPercent <= 100
          && (w.scope === undefined || typeof w.scope === 'string')
          && (w.resetsAt === undefined || typeof w.resetsAt === 'string')
          && (w.loadPercent === undefined || Number.isFinite(w.loadPercent)));
    })) as Record<string, PlanQuota>;
  } catch {
    return {};
  }
}

/** The API URL includes the hub, remote host and instance, so their readings stay separate. */
export function cachedQuota(url: string): PlanQuota | null {
  return readings()[url] ?? null;
}

export function rememberQuota(url: string, quota: PlanQuota): void {
  try {
    const entries = Object.entries({ ...readings(), [url]: quota });
    entries.sort((a, b) => Date.parse(b[1].checkedAt) - Date.parse(a[1].checkedAt));
    localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries.slice(0, MAX_READINGS))));
  } catch {
    // The API client still keeps its reading in memory when browser storage is unavailable.
  }
}

export function clearQuotaCache(): void {
  try { localStorage.removeItem(KEY); } catch { /* Storage may be disabled. */ }
}
