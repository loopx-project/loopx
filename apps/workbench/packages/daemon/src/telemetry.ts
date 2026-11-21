import type { DatabaseSync } from 'node:sqlite';
import type { StoredEvent } from './events.js';
import { summarizeCosts } from './pricing.js';
import type { TurnStatsRow, TurnTokenSums } from './turn-stats.js';

/** Never infer billable tokens from context size, character counts, or a historical replay. */
export interface UsageSample {
  id: string;
  scope: 'request' | 'turn' | 'update';
  source: string;
  input?: number;
  output?: number;
  total?: number;
  cacheRead?: number;
  cacheWrite?: number;
  reasoning?: number;
  model?: string;
  provider?: string;
  account?: string;
  durationMs?: number;
  ttftMs?: number;
  cost?: { amount: number; currency: string; estimated: boolean };
  error?: string;
  startedAt?: string;
  endedAt?: string;
  timing?: 'estimated';
}

export function cleanSample(data: Record<string, unknown>): UsageSample | null {
  if (typeof data.id !== 'string' || !data.id || !['request', 'turn', 'update'].includes(String(data.scope))) return null;
  const out: UsageSample = { id: data.id.slice(0, 200), scope: data.scope as UsageSample['scope'], source: String(data.source ?? 'native').slice(0, 80) };
  for (const key of ['input', 'output', 'total', 'cacheRead', 'cacheWrite', 'reasoning', 'durationMs', 'ttftMs'] as const) {
    const n = data[key];
    if (typeof n === 'number' && Number.isFinite(n) && n >= 0) out[key] = n;
  }
  for (const key of ['model', 'provider', 'account', 'error'] as const) {
    if (typeof data[key] === 'string') out[key] = data[key].slice(0, key === 'error' ? 500 : 200);
  }
  const cost = data.cost as UsageSample['cost'];
  if (data.timing === 'estimated' && typeof data.startedAt === 'string' && typeof data.endedAt === 'string') {
    const start = Date.parse(data.startedAt), end = Date.parse(data.endedAt);
    if (Number.isFinite(start) && Number.isFinite(end) && start <= end) {
      out.startedAt = new Date(start).toISOString(); out.endedAt = new Date(end).toISOString(); out.timing = 'estimated';
    }
  }
  if (cost && typeof cost.amount === 'number' && Number.isFinite(cost.amount) && cost.amount >= 0 && /^[A-Z]{3}$/.test(cost.currency)) {
    out.cost = { amount: cost.amount, currency: cost.currency, estimated: cost.estimated === true };
  }
  return out;
}

/** Chart buckets per range: about 60-170 points, so a day reads as a trend rather than per-minute spikes. */
const STEP_MINUTES: Record<number, number> = { 1: 1, 24: 10, 168: 60 };

/** How far back the daily heatmap reaches: a year, plus the week it starts in. */
const DAILY_DAYS = 371;

export class Telemetry {
  constructor(private readonly db: DatabaseSync) {
    db.exec(`
      CREATE TABLE IF NOT EXISTS telemetry_turns (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        agent TEXT NOT NULL, configured_model TEXT, linked_account TEXT,
        first_output_ms REAL, status TEXT NOT NULL, error TEXT,
        PRIMARY KEY(thread_id, turn_id)
      );
      CREATE TABLE IF NOT EXISTS telemetry_samples (
        thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, sample_id TEXT NOT NULL,
        ts TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY(thread_id, turn_id, sample_id),
        FOREIGN KEY(thread_id, turn_id) REFERENCES telemetry_turns(thread_id, turn_id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS telemetry_time ON telemetry_turns(started_at);
      CREATE INDEX IF NOT EXISTS telemetry_sample_time ON telemetry_samples(ts);
      CREATE TABLE IF NOT EXISTS telemetry_turn_stats (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL,
        timing TEXT NOT NULL, segments INTEGER NOT NULL, steps INTEGER NOT NULL,
        wait_ms REAL NOT NULL, gen_ms REAL NOT NULL, tool_ms REAL NOT NULL,
        PRIMARY KEY(thread_id, turn_id)
      );
    `);
  }

  observe(event: StoredEvent): void {
    const { threadId, turnId, ts, type, data } = event;
    if (!turnId || data.imported || data.fromTerminal) return;
    if (type === 'turn.started') {
      this.db.prepare(`INSERT OR IGNORE INTO telemetry_turns(thread_id,turn_id,started_at,agent,configured_model,linked_account,status)
        SELECT id,?,?,agent,model,?,'running' FROM threads WHERE id=?`).run(turnId, ts, typeof data.linkedAccount === 'string' ? data.linkedAccount : null, threadId);
    } else if ((type === 'message.delta' || type === 'reasoning.delta') && typeof data.text === 'string' && data.text.length) {
      this.db.prepare(`UPDATE telemetry_turns SET first_output_ms=MAX(0,(julianday(?) - julianday(started_at))*86400000)
        WHERE thread_id=? AND turn_id=? AND first_output_ms IS NULL`).run(ts, threadId, turnId);
    } else if (type === 'turn.done' || type === 'turn.error') {
      this.db.prepare('UPDATE telemetry_turns SET finished_at=?,status=?,error=? WHERE thread_id=? AND turn_id=?')
        .run(ts, type === 'turn.error' ? 'error' : data.stopReason === 'cancelled' ? 'cancelled' : 'done',
          type === 'turn.error' ? String(data.message ?? '执行失败').slice(0, 500) : null, threadId, turnId);
    } else if (type === 'usage.sample') {
      const sample = cleanSample(data);
      if (!sample) return;
      this.db.prepare(`INSERT OR IGNORE INTO telemetry_samples(thread_id,turn_id,sample_id,ts,data)
        SELECT ?,?,?,?,? WHERE EXISTS(SELECT 1 FROM telemetry_turns WHERE thread_id=? AND turn_id=?)`)
        .run(threadId, turnId, sample.id, ts, JSON.stringify(sample), threadId, turnId);
    }
  }

  /** Store one turn's computed timing; the computation itself lives in turn-stats.ts / trace.ts. */
  recordTurnStats(threadId: string, turnId: string, stats: TurnStatsRow): void {
    this.db.prepare(`INSERT OR REPLACE INTO telemetry_turn_stats(thread_id,turn_id,timing,segments,steps,wait_ms,gen_ms,tool_ms)
      VALUES (?,?,?,?,?,?,?,?)`)
      .run(threadId, turnId, stats.timing, stats.segments, stats.steps, stats.waitMs, stats.genMs, stats.toolMs);
  }

  /** Timing already computed for the thread's turns, keyed by turn id. */
  turnStatsRows(threadId: string): Map<string, TurnStatsRow> {
    const rows = this.db.prepare('SELECT turn_id,timing,segments,steps,wait_ms,gen_ms,tool_ms FROM telemetry_turn_stats WHERE thread_id=?')
      .all(threadId) as Array<{ turn_id: string; timing: string; segments: number; steps: number; wait_ms: number; gen_ms: number; tool_ms: number }>;
    return new Map(rows.map((row) => [row.turn_id, {
      timing: row.timing === 'native' ? 'native' : 'hub',
      segments: row.segments, steps: row.steps, waitMs: row.wait_ms, genMs: row.gen_ms, toolMs: row.tool_ms,
    }]));
  }

  /**
   * Native usage per turn, summed field by field over the turn's samples. Reads only this
   * thread's rows (the primary key's prefix), never a whole event log. A field no sample
   * reported stays null; `measured` counts samples that carried any token count at all.
   */
  tokenSumsByTurn(threadId: string): Map<string, TurnTokenSums> {
    const rows = this.db.prepare(`SELECT turn_id AS turnId,
        SUM(json_extract(data,'$.input')) AS input, SUM(json_extract(data,'$.output')) AS output,
        SUM(json_extract(data,'$.total')) AS total, SUM(json_extract(data,'$.cacheRead')) AS cacheRead,
        SUM(json_extract(data,'$.cacheWrite')) AS cacheWrite, SUM(json_extract(data,'$.reasoning')) AS reasoning,
        SUM(json_extract(data,'$.input') IS NOT NULL OR json_extract(data,'$.output') IS NOT NULL
          OR json_extract(data,'$.total') IS NOT NULL) AS measured
      FROM telemetry_samples WHERE thread_id=? GROUP BY turn_id`).all(threadId) as any[];
    const out = new Map<string, TurnTokenSums>();
    for (const row of rows) {
      out.set(row.turnId, {
        input: row.input ?? null, output: row.output ?? null, total: row.total ?? null,
        cacheRead: row.cacheRead ?? null, cacheWrite: row.cacheWrite ?? null, reasoning: row.reasoning ?? null,
        measured: row.measured ?? 0,
      });
    }
    return out;
  }

  private samples(since: string, until: string, agent: string) {
    const rows = this.db.prepare(`SELECT s.*,t.agent,t.configured_model,
      EXISTS(SELECT 1 FROM telemetry_samples c WHERE c.thread_id=s.thread_id AND c.turn_id=s.turn_id
        AND json_extract(c.data,'$.scope')='turn' AND json_extract(c.data,'$.cost.amount') IS NOT NULL) AS turn_cost
      FROM telemetry_samples s JOIN telemetry_turns t USING(thread_id,turn_id)
      WHERE s.ts>=? AND s.ts<=? AND (?='' OR t.agent=?) ORDER BY s.ts DESC`).all(since, until, agent, agent) as any[];
    return rows.map(row => ({ ...row, sample: JSON.parse(row.data) as UsageSample }));
  }

  private unmeasuredTurns(since: string, until: string, agent: string): number {
    return (this.db.prepare(`SELECT COUNT(*) AS n FROM telemetry_turns t
      WHERE started_at>=? AND started_at<=? AND (?='' OR agent=?) AND NOT EXISTS(
        SELECT 1 FROM telemetry_samples s WHERE s.thread_id=t.thread_id AND s.turn_id=t.turn_id
        AND s.ts>=? AND s.ts<=? AND (json_extract(s.data,'$.total') IS NOT NULL
          OR json_extract(s.data,'$.input') IS NOT NULL OR json_extract(s.data,'$.output') IS NOT NULL
          OR json_extract(s.data,'$.cost.amount') IS NOT NULL))`).get(since, until, agent, agent, since, until) as { n: number }).n;
  }

  costReport(hours = 24, agent = '', now = Date.now()) {
    const since = new Date(now - hours * 3600000).toISOString(), until = new Date(now).toISOString();
    return summarizeCosts(this.samples(since, until, agent).map(row => ({
      sample: row.sample, configuredModel: row.configured_model, at: row.ts, turnCost: !!row.turn_cost,
    })), this.unmeasuredTurns(since, until, agent), now, true);
  }

  /**
   * Tokens per calendar day over the last year, for the activity heatmap, in the viewer's time
   * zone: the host may well keep UTC while its user is a day boundary away. Only days with a turn
   * are listed; a day whose turns reported no tokens keeps them unknown (null), not zero.
   * `since` is the first day anything was recorded at all: before it the days are unknown too.
   */
  daily(agent = '', timeZone?: string, now = Date.now()) {
    const from = new Date(now - DAILY_DAYS * 86400000).toISOString(), until = new Date(now).toISOString();
    // en-CA writes dates as YYYY-MM-DD. Throws a RangeError on a zone it does not know.
    const format = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' });
    // SQLite only knows UTC and the host's zone, so it sums ten-minute slices and the day is named
    // here: fine enough for the zones that sit half or three quarters of an hour off.
    const dayOf = (slice: string) => format.format(new Date(`${slice}0:00Z`));
    const days = new Map<string, { date: string; total: number | null; output: number | null; turns: number }>();
    const day = (date: string) => {
      let entry = days.get(date);
      if (!entry) days.set(date, entry = { date, total: null, output: null, turns: 0 });
      return entry;
    };
    const add = (a: number | null, b: number | null) => b == null ? a : (a ?? 0) + b;
    const turns = this.db.prepare(`SELECT substr(started_at,1,15) AS slice,COUNT(*) AS turns FROM telemetry_turns
      WHERE started_at>=? AND started_at<=? AND (?='' OR agent=?) GROUP BY slice`).all(from, until, agent, agent) as Array<{ slice: string; turns: number }>;
    for (const row of turns) day(dayOf(row.slice)).turns += row.turns;
    const tokens = this.db.prepare(`SELECT substr(s.ts,1,15) AS slice,
        SUM(json_extract(s.data,'$.total')) AS total,SUM(json_extract(s.data,'$.output')) AS output
      FROM telemetry_samples s JOIN telemetry_turns t USING(thread_id,turn_id)
      WHERE s.ts>=? AND s.ts<=? AND (?='' OR t.agent=?) GROUP BY slice`).all(from, until, agent, agent) as Array<{ slice: string; total: number | null; output: number | null }>;
    for (const row of tokens) {
      const entry = day(dayOf(row.slice));
      entry.total = add(entry.total, row.total);
      entry.output = add(entry.output, row.output);
    }
    const first = this.db.prepare(`SELECT MIN(started_at) AS at FROM telemetry_turns WHERE (?='' OR agent=?)`).get(agent, agent) as { at: string | null };
    return { checkedAt: until, since: first.at ? format.format(new Date(first.at)) : null, days: [...days.values()].sort((a, b) => a.date.localeCompare(b.date)) };
  }

  read(hours = 24, agent = '', now = Date.now()) {
    const since = new Date(now - hours * 3600000).toISOString();
    const until = new Date(now).toISOString();
    const turns = this.db.prepare(`SELECT t.*,h.title FROM telemetry_turns t JOIN threads h ON h.id=t.thread_id
      WHERE t.started_at>=? AND t.started_at<=? AND (?='' OR t.agent=?) ORDER BY t.started_at DESC LIMIT 100`)
      .all(since, until, agent, agent) as any[];
    const samples = this.samples(since, until, agent);
    const costSummary = summarizeCosts(samples.map(row => ({
      sample: row.sample, configuredModel: row.configured_model, at: row.ts, turnCost: !!row.turn_cost,
    })), this.unmeasuredTurns(since, until, agent), now);
    const totals: Record<string, number | null> = { input: null, output: null, total: null, cacheRead: null, cacheWrite: null, reasoning: null };
    const costs: Record<string, { amount: number; estimated: boolean }> = {};
    let tpm = 0, outputTpm = 0, measured = 0, outputMeasured = 0, estimated = false;
    // Buckets sit on clock boundaries so points stay put between refreshes.
    const step = (STEP_MINUTES[hours] ?? Math.max(1, Math.ceil(hours * 60 / 144))) * 60000;
    const first = Math.floor((now - hours * 3600000) / step) * step;
    const count = Math.max(1, Math.ceil((now - first) / step));
    const buckets = new Array<number>(count).fill(0);
    const outputBuckets = new Array<number>(count).fill(0);
    const byAgent = new Map<string, { total: number[]; output: number[]; hasTotal: boolean; hasOutput: boolean }>();
    const byTurn = new Map<string, Array<UsageSample & { observedAt: string }>>();
    const turnKey = (thread: string, turn: string) => JSON.stringify([thread, turn]);
    for (const row of turns) byTurn.set(turnKey(row.thread_id, row.turn_id), []);
    // Allocate a measured amount across its estimated interval, clipped to the selected window.
    const distribute = (value: number, start: number, end: number, target: number[]) => {
      if (end <= start) {
        const slot = Math.max(0, Math.min(count - 1, Math.floor((end - first) / step)));
        target[slot] += value;
        return;
      }
      const low = Math.max(0, Math.floor((start - first) / step));
      const high = Math.min(count - 1, Math.floor((end - first) / step));
      for (let i = low; i <= high; i++) {
        const overlap = Math.max(0, Math.min(end, first + (i + 1) * step, now) - Math.max(start, first + i * step, now - hours * 3600000));
        target[i] += value * overlap / (end - start);
      }
    };
    for (const row of samples) {
      const sample = row.sample;
      byTurn.get(turnKey(row.thread_id, row.turn_id))?.push({ ...sample, observedAt: row.ts });
      for (const key of Object.keys(totals)) {
        const value = sample[key as keyof UsageSample];
        if (typeof value === 'number') totals[key] = (totals[key] ?? 0) + value;
      }
      const at = Date.parse(row.ts);
      const end = sample.endedAt ? Math.min(at, Date.parse(sample.endedAt)) : at;
      const start = sample.startedAt ? Math.min(end, Date.parse(sample.startedAt)) : end;
      const fraction = end > start ? Math.max(0, Math.min(end, now) - Math.max(start, now - 60000)) / (end - start) : Number(end > now - 60000);
      if (sample.total !== undefined || sample.output !== undefined) {
        estimated ||= sample.timing === 'estimated' && end > start;
        let instance = byAgent.get(row.agent);
        if (!instance) {
          instance = { total: new Array<number>(count).fill(0), output: new Array<number>(count).fill(0), hasTotal: false, hasOutput: false };
          byAgent.set(row.agent, instance);
        }
        if (sample.total !== undefined) {
          measured++; instance.hasTotal = true;
          tpm += sample.total * fraction;
          distribute(sample.total, start, end, buckets);
          distribute(sample.total, start, end, instance.total);
        }
        if (sample.output !== undefined) {
          outputMeasured++; instance.hasOutput = true;
          outputTpm += sample.output * fraction;
          distribute(sample.output, start, end, outputBuckets);
          distribute(sample.output, start, end, instance.output);
        }
      }
      if (sample.cost) {
        const previous = costs[sample.cost.currency] ?? { amount: 0, estimated: false };
        costs[sample.cost.currency] = { amount: previous.amount + sample.cost.amount, estimated: previous.estimated || sample.cost.estimated };
      }
    }
    const counts = this.db.prepare(`SELECT COUNT(*) AS turns,SUM(status='error') AS errors FROM telemetry_turns
      WHERE started_at>=? AND started_at<=? AND (?='' OR agent=?)`).get(since, until, agent, agent) as { turns: number; errors: number | null };
    const rows = turns.map(row => ({
      threadId: row.thread_id as string, turnId: row.turn_id as string, title: row.title as string, agent: row.agent as string,
      configuredModel: row.configured_model as string | null, linkedAccount: row.linked_account as string | null,
      startedAt: row.started_at as string, finishedAt: row.finished_at as string | null,
      firstOutputMs: row.first_output_ms as number | null, status: row.status as string, error: row.error as string | null,
      samples: byTurn.get(turnKey(row.thread_id, row.turn_id)) ?? [],
    }));
    // Each point is the bucket's average TPM; the range edges cut the first and last bucket short, at least one minute.
    const minutes = buckets.map((_, i) => {
      const start = first + i * step;
      return Math.max(1, (Math.min(start + step, now) - Math.max(start, now - hours * 3600000)) / 60000);
    });
    const points = buckets.map((tokens, i) => ({ at: new Date(first + i * step).toISOString(), tpm: Math.round(tokens / minutes[i]), outputTpm: outputMeasured ? Math.round(outputBuckets[i] / minutes[i]) : null }));
    // The same buckets per instance, only for instances that reported usage in the range.
    const agents = [...byAgent].map(([id, tokens]) => ({ agent: id, tpm: tokens.hasTotal ? tokens.total.map((t, i) => Math.round(t / minutes[i])) : null, outputTpm: tokens.hasOutput ? tokens.output.map((t, i) => Math.round(t / minutes[i])) : null }));
    return { checkedAt: until, hours, totals, costs, costSummary, tpm: measured ? Math.round(tpm) : null, outputTpm: outputMeasured ? Math.round(outputTpm) : null, measuredSamples: measured, outputMeasuredSamples: outputMeasured,
      sampleCount: samples.length, turns: counts.turns, errors: counts.errors ?? 0, series: { stepMinutes: step / 60000, points, agents, estimated }, rows };
  }
}
