import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexRealtime, CodexVoiceTransport, peerTransport } from '../src/adapters/codex-realtime.js';
import { JsonRpcPeer } from '../src/adapters/jsonrpc.js';
import { VoiceSessions, type VoiceEvent } from '../src/voice-sessions.js';

const flush = () => new Promise<void>(resolve => setImmediate(resolve));
const timeouts = { requestTimeoutMs: 3000, establishTimeoutMs: 3000, closeTimeoutMs: 10 };

test('early started and SDP notifications cannot conceal a rejected start RPC', async () => {
  let rejectStart!: (error: Error) => void;
  let rt!: CodexRealtime;
  rt = new CodexRealtime('thread', {
    isClosed: false,
    request(method) {
      if (method === 'account/read') return Promise.resolve({ account: { type: 'chatgpt' } });
      if (method === 'thread/realtime/start') return new Promise((_resolve, reject) => { rejectStart = reject; });
      if (method === 'thread/realtime/stop') rt.handleNotification('thread/realtime/closed', { threadId: 'thread' });
      return Promise.resolve({});
    },
  }, {}, timeouts);
  const start = rt.start({ sdpOffer: 'offer' });
  const failed = assert.rejects(start, /unsupported/);
  await flush();
  rt.handleNotification('thread/realtime/started', { threadId: 'thread' });
  rt.handleNotification('thread/realtime/sdp', { threadId: 'thread', sdp: 'answer' });
  rejectStart(new Error('unsupported request SECRET_NATIVE_DATA'));
  try { await failed; }
  finally { await rt.stop(); }
});

test('a terminated controller cannot reuse a native thread with untagged late notifications', async () => {
  let requests = 0;
  let rt!: CodexRealtime;
  rt = new CodexRealtime('thread', {
    isClosed: false,
    async request(method) {
      requests++;
      if (method === 'account/read') return { account: { type: 'chatgpt' } };
      if (method === 'thread/realtime/start') {
        rt.handleNotification('thread/realtime/started', { threadId: 'thread' });
        rt.handleNotification('thread/realtime/sdp', { threadId: 'thread', sdp: 'answer' });
      }
      if (method === 'thread/realtime/stop') rt.handleNotification('thread/realtime/closed', { threadId: 'thread' });
      return {};
    },
  }, {}, timeouts);
  await rt.start({ sdpOffer: 'first-offer' });
  await rt.stop();
  const before = requests;
  try {
    await assert.rejects(rt.start({ sdpOffer: 'second-offer' }));
    assert.equal(requests, before);
  } finally { await rt.stop(); }
});

test('a state subscriber may stop synchronously without leaving the start promise pending', async () => {
  const requests: string[] = [];
  let rt!: CodexRealtime;
  rt = new CodexRealtime('thread', {
    isClosed: false,
    async request(method) { requests.push(method); return { account: { type: 'chatgpt' } }; },
  }, {
    onState(state) { if (state.phase === 'starting') void rt.stop(); },
  }, timeouts);
  let outcome = 'pending';
  void rt.start({ sdpOffer: 'offer' }).then(() => { outcome = 'resolved'; }, error => { outcome = error.message; });
  await flush();
  assert.equal(outcome, 'stopped');
  assert.ok(!requests.includes('thread/realtime/start'));
});

for (const scenario of ['ok', 'async-error-early']) {
  test(`native transport and voice leases: ${scenario} preserves the shared peer`, async t => {
    const directory = await mkdtemp(join(tmpdir(), 'calli-voice-integration-'));
    const log = join(directory, 'requests.jsonl');
    const child = spawn(process.execPath, [fileURLToPath(new URL('./fixtures/fake-codex-realtime.mjs', import.meta.url))], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, FAKE_REALTIME_SCENARIO: scenario, FAKE_REALTIME_LOG: log },
    });
    const exited = new Promise<void>(resolve => child.once('close', () => resolve()));
    let native: CodexVoiceTransport | undefined;
    const peer = new JsonRpcPeer(child, {
      onNotification: (method, params) => native?.handleNotification(method, params as Record<string, unknown>),
      onClosed: () => native?.handleClosed(),
      onRequest: () => undefined,
    }, 'voice-fixture');
    const manager = new VoiceSessions({
      authorize: () => {},
      create(binding) {
        native = new CodexVoiceTransport(binding.threadId, peerTransport(peer), timeouts);
        return native;
      },
      startupMs: 4000, leaseMs: 5000, stopMs: 1000,
    });
    t.after(async () => {
      await manager.dispose();
      peer.close();
      await exited;
      await rm(directory, { recursive: true, force: true });
    });
    await peer.request('initialize', { capabilities: { experimentalApi: true } });
    const snapshot = manager.open({
      extensionId: 'voice', hostId: 'local', providerId: 'provider', threadId: 'rt-thread-1', ownerId: 'browser',
    }, 'fixture-offer');
    const access = { id: snapshot.id, extensionId: 'voice', ownerId: 'browser' };
    const events: VoiceEvent[] = [];
    let signal!: () => void;
    const ready = new Promise<void>(resolve => { signal = resolve; });
    manager.subscribe(access, event => {
      events.push(event);
      const current = events.filter((e): e is Extract<VoiceEvent, { type: 'state' }> => e.type === 'state').at(-1)?.session;
      if (current?.state === 'failed'
        || (current?.startAccepted && current.upstreamStarted && events.some(e => e.type === 'sdp'))) signal();
    });
    let deadline: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([ready, new Promise<never>((_, reject) => {
        deadline = setTimeout(() => reject(new Error('Fixture did not reach the expected voice state')), 5000);
      })]);
    } finally { clearTimeout(deadline); }
    if (scenario === 'ok') {
      assert.equal(manager.get(access).state, 'connecting');
      assert.equal(manager.mediaConnected(access).state, 'connected');
      await manager.stop(access);
      assert.equal((events.at(-1) as Extract<VoiceEvent, { type: 'state' }>).session.state, 'closed');
    } else {
      assert.equal((events.at(-1) as Extract<VoiceEvent, { type: 'state' }>).session.reason, 'provider_error');
      assert.ok(!JSON.stringify(events).includes('SECRET_TOKEN'));
    }
    // Closing voice must leave the shared app-server usable for independent task operations.
    assert.equal(peer.isClosed, false);
    assert.deepEqual(await peer.request('account/read', { refreshToken: false }), { account: { type: 'chatgpt' } });
    const requests = (await readFile(log, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.equal(requests.filter(r => r.method === 'thread/realtime/stop').length, 1);
    assert.ok(!requests.some(r => /turn\/(?:interrupt|cancel)|thread\/(?:archive|close)/.test(r.method)));
  });
}
