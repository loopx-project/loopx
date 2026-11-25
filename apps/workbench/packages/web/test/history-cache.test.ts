import { afterEach, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, type Api } from '../src/api';
import {
  cachedHistoryPage,
  cachedRouterThreadId,
  clearHistoryCache,
  forgetStoredHistoryPage,
  rememberHistoryPage,
  rememberRouterThreadId,
} from '../src/history-cache';
import { historyPage, peekCachedPage, watchHistory, type HistoryPage } from '../src/history';
import type { StoredEvent } from '../src/timeline';

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    get length() { return data.size; },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => { data.set(k, v); },
    removeItem: (k: string) => { data.delete(k); },
    clear: () => { data.clear(); },
  };
}

const makeEvent = (threadId: string, seq: number, text = `msg ${seq}`): StoredEvent => ({
  threadId,
  turnId: `turn-${seq}`,
  seq,
  type: 'turn.started',
  ts: '2026-11-24T10:00:00Z',
  data: { text },
});

const makePage = (threadId: string, seq: number, text?: string): HistoryPage => ({
  events: [makeEvent(threadId, seq, text)],
  snapshotSeq: seq,
  nextCursor: null,
  hasOlder: false,
});

const previousStorage = (globalThis as { localStorage?: Storage }).localStorage;

beforeEach(() => {
  (globalThis as { localStorage?: unknown }).localStorage = memoryStorage();
  clearHistoryCache();
});

afterEach(() => {
  clearHistoryCache();
  (globalThis as { localStorage?: unknown }).localStorage = previousStorage;
});

test('persists assistant router thread id per host and clears on null or sign-out', () => {
  assert.equal(cachedRouterThreadId(null), null);
  rememberRouterThreadId(null, 'assistant-local');
  rememberRouterThreadId('devbox', 'assistant-devbox');
  assert.equal(cachedRouterThreadId(null), 'assistant-local');
  assert.equal(cachedRouterThreadId('devbox'), 'assistant-devbox');

  rememberRouterThreadId(null, null);
  assert.equal(cachedRouterThreadId(null), null);
  assert.equal(cachedRouterThreadId('devbox'), 'assistant-devbox');

  clearHistoryCache();
  assert.equal(cachedRouterThreadId('devbox'), null);
});

test('pins assistant page while capping normal conversations to 8 recent entries', () => {
  rememberRouterThreadId(null, 'assistant-1');
  rememberHistoryPage(null, 'assistant-1', makePage('assistant-1', 1, 'hello assistant'), { pinned: true });

  for (let i = 1; i <= 10; i++) {
    rememberHistoryPage(null, `thread-${i}`, makePage(`thread-${i}`, i, `normal ${i}`));
  }

  // Assistant pinned slot survives even after 10 normal conversations are cached.
  assert.equal(cachedHistoryPage(null, 'assistant-1')?.snapshotSeq, 1);
  // The 8 newest normal conversations remain; the oldest 2 are evicted.
  assert.equal(cachedHistoryPage(null, 'thread-10')?.snapshotSeq, 10);
  assert.equal(cachedHistoryPage(null, 'thread-3')?.snapshotSeq, 3);
  assert.equal(cachedHistoryPage(null, 'thread-1'), null);
  assert.equal(cachedHistoryPage(null, 'thread-2'), null);
});

test('never persists empty pages, credentials, or bulky inline image data', () => {
  localStorage.setItem('calli.token', 'secret-hub-token-123456');

  rememberHistoryPage(null, 'th-empty', { events: [], snapshotSeq: 0, nextCursor: null, hasOlder: false });
  assert.equal(cachedHistoryPage(null, 'th-empty'), null);

  rememberHistoryPage(null, 'th-secret', makePage('th-secret', 1, 'leak secret-hub-token-123456 here'));
  assert.equal(cachedHistoryPage(null, 'th-secret'), null);

  const bigImage = 'data:image/png;base64,' + 'A'.repeat(4096);
  const pageWithImage: HistoryPage = {
    events: [{
      threadId: 'th-img',
      turnId: 't1',
      seq: 1,
      type: 'turn.started',
      ts: '2026-11-24T10:00:00Z',
      data: { text: 'look', preview: bigImage },
    }],
    snapshotSeq: 1,
    nextCursor: null,
    hasOlder: false,
  };
  rememberHistoryPage(null, 'th-img', pageWithImage);
  const stored = cachedHistoryPage(null, 'th-img');
  assert.ok(stored);
  assert.equal(stored.events[0].data.preview, undefined);
});

test('peekCachedPage separates tab memory from persisted storage and watchHistory revalidates storage hits', async () => {
  const id = 'th-swr';
  const stale = makePage(id, 1, 'cached turn');
  rememberHistoryPage(null, id, stale);

  // Fresh Api instance (simulating F5 reload): memory is empty, storage hits synchronously.
  let fetchCalls = 0;
  const freshApi = {
    demo: false,
    host: null,
    historyPage: async (): Promise<HistoryPage> => {
      fetchCalls++;
      return makePage(id, 2, 'live turn');
    },
    stream: async (_path: string, _push: (ev: StoredEvent) => void, signal: AbortSignal) => {
      await new Promise<void>((resolve) => {
        if (signal.aborted) return resolve();
        signal.addEventListener('abort', () => resolve(), { once: true });
      });
    },
  } as unknown as Api;

  const peek = peekCachedPage(freshApi, id);
  assert.equal(peek?.source, 'storage');
  assert.equal(peek?.page.snapshotSeq, 1);

  const seenSeqs: number[] = [];
  const ctrl = new AbortController();
  const watch = watchHistory(freshApi, id, ctrl.signal, {
    page: (p) => {
      seenSeqs.push(p.snapshotSeq);
      if (p.snapshotSeq === 2) ctrl.abort();
    },
    events: () => {},
    error: () => {},
  });
  await watch;

  assert.equal(fetchCalls, 1);
  assert.deepEqual(seenSeqs, [1, 2]);
  // After revalidation, memory tier is now warm too.
  assert.equal(peekCachedPage(freshApi, id)?.source, 'memory');
  assert.equal(cachedHistoryPage(null, id)?.snapshotSeq, 2);
});

test('watchHistory evicts stale storage entry on 404', async () => {
  const id = 'th-deleted';
  rememberHistoryPage(null, id, makePage(id, 1, 'gone'));
  assert.ok(cachedHistoryPage(null, id));

  const api = {
    demo: false,
    host: null,
    historyPage: async (): Promise<HistoryPage> => {
      throw new ApiError('thread not found', {}, 404);
    },
  } as unknown as Api;

  const ctrl = new AbortController();
  await watchHistory(api, id, ctrl.signal, {
    page: () => {},
    events: () => {},
    error: () => { ctrl.abort(); },
  });

  assert.equal(cachedHistoryPage(null, id), null);
});

test('forgetStoredHistoryPage isolates hosts and removes both chain variants', async () => {
  const apiLocal = { demo: false, host: null, historyPage: async () => makePage('same-id', 1) } as unknown as Api;
  const apiRemote = { demo: false, host: 'mac', historyPage: async () => makePage('same-id', 9) } as unknown as Api;
  await historyPage(apiLocal, 'same-id', undefined, undefined, false);
  await historyPage(apiLocal, 'same-id', undefined, undefined, true);
  await historyPage(apiRemote, 'same-id', undefined, undefined, false);

  forgetStoredHistoryPage(null, 'same-id');
  assert.equal(cachedHistoryPage(null, 'same-id', false), null);
  assert.equal(cachedHistoryPage(null, 'same-id', true), null);
  assert.equal(cachedHistoryPage('mac', 'same-id', false)?.snapshotSeq, 9);
});
