import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import type { ExtensionManifest } from '@calli/extension-api';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { ExtensionHost } from '../src/extension-host.js';
import { createHttpServer } from '../src/http.js';
import { HttpError } from '../src/errors.js';
import { VoiceService, type VoiceProviderAdapter } from '../src/voice-service.js';
import type { VoiceSignal } from '../src/voice-sessions.js';

const owner = 'browser-aaaaaaaaaaaaaaaaaaaa';
const anotherOwner = 'browser-bbbbbbbbbbbbbbbbbbbb';
const manifest: ExtensionManifest = {
  id: 'voice', version: '0.1.0', apiVersion: 1,
  name: { zh: '语音', en: 'Voice', ja: '音声' }, description: { zh: '语音', en: 'Voice', ja: '音声' },
  permissions: ['voice.control'], requires: { agentCapabilities: ['realtimeVoice'] }, contributes: {},
};

async function fixture(t: TestContext, supported = true) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-voice-http-'));
  const store = new Store(dir);
  const manager = new ThreadManager(store, new Map(), [dir], new AttachmentStore(dir));
  const extensions = new ExtensionHost({ store: store.extensions, source: manager, catalog: [manifest] });
  extensions.start();
  let probes = 0;
  const adapters = new Map<string, VoiceProviderAdapter>([['provider', {
    available: async () => true,
    realtimeSupport: async () => { probes++; return { supported }; },
  }]]);
  const transports: Array<{ emit: (e: VoiceSignal) => void; stops: number; texts: string[]; disposed: Promise<void> }> = [];
  const voice = new VoiceService({ adapters, extensions, manager: {
    voiceProvider(threadId) {
      if (threadId !== 'thread') throw new HttpError(404, 'thread_not_found');
      return { providerId: 'provider' };
    },
    voiceTransport() {
      let done!: () => void;
      const transport = { emit: (_e: VoiceSignal) => {}, stops: 0, texts: [] as string[], disposed: new Promise<void>(resolve => { done = resolve; }) };
      transports.push(transport);
      return {
        async start(_offer, emit) {
          transport.emit = emit;
          emit({ type: 'started' });
          emit({ type: 'sdp', sdp: 'PRIVATE_ANSWER' });
        },
        async appendText(text) { transport.texts.push(text); },
        async stop() { transport.stops++; },
        dispose: done,
      };
    },
  } });
  const server = createHttpServer({ manager, extensions, voice, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const request = (path: string, method = 'GET', body?: unknown, ownerId = owner, auth = 'test-token') => fetch(base + '/api/' + path, {
    method, headers: { authorization: `Bearer ${auth}`, 'content-type': 'application/json', 'x-calli-voice-owner': ownerId },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const install = () => request('extensions/voice/install', 'POST', { granted: ['voice.control'] });
  const open = () => request('voice', 'POST', { extensionId: 'voice', threadId: 'thread', offer: 'PRIVATE_OFFER' });
  t.after(async () => {
    await voice.dispose();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    extensions.dispose();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });
  return { voice, extensions, adapters, store, base, request, install, open, transports, probes: () => probes };
}

test('voice HTTP authenticates before probing and blocks install without verified support', async t => {
  const h = await fixture(t, false);
  assert.equal((await h.request('voice/providers', 'GET', undefined, owner, 'wrong')).status, 401);
  assert.equal(h.probes(), 0);
  assert.equal((await h.install()).status, 400);
  assert.equal((await h.open()).status, 403);
  assert.equal(h.transports.length, 0);
});

test('voice HTTP requires explicit installation, owner header and an existing bound thread', async t => {
  const h = await fixture(t);
  assert.equal((await h.open()).status, 403);
  assert.equal(h.probes(), 0);
  assert.equal((await h.install()).status, 200);
  assert.equal((await h.request('voice', 'POST', { extensionId: 'voice', threadId: 'thread', offer: 'sdp', ownerId: owner }, '')).status, 400);
  assert.equal((await h.request('voice', 'POST', { extensionId: 'voice', threadId: 'other', offer: 'sdp' })).status, 404);
  assert.equal((await h.request('voice', 'POST', { extensionId: 'other', threadId: 'thread', offer: 'sdp' })).status, 400);
  assert.equal((await h.request('voice', 'POST', { extensionId: 'voice', threadId: 'thread', offer: 'x'.repeat(65537) })).status, 400);
  assert.equal(h.transports.length, 0);
});

test('a page loaded before the rename to Calli still names its owner in the old header', async t => {
  const h = await fixture(t);
  await h.install();
  const opened = await fetch(h.base + '/api/voice', {
    method: 'POST', headers: { authorization: 'Bearer test-token', 'content-type': 'application/json', 'x-rah-voice-owner': owner },
    body: JSON.stringify({ extensionId: 'voice', threadId: 'thread', offer: 'PRIVATE_OFFER' }),
  });
  assert.equal(opened.status, 201);
  const session = await opened.json();
  assert.equal((await h.request(`voice/${session.id}`, 'DELETE')).status, 204, 'the same owner under the new header');
});

test('voice HTTP owner isolation, negotiation, heartbeat and idempotent stop', async t => {
  const h = await fixture(t);
  await h.install();
  const opened = await h.open();
  assert.equal(opened.status, 201);
  const session = await opened.json();
  assert.equal(session.state, 'connecting');
  assert.ok(!JSON.stringify(session).includes('PRIVATE'));
  assert.equal((await h.open()).status, 409);
  for (const [suffix, method] of [['events', 'GET'], ['heartbeat', 'POST'], ['connected', 'POST'], ['', 'DELETE']]) {
    assert.equal((await h.request(`voice/${session.id}${suffix ? '/' + suffix : ''}`, method, undefined, anotherOwner)).status, 404);
  }
  const connected = await h.request(`voice/${session.id}/connected`, 'POST');
  assert.equal((await connected.json()).state, 'connected');
  assert.equal((await h.request(`voice/${session.id}/heartbeat`, 'POST')).status, 200);
  assert.equal((await h.request(`voice/${session.id}`, 'DELETE')).status, 204);
  assert.equal((await h.request(`voice/${session.id}`, 'DELETE')).status, 204);
  assert.equal(h.transports[0].stops, 1);
  assert.equal((await h.request(`voice/${session.id}/events`)).status, 404);
});

test('voice SSE is private, replays only SDP, and disconnect stops its native lease', async t => {
  const h = await fixture(t);
  await h.install();
  const session = await (await h.open()).json();
  h.transports[0].emit({ type: 'transcript', itemId: 'before', text: 'old transcript', final: true });
  const stream = await h.request(`voice/${session.id}/events`);
  assert.equal(stream.status, 200);
  assert.equal(stream.headers.get('cache-control'), 'no-store');
  const reader = stream.body!.getReader();
  const first = new TextDecoder().decode((await reader.read()).value);
  assert.match(first, /PRIVATE_ANSWER/);
  assert.doesNotMatch(first, /old transcript|PRIVATE_OFFER/);
  await reader.cancel();
  await h.transports[0].disposed;
  assert.equal(h.transports[0].stops, 1);
});

test('disabling the extension closes its active SSE and does not resume media when reenabled', async t => {
  const h = await fixture(t);
  await h.install();
  const session = await (await h.open()).json();
  const stream = await h.request(`voice/${session.id}/events`);
  const reading = stream.text();
  assert.equal((await h.request('extensions/voice', 'PATCH', { enabled: false })).status, 200);
  const events = await reading;
  assert.match(events, /"reason":"revoked"/);
  assert.equal(h.transports[0].stops, 1);
  assert.equal((await h.request('extensions/voice', 'PATCH', { enabled: true })).status, 200);
  assert.equal(h.transports.length, 1);
});

test('provider removal invalidates dependencies and shuts down existing voice', async t => {
  const h = await fixture(t);
  await h.install();
  await h.open();
  h.adapters.clear();
  assert.deepEqual(await h.voice.refresh(), []);
  await h.transports[0].disposed;
  const state = h.extensions.list()[0];
  assert.equal(state.installed, true);
  assert.equal(state.enabled, false);
  assert.equal(state.requirements?.state, 'missing');
  assert.equal((await h.open()).status, 403);
});

test('provider probes are deduplicated and never expose native error details', async t => {
  const h = await fixture(t);
  h.adapters.set('provider', { available: async () => { throw new Error('SECRET_TOKEN private account'); }, realtimeSupport: async () => ({ supported: true }) });
  const [a, b] = await Promise.all([h.request('voice/providers'), h.request('voice/providers')]);
  const result = await a.json();
  assert.deepEqual(result, await b.json());
  assert.equal(result.providers[0].reason, 'probe_failed');
  assert.ok(!JSON.stringify(result).includes('SECRET'));
  assert.equal((await h.install()).status, 400);
  assert.equal(h.extensions.list()[0].requirements?.state, 'error');
});

test('a stalled probe does not delay disabling the extension and reclaiming media', async t => {
  const h = await fixture(t);
  await h.install();
  await h.open();
  let resolve!: (value: { supported: boolean }) => void;
  const probe = new Promise<{ supported: boolean }>(done => { resolve = done; });
  h.adapters.set('provider', { available: async () => true, realtimeSupport: () => probe });
  const refresh = h.voice.refresh(true);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const response = await Promise.race([
      h.request('extensions/voice', 'PATCH', { enabled: false }),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('Disable waited for a probe')), 2000); }),
    ]);
    assert.equal(response.status, 200);
    await h.transports[0].disposed;
    assert.equal((await h.open()).status, 403);
  } finally { clearTimeout(timer); resolve({ supported: true }); await refresh; }
  assert.equal(h.transports[0].stops, 1);
  assert.equal(h.extensions.list()[0].enabled, false);
});

test('late capability probes cannot restore a provider removed during discovery', async t => {
  const h = await fixture(t);
  let resolve!: (value: { supported: boolean }) => void;
  let started!: () => void;
  const probing = new Promise<void>(done => { started = done; });
  h.adapters.set('provider', {
    available: async () => true,
    realtimeSupport: () => new Promise(done => { resolve = done; started(); }),
  });
  const pending = h.voice.refresh(true);
  await probing;
  h.adapters.clear();
  resolve({ supported: true });
  assert.deepEqual(await pending, []);
  assert.equal(h.extensions.list()[0].requirements?.state, 'missing');
});

test('replacing a provider account under the same id closes its old lease without reopening', async t => {
  const h = await fixture(t);
  await h.install();
  await h.open();
  h.adapters.set('provider', { available: async () => true, realtimeSupport: async () => ({ supported: true }) });
  assert.equal((await h.voice.refresh())[0].supported, true);
  await h.transports[0].disposed;
  assert.equal(h.transports[0].stops, 1);
  assert.equal(h.transports.length, 1);
  assert.equal(h.extensions.list()[0].enabled, true);
});

test('missing account login is distinct from missing protocol support and never starts media', async t => {
  const h = await fixture(t);
  const provider = h.adapters.get('provider')!;
  provider.realtimeSupport = async () => ({ supported: true, login: 'required' });
  const installed = await h.install();
  assert.equal(installed.status, 200);
  assert.equal((await installed.json()).requirements.state, 'satisfied');
  const response = await h.open();
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, 'voice_login_required');
  assert.equal(h.transports.length, 0);
  provider.realtimeSupport = async () => ({ supported: true, login: 'ready' });
  assert.equal((await h.open()).status, 201);
});


test('native text endpoint checks owner, connection and bounded nonempty text', async t => {
  const h = await fixture(t); await h.install();
  const { id } = await (await h.open()).json();
  const path = `voice/${id}/text`;
  assert.equal((await h.request(path, 'POST', { text: 'hello' }, anotherOwner)).status, 404);
  assert.equal((await h.request(path, 'POST', { text: 'hello' })).status, 409);
  await h.request(`voice/${id}/connected`, 'POST');
  for (const text of ['', ' ', 'x'.repeat(32769), 1]) assert.equal((await h.request(path, 'POST', { text })).status, 400);
  assert.equal((await h.request(path, 'POST', { text: ' hello ' })).status, 200);
  assert.deepEqual(h.transports[0].texts, ['hello']);
  await h.request(`voice/${id}`, 'DELETE');
  assert.equal((await h.request(path, 'POST', { text: 'late' })).status, 404);
});


test('transcript expansion stays thread-scoped and is absent from initial history', async t => {
  const h = await fixture(t);
  for (const id of ['one', 'two']) h.store.createThread({ id, agent: 'provider', cwd: '/tmp', title: id,
    agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  h.store.appendEvent('one', null, 'voice.message', { sessionId: 'call', itemId: 'call:u', role: 'user', text: 'private speech', final: true });
  h.store.appendEvent('one', null, 'voice.closed', { sessionId: 'call', startSeq: 1, endSeq: 1, count: 1 });
  const initial = await (await h.request('threads/one/history')).json();
  assert.ok(!JSON.stringify(initial).includes('private speech'));
  const expanded = await h.request('threads/one/history/voice?session=call');
  assert.equal(expanded.status, 200);
  assert.equal((await expanded.json()).events[0].data.text, 'private speech');
  assert.equal((await h.request('threads/two/history/voice?session=call')).status, 404);
  assert.equal((await h.request('threads/one/history/voice?session=call&cursor=-1')).status, 400);
});
