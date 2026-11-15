import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ExtensionManifest, ExtensionState } from '@calli/extension-api';
import { createExtensionVoice } from '../src/extension-voice.js';
import { ExtensionHost } from '../src/extension-host.js';
import { Store } from '../src/store.js';
import { HttpError } from '../src/errors.js';
import type { VoiceAccess, VoiceBinding, VoiceEvent } from '../src/voice-sessions.js';

const binding: VoiceBinding = { extensionId: 'voice', hostId: 'local', providerId: 'native-provider', threadId: 'chosen-thread', ownerId: 'browser-owner' };
const text = { zh: '测试', en: 'Test', ja: 'テスト' };
const manifest: ExtensionManifest = { id: 'voice', version: '1.0.0', apiVersion: 1, name: text, description: text, permissions: ['voice.control'], contributes: {} };
const flush = () => new Promise<void>(resolve => setImmediate(resolve));

function setup(catalog = [manifest]) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-extension-voice-'));
  const store = new Store(dir);
  const extensions = new ExtensionHost({
    store: store.extensions, catalog,
    source: { listThreads: () => [], threadView: () => { throw new Error('No thread'); }, subscribe: () => () => {} },
  });
  extensions.start();
  let starts = 0, stops = 0, disposals = 0;
  const runtime = createExtensionVoice({
    extensions,
    authorizeTarget: target => {
      if (target.hostId !== binding.hostId || target.threadId !== binding.threadId || target.providerId !== binding.providerId) {
        throw new HttpError(403, 'Target not allowed');
      }
    },
    create: () => ({ start: async () => { starts++; }, stop: async () => { stops++; }, dispose: () => { disposals++; } }),
  });
  const open = () => {
    const { id } = runtime.sessions.open(binding, 'sdp-offer');
    const access: VoiceAccess = { id, ownerId: binding.ownerId, extensionId: binding.extensionId };
    const events: VoiceEvent[] = [];
    runtime.sessions.subscribe(access, event => events.push(event));
    return { access, events };
  };
  return {
    runtime, extensions, store, open,
    counts: () => ({ starts, stops, disposals }),
    async close() { await runtime.dispose(); extensions.dispose(); store.close(); rmSync(dir, { recursive: true, force: true }); },
  };
}

for (const action of ['disable', 'uninstall'] as const) {
  test(`${action} closes host-owned voice even when the extension has no daemon implementation`, async t => {
    const h = setup(); t.after(() => h.close());
    h.extensions.install('voice', ['voice.control']);
    const { access, events } = h.open();
    await flush();
    assert.equal(h.counts().starts, 1);
    if (action === 'disable') h.extensions.setEnabled('voice', false);
    else h.extensions.uninstall('voice');
    assert.throws(() => h.runtime.sessions.heartbeat(access), (e: any) => e.status === 403);
    await h.runtime.sessions.revalidate();
    assert.deepEqual(h.counts(), { starts: 1, stops: 1, disposals: 1 });
    assert.equal((events.at(-1) as any).session.reason, 'revoked');
    assert.equal(h.extensions.list()[0].installed, action === 'disable');
  });
}

test('voice requires an installation and the exact voice permission grant before allocating resources', async t => {
  const h = setup(); t.after(() => h.close());
  assert.throws(() => h.open(), (e: any) => e.status === 403);
  h.store.extensions.install('voice', '0.9.0', []);
  assert.throws(() => h.open(), (e: any) => e.status === 403);
  h.extensions.install('voice', ['voice.control']);
  h.open(); await flush();
  assert.equal(h.counts().starts, 1);
});

test('an enabled extension without voice.control cannot borrow another extension grant', async t => {
  const h = setup([{ ...manifest, permissions: ['events.summary'] }]); t.after(() => h.close());
  h.extensions.install('voice', ['events.summary']);
  assert.equal(h.extensions.list()[0].enabled, true);
  assert.throws(() => h.open(), (e: any) => e.status === 403);
  assert.deepEqual(h.counts(), { starts: 0, stops: 0, disposals: 0 });
});

test('installation permission does not authorize another provider, machine or target thread', async t => {
  const h = setup(); t.after(() => h.close());
  h.extensions.install('voice', ['voice.control']);
  for (const change of [{ hostId: 'another-machine' }, { providerId: 'another-provider' }, { threadId: 'another-thread' }]) {
    assert.throws(() => h.runtime.sessions.open({ ...binding, ...change }, 'offer'), (e: any) => e.status === 403 && e.message === 'Target not allowed');
  }
  assert.deepEqual(h.counts(), { starts: 0, stops: 0, disposals: 0 });
});

test('external extensions remain excluded even if a caller supplies a forged voice grant', async () => {
  const state: ExtensionState = { manifest, source: 'external', installed: true, enabled: true, granted: ['voice.control'], installedAt: null, updatedAt: null };
  const runtime = createExtensionVoice({
    extensions: { list: () => [state], onChange: () => () => {} },
    authorizeTarget: () => {}, create: () => { throw new Error('Must not allocate a transport'); },
  });
  try { assert.throws(() => runtime.sessions.open(binding, 'offer'), (e: any) => e.status === 403); }
  finally { await runtime.dispose(); }
});

test('runtime shutdown releases its host subscription and rejects subsequent starts', async () => {
  let unsubscribed = 0;
  const runtime = createExtensionVoice({
    extensions: { list: () => [], onChange: () => () => { unsubscribed++; } },
    authorizeTarget: () => {}, create: () => { throw new Error('Must not allocate a transport'); },
  });
  await Promise.all([runtime.dispose(), runtime.dispose()]);
  assert.equal(unsubscribed, 1);
  assert.throws(() => runtime.sessions.open(binding, 'offer'), (e: any) => e.status === 503);
});

test('losing the verified provider revokes active voice through the extension host change hook', async t => {
  const h = setup([{ ...manifest, requires: { agentCapabilities: ['realtimeVoice'] } }]);
  t.after(() => h.close());
  h.extensions.reconcile([{ providerId: binding.providerId, available: true, capabilities: ['realtimeVoice'] }]);
  h.extensions.install('voice', ['voice.control']);
  const { access, events } = h.open(); await flush();
  h.extensions.reconcile([]);
  assert.throws(() => h.runtime.sessions.get(access), (e: any) => e.status === 403);
  await h.runtime.sessions.revalidate();
  assert.equal(h.counts().disposals, 1);
  assert.equal((events.at(-1) as any).session.reason, 'revoked');
  assert.equal(h.store.extensions.get('voice')?.enabled, true);
  assert.equal(h.extensions.list()[0].enabled, false);
  h.extensions.reconcile([{ providerId: binding.providerId, available: true, capabilities: ['realtimeVoice'] }]);
  assert.equal(h.extensions.list()[0].enabled, true);
  assert.equal(h.counts().starts, 1); // Recovering capabilities does not reopen a microphone/session.
});
