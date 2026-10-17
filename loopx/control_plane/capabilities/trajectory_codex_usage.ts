/** Read-only Codex session adapter for benchmark-toolkit, separate from model judgments. */
import {EffectRuntimeRequestError} from '../effect_runtime_errors.ts';
import type {TrajectorySolverUsage} from './trajectory_assessment_types.ts';

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
export function summarizeTrajectoryCodexUsage(params: Record<string, unknown>): TrajectorySolverUsage {
  if (!Array.isArray(params.events) || typeof params.session_id !== 'string' || !params.session_id) return fail('session events and expected identity required');
  const rows = params.events;
  let sessionSeen = false, previousTime = -Infinity, previous = zero(), samples = 0;
  let active: {turn_id: string; start: string; start_ms: number; first_line: number; baseline: Counters; samples: number} | null = null;
  const turns: Array<{turn_id: string; start: string; end: string; first_line: number; last_line: number; elapsed_seconds: number; usage: Counters & {uncached_input_tokens: number}}> = [];
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
    if (event.type !== 'event_msg' || !['task_started', 'task_complete', 'token_count'].includes(p?.type)) continue;
    const stamp = time(event.timestamp);
    if (stamp < previousTime) return fail('out-of-order events');
    previousTime = stamp;
    if (p.type === 'task_started') {
      if (active || typeof p.turn_id !== 'string' || !p.turn_id || turns.some(t => t.turn_id === p.turn_id)) return fail('duplicate or overlapping Turn');
      active = {turn_id: p.turn_id, start: event.timestamp, start_ms: stamp, first_line: event.line, baseline: {...previous}, samples: 0};
    } else if (p.type === 'token_count' && p.info) {
      if (!active) return fail('usage outside a captured Turn');
      const total = counters(p.info.total_token_usage), last = counters(p.info.last_token_usage);
      const delta = subtract(total, previous);
      if (samples === 0 && counterKeys.some(k => total[k] !== last[k])) return fail('missing initial usage prefix');
      if (counterKeys.some(k => delta[k] < 0)) return fail('counter reset; do not silently splice sessions');
      // Repeated observations are legal. A changed total must represent exactly
      // the newly observed request, including the first request after zero.
      if (counterKeys.some(k => delta[k] !== 0) && counterKeys.some(k => delta[k] !== last[k])) return fail('missing usage prefix or counter gap');
      previous = total; samples++; active.samples++;
    } else if (p.type === 'task_complete') {
      if (!active || p.turn_id !== active.turn_id || !active.samples) return fail('unmatched or unmeasured completed Turn');
      const usage = subtract(previous, active.baseline);
      if (usage.cached_input_tokens > usage.input_tokens) return fail('invalid delta cache accounting');
      turns.push({turn_id: active.turn_id, start: active.start, end: event.timestamp, first_line: active.first_line,
        last_line: event.line, elapsed_seconds: (stamp - active.start_ms) / 1000,
        usage: {...usage, uncached_input_tokens: usage.input_tokens - usage.cached_input_tokens}});
      active = null;
    }
  }
  if (active || !turns.length) return fail('capture must end after a completed Turn');
  return {session_id: params.session_id, method: 'codex_complete_session_counters_v1',
    start: turns[0].start, end: turns.at(-1)!.end, completed_turns: turns.length, counter_observations: samples,
    input_tokens: previous.input_tokens, cached_input_tokens: previous.cached_input_tokens,
    uncached_input_tokens: previous.input_tokens - previous.cached_input_tokens, output_tokens: previous.output_tokens,
    elapsed_seconds: (time(turns.at(-1)!.end) - time(turns[0].start)) / 1000,
    turn_elapsed_seconds: turns.reduce((sum, t) => sum + t.elapsed_seconds, 0), turns,
    limitations: ['Complete captured session only; donor/prefix study costs and evaluator costs are not included.',
      'Input includes cached input. Output includes any reasoning tokens. No dollar or generation-time estimate.',
      'Elapsed time includes tools and orchestration; it does not measure CPU time or avoidable overhead.',
      'Registered operator binding identifies the case; this does not independently prove run provenance.']};
}
