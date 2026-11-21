import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { Api, saveToken, type PlanQuota } from '../src/api.js';

const quota = (usedPercent = 42): PlanQuota => ({ windows: [{ label: '5H', usedPercent }], checkedAt: '2026-10-15T10:00:00Z' });

function storage(t: TestContext) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const data = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    removeItem: (key: string) => { data.delete(key); },
  } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  });
  saveToken('test-token');
  return data;
}

test('quota survives a new API client and failed refresh, while overlapping requests share one read', async t => {
  storage(t);
  const api = new Api('test-token');
  let resolve!: (response: Response) => void;
  const fetch = t.mock.method(globalThis, 'fetch', () => new Promise<Response>(done => { resolve = done; }));
  assert.equal(api.cachedAgentQuota('one'), null);
  const first = api.agentQuota('one');
  assert.equal(api.agentQuota('one', true), first);
  assert.equal(fetch.mock.callCount(), 1);
  resolve(Response.json(quota()));
  await first;
  const reopened = new Api('test-token');
  assert.deepEqual(reopened.cachedAgentQuota('one'), quota());
  const refresh = reopened.agentQuota('one', true);
  assert.deepEqual(reopened.cachedAgentQuota('one'), quota(), 'old value stays available while refreshing');
  resolve(Response.json({ error: 'unavailable' }, { status: 503 }));
  await assert.rejects(refresh, /unavailable/);
  assert.deepEqual(reopened.cachedAgentQuota('one'), quota());
  const retry = reopened.agentQuota('one', true);
  resolve(Response.json(quota(56)));
  await retry;
  assert.deepEqual(new Api('test-token').cachedAgentQuota('one'), quota(56));
});

test('quota cache separates hubs, machines and instances and clears on authentication changes', async t => {
  storage(t);
  t.mock.method(globalThis, 'fetch', async () => Response.json(quota()));
  await new Api('test-token').agentQuota('one');
  assert.equal(new Api('test-token').cachedAgentQuota('two'), null);
  assert.equal(new Api('test-token', '', 'remote').cachedAgentQuota('one'), null);
  assert.equal(new Api('test-token', 'https://other.example').cachedAgentQuota('one'), null);
  assert.equal(new Api('different-token').cachedAgentQuota('one'), null);
  saveToken('different-token');
  assert.equal(new Api('different-token').cachedAgentQuota('one'), null);
  await new Api('different-token').agentQuota('one');
  saveToken('');
  saveToken('different-token');
  assert.equal(new Api('different-token').cachedAgentQuota('one'), null);
});

test('a late quota response after sign-out cannot refill persistent storage', async t => {
  storage(t);
  let resolve!: (response: Response) => void;
  t.mock.method(globalThis, 'fetch', () => new Promise<Response>(done => { resolve = done; }));
  const pending = new Api('test-token').agentQuota('one');
  saveToken('');
  resolve(Response.json(quota()));
  await pending;
  saveToken('test-token');
  assert.equal(new Api('test-token').cachedAgentQuota('one'), null);
});

test('invalid saved quota is ignored and blocked storage still allows in-memory reuse', async t => {
  const data = storage(t);
  for (const value of ['not json', '[]', JSON.stringify({ '/api/agents/one/quota': { checkedAt: 'invalid', windows: [] } }),
    JSON.stringify({ '/api/agents/one/quota': { ...quota(), windows: [{ label: '5H', usedPercent: '42' }] } })]) {
    data.set('calli.quota.v1', value);
    assert.equal(new Api('test-token').cachedAgentQuota('one'), null);
  }
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get: () => { throw new Error('disabled'); } });
  t.mock.method(globalThis, 'fetch', async () => Response.json(quota()));
  const api = new Api('test-token');
  await api.agentQuota('one');
  assert.deepEqual(api.cachedAgentQuota('one'), quota());
});

test('a stored reading keeps its balances, and a malformed balance discards the reading', async t => {
  const data = storage(t);
  const { cachedQuota, rememberQuota } = await import('../src/quota-cache.js');
  const reading: PlanQuota = { ...quota(), balances: [{ scope: 'DeepSeek', amount: '¥60.52' }] };
  rememberQuota('/api/agents/one/quota', reading);
  assert.deepEqual(cachedQuota('/api/agents/one/quota'), reading);
  const [key, value] = [...data].find(([k]) => k === 'calli.quota.v1')!;
  data.set(key, value.replace('"amount":"¥60.52"', '"amount":60.52'));
  assert.equal(cachedQuota('/api/agents/one/quota'), null);
});
