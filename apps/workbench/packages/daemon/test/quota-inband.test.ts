import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { acpRateLimitQuota } from '../src/adapters/acp.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { Workdirs } from '../src/workdirs.js';
import type { PlanQuota } from '../src/adapters/types.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const fixture = (name: string) => [process.execPath, join(fixtures, `fake-${name}.mjs`)];
const wait = async (check: () => any, timeout = 15000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};

test('a rate_limit_event becomes quota windows, unknown kinds stay as they are', () => {
  const now = Math.floor(Date.now() / 1000);
  const quota = acpRateLimitQuota({
    status: 'ok', rateLimitType: 'five_hour', utilization: 0.9, resetsAt: now + 3600,
    unifiedWindows: {
      five_hour: { utilization: 0.9, resetsAt: now + 3600 },
      seven_day: { utilization: 0.42, resetsAt: now + 86400 },
      seven_day_opus: { utilization: 0.71 },
      seven_day_overage_included: { utilization: 0.05 },
    },
  })!;
  assert.deepEqual(quota.windows.map(w => [w.label, w.scope, w.usedPercent]).sort((a, b) => a[2] - b[2]),
    [['5H', undefined, 90], ['7D', undefined, 42], ['7D', 'Opus', 71], ['7D', 'Fable', 5]].sort((a, b) => a[2] - b[2]));
  assert.deepEqual(quota.windows.find(w => w.scope === 'Fable')?.modelIds, ['fable'], 'the Fable pool is scoped to Fable models');
  assert.ok(quota.windows[0].resetsAt && Date.parse(quota.windows[0].resetsAt) > Date.now());
  // A rejection about a window it did not itemize counts as that window being full.
  const rejected = acpRateLimitQuota({ status: 'rejected', rateLimitType: 'seven_day_sonnet', resetsAt: now + 7200 })!;
  assert.equal(rejected.windows.find(w => w.scope === 'Sonnet')?.usedPercent, 100);
  assert.deepEqual(acpRateLimitQuota({ rateLimitType: 'seven_day', utilization: 0.95 })?.windows,
    [{ label: '7D', usedPercent: 95 }], 'single-window updates also use the account-wide pool');
  assert.equal(acpRateLimitQuota({}), null);
  assert.equal(acpRateLimitQuota('junk'), null);
  assert.equal(acpRateLimitQuota({ unifiedWindows: { five_hour: { utilization: 9 } } }), null, 'over-full utilization is not a share');
});

test('the adapter observes quota as the agent answers', async () => {
  const adapter = new ClaudeAcpAdapter(fixture('acp'));
  const seen: PlanQuota[] = [];
  adapter.onQuotaObserved = q => seen.push(q);
  const session = await adapter.start({ cwd: process.cwd() });
  try {
    const events: unknown[] = [];
    await session.prompt({ text: 'rate-limit-tell', images: [] }, { emit: e => events.push(e), requestApproval: async () => ({ cancelled: true }) });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].windows.find(w => w.label === '5H')?.usedPercent, 90);
    assert.equal(seen[0].windows.find(w => w.label === '7D' && w.scope === undefined)?.usedPercent, 42);
  } finally { await session.close(); }
});

test('the hub reads plan usage the thread itself reported, with no probe', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-quota-inband-'));
  const store = new Store(dir);
  const adapters = new Map([['cc', new ClaudeAcpAdapter(fixture('acp'))] as const]);
  const manager = new ThreadManager(store, adapters as any, new Workdirs([dir], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir), () => [{ id: 'cc', adapter: 'claude' }]);
  try {
    const thread = await manager.createThread({ agent: 'cc', cwd: dir });
    await manager.startTurn(thread.id, { text: 'rate-limit-tell' });
    await wait(() => manager.threadView(thread.id).status !== 'running');
    // The in-band reading answers decisionQuota without spawning any usage probe.
    const quota = await manager.decisionQuota('cc');
    assert.deepEqual(quota?.windows.map(w => [w.label, w.scope, w.usedPercent]),
      [['5H', undefined, 90], ['7D', undefined, 42]], 'both account-wide periods reach quota decisions together');
    // And it is what a plain cached read serves, the dashboard included.
    assert.deepEqual((await manager.agentQuota('cc')).windows, quota?.windows, 'the dashboard serves the same paired in-band reading');
  } finally { await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('a reported reading is pushed to pages once, and a refresh right after it does not probe', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-quota-push-'));
  const store = new Store(dir);
  const adapter = new ClaudeAcpAdapter(fixture('acp'));
  let probes = 0;
  adapter.fetchQuota = async () => { probes++; return { windows: [{ label: '5H', usedPercent: 10 }], plan: { id: 'max', label: 'Max' }, checkedAt: new Date().toISOString() }; };
  const adapters = new Map([['cc', adapter] as const]);
  const manager = new ThreadManager(store, adapters as any, new Workdirs([dir], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir), () => [{ id: 'cc', adapter: 'claude' }]);
  const pushed: PlanQuota[] = [];
  const off = manager.subscribe(ev => { if (ev.type === 'quota.updated' && ev.data.agent === 'cc') pushed.push(ev.data.quota as PlanQuota); });
  try {
    // A probe first: its plan is kept when the agent later reports only its windows.
    await manager.agentQuota('cc', true);
    assert.equal(probes, 1);
    const thread = await manager.createThread({ agent: 'cc', cwd: dir });
    await manager.startTurn(thread.id, { text: 'rate-limit-tell' });
    await wait(() => manager.threadView(thread.id).status !== 'running');
    assert.deepEqual(pushed.map(q => q.windows.find(w => w.label === '5H')?.usedPercent), [10, 90], 'the probe and the report, each pushed once');
    assert.equal(pushed[1].plan?.label, 'Max');
    // The refresh button: the reading the agent just gave stands, no probe is spawned.
    const refreshed = await manager.agentQuota('cc', true);
    assert.equal(probes, 1);
    assert.equal(refreshed.windows.find(w => w.label === '5H')?.usedPercent, 90);
    assert.equal(pushed.length, 2, 'an unchanged reading is not pushed again');
  } finally { off(); await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});
