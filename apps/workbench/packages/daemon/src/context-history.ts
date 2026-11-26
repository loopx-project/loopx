import { randomUUID } from 'node:crypto';
import { HttpError } from './errors.js';
import type { ChainSegment } from './handover.js';
import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';

export const shellArgument = (value: string) => `'${value.replace(/'/g, "'\\''")}'`;
export function integer(value: unknown, fallback: number, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  if (value === undefined) return fallback;
  if ((typeof value !== 'string' && typeof value !== 'number') || !/^(0|[1-9]\d*)$/.test(String(value))
    || !Number.isSafeInteger(Number(value)) || Number(value) < min || Number(value) > max) throw new HttpError(400, 'invalid history pagination');
  return Number(value);
}

export interface HistorySnapshot {
  id: string;
  owner: string;
  scope: string;
  segments: ChainSegment[];
  references: string[];
}
interface Start { thread: string; turn: string; seq: number; ts: string; segment: number; type: string; data: string }
interface Cursor { ts: string; segment: number; seq: number }
const encodeCursor = (row: Cursor) => Buffer.from(JSON.stringify({ ts: row.ts, segment: row.segment, seq: row.seq })).toString('base64url');
function cursor(value: unknown): Cursor {
  if (typeof value !== 'string' || value.length > 500 || !/^[\w-]+$/.test(value)) throw new HttpError(400, 'invalid history cursor');
  try {
    const row = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
    if (typeof row.ts !== 'string' || row.ts.length > 50 || !Number.isSafeInteger(row.segment) || row.segment < 0 || !Number.isSafeInteger(row.seq) || row.seq < 1) throw new Error();
    return row;
  } catch { throw new HttpError(400, 'invalid history cursor'); }
}

/** Strip only standalone hub protocol, keeping human comments, quotations and ordinary inline code. */
export function userRequest(data: Record<string, unknown>): string {
  const channel = data.channel as { kind?: string; parts?: Array<{ kind: string; text?: string }> } | undefined;
  if (typeof data.userInput !== 'string' && channel) return channel.parts?.filter(part => part.kind === 'person').map(part => part.text ?? '').join('\n\n') ?? '';
  return String(data.userInput ?? data.displayText ?? data.text ?? '')
    .replace(/(?:^|\n\n)<(?:loochat|calli)-[a-z-]+>[\s\S]*?<\/(?:loochat|calli)-[a-z-]+>(?=\n|$)/g, '')
    .replace(/(^\[注释 \d+\][\s\S]*?\n)来源：(交接前的会话|另一个会话)，原文和上下文用 (?:loochat|calli) history [^\n]+ 读\n/gm, '$1')
    .replace(/(?:^|\n\n)参考(?:消息|会话) [^\n]+：`(?:loochat|calli) references [^`]+`（只读；按返回的 command 继续）。(?=\n|$)/g, '')
    .trim();
}
function isPerson(data: Record<string, unknown>): boolean {
  if (typeof data.userInput === 'string') return data.userOrigin === 'person';
  const channel = data.channel as { parts?: Array<{ kind: string }> } | undefined;
  if (channel) return Boolean(channel.parts?.some(part => part.kind === 'person'));
  if (data.userOrigin !== undefined) return data.userOrigin === 'person';
  // Older ordinary and imported turns did not record fromUser; absence must not erase real asks.
  return !['handover', 'coordination', 'scheduled', 'review', 'proactive', 'background', 'control', 'dispatchRunId', 'assistantSay'].some(key => data[key])
    && data.source !== 'background' && data.source !== 'voice';
}

/** One bounded reader serves handovers and references. Snapshot tokens are owner- and scope-bound. */
export class ContextHistory {
  constructor(private store: Store, private manager: ThreadManager) {
    store.db.exec(`CREATE TABLE IF NOT EXISTS context_history_snapshots (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, scope TEXT NOT NULL, data TEXT NOT NULL);`);
  }

  freeze(owner: string, scope: string, segments: ChainSegment[], references: string[] = []): HistorySnapshot {
    const snapshot: HistorySnapshot = { id: randomUUID(), owner, scope, segments: segments.map(segment => ({ ...segment,
      lowerSeq: segment.lowerSeq ?? 0, upperSeq: Math.min(segment.upperSeq ?? this.store.history.latest(segment.id), this.store.history.latest(segment.id)) })), references: [...new Set(references)] };
    this.store.transaction(() => {
      this.store.toolHistory.pinSegments(snapshot.segments);
      this.store.db.prepare('INSERT INTO context_history_snapshots VALUES (?, ?, ?, ?)').run(snapshot.id, owner, scope, JSON.stringify(snapshot));
    });
    return snapshot;
  }

  get(owner: string, scope: string, id: unknown): HistorySnapshot {
    if (typeof id !== 'string') throw new HttpError(400, 'snapshot token is required');
    const row = this.store.db.prepare('SELECT data FROM context_history_snapshots WHERE id=? AND owner=? AND scope=?').get(id, owner, scope) as { data: string } | undefined;
    if (!row) throw new HttpError(403, 'snapshot is outside this conversation or read scope');
    const snapshot = JSON.parse(row.data) as HistorySnapshot;
    this.check(snapshot);
    return snapshot;
  }

  check(snapshot: HistorySnapshot) {
    for (const segment of snapshot.segments) {
      if (!this.store.getThread(segment.id)) throw new HttpError(404, 'source conversation no longer exists on this machine');
      if (this.store.history.latest(segment.id) < segment.upperSeq! || this.store.db.prepare("SELECT 1 FROM events WHERE thread_id=? AND seq>? AND type='thread.historyChanged' LIMIT 1").get(segment.id, segment.upperSeq!)) {
        throw new HttpError(409, 'referenced history changed; start a new read');
      }
    }
  }

  private starts(snapshot: HistorySnapshot, input: Record<string, unknown>, users: boolean): { selected: Start[]; more: boolean } {
    const segments = snapshot.segments.map((segment, index) => ({ ...segment, index }))
      .filter(segment => input.segment === undefined || segment.id === input.segment);
    if (!segments.length) throw new HttpError(403, 'segment is outside this read scope');
    const parameters: Array<string | number> = [];
    const queries = segments.map(segment => {
      parameters.push(segment.id, segment.lowerSeq!, segment.upperSeq!);
      return `SELECT thread_id AS thread, turn_id AS turn, seq, ts, ${segment.index} AS segment, type, data FROM events
        WHERE thread_id=? AND seq>? AND seq<=? AND turn_id IS NOT NULL
          AND ${users ? "(type='turn.started' OR type='input.resolved' AND json_extract(data, '$.userOrigin')='person' AND json_extract(data, '$.userInput') IS NOT NULL)" : "type='turn.started'"}`;
    });
    const direction = users ? 'ASC' : 'DESC';
    const count = integer(input.turns, 10, 1, 50);
    const clauses: string[] = [];
    const supplied = users ? input.cursor : input.before;
    if (supplied !== undefined) {
      if (!users && /^(0|[1-9]\d*)$/.test(String(supplied))) {
        if (new Set(segments.map(segment => segment.id)).size > 1) throw new HttpError(400, 'use the returned cursor for a multi-segment page');
        clauses.push('seq<?'); parameters.push(integer(supplied, 0, 1));
      } else {
        const position = cursor(supplied);
        clauses.push(`(ts, segment, seq) ${users ? '>' : '<'} (?, ?, ?)`);
        parameters.push(position.ts, position.segment, position.seq);
      }
    }
    // User provenance is cheap to select. Filtering before LIMIT prevents hub turns from consuming pages.
    if (users) clauses.push(`(CASE WHEN json_extract(data, '$.userInput') IS NOT NULL THEN json_extract(data, '$.userOrigin')='person'
      WHEN json_extract(data, '$.channel') IS NOT NULL
      THEN EXISTS (SELECT 1 FROM json_each(json_extract(data, '$.channel.parts')) WHERE json_extract(value, '$.kind')='person')
      WHEN json_extract(data, '$.userOrigin') IS NOT NULL THEN json_extract(data, '$.userOrigin')='person'
      ELSE COALESCE(json_extract(data, '$.handover'), 0)=0 AND json_extract(data, '$.coordination') IS NULL
        AND COALESCE(json_extract(data, '$.scheduled'), 0)=0 AND COALESCE(json_extract(data, '$.review'), 0)=0
        AND COALESCE(json_extract(data, '$.proactive'), 0)=0 AND COALESCE(json_extract(data, '$.background'), 0)=0
        AND COALESCE(json_extract(data, '$.control'), 0)=0 AND json_extract(data, '$.dispatchRunId') IS NULL
        AND json_extract(data, '$.assistantSay') IS NULL AND COALESCE(json_extract(data, '$.source'), '') NOT IN ('background', 'voice') END)`);
    const rows = this.store.db.prepare(`SELECT * FROM (${queries.join(' UNION ALL ')}) ${clauses.length ? `WHERE ${clauses.join(' AND ')}` : ''}
      ORDER BY ts ${direction}, segment ${direction}, seq ${direction} LIMIT ?`).all(...parameters, count + 1) as unknown as Start[];
    return { selected: rows.slice(0, count), more: rows.length > count };
  }

  private turn(snapshot: HistorySnapshot, input: Record<string, unknown>, id: unknown): { thread: string; upper: number; data: Record<string, unknown> } {
    if (typeof id !== 'string' || !id) throw new HttpError(400, 'turn id is required');
    for (const segment of snapshot.segments) {
      if (input.segment !== undefined && input.segment !== segment.id) continue;
      const row = this.store.db.prepare("SELECT data FROM events WHERE thread_id=? AND turn_id=? AND seq>? AND seq<=? AND type='turn.started' LIMIT 1")
        .get(segment.id, id, segment.lowerSeq!, segment.upperSeq!) as { data: string } | undefined;
      if (row) return { thread: segment.id, upper: segment.upperSeq!, data: JSON.parse(row.data) };
    }
    throw new HttpError(404, 'turn not found in authorized history');
  }

  read(snapshot: HistorySnapshot, input: Record<string, unknown>, prefix: string) {
    this.check(snapshot);
    if (input.limit !== undefined) throw new HttpError(400, 'use --turns to page complete rounds');
    const mode = ['turn', 'tools', 'input', 'answer'].filter(key => input[key] !== undefined);
    if (mode.length > 1 || mode.length && (input.before !== undefined || input.turns !== undefined || input.userInputs || input.cursor !== undefined)
      || input.tool !== undefined && input.turn === undefined) throw new HttpError(400, 'do not combine history read modes');
    const command = (thread: string, options: string) => `${prefix} --segment ${shellArgument(thread)} --snapshot ${snapshot.id}${options}`;
    const chunk = (text: string, options: string, thread: string, upper: number) => {
      const chars = Array.from(text);
      const offset = integer(input.offset, 0, 0, chars.length);
      const nextOffset = offset + 16000 < chars.length ? offset + 16000 : null;
      return { text: chars.slice(offset, offset + 16000).join(''), offset, length: chars.length, nextOffset, snapshot: snapshot.id, snapshotSeq: upper,
        command: nextOffset === null ? null : command(thread, `${options} --offset ${nextOffset}`) };
    };
    if (input.answer !== undefined) {
      if (typeof input.answer !== 'string' || !input.answer) throw new HttpError(400, 'answer id is required');
      for (const segment of snapshot.segments) {
        if (input.segment !== undefined && input.segment !== segment.id) continue;
        const row = this.store.db.prepare(`SELECT turn_id AS turn, seq, ts, data FROM events
          WHERE thread_id=? AND seq>? AND seq<=? AND type='input.resolved'
            AND json_extract(data, '$.inputId')=? AND json_extract(data, '$.userOrigin')='person'
            AND json_type(data, '$.userInput')='text' LIMIT 1`)
          .get(segment.id, segment.lowerSeq!, segment.upperSeq!, input.answer) as { turn: string; seq: number; ts: string; data: string } | undefined;
        if (row) {
          const data = JSON.parse(row.data);
          return { threadId: segment.id, turnId: row.turn, inputId: input.answer, startSeq: row.seq, at: row.ts,
            ...chunk(userRequest(data), ` --answer ${shellArgument(input.answer)}`, segment.id, segment.upperSeq!),
            attachments: this.manager.contextAttachments(segment.id, data) };
        }
      }
      throw new HttpError(404, 'answer not found in authorized history');
    }
    if (mode.length) {
      const id = input[mode[0]];
      const turn = this.turn(snapshot, input, id);
      if (input.tools !== undefined) {
        const offset = integer(input.offset, 0);
        const rows = this.store.db.prepare("SELECT json_extract(data, '$.id') AS id FROM events WHERE thread_id=? AND turn_id=? AND seq<=? AND type IN ('tool.start','tool.update','tool.end') GROUP BY id ORDER BY MIN(seq) LIMIT 51 OFFSET ?")
          .all(turn.thread, id as string, turn.upper, offset) as Array<{ id: string }>;
        const nextOffset = rows.length > 50 ? offset + 50 : null;
        return { snapshot: snapshot.id, snapshotSeq: turn.upper, tools: rows.slice(0, 50).map(tool => ({ id: tool.id,
          command: command(turn.thread, ` --turn ${shellArgument(id as string)} --tool ${shellArgument(tool.id)}`) })), nextOffset,
          command: nextOffset === null ? null : command(turn.thread, ` --tools ${shellArgument(id as string)} --offset ${nextOffset}`) };
      }
      if (input.input !== undefined) {
        if (!isPerson(turn.data)) throw new HttpError(404, 'this turn is not a user input');
        return { turnId: id, ...chunk(userRequest(turn.data), ` --input ${shellArgument(id as string)}`, turn.thread, turn.upper), attachments: this.manager.contextAttachments(turn.thread, turn.data) };
      }
      if (input.tool !== undefined) {
        if (typeof input.tool !== 'string' || !input.tool) throw new HttpError(400, 'tool id is required');
        const full = this.store.toolHistory.read(turn.thread, id as string, input.tool, turn.upper);
        const rows = full ?? (this.store.db.prepare("SELECT type, data FROM events WHERE thread_id=? AND turn_id=? AND seq<=? AND type IN ('tool.start','tool.update','tool.end') AND json_extract(data, '$.id')=? ORDER BY seq")
          .all(turn.thread, id as string, turn.upper, input.tool) as Array<{ type: string; data: string }>).map(row => ({ type: row.type, data: JSON.parse(row.data) as Record<string, unknown> }));
        if (!rows.length) throw new HttpError(404, 'tool not found in authorized history');
        if (rows.some(row => row.data.outputTruncated)) throw new HttpError(409, 'full tool history is unavailable at this snapshot; a preview is not the full output');
        return chunk(JSON.stringify(rows), ` --turn ${shellArgument(id as string)} --tool ${shellArgument(input.tool)}`, turn.thread, turn.upper);
      }
      const text = this.store.history.handoverText(turn.thread, id as string, integer(input.offset, 0), turn.upper);
      return { ...text, snapshot: snapshot.id, command: text.nextOffset === null ? null : command(turn.thread, ` --turn ${shellArgument(id as string)} --offset ${text.nextOffset}`) };
    }
    if (input.offset !== undefined || input.cursor !== undefined && !input.userInputs) throw new HttpError(400, '--offset requires a text or tool read; --cursor requires --user-inputs');
    const users = input.userInputs === true;
    const page = this.starts(snapshot, input, users);
    const nextCursor = page.more && page.selected.length ? encodeCursor(page.selected.at(-1)!) : null;
    const suffix = input.segment === undefined ? '' : ` --segment ${shellArgument(String(input.segment))}`;
    const continuation = nextCursor === null ? null : `${prefix}${suffix} --snapshot ${snapshot.id}${users ? ' --user-inputs --cursor' : ' --before'} ${nextCursor} --turns ${integer(input.turns, 10, 1, 50)}`;
    if (users) return { view: 'user-inputs', snapshot: snapshot.id, segments: snapshot.segments, inputs: page.selected.map(row => {
      const data = JSON.parse(row.data);
      const text = userRequest(data);
      const chars = Array.from(text);
      const native = row.type === 'input.resolved';
      return { threadId: row.thread, turnId: row.turn, ...(native ? { inputId: data.inputId } : {}), startSeq: row.seq, at: row.ts,
        text: chars.slice(0, 16000).join(''), length: chars.length, truncated: chars.length > 16000,
        command: chars.length > 16000 ? command(row.thread, native ? ` --answer ${shellArgument(data.inputId)}` : ` --input ${shellArgument(row.turn)}`) : null,
        attachments: this.manager.contextAttachments(row.thread, data) };
    }), nextCursor, hasMore: page.more, command: continuation };
    const turns = page.selected.reverse().map(row => {
      const upper = snapshot.segments[row.segment].upperSeq!;
      const turn = this.store.history.handoverPage(row.thread, row.seq + 1, 1, upper).turns[0];
      const data = JSON.parse(row.data);
      return { ...turn, threadId: row.thread, question: userRequest(data), attachments: this.manager.contextAttachments(row.thread, data),
        toolsCommand: turn.toolsOmitted ? command(row.thread, ` --tools ${shellArgument(row.turn)}`) : null,
        tools: turn.tools.map(tool => ({ ...tool, command: command(row.thread, ` --turn ${shellArgument(row.turn)} --tool ${shellArgument(tool.id)}`) })),
        answer: { ...turn.answer, fullText: turn.answer.fullText ? { ...turn.answer.fullText, command: command(row.thread, ` --turn ${shellArgument(row.turn)}`) } : null } };
    });
    return { snapshot: snapshot.id, snapshotSeq: snapshot.segments.at(-1)?.upperSeq ?? 0, segments: snapshot.segments, turns, nextCursor, hasOlder: page.more, command: continuation };
  }
}
