import type { DatabaseSync } from 'node:sqlite';
import { hasDiffParts, toolText, type EventType } from './events.js';

interface Payload { seq: number; type: string; data: string }

/** Keep one full state per call; only a real read/handover boundary retains an older version. */
export class ToolHistory {
  constructor(private db: DatabaseSync) {
    db.exec(`CREATE TABLE IF NOT EXISTS context_tool_current (
      thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, turn_id TEXT NOT NULL, tool_id TEXT NOT NULL,
      seq INTEGER NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(thread_id, turn_id, tool_id));
      CREATE TABLE IF NOT EXISTS context_tool_versions (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, turn_id TEXT NOT NULL, tool_id TEXT NOT NULL,
        seq INTEGER NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(thread_id, turn_id, tool_id, seq));
      CREATE TABLE IF NOT EXISTS context_tool_boundaries (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, seq INTEGER NOT NULL, PRIMARY KEY(thread_id, seq));`);
  }

  pin(thread: string, seq: number): void {
    this.db.prepare('INSERT OR IGNORE INTO context_tool_boundaries VALUES (?, ?)').run(thread, seq);
  }

  pinSegments(segments: Array<{ id: string; upperSeq?: number }>): void {
    for (const segment of segments) if (segment.upperSeq !== undefined) this.pin(segment.id, segment.upperSeq);
  }

  record(thread: string, turn: string | null, seq: number, type: EventType, data: Record<string, unknown>): void {
    if (!turn || !['tool.start', 'tool.update', 'tool.end'].includes(type) || typeof data.id !== 'string' || !data.id) return;
    const previous = this.db.prepare('SELECT seq, type, data FROM context_tool_current WHERE thread_id=? AND turn_id=? AND tool_id=?')
      .get(thread, turn, data.id) as Payload | undefined;
    if (previous && this.db.prepare('SELECT 1 FROM context_tool_boundaries WHERE thread_id=? AND seq>=? AND seq<? LIMIT 1').get(thread, previous.seq, seq)) {
      this.db.prepare('INSERT OR IGNORE INTO context_tool_versions VALUES (?, ?, ?, ?, ?, ?)').run(thread, turn, data.id, previous.seq, previous.type, previous.data);
    }
    const old = previous ? JSON.parse(previous.data) as Record<string, unknown> : {};
    const merged = { ...old, ...Object.fromEntries(Object.entries(data).filter(([, value]) => value !== undefined)) };
    // Undefined protocol fields are absent updates, not instructions to erase earlier payloads.
    if (hasDiffParts(old.content) && !hasDiffParts(data.content)) {
      merged.content = Array.isArray(data.content)
        ? [...(old.content as Array<{ type?: string }>).filter(part => part?.type === 'diff'), ...data.content]
        : old.content;
    }
    // Keep structured output intact; deriving display text must not change its original shape.
    if (data.output === undefined) {
      const text = toolText(undefined, data.content);
      if (text) merged.output = text;
    }
    this.db.prepare('INSERT INTO context_tool_current VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(thread_id, turn_id, tool_id) DO UPDATE SET seq=excluded.seq, type=excluded.type, data=excluded.data')
      .run(thread, turn, data.id, seq, type, JSON.stringify(merged));
  }

  read(thread: string, turn: string, tool: string, upper: number): Array<{ type: string; data: Record<string, unknown> }> | null {
    const row = this.db.prepare(`SELECT seq, type, data FROM (
      SELECT seq, type, data FROM context_tool_current WHERE thread_id=? AND turn_id=? AND tool_id=?
      UNION ALL SELECT seq, type, data FROM context_tool_versions WHERE thread_id=? AND turn_id=? AND tool_id=?)
      WHERE seq<=? ORDER BY seq DESC LIMIT 1`).get(thread, turn, tool, thread, turn, tool, upper) as Payload | undefined;
    return row ? [{ type: row.type, data: JSON.parse(row.data) }] : null;
  }

  /** Message edits abandon complete turns; remove their payloads in the same event-log transaction. */
  archive(thread: string, from: number): void {
    for (const table of ['context_tool_current', 'context_tool_versions']) {
      this.db.prepare(`DELETE FROM ${table} WHERE thread_id=? AND turn_id IN
        (SELECT turn_id FROM events WHERE thread_id=? AND seq>=? AND turn_id IS NOT NULL)`).run(thread, thread, from);
    }
    this.db.prepare('DELETE FROM context_tool_boundaries WHERE thread_id=? AND seq>=?').run(thread, from);
  }

  /** Forks keep the same event identities, including any full payload pinned before their cut. */
  copy(source: string, target: string, upper: number): void {
    this.pin(target, upper);
    this.db.prepare(`INSERT INTO context_tool_current (thread_id, turn_id, tool_id, seq, type, data)
      SELECT ?, turn_id, tool_id, seq, type, data FROM (
        SELECT turn_id, tool_id, seq, type, data, ROW_NUMBER() OVER (PARTITION BY turn_id, tool_id ORDER BY seq DESC) AS rank FROM (
          SELECT turn_id, tool_id, seq, type, data FROM context_tool_current WHERE thread_id=? AND seq<=?
          UNION ALL SELECT turn_id, tool_id, seq, type, data FROM context_tool_versions WHERE thread_id=? AND seq<=?)) WHERE rank=1`)
      .run(target, source, upper, source, upper);
  }
}
