/**
 * VoiceMediaController: Browser-side WebRTC and audio lifecycle controller.
 *
 * Implements bounded media acquisition and WebRTC transport for real-time voice sessions.
 * - Single-use: once closed or errored, cannot be restarted (create a new instance for a new session).
 * - Explicit lifecycle: default constructor touches no media; prepare() triggers getUserMedia in user gesture stack.
 * - Mute control: toggles input audio track enabled property without dropping connection.
 * - Memory-only: SDP and media streams are kept strictly in memory; never logged or saved to storage.
 * - Injectable environment: supports Node.js unit testing with mock browser media APIs.
 */

export type VoiceMediaCategory = 'idle' | 'connecting' | 'connected' | 'closed' | 'error';

export type VoiceMediaState =
  | 'idle'
  | 'requesting-permission'
  | 'gathering-ice'
  | 'waiting-answer'
  | 'connected'
  | 'closed'
  | 'error';

export type VoiceMediaErrorCode =
  | 'permission-denied'
  | 'insecure-context'
  | 'not-supported'
  | 'ice-timeout'
  | 'sdp-failed'
  | 'playback-blocked'
  | 'connection-failed'
  | 'track-ended'
  | 'invalid-state'
  | 'aborted';

export interface VoiceMediaError {
  readonly code: VoiceMediaErrorCode;
  readonly message: string;
  readonly cause?: unknown;
}

export interface VoiceMediaSnapshot {
  readonly category: VoiceMediaCategory;
  readonly state: VoiceMediaState;
  readonly isMuted: boolean;
  readonly error?: VoiceMediaError;
}

export type VoiceMediaStateListener = (snapshot: VoiceMediaSnapshot) => void;

export class VoiceMediaException extends Error {
  readonly code: VoiceMediaErrorCode;
  readonly cause?: unknown;

  constructor(code: VoiceMediaErrorCode, message: string, cause?: unknown) {
    super(message);
    this.name = 'VoiceMediaException';
    this.code = code;
    this.cause = cause;
  }
}

export interface MediaTrackLike {
  kind: string;
  enabled: boolean;
  stop?(): void;
  addEventListener?(type: string, listener: (ev: any) => void): void;
  removeEventListener?(type: string, listener: (ev: any) => void): void;
}

export interface MediaStreamLike {
  getTracks?(): MediaTrackLike[];
  getAudioTracks?(): MediaTrackLike[];
}

export interface RTCDataChannelLike {
  readonly label: string;
  readonly readyState: string;
  send(data: string | Blob | ArrayBuffer | ArrayBufferView): void;
  close(): void;
  addEventListener?(type: string, listener: (ev: any) => void): void;
  removeEventListener?(type: string, listener: (ev: any) => void): void;
  onopen?: ((ev: any) => void) | null;
  onclose?: ((ev: any) => void) | null;
  onerror?: ((ev: any) => void) | null;
  onmessage?: ((ev: any) => void) | null;
}

export interface RTCPeerConnectionLike {
  readonly connectionState?: string;
  readonly iceConnectionState?: string;
  readonly iceGatheringState?: string;
  readonly localDescription?: { readonly sdp: string; readonly type: string } | null;
  createDataChannel(label: string, dataChannelDict?: any): RTCDataChannelLike;
  addTrack(track: MediaTrackLike, ...streams: any[]): any;
  createOffer(options?: any): Promise<{ sdp: string; type: string }>;
  setLocalDescription(description: any): Promise<void>;
  setRemoteDescription(description: any): Promise<void>;
  close(): void;
  addEventListener?(type: string, listener: (ev: any) => void): void;
  removeEventListener?(type: string, listener: (ev: any) => void): void;
}

export interface AudioPlaybackSink {
  srcObject: any;
  play(): Promise<void> | void;
  pause?(): void;
}

export interface VoiceMediaEnv {
  mediaDevices?: {
    getUserMedia(constraints?: any): Promise<MediaStreamLike>;
  };
  isSecureContext?: boolean;
  RTCPeerConnection?: new (config?: any) => RTCPeerConnectionLike;
  createAudioElement?: () => AudioPlaybackSink;
  createMediaStream?: (tracks: MediaTrackLike[]) => MediaStreamLike;
  setTimeout?: (handler: () => void, timeout?: number) => any;
  clearTimeout?: (handle: any) => void;
}

export interface VoiceMediaOptions {
  env?: Partial<VoiceMediaEnv>;
  audioSink?: AudioPlaybackSink;
  iceTimeoutMs?: number;
  rtcConfig?: any;
  audioConstraints?: any;
  onStateChange?: VoiceMediaStateListener;
  onRemoteTrack?: (track: MediaTrackLike) => void;
  onDataChannel?: (channel: RTCDataChannelLike) => void;
}

const DEFAULT_ICE_TIMEOUT_MS = 5000;

function resolveBrowserEnv(custom?: Partial<VoiceMediaEnv>): VoiceMediaEnv {
  const hasWindow = typeof window !== 'undefined';
  const hasNav = typeof navigator !== 'undefined';

  return {
    mediaDevices: custom?.mediaDevices ?? (hasNav ? navigator.mediaDevices : undefined),
    isSecureContext: custom?.isSecureContext ?? (hasWindow ? window.isSecureContext : undefined),
    RTCPeerConnection:
      custom?.RTCPeerConnection ??
      (hasWindow ? (window as any).RTCPeerConnection : typeof RTCPeerConnection !== 'undefined' ? RTCPeerConnection : undefined),
    createAudioElement:
      custom?.createAudioElement ?? (typeof Audio !== 'undefined' ? () => new Audio() : undefined),
    createMediaStream: custom?.createMediaStream ?? (typeof MediaStream !== 'undefined' ? tracks => new MediaStream(tracks as MediaStreamTrack[]) : undefined),
    setTimeout: custom?.setTimeout ?? ((handler, timeout) => globalThis.setTimeout(handler, timeout)),
    clearTimeout: custom?.clearTimeout ?? (handle => globalThis.clearTimeout(handle)),
  };
}

/** Check if the current environment supports WebRTC voice media. */
export function isVoiceMediaSupported(customEnv?: Partial<VoiceMediaEnv>): boolean {
  const env = resolveBrowserEnv(customEnv);
  if (env.isSecureContext === false) return false;
  return Boolean(env.mediaDevices?.getUserMedia && env.RTCPeerConnection);
}

export class VoiceMediaController {
  private readonly env: VoiceMediaEnv;
  private readonly options: VoiceMediaOptions;
  private readonly iceTimeoutMs: number;
  private readonly rtcConfig: any;
  private readonly listeners = new Set<VoiceMediaStateListener>();

  private _category: VoiceMediaCategory = 'idle';
  private _state: VoiceMediaState = 'idle';
  private _isMuted = false;
  private _error?: VoiceMediaError;

  private disposed = false;
  private prepareCalled = false;
  private cancelPending!: () => void;
  private readonly cancellation = new Promise<void>(resolve => { this.cancelPending = resolve; });

  private audioContext: AudioContext | null = null;
  private meters: Partial<Record<'input' | 'output', { source: MediaStreamAudioSourceNode; analyser: AnalyserNode; data: Float32Array<ArrayBuffer>; level: number }>> = {};

  private meter(stream: MediaStreamLike, side: 'input' | 'output'): void {
    if (typeof AudioContext === 'undefined') return;
    try {
      this.audioContext ??= new AudioContext();
      void this.audioContext.resume().catch(() => {});
      this.meters[side]?.source.disconnect();
      const source = this.audioContext.createMediaStreamSource(stream as MediaStream);
      const analyser = this.audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      this.meters[side] = { source, analyser, data: new Float32Array(256), level: 0 };
    } catch { /* Metering is optional; a visual failure must not interrupt speech. */ }
  }

  /** Sampled by the orb's animation frame, never put in React state. */
  levels(): { input: number; output: number } {
    const read = (side: 'input' | 'output') => {
      const meter = this.meters[side];
      if (!meter) return 0;
      meter.analyser.getFloatTimeDomainData(meter.data);
      const rms = Math.sqrt(meter.data.reduce((n, v) => n + v * v, 0) / meter.data.length);
      meter.level = meter.level * .72 + Math.min(1, rms * 5) * .28;
      return side === 'input' && this._isMuted ? 0 : meter.level;
    };
    return { input: read('input'), output: read('output') };
  }

  private localStream: MediaStreamLike | null = null;
  private remoteTrack: MediaTrackLike | null = null;
  private pc: RTCPeerConnectionLike | null = null;
  private dataChannel: RTCDataChannelLike | null = null;
  private audioSink: AudioPlaybackSink | null = null;
  private pendingIceCleanup: (() => void) | null = null;

  constructor(options: VoiceMediaOptions = {}) {
    this.options = options;
    this.env = resolveBrowserEnv(options.env);
    this.iceTimeoutMs = Math.max(100, options.iceTimeoutMs ?? DEFAULT_ICE_TIMEOUT_MS);
    this.rtcConfig = options.rtcConfig ?? { iceServers: [] };
    if (options.onStateChange) {
      this.listeners.add(options.onStateChange);
    }
  }

  get category(): VoiceMediaCategory {
    return this._category;
  }

  get state(): VoiceMediaState {
    return this._state;
  }

  get isMuted(): boolean {
    return this._isMuted;
  }

  get error(): VoiceMediaError | undefined {
    return this._error;
  }

  get isDisposed(): boolean {
    return this.disposed;
  }

  get snapshot(): VoiceMediaSnapshot {
    return {
      category: this._category,
      state: this._state,
      isMuted: this._isMuted,
      error: this._error,
    };
  }

  getDataChannel(): RTCDataChannelLike | null {
    return this.dataChannel;
  }

  onStateChange(listener: VoiceMediaStateListener): () => void {
    this.listeners.add(listener);
    try { listener(this.snapshot); } catch { /* Consumers cannot interrupt resource cleanup. */ }
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emitState(): void {
    const snap = this.snapshot;
    for (const listener of this.listeners) {
      try {
        listener(snap);
      } catch {}
    }
  }

  private transition(category: VoiceMediaCategory, state: VoiceMediaState, error?: VoiceMediaError): void {
    if (this._category === category && this._state === state && this._error === error) {
      return;
    }
    this._category = category;
    this._state = state;
    if (error !== undefined) {
      this._error = error;
    }
    this.emitState();
  }

  private fail(code: VoiceMediaErrorCode, message: string, cause?: unknown): void {
    if (this.disposed) return;
    const error: VoiceMediaError = { code, message };
    this._error = error;
    this.disposed = true;
    this.cancelPending();
    this.cleanupResources();
    this.transition('error', 'error', error);
  }

  private assertLive(): void {
    if (this.disposed) throw new VoiceMediaException('aborted', 'Voice media controller was disposed');
  }

  /** Browser promises cannot always be cancelled; release a late resource without holding up stop. */
  private awaitLive<T>(pending: Promise<T>, releaseLate?: (value: T) => void): Promise<T> {
    return Promise.race([
      pending.then(value => {
        if (this.disposed) { releaseLate?.(value); this.assertLive(); }
        return value;
      }),
      this.cancellation.then(() => { throw new VoiceMediaException('aborted', 'Voice media controller was disposed'); }),
    ]);
  }

  private stopTracks(stream: MediaStreamLike): void {
    for (const track of stream.getTracks?.() ?? stream.getAudioTracks?.() ?? []) {
      try { track.stop?.(); } catch { /* Continue releasing the remaining tracks. */ }
    }
  }

  private getOrCreateAudioSink(): AudioPlaybackSink | null {
    if (this.audioSink) return this.audioSink;
    if (this.options.audioSink) {
      this.audioSink = this.options.audioSink;
      return this.audioSink;
    }
    if (this.env.createAudioElement) {
      try {
        this.audioSink = this.env.createAudioElement();
        return this.audioSink;
      } catch {}
    }
    return null;
  }

  private cleanupResources(): void {
    for (const meter of Object.values(this.meters)) { meter.source.disconnect(); meter.analyser.disconnect(); }
    this.meters = {};
    void this.audioContext?.close().catch(() => {});
    this.audioContext = null;
    if (this.pendingIceCleanup) {
      try {
        this.pendingIceCleanup();
      } catch {}
      this.pendingIceCleanup = null;
    }

    if (this.localStream) {
      const tracks = this.localStream.getTracks?.() ?? this.localStream.getAudioTracks?.() ?? [];
      for (const track of tracks) {
        try {
          track.stop?.();
        } catch {}
      }
      this.localStream = null;
    }

    if (this.remoteTrack) {
      try {
        this.remoteTrack.stop?.();
      } catch {}
      this.remoteTrack = null;
    }

    if (this.audioSink) {
      try {
        this.audioSink.pause?.();
      } catch {}
      try {
        this.audioSink.srcObject = null;
      } catch {}
      this.audioSink = null;
    }

    if (this.dataChannel) {
      try {
        this.dataChannel.close();
      } catch {}
      this.dataChannel = null;
    }

    if (this.pc) {
      try {
        this.pc.close();
      } catch {}
      this.pc = null;
    }
  }

  /**
   * Prepares the media session: requests microphone access, sets up RTCPeerConnection
   * with 'oai-events' data channel, creates SDP offer and gathers ICE candidates.
   * Must be called in a user gesture callstack. Returns the SDP offer string.
   */
  async prepare(): Promise<string> {
    if (this.prepareCalled || this.disposed || this._state !== 'idle') {
      const ex = new VoiceMediaException(
        'invalid-state',
        'VoiceMediaController is single-use and cannot be prepared more than once'
      );
      throw ex;
    }
    this.prepareCalled = true;

    this.transition('connecting', 'requesting-permission');
    this.assertLive();

    // Check secure context
    if (this.env.isSecureContext === false) {
      const ex = new VoiceMediaException(
        'insecure-context',
        'Microphone access requires a secure context (HTTPS or localhost)'
      );
      this.fail('insecure-context', ex.message, ex);
      throw ex;
    }

    const getUserMedia = this.env.mediaDevices?.getUserMedia;
    const RTCPeerConnectionClass = this.env.RTCPeerConnection;
    if (!getUserMedia || !RTCPeerConnectionClass) {
      const code: VoiceMediaErrorCode = 'not-supported';
      const ex = new VoiceMediaException(code, 'WebRTC or getUserMedia is not supported by this browser');
      this.fail(code, ex.message, ex);
      throw ex;
    }

    // Request user media
    let stream: MediaStreamLike;
    try {
      const constraints = this.options.audioConstraints ?? {
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      };
      stream = await this.awaitLive(getUserMedia.call(this.env.mediaDevices, constraints), late => this.stopTracks(late));
    } catch (err: any) {
      if (this.disposed) {
        throw new VoiceMediaException('aborted', 'Voice media controller was disposed before permission was granted');
      }
      const code: VoiceMediaErrorCode =
        err?.name === 'NotAllowedError' || err?.name === 'PermissionDeniedError'
          ? 'permission-denied'
          : err?.name === 'NotFoundError' || err?.name === 'DevicesNotFoundError'
          ? 'not-supported'
          : 'permission-denied';
      const ex = new VoiceMediaException(code, 'Microphone access failed');
      this.fail(code, ex.message, err);
      throw ex;
    }

    // If disposed while getUserMedia was awaiting, stop all acquired tracks immediately
    if (this.disposed) {
      const tracks = stream.getTracks?.() ?? stream.getAudioTracks?.() ?? [];
      for (const track of tracks) {
        try {
          track.stop?.();
        } catch {}
      }
      throw new VoiceMediaException('aborted', 'Voice media controller was disposed before permission was granted');
    }

    this.localStream = stream;
    this.meter(stream, 'input');
    const localTracks = stream.getAudioTracks?.() ?? stream.getTracks?.() ?? [];
    for (const track of localTracks) {
      track.enabled = !this._isMuted;
      track.addEventListener?.('ended', () => {
        if (this.disposed) return;
        this.fail('track-ended', 'Microphone audio track ended');
      });
    }

    this.transition('connecting', 'gathering-ice');
    this.assertLive();

    // Create peer connection
    let pc: RTCPeerConnectionLike;
    try {
      pc = new RTCPeerConnectionClass(this.rtcConfig);
      this.pc = pc;
    } catch (err: any) {
      const ex = new VoiceMediaException('sdp-failed', 'Failed to construct RTCPeerConnection', err);
      this.fail('sdp-failed', ex.message, err);
      throw ex;
    }

    // Create oai-events data channel
    try {
      this.dataChannel = pc.createDataChannel('oai-events');
      this.options.onDataChannel?.(this.dataChannel);
      this.assertLive();
    } catch (err: any) {
      const ex = new VoiceMediaException('sdp-failed', 'Failed to create oai-events data channel', err);
      this.fail('sdp-failed', ex.message, err);
      throw ex;
    }

    // Add local tracks
    for (const track of localTracks) {
      try {
        pc.addTrack(track, stream);
      } catch (err: any) {
        const ex = new VoiceMediaException('sdp-failed', 'Failed to add audio track to peer connection', err);
        this.fail('sdp-failed', ex.message, err);
        throw ex;
      }
    }

    // Remote track listener
    pc.addEventListener?.('track', (event: any) => {
      if (this.disposed) { event.track?.stop?.(); return; }
      void this.handleRemoteTrack(event);
    });

    // Connection state listeners
    pc.addEventListener?.('connectionstatechange', () => {
      if (this.disposed) return;
      const s = pc.connectionState;
      if (s === 'connected') {
        this.transition('connected', 'connected');
      } else if (s === 'disconnected' || s === 'failed') {
        this.fail('connection-failed', `WebRTC connection ${s}`);
      } else if (s === 'closed') {
        if (!this.disposed) {
          this.dispose();
        }
      }
    });

    pc.addEventListener?.('iceconnectionstatechange', () => {
      if (this.disposed) return;
      const s = pc.iceConnectionState;
      // ICE alone is not proof that DTLS/media connected; use connectionState above.
      if (s === 'failed') {
        this.fail('connection-failed', `WebRTC ICE connection ${s}`);
      }
    });

    // Create SDP offer
    try {
      const offer = await this.awaitLive(pc.createOffer());
      this.assertLive();
      await this.awaitLive(pc.setLocalDescription(offer));
      this.assertLive();
    } catch (err: any) {
      this.assertLive();
      const ex = new VoiceMediaException('sdp-failed', 'Failed to create SDP offer', err);
      this.fail('sdp-failed', ex.message, err);
      throw ex;
    }

    // Wait for ICE gathering
    try {
      await this.waitForIceGathering(pc);
    } catch (err: any) {
      const code: VoiceMediaErrorCode = err instanceof VoiceMediaException ? err.code : 'ice-timeout';
      this.fail(code, err?.message ?? 'ICE gathering failed', err);
      throw err;
    }

    if (this.disposed) {
      throw new VoiceMediaException('aborted', 'Voice media controller was disposed during ICE gathering');
    }

    const sdp = pc.localDescription?.sdp;
    if (!sdp) {
      const ex = new VoiceMediaException('sdp-failed', 'Local description SDP is missing');
      this.fail('sdp-failed', ex.message, ex);
      throw ex;
    }

    this.transition('connecting', 'waiting-answer');
    return sdp;
  }

  private waitForIceGathering(pc: RTCPeerConnectionLike): Promise<void> {
    this.assertLive();
    if (pc.iceGatheringState === 'complete') {
      return Promise.resolve();
    }

    return new Promise<void>((resolve, reject) => {
      let settled = false;

      const finish = () => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve();
      };

      const onTimeout = () => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new VoiceMediaException('ice-timeout', `ICE gathering timed out after ${this.iceTimeoutMs}ms`));
      };

      const timer = this.env.setTimeout!(onTimeout, this.iceTimeoutMs);

      const onGatheringState = () => {
        if (pc.iceGatheringState === 'complete') {
          finish();
        }
      };

      const onIceCandidate = (ev: any) => {
        if (!ev.candidate) {
          finish();
        }
      };

      const cleanup = () => {
        this.env.clearTimeout!(timer);
        pc.removeEventListener?.('icegatheringstatechange', onGatheringState);
        pc.removeEventListener?.('icecandidate', onIceCandidate);
        this.pendingIceCleanup = null;
      };

      this.pendingIceCleanup = () => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new VoiceMediaException('aborted', 'Disposed during ICE gathering'));
      };

      pc.addEventListener?.('icegatheringstatechange', onGatheringState);
      pc.addEventListener?.('icecandidate', onIceCandidate);
    });
  }

  /**
   * Accepts the remote SDP answer string and applies it to the peer connection.
   */
  async acceptAnswer(sdp: string): Promise<void> {
    if (this.disposed || this._state === 'closed' || this._state === 'error') {
      throw new VoiceMediaException('invalid-state', 'Cannot accept answer on closed or errored controller');
    }
    if (this._state !== 'waiting-answer' || !this.pc) {
      throw new VoiceMediaException('invalid-state', `Cannot accept answer in current state: ${this._state}`);
    }
    if (!sdp || typeof sdp !== 'string') {
      throw new VoiceMediaException('sdp-failed', 'Invalid SDP answer string');
    }

    const pc = this.pc;
    try {
      await this.awaitLive(pc.setRemoteDescription({ type: 'answer', sdp }));
    } catch (err: any) {
      this.assertLive();
      const ex = new VoiceMediaException('sdp-failed', 'Failed to apply remote SDP answer', err);
      this.fail('sdp-failed', ex.message, err);
      throw ex;
    }

    this.assertLive();
    if (pc.connectionState === 'connected') {
      this.transition('connected', 'connected');
    }
  }

  private async handleRemoteTrack(event: any): Promise<void> {
    if (this.disposed) return;
    const track: MediaTrackLike = event.track;
    if (!track) return;

    this.remoteTrack = track;
    track.addEventListener?.('ended', () => {
      if (this.disposed) return;
      this.fail('track-ended', 'Remote audio track ended');
    });

    try { this.options.onRemoteTrack?.(track); } catch { /* Observers do not own the connection. */ }
    if (this.disposed) return;

    const sink = this.getOrCreateAudioSink();
    if (sink) {
      try {
        if (event.streams && event.streams[0]) {
          sink.srcObject = event.streams[0];
        } else {
          if (!this.env.createMediaStream) throw new Error('MediaStream is unavailable');
          sink.srcObject = this.env.createMediaStream([track]);
        }
        this.meter(sink.srcObject, 'output');
        const p = sink.play();
        if (p && typeof (p as any).then === 'function') {
          await p;
        }
      } catch (err: any) {
        if (this.disposed) return;
        this.fail('playback-blocked', 'Audio playback blocked by browser', err);
      }
    } else this.fail('not-supported', 'Audio playback is unavailable');
  }

  /**
   * Toggles mute state of the local microphone track.
   * Does NOT destroy connection or cancel background tasks.
   */
  setMuted(muted: boolean): void {
    if (this.disposed || this._state === 'closed' || this._state === 'error') {
      return;
    }
    this._isMuted = Boolean(muted);
    if (this.localStream) {
      const tracks = this.localStream.getAudioTracks?.() ?? this.localStream.getTracks?.() ?? [];
      for (const track of tracks) {
        track.enabled = !this._isMuted;
      }
    }
    this.emitState();
  }

  /**
   * Sends a message over the WebRTC 'oai-events' data channel if open.
   */
  sendDataChannelMessage(data: string): boolean {
    if (this.disposed || !this.dataChannel || this.dataChannel.readyState !== 'open') {
      return false;
    }
    try {
      this.dataChannel.send(data);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Stops all media tracks, closes data channel and RTCPeerConnection, and pauses playback.
   * Idempotent; calling dispose repeatedly is safe and a no-op after the first call.
   */
  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.cancelPending();
    this.cleanupResources();
    if (this._state !== 'error') {
      this.transition('closed', 'closed');
    }
  }

  /** Alias for dispose(). */
  stop(): void {
    this.dispose();
  }
}
