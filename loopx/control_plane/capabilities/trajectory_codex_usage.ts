/** Read-only Codex session adapter for benchmark-toolkit, separate from model judgments. */
import {EffectRuntimeRequestError} from '../effect_runtime_errors.ts';
import type {
  ContextPolicyOperatorName,
  ContextPolicyTelemetry,
  PrefixKvRegimeBreakdown,
  TrajectoryPartialTurnUsage,
  TrajectorySolverUsage,
} from './trajectory_assessment_types.ts';
import {isIdentifiedTurnAbort, reconcileTrajectoryRequests, summarizeTrajectoryLoopXInput} from './trajectory_request_usage.ts';
import {estimateTrajectoryLoopXInput} from './trajectory_input_usage.ts';
import {summarizeTrajectoryToolContext} from './trajectory_tool_context.ts';
import {summarizeTrajectoryLoopXRequests} from './trajectory_loopx_requests.ts';

const fail = (message: string): never => { throw new EffectRuntimeRequestError(`trajectory usage: ${message}`); };
const counterKeys = ['input_tokens', 'cached_input_tokens', 'output_tokens'] as const;
type Counters = Record<typeof counterKeys[number], number>;
function counters(value: unknown): Counters {
  const x = value as Record<string, unknown> | undefined;
  if (!x || counterKeys.some(k => typeof x[k] !== 'number' || !Number.isSafeInteger(x[k]) || (x[k] as number) < 0)) return fail('missing or invalid counters');
  if ((x.cached_input_tokens as number) > (x.input_tokens as number)) return fail('cached input exceeds input');
  return Object.fromEntries(counterKeys.map(k => [k, x[k]])) as Counters;
}
const subtract = (a: Counters, b: Counters): Counters => Object.fromEntries(counterKeys.map(k => [k, a[k] - b[k]])) as Counters;
const zero = (): Counters => ({input_tokens: 0, cached_input_tokens: 0, output_tokens: 0});
const time = (v: unknown): number => {
  if (typeof v !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(v) || !Number.isFinite(Date.parse(v))) return fail('timestamp must include timezone');
  return Date.parse(v);
};
type ActiveTurn = {turn_id: string; start: string; start_ms: number; first_line: number; baseline: Counters; samples: number};

function partialTurn(turn: ActiveTurn, observedThrough: string, lastLine: number, total: Counters): TrajectoryPartialTurnUsage {
  const usage = subtract(total, turn.baseline);
  if (usage.cached_input_tokens > usage.input_tokens) return fail('invalid delta cache accounting');
  return {turn_id: turn.turn_id, start: turn.start, observed_through: observedThrough,
    first_line: turn.first_line, last_line: lastLine, counter_observations: turn.samples,
    usage: turn.samples ? {...usage, uncached_input_tokens: usage.input_tokens - usage.cached_input_tokens} : null};
}

export function summarizeTrajectoryCodexUsage(params: Record<string, unknown>): TrajectorySolverUsage {
  if (!Array.isArray(params.events) || typeof params.session_id !== 'string' || !params.session_id) return fail('session events and expected identity required');
  if (params.include_incomplete !== undefined && typeof params.include_incomplete !== 'boolean') return fail('include_incomplete must be boolean');
  const observed = params.include_incomplete === true;
  const rows = params.events;
  let start = '', terminalThrough = '', observedThrough = '', observedLine = 0;
  let sessionSeen = false, previousTime = -Infinity, previous = zero(), samples = 0;
  let active: ActiveTurn | null = null, interruptedSeconds = 0;
  const turns: TrajectorySolverUsage['turns'] = [];
  const interruptedTurns: TrajectoryPartialTurnUsage[] = [];
  const tokenCountDeltas: Array<{input_tokens: number; cached_input_tokens: number}> = [];
  const started = new Set<string>();
  let previousLine = 0;
  for (const event of rows) {
    if (!event || typeof event !== 'object' || Array.isArray(event)) return fail('invalid event');
    if (!Number.isSafeInteger(event.line) || event.line <= previousLine) return fail('invalid source line order');
    previousLine = event.line;
    const p = event.payload;
    if (event.type === 'session_meta') {
      if (event.line !== 1 || sessionSeen || p?.id !== params.session_id || p?.forked_from_id) return fail('expected one complete, unforked session');
      sessionSeen = true;
      continue;
    }
    if (!sessionSeen) return fail('session header missing');
    if (event.type !== 'event_msg') continue;
    const interrupted = isIdentifiedTurnAbort(p);
    if (!interrupted && !['task_started', 'task_complete', 'token_count'].includes(p?.type)) continue;
    const stamp = time(event.timestamp);
    if (stamp < previousTime) return fail('out-of-order events');
    previousTime = stamp;
    observedThrough = event.timestamp; observedLine = event.line;
    if (p.type === 'task_started') {
      if (active || typeof p.turn_id !== 'string' || !p.turn_id || started.has(p.turn_id)) return fail('duplicate or overlapping Turn');
      start ||= event.timestamp;
      started.add(p.turn_id);
      active = {turn_id: p.turn_id, start: event.timestamp, start_ms: stamp, first_line: event.line, baseline: {...previous}, samples: 0};
    } else if (interrupted) {
      if (!active || p.turn_id !== active.turn_id) return fail('unmatched aborted Turn');
      if (!observed) return fail('capture includes an aborted Turn; use observed usage');
      interruptedTurns.push(partialTurn(active, observedThrough, observedLine, previous));
      interruptedSeconds += (stamp - active.start_ms) / 1000;
      terminalThrough = event.timestamp;
      active = null;
    } else if (p.type === 'token_count' && p.info) {
      if (!active) return fail('usage outside a captured Turn');
      const total = counters(p.info.total_token_usage), last = counters(p.info.last_token_usage);
      const delta = subtract(total, previous);
      if (samples === 0 && counterKeys.some(k => total[k] !== last[k])) return fail('missing initial usage prefix');
      if (counterKeys.some(k => delta[k] < 0)) return fail('counter reset; do not silently splice sessions');
      // Repeated observations are legal. A changed total must represent exactly
      // the newly observed request, including the first request after zero.
      if (counterKeys.some(k => delta[k] !== 0) && counterKeys.some(k => delta[k] !== last[k])) return fail('missing usage prefix or counter gap');
      if (counterKeys.some(k => delta[k] !== 0)) {
        tokenCountDeltas.push({input_tokens: delta.input_tokens, cached_input_tokens: delta.cached_input_tokens});
      }
      previous = total; samples++; active.samples++;
    } else if (p.type === 'task_complete') {
      if (!active || p.turn_id !== active.turn_id || !active.samples) return fail('unmatched or unmeasured completed Turn');
      const usage = subtract(previous, active.baseline);
      if (usage.cached_input_tokens > usage.input_tokens) return fail('invalid delta cache accounting');
      turns.push({turn_id: active.turn_id, start: active.start, end: event.timestamp, first_line: active.first_line,
        last_line: event.line, elapsed_seconds: (stamp - active.start_ms) / 1000,
        usage: {...usage, uncached_input_tokens: usage.input_tokens - usage.cached_input_tokens}});
      terminalThrough = event.timestamp;
      active = null;
    }
  }
  if (!observed && (active || !turns.length)) return fail('capture must end after a completed Turn');
  if (!samples) return fail('no measured usage in captured session');
  const incomplete = active ? partialTurn(active, observedThrough, observedLine, previous) : null;
  const end = active ? observedThrough : terminalThrough;
  const requestCoverage = reconcileTrajectoryRequests(rows, params.session_id);
  const loopxInput = summarizeTrajectoryLoopXInput(rows, params.session_id, requestCoverage)
    ?? estimateTrajectoryLoopXInput(params.loopx_input_trace, params.session_id, params.input_goal_id, rows, requestCoverage);
  const toolContext = summarizeTrajectoryToolContext(params.tool_context);
  const loopxRequests = toolContext?.status === 'unavailable'
    ? {method: 'codex_ordered_loopx_requests_v1' as const, status: 'unavailable' as const, reason: toolContext.reason}
    : summarizeTrajectoryLoopXRequests(rows, params.tool_context, requestCoverage);
  const contextPolicyTelemetry = summarizeContextPolicyTelemetryIfRequested(rows, params, requestCoverage, tokenCountDeltas);
  return {session_id: params.session_id, method: observed ? 'codex_observed_session_counters_v1' : 'codex_complete_session_counters_v1',
    ...(requestCoverage ? {request_coverage: requestCoverage} : {}),
    ...(loopxInput ? {loopx_input: loopxInput} : {}),
    ...(toolContext ? {tool_context: toolContext} : {}),
    ...(loopxRequests ? {loopx_requests: loopxRequests} : {}),
    ...(contextPolicyTelemetry ? {context_policy_telemetry: contextPolicyTelemetry} : {}),
    ...(observed ? {incomplete_turn: incomplete} : {}),
    ...(interruptedTurns.length ? {interrupted_turns: interruptedTurns} : {}),
    start, end, completed_turns: turns.length, counter_observations: samples,
    input_tokens: previous.input_tokens, cached_input_tokens: previous.cached_input_tokens,
    uncached_input_tokens: previous.input_tokens - previous.cached_input_tokens, output_tokens: previous.output_tokens,
    elapsed_seconds: (time(end) - time(start)) / 1000,
    turn_elapsed_seconds: turns.reduce((sum, t) => sum + t.elapsed_seconds, interruptedSeconds)
      + (active ? (time(end) - active.start_ms) / 1000 : 0), turns,
    limitations: [observed
      ? 'Observed counters only, including any unfinished final Turn; unpersisted requests and later usage remain unknown. No termination or complete cost is inferred.'
      : 'Complete captured session only; donor/prefix study costs and evaluator costs are not included.',
      ...(observed ? ['Donor/prefix study costs and evaluator costs are not included.'] : []),
      'Input includes cached input. Output includes any reasoning tokens. No dollar or generation-time estimate.',
      'Elapsed time includes tools and orchestration; it does not measure CPU time or avoidable overhead.',
      'Registered operator binding identifies the case; this does not independently prove run provenance.']};
}

function nonNegativeInt(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

export function summarizeContextPolicyTelemetry(params: {
  policy_events?: unknown;
  rows?: ReadonlyArray<{type?: unknown; payload?: any}>;
  request_coverage?: TrajectorySolverUsage['request_coverage'];
  token_count_deltas?: ReadonlyArray<{input_tokens: number; cached_input_tokens: number}>;
}): ContextPolicyTelemetry {
  const operator_counts: Record<ContextPolicyOperatorName, number> = {
    KeepRaw: 0,
    FoldToReceipt: 0,
    EpochReset: 0,
  };
  let quiet_turn_skips = 0;
  let attempts = 0;
  let latency_ms = 0;
  let output_bytes = 0;
  let bytes_saved = 0;
  let round_trips_saved = 0;

  const eventsList = Array.isArray(params.policy_events) ? params.policy_events : [];
  for (const raw of eventsList) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue;
    const item = raw as Record<string, unknown>;
    const op = item.operator;
    if (op === 'KeepRaw' || op === 'FoldToReceipt' || op === 'EpochReset') {
      operator_counts[op] += 1;
    }
    if (item.quiet_turn_skipped === true || item.skipped === true) quiet_turn_skips += 1;
    else quiet_turn_skips += nonNegativeInt(item.quiet_turn_skips);
    if (item.prefetch_attempted === true || typeof item.latency_ms === 'number' || typeof item.prefetch_latency_ms === 'number') {
      attempts += 1;
    }
    latency_ms += nonNegativeInt(item.latency_ms ?? item.prefetch_latency_ms);
    output_bytes += nonNegativeInt(item.output_bytes ?? item.retained_bytes ?? item.prefetch_bytes);
    bytes_saved += nonNegativeInt(item.bytes_saved ?? item.prefetch_bytes_saved);
    round_trips_saved += nonNegativeInt(item.round_trips_saved);
  }

  let prefix_kv: PrefixKvRegimeBreakdown | undefined;
  const rows = Array.isArray(params.rows) ? params.rows : [];
  const requestRows = rows.filter(r => r?.type === 'token_usage_record' && r.payload && typeof r.payload.usage === 'object');
  if (requestRows.length) {
    const cold = {requests: 0, uncached_prefix_kv_tokens: 0, cached_prefix_kv_tokens: 0};
    const warm = {requests: 0, uncached_prefix_kv_tokens: 0, cached_prefix_kv_tokens: 0};
    const compactions = new Set(rows.filter(r => r?.type === 'compacted').map(r => r.payload?.compaction_response_id));
    let nextIsColdRebase = true;
    const seen = new Set<string>();
    for (const row of requestRows) {
      const p = row.payload!;
      if (typeof p.response_id === 'string') {
        if (seen.has(p.response_id)) continue;
        seen.add(p.response_id);
      }
      if (compactions.has(p.response_id)) {
        nextIsColdRebase = true;
        continue;
      }
      const usage = p.usage as Record<string, unknown>;
      const inputTokens = nonNegativeInt(usage.input_tokens);
      const cachedTokens = nonNegativeInt(usage.cached_input_tokens);
      const uncachedTokens = Math.max(0, inputTokens - cachedTokens);
      const explicitRegime = p.cache_regime;
      const isCold = explicitRegime === 'cold' || (explicitRegime !== 'warm' && (cachedTokens === 0 || nextIsColdRebase));
      const target = isCold ? cold : warm;
      target.requests += 1;
      target.uncached_prefix_kv_tokens += uncachedTokens;
      target.cached_prefix_kv_tokens += cachedTokens;
      nextIsColdRebase = false;
    }
    prefix_kv = {cold, warm};
  } else if (Array.isArray(params.token_count_deltas) && params.token_count_deltas.length) {
    const cold = {requests: 0, uncached_prefix_kv_tokens: 0, cached_prefix_kv_tokens: 0};
    const warm = {requests: 0, uncached_prefix_kv_tokens: 0, cached_prefix_kv_tokens: 0};
    params.token_count_deltas.forEach((delta, index) => {
      const inp = nonNegativeInt(delta.input_tokens);
      const cached = Math.min(inp, nonNegativeInt(delta.cached_input_tokens));
      const uncached = Math.max(0, inp - cached);
      const target = index === 0 || cached === 0 ? cold : warm;
      target.requests += 1;
      target.uncached_prefix_kv_tokens += uncached;
      target.cached_prefix_kv_tokens += cached;
    });
    prefix_kv = {cold, warm};
  } else if (params.request_coverage && params.request_coverage.status === 'verified') {
    const all = params.request_coverage.all_requests;
    const uncached = Math.max(0, all.input_tokens - all.cached_input_tokens);
    prefix_kv = {
      cold: {
        requests: all.cached_input_tokens === 0 ? params.request_coverage.unique_requests : 0,
        uncached_prefix_kv_tokens: all.cached_input_tokens === 0 ? uncached : 0,
        cached_prefix_kv_tokens: 0,
      },
      warm: {
        requests: all.cached_input_tokens > 0 ? params.request_coverage.unique_requests : 0,
        uncached_prefix_kv_tokens: all.cached_input_tokens > 0 ? uncached : 0,
        cached_prefix_kv_tokens: all.cached_input_tokens,
      },
    };
  }
  return {
    schema_version: 'loopx_context_policy_telemetry_v0',
    operator_counts,
    quiet_turn_skips,
    prefetch: {attempts, latency_ms, output_bytes, bytes_saved, round_trips_saved},
    ...(prefix_kv ? {prefix_kv} : {}),
  };
}

function summarizeContextPolicyTelemetryIfRequested(
  rows: ReadonlyArray<{type?: unknown; payload?: any}>,
  params: Record<string, unknown>,
  requestCoverage: TrajectorySolverUsage['request_coverage'],
  tokenCountDeltas: ReadonlyArray<{input_tokens: number; cached_input_tokens: number}>,
): ContextPolicyTelemetry | undefined {
  const inlineEvents = rows
    .filter(r => r?.type === 'loopx_context_policy' && r.payload && typeof r.payload === 'object')
    .map(r => r.payload);
  const supplied = Array.isArray(params.context_policy) ? params.context_policy : undefined;
  const includeKv = params.include_prefix_kv_regimes === true;
  if (!supplied && !inlineEvents.length && !includeKv) return undefined;
  return summarizeContextPolicyTelemetry({
    policy_events: [...(supplied ?? []), ...inlineEvents],
    rows,
    request_coverage: requestCoverage,
    token_count_deltas: tokenCountDeltas,
  });
}
