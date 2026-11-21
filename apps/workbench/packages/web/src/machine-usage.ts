/**
 * The machine token charts' arithmetic (persona, docs/server/machines.md 「Token usage charts」):
 * the counting rules are pure, so they are tested directly and the page only draws what they
 * return. The four kinds keep one fixed order — the stacking order, the legend order, and the
 * palette's validated adjacency order are all the same one.
 */
import type { MachineUsageReport, MachineUsageShare, UsagePoint } from './api';

export type UsageKind = 'in' | 'out' | 'cacheWrite' | 'cacheRead';

/** The kinds in the order a stacked chart puts them from the bottom. */
export const USAGE_KINDS: UsageKind[] = ['in', 'out', 'cacheWrite', 'cacheRead'];

/** The report field each kind reads: the chart's short names against the report's longer ones. */
export const USAGE_FIELD: Record<UsageKind, 'input' | 'output' | 'cacheWrite' | 'cacheRead'> = {
  in: 'input',
  out: 'output',
  cacheWrite: 'cacheWrite',
  cacheRead: 'cacheRead',
};

/** Each kind's chart colour (a styles.css token; validated as a set, see styles.css). */
export const USAGE_COLOR: Record<UsageKind, string> = {
  in: 'var(--usage-input)',
  out: 'var(--usage-output)',
  cacheWrite: 'var(--usage-cache-write)',
  cacheRead: 'var(--usage-cache-read)',
};

export type UsageTokens = Pick<MachineUsageReport['total'], 'input' | 'cacheWrite' | 'cacheRead' | 'output' | 'replies'>;

/** Every token, cache reads included. */
export function totalTokens(u: UsageTokens): number {
  return u.input + u.cacheWrite + u.cacheRead + u.output;
}

/** Cache read / (input + cache write + cache read); null without any input. */
export function hitRate(u: Pick<UsageTokens, 'input' | 'cacheWrite' | 'cacheRead'>): number | null {
  const prompt = u.input + u.cacheWrite + u.cacheRead;
  return prompt > 0 ? u.cacheRead / prompt : null;
}

export function percentText(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

/** A step's sum per minute. */
export function perMinute(sum: number, stepSeconds: number): number {
  return Math.round(sum / Math.max(stepSeconds / 60, 1));
}

/** The stacked chart's layers: for each kind shown, every step's bottom and top (per minute). */
export function stackLayers(
  points: UsagePoint[],
  step: number,
  kinds: UsageKind[],
): Array<{ kind: UsageKind; values: Array<{ t: number; y0: number; y1: number }> }> {
  const base = new Array<number>(points.length).fill(0);
  return kinds.map((kind) => ({
    kind,
    values: points.map((point, i) => {
      const y0 = base[i];
      base[i] += perMinute(point[USAGE_FIELD[kind]], step);
      return { t: point.t, y0, y1: base[i] };
    }),
  }));
}

/** The runs of offline steps, from the first's start to the last's end. */
export function offlineRuns(points: UsagePoint[], step: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let start: number | null = null;
  points.forEach((point, i) => {
    if (point.gap && start === null) start = point.t;
    const ends = !point.gap || i === points.length - 1;
    if (start !== null && ends) {
      out.push([start, point.gap ? point.t + step : point.t]);
      start = null;
    }
  });
  return out;
}

/** The hit rate's runs: a step with at least `minReplies` replies is drawn; fewer (or offline)
 *  breaks the line. Replies come from the filtered series. */
export function hitRuns(
  points: UsagePoint[],
  minReplies = 3,
): Array<Array<{ t: number; v: number }>> {
  const out: Array<Array<{ t: number; v: number }>> = [];
  let run: Array<{ t: number; v: number }> = [];
  for (const point of points) {
    const rate = hitRate(point);
    if (point.gap || point.replies < minReplies || rate === null) {
      if (run.length) out.push(run);
      run = [];
      continue;
    }
    run.push({ t: point.t, v: rate });
  }
  if (run.length) out.push(run);
  return out;
}

/** The step nearest to `t`, within one step, skipping offline ones; undefined over a gap. */
export function nearestStep(report: MachineUsageReport, t: number): UsagePoint | undefined {
  let best: UsagePoint | undefined;
  for (const point of report.points) {
    if (point.gap) continue;
    if (!best || Math.abs(point.t - t) < Math.abs(best.t - t)) best = point;
  }
  return best && Math.abs(best.t - t) <= report.step ? best : undefined;
}

/** Today's sums in the viewer's zone, from a 24-hour report's points: the window reaches past
 *  midnight only part of the day, so the points this side of local midnight are today's. */
export function todayTokens(report: MachineUsageReport): UsageTokens {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const from = midnight.getTime() / 1000;
  const today: UsageTokens = { input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0 };
  for (const point of report.points) {
    // A step reaching over midnight counts where most of it lies.
    if (point.t + report.step / 2 < from) continue;
    today.input += point.input;
    today.cacheWrite += point.cacheWrite;
    today.cacheRead += point.cacheRead;
    today.output += point.output;
    today.replies += point.replies;
  }
  return today;
}

/** Every token per minute right now: the average rate of the newest few live steps (persona
 *  averages the last few minutes too — the newest step is usually still being filled in). */
export function currentPerMinute(report: MachineUsageReport): number {
  const live = report.points.filter((point) => !point.gap);
  const recent = live.slice(-Math.min(3, live.length));
  if (!recent.length) return 0;
  const window = Math.max(recent.length * report.step, 60);
  return perMinute(recent.reduce((sum, point) => sum + totalTokens(point), 0), window);
}

/** A share row's tokens, sorted as the daemon sent them (busiest first). */
export function shareTokens(share: MachineUsageShare): number {
  return totalTokens(share);
}
