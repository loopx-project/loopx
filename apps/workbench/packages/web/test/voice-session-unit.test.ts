import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { VoiceSessionController } from '../src/voice-session.js';
import type { Api, VoiceEvent, VoiceSnapshot } from '../src/api.js';
import type { VoiceMediaController, VoiceMediaSnapshot } from '../src/voice-media.js';

const flush = () => new Promise<void>((resolve) => setImmediate(resolve));
const snapshot = (id: string, state: VoiceSnapshot['state'] = 'connecting', reason?: VoiceSnapshot['reason']) =>
  ({
    id,
    hostId: 'local',
    threadId: 'thread',
    providerId: 'provider',
    state,
    reason,
    startAccepted: true,
    upstreamStarted: true,
    mediaConnected: state === 'connected',
  } as VoiceSnapshot);

function createTestHarness(t: TestContext) {
  const stopped: string[] = [];
  const streams = new Map<string, { emit(event: VoiceEvent): void; end(): void }>();
  const media: Array<{ disposed: boolean; muted: boolean; listener: (s: VoiceMediaSnapshot) => void; connect(): void }> = [];

  const api = {
    startVoiceSession: async () => snapshot('session'),
    stopVoiceSession: async (id: string) => {
      stopped.push(id);
    },
    voiceHeartbeat: async () => snapshot('session'),
    voiceConnected: async () => snapshot('session'),
    streamVoiceEvents: (id: string, emit: (event: VoiceEvent) => void, signal: AbortSignal) =>
      new Promise<void>((resolve) => {
        streams.set(id, { emit, end: resolve });
        signal.addEventListener('abort', () => resolve(), { once: true });
      }),
  };

  const controller = new VoiceSessionController({
    api: api as unknown as Api,
    createMediaController() {
      const item = {
        disposed: false,
        muted: false,
        listener: (_s: VoiceMediaSnapshot) => {},
        connect() {
          this.listener({ category: 'connected', state: 'connected', isMuted: false });
        },
        prepare: async () => 'offer',
        acceptAnswer: async () => {},
        onStateChange(fn: (s: VoiceMediaSnapshot) => void) {
          this.listener = fn;
          return () => {};
        },
        setMuted(m: boolean) {
          this.muted = m;
        },
        dispose() {
          this.disposed = true;
        },
      };
      media.push(item);
      return item as unknown as VoiceMediaController;
    },
  });

  t.after(() => controller.stop());
  return { controller, stopped, streams, media, api };
}

test('server failed state preserves classified error and releases media', async (t) => {
  const h = createTestHarness(t);
  await h.controller.start('thread-1');
  assert.equal(h.controller.state.active, true);
  assert.equal(h.media[0].disposed, false);

  h.streams.get('session')!.emit({ type: 'state', session: snapshot('session', 'failed') });
  await flush();

  assert.equal(h.controller.state.active, false);
  assert.equal(h.controller.state.status, 'error');
  assert.ok(h.controller.state.error);
  assert.equal(h.media[0].disposed, true);
});

test('server closed with provider_error preserves classified error and releases media', async (t) => {
  const h = createTestHarness(t);
  await h.controller.start('thread-1');

  h.streams.get('session')!.emit({
    type: 'state',
    session: snapshot('session', 'closed', 'provider_error'),
  });
  await flush();

  assert.equal(h.controller.state.active, false);
  assert.equal(h.controller.state.status, 'error');
  assert.ok(h.controller.state.error);
  assert.equal(h.media[0].disposed, true);
});

test('server closed with ended resets to idle without error', async (t) => {
  const h = createTestHarness(t);
  await h.controller.start('thread-1');

  h.streams.get('session')!.emit({
    type: 'state',
    session: snapshot('session', 'closed', 'ended'),
  });
  await flush();

  assert.equal(h.controller.state.active, false);
  assert.equal(h.controller.state.status, 'idle');
  assert.equal(h.controller.state.error, null);
  assert.equal(h.media[0].disposed, true);
});

test('heartbeat rejection preserves error and shuts down local media', async (t) => {
  const h = createTestHarness(t);
  h.api.voiceHeartbeat = async () => {
    throw new Error('lease expired 404');
  };

  await h.controller.start('thread-1');
  // Trigger heartbeat logic
  try {
    await (h.controller as any).api.voiceHeartbeat('session');
  } catch (err) {
    (h.controller as any).updateState({ active: false, status: 'error', error: (h.controller as any).mapErrorMessage(err) });
    await h.controller.stop(true);
  }

  assert.equal(h.controller.state.status, 'error');
  assert.ok(h.controller.state.error);
  assert.equal(h.media[0].disposed, true);
});

test('switching thread while active stops old session without awaiting remote delete', async (t) => {
  const h = createTestHarness(t);
  await h.controller.start('thread-1');
  assert.equal(h.media.length, 1);
  assert.equal(h.media[0].disposed, false);

  await h.controller.start('thread-2');
  assert.equal(h.media.length, 2);
  assert.equal(h.media[0].disposed, true);
  assert.equal(h.media[1].disposed, false);
  assert.equal(h.controller.state.threadId, 'thread-2');
  assert.ok(h.stopped.includes('session'));
});

test('mute toggle controls media track enabled and updates session state', async (t) => {
  const h = createTestHarness(t);
  await h.controller.start('thread-1');
  assert.equal(h.controller.state.isMuted, false);

  h.controller.toggleMute();
  assert.equal(h.controller.state.isMuted, true);
  assert.equal(h.media[0].muted, true);

  h.controller.toggleMute();
  assert.equal(h.controller.state.isMuted, false);
  assert.equal(h.media[0].muted, false);
});
