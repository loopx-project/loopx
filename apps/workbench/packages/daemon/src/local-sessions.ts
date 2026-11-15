import { stat } from 'node:fs/promises';
import type { AgentAdapter, AgentSessionSummary } from './adapters/types.js';
import { isWithinWorkdirs } from './workdirs.js';

/** A conversation an agent on this machine already has, that is not a thread here yet. */
export interface ExternalSession {
  /** The instance id, as threads store it. */
  agent: string;
  sessionId: string;
  cwd: string;
  title: string;
  /** ISO time of the last activity, when the agent says. */
  updatedAt: string | null;
}

export interface LocalSessionPage {
  sessions: ExternalSession[];
  /** Read `?cursor=` with this for the next page; null at the end. */
  nextCursor: string | null;
  /** When the last scan finished; null before the first one. */
  scannedAt: string | null;
}

export interface LocalSessionsDeps {
  adapters: Map<string, AgentAdapter>;
  workdirs: () => string[];
  /** Every thread's agent session, sessionId → instance id, read once per page call: a session
   *  that is a thread anywhere is not external. */
  importedSessions: () => Map<string, string>;
  /** The instances live: hidden ones are left out, and `store` names the session store each one's
   *  sessions live in (same adapter, same account env): one store is asked once, so a session
   *  never appears under two instances. */
  instances: () => Array<{ id: string; hidden: boolean; store: string }>;
  /** Overridable for tests. */
  now?: () => number;
}

/**
 * Claude Code leaves a one-line session behind for every `claude -p /usage` the hub runs to read
 * plan quota (until the probe asked it not to). Nobody wants to continue one, so they are not listed.
 */
export function isProbeSession(title: string): boolean {
  return title.trim() === '/usage';
}

/** A request kicks a scan when the last one is older than this. Asking an agent costs a process,
 *  so a page that stays open rescans every few minutes, not every poll. */
const FRESH_MS = 5 * 60_000;
/** Stores asked at once: each is a process for the ACP agents, and two is plenty for a list. */
const SCAN_CONCURRENCY = 2;
/** Hard cap per agent, so one enormous config directory cannot pin the scan or the memory. */
const MAX_PER_AGENT = 2000;

/**
 * Lists the conversations every agent on this machine already has, in one scan the page reads
 * from — asking an agent costs a process, so a scan runs once after startup and then only when a
 * request finds the list stale. Only the outline is kept (agent, session id, directory, title,
 * last activity); the past turns are read by the existing import only when one is opened.
 * Sessions outside the allowed directories, in removed directories, or already a thread here are
 * left out. Together with machine-usage.ts this is the one subsystem that reads what the agents
 * on this machine have been doing; `CALLI_LOCAL_SCAN=off` stands both down.
 */
export class LocalSessions {
  private readonly cache = new Map<string, { at: number; sessions: ExternalSession[] }>();
  private readonly deps: LocalSessionsDeps;
  private readonly enabled: boolean;
  private scanning: Promise<void> | null = null;

  constructor(deps: LocalSessionsDeps, opts: { enabled?: boolean } = {}) {
    this.deps = deps;
    this.enabled = opts.enabled !== false;
  }

  /** The one scan after startup; there is no timer — requests keep the list fresh. */
  start(): void {
    if (!this.enabled) return;
    void this.scan().catch(() => undefined); // one agent failing is not the hub's failure
  }

  /** A visible page asks for this: fresh enough does nothing, stale kicks one scan (async). */
  ensureFresh(): void {
    if (!this.enabled) return;
    const now = this.deps.now?.() ?? Date.now();
    let newest = 0;
    for (const { at } of this.cache.values()) newest = Math.max(newest, at);
    if (!this.cache.size || now - newest > FRESH_MS) void this.scan().catch(() => undefined);
  }

  /** One pass over every session store. Concurrent calls share the one pass. */
  scan(): Promise<void> {
    this.scanning ??= this.doScan().finally(() => { this.scanning = null; });
    return this.scanning;
  }

  private async doScan(): Promise<void> {
    if (!this.enabled) return;
    const now = this.deps.now?.() ?? Date.now();
    const workdirs = this.deps.workdirs();
    const allowed = new Map<string, boolean>();
    const ok = async (cwd: string): Promise<boolean> => {
      let within = allowed.get(cwd);
      if (within === undefined) {
        // A session whose directory has since been removed cannot be continued, so it is not listed.
        within = !!cwd && isWithinWorkdirs(cwd, workdirs) && (await isDirectory(cwd));
        allowed.set(cwd, within);
      }
      return within;
    };

    // One representative per store: instances of one adapter that share the store's env list the
    // same sessions, and hidden instances are nobody's representative.
    const byStore = new Map<string, { id: string; list: () => Promise<AgentSessionSummary[]> }>();
    for (const instance of this.deps.instances()) {
      if (instance.hidden) continue;
      const adapter = this.deps.adapters.get(instance.id);
      if (!adapter?.capabilities.history || !adapter.listSessions) continue;
      if (!byStore.has(instance.store)) byStore.set(instance.store, { id: instance.id, list: adapter.listSessions.bind(adapter) });
    }
    const queue = [...byStore.values()];
    const asked = new Set(queue.map((entry) => entry.id));
    // Entries from stores nobody represents any more would list sessions forever.
    for (const id of [...this.cache.keys()]) if (!asked.has(id)) this.cache.delete(id);

    const worker = async (): Promise<void> => {
      for (;;) {
        const next = queue.shift();
        if (!next) return;
        const { id, list } = next;
        let sessions: ExternalSession[];
        try {
          sessions = [];
          for (const s of await list()) {
            if (!isProbeSession(s.title) && (await ok(s.cwd))) sessions.push({ agent: id, sessionId: s.id, cwd: s.cwd, title: s.title, updatedAt: s.updatedAt });
          }
          sessions.sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
          sessions.length = Math.min(sessions.length, MAX_PER_AGENT);
        } catch {
          continue; // this agent cannot answer right now; its last list stands
        }
        this.cache.set(id, { at: now, sessions });
      }
    };
    await Promise.all(Array.from({ length: Math.min(SCAN_CONCURRENCY, queue.length) }, () => worker()));
  }

  /**
   * A page of sessions that are not threads here, newest first. `cursor` is the previous page's
   * `nextCursor`: `<updatedAt>|<agent>/<sessionId>` of its last row (dated rows first; ones
   * without a date come last, with an empty date part). Sessions sharing a timestamp are told
   * apart by their id, so a page boundary never repeats or skips one.
   */
  page(limitRaw: unknown, cursorRaw: unknown): LocalSessionPage {
    const limit = Number.isFinite(Number(limitRaw)) ? Math.min(Math.max(Number(limitRaw), 1), 500) : 100;
    const cursor = typeof cursorRaw === 'string' && cursorRaw ? cursorRaw : null;
    const owners = this.deps.importedSessions();
    const all: ExternalSession[] = [];
    let scannedAt: string | null = null;
    for (const { at, sessions } of this.cache.values()) {
      if (scannedAt === null || at > Date.parse(scannedAt)) scannedAt = new Date(at).toISOString();
      for (const session of sessions) if (!owners.has(session.sessionId)) all.push(session);
    }
    const key = (s: ExternalSession) => `${s.updatedAt ?? ''}|${s.agent}/${s.sessionId}`;
    all.sort((a, b) => key(b).localeCompare(key(a)));
    let start = 0;
    if (cursor !== null) {
      start = all.findIndex((s) => key(s) === cursor);
      if (start < 0) return { sessions: [], nextCursor: null, scannedAt };
      start += 1;
    }
    const rows = all.slice(start, start + limit);
    const nextCursor = start + limit < all.length && rows.length ? key(rows.at(-1)!) : null;
    return { sessions: rows, nextCursor, scannedAt };
  }
}

async function isDirectory(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}
