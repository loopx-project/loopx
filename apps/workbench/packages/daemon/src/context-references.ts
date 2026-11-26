import { randomUUID } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { basename } from 'node:path';
import { HttpError } from './errors.js';
import { listProjectDirectories } from './project-directories.js';
import { ContextHistory, integer, shellArgument, type HistorySnapshot } from './context-history.js';
import type { ChainSegment } from './handover.js';
import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';

export interface ContextReference {
  id: string;
  kind: 'thread' | 'directory' | 'message';
  title: string;
  path: string;
  sourceId?: string;
  /** Stable installation identity, not an address or an agent credential. */
  machineId?: string;
  turnId?: string;
  snapshotSeq?: number;
  /** New conversation pointers are live until a read freezes a snapshot. Old references stay fixed. */
  pointer?: boolean;
  segments?: ChainSegment[];
  at: string;
}
const WIRE = /(?:^|\n\n)<(?:loochat|calli)-references>\n([^\n]*)\n<\/(?:loochat|calli)-references>(?=\n\n|$)/g;
export const referenceBody = (text: string) => text.replace(WIRE, '').replace(/(?:^|\n\n)<(?:loochat|calli)-reference-reader>[\s\S]*?<\/(?:loochat|calli)-reference-reader>(?=\n|$)/g, '').trim();

/** Display envelopes carry no authority; every caller supplies an independently validated set. */
export function referenceDisplay(text: string, references: ContextReference[]): string {
  const body = referenceBody(text);
  return references.length ? `<loochat-references>\n${JSON.stringify(references)}\n</loochat-references>\n\n${body}`.trim() : body;
}

/** Browser attachment metadata and read grants are deliberately separate from prompt text. */
export class ContextReferences {
  readonly history: ContextHistory;
  readonly machineId: string;
  constructor(private store: Store, private manager: ThreadManager) {
    const saved = store.hubSetting<unknown>('contextMachineId', null);
    this.machineId = typeof saved === 'string' && saved ? saved : randomUUID();
    if (saved !== this.machineId) store.saveHubSetting('contextMachineId', this.machineId);
    store.db.exec(`CREATE TABLE IF NOT EXISTS context_references (id TEXT PRIMARY KEY, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS context_reference_grants (
        owner TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        reference TEXT NOT NULL REFERENCES context_references(id) ON DELETE CASCADE,
        PRIMARY KEY(owner, reference));`);
    const columns = store.db.prepare('PRAGMA table_info(context_reference_grants)').all() as Array<{ name: string }>;
    if (!columns.some(column => column.name === 'at_seq')) store.db.exec('ALTER TABLE context_reference_grants ADD COLUMN at_seq INTEGER NOT NULL DEFAULT 0');
    store.db.exec(`CREATE TABLE IF NOT EXISTS context_reference_messages (
      reference TEXT PRIMARY KEY REFERENCES context_references(id) ON DELETE CASCADE, text TEXT NOT NULL);`);
    this.history = new ContextHistory(store, manager);
  }

  create(input: Record<string, unknown>): ContextReference {
    const base = { id: randomUUID(), at: new Date().toISOString() };
    let reference: ContextReference;
    if (input.kind === 'message') {
      if (typeof input.text !== 'string' || !input.text.trim() || input.text.length > 2_000_000
        || typeof input.title !== 'string' || input.title.length > 500 || typeof input.path !== 'string' || input.path.length > 4096) throw new HttpError(400, 'expected a message with source title and path');
      // Portable message snapshots never grant access merely because their metadata names a source.
      reference = { ...base, kind: 'message', title: input.title, path: input.path,
        ...(typeof input.sourceId === 'string' ? { sourceId: input.sourceId } : {}),
        ...(typeof input.turnId === 'string' ? { turnId: input.turnId } : {}) };
    } else if (input.kind === 'thread' && typeof input.sourceId === 'string') {
      if (input.portable === true && (typeof input.machineId !== 'string' || !input.machineId)) throw new HttpError(409, 'portable reference has no machine scope; paste the original text instead');
      if (input.machineId !== undefined && input.machineId !== this.machineId) throw new HttpError(409, 'source conversation belongs to another machine; paste the original text instead');
      const source = this.store.getThread(input.sourceId);
      if (!source) throw new HttpError(404, 'source conversation does not exist on this machine; paste the original text instead');
      if (input.turnId !== undefined && (typeof input.turnId !== 'string' || !this.store.db.prepare("SELECT 1 FROM events WHERE thread_id=? AND turn_id=? AND type='turn.started'").get(source.id, input.turnId))) throw new HttpError(404, 'quoted turn does not exist in the source conversation');
      const snapshotSeq = this.store.history.latest(source.id);
      reference = { ...base, kind: 'thread', title: source.title, path: source.cwd, sourceId: source.id, machineId: this.machineId,
        ...(input.pointer === true ? { pointer: true } : { snapshotSeq }),
        ...(typeof input.turnId === 'string' ? { turnId: input.turnId, snapshotSeq, segments: this.manager.handoverChain(source) } : {}) };
    } else if (input.kind === 'directory' && typeof input.path === 'string') {
      const dir = listProjectDirectories(this.manager.allowedWorkdirs, input.path);
      const path = realpathSync(dir.path);
      reference = { ...base, kind: 'directory', title: basename(path) || path, path };
    } else throw new HttpError(400, 'expected a thread or directory reference');
    this.store.transaction(() => {
      this.store.db.prepare('INSERT INTO context_references VALUES (?, ?)').run(reference.id, JSON.stringify(reference));
      if (reference.kind === 'thread' && reference.snapshotSeq !== undefined) this.store.toolHistory.pinSegments(this.segments(reference));
      if (reference.kind === 'message') this.store.db.prepare('INSERT INTO context_reference_messages VALUES (?, ?)').run(reference.id, input.text as string);
    });
    return reference;
  }

  get(id: unknown): ContextReference {
    if (typeof id !== 'string') throw new HttpError(400, 'reference id is required');
    const row = this.store.db.prepare('SELECT data FROM context_references WHERE id=?').get(id) as { data: string } | undefined;
    if (!row) throw new HttpError(404, 'reference no longer exists');
    return JSON.parse(row.data) as ContextReference;
  }

  private check(reference: ContextReference) {
    if (reference.kind === 'message') return;
    if (reference.kind === 'directory') {
      listProjectDirectories(this.manager.allowedWorkdirs, reference.path);
      if (realpathSync(reference.path) !== reference.path) throw new HttpError(409, 'referenced directory has moved');
      return;
    }
    if (!this.store.getThread(reference.sourceId!)) throw new HttpError(404, 'source conversation no longer exists on this machine');
    if (reference.snapshotSeq !== undefined && (this.store.db.prepare("SELECT 1 FROM events WHERE thread_id=? AND seq>? AND type='thread.historyChanged' LIMIT 1").get(reference.sourceId!, reference.snapshotSeq)
      || this.store.history.latest(reference.sourceId!) < reference.snapshotSeq)) throw new HttpError(409, 'referenced history changed; select the conversation again');
  }

  granted(owner: string, id: string): boolean {
    return Boolean(this.store.db.prepare('SELECT 1 FROM context_reference_grants WHERE owner=? AND reference=?').get(owner, id));
  }

  ids(owner: string, through = Number.MAX_SAFE_INTEGER): string[] {
    return (this.store.db.prepare('SELECT reference FROM context_reference_grants WHERE owner=? AND at_seq<=? ORDER BY rowid').all(owner, through) as Array<{ reference: string }>).map(row => row.reference);
  }

  /** Canonical wire is retained for UI recovery, but only the separate human attachment field grants. */
  prepare(owner: string, text: string, fromUser: boolean, handoverFrom?: string, attached?: unknown, inherited: readonly string[] = []): { text: string; displayText: string; references: ContextReference[] } {
    const ids = new Set<unknown>();
    if (attached !== undefined) {
      if (!fromUser) throw new HttpError(403, 'only an authenticated human send can attach references');
      if (!Array.isArray(attached) || attached.length > 12 || attached.some(id => typeof id !== 'string')) throw new HttpError(400, 'references must contain at most 12 reference ids');
      attached.forEach(id => ids.add(id));
    } else {
      for (const match of text.matchAll(WIRE)) {
        let values: unknown;
        try { values = JSON.parse(match[1]); } catch { throw new HttpError(400, 'invalid context references'); }
        if (!Array.isArray(values) || values.length > 12) throw new HttpError(400, 'at most 12 references may be attached');
        for (const value of values) {
          const id = value?.id;
          if (typeof id !== 'string' || !this.granted(owner, id) && !(handoverFrom && inherited.includes(id) && this.granted(handoverFrom, id))) throw new HttpError(403, 'send visible references in the separate attachment field');
          ids.add(id);
        }
      }
    }
    if (ids.size > 12) throw new HttpError(400, 'at most 12 references may be attached');
    const references = [...ids].map(id => {
      const reference = this.get(id);
      this.check(reference);
      // Never trust the clipboard or the display wire for a source title or path.
      const source = reference.kind === 'thread' ? this.store.getThread(reference.sourceId!) : null;
      return source ? { ...reference, title: source.title, path: source.cwd } : reference;
    });
    const body = referenceBody(text);
    const displayText = referenceDisplay(body, references);
    const instructions = references.map(reference => reference.kind === 'directory'
      ? `参考目录 ${JSON.stringify(reference.path)}：只读相关文件，不切换工作目录。`
      : `参考${reference.kind === 'message' ? '消息' : '会话'} ${JSON.stringify(reference.title)}：\`loochat references ${reference.id}${reference.kind === 'thread' ? reference.turnId ? ` --turn ${shellArgument(reference.turnId)}` : ' --user-inputs' : ''}\`（只读；按返回的 command 继续）。`);
    return { references, displayText, text: [body, instructions.join('\n')].filter(Boolean).join('\n\n') };
  }

  grant(owner: string, references: ContextReference[]) {
    for (const reference of references) this.store.db.prepare('INSERT OR IGNORE INTO context_reference_grants (owner, reference, at_seq) VALUES (?, ?, ?)').run(owner, reference.id, this.store.history.latest(owner) + 1);
  }

  /** Copy the set once at the handover boundary; later grants on the predecessor are not followed. */
  inherit(source: string, target: string, ids = this.ids(source)) {
    this.grant(target, ids.map(id => this.get(id)));
  }

  private segments(reference: ContextReference): ChainSegment[] {
    if (reference.kind !== 'thread') return [];
    if (reference.segments) return reference.segments;
    if (!reference.pointer) {
      const source = this.manager.getThread(reference.sourceId!);
      return [{ id: source.id, title: source.title, updatedAt: source.updatedAt, archived: Boolean(source.archivedAt), lowerSeq: 0, upperSeq: reference.snapshotSeq! }];
    }
    return this.manager.handoverChain({ id: reference.sourceId! });
  }

  private snapshot(owner: string, reference: ContextReference, input: Record<string, unknown>, scope: string, nested = false): HistorySnapshot {
    if (input.snapshot !== undefined) return this.history.get(owner, scope, input.snapshot);
    if (reference.pointer && reference.snapshotSeq === undefined && (input.before !== undefined || input.cursor !== undefined || integer(input.offset, 0) > 0)) throw new HttpError(400, 'continue with the snapshot and command from the previous response');
    const segments = this.segments(reference);
    const refs = nested || !reference.pointer ? [] : segments.flatMap(segment => this.ids(segment.id, segment.upperSeq ?? this.store.history.latest(segment.id)));
    return this.history.freeze(owner, scope, segments, refs);
  }

  read(owner: string, input: Record<string, unknown>): unknown {
    if (owner === 'operator') throw new HttpError(403, 'references require thread-scoped credentials');
    const root = this.get(input.id);
    if (!this.granted(owner, root.id)) throw new HttpError(403, 'reference is not attached to this conversation');
    this.check(root);
    let reference = root;
    let prefix = `loochat references ${root.id}`;
    let scope = `reference:${root.id}`;
    let nested = false;
    if (input.via !== undefined) {
      const parent = this.history.get(owner, scope, input.parentSnapshot);
      if (typeof input.via !== 'string' || !parent.references.includes(input.via)) throw new HttpError(403, 'reference is outside the pointer\'s one-level scope');
      reference = this.get(input.via);
      this.check(reference);
      nested = true;
      scope += `:via:${reference.id}:${parent.id}`;
      prefix += ` --via ${reference.id} --parent-snapshot ${parent.id}`;
    } else if (input.parentSnapshot !== undefined) throw new HttpError(400, '--parent-snapshot requires --via');
    if (reference.kind === 'directory') return reference;
    if (reference.kind === 'message') {
      if (['turn', 'tool', 'tools', 'turns', 'before', 'userInputs', 'input', 'answer', 'cursor', 'segment', 'snapshot'].some(key => input[key] !== undefined)) throw new HttpError(400, 'a message reference only accepts --offset');
      const row = this.store.db.prepare('SELECT text FROM context_reference_messages WHERE reference=?').get(reference.id) as { text: string } | undefined;
      if (!row) throw new HttpError(404, 'message snapshot not found');
      const chars = Array.from(row.text);
      const offset = integer(input.offset, 0, 0, chars.length);
      const nextOffset = offset + 16000 < chars.length ? offset + 16000 : null;
      return { reference, text: chars.slice(offset, offset + 16000).join(''), offset, length: chars.length, nextOffset,
        command: nextOffset === null ? null : `${prefix} --offset ${nextOffset}` };
    }
    const snapshot = this.snapshot(owner, reference, input, scope, nested);
    const result = this.history.read(snapshot, input, prefix);
    return { ...result, reference, ...(!nested ? { references: snapshot.references.map(id => {
      const value = this.get(id);
      return { id, kind: value.kind, title: value.title,
        command: `loochat references ${root.id} --via ${id} --parent-snapshot ${snapshot.id}${value.kind === 'thread' ? ' --user-inputs' : ''}` };
    }) } : {}) };
  }

  /** Dynamic permission checks share exactly the route shown by the handover outline. */
  async command(owner: string, input: Record<string, unknown>) {
    if (owner === 'operator') throw new HttpError(403, 'history requires thread-scoped credentials');
    this.manager.getThread(owner);
    const chain = this.manager.handoverChain({ id: owner });
    if (input.op === 'handover') return { threadId: owner, scope: 'self-and-ancestors',
      segments: await Promise.all(chain.map(async segment => ({ ...segment, ...await this.manager.handoverLocation(segment.id),
        command: `loochat history ${shellArgument(segment.id)} --user-inputs` }))), missingSource: null };
    if (input.op !== 'history') throw new HttpError(403, 'history credentials are read-only');
    if (typeof input.target !== 'string' || !input.target) throw new HttpError(400, 'thread id is required');
    let segments = chain.filter(segment => segment.id === input.target);
    for (const id of this.ids(owner)) {
      const reference = this.get(id);
      if (reference.kind !== 'thread') continue;
      const allowed = this.segments(reference).filter(segment => segment.id === input.target);
      if (allowed.length) { this.check(reference); segments.push(...allowed); }
    }
    if (!segments.length && this.manager.forkFamilyIds(owner).has(input.target)) {
      const targetThread = this.store.getThread(input.target);
      if (targetThread) {
        segments.push({
          id: targetThread.id,
          title: targetThread.title,
          updatedAt: targetThread.updatedAt,
          archived: Boolean(targetThread.archivedAt),
          lowerSeq: 0,
          upperSeq: this.store.history.latest(targetThread.id),
        });
      }
    }
    if (!segments.length) throw new HttpError(403, 'thread outside handover chain or attached references');
    // Merge overlapping grants, without filling a gap between disjoint authorized stretches.
    segments = segments.sort((a, b) => (a.lowerSeq ?? 0) - (b.lowerSeq ?? 0)).reduce<ChainSegment[]>((out, segment) => {
      const previous = out.at(-1);
      if (previous && (previous.upperSeq ?? 0) >= (segment.lowerSeq ?? 0)) previous.upperSeq = Math.max(previous.upperSeq ?? 0, segment.upperSeq ?? 0);
      else out.push({ ...segment });
      return out;
    }, []);
    const scope = `history:${input.target}`;
    let snapshot: HistorySnapshot;
    if (typeof input.snapshot === 'string' && !/^\d+$/.test(input.snapshot)) snapshot = this.history.get(owner, scope, input.snapshot);
    else {
      if ((input.before !== undefined || input.cursor !== undefined || integer(input.offset, 0) > 0) && input.snapshot === undefined) throw new HttpError(400, 'continuing a read requires --snapshot from the previous response');
      if (input.snapshot !== undefined) {
        const upper = integer(input.snapshot, 0);
        if (upper > Math.max(...segments.map(segment => segment.upperSeq ?? 0))) throw new HttpError(403, 'snapshot exceeds authorized history');
        segments = segments.map(segment => ({ ...segment, upperSeq: Math.min(segment.upperSeq ?? 0, upper) }));
      }
      snapshot = this.history.freeze(owner, scope, segments);
    }
    // Detaching a link invalidates existing tokens instead of making them an authority of their own.
    if (snapshot.segments.some(saved => !segments.some(segment => (segment.lowerSeq ?? 0) <= saved.lowerSeq! && (segment.upperSeq ?? 0) >= saved.upperSeq!))) throw new HttpError(403, 'snapshot is no longer in the authorized chain');
    return this.history.read(snapshot, input, `loochat history ${shellArgument(input.target)}`);
  }
}
