import { Api, AuthError } from './api';
import type { DetailRef, StoredEvent, Item } from './timeline';

export interface HistoryPage { turnIds?: string[]; events: StoredEvent[]; snapshotSeq: number; nextCursor: number | null; hasOlder: boolean }
export interface HistoryDetail { version: number; text?: string; input?: unknown; output?: string; content?: unknown; nextCursor: number | null; toolId?: string | null; outputTruncated?: boolean; outputOmitted?: number; fullAvailable?: boolean }
export type WorkFiles = NonNullable<Extract<Item, { kind: 'work' }>['files']>;
export interface HistoryWork { files?: WorkFiles; version: number; events: StoredEvent[]; nextCursor: number | null }
export type ResourceKind = 'work' | 'detail' | 'file';

/** Replace only the turns covered by a reconnect snapshot; keep previously paged history. */
export function mergeHistory(previous: StoredEvent[], updates: StoredEvent[]): StoredEvent[] {
  let out = previous;
  for (const ev of updates) {
    if (ev.type === 'history.reset') {
      const turns = new Set<string>(ev.data.turns);
      const received = out.flatMap(e => e.type === 'history.work' ? e.data.liveEvents ?? [] : e.type.startsWith('tool.') || e.type === 'reasoning.delta' ? [e] : []) as StoredEvent[];
      const starts = new Map(received.filter(e => e.type === 'tool.start').map(e => [`${e.turnId}:${e.data.id}`, e.seq]));
      const snapshot = ev.data.events as StoredEvent[];
      const events = snapshot.map((event, i) => {
        if (event.type !== 'history.work') return event;
        const until = snapshot.slice(i + 1).find(e => e.turnId === event.turnId && e.type === 'history.work')?.seq ?? Infinity;
        const local = received.filter(e => {
          if (e.turnId !== event.turnId || e.seq > ev.seq) return false;
          const start = e.type.startsWith('tool.') ? starts.get(`${e.turnId}:${e.data.id}`) : e.seq;
          return start === undefined || start >= event.seq && start < until;
        });
        return { ...event, data: { ...event.data, liveEvents: [...new Map(local.map(e => [e.seq, e])).values()] } };
      });
      const seqs = new Set(events.map(e => e.seq));
      const newer = out.filter(e => e.seq > ev.seq);
      out = [...out.filter(e => e.seq <= ev.seq && !turns.has(e.turnId ?? '') && !seqs.has(e.seq)), ...events, ...newer].sort((a,b) => a.seq - b.seq);
    } else if (!out.length || ev.seq > out[out.length - 1].seq) {
      out = [...out, ev];
    }
  }
  return out;
}

export async function historyPage(api: Api, id: string, before?: number, after?: number, chain = false): Promise<HistoryPage> {
  if (api.demo) {
    const events = before ? await api.eventsBefore(id, before) : await api.eventsTail(id);
    const nextCursor = (events[0]?.seq ?? 0) > 1 ? events[0].seq : null;
    return { events, nextCursor, hasOlder: nextCursor !== null, snapshotSeq: events.at(-1)?.seq ?? 0 };
  }
  return api.historyPage(id, before, after, chain);
}

/** All conversation surfaces share snapshot/stream ordering and batched delivery. */
export async function watchHistory(api: Api, id: string, signal: AbortSignal, callbacks: {
  page: (page: HistoryPage) => void;
  events: (events: StoredEvent[]) => void;
  error: (error: unknown) => void;
  connection?: (connected: boolean) => void;
  minimumSeq?: number;
  chain?: boolean;
}) {
  let after = 0;
  let loaded = false;
  let buffer: StoredEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  const flush = () => {
    clearTimeout(timer); timer = undefined;
    const batch = buffer; buffer = [];
    if (!signal.aborted && batch.length) callbacks.events(batch);
  };
  try {
    while (!signal.aborted) {
      try {
        if (!loaded) {
          const page = await historyPage(api, id, undefined, callbacks.minimumSeq, callbacks.chain);
          if (signal.aborted) return;
          after = page.snapshotSeq; callbacks.page(page); loaded = true;
        }
        callbacks.connection?.(true);
        await api.stream(`/api/threads/${id}/stream?view=history&after=${after}${callbacks.chain ? '&chain=1' : ''}`, (ev: StoredEvent) => {
          after = Math.max(after, ev.seq);
          buffer.push(ev);
          timer ??= setTimeout(flush, 40);
        }, signal);
      } catch (error) {
        if (signal.aborted) return;
        if (!loaded || error instanceof AuthError) callbacks.error(error);
        if (error instanceof AuthError) return;
      }
      flush(); callbacks.connection?.(false);
      await new Promise<void>(resolve => {
        if (signal.aborted) return resolve();
        const done = () => { clearTimeout(wait); signal.removeEventListener('abort', done); resolve(); };
        const wait = setTimeout(done, 1500);
        signal.addEventListener('abort', done, { once: true });
      });
    }
  } finally { clearTimeout(timer); }
}

/** Scoped to one mounted thread/API instance; bound memory and share concurrent requests. */
let clientIdentity = 0;
export class HistoryClient {
  readonly identity = ++clientIdentity;
  private cache = new Map<string, { value: unknown; bytes: number }>();
  private pending = new Map<string, Promise<any>>();
  private bytes = 0;
  constructor(readonly api: Api, readonly threadId: string, private onWork?: (ref: DetailRef, value: HistoryWork) => void, readonly chain = false) {}
  private key(kind: ResourceKind, ref: DetailRef, path?: string, cursor?: number) { return JSON.stringify([kind, ref.turnId, ref.id, ref.version, path, cursor]); }
  invalidate(kind: ResourceKind, ref: DetailRef, path?: string) {
    const key = this.key(kind, ref, path);
    this.bytes -= this.cache.get(key)?.bytes ?? 0;
    this.cache.delete(key);
  }
  remember(kind: ResourceKind, ref: DetailRef, value: unknown, path?: string, cursor?: number) {
    const key = this.key(kind, ref, path, cursor);
    const bytes = JSON.stringify(value).length * 2;
    if (bytes > 8 * 1024 * 1024) return;
    this.bytes -= this.cache.get(key)?.bytes ?? 0;
    this.cache.set(key, { value, bytes }); this.bytes += bytes;
    while (this.bytes > 8 * 1024 * 1024 || this.cache.size > 100) {
      const first = this.cache.keys().next().value!;
      this.bytes -= this.cache.get(first)!.bytes; this.cache.delete(first);
    }
  }
  load<T>(kind: ResourceKind, ref: DetailRef, path?: string, cursor?: number): Promise<T> {
    const key = this.key(kind, ref, path, cursor);
    const hit = this.cache.get(key);
    if (hit) { this.cache.delete(key); this.cache.set(key, hit); return Promise.resolve(hit.value as T); }
    const existing = this.pending.get(key);
    if (existing) return existing;
    const request = (kind === 'work' ? this.api.historyWork(this.threadId, ref, cursor, this.chain) : this.api.historyDetail(this.threadId, ref, cursor, kind === 'file' ? path : undefined))
      .then(value => {
        this.remember(kind, ref, value, path, cursor);
        if (kind === 'work') this.onWork?.(ref, value as HistoryWork);
        return value;
      }).finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    return request as Promise<T>;
  }
}

/** Merge the lazily fetched work skeleton with complete events already received live. */
export function hydrateWork(history: StoredEvent[], live: StoredEvent[], snapshotSeq: number): StoredEvent[] {
  const versions = new Map(history.filter(e => e.type === 'history.tool.start').map(e => [String(e.data.id), Number(e.data.detail?.version ?? 0)]));
  const received = new Map<string, number>();
  for (const e of live) if (e.type.startsWith('tool.')) received.set(String(e.data.id), Math.max(e.seq, received.get(String(e.data.id)) ?? 0));
  const thoughts = history.filter(e => e.type === 'history.thought');
  const localSeqs = new Set(live.filter(e => e.type === 'reasoning.delta').map(e => e.seq));
  const events = new Map(history.map(e => [e.seq, e]));
  for (const event of live) {
    // The work snapshot already groups old text and excludes any closing remark promoted to
    // the final answer. Only append text produced after that snapshot.
    if (event.type === 'message.delta' && event.seq <= snapshotSeq) continue;
    // A reconnect may have missed a newer tool update. Do not replace its metadata with stale
    // local output; only a fully received version can satisfy a subsequent expansion locally.
    if (event.type.startsWith('tool.') && (received.get(String(event.data.id)) ?? 0) < (versions.get(String(event.data.id)) ?? 0)) continue;
    if (event.type === 'reasoning.delta') {
      const thought = thoughts.find(t => event.seq >= t.seq && event.seq <= Number(t.data.detail?.version));
      if (thought && (!localSeqs.has(thought.seq) || !localSeqs.has(Number(thought.data.detail?.version)))) continue;
    }
    events.set(event.seq, event);
  }
  return [...events.values()].sort((a, b) => a.seq - b.seq);
}
