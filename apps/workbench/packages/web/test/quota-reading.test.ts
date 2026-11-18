import test from 'node:test';
import assert from 'node:assert/strict';
import type { Api, PlanQuota } from '../src/api.js';
import { createQuotaReading, quotaPushed } from '../src/quota-reading.js';

const quota = (usedPercent = 42): PlanQuota => ({ windows: [{ label: '5H', usedPercent }], checkedAt: new Date().toISOString() });

test('chat and page subscribers share the request and both observe a forced refresh', async () => {
  let cached: PlanQuota | null = null;
  let resolve!: (value: PlanQuota) => void;
  const calls: boolean[] = [];
  const api = {
    cachedAgentQuota: () => cached,
    agentQuota: (_agent: string, force: boolean) => {
      calls.push(force);
      return new Promise<PlanQuota>(done => { resolve = value => { cached = value; done(value); }; });
    },
  } as unknown as Api;
  const store = createQuotaReading(api, 'one');
  const chat: Array<number | undefined> = [], page: Array<number | undefined> = [];
  const stopChat = store.subscribe(() => chat.push(store.getSnapshot().quota?.windows[0].usedPercent));
  const stopPage = store.subscribe(() => page.push(store.getSnapshot().quota?.windows[0].usedPercent));
  try {
    assert.deepEqual(calls, [false]);
    const first = store.refresh();
    resolve(quota()); await first;
    assert.equal(chat.at(-1), 42); assert.equal(page.at(-1), 42);
    const refresh = store.refresh();
    assert.deepEqual(calls, [false, true]);
    assert.equal(store.refresh(), refresh, 'a second surface joins the pending read');
    resolve(quota(88)); await refresh;
    assert.equal(chat.at(-1), 88); assert.equal(page.at(-1), 88);
  } finally { stopChat(); stopPage(); }
});

test('failed refresh retains the old value and success clears the error; empty differs from no reading', async () => {
  let cached: PlanQuota | null = quota();
  let fail = true;
  const api = {
    cachedAgentQuota: () => cached,
    agentQuota: async () => { if (fail) throw new Error('offline'); return cached; },
  } as unknown as Api;
  const store = createQuotaReading(api, 'one');
  await store.refresh();
  assert.equal(store.getSnapshot().quota, cached); assert.equal(store.getSnapshot().error, 'offline'); assert.ok(store.getSnapshot().failedAt);
  cached = { windows: [], checkedAt: new Date().toISOString() }; fail = false;
  await store.refresh();
  assert.deepEqual(store.getSnapshot().quota?.windows, []); assert.equal(store.getSnapshot().error, null); assert.equal(store.getSnapshot().failedAt, null);
  cached = null; fail = true;
  await store.refresh();
  assert.equal(store.getSnapshot().quota, null); assert.equal(store.getSnapshot().loading, false); assert.equal(store.getSnapshot().error, 'offline');
});

test('account snapshots retain their own data when another machine is read', async () => {
  const first = quota(5), second = quota(95);
  const api = (value: PlanQuota) => ({ cachedAgentQuota: () => null, agentQuota: async () => value }) as unknown as Api;
  const local = createQuotaReading(api(first), 'same-id');
  const remote = createQuotaReading(api(second), 'same-id');
  await Promise.all([local.refresh(), remote.refresh()]);
  assert.equal(local.getSnapshot().quota, first); assert.equal(remote.getSnapshot().quota, second);
});

test('a reading the hub pushes reaches the surfaces showing that account without a request', async () => {
  let requests = 0;
  const remembered: PlanQuota[] = [];
  const api = {
    cachedAgentQuota: () => null,
    agentQuota: async () => { requests++; return quota(); },
    rememberAgentQuota: (_agent: string, value: PlanQuota) => remembered.push(value),
  } as unknown as Api;
  const store = createQuotaReading(api, 'one');
  const seen: Array<number | undefined> = [];
  const stop = store.subscribe(() => seen.push(store.getSnapshot().quota?.windows[0].usedPercent));
  try {
    await store.refresh();
    const asked = requests;
    store.pushed(quota(63));
    assert.equal(seen.at(-1), 63);
    assert.equal(store.getSnapshot().error, null);
    assert.equal(requests, asked, 'a push is not followed by a request');
    quotaPushed(api, 'one', quota(70));
    assert.equal(remembered.at(-1)?.windows[0].usedPercent, 70, 'kept for the next page load too');
  } finally { stop(); }
});

test('a request answered after a push does not put an older reading back', async () => {
  let resolve!: (value: PlanQuota) => void;
  const api = {
    cachedAgentQuota: () => null,
    agentQuota: () => new Promise<PlanQuota>(done => { resolve = done; }),
  } as unknown as Api;
  const store = createQuotaReading(api, 'one');
  const refresh = store.refresh();
  store.pushed(quota(90));
  resolve(quota(40)); await refresh;
  assert.equal(store.getSnapshot().quota?.windows[0].usedPercent, 90);
  assert.equal(store.getSnapshot().loading, false);
});
