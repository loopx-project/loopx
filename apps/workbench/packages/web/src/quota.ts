import type { PlanQuota, QuotaBalance, QuotaWindow } from './api';
import { formatDate, t } from './i18n';

// A legacy wire value, independent of the interface language.
const LEGACY_ACCOUNT_QUOTA_SCOPE = '全部模型';

/** Colour class for a bar: calm under half, warning past it, danger when nearly full. */
export function quotaLevel(usedPercent: number): 'ok' | 'warn' | 'danger' {
  return usedPercent >= 80 ? 'danger' : usedPercent >= 50 ? 'warn' : 'ok';
}

/**
 * When a window resets, as a short local-time note: "Resets 14:40" while it is today,
 * "Resets Oct 12 07:00" otherwise. Empty when unknown.
 */
export function formatReset(window: QuotaWindow, now = new Date()): string {
  if (!window.resetsAt) return '';
  const at = new Date(window.resetsAt);
  if (Number.isNaN(at.getTime())) return '';
  const sameDay = at.toDateString() === now.toDateString();
  const hm = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
  return t('quota.resets', sameDay ? hm : `${formatDate(at, { month: 'short', day: 'numeric' })} ${hm}`);
}

/** The board's rows: visible agents that report quota, with the one in use moved to the top. */
export function quotaRows<T extends { id: string; hidden?: boolean; capabilities: { quota: boolean } }>(agents: T[], current?: string): T[] {
  const rows = agents.filter((a) => !a.hidden && a.capabilities.quota);
  return [...rows.filter((a) => a.id === current), ...rows.filter((a) => a.id !== current)];
}

/** How much of a window is left, 0 to 100, to one decimal at most. */
export function quotaLeft(window: QuotaWindow): number {
  return Math.round(Math.max(0, Math.min(100, 100 - window.usedPercent)) * 10) / 10;
}

/** Hours, then days; a label of another shape keeps the order it was met in, after them. */
function periodLength(label: string): number {
  const m = /^(\d+)(H|D)$/.exec(label);
  return m ? Number(m[1]) * (m[2] === 'D' ? 24 : 1) : Number.POSITIVE_INFINITY;
}

/** The board's columns: every window length any agent reports, shortest first. */
export function quotaColumns(quotas: Array<PlanQuota | null | undefined>): string[] {
  const labels: string[] = [];
  for (const quota of quotas) for (const w of quota?.windows ?? []) if (!labels.includes(w.label)) labels.push(w.label);
  return labels.map((label, i) => ({ label, i })).sort((a, b) => periodLength(a.label) - periodLength(b.label) || a.i - b.i).map((c) => c.label);
}

/** A column's name: "5 hours", "Weekly", "3 days"; a label of another shape as it is. */
export function periodName(label: string): string {
  if (label === '7D') return t('quota.board.weekly');
  const m = /^(\d+)(H|D)$/.exec(label);
  if (!m) return label;
  return m[2] === 'H' ? t('quota.board.hours', Number(m[1])) : t('quota.board.days', Number(m[1]));
}

/** Windows split by the model group they cover, in the order the agent gave; ungrouped first. */
export function quotaGroups(windows: QuotaWindow[]): Array<{ scope?: string; windows: QuotaWindow[] }> {
  const groups: Array<{ scope?: string; windows: QuotaWindow[] }> = [];
  for (const w of windows) {
    // Keep readings from older servers and caches in the same account-wide pool.
    const scope = w.scope === LEGACY_ACCOUNT_QUOTA_SCOPE ? undefined : w.scope;
    const group = groups.find((g) => g.scope === scope);
    if (group) group.windows.push(w);
    else groups.push({ scope, windows: [w] });
  }
  return groups.sort((a, b) => Number(a.scope !== undefined) - Number(b.scope !== undefined));
}

/** Keep short and weekly readings side by side, without inventing a value for a missing window. */
export function quotaSlots(windows: QuotaWindow[]): Array<{ label: string; window?: QuotaWindow }> {
  const labels = [...new Set(windows.map(window => window.label))].sort((a, b) => periodLength(a) - periodLength(b));
  const periods = labels.includes('5H') || labels.includes('7D') ? ['5H', '7D', ...labels.filter(label => label !== '5H' && label !== '7D')] : labels;
  return periods.flatMap(label => {
    const matches = windows.filter(window => window.label === label);
    return matches.length ? matches.map(window => ({ label, window })) : [{ label }];
  });
}

/** Whether a reported balance has run out: its number is zero or below. Unparsable is not empty. */
export function balanceEmpty(balance: QuotaBalance): boolean {
  const value = /-?\d[\d,]*(?:\.\d+)?/.exec(balance.amount)?.[0];
  return value !== undefined && Number(value.replace(/,/g, '')) <= 0;
}

/** When a window resets, as short as a table cell allows: "14:40" today, "10/12" later. */
export function shortReset(window: QuotaWindow, now = new Date()): string {
  if (!window.resetsAt) return '';
  const at = new Date(window.resetsAt);
  if (Number.isNaN(at.getTime())) return '';
  if (at.toDateString() === now.toDateString()) return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
  return `${at.getMonth() + 1}/${at.getDate()}`;
}
