import { readReferences, type ContextReference } from './context-references';
import type { AgentMention } from './api';
import type { Note } from './timeline';

/** Unsent words in a thread's box, kept on this device so a reload does not lose them. */
export interface StoredDraft {
  mentions?: AgentMention[];
  references?: ContextReference[];
  text: string;
  /** Passages noted for this message, in order: they are numbered when sent. */
  notes: Note[];
  /** When it was last typed in, so the oldest go first once there are too many. */
  at: number;
  /**
   * Set while the message is on its way out. A page reloaded before the send came back finds the
   * message here, and cannot know by itself whether it arrived; the thread can.
   */
  sending?: Sending;
}

/** A send in progress: the text the message goes as, and the user's latest turn in the thread when it set off (null: none). */
export interface Sending {
  text: string;
  after: string | null;
}

function readSending(value: unknown): Sending | undefined {
  const v = value as { text?: unknown; after?: unknown } | null;
  if (!v || typeof v !== 'object' || typeof v.text !== 'string') return undefined;
  return { text: v.text, after: typeof v.after === 'string' ? v.after : null };
}

export const DRAFTS_KEY = 'calli.drafts';

/** Enough for every thread in use; drafts left in threads long gone make room for new ones. */
export const MAX_DRAFTS = 50;

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>;

function readNotes(value: { notes?: unknown; quote?: unknown }): StoredDraft['notes'] {
  if (Array.isArray(value.notes)) {
    return value.notes
      .filter((n): n is { quote: string; comment?: unknown; file?: unknown; source?: { threadId?: unknown; turnId?: unknown } } => !!n && typeof n.quote === 'string')
      .map((n) => ({
        quote: n.quote, comment: typeof n.comment === 'string' ? n.comment : '',
        ...(typeof n.file === 'string' && n.file ? { file: n.file } : {}),
        ...(typeof n.source?.threadId === 'string' && typeof n.source.turnId === 'string' ? { source: { threadId: n.source.threadId, turnId: n.source.turnId } } : {}),
      }));
  }
  // Drafts saved before notes held a single quote.
  return typeof value.quote === 'string' ? [{ quote: value.quote, comment: '' }] : [];
}

export function readDrafts(storage: Storage): Record<string, StoredDraft> {
  try {
    const parsed = JSON.parse(storage.getItem(DRAFTS_KEY) ?? '{}') as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Record<string, StoredDraft> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, { text?: unknown; notes?: unknown; quote?: unknown; at?: unknown; sending?: unknown; mentions?: unknown; references?: unknown }>)) {
      if (value && typeof value.text === 'string') {
        const sending = readSending(value.sending);
        const references = readReferences(value.references);
        out[key] = { ...(references.length ? { references } : {}), ...(Array.isArray(value.mentions) ? { mentions: value.mentions.filter(m => m && typeof m.id === 'string' && typeof m.label === 'string' && ['agent', 'member'].includes(m.kind) && (m.cwd === undefined || typeof m.cwd === 'string')) } : {}), text: value.text, notes: readNotes(value), at: Number(value.at) || 0, ...(sending ? { sending } : {}) };
      }
    }
    return out;
  } catch {
    return {};
  }
}

/** Keeps `draft` under `key`, or forgets it when there is nothing left to keep. */
export function writeDraft(storage: Storage, key: string, draft: { text: string; notes: StoredDraft['notes']; sending?: Sending; mentions?: AgentMention[]; references?: ContextReference[] } | null, now = Date.now()): void {
  const all = readDrafts(storage);
  if (draft && (draft.text.trim() || draft.notes.length || draft.mentions?.length || draft.references?.length)) {
    all[key] = {
      text: draft.text,
      ...(draft.references?.length ? { references: draft.references } : {}),
      ...(draft.mentions?.length ? { mentions: draft.mentions } : {}),
      notes: draft.notes.map(({ quote, comment, file, source }) => ({ quote, comment, ...(file ? { file } : {}), ...(source ? { source } : {}) })),
      at: now,
      ...(draft.sending !== undefined ? { sending: draft.sending } : {}),
    };
  } else if (key in all) delete all[key];
  else return;
  const kept = Object.entries(all)
    .sort((a, b) => b[1].at - a[1].at)
    .slice(0, MAX_DRAFTS);
  try {
    storage.setItem(DRAFTS_KEY, JSON.stringify(Object.fromEntries(kept)));
  } catch {
    // Storage full or unavailable: the draft still lasts until the page reloads.
  }
}
