/** Read-only human observations for the existing research pack; no gate authority. */
import {EffectRuntimeRequestError} from '../effect_runtime_errors.ts';
import type {AssessmentCitation, TrajectoryInput} from './trajectory_assessment_types.ts';

export const HUMAN_ATTENTION_KINDS = ['clarification', 'correction', 'review', 'approval', 'status', 'other'] as const;
export type HumanAttentionKind = typeof HUMAN_ATTENTION_KINDS[number];
export type HumanAttentionNecessity = 'required' | 'avoidable' | 'unknown';
export interface HumanAttentionEvent {
  event_id: string; episode_id: string | null; kind: HumanAttentionKind;
  necessity: HumanAttentionNecessity; occurred_at: string;
  active: {start: string; end: string} | null;
  request: {start: string; end: string | null} | null;
  citation: AssessmentCitation;
}
export interface TrajectoryHumanAttention {
  method: 'source_bound_human_attention_v1'; source_id: string;
  start: string; end: string; complete: boolean; events: HumanAttentionEvent[];
  total: number; counts: Record<HumanAttentionKind, number>;
  required: number; avoidable: number; unknown: number; unassigned: number;
  active_ms: number | null; active_covered: number;
  wait_ms: number | null; wait_covered: number; pending_requests: number;
  optimization_points: Array<{kind: HumanAttentionKind; event_ids: string[]}>;
}
function fail(message: string): never {throw new EffectRuntimeRequestError(message);}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('human attention requires objects');
  return value as Record<string, unknown>;
}
function time(value: unknown): string {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(value)
      || !Number.isFinite(Date.parse(value))) fail('human attention requires UTC timestamps');
  const canonical = value.slice(0, 19) + '.' + (value.slice(19, -1).slice(1) || '').padEnd(3, '0') + 'Z';
  if (new Date(value).toISOString() !== canonical) fail('human attention has an invalid calendar timestamp');
  return value;
}
function union(intervals: Array<{start: string; end: string}>): number | null {
  if (!intervals.length) return null;
  const ordered = intervals.map(x => [Date.parse(x.start), Date.parse(x.end)]).sort((a,b) => a[0]! - b[0]!);
  let [start, end] = ordered[0]!, total = 0;
  for (const [nextStart, nextEnd] of ordered.slice(1)) {
    if (nextStart! <= end!) end = Math.max(end!, nextEnd!);
    else {total += end! - start!; start = nextStart; end = nextEnd;}
  }
  return total + end! - start!;
}

/** Capture is prepared by a trusted Agent/provider from human receipts or explicit
 * observations. Binding proves provenance, not that a classification is correct. */
export function projectTrajectoryHumanAttention(input: TrajectoryInput): TrajectoryHumanAttention | undefined {
  const sourceId = input.human_attention_source_id;
  if (sourceId === undefined) return undefined;
  const source = input.sources.find(s => s.id === sourceId);
  if (!source || source.first_line !== 1) fail('human attention requires a complete registered source');
  let value: unknown;
  try {value = JSON.parse(source.text);} catch {fail('human attention requires a JSON capture');}
  const raw = object(value), scope = object(raw.scope);
  if (raw.schema_version !== 'trajectory_human_attention_v1'
      || Object.entries(input.scope).some(([key,v]) => scope[key] !== v)) fail('human attention scope mismatch');
  const start = time(raw.start), end = time(raw.end);
  if (Date.parse(end) < Date.parse(start) || typeof raw.complete !== 'boolean') fail('human attention needs a window and explicit coverage');
  if (!Array.isArray(raw.events) || raw.events.length > 10000) fail('human attention requires at most 10000 events');
  const within = (t: string) => Date.parse(t) >= Date.parse(start) && Date.parse(t) <= Date.parse(end);
  const interval = (v: unknown, open: boolean) => {
    if (v === null) return null;
    const x = object(v), s = time(x.start), e = open && x.end === null ? null : time(x.end);
    if (!within(s) || e !== null && (!within(e) || Date.parse(e) < Date.parse(s))) fail('human attention interval outside capture');
    return {start: s, end: e};
  };
  const seen = new Map<string, HumanAttentionEvent>();
  for (const v of raw.events) {
    const e = object(v);
    if (typeof e.event_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(e.event_id)) fail('human attention needs a stable event ID');
    if (!HUMAN_ATTENTION_KINDS.includes(e.kind as HumanAttentionKind)
        || !['required', 'avoidable', 'unknown'].includes(String(e.necessity))) fail('human attention classification must be explicit');
    if (e.episode_id !== null && !input.episodes.some(x => x.id === e.episode_id)) fail('human attention has an unknown episode');
    const occurred = time(e.occurred_at);
    if (!within(occurred)) fail('human attention event outside capture');
    const c = object(e.citation), evidence = input.sources.find(s => s.id === c.source_id);
    if (!evidence || evidence.id === sourceId || !Number.isSafeInteger(c.first_line) || !Number.isSafeInteger(c.last_line)
        || Number(c.first_line) < evidence.first_line || Number(c.last_line) < Number(c.first_line)
        || Number(c.last_line) >= evidence.first_line + evidence.text.split('\n').length) fail('human attention citation outside independent registered evidence');
    if (e.episode_id !== null && !input.episodes.find(x => x.id === e.episode_id)!.source_ids.includes(evidence.id)) {
      fail('human attention citation outside assigned episode evidence');
    }
    const active = interval(e.active, false) as HumanAttentionEvent['active'];
    const request = interval(e.request, true);
    const event: HumanAttentionEvent = {event_id: e.event_id, episode_id: e.episode_id as string | null,
      kind: e.kind as HumanAttentionKind, necessity: e.necessity as HumanAttentionNecessity, occurred_at: occurred,
      active, request, citation: {source_id: evidence.id, first_line: Number(c.first_line), last_line: Number(c.last_line)}};
    const old = seen.get(event.event_id);
    if (old && JSON.stringify(old) !== JSON.stringify(event)) fail('human attention event ID has conflicting observations');
    seen.set(event.event_id, event); // Identical persisted replay is one human event.
  }
  const events = [...seen.values()].sort((a,b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at) || a.event_id.localeCompare(b.event_id));
  // A pending request is observed demand, not a completed human input or decision.
  const inputs = events.filter(e => e.request?.end !== null);
  const counts = Object.fromEntries(HUMAN_ATTENTION_KINDS.map(k => [k, inputs.filter(e => e.kind === k).length])) as Record<HumanAttentionKind, number>;
  const active = events.flatMap(e => e.active ? [e.active] : []);
  const waits = events.flatMap(e => e.request && e.request.end !== null ? [{start: e.request.start, end: e.request.end}] : []);
  return {method: 'source_bound_human_attention_v1', source_id: sourceId, start, end, complete: raw.complete,
    events, total: inputs.length, counts,
    required: inputs.filter(e => e.necessity === 'required').length,
    avoidable: inputs.filter(e => e.necessity === 'avoidable').length,
    unknown: inputs.filter(e => e.necessity === 'unknown').length,
    unassigned: inputs.filter(e => e.episode_id === null).length,
    active_ms: union(active), active_covered: active.length, wait_ms: union(waits), wait_covered: waits.length,
    pending_requests: events.filter(e => e.request?.end === null).length,
    optimization_points: HUMAN_ATTENTION_KINDS.flatMap(kind => {
      const event_ids = inputs.filter(e => e.kind === kind && e.necessity === 'avoidable').map(e => e.event_id);
      return event_ids.length ? [{kind, event_ids}] : [];
    }).sort((a,b) => b.event_ids.length - a.event_ids.length)};
}
