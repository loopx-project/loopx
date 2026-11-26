import { splitNotes, withNotes } from './timeline';

/** Metadata only. The daemon authorizes and reads the source when requested by the agent. */
export interface ContextReference {
  id: string;
  kind: 'thread' | 'directory' | 'message';
  title: string;
  path: string;
  sourceId?: string;
  machineId?: string;
  turnId?: string;
  snapshotSeq?: number;
  pointer?: boolean;
  /** Only the auto-added source is replaced by another "new thread here" action. */
  automatic?: boolean;
  /** A visible, durable source awaiting canonical metadata; it cannot be sent as an attachment. */
  pending?: boolean;
  at: string;
}

export function readReferences(value: unknown): ContextReference[] {
  return Array.isArray(value) ? value.filter((r): r is ContextReference => r && typeof r.id === 'string'
    && ['thread', 'directory', 'message'].includes(r.kind) && typeof r.title === 'string' && typeof r.path === 'string' && typeof r.at === 'string') : [];
}

export function withReferences(references: ContextReference[], body: string): string {
  return references.length ? `<loochat-references>\n${JSON.stringify(references)}\n</loochat-references>\n\n${body.trim()}`.trim() : body.trim();
}

/** Metadata can precede or follow notes in old displays; leave the note blocks unchanged. */
export function splitReferences(text: string): { references: ContextReference[]; body: string } {
  let references: ContextReference[] = [];
  const body = text.replace(/(?:^|\n\n)<(?:loochat|calli)-references>\n([^\n]*)\n<\/(?:loochat|calli)-references>(?=\n\n|$)/g, (block, data: string) => {
    try {
      const parsed = readReferences(JSON.parse(data));
      if (!parsed.length) return block;
      references = mergeReferences(references, parsed);
      return '';
    } catch { return block; }
  });
  return { references, body: references.length ? body.replace(/^\n+/, '') : body };
}

export function mergeReferences(a: ContextReference[], b: ContextReference[]): ContextReference[] {
  return [...new Map([...a, ...b].map(r => [r.id, r])).values()];
}

export interface ReferenceAction { value: ContextReference | null; nonce: number; replaceAutomatic?: boolean }

let pendingSequence = 0;
/** Navigation keeps a visible source before metadata arrives, never a server attachment id. */
export function pendingThreadReference(source: { id: string; title: string; cwd: string }): ContextReference {
  return { id: `pending:${Date.now()}:${++pendingSequence}`, kind: 'thread', sourceId: source.id,
    title: source.title, path: source.cwd, pointer: true, pending: true, at: new Date().toISOString() };
}

/** A message snapshot keeps only its display metadata pending, never the source's history. */
export function pendingMessageReference(source: { title: string; path: string; sourceId: string; turnId: string }): ContextReference {
  return { id: `pending:${Date.now()}:${++pendingSequence}`, kind: 'message', title: source.title, path: source.path,
    sourceId: source.sourceId, turnId: source.turnId, pending: true, at: new Date().toISOString() };
}

export function replaceAutomaticReference(references: ContextReference[], source: ContextReference | null): ContextReference[] {
  return mergeReferences(references.filter(reference => !reference.automatic), source ? [{ ...source, automatic: true }] : []);
}

/** Canonical source titles can change while a send is pending; only body and attachment ids identify it. */
export function sameReferencedMessage(a: string, b: string): boolean {
  const left = splitReferences(a);
  const right = splitReferences(b);
  return left.body.trim() === right.body.trim()
    && JSON.stringify(left.references.map(reference => reference.id).sort()) === JSON.stringify(right.references.map(reference => reference.id).sort());
}

/** A note owns its read permission; removing it cannot leave an invisible pending grant. */
export function attachedReferences(references: ContextReference[], notes: Array<{ reference?: ContextReference }>): ContextReference[] {
  return mergeReferences(references, notes.flatMap(note => note.reference ? [note.reference] : []));
}

/** Recover note-bound permissions out of the metadata block used by old drafts and queued turns. */
export function recoverNoteReferences<T extends { source?: { threadId: string; turnId: string }; reference?: ContextReference }>(notes: T[], references: ContextReference[]): { notes: T[]; references: ContextReference[] } {
  const used = new Set<string>();
  const recovered = notes.map(note => {
    const reference = note.reference ?? references.find(value => value.kind === 'thread' && value.turnId === note.source?.turnId && value.sourceId === note.source?.threadId);
    if (!reference) return note;
    used.add(reference.id);
    return { ...note, reference };
  });
  return { notes: recovered, references: references.filter(reference => !used.has(reference.id)) };
}

/** Search every conversation; source and same-directory threads stay ahead of the default shortlist. */
export function referenceThreads<T extends { id: string; title: string; cwd: string }>(threads: T[], source: { cwd: string; threadId?: string; sourceId?: string }, query: string): T[] {
  const term = query.trim().toLowerCase();
  const rank = (thread: T) => thread.id === source.sourceId ? 0 : thread.cwd === source.cwd ? 1 : 2;
  const matches = threads.filter(thread => thread.id !== source.threadId && `${thread.title} ${thread.cwd}`.toLowerCase().includes(term))
    .sort((a, b) => rank(a) - rank(b));
  return term ? matches : [...matches.filter(thread => rank(thread) < 2), ...matches.filter(thread => rank(thread) === 2).slice(0, 8)];
}

/** Attaches a fallback reference after leading notes, skipping duplicates or texts already at the limit. */
export function attachFallbackReference(text: string, reference?: ContextReference | null): string {
  if (!reference) return text;
  const noted = splitNotes(text);
  const referenced = splitReferences(noted.body);
  if (reference.kind === 'thread' && reference.sourceId
    && referenced.references.some(item => item.kind === 'thread' && item.sourceId === reference.sourceId)) return text;
  if (referenced.references.length >= 12) return text;
  return withNotes(noted.notes, withReferences(mergeReferences(referenced.references, [reference]), referenced.body));
}

/** Compact queue/search labels never expose the metadata wire format. */
export function referencePreview(text: string): string {
  return text.replace(/(?:^|\n\n)<(?:loochat|calli)-references>\n([^\n]*)\n<\/(?:loochat|calli)-references>(?=\n\n|$)/g, (_, data: string) => {
    try { return readReferences(JSON.parse(data)).map(r => r.title).join(' · '); }
    catch { return ''; }
  }).replace(/\n\n/g, '\n');
}
