import type { HandoverLink } from './handover-mode.js';
import type { DatabaseSync } from 'node:sqlite';
import type { StoredEvent } from './events.js';
import { compactDeltas, replaceMessageTail, toolText } from './events.js';
import { turnOutcome } from './lifecycle.js';
import { HttpError } from './errors.js';
import { diffStats } from './history-diff.js';
import { realpath } from 'node:fs/promises';
import { outputPath } from './outputs.js';
import { handoverPreview } from './handover.js';

/** Read-model events are explicitly distinct from the complete event log. */
export interface HistoryEvent extends Omit<StoredEvent, 'type'> { type: string }
export interface HistoryPage {
  events: HistoryEvent[];
  snapshotSeq: number;
  nextCursor: number | null;
  hasOlder: boolean;
  /** Only for a forward read: more turns follow, and `nextCursor` is the `after` to read them with. */
  hasNewer?: boolean;
  turnIds?: string[];
}
interface Row { thread_id: string; turn_id: string | null; seq: number; type: string; ts: string; data: string }
interface Detail {
  thread_id: string; turn_id: string; id: number; tool_id: string | null; kind: string;
  version: number; input_seq: number | null; output_seq: number | null; diff_seq: number | null;
  end_seq: number;
}
const visibleVoice = (table: string) => `NOT (${table}.type='voice.message' AND EXISTS (
  SELECT 1 FROM events closed WHERE closed.thread_id=${table}.thread_id AND closed.type='voice.closed'
  AND ${table}.seq BETWEEN json_extract(closed.data, '$.startSeq') AND json_extract(closed.data, '$.endSeq')
))`;
const eventOf = (r: Row): HistoryEvent => ({ threadId: r.thread_id, turnId: r.turn_id, seq: r.seq, type: r.type, ts: r.ts, data: JSON.parse(r.data) });
const isWork = (e: HistoryEvent) => e.type === 'history.thought' || e.type === 'context.compaction' || e.type === 'history.tool.start';
const isBoundary = (e: HistoryEvent) => ['input.request', 'approval.request', 'plan.delta', 'plan.completed'].includes(e.type);
const hasOutput = (output: unknown, content: unknown): boolean => {
  if (output !== undefined && output !== null && output !== '') return true;
  if (typeof content === 'string') return content.length > 0;
  return Array.isArray(content) && content.some(p => p?.type === 'text' && typeof p.text === 'string' && p.text.length > 0
    || p?.type === 'content' && p.content?.type === 'text' && String(p.content.text ?? '').length > 0);
};

/** Partition with the same work/answer boundaries as the timeline; no translated UI strings. */
export function partitionHistory(events: HistoryEvent[]): { summary: HistoryEvent[]; blocks: Map<number, HistoryEvent[]> } {
  const summary: HistoryEvent[] = [];
  const blocks = new Map<number, HistoryEvent[]>();
  let block: HistoryEvent | undefined;
  let answer: HistoryEvent[] = [];
  let lastNote: HistoryEvent[] = [];
  const tools = new Map<string, { block: HistoryEvent; data: Record<string, any> }>();
  const plans = new Set<string>();
  const flushAnswer = () => { summary.push(...answer); answer = []; };
  const finish = () => {
    if (!answer.length && block && lastNote.length) {
      const entries = blocks.get(block.seq)!;
      const remove = new Set(lastNote.map(e => e.seq));
      blocks.set(block.seq, entries.filter(e => !remove.has(e.seq)));
      const at = summary.indexOf(block);
      summary.splice(at + 1, 0, ...lastNote);
    }
    flushAnswer(); block = undefined; lastNote = [];
  };
  for (const ev of events) {
    if (ev.type === 'turn.started') { finish(); tools.clear(); plans.clear(); summary.push(ev); continue; }
    if (ev.type === 'message.delta' || ev.type === 'message.replace') { answer.push(ev); continue; }
    if (isWork(ev)) {
      if (!block) {
        block = { ...ev, type: 'history.work', data: { count: 0, version: ev.seq, files: [], endedAt: ev.ts } };
        blocks.set(block.seq, []); summary.push(block); lastNote = [];
      }
      const entries = blocks.get(block.seq)!;
      if (answer.length) { entries.push(...answer); lastNote = answer; answer = []; }
      entries.push(ev);
      block.data.version = Math.max(Number(block.data.version), Number(ev.data.version ?? ev.seq));
      block.data.endedAt = ev.ts;
      // A skeleton tool start may already know when its call finished; that is the better end.
      if (typeof ev.data.endedAt === 'string') block.data.endedAt = ev.data.endedAt;
      if (ev.type === 'history.tool.start') {
        block.data.count = Number(block.data.count) + 1;
        tools.set(String(ev.data.id), { block, data: { ...ev.data } });
      }
      continue;
    }
    if (ev.type.startsWith('history.tool.')) {
      const tool = tools.get(String(ev.data.id));
      if (tool) {
        blocks.get(tool.block.seq)!.push(ev);
        Object.assign(tool.data, ev.data);
        tool.block.data.version = ev.seq;
        tool.block.data.endedAt = ev.ts;
      }
      continue;
    }
    if (isBoundary(ev)) {
      const plan = ev.type === 'plan.delta' || ev.type === 'plan.completed';
      const id = String(ev.data.id ?? 'plan');
      if (!plan || !plans.has(id)) { flushAnswer(); block = undefined; lastNote = []; }
      if (plan) plans.add(id);
    }
    if (ev.type === 'turn.done' || ev.type === 'turn.error') finish();
    summary.push(ev);
  }
  flushAnswer();
  // Count only successful edits, exactly as the regular timeline does.
  for (const { block: owner, data } of tools.values()) {
    if (data.status !== 'completed') continue;
    (owner.data.files as unknown[]).push(...(data.files ?? []));
  }
  return { summary, blocks };
}

/** Collapse superseded lightweight tool updates without changing interleaved text boundaries. */
export function compactHistory(events: HistoryEvent[]): HistoryEvent[] {
  const out: HistoryEvent[] = [];
  for (const ev of events) {
    const last = out.at(-1);
    if (last && ev.type.startsWith('history.tool.') && ev.type !== 'history.tool.start'
      && last.type.startsWith('history.tool.') && last.type !== 'history.tool.start'
      && last.turnId === ev.turnId && last.data.id === ev.data.id) {
      out[out.length - 1] = { ...ev, data: { ...last.data, ...ev.data } };
    } else out.push(ev);
  }
  return compactDeltas(out as StoredEvent[]);
}

/** SQLite-backed projections. Raw payloads are referenced, never copied into the read model. */
export class HistoryStore {
  private summaryTargets = new Map<string, string | null>();
  private pending = new Map<string, Promise<void>>();
  private closed = false;
  constructor(private db: DatabaseSync) {
    db.exec(`
      CREATE INDEX IF NOT EXISTS events_voice_closed ON events(thread_id, seq) WHERE type='voice.closed';
      CREATE TABLE IF NOT EXISTS history_rows (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, turn_id TEXT, type TEXT NOT NULL, ts TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY(thread_id, seq));
      CREATE INDEX IF NOT EXISTS history_rows_turn ON history_rows(thread_id, turn_id, seq);
      CREATE TABLE IF NOT EXISTS history_details (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL, id INTEGER NOT NULL, tool_id TEXT, kind TEXT NOT NULL,
        version INTEGER NOT NULL, input_seq INTEGER, output_seq INTEGER, diff_seq INTEGER, end_seq INTEGER NOT NULL,
        PRIMARY KEY(thread_id, turn_id, id));
      CREATE UNIQUE INDEX IF NOT EXISTS history_tool ON history_details(thread_id, turn_id, tool_id) WHERE tool_id IS NOT NULL;
      CREATE TABLE IF NOT EXISTS history_turns (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY(thread_id, turn_id));
      CREATE TABLE IF NOT EXISTS history_coverage (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        start_seq INTEGER NOT NULL, end_seq INTEGER NOT NULL, PRIMARY KEY(thread_id, start_seq));
    `);
  }
  close() { this.closed = true; }
  projected(ev: StoredEvent): HistoryEvent {
    if (ev.type === 'reasoning.delta' && ev.turnId) {
      const d = this.db.prepare("SELECT id FROM history_details WHERE thread_id=? AND turn_id=? AND kind='thought' AND id<=? AND end_seq>=?").get(ev.threadId, ev.turnId, ev.seq, ev.seq) as { id: number } | undefined;
      const r = d && this.db.prepare('SELECT * FROM history_rows WHERE thread_id=? AND seq=?').get(ev.threadId, d.id) as unknown as Row | undefined;
      if (r) return { ...eventOf(r), seq: ev.seq };
    }
    const row = this.db.prepare('SELECT * FROM history_rows WHERE thread_id=? AND seq=?').get(ev.threadId, ev.seq) as unknown as Row | undefined;
    if (!row) throw new Error('history projection missing');
    return ev.type === 'approval.request' ? ev : eventOf(row);
  }
  private save(ev: HistoryEvent) {
    this.db.prepare('INSERT OR REPLACE INTO history_rows VALUES (?, ?, ?, ?, ?, ?)')
      .run(ev.threadId, ev.seq, ev.turnId, ev.type, ev.ts, JSON.stringify(ev.data));
  }
  private cover(thread: string, seq: number, through = seq) {
    const ranges = this.db.prepare('SELECT start_seq, end_seq FROM history_coverage WHERE thread_id=? AND start_seq<=? AND end_seq>=?')
      .all(thread, through + 1, seq - 1) as { start_seq: number; end_seq: number }[];
    if (ranges.some(r => r.start_seq <= seq && r.end_seq >= through)) return;
    const start = Math.min(seq, ...ranges.map(r => r.start_seq));
    const end = Math.max(through, ...ranges.map(r => r.end_seq));
    for (const r of ranges) if (r.start_seq !== start) this.db.prepare('DELETE FROM history_coverage WHERE thread_id=? AND start_seq=?').run(thread, r.start_seq);
    this.db.prepare('INSERT INTO history_coverage VALUES (?, ?, ?) ON CONFLICT(thread_id, start_seq) DO UPDATE SET end_seq=excluded.end_seq').run(thread, start, end);
  }
  /** Called within the event writer's transaction, including imports and terminal sync. */
  record(ev: StoredEvent): HistoryEvent {
    const { threadId, turnId, seq, data } = ev;
    if (turnId) this.db.prepare('DELETE FROM history_turns WHERE thread_id=? AND turn_id=?').run(threadId, turnId);
    let view: HistoryEvent = ev;
    if (ev.type.startsWith('tool.') && turnId) {
      let detail = this.db.prepare('SELECT * FROM history_details WHERE thread_id=? AND turn_id=? AND tool_id=?')
        .get(threadId, turnId, String(data.id)) as unknown as Detail | undefined;
      if (!detail) {
        this.db.prepare("INSERT INTO history_details VALUES (?, ?, ?, ?, 'tool', ?, NULL, NULL, NULL, ?)")
          .run(threadId, turnId, seq, String(data.id), seq, seq);
        detail = { thread_id: threadId, turn_id: turnId, id: seq, tool_id: String(data.id), kind: 'tool', version: seq, input_seq: null, output_seq: null, diff_seq: null, end_seq: seq };
      }
      const diffs = Array.isArray(data.content) ? data.content.filter((p: any) => p?.type === 'diff') as any[] : [];
      // The input is tool.start's, unless a later event names the call: one that surfaced while its
      // input still streamed (Claude's bridge) starts with none and learns its command from an update.
      const named = data.input !== undefined && (ev.type === 'tool.start' || (typeof data.input === 'object' && data.input !== null && Object.keys(data.input).length > 0));
      const inputSeq = named ? Math.max(seq, detail.input_seq ?? 0) : detail.input_seq;
      const outputSeq = hasOutput(ev.type === 'tool.start' ? undefined : data.output, data.content) ? Math.max(seq, detail.output_seq ?? 0) : detail.output_seq;
      const diffSeq = diffs.length ? Math.max(seq, detail.diff_seq ?? 0) : detail.diff_seq;
      this.db.prepare('UPDATE history_details SET version=?, input_seq=?, output_seq=?, diff_seq=?, end_seq=? WHERE thread_id=? AND turn_id=? AND id=?')
        .run(Math.max(seq, detail.version), inputSeq, outputSeq, diffSeq, Math.max(seq, detail.end_seq), threadId, turnId, detail.id);
      const projected: Record<string, unknown> = { id: data.id, detail: { turnId, id: detail.id, version: seq } };
      for (const field of ['kind', 'status', 'title']) if (data[field]) projected[field] = String(data[field]).slice(0, 240);
      if (diffs.length) projected.files = diffs.map(p => ({ path: String(p.path ?? ''), created: typeof p.oldText !== 'string', ...diffStats(typeof p.oldText === 'string' ? p.oldText : null, typeof p.newText === 'string' ? p.newText : ''), detail: { turnId, id: detail!.id, version: seq } }));
      view = { ...ev, type: `history.${ev.type}`, data: projected };
    } else if (ev.type === 'reasoning.delta' && turnId) {
      const prior = this.db.prepare("SELECT * FROM history_details WHERE thread_id=? AND turn_id=? AND kind='thought' AND end_seq=?")
        .get(threadId, turnId, seq - 1) as unknown as Detail | undefined;
      const id = prior?.id ?? seq;
      this.db.prepare("INSERT INTO history_details VALUES (?, ?, ?, NULL, 'thought', ?, NULL, NULL, NULL, ?) ON CONFLICT(thread_id, turn_id, id) DO UPDATE SET version=excluded.version, end_seq=excluded.end_seq")
        .run(threadId, turnId, id, seq, seq);
      const prev = prior ? this.db.prepare('SELECT * FROM history_rows WHERE thread_id=? AND seq=?').get(threadId, id) as unknown as Row | undefined : undefined;
      const before = prev ? eventOf(prev) : undefined;
      view = { ...ev, type: 'history.thought', data: { id, text: (String(before?.data.text ?? '') + String(data.text ?? '')).slice(-240), version: seq, startedAt: before?.ts ?? ev.ts, endedAt: ev.ts, detail: { turnId, id, version: seq } } };
      this.save({ ...view, seq: id, ts: before?.ts ?? ev.ts });
      this.cover(threadId, seq);
      return view;
    }
    if (ev.type === 'approval.request') {
      const { toolCall: _toolCall, ...summary } = data;
      view = { ...ev, data: summary };
    }
    if (ev.type === 'turn.started' && data.voiceSummary) view = { ...ev, data: { ...data, text: '', displayText: '' } };
    this.save(view);
    this.cover(threadId, seq);
    return view;
  }
  latest(thread: string): number {
    return Number((this.db.prepare('SELECT COALESCE(MAX(seq),0) AS n FROM events WHERE thread_id=?').get(thread) as any).n);
  }
  private rows(thread: string, start: number, end: number, hiddenStart = 0): HistoryEvent[] {
    return (this.db.prepare('SELECT thread_id, turn_id, seq, type, ts, CASE WHEN seq=? AND type=\'turn.started\' THEN \'{"text":"","handoverCollapsed":true}\' ELSE data END AS data FROM history_rows WHERE thread_id=? AND seq>=? AND seq<? AND ' + visibleVoice('history_rows') + ' ORDER BY seq').all(hiddenStart, thread, start, end) as unknown as Row[]).map(eventOf);
  }
  private bounds(thread: string, turn: string): { start: number; end: number } {
    const row = this.db.prepare("SELECT seq FROM events WHERE thread_id=? AND turn_id=? AND type='turn.started' ORDER BY seq LIMIT 1").get(thread, turn) as { seq: number } | undefined;
    if (!row) throw new HttpError(404, 'turn not found');
    const next = this.db.prepare("SELECT seq FROM events WHERE thread_id=? AND type='turn.started' AND seq>? ORDER BY seq LIMIT 1").get(thread, row.seq) as { seq: number } | undefined;
    return { start: row.seq, end: next?.seq ?? this.latest(thread) + 1 };
  }
  /** Cold histories are built in bounded batches; concurrent readers share the same job. */
  async ensure(thread: string, start: number, end: number, toolsOnly = false): Promise<void> {
    if (start >= end || this.db.prepare('SELECT 1 FROM history_coverage WHERE thread_id=? AND start_seq<=? AND end_seq>=?').get(thread, start, end - 1)) return;
    const key = `${thread}:${start}:${end}:${toolsOnly}`;
    const existing = this.pending.get(key);
    if (existing) return existing;
    const run = async () => {
      let after = start - 1;
      while (!this.closed) {
        const missing = this.db.prepare(`SELECT e.* FROM events e LEFT JOIN history_rows h ON h.thread_id=e.thread_id AND h.seq=e.seq
          WHERE e.thread_id=? AND e.seq>? AND e.seq<? AND h.seq IS NULL
          ${toolsOnly ? "AND e.type IN ('tool.start', 'tool.update', 'tool.end') AND e.turn_id IS NOT NULL" : ''}
          AND NOT EXISTS (SELECT 1 FROM history_details d WHERE d.thread_id=e.thread_id AND d.kind='thought' AND e.type='reasoning.delta' AND e.seq BETWEEN d.id AND d.end_seq)
          ORDER BY e.seq LIMIT 64`).iterate(thread, after, end) as unknown as Iterable<Row>;
        let count = 0;
        let bytes = 0;
        const began = performance.now();
        for (const row of missing) {
          this.record(eventOf(row) as StoredEvent); after = row.seq; count++; bytes += row.data.length;
          if (bytes > 1024 * 1024 || performance.now() - began > 8) break;
        }
        if (!count) { if (!toolsOnly) this.cover(thread, start, end - 1); break; }
        await new Promise<void>(resolve => setImmediate(resolve));
      }
    };
    const promise = run().finally(() => this.pending.delete(key));
    this.pending.set(key, promise);
    return promise;
  }
  /** Read only event envelopes to locate answers. Never parse tool payloads or build projections. */
  private directSummary(thread: string, start: number, end: number, hiddenStart = 0): HistoryEvent[] {
    // Collapse text/thinking runs before returning metadata to JS. The window reads no data column.
    const spans = this.db.prepare(`WITH ordered AS (
      SELECT thread_id, turn_id, seq, type, ts, LAG(type) OVER (ORDER BY seq) AS previous
      FROM events WHERE thread_id=? AND seq>=? AND seq<? AND ${visibleVoice('events')}
    ), edges AS (
      SELECT * FROM ordered WHERE type NOT IN ('message.delta', 'reasoning.delta') OR previous IS NULL OR previous<>type
    ), spans AS (
      SELECT *, LEAD(seq, 1, ?) OVER (ORDER BY seq) AS until FROM edges
    ), bounded AS (
      -- A tool call's last update or end is the row before the next non-tool span; its timestamp
      -- is how long the call ran, which the skeleton otherwise cannot know.
      SELECT s.*, e.ts AS end_ts FROM spans s LEFT JOIN events e
        ON e.thread_id = s.thread_id
       AND e.seq = COALESCE((SELECT MIN(x.seq) FROM edges x WHERE x.seq > s.seq AND x.type NOT IN ('tool.update', 'tool.end')), ?) - 1
    ) SELECT thread_id, turn_id, seq, type, ts, until, end_ts FROM bounded
      WHERE type NOT IN ('tool.update', 'tool.end') ORDER BY seq`).all(thread, start, end, end, end) as unknown as (Omit<Row, 'data'> & { until: number; end_ts?: string })[];
    const ranges = new Map(spans.map(s => [s.seq, s.until]));
    const read = this.db.prepare('SELECT data FROM events WHERE thread_id=? AND seq=?');
    const readStart = this.db.prepare("SELECT CASE WHEN json_extract(data, '$.voiceSummary') IS NOT NULL THEN json_set(data, '$.text', '', '$.displayText', '') ELSE data END AS data FROM events WHERE thread_id=? AND seq=?");
    const readApproval = this.db.prepare("SELECT json_remove(data, '$.toolCall') AS data FROM events WHERE thread_id=? AND seq=?");
    const skeleton = spans.map(row => {
      let type = row.type;
      let data: Record<string, any> = {};
      if (type === 'tool.start') { type = 'history.tool.start'; data.id = String(row.seq); if (row.end_ts) data.endedAt = row.end_ts; }
      else if (type === 'reasoning.delta') {
        type = 'history.thought';
        data = { id: row.seq, text: '', version: row.until - 1, startedAt: row.ts };
        if (row.end_ts) data.endedAt = row.end_ts;
        data.detail = { turnId: row.turn_id, id: row.seq, version: data.version };
      }
      else if (type === 'turn.started' && row.seq === hiddenStart) data = { text: '', handoverCollapsed: true };
      else if (type !== 'message.delta') {
        data = JSON.parse(String((type === 'approval.request' ? readApproval : type === 'turn.started' ? readStart : read).get(thread, row.seq)!.data));
      }
      return { threadId: thread, turnId: row.turn_id, seq: row.seq, ts: row.ts, type, data };
    });
    const { summary } = partitionHistory(skeleton);
    const done = skeleton.some(e => e.type === 'turn.done' || e.type === 'turn.error');
    const resolved = new Set(skeleton.filter(e => e.type === 'approval.resolved').map(e => e.data.approvalId));
    const out: HistoryEvent[] = [];
    for (const e of summary) {
      if (e.type === 'message.delta') {
        const rows = this.db.prepare("SELECT * FROM events WHERE thread_id=? AND seq>=? AND seq<? AND type='message.delta' ORDER BY seq")
          .all(thread, e.seq, ranges.get(e.seq)!) as unknown as Row[];
        out.push(...compactDeltas(rows.map(eventOf) as StoredEvent[]));
      } else {
        if (e.type === 'history.work') e.data.version = end - 1;
        if (!done && e.type === 'approval.request' && !resolved.has(e.data.approvalId)) e.data = JSON.parse(String(read.get(thread, e.seq)!.data));
        out.push(e);
      }
    }
    return out;
  }
  private turnView(thread: string, turn: string | null, start: number, end: number, hiddenStart = 0): HistoryEvent[] {
    if (turn) {
      const first = this.db.prepare("SELECT thread_id, turn_id, seq, type, ts, '{}' AS data FROM events WHERE thread_id=? AND turn_id=? AND type='turn.started' ORDER BY seq LIMIT 1").get(thread, turn) as unknown as Row | undefined;
      const marker = first && this.summaryEvent(eventOf(first));
      if (marker?.type === 'history.handover') {
        // Errors and pending decisions remain visible outside the collapsed summary.
        const important = this.db.prepare("SELECT * FROM events WHERE thread_id=? AND turn_id=? AND seq<? AND type IN ('turn.error','approval.request','approval.resolved','input.request','input.resolved') ORDER BY seq").all(thread, turn, end) as unknown as Row[];
        return [marker, ...important.map(eventOf)];
      }
    }
    // Existing projections are optional. Missing ones must never delay the first response.
    const covered = this.db.prepare('SELECT 1 FROM history_coverage WHERE thread_id=? AND start_seq<=? AND end_seq>=?').get(thread, start, end - 1);
    if (!covered) return this.directSummary(thread, start, end, hiddenStart);
    const rows = compactHistory(this.rows(thread, start, end, hiddenStart));
    const done = rows.some(e => e.type === 'turn.done' || e.type === 'turn.error');
    if (!done) {
      const resolved = new Set(rows.filter(e => e.type === 'approval.resolved').map(e => e.data.approvalId));
      for (const e of rows) if (e.type === 'approval.request' && !resolved.has(e.data.approvalId)) {
        const raw = this.db.prepare('SELECT data FROM events WHERE thread_id=? AND seq=?').get(thread, e.seq);
        if (raw) e.data = JSON.parse(String(raw.data));
      }
    }
    const view = partitionHistory(rows).summary;
    for (const e of view) if (e.type === 'history.work') e.data.version = end - 1;
    return view;
  }
  /** Page boundaries: voice-only conversations have no task turns, so each committed utterance is one too. */
  private starts(thread: string, end: number, after: number, order: 'ASC' | 'DESC'): { seq: number; turn_id: string | null }[] {
    return this.db.prepare(`SELECT e.seq, e.turn_id FROM events e
      WHERE e.thread_id=? AND e.seq<? AND e.seq>? AND e.type IN ('turn.started', 'voice.message', 'voice.closed') AND ${visibleVoice('e')} AND (
        e.type IN ('turn.started', 'voice.closed') OR e.type='voice.message' AND COALESCE((
          SELECT b.type FROM events b WHERE b.thread_id=e.thread_id AND b.seq<e.seq
          AND b.type IN ('turn.started', 'turn.done', 'turn.error') ORDER BY b.seq DESC LIMIT 1
        ), '')<>'turn.started')
      ORDER BY e.seq ${order} LIMIT 9`).all(thread, end, after) as { seq: number; turn_id: string | null }[];
  }
  /**
   * The newest eight turns before `before`, or with `forward` the eight right after `after`, so a
   * conversation read from its beginning (the next segment of a handover chain) is paged the way it
   * happened. A forward page that is not the last gives the `after` of the next one as `nextCursor`.
   */
  async page(thread: string, before?: number, after = 0, chain = false, forward = false): Promise<HistoryPage> {
    const hiddenStart = chain ? this.handoverStart(thread) : 0;
    const snapshotSeq = this.latest(thread);
    let end = Math.min(before ?? snapshotSeq + 1, snapshotSeq + 1);
    // Each committed utterance is a page boundary, so opening a long call does not eagerly load its entire transcript.
    let hasNewer = false;
    if (forward) {
      const ahead = this.starts(thread, end, after, 'ASC');
      if (ahead.length > 8) { end = ahead[8].seq; hasNewer = true; }
    }
    const starts = this.starts(thread, end, after, 'DESC');
    const selected = starts.slice(0, 8).reverse();
    const hasOlder = starts.length > 8;
    const start = hasOlder ? selected[0].seq : after + 1;
    const events: HistoryEvent[] = [];
    // A usage reading can precede all eight visible turns; paging must not erase the meter.
    const usage = this.db.prepare("SELECT * FROM events WHERE thread_id=? AND type='usage.updated' AND seq<? ORDER BY seq DESC LIMIT 1").get(thread, start) as unknown as Row | undefined;
    if (usage && !after) events.push(eventOf(usage));
    if (!selected.length) events.push(...this.directSummary(thread, start, end, hiddenStart));
    else {
      events.push(...this.directSummary(thread, start, selected[0].seq, hiddenStart));
      selected.forEach((s, i) => events.push(...this.turnView(thread, s.turn_id, s.seq, selected[i + 1]?.seq ?? end, hiddenStart)));
    }
    // Read only the indexed turn boundaries, never the message/tool payloads. Paging keeps
    // the initial index; live turn.started events extend it in the client.
    const turnIds = before === undefined ? (this.db.prepare("SELECT turn_id FROM events WHERE thread_id=? AND type='turn.started' AND seq<=? ORDER BY seq")
      .all(thread, snapshotSeq) as { turn_id: string }[]).map(row => row.turn_id) : undefined;
    if (forward) return { events, snapshotSeq, nextCursor: hasNewer ? end - 1 : null, hasOlder: false, hasNewer, turnIds };
    return { events, snapshotSeq, nextCursor: hasOlder ? start : null, hasOlder, turnIds };
  }
  voiceStats(thread: string, start: number) {
    const row = this.db.prepare(`WITH latest AS (
      SELECT data, ROW_NUMBER() OVER (PARTITION BY json_extract(data, '$.itemId') ORDER BY seq DESC) AS position
      FROM events WHERE thread_id=? AND type='voice.message' AND seq>=?
    ) SELECT COUNT(*) AS count, COALESCE(SUM(CASE WHEN json_extract(data, '$.final')=1 THEN 1 ELSE 0 END),0) AS finals,
      COALESCE(SUM(CASE WHEN json_extract(data, '$.final')=1 AND json_extract(data, '$.role')='user'
      THEN length(trim(json_extract(data, '$.text'))) ELSE 0 END),0) AS userChars
      FROM latest WHERE position=1 AND length(trim(json_extract(data, '$.text')))>0`).get(thread, start)!;
    return { count: Number(row.count), finals: Number(row.finals), userChars: Number(row.userChars) };
  }

  voiceText(thread: string, session: string, cursor = 0) {
    const closed = this.db.prepare("SELECT data FROM events WHERE thread_id=? AND type='voice.closed' AND json_extract(data, '$.sessionId')=? LIMIT 1").get(thread, session);
    if (!closed) throw new HttpError(404, 'voice history not found');
    const { startSeq, endSeq } = JSON.parse(String(closed.data));
    const rows = this.db.prepare("SELECT * FROM events WHERE thread_id=? AND type='voice.message' AND seq>=? AND seq<=? AND seq>? ORDER BY seq LIMIT 65")
      .all(thread, startSeq, endSeq, cursor) as unknown as Row[];
    return { events: rows.slice(0, 64).map(eventOf), nextCursor: rows.length > 64 ? rows[63].seq : null };
  }
  /** Summary turns are system activity; only expansion reads their prompt and answer. */
  summaryEvent(event: HistoryEvent): HistoryEvent {
    if (event.type === 'turn.started' && event.data.voiceSummary) return { ...event, data: { ...event.data, text: '', displayText: '' } };
    if (!event.turnId || !['turn.started', 'turn.done', 'message.delta', 'message.replace', 'reasoning.delta', 'context.compaction', 'tool.start', 'tool.update', 'tool.end'].includes(event.type)) return event;
    const key = `${event.threadId}:${event.turnId}`;
    if (!this.summaryTargets.has(key)) {
      const start = this.db.prepare(`SELECT COALESCE(json_extract(data, '$.handoverLabel'), json_extract(data, '$.handover')) AS label
        FROM events WHERE thread_id=? AND turn_id=? AND type='turn.started' ORDER BY seq LIMIT 1`).get(event.threadId, event.turnId);
      this.summaryTargets.set(key, start?.label ? String(start.label) : null);
      if (this.summaryTargets.size > 256) this.summaryTargets.delete(this.summaryTargets.keys().next().value!);
    }
    const target = this.summaryTargets.get(key);
    if (!target) return event;
    const end = this.db.prepare("SELECT MAX(seq) AS version, MAX(CASE WHEN type IN ('turn.done','turn.error') THEN 1 ELSE 0 END) AS done FROM events WHERE thread_id=? AND turn_id=?").get(event.threadId, event.turnId)!;
    return { ...event, type: 'history.handover', data: { target, done: Boolean(end.done), version: end.version } };
  }
  summaryText(thread: string, turn: string, offset = 0, snapshot?: number) {
    const row = this.db.prepare("SELECT json_extract(data, '$.text') AS prompt FROM events WHERE thread_id=? AND turn_id=? AND type='turn.started' AND json_extract(data, '$.handover') IS NOT NULL LIMIT 1").get(thread, turn);
    if (!row) throw new HttpError(404, 'handover summary not found');
    return { ...this.handoverText(thread, turn, offset, snapshot), prompt: String(row.prompt ?? '') };
  }
  private handoverStart(thread: string): number {
    const record = this.db.prepare('SELECT handover, created_at FROM threads WHERE id=?').get(thread);
    // Read as part of a chain, the first turn of a handed-over segment is its folded card, whether the
    // relay was automatic or by hand. Read on its own (not a chain), nothing is hidden.
    if (!record?.handover || (JSON.parse(String(record.handover)) as HandoverLink).detached) return 0;

    return Number(this.db.prepare(`SELECT e.seq FROM events e JOIN threads t ON t.id=e.thread_id
      WHERE e.thread_id=? AND t.handover IS NOT NULL AND e.type='turn.started' ORDER BY e.seq LIMIT 1`).get(thread)?.seq ?? 0);
  }

  /** The merged view never receives the first handover prompt until its separator is opened. */
  handoverEvent(event: HistoryEvent): HistoryEvent {
    return event.type === 'turn.started' && event.seq === this.handoverStart(event.threadId)
      ? { ...event, data: { text: '', handoverCollapsed: true } } : event;
  }

  handoverInfo(thread: string, full = false) {
    const link = this.db.prepare('SELECT handover FROM threads WHERE id=?').get(thread)?.handover;
    if (!link) throw new HttpError(404, 'no handover source');
    const source = JSON.parse(String(link)) as { sourceId: string; agent: string; title: string; method?: 'resume' | 'summary' };
    const first = Number(this.db.prepare("SELECT seq FROM events WHERE thread_id=? AND type='turn.started' ORDER BY seq LIMIT 1").get(thread)?.seq ?? 0);
    // Folded, only the start of the text is read: enough for a preview and its size, not the whole brief.
    const row = this.db.prepare(`SELECT turn_id, ${full ? "COALESCE(json_extract(data, '$.displayText'), json_extract(data, '$.text'))" : "substr(COALESCE(json_extract(data, '$.displayText'), json_extract(data, '$.text')), 1, 1200)"} AS text,
      length(COALESCE(json_extract(data, '$.displayText'), json_extract(data, '$.text'))) AS size
      FROM events WHERE thread_id=? AND seq=?`).get(thread, first);
    const error = this.db.prepare(`SELECT json_extract(data, '$.code') AS code FROM events WHERE thread_id=? AND type='turn.error'
      AND turn_id=(SELECT turn_id FROM events WHERE thread_id=? AND type='turn.started' ORDER BY seq DESC LIMIT 1) ORDER BY seq DESC LIMIT 1`)
      .get(source.sourceId, source.sourceId);
    const text = typeof row?.text === 'string' ? row.text : '';
    return { source, method: source.method ?? null, turnId: row?.turn_id ?? null, reason: typeof error?.code === 'string' ? error.code : null,
      size: Number(row?.size ?? 0), preview: handoverPreview(text),
      ...(full ? { text } : {}) };
  }

  /** Merge only answer text, without loading tool payloads or building write-side projections. */
  private handoverAnswer(thread: string, turn: string, snapshotSeq: number): string {
    const rows = this.db.prepare(`SELECT type, json_extract(data, '$.text') AS text,
      json_extract(data, '$.previous') AS previous FROM events
      WHERE thread_id=? AND turn_id=? AND seq<=? AND type IN ('message.delta', 'message.replace') ORDER BY seq`)
      .iterate(thread, turn, snapshotSeq);
    let answer = '';
    for (const row of rows) {
      if (row.type === 'message.replace') answer = replaceMessageTail(answer, row);
      else if (typeof row.text === 'string') answer += row.text;
    }
    return answer;
  }

  /** Complete rounds are page boundaries; only unusually long answers need a separate text read. */
  handoverPage(thread: string, before?: number, count = 10, snapshot?: number) {
    const snapshotSeq = snapshot ?? this.latest(thread);
    const starts = this.db.prepare(`SELECT thread_id, turn_id, seq, type, ts,
      json_object('text', COALESCE(json_extract(data, '$.displayText'), json_extract(data, '$.text')),
        'handover', json_extract(data, '$.handover')) AS data
      FROM events WHERE thread_id=? AND seq<? AND type='turn.started' AND turn_id IS NOT NULL
      ORDER BY seq DESC LIMIT ?`).all(thread, Math.min(before ?? snapshotSeq + 1, snapshotSeq + 1), count + 1) as unknown as Row[];
    const hasOlder = starts.length > count;
    const selected = starts.slice(0, count).reverse();
    const turns = selected.map(row => {
      const start = eventOf(row);
      const turnId = row.turn_id!;
      const answer = Array.from(this.handoverAnswer(thread, turnId, snapshotSeq));
      const tools = new Map<string, { id: string; name: string | null; kind: string | null; title: string | null; status: string | null }>();
      const updates = this.db.prepare(`SELECT json_extract(data, '$.id') AS id,
        substr(COALESCE(json_extract(data, '$.tool'), json_extract(data, '$.name')), 1, 240) AS name,
        substr(json_extract(data, '$.kind'), 1, 240) AS kind, substr(json_extract(data, '$.title'), 1, 240) AS title,
        substr(json_extract(data, '$.status'), 1, 240) AS status
        FROM events WHERE thread_id=? AND turn_id=? AND seq<=? AND type IN ('tool.start', 'tool.update', 'tool.end') ORDER BY seq`)
        .iterate(thread, turnId, snapshotSeq);
      for (const update of updates) {
        const id = String(update.id);
        const tool = tools.get(id) ?? { id, name: null, kind: null, title: null, status: null };
        for (const field of ['name', 'kind', 'title', 'status'] as const) if (typeof update[field] === 'string') tool[field] = update[field];
        tools.set(id, tool);
      }
      const ended = this.db.prepare(`SELECT thread_id, turn_id, seq, type, ts,
        json_object('stopReason', json_extract(data, '$.stopReason'), 'message', COALESCE(json_extract(data, '$.message'), json_extract(data, '$.error')),
          'code', json_extract(data, '$.code')) AS data
        FROM events WHERE thread_id=? AND turn_id=? AND seq<=? AND type IN ('turn.done', 'turn.error') ORDER BY seq DESC LIMIT 1`)
        .get(thread, turnId, snapshotSeq) as unknown as Row | undefined;
      const outcome = ended ? turnOutcome(eventOf(ended) as StoredEvent) : null;
      return { turnId, startSeq: row.seq, startedAt: row.ts, question: String(start.data.text ?? ''), handover: start.data.handover ?? null,
        answer: { text: answer.slice(0, 4000).join(''), length: answer.length, truncated: answer.length > 4000,
          fullText: answer.length > 4000 ? { turnId, offset: 0, snapshotSeq } : null },
        tools: [...tools.values()].slice(-30), toolCount: tools.size, toolsOmitted: Math.max(0, tools.size - 30),
        status: outcome?.outcome ?? 'unfinished', endedAt: ended?.ts ?? null,
        error: outcome?.message ? { message: outcome.message, code: outcome.code } : null };
    });
    return { turns, snapshotSeq, nextCursor: hasOlder ? selected[0].seq : null, hasOlder };
  }

  /** Unicode character offsets and a fixed snapshot let callers reconstruct even a live answer. */
  handoverText(thread: string, turn: string, offset = 0, snapshot?: number) {
    const snapshotSeq = snapshot ?? this.latest(thread);
    if (snapshotSeq > this.latest(thread)) throw new HttpError(400, 'snapshot is newer than this history');
    const { start } = this.bounds(thread, turn);
    if (start > snapshotSeq) throw new HttpError(404, 'turn not found in snapshot');
    const answer = Array.from(this.handoverAnswer(thread, turn, snapshotSeq));
    if (offset > answer.length) throw new HttpError(400, 'offset exceeds answer length');
    const next = offset + 16000;
    return { turnId: turn, snapshotSeq, text: answer.slice(offset, next).join(''), offset, length: answer.length,
      nextOffset: next < answer.length ? next : null };
  }
  /** Reconnect replaces affected turns from a bounded snapshot, never replays tool payloads. */
  async since(thread: string, after: number, chain = false): Promise<{ events: HistoryEvent[]; turns: string[]; snapshotSeq: number }> {
    const snapshotSeq = this.latest(thread);
    const hiddenStart = chain ? this.handoverStart(thread) : 0;
    const turns = (this.db.prepare('SELECT DISTINCT turn_id FROM events WHERE thread_id=? AND seq>? AND seq<=? AND turn_id IS NOT NULL').all(thread, after, snapshotSeq) as { turn_id: string }[]).map(r => r.turn_id);
    const events: HistoryEvent[] = [];
    for (const turn of turns) {
      const { start, end } = this.bounds(thread, turn);
      const until = Math.min(end, snapshotSeq + 1);
      events.push(...this.turnView(thread, turn, start, until, hiddenStart).filter(e => e.turnId === turn));
    }
    const global = this.db.prepare('SELECT * FROM events WHERE thread_id=? AND seq>? AND seq<=? AND turn_id IS NULL AND ' + visibleVoice('events') + ' ORDER BY seq').all(thread, after, snapshotSeq) as unknown as Row[];
    events.push(...global.map(eventOf));
    events.sort((a,b) => a.seq - b.seq);
    return { events, turns, snapshotSeq };
  }
  async work(thread: string, turn: string, block: number, cursor = 0, _expectedVersion?: number, chain = false): Promise<{ events: HistoryEvent[]; files: unknown[]; version: number; nextCursor: number | null }> {
    const { start, end } = this.bounds(thread, turn);
    // A stale expectedVersion is accepted: a running turn's end keeps moving, and the entry
    // list only ever grows, so a cursor still names the same window. Rejecting it made live
    // work panels page back to the start on every refresh. The returned version is current,
    // so the next continuation continues from what the server now knows.
    await this.ensure(thread, start, end);
    const rows = compactHistory(this.rows(thread, start, end, chain ? this.handoverStart(thread) : 0));
    const partition = partitionHistory(rows);
    const entries = partition.blocks.get(block);
    if (!entries) throw new HttpError(404, 'work block not found');
    const list: HistoryEvent[] = [];
    const tools = new Map<string, HistoryEvent>();
    for (const e of entries) {
      if (e.type === 'history.tool.start') { const entry = { ...e, data: { ...e.data } }; tools.set(String(e.data.id), entry); list.push(entry); }
      else if (e.type.startsWith('history.tool.')) {
        const entry = tools.get(String(e.data.id));
        if (entry) { Object.assign(entry.data, e.data); if (e.type === 'history.tool.end') entry.data.endedAt = e.ts; }
      } else list.push(e);
    }
    const next = cursor + 100;
    const terminal = rows.find(e => e.type === 'turn.done' || e.type === 'turn.error');
    return { events: [rows.find(e => e.type === 'turn.started')!, ...list.slice(cursor, next), ...(terminal ? [terminal] : [])], files: (partition.summary.find(e => e.type === 'history.work' && e.seq === block)?.data.files ?? []) as unknown[], version: end - 1, nextCursor: next < list.length ? next : null };
  }
  async detail(thread: string, turn: string, id: number, offset = 0, expectedVersion?: number, path?: string): Promise<Record<string, unknown>> {
    const { start, end } = this.bounds(thread, turn);
    await this.ensure(thread, start, end);
    const d = this.db.prepare('SELECT * FROM history_details WHERE thread_id=? AND turn_id=? AND id=?').get(thread, turn, id) as unknown as Detail | undefined;
    if (!d) throw new HttpError(404, 'detail not found');
    if (expectedVersion !== undefined && expectedVersion !== d.version) throw new HttpError(409, 'detail changed; reload before continuing');
    const read = (seq: number | null) => seq === null ? {} : JSON.parse(String(this.db.prepare('SELECT data FROM events WHERE thread_id=? AND turn_id=? AND seq=?').get(thread, turn, seq)?.data ?? '{}'));
    if (d.kind === 'thought') {
      const rows = this.db.prepare("SELECT data FROM events WHERE thread_id=? AND turn_id=? AND seq>=? AND seq<=? AND type='reasoning.delta' ORDER BY seq").all(thread, turn, d.id, d.end_seq);
      const text = rows.map(r => String(JSON.parse(String(r.data)).text ?? '')).join('');
      const next = offset + 65536;
      return { version: d.version, text: text.slice(offset, next), nextCursor: next < text.length ? next : null };
    }
    if (path !== undefined) {
      const content = read(d.diff_seq).content;
      const cwd = String(this.db.prepare('SELECT cwd FROM threads WHERE id=?').get(thread)?.cwd ?? '');
      let cwdReal = cwd;
      try { cwdReal = await realpath(cwd); } catch { /* Deleted files still have readable recorded diffs. */ }
      const requested = outputPath(path, cwd, cwdReal);
      return { version: d.version, content: Array.isArray(content) ? content.filter((p: any) => p?.type === 'diff' && typeof p.path === 'string' && outputPath(p.path, cwd, cwdReal) === requested) : [], nextCursor: null };
    }
    const output = read(d.output_seq);
    const text = toolText(output.output, output.content);
    const next = offset + 65536;
    return { version: d.version, toolId: d.tool_id,
      outputTruncated: output.outputTruncated === true,
      outputOmitted: typeof output.outputOmitted === 'number' ? output.outputOmitted : undefined,
      input: offset ? undefined : read(d.input_seq).input, output: text.slice(offset, next), content: offset ? undefined : read(d.diff_seq).content, nextCursor: next < text.length ? next : null };
  }
}
