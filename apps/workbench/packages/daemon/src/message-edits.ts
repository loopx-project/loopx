import { createHash } from 'node:crypto';
import type { DatabaseSync } from 'node:sqlite';

export interface MessageEdit {
  threadId: string;
  requestId: string;
  turnId: string;
  revision: number;
  fingerprint: string;
  sourceSession: string;
  branchSession: string | null;
  newTurnId: string | null;
  state: 'creating' | 'ready' | 'committed' | 'failed';
}

/** Hidden recovery records, never represented as another user-visible thread. */
export class MessageEdits {
  constructor(private readonly db: DatabaseSync) {
    db.exec(`
      CREATE TABLE IF NOT EXISTS event_sequences (
        thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
        last_seq INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS message_edits (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, turn_id TEXT NOT NULL, revision INTEGER NOT NULL,
        fingerprint TEXT NOT NULL, source_session TEXT NOT NULL, branch_session TEXT,
        new_turn_id TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(thread_id, request_id));
      CREATE TABLE IF NOT EXISTS edited_events (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, seq INTEGER NOT NULL, turn_id TEXT,
        type TEXT NOT NULL, ts TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY(thread_id, request_id, seq));
    `);
  }
  static fingerprint(value: unknown): string { return createHash('sha256').update(JSON.stringify(value)).digest('hex'); }
  get(threadId: string, requestId: string): MessageEdit | null {
    const row = this.db.prepare('SELECT * FROM message_edits WHERE thread_id=? AND request_id=?').get(threadId, requestId);
    return row ? {
      threadId, requestId, turnId: String(row.turn_id), revision: Number(row.revision), fingerprint: String(row.fingerprint),
      sourceSession: String(row.source_session), branchSession: row.branch_session as string | null,
      newTurnId: row.new_turn_id as string | null, state: row.state as MessageEdit['state'],
    } : null;
  }
  reserve(edit: Omit<MessageEdit, 'branchSession' | 'newTurnId' | 'state'>): void {
    this.db.prepare('INSERT INTO message_edits VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, \'creating\', ?)')
      .run(edit.threadId, edit.requestId, edit.turnId, edit.revision, edit.fingerprint, edit.sourceSession, new Date().toISOString());
  }
  ready(thread: string, request: string, session: string): void {
    this.db.prepare("UPDATE message_edits SET branch_session=?, state='ready' WHERE thread_id=? AND request_id=?").run(session, thread, request);
  }
  failed(thread: string, request: string): void {
    this.db.prepare("UPDATE message_edits SET state='failed' WHERE thread_id=? AND request_id=?").run(thread, request);
  }
  commit(thread: string, request: string, turn: string): void {
    this.db.prepare("UPDATE message_edits SET new_turn_id=?, state='committed' WHERE thread_id=? AND request_id=?").run(turn, thread, request);
  }
  /** Reserve all old sequence numbers before removing the abandoned suffix. */
  archive(thread: string, request: string, from: number): void {
    this.db.prepare(`INSERT INTO event_sequences VALUES (?, (SELECT COALESCE(MAX(seq),0) FROM events WHERE thread_id=?))
      ON CONFLICT(thread_id) DO UPDATE SET last_seq=MAX(last_seq, excluded.last_seq)`).run(thread, thread);
    this.db.prepare('INSERT INTO edited_events SELECT thread_id, ?, seq, turn_id, type, ts, data FROM events WHERE thread_id=? AND seq>=?').run(request, thread, from);
    this.db.prepare('DELETE FROM turn_text WHERE thread_id=? AND turn_id IN (SELECT turn_id FROM events WHERE thread_id=? AND seq>=?)').run(thread, thread, from);
    this.db.prepare('DELETE FROM events WHERE thread_id=? AND seq>=?').run(thread, from);
    this.db.prepare('DELETE FROM history_rows WHERE thread_id=? AND seq>=?').run(thread, from);
    this.db.prepare('DELETE FROM history_details WHERE thread_id=? AND end_seq>=?').run(thread, from);
    this.db.prepare('DELETE FROM history_turns WHERE thread_id=?').run(thread);
    this.db.prepare('DELETE FROM history_coverage WHERE thread_id=?').run(thread);
  }
  /** Retired native sessions stay associated with this thread and cannot be imported as new ones. */
  owner(agent: string, session: string): string | null {
    const row = this.db.prepare(`SELECT e.thread_id FROM message_edits e JOIN threads t ON t.id=e.thread_id
      WHERE t.agent=? AND (e.source_session=? OR e.branch_session=?) LIMIT 1`).get(agent, session, session);
    return row ? String(row.thread_id) : null;
  }
}
