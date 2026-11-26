import type { HistoryPage } from './history';
import type { StoredEvent } from './timeline';

/**
 * Cold-start conversation cache persisted in localStorage:
 * - The assistant's current router thread id per machine (so opening Assistant never waits on
 *   `/api/personal/router` or flashes the empty welcome screen).
 * - The assistant's pinned first page (last 2 turns) plus an LRU of the 8 most recently opened
 *   normal threads, so F5 reloads render immediately while live history revalidates in parallel.
 */

const ROUTER_KEY = 'calli.router.v1';
const HISTORY_KEY = 'calli.history.v1';
const TOKEN_KEY = 'calli.token';

const MAX_HOSTS = 8;
const MAX_NORMAL_ENTRIES = 8;
const MAX_PINNED_ENTRIES = 4;
const MAX_PINNED_BYTES = 256 * 1024;
const MAX_NORMAL_BYTES = 160 * 1024;
const MAX_TOTAL_BYTES = 1500 * 1024;

interface StoredRouter {
  at: number;
  threadId: string;
}

interface StoredPageEntry {
  key: string;
  host: string;
  threadId: string;
  chain: boolean;
  pinned: boolean;
  at: number;
  bytes: number;
  page: HistoryPage;
}

const routerMemory = new Map<string, string | null>();
let historyMemory: Map<string, StoredPageEntry> | null = null;
let lastStamp = 0;
const nextStamp = () => (lastStamp = Math.max(Date.now(), lastStamp + 1));

const hostKey = (hostId: string | null | undefined) => hostId ?? '';
const entryKey = (hostId: string | null | undefined, threadId: string, chain: boolean) =>
  `${hostKey(hostId)}\u0000${threadId}\u0000${chain ? 1 : 0}`;

function safeToken(): string {
  try {
    return typeof localStorage !== 'undefined' ? (localStorage.getItem(TOKEN_KEY) ?? '') : '';
  } catch {
    return '';
  }
}

function containsToken(raw: string): boolean {
  const token = safeToken();
  if (!token || token.length < 8) return false;
  return raw.includes(token) || raw.includes(encodeURIComponent(token));
}

function readJson(key: string): unknown {
  try {
    if (typeof localStorage === 'undefined') return null;
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function isValidEvent(value: unknown): value is StoredEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const ev = value as Record<string, unknown>;
  return (
    typeof ev.threadId === 'string' &&
    typeof ev.type === 'string' &&
    typeof ev.seq === 'number' &&
    Number.isFinite(ev.seq) &&
    typeof ev.ts === 'string' &&
    (ev.turnId === null || typeof ev.turnId === 'string') &&
    ev.data !== null &&
    typeof ev.data === 'object'
  );
}

function isValidPage(value: unknown): value is HistoryPage {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const page = value as Partial<HistoryPage>;
  if (
    typeof page.snapshotSeq !== 'number' ||
    !Number.isFinite(page.snapshotSeq) ||
    typeof page.hasOlder !== 'boolean' ||
    !(page.nextCursor === null || (typeof page.nextCursor === 'number' && Number.isFinite(page.nextCursor))) ||
    !Array.isArray(page.events) ||
    page.events.length === 0
  ) {
    return false;
  }
  return page.events.every(isValidEvent);
}

/** Strip bulky inline base64 payloads before writing to localStorage. */
function stripForStorage(key: string, value: unknown): unknown {
  if (typeof value === 'string' && value.length > 2048 && value.startsWith('data:image/')) {
    return undefined;
  }
  if (key === 'liveEvents') return undefined;
  return value;
}

function loadRouters(): Record<string, StoredRouter> {
  const parsed = readJson(ROUTER_KEY);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
  const out: Record<string, StoredRouter> = {};
  for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (!v || typeof v !== 'object' || Array.isArray(v)) continue;
    const row = v as Partial<StoredRouter>;
    if (typeof row.threadId === 'string' && row.threadId && typeof row.at === 'number') {
      out[k] = { threadId: row.threadId, at: row.at };
    }
  }
  return out;
}

/** The assistant's router thread id last known for this host, or null if never seen. */
export function cachedRouterThreadId(hostId: string | null | undefined): string | null {
  const key = hostKey(hostId);
  if (routerMemory.has(key)) return routerMemory.get(key) ?? null;
  const hit = loadRouters()[key]?.threadId ?? null;
  if (hit) routerMemory.set(key, hit);
  return hit;
}

export function rememberRouterThreadId(hostId: string | null | undefined, threadId: string | null): void {
  const key = hostKey(hostId);
  routerMemory.set(key, threadId);
  try {
    if (typeof localStorage === 'undefined') return;
    const all = loadRouters();
    if ((all[key]?.threadId ?? null) === threadId) return;
    if (threadId) {
      all[key] = { threadId, at: nextStamp() };
    } else {
      delete all[key];
    }
    const entries = Object.entries(all)
      .sort((a, b) => b[1].at - a[1].at)
      .slice(0, MAX_HOSTS);
    if (entries.length) localStorage.setItem(ROUTER_KEY, JSON.stringify(Object.fromEntries(entries)));
    else localStorage.removeItem(ROUTER_KEY);
  } catch {
    // Storage unavailable or full: in-memory copy still works for this tab.
  }
}

function loadHistoryMap(): Map<string, StoredPageEntry> {
  if (historyMemory) return historyMemory;
  const map = new Map<string, StoredPageEntry>();
  const parsed = readJson(HISTORY_KEY);
  if (Array.isArray(parsed)) {
    for (const item of parsed) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
      const entry = item as Partial<StoredPageEntry>;
      if (
        typeof entry.host !== 'string' ||
        typeof entry.threadId !== 'string' ||
        !entry.threadId ||
        typeof entry.chain !== 'boolean' ||
        typeof entry.at !== 'number' ||
        !isValidPage(entry.page)
      ) {
        continue;
      }
      const key = entryKey(entry.host, entry.threadId, entry.chain);
      const bytes = typeof entry.bytes === 'number' && entry.bytes > 0 ? entry.bytes : JSON.stringify(entry.page).length;
      map.set(key, {
        key,
        host: entry.host,
        threadId: entry.threadId,
        chain: entry.chain,
        pinned: Boolean(entry.pinned),
        at: entry.at,
        bytes,
        page: entry.page,
      });
    }
  }
  historyMemory = map;
  return map;
}

function trimEntries(map: Map<string, StoredPageEntry>): StoredPageEntry[] {
  const pinned = [...map.values()].filter((e) => e.pinned).sort((a, b) => b.at - a.at).slice(0, MAX_PINNED_ENTRIES);
  const normal = [...map.values()].filter((e) => !e.pinned).sort((a, b) => b.at - a.at).slice(0, MAX_NORMAL_ENTRIES);
  const kept: StoredPageEntry[] = [...pinned, ...normal];
  let total = kept.reduce((sum, e) => sum + e.bytes, 0);
  while (total > MAX_TOTAL_BYTES && kept.length > 1) {
    // Drop oldest normal entry first; only evict oldest pinned if no normal entries remain.
    const dropIndex = (() => {
      for (let i = kept.length - 1; i >= 0; i--) if (!kept[i].pinned) return i;
      return kept.length - 1;
    })();
    total -= kept[dropIndex].bytes;
    kept.splice(dropIndex, 1);
  }
  map.clear();
  for (const entry of kept) map.set(entry.key, entry);
  return kept;
}

function flushHistoryMap(map: Map<string, StoredPageEntry>): void {
  const entries = trimEntries(map);
  try {
    if (typeof localStorage === 'undefined') return;
    if (!entries.length) {
      localStorage.removeItem(HISTORY_KEY);
      return;
    }
    const list = [...entries];
    while (list.length) {
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
        return;
      } catch {
        // Quota exceeded: drop oldest non-pinned entry and retry.
        const idx = (() => {
          for (let i = list.length - 1; i >= 0; i--) if (!list[i].pinned) return i;
          return list.length - 1;
        })();
        const [removed] = list.splice(idx, 1);
        if (removed) map.delete(removed.key);
      }
    }
    localStorage.removeItem(HISTORY_KEY);
  } catch {
    // Storage disabled: memory mirror still works.
  }
}

/** Synchronously read the last persisted first page for a thread, or null if absent. */
export function cachedHistoryPage(hostId: string | null | undefined, threadId: string, chain = false): HistoryPage | null {
  if (!threadId) return null;
  const map = loadHistoryMap();
  const hit = map.get(entryKey(hostId, threadId, chain));
  if (!hit) return null;
  hit.at = nextStamp();
  return hit.page;
}

/** Persist a non-empty first page for instant cold render. Pinned entries (assistant) get priority. */
export function rememberHistoryPage(
  hostId: string | null | undefined,
  threadId: string,
  page: HistoryPage,
  opts: { chain?: boolean; pinned?: boolean } = {},
): void {
  if (!threadId) return;
  const chain = Boolean(opts.chain);
  const map = loadHistoryMap();
  const key = entryKey(hostId, threadId, chain);
  if (!page.events.length) {
    if (map.delete(key)) flushHistoryMap(map);
    return;
  }
  const existing = map.get(key);
  const pinned = Boolean(opts.pinned || existing?.pinned || cachedRouterThreadId(hostId) === threadId);
  const limit = pinned ? MAX_PINNED_BYTES : MAX_NORMAL_BYTES;
  let serialized: string;
  try {
    serialized = JSON.stringify(page, stripForStorage);
  } catch {
    return;
  }
  if (!serialized || serialized.length > limit || containsToken(serialized)) {
    if (map.delete(key)) flushHistoryMap(map);
    return;
  }
  let sanitized: HistoryPage;
  try {
    sanitized = JSON.parse(serialized) as HistoryPage;
  } catch {
    return;
  }
  if (!isValidPage(sanitized)) return;
  map.set(key, {
    key,
    host: hostKey(hostId),
    threadId,
    chain,
    pinned,
    at: nextStamp(),
    bytes: serialized.length,
    page: sanitized,
  });
  flushHistoryMap(map);
}

/** Drop every persisted variant (chain/non-chain) of a thread, e.g. after edit/delete/rewrite. */
export function forgetStoredHistoryPage(hostId: string | null | undefined, threadId: string): void {
  const host = hostKey(hostId);
  const map = loadHistoryMap();
  let changed = false;
  for (const [key, entry] of map) {
    if (entry.host === host && entry.threadId === threadId) {
      map.delete(key);
      changed = true;
    }
  }
  if (changed) flushHistoryMap(map);
}

/** Signed out or switched hub: drop every cached router id and persisted conversation page. */
export function clearHistoryCache(): void {
  routerMemory.clear();
  historyMemory = new Map();
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(ROUTER_KEY);
      localStorage.removeItem(HISTORY_KEY);
    }
  } catch {
    // Storage may be disabled.
  }
}
