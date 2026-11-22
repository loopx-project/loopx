import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Api } from '../src/api';
import { entryScriptIn } from '../src/version';
import type { StoredEvent } from '../src/timeline';

const sse = (ev: Partial<StoredEvent>) => new TextEncoder().encode(`id: ${ev.seq}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`);

/** Replaces fetch with one stream the test feeds by hand. */
function fakeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    start: (c) => void (controller = c),
    cancel: () => void (cancelled = true),
  });
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(body, { status: 200 })) as typeof fetch;
  return { push: (bytes: Uint8Array) => controller.enqueue(bytes), wasCancelled: () => cancelled, restore: () => void (globalThis.fetch = realFetch) };
}

test('a stream that goes silent is given up on, so the caller can reconnect', async () => {
  const fake = fakeStream();
  try {
    const seen: number[] = [];
    const done = new Api('t').stream('/api/stream', (ev) => seen.push(ev.seq), new AbortController().signal, 80);
    fake.push(sse({ seq: 1, type: 'thread.updated' }));
    // Nothing more comes, and the stream never ends: a phone that slept leaves exactly this.
    await done;
    assert.deepEqual(seen, [1]);
    assert.equal(fake.wasCancelled(), true);
  } finally {
    fake.restore();
  }
});

test('a stream that keeps talking, pings included, is left alone', async () => {
  const fake = fakeStream();
  try {
    const seen: number[] = [];
    let ended = false;
    const done = new Api('t').stream('/api/stream', (ev) => seen.push(ev.seq), new AbortController().signal, 120).then(() => void (ended = true));
    for (let i = 0; i < 5; i++) {
      await new Promise((r) => setTimeout(r, 60));
      fake.push(i % 2 ? sse({ seq: i, type: 'thread.updated' }) : new TextEncoder().encode(': ping\n\n'));
    }
    assert.equal(ended, false, 'five writes 60 ms apart never leave it silent for 120 ms');
    assert.deepEqual(seen, [1, 3]);
    await done; // and it is given up on once they stop
  } finally {
    fake.restore();
  }
});

test('entryScriptIn reads the script a built index.html loads', () => {
  const html = '<!doctype html><html><head><script type="module" crossorigin src="/assets/index-AbC_12-x.js"></script><link rel="stylesheet" href="/assets/index-Zz.css"></head></html>';
  assert.equal(entryScriptIn(html), '/assets/index-AbC_12-x.js');
  assert.equal(entryScriptIn('<html><body>no scripts</body></html>'), null);
});
