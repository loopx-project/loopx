import { Api, AuthError } from './api';
import type { DetailRef, StoredEvent, Item } from './timeline';

export interface HistoryPage { turnIds?: string[]; events: StoredEvent[]; snapshotSeq: number; nextCursor: number | null; hasOlder: boolean; hasNewer?: boolean }
export interface HistoryDetail { version: number; text?: string; input?: unknown; output?: string; content?: unknown; nextCursor: number | null; toolId?: string | null; outputTruncated?: boolean; outputOmitted?: number; fullAvailable?: boolean }
export type WorkFiles = NonNullable<Extract<Item, { kind: 'work' }>['files']>;
export interface HistoryWork { files?: WorkFiles; version: number; events: StoredEvent[]; nextCursor: number | null }
export type ResourceKind = 'work' | 'detail' | 'file';

const PAGE_CACHE_LIMIT = 16;
const PAGE_CACHE_MAX_BYTES = 8 * 1024 * 1024;
const PAGE_CACHE_ENTRY_MAX_BYTES = 2 * 1024 * 1024;
const PAGE_CACHE_TTL_MS = 10 * 60 * 1000;
interface CachedPage { page: HistoryPage; bytes: number; at: number }
interface PageCache { pages: Map<string, CachedPage>; bytes: number }
const pageCaches = new WeakMap<Api, PageCache>();

const pageCacheKey = (id: string, before?: number, after?: number, chain = false) =>
  `${id}\u0000${chain ? 1 : 0}\u0000${before ?? ''}\u0000${after ?? ''}`;

function pageCache(api: Api): PageCache {
  let cache = pageCaches.get(api);
  if (!cache) {
    cache = { pages: new Map(), bytes: 0 };
    pageCaches.set(api, cache);
  }
  return cache;
}

function pageBytes(page: HistoryPage): number {
  return JSON.stringify(page).length * 2;
}

function trimPageCache(cache: PageCache) {
  while (cache.pages.size > PAGE_CACHE_LIMIT || cache.bytes > PAGE_CACHE_MAX_BYTES) {
    const key = cache.pages.keys().next().value;
    if (key === undefined) break;
    cache.bytes -= cache.pages.get(key)?.bytes ?? 0;
    cache.pages.delete(key);
  }
}

function readCachedPage(api: Api, id: string, before?: number, after?: number, chain = false): HistoryPage | undefined {
  const cache = pageCaches.get(api);
  const key = pageCacheKey(id, before, after, chain);
  const hit = cache?.pages.get(key);
  if (!cache || !hit) return undefined;
  if (Date.now() - hit.at > PAGE_CACHE_TTL_MS) {
    cache.bytes -= hit.bytes;
    cache.pages.delete(key);
    return undefined;
  }
  cache.pages.delete(key);
  cache.pages.set(key, { ...hit, at: Date.now() });
  return hit.page;
}

function rememberPage(api: Api, id: string, page: HistoryPage, before?: number, after?: number, chain = false) {
  const bytes = pageBytes(page);
  if (bytes > PAGE_CACHE_ENTRY_MAX_BYTES) return;
  const cache = pageCache(api);
  const key = pageCacheKey(id, before, after, chain);
  cache.bytes -= cache.pages.get(key)?.bytes ?? 0;
  cache.pages.set(key, { page, bytes, at: Date.now() });
  cache.bytes += bytes;
  trimPageCache(cache);
}

function forgetThreadPages(api: Api, id: string) {
  const cache = pageCaches.get(api);
  if (!cache) return;
  for (const [key, entry] of cache.pages) {
    if (!key.startsWith(`${id}\u0000`)) continue;
    cache.bytes -= entry.bytes;
    cache.pages.delete(key);
  }
}

function updateCachedLivePage(api: Api, id: string, batch: StoredEvent[], after?: number, chain = false) {
  if (!batch.length) return;
  if (batch.some(ev => ev.type === 'thread.historyChanged')) {
    forgetThreadPages(api, id);
    return;
  }
  const cache = pageCaches.get(api);
  const key = pageCacheKey(id, undefined, after, chain);
  const hit = cache?.pages.get(key);
  if (!cache || !hit) return;
  const page = {
    ...hit.page,
    events: mergeHistory(hit.page.events, batch),
    snapshotSeq: batch.reduce((latest, ev) => Math.max(latest, ev.seq), hit.page.snapshotSeq),
  };
  // Live updates arrive in short batches. Estimate growth from the batch so streaming stays cheap;
  // full sizing is still done when a page is first fetched.
  const bytes = hit.bytes + JSON.stringify(batch).length * 2;
  cache.bytes -= hit.bytes;
  if (bytes > PAGE_CACHE_ENTRY_MAX_BYTES) {
    cache.pages.delete(key);
    return;
  }
  cache.pages.delete(key);
  cache.pages.set(key, { page, bytes, at: Date.now() });
  cache.bytes += bytes;
  trimPageCache(cache);
}

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

/**
 * Puts an earlier page in front of what is loaded. A page lists each turn's answer after its work,
 * so seqs are not in order; merging it event by event as if they were dropped every answer.
 */
export function prependHistory(older: StoredEvent[], loaded: StoredEvent[]): StoredEvent[] {
  const seen = new Set(loaded.map(event => event.seq));
  return [...older.filter(event => !seen.has(event.seq)), ...loaded];
}

export async function historyPage(api: Api, id: string, before?: number, after?: number, chain = false): Promise<HistoryPage> {
  const cached = readCachedPage(api, id, before, after, chain);
  if (cached) return cached;
  if (api.demo) {
    const events = before ? await api.eventsBefore(id, before) : await api.eventsTail(id);
    const nextCursor = (events[0]?.seq ?? 0) > 1 ? events[0].seq : null;
    const page = { events, nextCursor, hasOlder: nextCursor !== null, snapshotSeq: events.at(-1)?.seq ?? 0 };
    rememberPage(api, id, page, before, after, chain);
    return page;
  }
  const page = await api.historyPage(id, before, after, chain);
  rememberPage(api, id, page, before, after, chain);
  return page;
}

/** A decision response may arrive without its SSE event. Read a fresh, bounded summary, not raw history. */
export async function refreshHistoryPage(api: Api, id: string, chain = false): Promise<HistoryPage> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);
  try {
    const page = await api.historyPage(id, undefined, undefined, chain, controller.signal);
    forgetThreadPages(api, id);
    rememberPage(api, id, page, undefined, undefined, chain);
    return page;
  } finally {
    clearTimeout(timer);
  }
}

/** Refresh only the summary's covered turns; preserve older pages and events newer than the read. */
export function mergeHistoryPage(previous: StoredEvent[], page: HistoryPage): StoredEvent[] {
  const threadId = page.events[0]?.threadId;
  if (!threadId) return previous;
  return mergeHistory(previous, [{
    threadId, turnId: null, type: 'history.reset', ts: '', seq: page.snapshotSeq,
    data: { turns: [...new Set(page.events.map(event => event.turnId).filter(Boolean))], events: page.events },
  }]);
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
    if (!signal.aborted && batch.length) {
      updateCachedLivePage(api, id, batch, callbacks.minimumSeq, callbacks.chain);
      callbacks.events(batch);
    }
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
