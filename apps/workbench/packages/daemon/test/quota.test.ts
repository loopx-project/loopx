import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { AgyAdapter } from '../src/adapters/agy.js';
import { ClaudeAcpAdapter, parseClaudeUsage } from '../src/adapters/claude.js';
import { parseAgyUsage } from '../src/adapters/agy.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

// The quota panel: plan usage read by one-off CLI runs, parsed into a shared shape.

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };

test('parseClaudeUsage keeps session and all-model weekly limits in one account pool, with resets crossing the year', () => {
  const now = new Date('2026-12-28T10:00:00Z');
  const windows = parseClaudeUsage(
    ['Intro line', 'Current session: 95% used · resets Dec 29, 1:30am (UTC)', '', 'Current week (all models): 40% used · resets Jan 2, 7am (UTC)', 'Advice that must be ignored'].join('\n'),
    now,
  );
  assert.deepEqual(windows, [
    { label: '5H', usedPercent: 95, resetsAt: '2026-12-29T01:30:00.000Z' },
    { label: '7D', usedPercent: 40, resetsAt: '2027-01-02T07:00:00.000Z' },
  ]);
});

test('parseClaudeUsage keeps a window that has no reset time, and skips junk', () => {
  const windows = parseClaudeUsage('Current session: 3% used\nCurrent nonsense: 9% used');
  assert.deepEqual(windows, [{ label: '5H', usedPercent: 3 }]);
});

test('parseAgyUsage flips REMAINING into used and keeps model groups apart', () => {
  const windows = parseAgyUsage(
    ['Gemini Models\tWeekly Limit Remaining\t98%\t2026-10-16T11:59:18Z', 'Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-10-10T15:33:54Z', 'not a quota row'].join('\n'),
  );
  assert.deepEqual(windows, [
    { label: '7D', scope: 'Gemini Models', usedPercent: 2, resetsAt: '2026-10-16T11:59:18Z' },
    { label: '5H', scope: 'Claude and GPT models', usedPercent: 0, resetsAt: '2026-10-10T15:33:54Z' },
  ]);
});

let server: Server;
let base: string;
let dataDir: string;
let manager: ThreadManager;
let store: Store;
let usageLog: string;
let oldQuotaBin: string | undefined;

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-quota-test-'));
  usageLog = join(dataDir, 'claude-usage-runs.log');
  oldQuotaBin = process.env.CALLI_CLAUDE_QUOTA_BIN;
  process.env.CALLI_CLAUDE_QUOTA_BIN = join(here, 'fixtures', 'fake-claude-usage.sh');
  process.env.FAKE_CLAUDE_USAGE_LOG = usageLog;
  const adapters = new Map<string, AgentAdapter>();
  adapters.set('claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')]));
  adapters.set('pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')]));
  adapters.set('agy', new AgyAdapter([process.execPath, join(here, 'fixtures', 'fake-agy.mjs')]));
  store = new Store(dataDir);
  manager = new ThreadManager(store, adapters, [], new AttachmentStore(dataDir));
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const { port } = server.address() as AddressInfo;
  base = `http://127.0.0.1:${port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  store.close();
  process.env.CALLI_CLAUDE_QUOTA_BIN = oldQuotaBin;
  delete process.env.FAKE_CLAUDE_USAGE_LOG;
  rmSync(dataDir, { recursive: true, force: true });
});

interface Window {
  label: string;
  scope?: string;
  usedPercent: number;
  resetsAt?: string;
}

test('the quota route reads each agent, and refuses those without a plan', async () => {
  const claude = (await (await fetch(`${base}/api/agents/claude/quota`, { headers: auth })).json()) as { windows: Window[]; checkedAt: string };
  assert.deepEqual(claude.windows.map((w) => [w.label, w.usedPercent]), [
    ['5H', 95],
    ['7D', 40],
  ]);

  const agy = (await (await fetch(`${base}/api/agents/agy/quota`, { headers: auth })).json()) as { windows: Window[] };
  assert.deepEqual(agy.windows.map((w) => [w.label, w.scope, w.usedPercent]), [
    ['7D', 'Fast models', 3],
    ['5H', 'Slow | models', 60],
  ]);

  assert.equal((await fetch(`${base}/api/agents/pi/quota`, { headers: auth })).status, 400);
  assert.equal((await fetch(`${base}/api/agents/nope/quota`, { headers: auth })).status, 404);
});

test('answers are shared for a minute, so the probe runs once', async () => {
  for (let i = 0; i < 3; i++) await fetch(`${base}/api/agents/claude/quota`, { headers: auth });
  // The adapter was constructed with the fake CLIs above; the one probe so far came from the
  // route test. Whatever the exact count, three more requests must not run three more probes.
  const runs = readFileSync(usageLog, 'utf8').trim().split('\n').filter(Boolean);
  assert.ok(runs.length <= 2, `probe ran ${runs.length} times`);
});

test('the probe asks claude not to save a session for it', () => {
  const runs = readFileSync(usageLog, 'utf8').trim().split('\n').filter(Boolean);
  assert.ok(runs.length > 0);
  for (const run of runs) assert.match(run, / -p \/usage --no-session-persistence$/);
});

test('?refresh=1 skips the minute cache: the probe runs again right away', async () => {
  const runsBefore = readFileSync(usageLog, 'utf8').trim().split('\n').filter(Boolean).length;
  const first = (await (await fetch(`${base}/api/agents/claude/quota`, { headers: auth })).json()) as { checkedAt: string };
  const cached = (await (await fetch(`${base}/api/agents/claude/quota`, { headers: auth })).json()) as { checkedAt: string };
  assert.equal(cached.checkedAt, first.checkedAt, 'without the flag the cached answer comes back');
  const forced = (await (await fetch(`${base}/api/agents/claude/quota?refresh=1`, { headers: auth })).json()) as { checkedAt: string };
  const runsAfter = readFileSync(usageLog, 'utf8').trim().split('\n').filter(Boolean).length;
  assert.equal(runsAfter, runsBefore + 1, 'the forced read is one new probe');
  assert.notEqual(forced.checkedAt, first.checkedAt);
});
