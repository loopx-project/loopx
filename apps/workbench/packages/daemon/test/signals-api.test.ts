import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';

test('the signals API lists lightweight projections and reads bodies only by explicit key', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-signals-api-'));
  const store = new Store(dir);
  const manager = new ThreadManager(store, new Map(), [dir], new AttachmentStore(dir));
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, token = 'test-token') => fetch(base + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  try {
    store.createTodo('m1', '事项 one', null, {});
    store.insertSignal('lark-calendar:evt1', { summary: '评审会已结束', source: { type: 'lark-calendar', ref: 'evt1' }, meeting: { event: 'ended', meetingId: 'mtg1' } },
      { outcome: 'delivered', matterIds: ['m1'], deliveries: [{ matterId: 'm1', threadId: 'worker1' }] }, '2026-11-01T02:00:00.000Z');
    store.insertSignal('lark-message:noise', { summary: '今天天气不错', source: { type: 'lark-message', ref: 'noise' } },
      { outcome: 'filtered', reason: 'not-a-request' }, '2026-11-01T02:01:00.000Z');

    assert.equal((await api('/api/personal/signals', '')).status, 401, 'the API stays behind the bearer token');
    const list = await (await api('/api/personal/signals?matterId=m1')).json();
    assert.equal(list.signals.length, 1);
    const signal = list.signals[0];
    assert.equal(signal.key, 'lark-calendar:evt1'); assert.equal(signal.summary, '评审会已结束');
    assert.deepEqual(signal.meeting, { event: 'ended', meetingId: 'mtg1' }); assert.equal(signal.source.type, 'lark-calendar');
    assert.equal(signal.disposition.outcome, 'delivered'); assert.equal(signal.payload, undefined); assert.equal(signal.material, undefined);
    assert.equal((await (await api('/api/personal/signals?matterId=absent')).json()).signals.length, 0);

    const material = await (await api(`/api/personal/signals/${encodeURIComponent('lark-calendar:evt1')}/material`)).json();
    assert.equal(material.key, 'lark-calendar:evt1'); assert.equal(material.material.error, 'lark_disabled'); assert.equal(material.content, null);
    assert.equal((await api(`/api/personal/signals/${encodeURIComponent('lark-message:noise')}/material`)).status, 404);
    assert.equal((await api(`/api/personal/signals/${encodeURIComponent('lark-calendar:absent')}/material`)).status, 404);
    assert.equal((await api('/api/personal/signals/%zz/material')).status, 400);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
    await manager.shutdown();
    store.close(); rmSync(dir, { recursive: true, force: true });
  }
});
