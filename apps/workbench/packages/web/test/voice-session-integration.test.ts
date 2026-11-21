import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { VoiceSessionController } from '../src/voice-session.js';
import type { Api, VoiceEvent, VoiceSnapshot } from '../src/api.js';
import type { VoiceMediaController, VoiceMediaSnapshot } from '../src/voice-media.js';

const flush = () => new Promise<void>(resolve => setImmediate(resolve));
const snapshot = (id: string, state = 'connecting') => ({
  id, hostId: 'local', threadId: 'thread', providerId: 'provider', state,
  startAccepted: true, upstreamStarted: true, mediaConnected: state === 'connected',
} as VoiceSnapshot);

function harness(t: TestContext, delayed = false) {
  const starts: Array<(value: VoiceSnapshot) => void> = [];
  const stopped: string[] = [];
  const streams = new Map<string, { emit(event: VoiceEvent): void; end(): void }>();
  const media: Array<{ disposed: boolean; listener: (s: VoiceMediaSnapshot) => void; connect(): void }> = [];
  const api = {
    startVoiceSession: async () => delayed ? new Promise<VoiceSnapshot>(resolve => { starts.push(resolve); }) : snapshot('session'),
    stopVoiceSession: async (id: string) => { stopped.push(id); },
    voiceHeartbeat: async () => snapshot('session'),
    voiceConnected: async () => snapshot('session'),
    streamVoiceEvents: (id: string, emit: (event: VoiceEvent) => void, signal: AbortSignal) => new Promise<void>(resolve => {
      streams.set(id, { emit, end: resolve });
      signal.addEventListener('abort', () => resolve(), { once: true });
    }),
  };
  const controller = new VoiceSessionController({ api: api as unknown as Api,
    createMediaController() {
      const item = {
        disposed: false,
        listener: (_s: VoiceMediaSnapshot) => {},
        connect() { this.listener({ category: 'connected', state: 'connected', isMuted: false }); },
        prepare: async () => 'offer',
        acceptAnswer: async () => {},
        onStateChange(fn: (s: VoiceMediaSnapshot) => void) { this.listener = fn; return () => {}; },
        setMuted: () => {},
        dispose() { this.disposed = true; },
      };
      media.push(item);
      return item as unknown as VoiceMediaController;
    },
  });
  t.after(() => controller.stop());
  return { controller, starts, stopped, streams, media, api };
}

test('a late previous start is deleted without replacing or disposing the new media', async t => {
  const h = harness(t, true);
  const old = h.controller.start('thread');
  await flush();
  await h.controller.stop();
  const current = h.controller.start('thread');
  await flush();
  h.starts[0](snapshot('old'));
  await old;
  assert.ok(h.stopped.includes('old'));
  assert.equal(h.media[1].disposed, false);
  h.starts[1](snapshot('new'));
  await current;
  assert.equal(h.controller.state.sessionId, 'new');
  assert.equal(h.controller.state.active, true);
});

test('server closure and normal SSE EOF both release the microphone', async t => {
  for (const mode of ['closed', 'eof']) {
    const h = harness(t);
    await h.controller.start('thread');
    if (mode === 'closed') h.streams.get('session')!.emit({ type: 'state', session: snapshot('session', 'closed') });
    else h.streams.get('session')!.end();
    await flush();
    assert.equal(h.media[0].disposed, true, mode);
    assert.equal(h.controller.state.active, false, mode);
  }
});

test('browser connection waits for the daemon connection state before reporting connected', async t => {
  const h = harness(t);
  await h.controller.start('thread');
  h.media[0].connect();
  await flush();
  assert.notEqual(h.controller.state.status, 'connected');
  h.streams.get('session')!.emit({ type: 'state', session: snapshot('session', 'connected') });
  assert.equal(h.controller.state.status, 'connected');
});

test('transcript deltas append within an item, final replaces, and a new item starts fresh', async t => {
  const h = harness(t);
  await h.controller.start('thread');
  const { emit } = h.streams.get('session')!;
  emit({ type: 'transcript', itemId: 'one', text: 'hello ', final: false });
  emit({ type: 'transcript', itemId: 'one', text: 'world', final: false });
  assert.equal(h.controller.state.transcript, 'hello world');
  emit({ type: 'transcript', itemId: 'one', text: 'Hello world.', final: true });
  assert.equal(h.controller.state.transcript, 'Hello world.');
  emit({ type: 'transcript', itemId: 'two', text: 'Next', final: false });
  assert.equal(h.controller.state.transcript, 'Next');
});

test('role-bearing speech is independent per item and remains readable after stop', async t => {
  const h = harness(t);
  await h.controller.start('thread');
  const { emit } = h.streams.get('session')!;
  emit({ type: 'transcript', itemId: 'u1', role: 'user', text: 'Find ', final: false, afterSeq: 4 });
  emit({ type: 'transcript', itemId: 'a1', role: 'assistant', text: 'Looking', final: false, afterSeq: 4 });
  emit({ type: 'transcript', itemId: 'u1', role: 'user', text: 'newz', final: false, afterSeq: 5 });
  emit({ type: 'transcript', itemId: 'u1', role: 'user', text: 'Find news', final: true, afterSeq: 5 });
  assert.deepEqual(h.controller.state.messages.map(m => [m.role, m.text]), [['user', 'Find news'], ['assistant', 'Looking']]);
  assert.equal(h.controller.state.messages[0].afterSeq, 4);
  await h.controller.stop();
  assert.equal(h.controller.state.messages.length, 2);
  assert.ok(h.controller.state.messages.every(m => !m.streaming));
  assert.equal(h.controller.state.messagesThreadId, 'thread');
  await h.controller.start('other-thread');
  assert.deepEqual(h.controller.state.messages, []);
  assert.equal(h.controller.state.messagesThreadId, 'other-thread');
});

test('transport errors are classified for display and leave no active media', async t => {
  const h = harness(t);
  h.api.startVoiceSession = async () => { throw new Error('SECRET_TOKEN private native path'); };
  await h.controller.start('thread');
  assert.equal(h.controller.state.active, false);
  assert.equal(h.media[0].disposed, true);
  assert.ok(h.controller.state.error);
  assert.ok(!h.controller.state.error!.includes('SECRET_TOKEN'));
});

test('provider failure remains visible after local media is released', async t => {
  const h = harness(t);
  await h.controller.start('thread');
  h.streams.get('session')!.emit({ type: 'state', session: { ...snapshot('session', 'stopping'), reason: 'provider_error' } });
  await flush();
  assert.equal(h.media[0].disposed, true);
  assert.equal(h.controller.state.active, false);
  assert.equal(h.controller.state.status, 'error');
  assert.ok(h.controller.state.error);
});

test('a new utterance sharing the native role key replaces a finalized caption and stays bounded', async t => {
  const h = harness(t);
  await h.controller.start('thread');
  const { emit } = h.streams.get('session')!;
  emit({ type: 'transcript', itemId: 'transcript:user', text: 'Previous.', final: true });
  emit({ type: 'transcript', itemId: 'transcript:user', text: 'Next ', final: false });
  assert.equal(h.controller.state.transcript, 'Next ');
  for (let i = 0; i < 10; i++) emit({ type: 'transcript', itemId: 'transcript:user', text: 'a'.repeat(32000), final: false });
  assert.ok(h.controller.state.transcript.length <= 32768, 'live caption memory must remain bounded');
});
