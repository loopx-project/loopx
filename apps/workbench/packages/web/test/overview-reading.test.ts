import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, type Api, type ThreadOutputs, type ThreadStats } from '../src/api.js';
import { createOverviewReading, overviewReadingFor, prepareOverview } from '../src/overview-reading.js';

const output: ThreadOutputs = { changes: { files: [], added: 0, removed: 0, total: 0 }, images: [], files: [{ path: 'notes.txt', turnId: 'turn', at: '' }], seq: 1 };
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test('cold opening deduplicates reads and a failed refresh retains valid output and stats', async () => {
  const request = deferred<ThreadOutputs>();
  let count = 0;
  let fail = false;
  const stats = { turns: 2 } as ThreadStats;
  const store = createOverviewReading({ outputs: () => { count++; return fail ? Promise.reject(new Error('offline')) : request.promise; }, stats: () => fail ? Promise.reject(new Error('offline')) : Promise.resolve(stats) }, 'thread');
  const first = store.refresh('1');
  assert.equal(store.refresh('1'), first);
  assert.equal(count, 1);
  assert.equal(store.getSnapshot().loading, true);
  assert.equal(store.getSnapshot().outputs, null);
  request.resolve(output);
  await first;
  assert.equal(store.getSnapshot().outputs, output);
  assert.equal(store.getSnapshot().stats, stats);
  assert.equal(store.stale('1'), false);
  fail = true;
  await store.refresh('2');
  assert.deepEqual(store.getSnapshot(), { outputs: output, stats, loading: false, failed: true });
});

test('cached opening is immediate and output is isolated by thread and API connection', async () => {
  const calls: string[] = [];
  const api = { outputs: (id: string) => { calls.push(id); return Promise.resolve(output); }, stats: () => Promise.resolve({ turns: 0 } as ThreadStats) } as unknown as Api;
  await prepareOverview(api, 'one', '1');
  await prepareOverview(api, 'one', '2');
  assert.deepEqual(calls, ['one']);
  assert.equal(overviewReadingFor(api, 'two').getSnapshot().outputs, null);
  assert.equal(overviewReadingFor({ ...api } as Api, 'one').getSnapshot().outputs, null);
  await prepareOverview(api, 'two', '1');
  assert.deepEqual(calls, ['one', 'two']);
});

test('a failed first read stays unknown, a retry can recover, and 404 is genuinely empty', async () => {
  let failure: Error | null = new Error('offline');
  const store = createOverviewReading({ outputs: () => failure ? Promise.reject(failure) : Promise.resolve(output), stats: () => Promise.reject(new Error('offline')) }, 'one');
  await store.refresh();
  assert.equal(store.getSnapshot().outputs, null);
  assert.equal(store.getSnapshot().failed, true);
  failure = null;
  await store.refresh();
  assert.equal(store.getSnapshot().outputs, output);
  assert.equal(store.getSnapshot().failed, false);
  failure = new ApiError('not found', {}, 404);
  await store.refresh();
  assert.deepEqual(store.getSnapshot().outputs?.files, []);
  assert.equal(store.getSnapshot().failed, false);
});


test('an update arriving during a slow read is fetched after it rather than silently dropped', async () => {
  const first = deferred<ThreadOutputs>();
  const second = deferred<ThreadOutputs>();
  let calls = 0;
  const store = createOverviewReading({ outputs: () => ++calls === 1 ? first.promise : second.promise, stats: () => Promise.resolve({ turns: 0 } as ThreadStats) }, 'thread');
  const read = store.refresh('first');
  store.refresh('latest');
  first.resolve(output);
  await read;
  assert.equal(calls, 2);
  second.resolve({ ...output, seq: 2 });
  await store.refresh('latest');
  assert.equal(store.getSnapshot().outputs?.seq, 2);
  assert.equal(store.stale('latest'), false);
});
