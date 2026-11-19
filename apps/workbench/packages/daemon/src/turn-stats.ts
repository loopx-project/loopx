import type { TurnTrace } from './trace.js';

/**
 * Session statistics reduced from per-turn data: what the top-bar readings and the token popover
 * show. Timing comes from the stored per-turn rows (computed from each turn's trace once), tokens
 * are summed from the thread's native usage samples; both are reduced here, field by field, with
 * unknown kept unknown — a field nobody measured stays null and the interface hides it.
 */

/** Per-turn timing, stored in `telemetry_turn_stats` when the turn ends. Tokens are not stored. */
export interface TurnStatsRow {
  /** 'hub' when any of the turn's segments was timed from the hub's event log. */
  timing: 'native' | 'hub';
  segments: number;
  steps: number;
  /** Summed wait for the first output, generation time, and the union of tool intervals. */
  waitMs: number;
  genMs: number;
  toolMs: number;
}

/** A turn's native usage, summed over its samples; a field no sample reported stays null. */
export interface TurnTokenSums {
  input: number | null;
  output: number | null;
  total: number | null;
  cacheRead: number | null;
  cacheWrite: number | null;
  reasoning: number | null;
  /** Samples that carried at least one token count; zero marks a turn without usage data. */
  measured: number;
}

/** GET /api/threads/:id/stats. Only summary numbers; detail is the trace's business. */
export interface ThreadStats {
  threadId: string;
  checkedAt: string;
  /** A turn is still running; the numbers include its live progress. */
  running: boolean;
  turns: number;
  steps: number;
  timing: null | {
    source: 'native' | 'hub';
    turns: number;
    segments: number;
    waitMs: number;
    avgWaitMs: number | null;
    genMs: number;
    modelMs: number;
    toolMs: number;
  };
  tokens: null | {
    turnsWithout: number;
    /** Normalized input: plain input plus cache reads and writes. */
    input: number | null;
    cacheRead: number | null;
    cacheWrite: number | null;
    output: number | null;
    reasoning: number | null;
    total: number | null;
  };
  tokPerSec: number | null;
  tokPerSecApprox: boolean;
  /** Compactions the agent itself completed, for the context panel's history line. */
  compactions: { count: number; last: { ts: string; automatic: boolean } | null };
}

const ms = (a: string, b: string | null | undefined): number => Date.parse(b ?? '') - Date.parse(a);

/**
 * One turn's timing from its trace. The turn's model segments carry wait and generation; tool
 * time is the union of the tool intervals, so parallel calls are not counted twice. `now` stands
 * in for the end of pieces still running (the turn's last event); null when nothing is running.
 */
export function statsOfTrace(trace: Pick<TurnTrace, 'steps' | 'segments' | 'endedAt'>, now: string | null): TurnStatsRow {
  let waitMs = 0, genMs = 0, toolMs = 0;
  let source: TurnStatsRow['timing'] = 'native';
  let segments = 0;
  for (const seg of trace.segments) {
    const end = seg.end ?? now;
    if (!end || !Number.isFinite(Date.parse(end)) || !Number.isFinite(Date.parse(seg.start))) continue;
    segments += 1;
    if (seg.source === 'hub') source = 'hub';
    const first = seg.firstOutput ?? end;
    waitMs += Math.max(0, ms(seg.start, first));
    if (seg.firstOutput) genMs += Math.max(0, ms(seg.firstOutput, end));
  }
  const intervals: Array<[number, number]> = [];
  for (const step of trace.steps) {
    if (step.kind !== 'tool') continue;
    const end = step.endTs ?? now ?? step.ts;
    const start = Date.parse(step.ts), finish = Date.parse(end);
    if (Number.isFinite(start) && Number.isFinite(finish) && finish >= start) intervals.push([start, finish]);
  }
  intervals.sort((a, b) => a[0] - b[0]);
  let reached = Number.NEGATIVE_INFINITY;
  for (const [start, finish] of intervals) {
    toolMs += Math.max(0, finish - Math.max(start, reached));
    reached = Math.max(reached, finish);
  }
  return { timing: segments ? source : 'hub', segments, steps: trace.steps.length, waitMs, genMs, toolMs };
}

const TOKEN_FIELDS = ['input', 'output', 'total', 'cacheRead', 'cacheWrite', 'reasoning'] as const;

/**
 * The thread's numbers over its turns: timing summed over turns that measured any, tokens over
 * every turn that reported usage, and output speed only over turns that measured both. Unknown
 * stays unknown per field: sums exist only where at least one sample reported the field.
 */
export function aggregateThreadStats(input: {
  threadId: string;
  checkedAt: string;
  running: boolean;
  /** The turn still running, whose usage verdict is deferred, not "without". */
  runningTurnId?: string | null;
  turns: Array<{ turnId: string }>;
  rows: Map<string, TurnStatsRow>;
  tokens: Map<string, TurnTokenSums>;
  /** Completed native compactions, read from the event log by the caller. */
  compactions: { count: number; last: { ts: string; automatic: boolean } | null };
}): ThreadStats {
  const { threadId, checkedAt, running, runningTurnId, turns, rows, tokens } = input;
  let steps = 0;
  let timedTurns = 0, segments = 0, waitMs = 0, genMs = 0, toolMs = 0, hubTimed = false;
  let measuredSamples = 0, turnsWithout = 0;
  const sums: Record<(typeof TOKEN_FIELDS)[number], number | null> = { input: null, output: null, total: null, cacheRead: null, cacheWrite: null, reasoning: null };
  let tpsOut = 0, tpsGen = 0, tpsHub = false;
  for (const turn of turns) {
    const row = rows.get(turn.turnId);
    if (row) {
      steps += row.steps;
      if (row.segments > 0 || row.toolMs > 0) {
        timedTurns += 1;
        segments += row.segments;
        waitMs += row.waitMs;
        genMs += row.genMs;
        toolMs += row.toolMs;
        if (row.timing === 'hub') hubTimed = true;
      }
      const out = tokens.get(turn.turnId)?.output;
      if (out != null && out >= 0 && row.genMs > 0) {
        tpsOut += out;
        tpsGen += row.genMs;
        if (row.timing === 'hub') tpsHub = true;
      }
    }
    const turnTokens = tokens.get(turn.turnId);
    measuredSamples += turnTokens?.measured ?? 0;
    if (turn.turnId !== runningTurnId && (!turnTokens || turnTokens.measured === 0)) turnsWithout += 1;
    for (const field of TOKEN_FIELDS) {
      const value = turnTokens?.[field];
      if (typeof value === 'number') sums[field] = (sums[field] ?? 0) + value;
    }
  }
  const timing = timedTurns ? {
    source: hubTimed ? 'hub' as const : 'native' as const,
    turns: timedTurns,
    segments,
    waitMs,
    avgWaitMs: segments ? waitMs / segments : null,
    genMs,
    modelMs: waitMs + genMs,
    toolMs,
  } : null;
  return {
    threadId,
    checkedAt,
    running,
    turns: turns.length,
    steps,
    timing,
    tokens: measuredSamples ? { turnsWithout, ...sums } : null,
    tokPerSec: tpsGen > 0 ? tpsOut / (tpsGen / 1000) : null,
    tokPerSecApprox: tpsHub,
    compactions: input.compactions,
  };
}
