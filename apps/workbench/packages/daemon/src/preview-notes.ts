import { randomUUID } from 'node:crypto';
import { HttpError } from './errors.js';
import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';

type Receipt = { turnId: string } | { queued: true; id: string };
export interface PreviewNote {
  id: string;
  page: string;
  at: string;
  comment: string;
  target: { selector: string; text: string; rect: { x: number; y: number; width: number; height: number } };
  context: { title: string; url: string; theme: string; variant: string; revision: string; viewport: { width: number; height: number } };
  receipt: Receipt | null;
}
const object = (v: unknown): Record<string, any> => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new HttpError(400, 'invalid preview annotation');
  return v as Record<string, any>;
};
const text = (v: unknown, max: number, required = false): string => {
  if (v === undefined && !required) return '';
  if (typeof v !== 'string' || v.length > max || required && !v.trim()) throw new HttpError(400, 'invalid preview annotation text');
  return v;
};
const dimensions = <K extends string>(raw: unknown, keys: K[]): Record<K, number> => {
  const value = object(raw);
  return Object.fromEntries(keys.map(key => {
    const n = value[key];
    if (typeof n !== 'number' || !Number.isFinite(n) || Math.abs(n) > 100000 || ['width', 'height'].includes(key) && n < 0) throw new HttpError(400, 'invalid preview dimensions');
    return [key, n];
  })) as Record<K, number>;
};

/** User-owned drafts. HTML is a separate column, loaded only for an explicit detail read. */
export class PreviewNotes {
  private sending = new Map<string, Promise<Receipt>>();
  constructor(private store: Store, private manager: ThreadManager) {
    store.db.exec(`CREATE TABLE IF NOT EXISTS preview_notes (
      id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
      page TEXT NOT NULL, data TEXT NOT NULL, html TEXT NOT NULL, receipt TEXT
    ); CREATE INDEX IF NOT EXISTS preview_notes_page ON preview_notes(thread_id, page);`);
  }
  list(owner: string, page: unknown): PreviewNote[] {
    this.manager.getThread(owner);
    return (this.store.db.prepare('SELECT data, receipt FROM preview_notes WHERE thread_id=? AND page=? ORDER BY rowid').all(owner, text(page, 4096, true)) as Array<{data: string; receipt: string | null}>)
      .map(row => ({ ...JSON.parse(row.data), receipt: row.receipt ? JSON.parse(row.receipt) : null }));
  }
  create(owner: string, raw: unknown): PreviewNote {
    this.manager.getThread(owner);
    const value = object(raw), target = object(value.target), context = object(value.context);
    const count = this.store.db.prepare('SELECT count(*) AS n FROM preview_notes WHERE thread_id=? AND receipt IS NULL').get(owner) as { n: number };
    if (count.n >= 100) throw new HttpError(409, 'at most 100 draft annotations per conversation');
    const note: PreviewNote = {
      id: randomUUID(), page: text(value.page, 4096, true), at: new Date().toISOString(), comment: text(value.comment, 2000, true).trim(),
      target: { selector: text(target.selector, 1500, true), text: text(target.text, 300), rect: dimensions(target.rect, ['x', 'y', 'width', 'height']) },
      context: { title: text(context.title, 300), url: text(context.url, 4096), theme: text(context.theme, 40), variant: text(context.variant, 100), revision: text(context.revision, 200), viewport: dimensions(context.viewport, ['width', 'height']) },
      receipt: null,
    };
    const html = text(target.html, 6000);
    this.store.db.prepare('INSERT INTO preview_notes VALUES (?, ?, ?, ?, ?, NULL)').run(note.id, owner, note.page, JSON.stringify(note), html);
    return note;
  }
  read(owner: string, id: unknown, sentOnly = false, withHtml = true): PreviewNote & { html?: string } {
    this.manager.getThread(owner);
    const row = this.store.db.prepare(`SELECT data, ${withHtml ? 'html,' : ''} receipt FROM preview_notes WHERE thread_id=? AND id=?`).get(owner, text(id, 100, true)) as { data: string; html: string; receipt: string | null } | undefined;
    if (!row || sentOnly && !row.receipt) throw new HttpError(404, 'annotation not found in this conversation');
    return { ...JSON.parse(row.data), ...(withHtml ? { html: row.html } : {}), receipt: row.receipt ? JSON.parse(row.receipt) : null };
  }
  remove(owner: string, id: unknown) {
    if (this.sending.has(owner)) throw new HttpError(409, 'annotations are being sent');
    const note = this.read(owner, id);
    if (note.receipt) throw new HttpError(409, 'sent annotations are immutable');
    this.store.db.prepare('DELETE FROM preview_notes WHERE thread_id=? AND id=?').run(owner, note.id);
  }
  send(owner: string, raw: unknown): Promise<Receipt> {
    const value = object(raw);
    if (!Array.isArray(value.ids) || value.ids.length < 1 || value.ids.length > 20 || new Set(value.ids).size !== value.ids.length) throw new HttpError(400, 'send 1 to 20 distinct annotations');
    const page = text(value.page, 4096, true), ids = value.ids.map((id: unknown) => text(id, 100, true));
    const previous = this.sending.get(owner) ?? Promise.resolve();
    const next = previous.catch(() => undefined).then(async () => {
      const notes = ids.map((id: string) => this.read(owner, id, false, false));
      if (notes.some((n: PreviewNote) => n.page !== page)) throw new HttpError(400, 'annotations belong to a different preview');
      if (notes.every((n: PreviewNote) => n.receipt)) return notes[0].receipt!;
      if (notes.some((n: PreviewNote) => n.receipt)) throw new HttpError(409, 'some annotations were already sent; refresh the preview');
      const quote = (s: string) => s.split('\n').map(line => `> ${line}`).join('\n');
      const message = notes.map((n: PreviewNote, i: number) => [
        `[注释 ${i + 1}]`, quote(`网页：${n.page}\n元素：${n.target.selector}\n文字：${n.target.text}\n外观：${n.context.variant} · ${n.context.theme} · ${n.context.viewport.width}×${n.context.viewport.height}\n捕获：${n.at}${n.context.revision ? ` · ${n.context.revision}` : ''}\n详情：loochat preview-note ${n.id}`),
        `评论：${n.comment.replace(/\s*\n\s*/g, ' ')}`,
      ].join('\n')).join('\n\n');
      const record = (receipt: Receipt) => { for (const id of ids) this.store.db.prepare('UPDATE preview_notes SET receipt=? WHERE thread_id=? AND id=?').run(JSON.stringify(receipt), owner, id); };
      await this.manager.coordination?.userSpoke(owner);
      const receipt = await this.manager.startTurn(owner, { text: message, queue: true, fromUser: true }, undefined, false, undefined, turnId => record({ turnId }));
      // A running thread uses the existing queue. Idle acceptance records with turn.started atomically.
      if ('queued' in receipt) this.store.transaction(() => record(receipt));
      return receipt;
    });
    this.sending.set(owner, next);
    const clean = () => { if (this.sending.get(owner) === next) this.sending.delete(owner); };
    void next.then(clean, clean);
    return next;
  }
}
