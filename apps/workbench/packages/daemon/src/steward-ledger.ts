import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import { basename, join } from 'node:path';
import type { AgentAdapter } from './adapters/types.js';
import { HttpError } from './errors.js';
import { GramExclusionIndex, sha256Text } from './gram-exclusion-index.js';
import type { ExternalSession, LocalSessions } from './local-sessions.js';
import type { Store } from './store.js';
import type { ThreadManager, ThreadView } from './threads.js';

export type StewardLedgerPhase = 'running' | 'needs_user' | 'blocked' | 'idle_open' | 'done_recent';

export interface StewardSourceHealthEntry {
  status: 'ok' | 'stale' | 'unavailable';
  reason?: string;
  scannedAt?: string | null;
  itemCount?: number;
}

export interface StewardLedgerItem {
  id: string;
  kind: 'workbench_thread' | 'local_session';
  phase: StewardLedgerPhase;
  title: string;
  agent: string;
  cwd: string;
  updatedAt: string | null;
  goalId: string | null;
  goalTitle: string | null;
  summary: string | null;
  chainIds: string[];
  jumpTarget:
    | { kind: 'thread'; threadId: string }
    | { kind: 'local_session'; agent: string; sessionId: string; cwd: string };
}

export interface StewardLedgerSnapshot {
  revision: string;
  generatedAt: string;
  ledgerCapture: boolean;
  cacheHit?: boolean;
  sourceHealth: {
    workbenchThreads: StewardSourceHealthEntry;
    localSessions: StewardSourceHealthEntry;
  };
  coverage: {
    workbenchThreads: 'complete' | 'unavailable';
    localSessions: 'complete' | 'unavailable';
  };
  counts: Record<StewardLedgerPhase, number>;
  items: StewardLedgerItem[];
  nextCursor: string | null;
}

export interface StewardSessionFindHit {
  id: string;
  kind: 'workbench_thread' | 'local_session';
  title: string;
  agent: string;
  cwd: string;
  updatedAt: string | null;
  phase: StewardLedgerPhase;
  goalId: string | null;
  snippet: string;
  jumpTarget:
    | { kind: 'thread'; threadId: string }
    | { kind: 'local_session'; agent: string; sessionId: string; cwd: string };
}

export interface StewardSessionsFindResult {
  query: string;
  revision: string;
  indexStatus: 'ready' | 'rebuilding';
  sourceHealth: StewardLedgerSnapshot['sourceHealth'];
  hits: StewardSessionFindHit[];
}

export interface StewardSessionReadChunk {
  index: number;
  role: 'user' | 'assistant' | 'tool' | 'summary';
  ts: string | null;
  text: string;
}

export interface StewardSessionReadResult {
  id: string;
  kind: 'workbench_thread' | 'local_session';
  title: string;
  agent: string;
  cwd: string;
  updatedAt: string | null;
  imported: boolean;
  mode: 'outline' | 'recent_turns' | 'turn_range';
  chunks: StewardSessionReadChunk[];
  nextCursor: string | null;
}

export interface StewardLedgerDeps {
  store: Store;
  manager: ThreadManager;
  localSessions?: LocalSessions;
  adapters?: Map<string, AgentAdapter>;
  dataDir: string;
}

const PHASE_PRIORITY: Record<StewardLedgerPhase, number> = {
  needs_user: 0,
  running: 1,
  blocked: 2,
  idle_open: 3,
  done_recent: 4,
};

const DONE_RECENT_WINDOW_MS = 24 * 60 * 60 * 1000;

function deriveThreadPhase(
  thread: ThreadView,
  doneCutoffIso: string,
): { phase: StewardLedgerPhase | 'done_old'; summary: string | null } {
  if (thread.waiting) {
    const kind = thread.waitingReason ?? 'question';
    return {
      phase: 'needs_user',
      summary:
        kind === 'approval'
          ? '等待确认权限请求'
          : kind === 'plan'
            ? '等待确认执行计划'
            : '等待回答会话提问',
    };
  }
  if (thread.report?.stage === 'blocked') {
    return {
      phase: 'blocked',
      summary: thread.report.note || '需要你确认下一步',
    };
  }
  if (thread.status === 'error') {
    return {
      phase: 'blocked',
      summary: thread.failure?.message || '上一轮运行出错，等待处理',
    };
  }
  if (thread.status === 'running') {
    return {
      phase: 'running',
      summary: thread.report?.note || thread.coordination?.doing || thread.preview || null,
    };
  }
  if (thread.report?.stage === 'review') {
    return {
      phase: 'needs_user',
      summary: thread.report.note || '待你验收结果',
    };
  }
  const doneTs = thread.doneAt ?? thread.archivedAt;
  if (doneTs) {
    if (doneTs >= doneCutoffIso || thread.updatedAt >= doneCutoffIso) {
      return { phase: 'done_recent', summary: thread.report?.note || thread.preview || null };
    }
    return { phase: 'done_old', summary: null };
  }
  return {
    phase: 'idle_open',
    summary: thread.report?.note || thread.preview || null,
  };
}

interface SearchableThreadDoc {
  thread: ThreadView;
  priors: ThreadView[];
  docText: string;
  contentSha256: string;
}

export class StewardLedgerService {
  readonly gramIndex: GramExclusionIndex;
  readonly credentialPath: string;
  readonly credentialFile: string;
  readonly socketPath: string;
  private readonly supervisorToken: string;
  private cachedLedger: {
    revision: string;
    items: StewardLedgerItem[];
    counts: Record<StewardLedgerPhase, number>;
    sourceHealth: StewardLedgerSnapshot['sourceHealth'];
    coverage: StewardLedgerSnapshot['coverage'];
  } | null = null;
  private cachedThreadDocs = new Map<
    string,
    { updatedAt: string; title: string; priorKey: string; eventCount: number; text: string; sha256: string }
  >();
  private cachedExternalDocs = new Map<
    string,
    { updatedAt: string | null; title: string; text: string; sha256: string; chunks: StewardSessionReadChunk[] }
  >();

  constructor(private readonly deps: StewardLedgerDeps) {
    this.gramIndex = new GramExclusionIndex(deps.dataDir);
    const stewardDir = join(deps.dataDir, 'steward');
    mkdirSync(stewardDir, { recursive: true, mode: 0o700 });
    this.socketPath = join(stewardDir, 'calli.sock');
    this.credentialPath = join(stewardDir, 'credential.json');
    this.credentialFile = this.credentialPath;
    this.supervisorToken = randomBytes(32).toString('hex');
    this.writeCredentialFile();
  }

  private writeCredentialFile(): void {
    const payload = JSON.stringify({
      schemaVersion: 'workbench_steward_credential_v0',
      socketPath: this.socketPath,
      token: this.supervisorToken,
      pid: process.pid,
      createdAt: new Date().toISOString(),
    });
    const tmp = `${this.credentialPath}.tmp-${process.pid}`;
    writeFileSync(tmp, payload, { mode: 0o600 });
    chmodSync(tmp, 0o600);
    renameSync(tmp, this.credentialPath);
  }

  /** Constant-time verification of supervisor socket bearer credential. Workers receive 403. */
  verifySupervisorToken(bearerHeader: string | undefined): void {
    const raw = (bearerHeader ?? '').replace(/^Bearer\s+/i, '').trim();
    const expected = Buffer.from(this.supervisorToken, 'utf8');
    const received = Buffer.from(raw, 'utf8');
    if (expected.length !== received.length || !timingSafeEqual(expected, received)) {
      throw new HttpError(403, 'unauthorized_caller_role');
    }
  }

  dispose(): void {
    rmSync(this.socketPath, { force: true });
    rmSync(this.credentialPath, { force: true });
  }

  private isInternalThread(thread: ThreadView): boolean {
    const routerId = this.deps.manager.personal.router.status().threadId;
    if (routerId && thread.id === routerId) return true;
    if (this.deps.store.isRouterThread(thread.id) || this.deps.store.isReviewThread(thread.id)) return true;
    if (this.deps.store.summaryThreadIds().has(thread.id)) return true;
    return false;
  }

  /** Fold handover chains so superseded segments attach under the active head thread. */
  private foldHandoverChains(threads: ThreadView[]): Array<{ active: ThreadView; priors: ThreadView[] }> {
    const byId = new Map(threads.map((t) => [t.id, t]));
    const supersededBy = new Map<string, string>();
    for (const t of threads) {
      const link = t.handover as (NonNullable<ThreadView['handover']> & { from?: string }) | null | undefined;
      const srcId = link?.sourceId ?? link?.from;
      if (srcId && byId.has(srcId) && srcId !== t.id) {
        const src = byId.get(srcId)!;
        if (!link?.sourceMoved && src.status !== 'running') {
          supersededBy.set(srcId, t.id);
        }
      }
    }

    const rootTarget = (id: string): string => {
      const seen = new Set<string>([id]);
      let cur = id;
      while (supersededBy.has(cur)) {
        const next = supersededBy.get(cur)!;
        if (seen.has(next)) break;
        seen.add(next);
        cur = next;
      }
      return cur;
    };

    const priorsMap = new Map<string, ThreadView[]>();
    const actives: ThreadView[] = [];
    for (const t of threads) {
      if (supersededBy.has(t.id)) {
        const target = rootTarget(t.id);
        const list = priorsMap.get(target) ?? [];
        list.push(t);
        priorsMap.set(target, list);
      } else {
        actives.push(t);
      }
    }
    for (const list of priorsMap.values()) {
      list.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    }
    return actives.map((active) => ({ active, priors: priorsMap.get(active.id) ?? [] }));
  }

  private computeLocalSessionsHealth(ledgerCapture: boolean): {
    health: StewardSourceHealthEntry;
    coverage: StewardLedgerSnapshot['coverage']['localSessions'];
    externalSessions: ExternalSession[];
  } {
    if (!ledgerCapture) {
      return {
        health: { status: 'unavailable', reason: 'ledger_capture_disabled', scannedAt: null, itemCount: 0 },
        coverage: 'unavailable',
        externalSessions: [],
      };
    }
    const ls = this.deps.localSessions;
    if (!ls || !ls.isEnabled()) {
      return {
        health: { status: 'unavailable', reason: 'local_scan_disabled', scannedAt: null, itemCount: 0 },
        coverage: 'unavailable',
        externalSessions: [],
      };
    }
    ls.ensureFresh();
    const scannedAt = ls.lastScannedAt();
    if (scannedAt === null) {
      return {
        health: { status: 'stale', reason: 'initial_scan_pending', scannedAt: null, itemCount: 0 },
        coverage: 'unavailable',
        externalSessions: [],
      };
    }
    const importedOwners = this.deps.store.agentSessionOwners();
    const listAll = this.deps.manager.generalSettings().autoImportLocal;
    const external = ls.all().filter((s) => !importedOwners.has(s.sessionId) && (listAll || Boolean(s.loopx)));
    return {
      health: { status: 'ok', scannedAt, itemCount: external.length },
      coverage: 'complete',
      externalSessions: external,
    };
  }

  private buildRevisionKey(
    ledgerCapture: boolean,
    threads: ThreadView[],
    externalSessions: ExternalSession[],
    lsHealth: StewardSourceHealthEntry,
    doneBucket: number,
  ): string {
    const h = createHash('sha256');
    h.update(`capture:${ledgerCapture}|bucket:${doneBucket}|ls:${lsHealth.status}:${lsHealth.reason ?? ''}:${lsHealth.scannedAt ?? ''}\n`);
    for (const t of threads) {
      h.update(
        `t:${t.id}:${t.updatedAt}:${t.status}:${t.waiting}:${t.waitingReason ?? ''}:${t.doneAt ?? ''}:${t.archivedAt ?? ''}:${t.report?.stage ?? ''}:${t.report?.at ?? ''}:${t.loopx?.goalId ?? t.loopxInfo?.goalId ?? ''}\n`,
      );
    }
    for (const s of externalSessions) {
      h.update(`e:${s.agent}:${s.sessionId}:${s.updatedAt ?? ''}:${s.title}:${s.loopx?.goalId ?? ''}\n`);
    }
    return `sha256:${h.digest('hex')}`;
  }

  ledgerRead(opts: { cursor?: string | null; limit?: number; includeDoneSince?: string | null } = {}): StewardLedgerSnapshot {
    const limit = Number.isFinite(opts.limit) ? Math.min(Math.max(Math.trunc(opts.limit!), 1), 100) : 50;
    const cursor = typeof opts.cursor === 'string' && opts.cursor ? opts.cursor : null;
    const includeDoneSince = typeof opts.includeDoneSince === 'string' && opts.includeDoneSince ? opts.includeDoneSince : null;
    const ledgerCapture = this.deps.manager.generalSettings().ledgerCapture !== false;
    const nowMs = Date.now();
    const nowIso = new Date(nowMs).toISOString();

    if (!ledgerCapture) {
      this.gramIndex.clear();
      this.cachedLedger = null;
      const revision = `sha256:${sha256Text('ledger_capture_disabled')}`;
      return {
        revision,
        generatedAt: nowIso,
        ledgerCapture: false,
        cacheHit: false,
        sourceHealth: {
          workbenchThreads: { status: 'unavailable', reason: 'ledger_capture_disabled', itemCount: 0 },
          localSessions: { status: 'unavailable', reason: 'ledger_capture_disabled', itemCount: 0 },
        },
        coverage: { workbenchThreads: 'unavailable', localSessions: 'unavailable' },
        counts: { running: 0, needs_user: 0, blocked: 0, idle_open: 0, done_recent: 0 },
        items: [],
        nextCursor: null,
      };
    }

    const doneCutoffIso = includeDoneSince ?? new Date(nowMs - DONE_RECENT_WINDOW_MS).toISOString();
    // Bucket default 24h cutoff to 60s intervals so warm reads within the same minute hit the cache in <1ms.
    const doneBucket = includeDoneSince ? 0 : Math.floor(nowMs / 60_000);

    // Open work is NEVER dropped by age (>30d, >90d all stay visible until explicitly marked done/archived).
    const activeThreads = this.deps.manager.listThreads({ archived: false }).filter((t) => !this.isInternalThread(t));
    const recentArchived = this.deps.manager
      .listThreads({ archived: true })
      .filter((t) => !this.isInternalThread(t) && (t.archivedAt ?? t.updatedAt) >= doneCutoffIso);
    const allCandidateThreads = [...activeThreads, ...recentArchived];
    const { health: lsHealth, coverage: lsCoverage, externalSessions } = this.computeLocalSessionsHealth(true);
    const revision = this.buildRevisionKey(true, allCandidateThreads, externalSessions, lsHealth, doneBucket);

    let allItems: StewardLedgerItem[];
    let counts: Record<StewardLedgerPhase, number>;
    let sourceHealth: StewardLedgerSnapshot['sourceHealth'];
    let coverage: StewardLedgerSnapshot['coverage'];
    let cacheHit = false;

    if (!includeDoneSince && this.cachedLedger && this.cachedLedger.revision === revision) {
      allItems = this.cachedLedger.items;
      counts = this.cachedLedger.counts;
      sourceHealth = this.cachedLedger.sourceHealth;
      coverage = this.cachedLedger.coverage;
      cacheHit = true;
    } else {
      const folded = this.foldHandoverChains(allCandidateThreads);
      allItems = [];

      for (const { active, priors } of folded) {
        const derived = deriveThreadPhase(active, doneCutoffIso);
        if (derived.phase === 'done_old') continue;
        const goalId = active.loopx?.goalId ?? active.loopxInfo?.goalId ?? null;
        const goalTitle = active.loopx?.goalTitle ?? active.loopxInfo?.goalTitle ?? null;
        allItems.push({
          id: `thread:${active.id}`,
          kind: 'workbench_thread',
          phase: derived.phase,
          title: active.title,
          agent: active.agent,
          cwd: active.cwd || '',
          updatedAt: active.updatedAt,
          goalId,
          goalTitle,
          summary: derived.summary,
          chainIds: [...priors.map((p) => p.id), active.id],
          jumpTarget: { kind: 'thread', threadId: active.id },
        });
      }

      for (const ext of externalSessions) {
        const goalId = ext.loopx?.goalId ?? null;
        const goalTitle = ext.loopx?.goalTitle ?? null;
        allItems.push({
          id: `local:${ext.agent}:${ext.sessionId}`,
          kind: 'local_session',
          phase: 'idle_open',
          title: ext.title || `${ext.agent} ${ext.sessionId.slice(0, 8)}`,
          agent: ext.agent,
          cwd: ext.cwd || '',
          updatedAt: ext.updatedAt,
          goalId,
          goalTitle,
          summary: null,
          chainIds: [ext.sessionId],
          jumpTarget: {
            kind: 'local_session',
            agent: ext.agent,
            sessionId: ext.sessionId,
            cwd: ext.cwd || '',
          },
        });
      }

      allItems.sort((a, b) => {
        const pa = PHASE_PRIORITY[a.phase] ?? 99;
        const pb = PHASE_PRIORITY[b.phase] ?? 99;
        if (pa !== pb) return pa - pb;
        const ta = a.updatedAt ?? '';
        const tb = b.updatedAt ?? '';
        if (ta !== tb) return tb.localeCompare(ta);
        return a.id.localeCompare(b.id);
      });

      counts = {
        running: allItems.filter((i) => i.phase === 'running').length,
        needs_user: allItems.filter((i) => i.phase === 'needs_user').length,
        blocked: allItems.filter((i) => i.phase === 'blocked').length,
        idle_open: allItems.filter((i) => i.phase === 'idle_open').length,
        done_recent: allItems.filter((i) => i.phase === 'done_recent').length,
      };
      sourceHealth = {
        workbenchThreads: { status: 'ok', scannedAt: nowIso, itemCount: folded.length },
        localSessions: lsHealth,
      };
      coverage = {
        workbenchThreads: 'complete',
        localSessions: lsCoverage,
      };
      if (!includeDoneSince) {
        this.cachedLedger = { revision, items: allItems, counts, sourceHealth, coverage };
      }
    }

    let start = 0;
    if (cursor !== null) {
      const idx = allItems.findIndex((item) => item.id === cursor);
      if (idx < 0) {
        return {
          revision,
          generatedAt: nowIso,
          ledgerCapture: true,
          cacheHit,
          sourceHealth,
          coverage,
          counts,
          items: [],
          nextCursor: null,
        };
      }
      start = idx + 1;
    }

    const pageItems = allItems.slice(start, start + limit);
    const nextCursor =
      start + limit < allItems.length && pageItems.length > 0 ? pageItems.at(-1)!.id : null;

    return {
      revision,
      generatedAt: nowIso,
      ledgerCapture: true,
      cacheHit,
      sourceHealth,
      coverage,
      counts,
      items: pageItems,
      nextCursor,
    };
  }

  private extractThreadChunks(threadId: string): StewardSessionReadChunk[] {
    const events = this.deps.store.listEvents(threadId, 0, -1);
    const chunks: StewardSessionReadChunk[] = [];
    let currentAssistant = '';
    let currentAssistantTs: string | null = null;

    const flushAssistant = () => {
      const trimmed = currentAssistant.trim();
      if (trimmed) {
        chunks.push({ index: chunks.length, role: 'assistant', ts: currentAssistantTs, text: trimmed });
      }
      currentAssistant = '';
      currentAssistantTs = null;
    };

    for (const ev of events) {
      if (ev.type === 'turn.started') {
        flushAssistant();
        const text = typeof ev.data.text === 'string' ? ev.data.text.trim() : '';
        if (text) {
          chunks.push({ index: chunks.length, role: 'user', ts: ev.ts, text });
        }
      } else if (ev.type === 'message.delta') {
        currentAssistantTs ??= ev.ts;
        currentAssistant += typeof ev.data.text === 'string' ? ev.data.text : '';
      } else if (ev.type === 'message.replace') {
        currentAssistantTs = ev.ts;
        currentAssistant = typeof ev.data.text === 'string' ? ev.data.text : '';
      } else if (ev.type === 'turn.done') {
        flushAssistant();
      }
    }
    flushAssistant();
    return chunks;
  }

  private loadThreadDocs(folded: Array<{ active: ThreadView; priors: ThreadView[] }>): SearchableThreadDoc[] {
    const docs: SearchableThreadDoc[] = [];
    const countStmt = this.deps.store.db.prepare('SELECT COUNT(*) as c FROM events WHERE thread_id = ?');

    for (const { active, priors } of folded) {
      const allIds = [...priors.map((p) => p.id), active.id];
      let eventCount = 0;
      for (const id of allIds) {
        const row = countStmt.get(id) as { c?: number } | undefined;
        eventCount += Number(row?.c ?? 0);
      }
      const priorKey = priors.map((p) => `${p.id}:${p.updatedAt}:${p.title}`).join('|');
      const cached = this.cachedThreadDocs.get(active.id);
      if (
        cached &&
        cached.updatedAt === active.updatedAt &&
        cached.title === active.title &&
        cached.priorKey === priorKey &&
        cached.eventCount === eventCount
      ) {
        docs.push({
          thread: active,
          priors,
          docText: cached.text,
          contentSha256: cached.sha256,
        });
        continue;
      }

      const parts: string[] = [
        active.title,
        active.cwd,
        basename(active.cwd || ''),
        active.report?.note ?? '',
        ...priors.map((p) => p.title),
      ];
      for (const id of allIds) {
        for (const chunk of this.extractThreadChunks(id)) {
          parts.push(chunk.text);
        }
      }
      const docText = parts.filter(Boolean).join('\n');
      const sha256 = sha256Text(docText);
      this.cachedThreadDocs.set(active.id, {
        updatedAt: active.updatedAt,
        title: active.title,
        priorKey,
        eventCount,
        text: docText,
        sha256,
      });
      docs.push({ thread: active, priors, docText, contentSha256: sha256 });
    }
    return docs;
  }

  private async loadExternalDoc(ext: ExternalSession): Promise<{ text: string; sha256: string; chunks: StewardSessionReadChunk[] }> {
    const key = `${ext.agent}:${ext.sessionId}`;
    const cached = this.cachedExternalDocs.get(key);
    if (cached && cached.updatedAt === ext.updatedAt && cached.title === ext.title) {
      return cached;
    }

    const chunks: StewardSessionReadChunk[] = [];
    const adapter = this.deps.adapters?.get(ext.agent);
    if (adapter?.loadHistory) {
      try {
        const turns = await adapter.loadHistory(ext.sessionId, ext.cwd);
        for (const turn of turns) {
          if (turn.text?.trim()) {
            chunks.push({ index: chunks.length, role: 'user', ts: ext.updatedAt, text: turn.text.trim() });
          }
          let answer = '';
          for (const ev of turn.events ?? []) {
            if (ev.type === 'message.delta' && typeof ev.data.text === 'string') answer += ev.data.text;
            else if (ev.type === 'message.replace' && typeof ev.data.text === 'string') answer = ev.data.text;
          }
          if (answer.trim()) {
            chunks.push({ index: chunks.length, role: 'assistant', ts: ext.updatedAt, text: answer.trim() });
          }
        }
      } catch {
        // Read-only best effort: if a CLI file is unreadable, title/cwd metadata still indexes.
      }
    }

    const text = [
      ext.title,
      ext.cwd,
      basename(ext.cwd || ''),
      ext.agent,
      ...chunks.map((c) => c.text),
    ]
      .filter(Boolean)
      .join('\n');
    const sha256 = sha256Text(text);
    const entry = { updatedAt: ext.updatedAt, title: ext.title, text, sha256, chunks };
    this.cachedExternalDocs.set(key, entry);
    return entry;
  }

  async sessionsFind(opts: {
    query: string;
    repo?: string | null;
    agent?: string | null;
    cursor?: string | null;
    limit?: number;
  }): Promise<StewardSessionsFindResult> {
    const rawQuery = (opts.query ?? '').trim();
    const limit = Number.isFinite(opts.limit) ? Math.min(Math.max(Math.trunc(opts.limit!), 1), 50) : 20;
    const repoFilter = opts.repo?.trim().toLowerCase() || null;
    const agentFilter = opts.agent?.trim().toLowerCase() || null;
    const ledgerCapture = this.deps.manager.generalSettings().ledgerCapture !== false;
    const nowMs = Date.now();
    const nowIso = new Date(nowMs).toISOString();
    const doneCutoffIso = new Date(nowMs - DONE_RECENT_WINDOW_MS).toISOString();

    if (!ledgerCapture) {
      this.gramIndex.clear();
      return {
        query: rawQuery,
        revision: `sha256:${sha256Text('ledger_capture_disabled')}`,
        indexStatus: 'ready',
        sourceHealth: {
          workbenchThreads: { status: 'unavailable', reason: 'ledger_capture_disabled', itemCount: 0 },
          localSessions: { status: 'unavailable', reason: 'ledger_capture_disabled', itemCount: 0 },
        },
        hits: [],
      };
    }

    const allThreads = [
      ...this.deps.manager.listThreads({ archived: false }),
      ...this.deps.manager.listThreads({ archived: true }),
    ].filter((t) => !this.isInternalThread(t));
    const folded = this.foldHandoverChains(allThreads);
    const threadDocs = this.loadThreadDocs(folded);
    const { health: lsHealth, externalSessions } = this.computeLocalSessionsHealth(true);
    const externalDocs = await Promise.all(
      externalSessions.map(async (ext) => ({ ext, doc: await this.loadExternalDoc(ext) })),
    );

    const indexedInputs = [
      ...threadDocs.map((d) => ({
        docId: `thread:${d.thread.id}`,
        text: d.docText,
        contentSha256: d.contentSha256,
      })),
      ...externalDocs.map(({ ext, doc }) => ({
        docId: `local:${ext.agent}:${ext.sessionId}`,
        text: doc.text,
        contentSha256: doc.sha256,
      })),
    ];

    if (!existsSync(this.gramIndex.searchDir)) {
      this.gramIndex.rebuild(indexedInputs);
    } else {
      this.gramIndex.sync(indexedInputs);
    }

    const { excluded } = rawQuery
      ? this.gramIndex.excludeNonMatching(
          rawQuery,
          indexedInputs.map((d) => ({ docId: d.docId, contentSha256: d.contentSha256 })),
        )
      : { excluded: new Set<string>() };

    const queryTerms = rawQuery
      .normalize('NFC')
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

    const matchesFilter = (cwd: string | null, agent: string): boolean => {
      if (repoFilter && !(cwd ?? '').toLowerCase().includes(repoFilter)) return false;
      if (agentFilter && agent.toLowerCase() !== agentFilter) return false;
      return true;
    };

    const scored: Array<{ score: number; hit: StewardSessionFindHit }> = [];

    for (const d of threadDocs) {
      const docId = `thread:${d.thread.id}`;
      if (excluded.has(docId)) continue;
      if (!matchesFilter(d.thread.cwd, d.thread.agent)) continue;

      const normDoc = d.docText.normalize('NFC').toLowerCase();
      if (queryTerms.length > 0 && !queryTerms.every((term) => normDoc.includes(term))) continue;

      const normTitle = d.thread.title.normalize('NFC').toLowerCase();
      const normPriors = d.priors.map((p) => p.title.normalize('NFC').toLowerCase());
      const normCwd = (d.thread.cwd || '').normalize('NFC').toLowerCase();
      let score = d.thread.archivedAt ? 5 : 25;
      for (const term of queryTerms) {
        if (normTitle === term) score += 120;
        else if (normTitle.includes(term)) score += 70;
        if (normPriors.some((pt) => pt.includes(term))) score += 45;
        if (basename(normCwd) === term || normCwd.endsWith(`/${term}`)) score += 55;
        else if (normCwd.includes(term)) score += 30;
      }

      const derived = deriveThreadPhase(d.thread, doneCutoffIso);
      const phase: StewardLedgerPhase = derived.phase === 'done_old' ? 'done_recent' : derived.phase;
      const goalId = d.thread.loopx?.goalId ?? d.thread.loopxInfo?.goalId ?? null;
      scored.push({
        score,
        hit: {
          id: docId,
          kind: 'workbench_thread',
          title: d.thread.title,
          agent: d.thread.agent,
          cwd: d.thread.cwd || '',
          updatedAt: d.thread.updatedAt,
          phase,
          goalId,
          snippet: this.extractSnippet(d.docText, queryTerms) ?? d.thread.report?.note ?? d.thread.preview ?? d.thread.title,
          jumpTarget: { kind: 'thread', threadId: d.thread.id },
        },
      });
    }

    for (const { ext, doc } of externalDocs) {
      const docId = `local:${ext.agent}:${ext.sessionId}`;
      if (excluded.has(docId)) continue;
      if (!matchesFilter(ext.cwd, ext.agent)) continue;

      const normDoc = doc.text.normalize('NFC').toLowerCase();
      if (queryTerms.length > 0 && !queryTerms.every((term) => normDoc.includes(term))) continue;

      const normTitle = (ext.title || '').normalize('NFC').toLowerCase();
      const normCwd = (ext.cwd || '').normalize('NFC').toLowerCase();
      let score = 20;
      for (const term of queryTerms) {
        if (normTitle === term) score += 115;
        else if (normTitle.includes(term)) score += 68;
        if (basename(normCwd) === term || normCwd.endsWith(`/${term}`)) score += 55;
        else if (normCwd.includes(term)) score += 30;
      }
      scored.push({
        score,
        hit: {
          id: docId,
          kind: 'local_session',
          title: ext.title || `${ext.agent} ${ext.sessionId.slice(0, 8)}`,
          agent: ext.agent,
          cwd: ext.cwd || '',
          updatedAt: ext.updatedAt,
          phase: 'idle_open',
          goalId: ext.loopx?.goalId ?? null,
          snippet: this.extractSnippet(doc.text, queryTerms) ?? ext.title ?? ext.cwd ?? '',
          jumpTarget: {
            kind: 'local_session',
            agent: ext.agent,
            sessionId: ext.sessionId,
            cwd: ext.cwd || '',
          },
        },
      });
    }

    scored.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      const ta = a.hit.updatedAt ?? '';
      const tb = b.hit.updatedAt ?? '';
      if (tb !== ta) return tb.localeCompare(ta);
      return a.hit.id.localeCompare(b.hit.id);
    });

    const revision = this.buildRevisionKey(true, allThreads, externalSessions, lsHealth, Math.floor(nowMs / 60_000));
    return {
      query: rawQuery,
      revision,
      indexStatus: 'ready',
      sourceHealth: {
        workbenchThreads: { status: 'ok', scannedAt: nowIso, itemCount: folded.length },
        localSessions: lsHealth,
      },
      hits: scored.slice(0, limit).map((s) => s.hit),
    };
  }

  private extractSnippet(text: string, terms: string[]): string | null {
    if (!terms.length) return null;
    const norm = text.normalize('NFC');
    const lower = norm.toLowerCase();
    const first = terms[0]!;
    const idx = lower.indexOf(first);
    if (idx < 0) return null;
    const start = Math.max(0, idx - 24);
    const end = Math.min(norm.length, idx + first.length + 48);
    const slice = norm.slice(start, end).replace(/\s+/g, ' ').trim();
    return `${start > 0 ? '…' : ''}${slice}${end < norm.length ? '…' : ''}`;
  }

  /**
   * Read-only bounded transcript/outline inspection.
   * S1 invariant: NEVER imports or mutates an external `local_session`.
   */
  async sessionRead(opts: {
    kind: 'workbench_thread' | 'local_session';
    id: string;
    agent?: string | null;
    mode?: 'outline' | 'recent_turns' | 'turn_range' | null;
    cursor?: string | null;
    maxChars?: number;
  }): Promise<StewardSessionReadResult> {
    if (this.deps.manager.generalSettings().ledgerCapture === false) {
      throw new HttpError(403, 'ledger_capture_disabled');
    }
    const mode = opts.mode === 'outline' || opts.mode === 'turn_range' ? opts.mode : 'recent_turns';
    const maxChars = Number.isFinite(opts.maxChars) ? Math.min(Math.max(Math.trunc(opts.maxChars!), 200), 24_000) : 8_000;

    if (opts.kind === 'workbench_thread') {
      const record = this.deps.store.getThread(opts.id);
      if (!record) throw new HttpError(404, 'session_not_found');
      const view = this.deps.manager.threadView(opts.id);
      if (this.isInternalThread(view)) throw new HttpError(404, 'session_not_found');

      const allChunks = this.extractThreadChunks(view.id);
      const { chunks, nextCursor } = this.boundChunks(allChunks, mode, opts.cursor ?? null, maxChars);
      return {
        id: `thread:${view.id}`,
        kind: 'workbench_thread',
        title: view.title,
        agent: view.agent,
        cwd: view.cwd || '',
        updatedAt: view.updatedAt,
        imported: true,
        mode,
        chunks,
        nextCursor,
      };
    }

    if (opts.kind === 'local_session') {
      const allLocal = this.deps.localSessions?.all() ?? [];
      const found =
        allLocal.find((s) => s.sessionId === opts.id && (!opts.agent || s.agent === opts.agent)) ??
        this.deps.localSessions?.find(opts.id);
      if (!found || (opts.agent && found.agent !== opts.agent)) {
        throw new HttpError(404, 'session_not_found');
      }
      const doc = await this.loadExternalDoc(found);
      const { chunks, nextCursor } = this.boundChunks(doc.chunks, mode, opts.cursor ?? null, maxChars);
      return {
        id: `local:${found.agent}:${found.sessionId}`,
        kind: 'local_session',
        title: found.title || `${found.agent} ${found.sessionId.slice(0, 8)}`,
        cwd: found.cwd || '',
        agent: found.agent,
        updatedAt: found.updatedAt,
        imported: false,
        mode,
        chunks,
        nextCursor,
      };
    }

    throw new HttpError(400, 'invalid_target_kind');
  }

  private boundChunks(
    allChunks: StewardSessionReadChunk[],
    mode: 'outline' | 'recent_turns' | 'turn_range',
    cursor: string | null,
    maxChars: number,
  ): { chunks: StewardSessionReadChunk[]; nextCursor: string | null } {
    if (allChunks.length === 0) return { chunks: [], nextCursor: null };
    if (mode === 'outline') {
      const first = allChunks[0]!;
      const last = allChunks.length > 1 ? allChunks.at(-1)! : null;
      const picked = last ? [first, last] : [first];
      return {
        chunks: picked.map((c) => ({ ...c, text: c.text.slice(0, Math.floor(maxChars / picked.length)) })),
        nextCursor: null,
      };
    }

    const startIdx = cursor !== null && Number.isFinite(Number(cursor)) ? Math.max(0, Math.trunc(Number(cursor))) : null;
    if (mode === 'recent_turns' && startIdx === null) {
      let budget = maxChars;
      const reversed: StewardSessionReadChunk[] = [];
      for (let i = allChunks.length - 1; i >= 0; i--) {
        const c = allChunks[i]!;
        const clipped = c.text.slice(0, Math.min(c.text.length, Math.max(200, budget)));
        if (reversed.length > 0 && clipped.length > budget) break;
        reversed.push({ ...c, text: clipped });
        budget -= clipped.length;
        if (budget <= 0) break;
      }
      reversed.reverse();
      const earliest = reversed[0]?.index ?? 0;
      return {
        chunks: reversed,
        nextCursor: earliest > 0 ? String(earliest - 1) : null,
      };
    }

    const from = startIdx ?? 0;
    let budget = maxChars;
    const forward: StewardSessionReadChunk[] = [];
    for (let i = from; i < allChunks.length; i++) {
      const c = allChunks[i]!;
      const clipped = c.text.slice(0, Math.min(c.text.length, Math.max(200, budget)));
      if (forward.length > 0 && clipped.length > budget) {
        return { chunks: forward, nextCursor: String(i) };
      }
      forward.push({ ...c, text: clipped });
      budget -= clipped.length;
    }
    return { chunks: forward, nextCursor: null };
  }
}

/**
 * Private supervisor Unix socket server (`<dataDir>/steward/calli.sock`).
 * Enforces caller-credential ACL (`403 unauthorized_caller_role` to workers).
 */
export function stewardServer(service: StewardLedgerService): Server {
  return createServer(async (req, res) => {
    try {
      service.verifySupervisorToken(req.headers.authorization);
      if (req.method !== 'POST' || req.url !== '/rpc') {
        throw new HttpError(404, 'not_found');
      }
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of req) {
        const buf = chunk as Buffer;
        size += buf.length;
        if (size > 65536) throw new HttpError(413, 'request_too_large');
        chunks.push(buf);
      }
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown>;
      } catch {
        throw new HttpError(400, 'invalid_json');
      }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new HttpError(400, 'payload_must_be_object');
      }

      const method = String(payload.method ?? '');
      const params = (payload.params && typeof payload.params === 'object' ? payload.params : {}) as Record<string, unknown>;

      let result: unknown;
      if (method === 'steward_ledger_read') {
        const rawSince = params.includeDoneSince ?? params.include_done_since;
        result = service.ledgerRead({
          cursor: typeof params.cursor === 'string' ? params.cursor : null,
          limit: typeof params.limit === 'number' ? params.limit : undefined,
          includeDoneSince: typeof rawSince === 'string' ? rawSince : null,
        });
      } else if (method === 'steward_sessions_find') {
        result = await service.sessionsFind({
          query: typeof params.query === 'string' ? params.query : '',
          repo: typeof params.repo === 'string' ? params.repo : null,
          agent: typeof params.agent === 'string' ? params.agent : null,
          cursor: typeof params.cursor === 'string' ? params.cursor : null,
          limit: typeof params.limit === 'number' ? params.limit : undefined,
        });
      } else if (method === 'steward_session_read') {
        const target = (params.target_ref && typeof params.target_ref === 'object' ? params.target_ref : null) as Record<string, unknown> | null;
        const rawKind = params.kind ?? target?.kind;
        const kind = rawKind === 'local_session' ? 'local_session' : rawKind === 'workbench_thread' ? 'workbench_thread' : null;
        if (!kind) throw new HttpError(400, 'invalid_target_kind');
        const id = String(params.id ?? (kind === 'workbench_thread' ? target?.threadId : target?.sessionId) ?? '');
        if (!id) throw new HttpError(400, 'missing_target_id');
        const rawAgent = params.agent ?? target?.agent;
        const rawMode = params.mode;
        const rawCursor = params.cursor ?? params.turn_cursor;
        const rawMaxChars = params.maxChars ?? params.max_chars;
        result = await service.sessionRead({
          kind,
          id,
          agent: typeof rawAgent === 'string' ? rawAgent : null,
          mode: rawMode === 'outline' || rawMode === 'recent_turns' || rawMode === 'turn_range' ? rawMode : null,
          cursor: typeof rawCursor === 'string' ? rawCursor : null,
          maxChars: typeof rawMaxChars === 'number' ? rawMaxChars : undefined,
        });
      } else {
        throw new HttpError(400, `unsupported_steward_method:${method}`);
      }

      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' }).end(JSON.stringify({ ok: true, result }));
    } catch (err) {
      const status = err instanceof HttpError ? err.status : 400;
      const error = err instanceof Error ? err.message : 'bad_request';
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }).end(JSON.stringify({ ok: false, error }));
    }
  });
}
