import { test } from 'node:test';
import assert from 'node:assert/strict';
import { request as httpRequest } from 'node:http';
import type { AddressInfo } from 'node:net';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { performance } from 'node:perf_hooks';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { Workdirs } from '../src/workdirs.js';
import { LocalSessions } from '../src/local-sessions.js';
import { StewardLedgerService, stewardServer } from '../src/steward-ledger.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import {
  sessionEnvironment,
  type AgentAdapter,
  type AgentSession,
  type AgentSessionSummary,
  type HistoryTurn,
  type ModelOption,
  type SlashCommand,
} from '../src/adapters/types.js';

class FakeCliAdapter implements AgentAdapter {
  readonly id = 'codex';
  readonly label = 'Codex';
  readonly capabilities = {
    resume: true,
    approvals: true,
    permissionModes: ['read_only', 'auto_edit', 'full_access'] as const,
    effortLevels: [] as const,
    images: false,
    deleteSession: false,
    history: true,
    quota: false,
    fork: false,
    forkAt: false,
    multiAccount: false,
    forkInto: false,
  };

  constructor(
    private readonly sessions: AgentSessionSummary[],
    private readonly histories: Record<string, HistoryTurn[]>,
  ) {}

  async available(): Promise<boolean> {
    return true;
  }
  async models(): Promise<ModelOption[]> {
    return [];
  }
  async commands(): Promise<SlashCommand[]> {
    return [];
  }
  resumeCommand(agentSessionId: string): string {
    return `codex resume ${agentSessionId}`;
  }
  sessionOrigin(): 'cli' {
    return 'cli';
  }
  async listSessions(): Promise<AgentSessionSummary[]> {
    return this.sessions;
  }
  async loadHistory(sessionId: string): Promise<HistoryTurn[]> {
    return this.histories[sessionId] ?? [];
  }
  async start(): Promise<AgentSession> {
    throw new Error('unexpected live session start during S1 read-only inspection');
  }
}

function callUnixSocket(
  socketPath: string,
  body: unknown,
  token?: string,
): Promise<{ status: number; json: Record<string, any> }> {
  const payload = JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = httpRequest(
      {
        socketPath,
        path: '/rpc',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c) => chunks.push(c as Buffer));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          resolve({ status: res.statusCode ?? 0, json: text ? JSON.parse(text) : {} });
        });
      },
    );
    req.on('error', reject);
    req.end(payload);
  });
}

async function createHarness(opts: { localScanEnabled?: boolean } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'steward-ledger-'));
  const cwd = join(dir, 'repo-alpha');
  mkdirSync(cwd, { recursive: true });

  const fortyDaysAgo = new Date(Date.now() - 40 * 86_400_000).toISOString();
  const fiveDaysAgo = new Date(Date.now() - 5 * 86_400_000).toISOString();
  const twoMinutesAgo = new Date(Date.now() - 2 * 60_000).toISOString();

  const externalSessions: AgentSessionSummary[] = [
    {
      id: 'ext-cli-cat',
      cwd,
      title: 'CLI 排查黑猫内存泄漏',
      updatedAt: twoMinutesAgo,
    },
    {
      id: 'ext-cli-old',
      cwd,
      title: '四十天前未收尾的 CLI 迁移',
      updatedAt: fortyDaysAgo,
    },
  ];
  const externalHistories: Record<string, HistoryTurn[]> = {
    'ext-cli-cat': [
      {
        text: '帮我看一下黑猫服务的 OOM 日志',
        images: [],
        events: [{ type: 'message.delta', data: { text: '定位到 gram 缓存未释放，准备修 patch' } }],
      },
    ],
    'ext-cli-old': [
      {
        text: '开始迁移 schema',
        images: [],
        events: [{ type: 'message.delta', data: { text: '已完成第一阶段，待确认切换窗口' } }],
      },
    ],
  };

  const adapter = new FakeCliAdapter(externalSessions, externalHistories);
  const adapters = new Map<string, AgentAdapter>([['codex', adapter]]);
  const store = new Store(dir);
  const dirs = new Workdirs([cwd], join(dir, 'dirs.json'), join(dir, 'scratch'));
  const manager = new ThreadManager(store, adapters, dirs, new AttachmentStore(dir));
  const localSessions = new LocalSessions(
    {
      adapters,
      workdirs: () => [cwd],
      hidden: () => [],
      importedSessions: () => store.agentSessionOwners(),
      instances: () => [{ id: 'codex', hidden: false, store: 'codex:' }],
    },
    { enabled: opts.localScanEnabled ?? true },
  );
  if (opts.localScanEnabled ?? true) {
    await localSessions.scan();
  }

  // 1. Handover chain: segment 1 handed over to segment 2 (marked review/needs_user via progress report)
  const seg1 = await manager.createThread({ agent: 'codex', cwd, title: '支付网关重构' });
  store.appendEvent(seg1.id, 't-seg1', 'turn.started', { text: '先跑支付网关单元测试 npm run build' });
  store.appendEvent(seg1.id, 't-seg1', 'message.delta', { text: '第一段上下文已满，准备接力' });
  const seg1End = store.appendEvent(seg1.id, 't-seg1', 'turn.done', { stopReason: 'end_turn' });

  const seg2 = await manager.createThread({ agent: 'codex', cwd, title: '支付网关重构' });
  store.db
    .prepare('UPDATE threads SET handover = ?, report = ? WHERE id = ?')
    .run(
      JSON.stringify({ sourceId: seg1.id, title: seg1.title, agent: 'codex', mode: 'continue', sourceSeq: seg1End.seq }),
      JSON.stringify({ stage: 'review', note: '待你验收签名校验改动', at: twoMinutesAgo }),
      seg2.id,
    );
  store.appendEvent(seg2.id, 't-seg2', 'turn.started', { text: '继续修支付网关签名校验' });
  store.appendEvent(seg2.id, 't-seg2', 'message.delta', { text: '签名校验已修复，请验收' });
  store.appendEvent(seg2.id, 't-seg2', 'turn.done', { stopReason: 'end_turn' });

  // 2. Old >30-day open Workbench thread (must NOT be dropped by age!)
  const oldOpen = await manager.createThread({ agent: 'codex', cwd, title: '35天前未结项的网关压测' });
  store.db.prepare('UPDATE threads SET updated_at = ? WHERE id = ?').run(fortyDaysAgo, oldOpen.id);

  // 3. Old completed Workbench thread (>24h ago, done_at set) — excluded from default open ledger
  const oldDone = await manager.createThread({ agent: 'codex', cwd, title: '上周已完成的清理任务' });
  store.db.prepare('UPDATE threads SET done_at = ?, updated_at = ? WHERE id = ?').run(fiveDaysAgo, fiveDaysAgo, oldDone.id);

  const stewardLedger = new StewardLedgerService({ store, manager, localSessions, adapters, dataDir: dir });
  const sockServer = stewardServer(stewardLedger);
  assert.equal(await listenPrivate(sockServer, stewardLedger.socketPath), true);

  const httpServer = createHttpServer({
    manager,
    localSessions,
    stewardLedger,
    token: 'owner-http-token-1234',
    webDir: null,
    version: 'test',
  });
  await new Promise<void>((r) => httpServer.listen(0, '127.0.0.1', r));
  const baseUrl = `http://127.0.0.1:${(httpServer.address() as AddressInfo).port}`;

  return {
    dir,
    cwd,
    store,
    manager,
    localSessions,
    stewardLedger,
    seg1,
    seg2,
    oldOpen,
    oldDone,
    baseUrl,
    async close() {
      await manager.shutdown();
      await new Promise<void>((r) => sockServer.close(() => r()));
      await new Promise<void>((r) => httpServer.close(() => r()));
      stewardLedger.dispose();
      store.close();
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

test('steward_ledger_read folds handover chains, retains >30-day open work, and hits <100ms warm cache', async () => {
  const h = await createHarness();
  try {
    const cred = JSON.parse(readFileSync(h.stewardLedger.credentialPath, 'utf8')) as { token: string; socketPath: string };
    assert.equal(statSync(h.stewardLedger.credentialPath).mode & 0o777, 0o600);

    const first = await callUnixSocket(cred.socketPath, { method: 'steward_ledger_read', params: {} }, cred.token);
    assert.equal(first.status, 200);
    const snap = first.json.result;
    assert.equal(snap.ledgerCapture, true);
    assert.equal(snap.coverage.workbenchThreads, 'complete');
    assert.equal(snap.coverage.localSessions, 'complete');

    // Handover chain seg1 -> seg2 is folded into a single needs_user row pointing to seg2.
    const paymentRows = snap.items.filter((i: any) => i.title === '支付网关重构');
    assert.equal(paymentRows.length, 1);
    assert.equal(paymentRows[0].phase, 'needs_user');
    assert.deepEqual(paymentRows[0].chainIds, [h.seg1.id, h.seg2.id]);
    assert.deepEqual(paymentRows[0].jumpTarget, { kind: 'thread', threadId: h.seg2.id });

    // >30-day open threads & external CLI sessions are retained, while 5-day-old done thread is omitted.
    const titles = snap.items.map((i: any) => i.title);
    assert.ok(titles.includes('35天前未结项的网关压测'), 'retains >30d open Workbench thread');
    assert.ok(titles.includes('四十天前未收尾的 CLI 迁移'), 'retains >30d open external CLI session');
    assert.ok(!titles.includes('上周已完成的清理任务'), 'excludes old done thread by default');

    // Warm read latency must stay well below 100ms.
    const t0 = performance.now();
    const warm = h.stewardLedger.ledgerRead({});
    const elapsedMs = performance.now() - t0;
    assert.equal(warm.revision, snap.revision);
    assert.equal(warm.cacheHit, true);
    assert.ok(elapsedMs < 100, `expected warm ledgerRead < 100ms, got ${elapsedMs.toFixed(2)}ms`);
  } finally {
    await h.close();
  }
});

test('steward socket rejects worker/unauthenticated callers with 403 and scrubs worker env', async () => {
  const h = await createHarness();
  try {
    const noToken = await callUnixSocket(h.stewardLedger.socketPath, { method: 'steward_ledger_read', params: {} });
    assert.equal(noToken.status, 403);
    assert.equal(noToken.json.error, 'unauthorized_caller_role');

    const badToken = await callUnixSocket(
      h.stewardLedger.socketPath,
      { method: 'steward_sessions_find', params: { query: '猫' } },
      'worker-forged-token',
    );
    assert.equal(badToken.status, 403);
    assert.equal(badToken.json.error, 'unauthorized_caller_role');

    // Verify worker child sessionEnvironment strips LOOPX_WORKBENCH_STEWARD_CREDENTIAL.
    const prev = process.env.LOOPX_WORKBENCH_STEWARD_CREDENTIAL;
    process.env.LOOPX_WORKBENCH_STEWARD_CREDENTIAL = h.stewardLedger.credentialPath;
    try {
      const childEnv = sessionEnvironment({});
      assert.equal(childEnv.LOOPX_WORKBENCH_STEWARD_CREDENTIAL, undefined);
      assert.equal(childEnv.LOOCHAT_STEWARD_SOCKET, undefined);
    } finally {
      if (prev === undefined) delete process.env.LOOPX_WORKBENCH_STEWARD_CREDENTIAL;
      else process.env.LOOPX_WORKBENCH_STEWARD_CREDENTIAL = prev;
    }
  } finally {
    await h.close();
  }
});

test('steward_sessions_find and steward_session_read search & read external CLI transcripts with 0 silent imports', async () => {
  const h = await createHarness();
  try {
    const threadCountBefore = h.store.listThreads().length;

    // 1. Search 1-char CJK "猫" and mid-word ASCII "uild" (across folded handover segment 1!).
    const findCat = await fetch(`${h.baseUrl}/api/steward/sessions/find?q=${encodeURIComponent('猫')}`, {
      headers: { Authorization: 'Bearer owner-http-token-1234' },
    }).then((r) => r.json());
    assert.equal(findCat.hits.length, 1);
    assert.equal(findCat.hits[0].id, 'local:codex:ext-cli-cat');
    assert.deepEqual(findCat.hits[0].jumpTarget, {
      kind: 'local_session',
      agent: 'codex',
      sessionId: 'ext-cli-cat',
      cwd: h.cwd,
    });

    const findBuild = await fetch(`${h.baseUrl}/api/steward/sessions/find?q=uild`, {
      headers: { Authorization: 'Bearer owner-http-token-1234' },
    }).then((r) => r.json());
    assert.equal(findBuild.hits.length, 1);
    assert.equal(findBuild.hits[0].jumpTarget.threadId, h.seg2.id, 'matches earlier handover segment text and jumps to head');

    // 2. Read external CLI session transcript without importing it.
    const readExternal = await fetch(
      `${h.baseUrl}/api/steward/sessions/read?kind=local_session&id=ext-cli-cat&agent=codex&mode=recent_turns`,
      { headers: { Authorization: 'Bearer owner-http-token-1234' } },
    ).then((r) => r.json());
    assert.equal(readExternal.imported, false);
    assert.equal(readExternal.chunks.length, 2);
    assert.match(readExternal.chunks[0].text, /黑猫服务的 OOM 日志/);
    assert.match(readExternal.chunks[1].text, /gram 缓存未释放/);

    assert.equal(h.store.listThreads().length, threadCountBefore, 'zero threads silently imported');
  } finally {
    await h.close();
  }
});

test('LOOCHAT_LOCAL_SCAN=off reports explicit unavailable coverage gap, and ledgerCapture=false cleanly rolls back', async () => {
  const h = await createHarness({ localScanEnabled: false });
  try {
    const snap = h.stewardLedger.ledgerRead({});
    assert.equal(snap.sourceHealth.localSessions.status, 'unavailable');
    assert.equal(snap.sourceHealth.localSessions.reason, 'local_scan_disabled');
    assert.equal(snap.coverage.localSessions, 'unavailable');
    assert.equal(snap.coverage.workbenchThreads, 'complete');

    // Turn off ledgerCapture via HTTP general-settings and verify rollback + .search cleanup.
    assert.equal(existsSync(join(h.dir, '.search')), false);
    await h.stewardLedger.sessionsFind({ query: '支付网关' });
    assert.equal(existsSync(join(h.dir, '.search')), true);

    const saved = await fetch(`${h.baseUrl}/api/general-settings`, {
      method: 'PUT',
      headers: {
        Authorization: 'Bearer owner-http-token-1234',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        startupPage: 'new-thread',
        autoArchiveDays: null,
        autoImportLocal: true,
        ledgerCapture: false,
      }),
    }).then((r) => r.json());
    assert.equal(saved.ledgerCapture, false);

    const disabledLedger = h.stewardLedger.ledgerRead({});
    assert.equal(disabledLedger.ledgerCapture, false);
    assert.equal(disabledLedger.items.length, 0);
    assert.equal(disabledLedger.sourceHealth.workbenchThreads.reason, 'ledger_capture_disabled');

    const disabledFind = await h.stewardLedger.sessionsFind({ query: '支付网关' });
    assert.equal(disabledFind.hits.length, 0);
    assert.equal(existsSync(join(h.dir, '.search')), false, 'clears .search cache when ledgerCapture=false');
  } finally {
    await h.close();
  }
});
