import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  VoiceMediaController,
  VoiceMediaException,
  isVoiceMediaSupported,
  type MediaTrackLike,
  type MediaStreamLike,
  type RTCPeerConnectionLike,
  type RTCDataChannelLike,
  type AudioPlaybackSink,
  type VoiceMediaEnv,
  type VoiceMediaSnapshot,
} from '../src/voice-media.js';

class MockTrack implements MediaTrackLike {
  kind = 'audio';
  enabled = true;
  stopped = false;
  private readonly listeners = new Map<string, Set<(ev: any) => void>>();

  stop(): void {
    this.stopped = true;
  }

  addEventListener(type: string, listener: (ev: any) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (ev: any) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type: string, ev: any = {}): void {
    const set = this.listeners.get(type);
    if (set) {
      for (const l of [...set]) l(ev);
    }
  }
}

class MockStream implements MediaStreamLike {
  tracks: MockTrack[];

  constructor(tracks: MockTrack[] = [new MockTrack()]) {
    this.tracks = tracks;
  }

  getTracks(): MediaTrackLike[] {
    return this.tracks;
  }

  getAudioTracks(): MediaTrackLike[] {
    return this.tracks.filter((t) => t.kind === 'audio');
  }
}

class MockDataChannel implements RTCDataChannelLike {
  readonly label: string;
  readyState = 'open';
  sentData: string[] = [];
  closed = false;

  constructor(label: string) {
    this.label = label;
  }

  send(data: any): void {
    if (this.closed || this.readyState !== 'open') throw new Error('DataChannel closed');
    this.sentData.push(String(data));
  }

  close(): void {
    this.closed = true;
    this.readyState = 'closed';
  }
}

class MockPeerConnection implements RTCPeerConnectionLike {
  connectionState = 'new';
  iceConnectionState = 'new';
  iceGatheringState = 'new';
  localDescription: { sdp: string; type: string } | null = null;
  remoteDescription: { sdp: string; type: string } | null = null;
  dataChannels: MockDataChannel[] = [];
  addedTracks: MediaTrackLike[] = [];
  closed = false;
  private readonly listeners = new Map<string, Set<(ev: any) => void>>();

  createOfferFail = false;
  setLocalFail = false;
  setRemoteFail = false;
  offerSdp = 'v=0\r\no=- 12345 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n';

  createDataChannel(label: string): RTCDataChannelLike {
    const dc = new MockDataChannel(label);
    this.dataChannels.push(dc);
    return dc;
  }

  addTrack(track: MediaTrackLike): any {
    this.addedTracks.push(track);
  }

  async createOffer(): Promise<{ sdp: string; type: string }> {
    if (this.createOfferFail) throw new Error('createOffer failed');
    return { sdp: this.offerSdp, type: 'offer' };
  }

  async setLocalDescription(desc: any): Promise<void> {
    if (this.setLocalFail) throw new Error('setLocalDescription failed');
    this.localDescription = desc;
    // By default simulate immediate complete gathering if not overridden
    if (this.iceGatheringState === 'new') {
      this.iceGatheringState = 'complete';
      this.emit('icegatheringstatechange');
    }
  }

  async setRemoteDescription(desc: any): Promise<void> {
    if (this.setRemoteFail) throw new Error('setRemoteDescription failed');
    this.remoteDescription = desc;
  }

  close(): void {
    this.closed = true;
    this.connectionState = 'closed';
  }

  addEventListener(type: string, listener: (ev: any) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (ev: any) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type: string, ev: any = {}): void {
    const set = this.listeners.get(type);
    if (set) {
      for (const l of [...set]) l(ev);
    }
  }
}

class MockAudioSink implements AudioPlaybackSink {
  srcObject: any = null;
  played = false;
  paused = false;
  playFail = false;

  async play(): Promise<void> {
    if (this.playFail) {
      const err = new Error('NotAllowedError: play() failed because the user didn\'t interact');
      err.name = 'NotAllowedError';
      throw err;
    }
    this.played = true;
  }

  pause(): void {
    this.paused = true;
  }
}

interface TestContext {
  mockTracks: MockTrack[];
  mockStream: MockStream;
  lastPc: MockPeerConnection | null;
  sink: MockAudioSink;
  env: VoiceMediaEnv;
  getUserMediaPending: { resolve: (stream: MediaStreamLike) => void; reject: (err: any) => void } | null;
}

function createTestContext(opts: { immediateStream?: boolean; secureContext?: boolean } = {}): TestContext {
  const mockTracks = [new MockTrack(), new MockTrack()];
  const mockStream = new MockStream(mockTracks);
  const sink = new MockAudioSink();
  let lastPc: MockPeerConnection | null = null;
  let getUserMediaPending: { resolve: (stream: MediaStreamLike) => void; reject: (err: any) => void } | null = null;

  const env: VoiceMediaEnv = {
    isSecureContext: opts.secureContext ?? true,
    mediaDevices: {
      getUserMedia: async () => {
        if (opts.immediateStream !== false) {
          return mockStream;
        }
        return new Promise<MediaStreamLike>((resolve, reject) => {
          getUserMediaPending = { resolve, reject };
        });
      },
    },
    RTCPeerConnection: function MockPCConstructor() {
      const pc = new MockPeerConnection();
      lastPc = pc;
      return pc;
    } as any,
    createAudioElement: () => sink,
    createMediaStream: tracks => new MockStream(tracks as MockTrack[]),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
  };

  return {
    mockTracks,
    mockStream,
    get lastPc() {
      return lastPc;
    },
    sink,
    env,
    get getUserMediaPending() {
      return getUserMediaPending;
    },
  };
}

// ---------------------------------------------------------------- Tests

test('default constructor touches no media and sets idle state', () => {
  const ctx = createTestContext();
  let getUserMediaCalled = false;
  const guardedEnv: VoiceMediaEnv = {
    ...ctx.env,
    mediaDevices: {
      getUserMedia: async () => {
        getUserMediaCalled = true;
        return ctx.mockStream;
      },
    },
    createAudioElement: () => {
      throw new Error('createAudioElement should not be called in constructor');
    },
  };

  const controller = new VoiceMediaController({ env: guardedEnv });
  assert.equal(controller.state, 'idle');
  assert.equal(controller.category, 'idle');
  assert.equal(controller.isMuted, false);
  assert.equal(controller.isDisposed, false);
  assert.equal(controller.error, undefined);
  assert.equal(getUserMediaCalled, false);
});

test('dispose called while permission request is pending stops late tracks and aborts', async () => {
  const ctx = createTestContext({ immediateStream: false });
  const controller = new VoiceMediaController({ env: ctx.env, audioSink: ctx.sink });

  const snapshots: VoiceMediaSnapshot[] = [];
  controller.onStateChange((s) => snapshots.push(s));

  const preparePromise = controller.prepare();
  assert.equal(controller.state, 'requesting-permission');
  assert.equal(controller.category, 'connecting');

  // Dispose before user grants permission
  controller.dispose();
  assert.equal(controller.state, 'closed');
  assert.equal(controller.category, 'closed');

  // Now permission resolves late
  assert.ok(ctx.getUserMediaPending);
  ctx.getUserMediaPending!.resolve(ctx.mockStream);

  await assert.rejects(preparePromise, (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'aborted');
    return true;
  });

  // All tracks from the late stream must be stopped immediately
  for (const t of ctx.mockTracks) {
    assert.equal(t.stopped, true, 'Track must be stopped when delivered after dispose');
  }

  // PeerConnection must never have been created
  assert.equal(ctx.lastPc, null);
  assert.equal(controller.state, 'closed');
});

test('permission denial (NotAllowedError) transitions to error state and cleans up', async () => {
  const ctx = createTestContext();
  ctx.env.mediaDevices!.getUserMedia = async () => {
    const err = new Error('Permission denied by user');
    err.name = 'NotAllowedError';
    throw err;
  };

  const controller = new VoiceMediaController({ env: ctx.env });
  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'permission-denied');
    return true;
  });

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'permission-denied');
  assert.equal(ctx.lastPc, null);
});

test('insecure context is rejected with insecure-context error', async () => {
  const ctx = createTestContext({ secureContext: false });
  const controller = new VoiceMediaController({ env: ctx.env });

  assert.equal(isVoiceMediaSupported(ctx.env), false);

  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'insecure-context');
    return true;
  });

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'insecure-context');
});

test('missing browser APIs reports not-supported', async () => {
  const ctx = createTestContext();
  const unsupportedEnv: VoiceMediaEnv = {
    isSecureContext: true,
    mediaDevices: undefined,
    RTCPeerConnection: undefined,
  };

  assert.equal(isVoiceMediaSupported(unsupportedEnv), false);

  const controller = new VoiceMediaController({ env: unsupportedEnv });
  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'not-supported');
    return true;
  });

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'not-supported');
});

test('ICE gathering timeout fails and cleans up tracks and peer connection', async () => {
  const ctx = createTestContext();
  let createdPc: MockPeerConnection | null = null;
  ctx.env.RTCPeerConnection = function StallingPC() {
    createdPc = new MockPeerConnection();
    // Do not complete ICE gathering
    createdPc.iceGatheringState = 'gathering';
    return createdPc;
  } as any;

  const controller = new VoiceMediaController({
    env: ctx.env,
    iceTimeoutMs: 100, // Short timeout for test
  });

  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'ice-timeout');
    return true;
  });

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'ice-timeout');

  // Local tracks must be stopped
  for (const t of ctx.mockTracks) {
    assert.equal(t.stopped, true);
  }
  // PC must be closed
  assert.equal(createdPc!.closed, true);
});

test('SDP createOffer failure cleans up all resources', async () => {
  const ctx = createTestContext();
  let createdPc: MockPeerConnection | null = null;
  ctx.env.RTCPeerConnection = function FailingPC() {
    createdPc = new MockPeerConnection();
    createdPc.createOfferFail = true;
    return createdPc;
  } as any;

  const controller = new VoiceMediaController({ env: ctx.env });

  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'sdp-failed');
    return true;
  });

  assert.equal(controller.state, 'error');
  assert.equal(controller.error?.code, 'sdp-failed');
  assert.equal(createdPc!.closed, true);
  for (const t of ctx.mockTracks) {
    assert.equal(t.stopped, true);
  }
});

test('successful prepare, acceptAnswer and connection lifecycle', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({
    env: ctx.env,
    audioSink: ctx.sink,
  });

  const stateChanges: string[] = [];
  controller.onStateChange((s) => stateChanges.push(`${s.category}:${s.state}`));

  const offer = await controller.prepare();
  assert.ok(typeof offer === 'string');
  assert.ok(offer.includes('m=audio'));
  assert.equal(controller.state, 'waiting-answer');
  assert.equal(controller.category, 'connecting');

  // Verify 'oai-events' data channel created
  const dc = controller.getDataChannel();
  assert.ok(dc);
  assert.equal(dc?.label, 'oai-events');

  // Apply remote answer
  const answerSdp = 'v=0\r\no=- 54321 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n';
  await controller.acceptAnswer(answerSdp);
  assert.equal(ctx.lastPc!.remoteDescription?.sdp, answerSdp);

  // Peer connection connects
  ctx.lastPc!.connectionState = 'connected';
  ctx.lastPc!.emit('connectionstatechange');
  assert.equal(controller.state, 'connected');
  assert.equal(controller.category, 'connected');

  // Data channel send works
  const sent = controller.sendDataChannelMessage('test-event');
  assert.equal(sent, true);
  assert.deepEqual((dc as MockDataChannel).sentData, ['test-event']);

  // Remote track arrives and plays through sink
  const remoteTrack = new MockTrack();
  ctx.lastPc!.emit('track', { track: remoteTrack });
  assert.equal(ctx.sink.played, true);
  assert.deepEqual(ctx.sink.srcObject.getTracks(), [remoteTrack]);

  // Normal dispose cleanly shuts down
  controller.dispose();
  assert.equal(controller.state, 'closed');
  assert.equal(controller.category, 'closed');
  assert.equal(ctx.lastPc!.closed, true);
  assert.equal(ctx.sink.paused, true);
  for (const t of ctx.mockTracks) {
    assert.equal(t.stopped, true);
  }
  assert.equal(remoteTrack.stopped, true);
});

test('setMuted toggles local audio track enabled without breaking connection', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env, audioSink: ctx.sink });

  await controller.prepare();
  await controller.acceptAnswer('v=0\r\ns=-\r\n');
  ctx.lastPc!.connectionState = 'connected';
  ctx.lastPc!.emit('connectionstatechange');
  assert.equal(controller.state, 'connected');
  assert.equal(controller.isMuted, false);

  // Mute
  controller.setMuted(true);
  assert.equal(controller.isMuted, true);
  for (const t of ctx.mockTracks) {
    assert.equal(t.enabled, false);
  }
  assert.equal(controller.state, 'connected'); // Connection intact

  // Unmute
  controller.setMuted(false);
  assert.equal(controller.isMuted, false);
  for (const t of ctx.mockTracks) {
    assert.equal(t.enabled, true);
  }
  assert.equal(controller.state, 'connected');

  controller.dispose();
});

test('repeated stop/dispose is idempotent', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env });
  await controller.prepare();

  const states: string[] = [];
  controller.onStateChange((s) => states.push(s.state));

  controller.dispose();
  const countAfterFirstDispose = states.length;

  controller.dispose();
  controller.stop();
  controller.dispose();

  assert.equal(controller.state, 'closed');
  assert.equal(states.length, countAfterFirstDispose, 'No redundant state events on repeated dispose');
});

test('single-use: controller rejects reuse after prepare, close or error', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env });
  await controller.prepare();

  // Cannot prepare again
  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'invalid-state');
    return true;
  });

  controller.dispose();

  // Cannot prepare or acceptAnswer after dispose
  await assert.rejects(controller.prepare(), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'invalid-state');
    return true;
  });

  await assert.rejects(controller.acceptAnswer('v=0\r\n'), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'invalid-state');
    return true;
  });
});

test('late answer or events do not revive after dispose', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env, audioSink: ctx.sink });
  await controller.prepare();

  controller.dispose();
  assert.equal(controller.state, 'closed');

  // Late answer rejected
  await assert.rejects(controller.acceptAnswer('v=0\r\n'), (err: any) => {
    assert.ok(err instanceof VoiceMediaException);
    assert.equal(err.code, 'invalid-state');
    return true;
  });

  // Late connection event ignored
  ctx.lastPc!.connectionState = 'connected';
  ctx.lastPc!.emit('connectionstatechange');
  assert.equal(controller.state, 'closed');

  // Late track event ignored
  const lateTrack = new MockTrack();
  ctx.lastPc!.emit('track', { track: lateTrack });
  assert.equal(ctx.sink.played, false);
  assert.equal(controller.state, 'closed');
});

test('peer connection failure releases resources and reports connection-failed', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env, audioSink: ctx.sink });
  await controller.prepare();
  await controller.acceptAnswer('v=0\r\n');
  ctx.lastPc!.connectionState = 'connected';
  ctx.lastPc!.emit('connectionstatechange');

  // Remote track active
  const remoteTrack = new MockTrack();
  ctx.lastPc!.emit('track', { track: remoteTrack });
  assert.equal(ctx.sink.played, true);

  // Network drops: connectionState becomes failed
  ctx.lastPc!.connectionState = 'failed';
  ctx.lastPc!.emit('connectionstatechange');

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'connection-failed');

  // Resources cleaned up
  assert.equal(ctx.lastPc!.closed, true);
  for (const t of ctx.mockTracks) {
    assert.equal(t.stopped, true);
  }
  assert.equal(remoteTrack.stopped, true);
  assert.equal(ctx.sink.paused, true);
});

test('playback blocked releases resources and reports playback-blocked', async () => {
  const ctx = createTestContext();
  ctx.sink.playFail = true; // Browser autoplay / permission blocked audio.play()

  const controller = new VoiceMediaController({ env: ctx.env, audioSink: ctx.sink });
  await controller.prepare();
  await controller.acceptAnswer('v=0\r\n');

  const remoteTrack = new MockTrack();
  // Remote track arrives, triggering sink.play() which rejects
  ctx.lastPc!.emit('track', { track: remoteTrack });

  // Allow microtask to process async handleRemoteTrack
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'playback-blocked');

  // Resources must be released
  assert.equal(ctx.lastPc!.closed, true);
  for (const t of ctx.mockTracks) {
    assert.equal(t.stopped, true);
  }
});

test('audio track ended event triggers cleanup and reports track-ended', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env, audioSink: ctx.sink });
  await controller.prepare();
  await controller.acceptAnswer('v=0\r\n');
  ctx.lastPc!.connectionState = 'connected';
  ctx.lastPc!.emit('connectionstatechange');

  // Local track hardware unplugged / ended
  ctx.mockTracks[0].emit('ended');

  assert.equal(controller.state, 'error');
  assert.equal(controller.category, 'error');
  assert.equal(controller.error?.code, 'track-ended');

  assert.equal(ctx.lastPc!.closed, true);
});

test('dispose promptly rejects a pending permission request without waiting for the permission dialog', async () => {
  const ctx = createTestContext({ immediateStream: false });
  const controller = new VoiceMediaController({ env: ctx.env });
  const pending = assert.rejects(controller.prepare(), (e: any) => e.code === 'aborted');
  controller.dispose();
  await pending;
  ctx.getUserMediaPending!.resolve(ctx.mockStream);
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(ctx.mockTracks.every(track => track.stopped));
});

test('dispose while an answer is being applied rejects cleanly and never reads a released peer', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env });
  await controller.prepare();
  let finish!: () => void;
  ctx.lastPc!.setRemoteDescription = () => new Promise(resolve => { finish = resolve; });
  const answer = assert.rejects(controller.acceptAnswer('answer'), (e: any) => e.code === 'aborted');
  controller.dispose();
  await answer;
  finish();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.state, 'closed');
  assert.equal(ctx.lastPc!.closed, true);
});

test('ICE connectivity alone does not claim the peer media transport is connected', async () => {
  const ctx = createTestContext();
  const controller = new VoiceMediaController({ env: ctx.env });
  try {
    await controller.prepare();
    ctx.lastPc!.iceConnectionState = 'completed';
    ctx.lastPc!.emit('iceconnectionstatechange');
    await controller.acceptAnswer('answer');
    assert.equal(controller.state, 'waiting-answer');
    ctx.lastPc!.connectionState = 'connected';
    ctx.lastPc!.emit('connectionstatechange');
    assert.equal(controller.state, 'connected');
  } finally { controller.dispose(); }
});

test('a state subscriber can dispose before capture or peer allocation without resurrecting resources', async () => {
  for (const state of ['requesting-permission', 'gathering-ice']) {
    const ctx = createTestContext();
    let captures = 0;
    ctx.env.mediaDevices!.getUserMedia = async () => { captures++; return ctx.mockStream; };
    const controller = new VoiceMediaController({ env: ctx.env });
    controller.onStateChange(snapshot => { if (snapshot.state === state) controller.dispose(); });
    await assert.rejects(controller.prepare(), (e: any) => e.code === 'aborted');
    assert.equal(controller.state, 'closed');
    assert.equal(ctx.lastPc, null);
    if (state === 'requesting-permission') assert.equal(captures, 0);
    else assert.ok(ctx.mockTracks.every(track => track.stopped));
  }
});

test('dispose aborts a hung offer and prevents a late offer from setting local description', async () => {
  const ctx = createTestContext();
  let finish!: (value: { sdp: string; type: string }) => void;
  const peer = new MockPeerConnection();
  peer.createOffer = () => new Promise(resolve => { finish = resolve; });
  ctx.env.RTCPeerConnection = function () { return peer; } as any;
  const controller = new VoiceMediaController({ env: ctx.env });
  const pending = assert.rejects(controller.prepare(), (e: any) => e.code === 'aborted');
  await new Promise(resolve => setImmediate(resolve));
  controller.dispose();
  await pending;
  finish({ sdp: 'late-offer', type: 'offer' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(peer.localDescription, null);
  assert.equal(peer.closed, true);
});

test('snapshot errors omit private browser diagnostics', async () => {
  const ctx = createTestContext();
  ctx.env.mediaDevices!.getUserMedia = async () => { throw Object.assign(new Error('SECRET_DEVICE_IDENTIFIER'), { name: 'NotAllowedError' }); };
  const controller = new VoiceMediaController({ env: ctx.env });
  await assert.rejects(controller.prepare(), (e: any) => e.code === 'permission-denied' && !e.message.includes('SECRET_'));
  assert.ok(!JSON.stringify(controller.snapshot).includes('SECRET_'));
});
