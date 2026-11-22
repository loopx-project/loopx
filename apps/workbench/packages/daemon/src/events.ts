/**
 * Unified event model. Every adapter maps its native events onto these types,
 * so the web UI only has to understand one stream.
 */
export type EventType =
  | 'agent.notice'
  | 'handover.updated'
  | 'handover.model'
  /** The quota reset came and the daemon resent the failed turn by itself. */
  | 'handover.resume'
  | 'thread.created'
  | 'thread.updated'
  /** Current conversation changed; clients reload history without rewinding the SSE cursor. */
  | 'thread.historyChanged'
  | 'turn.started'
  /** Request milestones, with elapsedMs and sincePreviousPhaseMs assigned by the hub. */
  | 'turn.phase'
  | 'turn.done'
  | 'turn.error'
  | 'message.delta'
  /** Authoritative item text correcting its already-streamed tail. */
  | 'message.replace'
  /** Final or interrupted voice text. Streaming speech deltas use the private voice channel. */
  | 'voice.message'
  | 'voice.closed'
  | 'voice.summary.error'
  | 'reasoning.delta'
  | 'tool.start'
  | 'tool.update'
  | 'tool.end'
  | 'context.compaction'
  | 'plan.updated'
  | 'plan.delta'
  | 'plan.completed'
  | 'input.request'
  | 'input.resolved'
  | 'usage.updated'
  | 'usage.sample'
  /** The thread's native goal changed or was cleared; the card reads it. */
  | 'goal.updated'
  | 'approval.request'
  | 'approval.resolved'
  /** Deferred model capabilities changed. Broadcast only, never stored in an event log. */
  | 'agent.modelsChanged'
  /** An account's plan usage moved. Broadcast only, never stored in an event log. */
  | 'quota.updated'
  /** Something on the plan page changed. Broadcast only, never stored in an event log. */
  | 'todo.updated'
  /** The scheduled-task list changed. Broadcast only, never stored in an event log. */
  | 'schedules.changed'
  /** An extension was installed, removed, enabled or disabled. Broadcast only, never stored in an event log. */
  | 'extensions.changed';

/** Structured origin for turns started by an internal channel rather than a person. */
export type TurnPart = { kind: 'person'; text: string } | { kind: 'lark' | 'worker' | 'meeting-prep'; count: number };
/** `mixed`: several arrivals folded into one Router turn, in order; `parts` keeps them apart for display. */
export interface TurnChannel { kind: 'lark' | 'worker' | 'meeting-prep' | 'mixed'; count: number; parts?: TurnPart[] }

export interface StoredEvent {
  threadId: string;
  seq: number;
  turnId: string | null;
  type: EventType;
  ts: string;
  data: Record<string, unknown>;
}

/** An event as emitted by an adapter, before the store assigns seq and ts. */
export interface EmittedEvent {
  type: EventType;
  data: Record<string, unknown>;
}

/** Replace only the exact streamed tail identified by an authoritative completion. */
export function replaceMessageTail(text: string, data: Record<string, unknown>): string {
  return typeof data.previous === 'string' && data.previous.length > 0
    && typeof data.text === 'string' && text.endsWith(data.previous)
    ? text.slice(0, -data.previous.length) + data.text : text;
}

/**
 * Text and thinking are stored one event per streamed fragment, so a long turn is tens of
 * thousands of events that say very little each. For reading back, a run of fragments of one kind
 * in one turn is given as two events: the first as it was, and one carrying the rest of the text
 * under the last fragment's seq and time. Both ends stay, so when the run began and ended is
 * still known, and resuming after the last seq still works.
 */
export function compactDeltas(events: StoredEvent[]): StoredEvent[] {
  const out: StoredEvent[] = [];
  // Index in `out` of the first fragment of the run being read, or -1.
  let head = -1;
  for (const ev of events) {
    const isDelta = ev.type === 'message.delta' || ev.type === 'reasoning.delta';
    const first = head >= 0 ? out[head] : undefined;
    if (isDelta && first && first.type === ev.type && first.turnId === ev.turnId) {
      const rest = out.length - 1 > head ? String(out[out.length - 1].data.text ?? '') : '';
      const merged = { ...ev, data: { ...ev.data, text: rest + String(ev.data.text ?? '') } };
      if (out.length - 1 > head) out[out.length - 1] = merged;
      else out.push(merged);
      continue;
    }
    out.push(ev);
    head = isDelta ? out.length - 1 : -1;
  }
  return out;
}

/* The pieces below mirror the web timeline's reading of a tool event field by field, so that one
 * merged event leaves the timeline in exactly the state the whole run would. If the web's reading
 * changes (src/timeline.ts, applyToolData), this must change with it. */

export function hasDiffParts(content: unknown): boolean {
  return Array.isArray(content) && content.some((part) => (part as { type?: unknown } | null)?.type === 'diff');
}

function textOfContent(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((part) => {
      const p = part as { type?: unknown; text?: unknown; content?: { type?: unknown; text?: unknown } } | null;
      if (!p || p.type === 'diff') return '';
      if (p.type === 'content' && p.content?.type === 'text') return String(p.content.text ?? '');
      if (p.type === 'text' && typeof p.text === 'string') return p.text;
      return '';
    })
    .filter(Boolean)
    .join('\n');
}

export function toolText(output: unknown, content: unknown): string {
  if (output !== undefined && output !== null && output !== '') {
    return typeof output === 'string' ? output : JSON.stringify(output, null, 2);
  }
  return textOfContent(content);
}

/** One run of updates and the end of a single tool call, as one event the timeline reads the same. */
function mergedToolEvent(run: StoredEvent[]): StoredEvent {
  const last = run[run.length - 1];
  let status: unknown;
  let title: unknown;
  let kind: unknown;
  let input: unknown;
  let content: unknown;
  let text: string | undefined;
  for (const ev of run) {
    if (ev.data.status) status = ev.data.status;
    if (ev.data.title) title = ev.data.title;
    if (ev.data.kind) kind = ev.data.kind;
    if (ev.data.input !== undefined) input = ev.data.input;
    // The timeline replaces a step's diffs only when an event carries some, and its output text
    // from the last event that carries either; each field is taken from where it was last set.
    if (hasDiffParts(ev.data.content)) content = ev.data.content;
    const said = toolText(ev.data.output, ev.data.content);
    if (said) text = said;
  }
  const data: Record<string, unknown> = { id: last.data.id };
  if (status !== undefined) data.status = status;
  if (title !== undefined) data.title = title;
  if (kind !== undefined) data.kind = kind;
  if (input !== undefined) data.input = input;
  if (content !== undefined) data.content = content;
  if (text !== undefined) data.output = text;
  return { ...last, data };
}

/**
 * A tool call streaming to completion is stored as a burst of updates, each a snapshot that
 * supersedes the last, and an end; a long call under a chatty agent is hundreds of events whose
 * intermediates no reader needs. Consecutive update/end events of one call are collapsed into one
 * that carries the state the whole run leaves behind — every field from the event that last set
 * it, and the call's last seq, time and type, so durations and "still running" read the same. The
 * store keeps every intermediate; this is only how pages are served.
 */
export function coalesceToolEvents(events: Iterable<StoredEvent>): StoredEvent[] {
  const out: StoredEvent[] = [];
  // Only the current snapshot is retained, even when the source is a database cursor.
  let pending: StoredEvent | undefined;
  const flush = () => {
    if (pending) out.push(pending);
    pending = undefined;
  };
  for (const ev of events) {
    const isTool = ev.type === 'tool.update' || ev.type === 'tool.end';
    const continues = isTool && pending && pending.turnId === ev.turnId && pending.data.id === ev.data.id;
    if (continues) pending = mergedToolEvent([pending!, ev]);
    else { flush(); if (isTool) pending = ev; else out.push(ev); }
  }
  flush();
  return out;
}
