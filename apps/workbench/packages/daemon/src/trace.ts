import { mcpCallOf } from './activity.js';
import { coalesceToolEvents, compactDeltas, replaceMessageTail, type StoredEvent } from './events.js';

/**
 * One turn read back as a trace: what the user sent, what the model thought and said, each tool
 * it called with what came back, and each model request with its tokens, in the order they
 * happened. Built from the hub's own event log, the same one the thread view replays; the whole
 * system prompt is the conversation's, not the turn's, and is read separately.
 */

export type TraceStepKind = 'user' | 'thinking' | 'assistant' | 'tool' | 'request' | 'context' | 'error';

export interface TraceStep {
  kind: TraceStepKind;
  ts: string;
  /** When the step finished: the last fragment of a text, a tool's end. Absent while it runs. */
  endTs?: string;
  /** user, thinking, assistant, error, context: the text itself, cut at TEXT_CHARS. */
  text?: string;
  /** tool: the raw name (`Bash`, `mcp__github__create_issue`) and the agent's one-line title. */
  tool?: string;
  title?: string;
  server?: string;
  status?: string;
  /** tool: what it was given and what it gave back, cut at TEXT_CHARS. */
  input?: string;
  output?: string;
  /** request: the model and its token counts, as the agent reported them. */
  model?: string;
  tokens?: { input?: number; output?: number; cacheRead?: number; cacheWrite?: number };
  /** True when a text above was cut; the thread still holds all of it. */
  cut?: boolean;
}

export interface TraceTotals {
  tools: number;
  mcp: number;
  requests: number;
  input: number;
  output: number;
}

/** One model request's time: from its start to the next tool call or the turn's end. */
export interface TraceSegment {
  /** The request's real start when the agent reported it, else when the segment opened. */
  start: string;
  /** First text, thought or tool call inside the segment; null until one arrived. */
  firstOutput: string | null;
  /** When the segment closed; still open (the turn is running) reads null. */
  end: string | null;
  /** native: the agent reported the request's own timing; hub: derived from the hub's log. */
  source: 'native' | 'hub';
  /** Output tokens of the request matched to this segment; absent when none matched. */
  outputTokens?: number;
  /** The trace steps (1-based, in `steps`) whose events fall inside this segment. */
  steps: number[];
}

export interface TurnTrace {
  threadId: string;
  turnId: string;
  title: string;
  agent: string;
  cwd: string;
  /** Models the requests named, in order of first use; the thread's chosen model when none did. */
  models: string[];
  status: 'running' | 'done' | 'cancelled' | 'error';
  startedAt: string;
  endedAt: string | null;
  steps: TraceStep[];
  /** The turn's model segments, in order; the timeline draws one bar per segment and per tool. */
  segments: TraceSegment[];
  totals: TraceTotals;
  /** The thread's other turns, oldest first, so the trace can step to the one before or after. */
  turns: Array<{ turnId: string; ts: string }>;
}

/** A trace is read, not archived: a long tool output is cut, the thread keeps the whole of it. */
const TEXT_CHARS = 20_000;

function cut(text: string): { text: string; cut: boolean } {
  return text.length > TEXT_CHARS ? { text: `${text.slice(0, TEXT_CHARS)}…`, cut: true } : { text, cut: false };
}

function stringOf(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

const num = (value: unknown): number | undefined => (typeof value === 'number' && Number.isFinite(value) ? value : undefined);

/**
 * The steps of one turn's events. Streamed fragments become one text, a tool's start, updates and
 * end become one step, and every `usage.sample` is one request. Events of other turns are ignored.
 *
 * The same walk collects the turn's model segments: one opens at the turn's start or when the last
 * running tool ends, and closes at the next tool call or the turn's end; a sample carrying native
 * timing (`durationMs` and `ttftMs`, pi and zcode) replaces the event-derived bounds. A segment
 * where nothing happened at all is dropped, so chained tool calls do not fake model time.
 */
export function buildTrace(events: StoredEvent[]): { steps: TraceStep[]; totals: TraceTotals; models: string[]; status: TurnTrace['status']; endedAt: string | null; segments: TraceSegment[] } {
  const steps: TraceStep[] = [];
  const tools = new Map<string, TraceStep>();
  const compactions = new Map<string, TraceStep>();
  const totals: TraceTotals = { tools: 0, mcp: 0, requests: 0, input: 0, output: 0 };
  const models: string[] = [];
  const segments: TraceSegment[] = [];
  const runningTools = new Set<string>();
  // The segment being filled, or null while tools run or the turn is between segments. Held in an
  // object so the helpers below can fill it without TypeScript narrowing it away.
  const segment: { current: TraceSegment | null } = { current: null };
  let seenEvent = false;
  let status: TurnTrace['status'] = 'running';
  let endedAt: string | null = null;

  const openSegment = (ts: string) => {
    if (!segment.current) segment.current = { start: ts, firstOutput: null, end: null, source: 'hub', steps: [] };
  };
  const closeSegment = (ts: string) => {
    const seg = segment.current;
    if (!seg) return;
    segment.current = null;
    // Nothing arrived and no time passed (chained tools): no model time to measure, not even zero.
    if (ts === seg.start && seg.source === 'hub' && seg.outputTokens === undefined) return;
    // A native request keeps its own bounds; the hub's event clock only fills what it knows.
    if (!(seg.source === 'native' && seg.end)) seg.end = ts;
    segments.push(seg);
  };

  const setText = (step: TraceStep, text: string) => {
    const c = cut(text);
    step.text = c.text;
    if (c.cut) step.cut = true;
    else delete step.cut;
  };

  for (const ev of compactDeltas(coalesceToolEvents(events))) {
    const data = ev.data;
    // A log that starts mid-turn (the daemon restarted) still times what it can see.
    if (!seenEvent) { seenEvent = true; openSegment(ev.ts); }
    switch (ev.type) {
      case 'turn.started':
        openSegment(ev.ts);
        setText(steps[steps.push({ kind: 'user', ts: ev.ts }) - 1], stringOf(data.text));
        break;
      case 'message.delta':
      case 'reasoning.delta': {
        const cur = segment.current;
        if (cur && cur.firstOutput === null) cur.firstOutput = ev.ts;
        const kind = ev.type === 'message.delta' ? 'assistant' : 'thinking';
        const last = steps[steps.length - 1];
        // compactDeltas gives a run as its first fragment and then the rest; join them back.
        if (last?.kind === kind && last.endTs !== undefined && !last.cut) {
          setText(last, (last.text ?? '') + stringOf(data.text));
          last.endTs = ev.ts;
        } else {
          const step: TraceStep = { kind, ts: ev.ts, endTs: ev.ts };
          setText(step, stringOf(data.text));
          steps.push(step);
        }
        break;
      }
      case 'message.replace': {
        const last = [...steps].reverse().find(step => step.kind === 'assistant');
        if (last && !last.cut) setText(last, replaceMessageTail(last.text ?? '', data));
        break;
      }
      case 'tool.start':
      case 'tool.update':
      case 'tool.end': {
        const id = String(data.id ?? '');
        let step = tools.get(id);
        if (!step) {
          // The call's first event is the model's answer to the open segment, and its end.
          const cur = segment.current;
          if (cur) { if (cur.firstOutput === null) cur.firstOutput = ev.ts; closeSegment(ev.ts); }
          runningTools.add(id);
          step = { kind: 'tool', ts: ev.ts };
          tools.set(id, step);
          steps.push(step);
          totals.tools += 1;
        }
        if (typeof data.tool === 'string' && data.tool) step.tool = data.tool;
        if (typeof data.title === 'string' && data.title) step.title = data.title;
        if (typeof data.status === 'string' && data.status) step.status = data.status;
        const mcp = mcpCallOf(data);
        if (mcp && !step.server) {
          step.server = mcp.server;
          totals.mcp += 1;
        }
        const input = stringOf(data.input);
        if (input && input !== '{}') {
          const c = cut(input);
          step.input = c.text;
          if (c.cut) step.cut = true;
        }
        const output = stringOf(data.output);
        if (output) {
          const c = cut(output);
          step.output = c.text;
          if (c.cut) step.cut = true;
        }
        if (ev.type === 'tool.end') {
          step.endTs = ev.ts;
          // The next segment opens when the last running tool of this batch ends.
          runningTools.delete(id);
          if (runningTools.size === 0) openSegment(ev.ts);
        }
        break;
      }
      case 'usage.sample': {
        const model = typeof data.model === 'string' ? data.model : undefined;
        if (model && !models.includes(model)) models.push(model);
        const tokens = { input: num(data.input), output: num(data.output), cacheRead: num(data.cacheRead), cacheWrite: num(data.cacheWrite) };
        totals.requests += 1;
        totals.input += tokens.input ?? 0;
        totals.output += tokens.output ?? 0;
        const started = typeof data.startedAt === 'string' ? data.startedAt : ev.ts;
        const ended = typeof data.endedAt === 'string' ? data.endedAt : ev.ts;
        steps.push({ kind: 'request', ts: started, endTs: ended, model, tokens });
        // Request-scoped samples can match one segment; turn- and update-scoped ones cannot.
        if (data.scope === 'request') {
          const target: TraceSegment | undefined = segment.current ?? segments[segments.length - 1];
          if (target) {
            if (typeof tokens.output === 'number') target.outputTokens ??= tokens.output;
            const durationMs = num(data.durationMs), ttftMs = num(data.ttftMs);
            // Native timing needs the request's own duration and first-token wait. With a start
            // time (zcode) the bounds are the request's own; without one (pi) the sample lands at
            // message_end, so the request ended there and began a duration earlier. Only a start
            // before the turn itself is pulled back to the turn's start: whatever the duration
            // claims, the request cannot have begun before the ask.
            if (durationMs !== undefined && ttftMs !== undefined) {
              const end = typeof data.startedAt === 'string' && Number.isFinite(Date.parse(data.startedAt))
                ? Date.parse(data.startedAt) + durationMs
                : Date.parse(ev.ts);
              const floor = steps[0]?.ts ? Date.parse(steps[0].ts) : Number.NEGATIVE_INFINITY;
              if (Number.isFinite(end)) {
                const start = Math.max(end - durationMs, floor);
                target.source = 'native';
                target.start = new Date(start).toISOString();
                target.firstOutput = new Date(start + Math.max(0, ttftMs)).toISOString();
                target.end = new Date(Math.max(end, start)).toISOString();
              }
            }
          }
        }
        break;
      }
      case 'context.compaction': {
        // Codex reports one as started and again as finished; it is one step.
        const id = String(data.id ?? ev.seq);
        if (compactions.has(id)) {
          compactions.get(id)!.endTs = ev.ts;
          break;
        }
        const step: TraceStep = { kind: 'context', ts: ev.ts, text: data.automatic ? '上下文自动压缩' : '上下文压缩' };
        compactions.set(id, step);
        steps.push(step);
        break;
      }
      case 'turn.error':
        status = 'error';
        endedAt = ev.ts;
        closeSegment(ev.ts);
        steps.push({ kind: 'error', ts: ev.ts, text: stringOf(data.message ?? data.error) || '这一轮出错了' });
        break;
      case 'turn.done':
        status = data.stopReason === 'cancelled' ? 'cancelled' : 'done';
        endedAt = ev.ts;
        closeSegment(ev.ts);
        break;
      default:
        break;
    }
  }
  // A tool step that never ended reads as running until the turn did; then it ended with it.
  if (endedAt) for (const step of [...tools.values(), ...compactions.values()]) step.endTs ??= endedAt;
  // A segment still open when the log ends is the one the turn is inside of right now.
  const tail = segment.current;
  if (tail) segments.push({ start: tail.start, firstOutput: tail.firstOutput, end: null, source: 'hub', steps: [] });
  // Steps belong to the segment whose window they fall in; the first one wins when native bounds overlap.
  const lastTs = events.length ? events[events.length - 1].ts : null;
  for (const [index, step] of steps.entries()) {
    if (step.kind === 'user') continue;
    const at = Date.parse(step.ts);
    if (!Number.isFinite(at)) continue;
    const seg = segments.find((s) => at >= Date.parse(s.start) && at <= Date.parse(s.end ?? lastTs ?? s.start));
    if (seg) seg.steps.push(index + 1);
  }
  return { steps, totals, models, status, endedAt, segments };
}
