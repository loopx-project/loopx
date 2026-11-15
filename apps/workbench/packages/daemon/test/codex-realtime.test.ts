import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexRealtime, CodexVoiceTransport, RealtimeError, classifyRealtimeError, peerTransport } from '../src/adapters/codex-realtime.js';
import type { RealtimeFailure, RealtimeHandlers, RealtimeOptions, RealtimeSignal, RealtimeState, RealtimeTransport } from '../src/adapters/codex-realtime.js';
import { JsonRpcPeer } from '../src/adapters/jsonrpc.js';

const THREAD = 'rt-thread-1';
const OTHER_THREAD = 'rt-other-thread';
const flush = () => new Promise(resolve => setImmediate(resolve));
const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const reason = (expected: RealtimeFailure) => (error: unknown) => {
  assert.ok(error instanceof RealtimeError, `expected RealtimeError, got ${String(error)}`);
  assert.equal(error.reason, expected);
  return true;
};

/** Transport with scripted responses; unscripted methods hang like an unresponsive server. */
class ScriptedTransport implements RealtimeTransport {
  isClosed = false;
  readonly calls: Array<{ method: string; params: any }> = [];
  private readonly scripts = new Map<string, (params: any) => unknown>();
  on(method: string, run: (params: any) => unknown): this {
    this.scripts.set(method, run);
    return this;
  }
  hang(method: string): void {
    this.scripts.delete(method);
  }
  request(method: string, params: unknown): Promise<unknown> {
    this.calls.push({ method, params: structuredClone(params) });
    const run = this.scripts.get(method);
    if (!run) return new Promise(() => {});
    try {
      return Promise.resolve(run(params));
    } catch (error) {
      return Promise.reject(error);
    }
  }
}

function setup(options?: RealtimeOptions) {
  const transport = new ScriptedTransport();
  transport.on('account/read', () => ({ account: { type: 'chatgpt' } }));
  transport.on('thread/realtime/start', () => ({}));
  transport.on('thread/realtime/stop', () => ({}));
  const states: RealtimeState[] = [];
  const signals: RealtimeSignal[] = [];
  const handlers: RealtimeHandlers = {
    onState: state => states.push(state),
    onSignal: signal => signals.push(signal),
  };
  const rt = new CodexRealtime(THREAD, transport, handlers, {
    requestTimeoutMs: 500, establishTimeoutMs: 400, closeTimeoutMs: 120, ...options,
  });
  return {
    rt, transport, states, signals, handlers,
    answers: () => signals.filter((s): s is Extract<RealtimeSignal, { type: 'sdp' }> => s.type === 'sdp').map(s => s.sdp),
    notify: (method: string, params: Record<string, unknown> = {}) => rt.handleNotification(method, { threadId: THREAD, ...params }),
    start: (sdpOffer = 'OFFER') => rt.start({ sdpOffer }),
    fresh: () => new CodexRealtime(THREAD, transport, handlers, {
      requestTimeoutMs: 500, establishTimeoutMs: 400, closeTimeoutMs: 120, ...options,
    }),
  };
}

/** Drives one scripted start all the way to `active`. */
async function reachActive(h: ReturnType<typeof setup>) {
  const started = h.start();
  await flush();
  h.notify('thread/realtime/started', { version: 'v3' });
  h.notify('thread/realtime/sdp', { sdp: 'ANSWER' });
  return started;
}

test('start attaches state before the RPC, and only started-plus-SDP means active', async () => {
  const h = setup();
  const started = h.start('OFFER');
  assert.equal(h.rt.state.phase, 'starting');
  await flush();
  assert.deepEqual(h.transport.calls.map(c => c.method), ['account/read', 'thread/realtime/start']);
  assert.deepEqual(h.transport.calls[0].params, { refreshToken: false });
  assert.deepEqual(h.transport.calls[1].params, {
    threadId: THREAD, version: 'v3', outputModality: 'audio', transport: { type: 'webrtc', sdp: 'OFFER' },
  });
  assert.equal(h.rt.state.phase, 'signaling');
  h.notify('thread/realtime/started', { realtimeSessionId: 'SECRET_SESSION_ID', version: 'v3' });
  assert.equal(h.rt.state.serverAccepted, true);
  assert.equal(h.rt.state.phase, 'signaling'); // acknowledgement plus started is still not connected
  h.notify('thread/realtime/sdp', { sdp: 'SECRET_ANSWER' });
  assert.deepEqual(h.answers(), ['SECRET_ANSWER']); // forwarded to the host, not stored in state
  const active = await started;
  assert.equal(active.phase, 'active');
  assert.ok(!('sdp' in active));
  assert.deepEqual(h.states.map(s => s.phase), ['starting', 'signaling', 'signaling', 'signaling', 'active']);
  assert.ok(!JSON.stringify(h.states).includes('SECRET')); // no SDP, session id or credentials in state
});

test('host-managed handoffs use speakable text and cannot append after voice stops', async () => {
  const h = setup();
  h.transport.on('thread/realtime/appendSpeech', () => ({}));
  const started = h.rt.start({ sdpOffer: 'OFFER', context: { instructions: 'Bound coordination instructions' } });
  await flush();
  h.notify('thread/realtime/started'); h.notify('thread/realtime/sdp', { sdp: 'ANSWER' });
  await started;
  assert.equal(h.transport.calls[1].params.clientManagedHandoffs, true);
  await h.rt.speak('The worker returned its result.');
  assert.deepEqual(h.transport.calls.at(-1), { method: 'thread/realtime/appendSpeech',
    params: { threadId: THREAD, text: 'The worker returned its result.' } });
  await assert.rejects(h.rt.speak('x'.repeat(4001)), /Invalid voice summary/);
  const stop = h.rt.stop(); h.notify('thread/realtime/closed'); await stop;
  const count = h.transport.calls.length;
  await assert.rejects(h.rt.speak('Late result'), reason('session_ended'));
  assert.equal(h.transport.calls.length, count);
  assert.ok(!h.transport.calls.some(c => c.method === 'turn/start' || c.method === 'turn/interrupt'));
});

test('canonical transcripts keep roles and separate repeated utterances, without exposing internal items', async () => {
  const h = setup();
  await reachActive(h);
  for (const [id, role, text] of [['u1', 'user', 'Hello'], ['a1', 'assistant', 'Hi'], ['u2', 'user', 'Hello']]) {
    h.notify('thread/realtime/item/started', { item: { type: 'transcriptSegment', id, role, text: '' } });
    h.notify('thread/realtime/item/transcript/delta', { itemId: id, delta: text });
    h.notify('thread/realtime/item/completed', { item: { type: 'transcriptSegment', id, role, text: `${text}!` } });
  }
  h.notify('thread/realtime/item/completed', { item: { type: 'bemItemPromoted', id: 'bem', item_id: 'exec', turn_id: 'task' } });
  h.notify('thread/realtime/item/started', { item: { type: 'transcriptSegment', id: 'system', role: 'system', text: 'SECRET_PROMPT' } });
  h.notify('thread/realtime/item/transcript/delta', { itemId: 'orphan', delta: 'SECRET_ORPHAN' });
  h.notify('thread/realtime/item/added', { item: { type: 'unknown', text: 'SECRET_RAW' } });
  const captions = h.signals.filter(s => s.type === 'transcript');
  assert.deepEqual(captions.map(s => [s.itemId, s.role, s.text, s.final]), [
    ['u1', 'user', 'Hello', false], ['u1', 'user', 'Hello!', true],
    ['a1', 'assistant', 'Hi', false], ['a1', 'assistant', 'Hi!', true],
    ['u2', 'user', 'Hello', false], ['u2', 'user', 'Hello!', true],
  ]);
  assert.ok(!JSON.stringify(captions).includes('SECRET'));
});

test('flat notifications before and after canonical items cannot alias or overwrite another utterance', async () => {
  const h = setup();
  await reachActive(h);
  for (const text of ['first flat utterance', 'second flat utterance']) {
    h.notify('thread/realtime/transcript/delta', { role: 'user', delta: text });
    h.notify('thread/realtime/transcript/done', { role: 'user', text });
  }
  for (const id of ['first', 'second']) {
    h.notify('thread/realtime/item/started', { item: { type: 'transcriptSegment', id, role: 'user', text: '' } });
    h.notify('thread/realtime/item/transcript/delta', { itemId: id, delta: 'draft' });
    h.notify('thread/realtime/transcript/done', { role: 'user', text: 'unidentifiable mirror' });
    h.notify('thread/realtime/item/completed', { item: { type: 'transcriptSegment', id, role: 'user', text: id } });
  }
  assert.deepEqual(h.signals.filter(s => s.type === 'transcript').map(s => [s.itemId, s.text]), [
    ['first', 'draft'], ['first', 'first'], ['second', 'draft'], ['second', 'second'],
  ]);
});

test('duplicate canonical completions keep identity and an empty final clears the draft', async () => {
  const h = setup();
  await reachActive(h);
  const item = { type: 'transcriptSegment', id: 'one', role: 'assistant', text: '' };
  h.notify('thread/realtime/item/started', { item });
  h.notify('thread/realtime/item/transcript/delta', { itemId: item.id, delta: 'draft' });
  h.notify('thread/realtime/item/completed', { item: { ...item, text: '**Final**' } });
  h.notify('thread/realtime/item/completed', { item: { ...item, text: '**Final**' } });
  h.notify('thread/realtime/item/completed', { item });
  assert.deepEqual(h.signals.filter(s => s.type === 'transcript').map(s => [s.itemId, s.text, s.final]), [
    ['one', 'draft', false], ['one', '**Final**', true], ['one', '**Final**', true], ['one', '', true],
  ]);
});

test('a second start while a session lives is refused without new RPCs', async () => {
  const h = setup();
  await reachActive(h);
  await assert.rejects(h.start('ANOTHER'), reason('session_busy'));
  assert.equal(h.rt.state.phase, 'active');
  assert.equal(h.transport.calls.filter(c => c.method === 'account/read').length, 1);
});

test('an API-key login is refused without sending a realtime start', async () => {
  const h = setup();
  h.transport.on('account/read', () => ({ account: { type: 'apiKey' } }));
  await assert.rejects(h.start(), reason('chatgpt_login_required'));
  assert.equal(h.rt.state.phase, 'failed');
  assert.equal(h.rt.state.error, 'chatgpt_login_required');
  assert.ok(!h.transport.calls.some(c => c.method === 'thread/realtime/start'));
});

test('an error notification racing the start acknowledgement still fails the session', async () => {
  const h = setup();
  h.transport.on('thread/realtime/start', () => {
    h.notify('thread/realtime/error', { message: 'realtime conversation requires API key auth SECRET_TOKEN https://private.invalid' });
    return {};
  });
  const started = h.start();
  await assert.rejects(started, reason('api_key_auth_required'));
  await flush();
  assert.ok(h.transport.calls.some(c => c.method === 'thread/realtime/stop')); // server session cleaned up
  h.notify('thread/realtime/closed');
  await flush();
  assert.equal(h.rt.state.phase, 'failed');
  assert.equal(h.rt.state.error, 'api_key_auth_required');
  assert.deepEqual(h.signals.filter(s => s.type === 'error').map(s => (s as any).reason), ['api_key_auth_required']);
});

test('a late error after the session is active stops and fails it', async () => {
  const h = setup();
  await reachActive(h);
  h.notify('thread/realtime/error', { message: 'upstream requires API key auth SECRET' });
  assert.equal(h.rt.state.phase, 'stopping');
  h.notify('thread/realtime/closed');
  await flush();
  assert.equal(h.rt.state.phase, 'failed');
  assert.equal(h.rt.state.error, 'api_key_auth_required');
  assert.ok(h.transport.calls.some(c => c.method === 'thread/realtime/stop'));
});

test('a rejected start RPC fails fast without a pointless stop', async () => {
  const h = setup();
  h.transport.on('thread/realtime/start', () => {
    throw new Error('realtime conversation requires API key auth SECRET_TOKEN');
  });
  await assert.rejects(h.start(), reason('api_key_auth_required'));
  assert.equal(h.rt.state.phase, 'failed');
  assert.ok(!h.transport.calls.some(c => c.method === 'thread/realtime/stop'));
});

test('early started and SDP cannot conceal a rejected start RPC', async () => {
  const h = setup();
  h.transport.on('thread/realtime/start', () => {
    h.notify('thread/realtime/started', { version: 'v3' });
    h.notify('thread/realtime/sdp', { sdp: 'EARLY_ANSWER' });
    throw new Error('unsupported request SECRET_NATIVE_DATA');
  });
  const started = h.start();
  await assert.rejects(started, reason('unsupported'));
  assert.equal(h.rt.state.phase, 'failed');
  assert.equal(h.rt.state.error, 'unsupported');
  assert.deepEqual(h.answers(), ['EARLY_ANSWER']); // the signals still reached the host
  await h.rt.stop(); // already settled: idempotent
});

test('a subscriber stopping synchronously on the first state settles the start', async () => {
  const states: RealtimeState[] = [];
  let rt!: CodexRealtime;
  rt = new CodexRealtime(THREAD, new ScriptedTransport(), {
    onState: state => { states.push(state); if (state.phase === 'starting') void rt.stop(); },
  }, { requestTimeoutMs: 500, establishTimeoutMs: 400, closeTimeoutMs: 100 });
  let outcome = 'pending';
  void rt.start({ sdpOffer: 'OFFER' }).then(() => { outcome = 'resolved'; }, error => { outcome = error instanceof RealtimeError ? error.reason : String(error); });
  await flush();
  assert.equal(outcome, 'stopped');
  assert.equal(rt.state.phase, 'closed');
  assert.deepEqual(states.map(s => s.phase), ['starting', 'closed']);
  await assert.rejects(rt.start({ sdpOffer: 'AGAIN' }), reason('session_ended'));
});

test('stop is idempotent and settles once closed arrives', async () => {
  const h = setup();
  await reachActive(h);
  const first = h.rt.stop();
  const second = h.rt.stop();
  h.notify('thread/realtime/closed');
  assert.equal((await first).phase, 'closed');
  assert.equal((await second).phase, 'closed');
  assert.equal((await h.rt.stop()).phase, 'closed');
  assert.equal(h.transport.calls.filter(c => c.method === 'thread/realtime/stop').length, 1);
});

test('stopping during signalling aborts the pending start', async () => {
  const h = setup();
  const started = h.start();
  await flush();
  assert.equal(h.rt.state.phase, 'signaling');
  const stopping = h.rt.stop();
  await assert.rejects(started, reason('stopped'));
  h.notify('thread/realtime/closed');
  assert.equal((await stopping).phase, 'closed');
});

test('stopping during authentication never sends the realtime start or a stop', async () => {
  const h = setup();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  h.transport.on('account/read', () => gate.then(() => ({ account: { type: 'chatgpt' } })));
  const started = h.start();
  await flush();
  assert.equal((await h.rt.stop()).phase, 'closed');
  release(); // the suspended start unwinds on its own; it must not be held to the RPC timeout
  await assert.rejects(started, reason('stopped'));
  await flush();
  assert.ok(!h.transport.calls.some(c => c.method === 'thread/realtime/start'));
  assert.ok(!h.transport.calls.some(c => c.method === 'thread/realtime/stop'));
});

test('notifications about another thread are ignored', async () => {
  const h = setup();
  const started = h.start();
  await flush();
  for (const [method, params] of [
    ['thread/realtime/started', { version: 'v3' }],
    ['thread/realtime/sdp', { sdp: 'FOREIGN_ANSWER' }],
    ['thread/realtime/error', { message: 'upstream failure' }],
  ] as const) {
    h.rt.handleNotification(method, { threadId: OTHER_THREAD, ...params });
  }
  assert.equal(h.rt.state.phase, 'signaling');
  assert.deepEqual(h.signals, []);
  h.notify('thread/realtime/started', { version: 'v3' });
  h.notify('thread/realtime/sdp', { sdp: 'ANSWER' });
  assert.equal((await started).phase, 'active');
});

test('establishment times out when started or the SDP answer never arrives', async () => {
  for (const deliver of ['none', 'started-only'] as const) {
    const h = setup({ establishTimeoutMs: 60 });
    const started = h.start();
    await flush();
    if (deliver === 'started-only') h.notify('thread/realtime/started', { version: 'v3' });
    await assert.rejects(started, reason('signaling_timeout'));
    await flush();
    assert.ok(h.transport.calls.some(c => c.method === 'thread/realtime/stop'));
    h.notify('thread/realtime/closed');
    await flush();
    assert.equal(h.rt.state.phase, 'failed');
    assert.equal(h.rt.state.error, 'signaling_timeout');
    assert.deepEqual(h.signals.filter(s => s.type === 'error').map(s => (s as any).reason), ['signaling_timeout']);
  }
});

test('a start RPC that never answers times out and cleans up', async () => {
  const h = setup({ requestTimeoutMs: 60, establishTimeoutMs: 5000 });
  h.transport.hang('thread/realtime/start'); // unscripted: the server never answers
  const started = h.start();
  await assert.rejects(started, reason('timeout'));
  await flush();
  assert.ok(h.transport.calls.some(c => c.method === 'thread/realtime/stop'));
  h.notify('thread/realtime/closed');
  await flush();
  assert.equal(h.rt.state.error, 'timeout');
});

test('stop settles even when the closed notification never comes', async () => {
  const h = setup({ closeTimeoutMs: 50 });
  await reachActive(h);
  assert.equal((await h.rt.stop()).phase, 'closed');
  assert.equal(h.rt.state.error, null);
});

test('process closure settles a live session without stop RPCs', async () => {
  const h = setup();
  await reachActive(h);
  h.rt.handleClosed();
  assert.equal(h.rt.state.phase, 'failed');
  assert.equal(h.rt.state.error, 'process_closed');
  assert.ok(!h.transport.calls.some(c => c.method === 'thread/realtime/stop'));
  assert.deepEqual(h.signals.filter(s => s.type === 'error').map(s => (s as any).reason), ['process_closed']);
});

test('process closure fails a start still authenticating', async () => {
  const h = setup();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  h.transport.on('account/read', () => gate.then(() => ({ account: { type: 'chatgpt' } })));
  const started = h.start();
  await flush();
  h.rt.handleClosed();
  release();
  await assert.rejects(started, reason('process_closed'));
  await flush();
  assert.ok(!h.transport.calls.some(c => c.method === 'thread/realtime/start'));
});

test('a session the server closes on its own is a failure, not a stop', async () => {
  const h = setup();
  await reachActive(h);
  h.notify('thread/realtime/closed');
  assert.equal(h.rt.state.phase, 'failed');
  assert.equal(h.rt.state.error, 'closed_by_server');
  assert.ok(h.signals.some(s => s.type === 'closed'));
});

test('an error during a user stop decides the outcome', async () => {
  const h = setup();
  await reachActive(h);
  const stopping = h.rt.stop();
  h.notify('thread/realtime/error', { message: 'upstream requires API key auth SECRET' });
  h.notify('thread/realtime/closed');
  assert.equal((await stopping).phase, 'failed');
  assert.equal(h.rt.state.error, 'api_key_auth_required');
});

test('after a clean close, a settled instance refuses restart and a fresh instance owns the thread', async () => {
  const h = setup();
  await reachActive(h);
  await h.rt.stop();
  h.notify('thread/realtime/closed'); // clean close: the trailing closed was consumed
  assert.equal(h.rt.state.phase, 'closed');
  // Leftovers of the stopped session, before and during the next start's authentication.
  h.notify('thread/realtime/error', { message: 'stale SECRET failure' });
  h.notify('thread/realtime/sdp', { sdp: 'STALE_SDP' });
  assert.equal(h.rt.state.error, null);
  assert.deepEqual(h.answers(), ['ANSWER']);
  await assert.rejects(h.start('OFFER-AGAIN'), reason('session_ended'));

  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  h.transport.on('account/read', () => gate.then(() => ({ account: { type: 'chatgpt' } })));
  const next = h.fresh();
  const second = next.start({ sdpOffer: 'OFFER-2' });
  await flush();
  next.handleNotification('thread/realtime/sdp', { threadId: THREAD, sdp: 'STALE_SDP_2' });
  next.handleNotification('thread/realtime/error', { threadId: THREAD, message: 'stale SECRET failure' });
  release();
  await flush();
  next.handleNotification('thread/realtime/started', { threadId: THREAD, version: 'v3' });
  next.handleNotification('thread/realtime/sdp', { threadId: THREAD, sdp: 'ANSWER' });
  const active = await second;
  assert.equal(active.phase, 'active');
  assert.deepEqual(h.answers(), ['ANSWER', 'ANSWER']); // stale answers never reached the host
  assert.equal(next.state.error, null);
});

test('an unclean close leaves late same-thread events unattributable: a fresh thread is required', async () => {
  // Native notifications carry only the thread id. When the previous session never delivered its
  // `closed`, that late arrival after a new start RPC is indistinguishable from the new session's
  // own closure. This pins the real protocol limit on purpose: the host must give the next session
  // a fresh native thread, where the different thread id filters the leftovers outright.
  const h = setup({ closeTimeoutMs: 40 });
  await reachActive(h);
  const stopping = h.rt.stop();
  // No closed notification ever arrives; the stop settles on its close timeout alone.
  assert.equal((await stopping).phase, 'closed');

  const next = h.fresh();
  const second = next.start({ sdpOffer: 'OFFER-2' });
  await flush();
  next.handleNotification('thread/realtime/started', { threadId: THREAD, version: 'v3' });
  next.handleNotification('thread/realtime/sdp', { threadId: THREAD, sdp: 'ANSWER' });
  assert.equal((await second).phase, 'active');

  next.handleNotification('thread/realtime/closed', { threadId: THREAD }); // the stale one
  assert.equal(next.state.phase, 'failed');
  assert.equal(next.state.error, 'closed_by_server');
});

test('dispose settles synchronously and ends the instance', async () => {
  const h = setup();
  await reachActive(h);
  const stopping = h.rt.stop();
  h.rt.dispose(); // the host gave up waiting for the native stop
  assert.equal(h.rt.state.phase, 'closed');
  assert.equal((await stopping).phase, 'closed');
  h.notify('thread/realtime/error', { message: 'late failure' });
  h.notify('thread/realtime/closed');
  assert.equal(h.signals.length, 2); // started and sdp only; nothing after dispose
  await assert.rejects(h.start('ONCE-MORE'), reason('session_ended'));
});

test('classification maps upstream text to tokens and drops the raw message', () => {
  const cases: Array<[unknown, RealtimeFailure]> = [
    ['realtime conversation requires API key auth SECRET_TOKEN', 'api_key_auth_required'],
    [new Error('text realtime output modality requires realtime v2'), 'text_requires_v2'],
    ['audio requires webrtc transport', 'webrtc_required'],
    [new Error('method not found: thread/realtime/start'), 'unsupported'],
    ['401 unauthorized SECRET', 'authentication_failed'],
    ['403 not entitled', 'access_denied'],
    [new Error('request timed out'), 'timeout'],
    ['something broke at https://private.invalid/x', 'native_error'],
    [{ weird: true }, 'native_error'],
  ];
  for (const [input, expected] of cases) assert.equal(classifyRealtimeError(input), expected);
  assert.ok(!classifyRealtimeError(new Error('failure with SECRET_TOKEN https://private.invalid')).includes('SECRET'));
});

test('voice transport adapter resolves at the acknowledgement and streams signals', async () => {
  const transport = new ScriptedTransport();
  transport.on('account/read', () => ({ account: { type: 'chatgpt' } }));
  transport.on('thread/realtime/start', () => ({}));
  transport.on('thread/realtime/stop', () => ({}));
  const vt = new CodexVoiceTransport(THREAD, transport, { requestTimeoutMs: 500, establishTimeoutMs: 400, closeTimeoutMs: 120 });
  const signals: RealtimeSignal[] = [];
  const start = vt.start('OFFER', signal => signals.push(signal));
  await flush();
  await start; // the native request was acknowledged — long before the session is established
  assert.equal(vt.state.phase, 'signaling');
  vt.handleNotification('thread/realtime/started', { threadId: THREAD, version: 'v3' });
  vt.handleNotification('thread/realtime/sdp', { threadId: THREAD, sdp: 'ANSWER' });
  vt.handleNotification('thread/realtime/item/started', { threadId: THREAD,
    item: { type: 'transcriptSegment', id: 'spoken', role: 'user', text: '' } });
  vt.handleNotification('thread/realtime/item/transcript/delta', { threadId: THREAD, itemId: 'spoken', delta: '嗯' });
  const stopping = vt.stop();
  vt.handleNotification('thread/realtime/closed', { threadId: THREAD });
  await stopping;
  assert.deepEqual(signals, [
    { type: 'started' },
    { type: 'sdp', sdp: 'ANSWER' },
    { type: 'transcript', itemId: 'spoken', text: '嗯', final: false, role: 'user' },
    { type: 'closed' },
  ]);
  assert.equal(vt.state.phase, 'closed');
  assert.deepEqual(transport.calls.map(call => call.method),
    ['account/read', 'thread/realtime/start', 'thread/realtime/stop']);
  vt.dispose();
  await assert.rejects(vt.start('AGAIN', () => {}), reason('session_ended'));
});

test('voice transport adapter rejects a failed start and stays silent after dispose', async () => {
  const transport = new ScriptedTransport();
  transport.on('account/read', () => ({ account: { type: 'apiKey' } }));
  const vt = new CodexVoiceTransport(THREAD, transport, { requestTimeoutMs: 200, establishTimeoutMs: 200, closeTimeoutMs: 100 });
  const signals: RealtimeSignal[] = [];
  await assert.rejects(vt.start('OFFER', signal => signals.push(signal)), reason('chatgpt_login_required'));
  assert.deepEqual(signals, []);

  const pending = vt.stop(); // nothing to stop; must not reject
  vt.dispose();
  await pending;
  vt.handleNotification('thread/realtime/sdp', { threadId: THREAD, sdp: 'LATE' });
  vt.handleClosed();
  assert.deepEqual(signals, []);
});

test('voice transport adapter surfaces a hanging stop through dispose', async () => {
  const transport = new ScriptedTransport();
  transport.on('account/read', () => ({ account: { type: 'chatgpt' } }));
  transport.on('thread/realtime/start', () => ({}));
  transport.hang('thread/realtime/stop'); // the native stop never settles
  const vt = new CodexVoiceTransport(THREAD, transport, { requestTimeoutMs: 500, establishTimeoutMs: 400, closeTimeoutMs: 5000 });
  const signals: RealtimeSignal[] = [];
  await vt.start('OFFER', signal => signals.push(signal));
  const stopping = vt.stop();
  await flush();
  vt.dispose(); // the coordinator's stop budget ran out
  await stopping; // dispose released the wait without waiting for closeTimeoutMs
  assert.equal(vt.state.phase, 'closed');
});

const fixture = fileURLToPath(new URL('./fixtures/fake-codex-realtime.mjs', import.meta.url));

type NativeEndpoint = { handleNotification(method: string, params: Record<string, unknown>): void; handleClosed(): void };

/** Runs a scenario against the fixture child through a real JsonRpcPeer, like the daemon will. */
async function withFixture<T extends NativeEndpoint>(
  scenario: string,
  build: (transport: RealtimeTransport, handlers: RealtimeHandlers, options: RealtimeOptions) => T,
  handlers: RealtimeHandlers,
  run: (endpoint: T, renew: () => T) => Promise<void>,
  options?: RealtimeOptions,
): Promise<Array<{ method?: string; params?: any } & Record<string, unknown>>> {
  const dir = await mkdtemp(join(tmpdir(), 'calli-realtime-test-'));
  const log = join(dir, 'requests.jsonl');
  const child = spawn(process.execPath, [fixture], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, FAKE_REALTIME_SCENARIO: scenario, FAKE_REALTIME_LOG: log },
  });
  const holder: { endpoint: T | null } = { endpoint: null };
  const peer = new JsonRpcPeer(child, {
    onNotification: (method, params) => holder.endpoint?.handleNotification(method, params as Record<string, unknown>),
    onRequest: async () => undefined,
    onClosed: () => holder.endpoint?.handleClosed(),
  }, 'fake-realtime');
  const opts = { requestTimeoutMs: 1500, establishTimeoutMs: 500, closeTimeoutMs: 300, ...options };
  const renew = () => { holder.endpoint = build(peerTransport(peer), handlers, opts); return holder.endpoint; };
  let messages: Array<any> = [];
  try {
    await peer.request('initialize', { clientInfo: { name: 'calli', title: 'Calli', version: '0.1.0' }, capabilities: { experimentalApi: true } });
    peer.notify('initialized', {});
    await run(renew(), renew);
  } finally {
    holder.endpoint = null;
    peer.close();
    await new Promise<void>(resolve => {
      const giveUp = setTimeout(resolve, 2000);
      child.once('close', () => { clearTimeout(giveUp); resolve(); });
    });
    messages = (await readFile(log, 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    await rm(dir, { recursive: true, force: true });
  }
  return messages;
}

function capture() {
  const states: RealtimeState[] = [];
  const signals: RealtimeSignal[] = [];
  return {
    states, signals,
    handlers: { onState: (s: RealtimeState) => states.push(s), onSignal: (s: RealtimeSignal) => signals.push(s) } as RealtimeHandlers,
    answers: () => signals.filter((s): s is Extract<RealtimeSignal, { type: 'sdp' }> => s.type === 'sdp').map(s => s.sdp),
  };
}

const buildController = (transport: RealtimeTransport, handlers: RealtimeHandlers, options?: RealtimeOptions) =>
  new CodexRealtime(THREAD, transport, handlers, options);

test('fixture: a full session over a real peer, from start to stop', async () => {
  const cap = capture();
  const messages = await withFixture('ok', (t, h, o) => buildController(t, h, o), cap.handlers, async rt => {
    const active = await rt.start({ sdpOffer: 'FIXTURE_OFFER' });
    assert.equal(active.phase, 'active');
    assert.deepEqual(cap.answers(), ['v=0\r\nSECRET_SDP_ANSWER']);
    // The transcript burst streams right behind the answer; read it before stopping, since
    // anything from a stopping session onward is late by definition.
    await wait(30);
    assert.equal(cap.signals.filter(s => s.type === 'transcript').length, 10);
    assert.equal((await rt.stop()).phase, 'closed');
    assert.equal(rt.state.error, null);
  });
  assert.ok(!JSON.stringify(cap.states).includes('SECRET'));
  assert.deepEqual(cap.signals.filter(s => s.type === 'transcript').map(s => [s.itemId, s.role, s.text, s.final]), [
    ['a1', 'assistant', '你', false], ['a1', 'assistant', '你好。', true],
    ['u1', 'user', '跑', false], ['u1', 'user', '下测试', false], ['u1', 'user', '跑一下测试', true],
    ['a2', 'assistant', '好的', false], ['a2', 'assistant', '好的，马上。', true],
    ['item-1', 'user', '顺便', false], ['item-1', 'user', '看下日志', false], ['item-1', 'user', '顺便看下日志', true],
  ]);
  const methods = messages.filter(m => m.method).map(m => m.method);
  assert.ok(methods.indexOf('account/read') < methods.indexOf('thread/realtime/start'));
  assert.ok(methods.indexOf('thread/realtime/start') < methods.indexOf('thread/realtime/stop'));
  const start = messages.find(m => m.method === 'thread/realtime/start');
  assert.deepEqual(start.params, {
    threadId: THREAD, version: 'v3', outputModality: 'audio', transport: { type: 'webrtc', sdp: 'FIXTURE_OFFER' },
  });
});

test('fixture: mixed flat and typed channels render one item per utterance', async () => {
  const cap = capture();
  await withFixture('mixed', (t, h, o) => buildController(t, h, o), cap.handlers, async rt => {
    const active = await rt.start({ sdpOffer: 'FIXTURE_OFFER' });
    assert.equal(active.phase, 'active');
    await wait(50); // the tail of the burst may still be in the pipe when start resolves
    assert.deepEqual(cap.signals.filter(s => s.type === 'transcript'), [
      { type: 'transcript', itemId: 'seg-1', text: '帮我整理', final: false, role: 'user' },
      { type: 'transcript', itemId: 'seg-1', text: '帮我整理新闻', final: true, role: 'user' },
      { type: 'transcript', itemId: 'seg-2', text: '好的', final: false, role: 'assistant' },
      { type: 'transcript', itemId: 'seg-2', text: '好的，整理好了。', final: true, role: 'assistant' },
    ]);
    const stopping = rt.stop();
    // Nothing further arrives, but the stop must still settle on its close timeout.
    assert.equal((await stopping).phase, 'closed');
  });
});

test('fixture: an API-key account never reaches thread/realtime/start', async () => {
  const cap = capture();
  const messages = await withFixture('api-key', (t, h, o) => buildController(t, h, o), cap.handlers, async rt => {
    await assert.rejects(rt.start({ sdpOffer: 'OFFER' }), reason('chatgpt_login_required'));
  });
  assert.ok(!messages.some(m => m.method === 'thread/realtime/start'));
});

test('fixture: an error before the acknowledgement fails the session and stops it', async () => {
  const cap = capture();
  const messages = await withFixture('async-error-early', (t, h, o) => buildController(t, h, o), cap.handlers, async rt => {
    await assert.rejects(rt.start({ sdpOffer: 'OFFER' }), reason('api_key_auth_required'));
    await rt.stop(); // the defensive stop is still wrapping up when the start rejects
    assert.equal(rt.state.phase, 'failed');
  });
  const methods = messages.filter(m => m.method).map(m => m.method);
  assert.ok(methods.indexOf('thread/realtime/start') < methods.indexOf('thread/realtime/stop'));
  assert.ok(!JSON.stringify(cap.states).includes('SECRET'));
});

test('fixture: after a clean close, leftovers never reach a fresh instance on the same peer', async () => {
  const cap = capture();
  const messages = await withFixture('stale', (t, h, o) => buildController(t, h, o), cap.handlers, async (rt, renew) => {
    const first = await rt.start({ sdpOffer: 'OFFER-1' });
    assert.equal(first.phase, 'active');
    assert.equal((await rt.stop()).phase, 'closed');
    await wait(50); // leftovers of the first session arrive after its close
    await assert.rejects(rt.start({ sdpOffer: 'REUSE' }), reason('session_ended'));

    const next = renew(); // one instance per native session; this stop closed cleanly
    const second = await next.start({ sdpOffer: 'OFFER-2' });
    assert.equal(second.phase, 'active');
    assert.equal(next.state.error, null);
  });
  assert.deepEqual(cap.answers(), ['v=0\r\nSECRET_SDP_ANSWER', 'v=0\r\nSECRET_SDP_ANSWER']);
  assert.equal(messages.filter(m => m.method === 'thread/realtime/start').length, 2);
});

test('fixture: the app-server exiting fails the session without a stop RPC', async () => {
  const cap = capture();
  const messages = await withFixture('exit', (t, h, o) => buildController(t, h, o), cap.handlers, async rt => {
    await assert.rejects(rt.start({ sdpOffer: 'OFFER' }), reason('process_closed'));
    assert.equal(rt.state.error, 'process_closed');
  });
  assert.ok(!messages.some(m => m.method === 'thread/realtime/stop'));
  assert.deepEqual(cap.signals.filter(s => s.type === 'error').map(s => (s as any).reason), ['process_closed']);
});

test('fixture: notifications about another thread never reach the session', async () => {
  const cap = capture();
  await withFixture('wrong-thread', (t, h, o) => buildController(t, h, o), cap.handlers, async rt => {
    const active = await rt.start({ sdpOffer: 'OFFER' });
    assert.equal(active.phase, 'active');
    assert.equal(cap.answers().length, 1);
  });
});

test('fixture: the voice transport adapter drives a session over a real peer', async () => {
  const cap = capture();
  await withFixture('ok', (t, h, o) => new CodexVoiceTransport(THREAD, t, o), cap.handlers, async vt => {
    const signals: RealtimeSignal[] = [];
    const start = vt.start('FIXTURE_OFFER', signal => signals.push(signal));
    await start; // resolves at the acknowledgement; establishment may legitimately be done already
    assert.ok(vt.state.phase === 'signaling' || vt.state.phase === 'active',
      `phase after the acknowledgement must be signaling or active, got ${vt.state.phase}`);
    await wait(50); // started, sdp and transcripts stream in
    assert.equal(vt.state.phase, 'active'); // connected still means acknowledgement plus started plus SDP
    const stopping = vt.stop(); // the fixture acknowledges and closes
    await stopping;
    vt.handleNotification('thread/realtime/closed', { threadId: THREAD }); // already settled: dropped
    assert.equal(vt.state.phase, 'closed');
    assert.deepEqual(signals.map(s => s.type), ['started', 'sdp', ...Array.from({ length: 10 }, () => 'transcript'), 'closed']);
  });
});
