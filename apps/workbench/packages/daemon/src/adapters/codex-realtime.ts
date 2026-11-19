import type { JsonRpcPeer } from './jsonrpc.js';
import type { VoiceContext } from '../voice-sessions.js';

/**
 * Controller for Codex's experimental thread realtime (voice) sessions: protocol v3, audio output,
 * WebRTC transport — the one combination verified against a real app-server (0.160.1). It lives next
 * to a `CodexSession` on the same app-server peer but is independent of the turn lifecycle: turns
 * may start and finish while a realtime session is up, and stopping voice never touches turns.
 *
 * The host owns the process and wires three entry points to its existing peer:
 * `handleNotification` (route `thread/realtime/*`), `handleClosed` (peer exit) and a transport
 * (see `peerTransport`). Nothing else of the peer is exposed through this class: there is no
 * generic RPC passthrough.
 *
 * Guarantees:
 * - Authentication is re-checked with `account/read` on every start; an API-key login is refused,
 *   never fallen back to.
 * - State is attached before the start RPC leaves, so notifications that race the acknowledgement
 *   (early error, SDP, closed) are handled; an acknowledgement is resolved to the host, but it is
 *   not "connected" — audio connectivity is the browser's peer connection, out of scope.
 * - One instance is one use: a settled (or disposed) instance refuses to start again. Native
 *   realtime notifications carry only the thread id — no session or generation marker the client
 *   could filter by — so a late `closed`/SDP of an earlier session on the same thread cannot
 *   always be told apart from the next session's events, and no local counter changes that.
 * - What IS guaranteed per instance: only notifications arriving between this session's own start
 *   RPC and its settlement are processed. Other threads, anything before the start RPC was
 *   written, and anything after settling are dropped. Across sessions on the same thread,
 *   isolation is only as good as the thread id: the stopping phase consumes the trailing
 *   `closed` (bounded by closeTimeoutMs), so a fresh instance on the same thread is safe after a
 *   clean close; after an unclean one (close timeout, crash), the host must hand the next session
 *   a fresh native thread, where the different thread id filters the leftovers outright.
 * - Failures surface as classification tokens only. SDP and transcripts are forwarded to the host
 *   and never stored in state; raw upstream messages and credentials are neither kept nor logged.
 *
 * For chat-while-working: the connection lifecycle uses three native requests —
 * `account/read`, `thread/realtime/start`, `thread/realtime/stop` — so starting voice waits only
 * for the control handshake, never for background tasks; transcripts are forwarded as signals and
 * never re-prompted (any delegation is the host's separate decision); and stop never touches
 * turn/interrupt, so background work keeps running under its own authorization. Task identity,
 * async results and continuous chat are the coordinator's to integrate; nothing here claims that
 * native delegation itself has been verified.
 */

export type RealtimePhase =
  | 'idle' // never started
  | 'starting' // authenticating or the start RPC is in flight
  | 'signaling' // start acknowledged; waiting for `started` and the SDP answer
  | 'active' // server accepted the session and answered SDP; still not proof of audio
  | 'stopping' // stop requested or failure cleanup; the trailing `closed` is being consumed
  | 'closed' // ended by stop or dispose (or stop timed out waiting for `closed`)
  | 'failed'; // ended by an error; `state.error` says which kind

export type RealtimeFailure =
  // Classified from upstream responses and `thread/realtime/error`.
  | 'api_key_auth_required'
  | 'text_requires_v2'
  | 'webrtc_required'
  | 'unsupported'
  | 'authentication_failed'
  | 'access_denied'
  | 'timeout'
  | 'native_error'
  // Produced by this controller.
  | 'chatgpt_login_required' // account/read says the login is not ChatGPT (e.g. API key)
  | 'process_closed' // the app-server process went away
  | 'signaling_timeout' // acknowledged, but `started`/SDP never arrived in time
  | 'closed_by_server' // the server closed the session on its own
  | 'session_busy' // another start/stop sequence is in progress
  | 'session_ended' // this instance already ran (or was disposed); create a fresh one
  | 'stopped'; // the caller's own stop()/dispose() ended a pending start

export interface RealtimeState {
  phase: RealtimePhase;
  /** The `thread/realtime/started` notification arrived for this session. */
  serverAccepted: boolean;
  /** An SDP answer arrived for this session. Its content is never part of this state. */
  sdpAnswered: boolean;
  /** Failure kind when the session ended badly; null otherwise. */
  error: RealtimeFailure | null;
}

/**
 * Session events for the host, structurally compatible with the coordinator's `VoiceSignal`
 * (voice-sessions.ts); the `error` variant additionally carries the classification for logs.
 *
 * Canonical v3 timeline items retain their own `itemId` and verified user/assistant role.
 * Deltas append; a final carries the complete text, including an authoritative empty string.
 * Flat transcripts have no identity and are deliberately ignored. Promoted task work and raw
 * `item/added` payloads also never become chat captions.
 */
export type RealtimeTranscriptRole = 'user' | 'assistant';
export type RealtimeSignal =
  | { type: 'started' }
  | { type: 'sdp'; sdp: string }
  | { type: 'transcript'; itemId: string; text: string; final: boolean; role?: RealtimeTranscriptRole }
  | { type: 'error'; reason: RealtimeFailure }
  | { type: 'closed' };

/** What the controller needs from the app-server: requests, and whether the process is gone. */
export interface RealtimeTransport {
  request(method: string, params: unknown): Promise<unknown>;
  readonly isClosed: boolean;
}

/** Adapts the shared session peer; notifications and closure are routed by the host. */
export function peerTransport(peer: JsonRpcPeer): RealtimeTransport {
  return {
    request: (method, params) => peer.request(method, params),
    get isClosed() {
      return peer.isClosed;
    },
  };
}

export interface RealtimeHandlers {
  /** Every state change. Snapshots carry no SDP, transcripts, session ids or raw upstream text. */
  onState?(state: RealtimeState): void;
  /** The start RPC was acknowledged: the native session request itself was accepted. */
  onAccepted?(): void;
  /** Live session events: started, SDP answers, transcript parts, classified errors, closure. */
  onSignal?(signal: RealtimeSignal): void;
}

export interface RealtimeOptions {
  /** Per-RPC budget, including the authentication read. Default 15s. */
  requestTimeoutMs?: number;
  /** From sending the start RPC until `started` plus SDP answer. Default 20s. */
  establishTimeoutMs?: number;
  /** Waiting for `thread/realtime/closed` after a stop before moving on anyway. Default 5s. */
  closeTimeoutMs?: number;
}

export interface RealtimeStartInput {
  /** SDP offer of the browser's RTCPeerConnection, audio track plus realtime events channel. */
  sdpOffer: string;
  context?: VoiceContext;
}

/** Carries only the classification token; safe to show, store and log. */
export class RealtimeError extends Error {
  constructor(readonly reason: RealtimeFailure) {
    super(reason);
  }
}

/**
 * Maps an upstream message to a stable token. Upstream messages may embed credentials or private
 * URLs, so the raw text is dropped here and never leaves this module.
 */
export function classifyRealtimeError(error: unknown): RealtimeFailure {
  const text = String((error as { message?: unknown } | null)?.message ?? error);
  if (/requires API key auth/i.test(text)) return 'api_key_auth_required';
  if (/text realtime output modality requires realtime v2/i.test(text)) return 'text_requires_v2';
  if (/requires.*webrtc|webrtc.*required/i.test(text)) return 'webrtc_required';
  if (/not supported|unsupported|method not found/i.test(text)) return 'unsupported';
  if (/401|unauthorized|authentication|not authenticated/i.test(text)) return 'authentication_failed';
  if (/403|forbidden|not entitled|access denied/i.test(text)) return 'access_denied';
  if (/timeout|timed out/i.test(text)) return 'timeout';
  return 'native_error';
}

/** The verified combination this controller speaks; other version/modality mixes are untested. */
export const REALTIME_PROFILE = { version: 'v3', outputModality: 'audio', transport: 'webrtc' } as const;

const REALTIME_NOTIFICATIONS = new Set([
  'thread/realtime/started', 'thread/realtime/sdp', 'thread/realtime/error', 'thread/realtime/closed',
  'thread/realtime/transcript/delta', 'thread/realtime/transcript/done',
  'thread/realtime/item/started', 'thread/realtime/item/transcript/delta', 'thread/realtime/item/completed',
]);
const LIVE_PHASES: RealtimePhase[] = ['starting', 'signaling', 'active'];

/** The two roles a transcript may carry. Everything else is not a verified chat caption. */
function transcriptRole(value: unknown): RealtimeTranscriptRole | null {
  return value === 'user' || value === 'assistant' ? value : null;
}
const ESTABLISHING_PHASES: RealtimePhase[] = ['starting', 'signaling'];
const BUSY_PHASES: RealtimePhase[] = ['starting', 'signaling', 'active', 'stopping'];

export class CodexRealtime {
  private snapshot: RealtimeState = { phase: 'idle', serverAccepted: false, sdpAnswered: false, error: null };
  /** Internal re-entrancy nonce for the one start an instance performs; not an event filter. */
  private attemptNonce = 0;
  /** True once this instance's single session has settled; further starts are refused. */
  private ended = false;
  /** True once this session's start RPC was written; earlier same-thread events are stale. */
  private startSent = false;
  /** Roles of live canonical v3 transcript items; deltas carry only their item id. */
  private readonly itemRoles = new Map<string, RealtimeTranscriptRole>();
  private resolveActive: ((state: RealtimeState) => void) | null = null;
  private rejectActive: ((error: RealtimeError) => void) | null = null;
  private establishTimer: ReturnType<typeof setTimeout> | null = null;
  private closeTimer: ReturnType<typeof setTimeout> | null = null;
  /** Released by `settle`, so a `closed` that beats the close timeout ends the stop wait at once. */
  private closeWaiter: (() => void) | null = null;
  private stoppingTask: Promise<RealtimeState> | null = null;
  private readonly options: Required<RealtimeOptions>;

  constructor(
    readonly threadId: string,
    private readonly transport: RealtimeTransport,
    private readonly handlers: RealtimeHandlers = {},
    options?: RealtimeOptions,
  ) {
    this.options = { requestTimeoutMs: 15_000, establishTimeoutMs: 20_000, closeTimeoutMs: 5_000, ...options };
  }

  get state(): RealtimeState {
    return { ...this.snapshot };
  }

  /**
   * Starts this instance's one native session. Resolves at `active` — the server accepted the
   * session and answered SDP, which is still not audio connectivity; hosts that only need the
   * acknowledgement should use `CodexVoiceTransport`. Rejects with a `RealtimeError` otherwise.
   */
  async start(input: RealtimeStartInput): Promise<RealtimeState> {
    if (typeof input?.sdpOffer !== 'string' || !input.sdpOffer) throw new Error('sdpOffer must be a non-empty string');
    if (this.ended || this.snapshot.phase === 'closed' || this.snapshot.phase === 'failed') {
      // Late native events carry only the thread id; start a fresh instance (ideally a fresh
      // native thread) instead of reusing a settled one.
      throw new RealtimeError('session_ended');
    }
    if (BUSY_PHASES.includes(this.snapshot.phase)) throw new RealtimeError('session_busy');
    if (this.transport.isClosed) throw new RealtimeError('process_closed');
    const attempt = this.attemptNonce + 1;
    this.attemptNonce = attempt;
    this.startSent = false;
    this.stoppingTask = null;
    this.itemRoles.clear();
    // The waiters exist before the first state emission: a subscriber may stop synchronously
    // from `onState`, which must settle this start instead of leaving it pending forever.
    const outcome = new Promise<RealtimeState>((resolve, reject) => {
      this.resolveActive = resolve;
      this.rejectActive = reject;
    });
    // A settle while this start is suspended at an await rejects `outcome` before the async
    // function returns it; without a handler that registers as an unhandled rejection.
    outcome.catch(() => undefined);
    this.update({ phase: 'starting', serverAccepted: false, sdpAnswered: false, error: null });
    if (this.snapshot.phase !== 'starting') return outcome; // stopped by a subscriber mid-emission
    // Still the same start attempt, and nothing has settled or stopped it meanwhile.
    const current = () => this.attemptNonce === attempt && this.snapshot.phase === 'starting';
    try {
      const account = await this.rpc('account/read', { refreshToken: false }) as { account?: { type?: unknown } };
      if (!current()) return outcome;
      if (account?.account?.type !== 'chatgpt') throw new RealtimeError('chatgpt_login_required');
      this.startSent = true;
      this.armEstablishment(attempt);
      await this.rpc('thread/realtime/start', {
        threadId: this.threadId,
        version: REALTIME_PROFILE.version,
        outputModality: REALTIME_PROFILE.outputModality,
        transport: { type: REALTIME_PROFILE.transport, sdp: input.sdpOffer },
        ...(input.context ? {
          realtimeStartInstructions: input.context.instructions,
          clientManagedHandoffs: true,
        } : {}),
      });
      if (!current()) return outcome;
      this.update({ phase: 'signaling' });
      this.safeCall(() => this.handlers.onAccepted?.());
      this.maybeActive();
    } catch (error) {
      if (current()) {
        if (this.transport.isClosed) this.settle('failed', 'process_closed');
        else if (error instanceof RealtimeError && error.reason === 'timeout') this.failWithStop('timeout');
        else this.settle('failed', error instanceof RealtimeError ? error.reason : classifyRealtimeError(error));
      }
    }
    return outcome;
  }

  async appendText(text: string): Promise<void> {
    if (this.ended || this.snapshot.phase !== 'active') throw new RealtimeError('session_ended');
    if (!text.trim() || text.length > 32768) throw new Error('Invalid voice text');
    try { await this.rpc('thread/realtime/appendText', { threadId: this.threadId, text }); }
    catch (error) { throw new RealtimeError(classifyRealtimeError(error)); }
  }

  /** Native speakable text, distinct from quiet context and from a new task prompt. */
  async speak(text: string): Promise<void> {
    if (this.ended || !this.startSent || !LIVE_PHASES.includes(this.snapshot.phase)) throw new RealtimeError('session_ended');
    if (!text.trim() || text.length > 4000) throw new Error('Invalid voice summary');
    try { await this.rpc('thread/realtime/appendSpeech', { threadId: this.threadId, text }); }
    catch (error) { throw new RealtimeError(classifyRealtimeError(error)); }
  }

  /** Ends the session. Idempotent; independent of turn cancellation. Never rejects. */
  async stop(): Promise<RealtimeState> {
    const phase = this.snapshot.phase;
    if (phase === 'idle' || phase === 'closed' || phase === 'failed') return this.state;
    if (!this.startSent) {
      // Authentication was still running; nothing reached the server, so there is nothing to stop.
      this.settle('closed');
      return this.state;
    }
    this.stoppingTask ??= (async () => {
      this.update({ phase: 'stopping' });
      // A start that never became active ends here, not with a failure.
      const reject = this.rejectActive;
      this.resolveActive = null;
      this.rejectActive = null;
      reject?.(new RealtimeError('stopped'));
      return this.finishStopping();
    })();
    return this.stoppingTask;
  }

  /**
   * Synchronous, unconditional release: settles everything without further RPCs and stops reacting
   * to native events. Use when a stop request may never settle; the app-server process keeps
   * running under the host's control.
   */
  dispose(): void {
    if (this.ended) return;
    this.settle('closed');
  }

  /**
   * Route `thread/realtime/*` notifications here. Anything else, anything about another thread, and
   * anything not belonging to the current session's start is dropped.
   */
  handleNotification(method: string, params: { threadId?: unknown } & Record<string, unknown>): void {
    if (!REALTIME_NOTIFICATIONS.has(method) || params?.threadId !== this.threadId || !this.startSent) return;
    const phase = this.snapshot.phase;
    switch (method) {
      case 'thread/realtime/started':
        if (LIVE_PHASES.includes(phase)) {
          if (!this.snapshot.serverAccepted) this.signal({ type: 'started' });
          this.update({ serverAccepted: true });
          this.maybeActive();
        }
        break;
      case 'thread/realtime/sdp': {
        const sdp = params.sdp;
        if (typeof sdp !== 'string' || !sdp || !LIVE_PHASES.includes(phase)) break;
        this.update({ sdpAnswered: true });
        this.signal({ type: 'sdp', sdp });
        this.maybeActive();
        break;
      }
      case 'thread/realtime/error': {
        const failure = classifyRealtimeError(params.message);
        if (LIVE_PHASES.includes(phase)) this.failWithStop(failure);
        else if (phase === 'stopping') this.update({ error: failure });
        break;
      }
      case 'thread/realtime/closed':
        if (phase === 'stopping') {
          this.signal({ type: 'closed' });
          this.settle(this.snapshot.error ? 'failed' : 'closed', this.snapshot.error ?? undefined);
        } else if (LIVE_PHASES.includes(phase)) {
          this.signal({ type: 'closed' });
          this.settle('failed', 'closed_by_server');
        }
        break;
      case 'thread/realtime/transcript/delta':
      case 'thread/realtime/transcript/done':
        // This profile is fixed to v3: use its canonical typed timeline exclusively. The flat
        // stream has no item id, so neither role, arrival order nor matching text can safely
        // associate it with a typed utterance. Mixing them can duplicate or overwrite speech.
        break;
      case 'thread/realtime/item/started':
      case 'thread/realtime/item/completed': {
        // Timeline items are typed: only a transcriptSegment is a chat caption. Promoted task
        // work (bemItemPromoted), session bookkeeping and raw `item/added` payloads are not.
        if (!LIVE_PHASES.includes(phase)) break;
        const item = params.item as { type?: unknown; id?: unknown; role?: unknown; text?: unknown } | undefined;
        const final = method === 'thread/realtime/item/completed';
        if (item?.type !== 'transcriptSegment') break;
        const role = transcriptRole(item.role);
        if (!role || typeof item.id !== 'string' || !item.id || typeof item.text !== 'string') break;
        const itemId = item.id;
        if (itemId.length > 256) break;
        if (final) this.itemRoles.delete(itemId);
        else this.remember(this.itemRoles, itemId, role);
        if (!final && !item.text) break; // an empty start only says the item exists
        // An empty completed text is authoritative: it clears what the draft showed.
        this.signal({ type: 'transcript', itemId, text: item.text, final, role });
        break;
      }
      case 'thread/realtime/item/transcript/delta': {
        if (!LIVE_PHASES.includes(phase)) break;
        const itemId = params.itemId;
        if (typeof itemId !== 'string' || !itemId || typeof params.delta !== 'string' || !params.delta) break;
        const role = this.itemRoles.get(itemId);
        if (role) this.signal({ type: 'transcript', itemId, text: params.delta, final: false, role });
        break;
      }
    }
  }

  /** The app-server process is gone: settle anything still pending, without further RPCs. */
  handleClosed(): void {
    if (this.snapshot.phase === 'idle' || this.snapshot.phase === 'closed' || this.snapshot.phase === 'failed') return;
    const failure = this.snapshot.error ?? 'process_closed';
    this.signal({ type: 'error', reason: failure });
    this.settle('failed', failure);
  }

  private maybeActive(): void {
    // Only from 'signaling': pre-ACK started/SDP are recorded, but the acknowledgement is part
    // of establishment, so a rejected start RPC cannot be concealed by early notifications.
    if (this.snapshot.phase !== 'signaling') return;
    if (!this.snapshot.serverAccepted || !this.snapshot.sdpAnswered) return;
    const resolve = this.resolveActive;
    this.resolveActive = null;
    this.rejectActive = null;
    this.clearTimers();
    this.update({ phase: 'active' });
    resolve?.(this.state);
  }

  /** A failure that may have left a server-side session behind: stop it, and let the trailing
   * `closed` arrive while stops are still blocking restarts. */
  private failWithStop(reason: RealtimeFailure): void {
    this.signal({ type: 'error', reason });
    this.update({ phase: 'stopping', error: reason });
    const reject = this.rejectActive;
    this.resolveActive = null;
    this.rejectActive = null;
    reject?.(new RealtimeError(reason));
    this.stoppingTask ??= this.finishStopping();
  }

  private async finishStopping(): Promise<RealtimeState> {
    try {
      await this.rpc('thread/realtime/stop', { threadId: this.threadId });
    } catch {
      // Best effort; the closed notification or the bound below still ends the wait.
    }
    if (this.snapshot.phase === 'stopping') {
      await new Promise<void>(resolve => {
        this.closeWaiter = resolve;
        this.closeTimer = setTimeout(() => {
          this.closeWaiter = null;
          resolve();
        }, this.options.closeTimeoutMs);
      });
    }
    if (this.snapshot.phase === 'stopping') this.settle(this.snapshot.error ? 'failed' : 'closed', this.snapshot.error ?? undefined);
    return this.state;
  }

  private settle(phase: 'closed' | 'failed', reason?: RealtimeFailure): void {
    this.itemRoles.clear();
    this.clearTimers();
    this.ended = true;
    const waiter = this.closeWaiter;
    this.closeWaiter = null;
    const error = phase === 'failed' ? reason ?? this.snapshot.error ?? 'native_error' : null;
    this.update({ phase, error });
    const reject = this.rejectActive;
    this.resolveActive = null;
    this.rejectActive = null;
    reject?.(new RealtimeError(error ?? 'stopped'));
    waiter?.();
  }

  private armEstablishment(attempt: number): void {
    this.establishTimer = setTimeout(() => {
      if (this.attemptNonce === attempt && ESTABLISHING_PHASES.includes(this.snapshot.phase)) {
        this.failWithStop('signaling_timeout');
      }
    }, this.options.establishTimeoutMs);
  }

  private rpc(method: string, params: unknown): Promise<unknown> {
    if (this.transport.isClosed) return Promise.reject(new RealtimeError('process_closed'));
    let timer: ReturnType<typeof setTimeout>;
    const bounded = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new RealtimeError('timeout')), this.options.requestTimeoutMs);
    });
    return Promise.race([this.transport.request(method, params), bounded]).finally(() => clearTimeout(timer));
  }

  /** Inserts into a bounded map, evicting the oldest entry so a long session cannot grow it. */
  private remember<K, V>(map: Map<K, V>, key: K, value: V): void {
    map.delete(key);
    map.set(key, value);
    while (map.size > 256) map.delete(map.keys().next().value!);
  }

  private update(patch: Partial<RealtimeState>): void {
    const next = { ...this.snapshot, ...patch };
    const before = this.snapshot;
    if (
      next.phase === before.phase && next.serverAccepted === before.serverAccepted
      && next.sdpAnswered === before.sdpAnswered && next.error === before.error
    ) return;
    this.snapshot = next;
    this.safeCall(() => this.handlers.onState?.(this.state));
  }

  private signal(signal: RealtimeSignal): void {
    this.safeCall(() => this.handlers.onSignal?.(signal));
  }

  private safeCall(run: () => void): void {
    try {
      run();
    } catch {
      // A misbehaving subscriber must not break the session state machine.
    }
  }

  private clearTimers(): void {
    if (this.establishTimer) clearTimeout(this.establishTimer);
    if (this.closeTimer) clearTimeout(this.closeTimer);
    this.establishTimer = null;
    this.closeTimer = null;
  }
}

/**
 * One native voice session in the coordinator's transport shape (voice-sessions.ts
 * `VoiceTransport`): `start` resolves when the native start request is acknowledged — the
 * acknowledgement is not connectivity — while `started`, SDP answers, transcripts, classified
 * errors and closure stream through `emit`. Single use: after `stop`/`dispose`/failure, create a
 * new transport (and preferably a new native thread) for the next session.
 */
export interface RealtimeVoiceTransport {
  start(offer: string, emit: (signal: RealtimeSignal) => void): Promise<void>;
  stop(): Promise<void>;
  dispose(): void;
}

export class CodexVoiceTransport implements RealtimeVoiceTransport {
  private readonly rt: CodexRealtime;
  private emit: ((signal: RealtimeSignal) => void) | null = null;
  private pending: { resolve(): void; reject(error: RealtimeError): void } | null = null;
  private started = false;
  private disposed = false;

  constructor(threadId: string, transport: RealtimeTransport, options?: RealtimeOptions, private readonly context?: VoiceContext) {
    this.rt = new CodexRealtime(threadId, transport, {
      onAccepted: () => {
        const pending = this.pending;
        this.pending = null;
        pending?.resolve();
      },
      onSignal: signal => this.emit?.(signal),
    }, options);
  }

  get state(): RealtimeState {
    return this.rt.state;
  }

  async start(offer: string, emit: (signal: RealtimeSignal) => void): Promise<void> {
    if (this.disposed || this.started) throw new RealtimeError('session_ended');
    this.started = true;
    this.emit = emit;
    // Failures after the acknowledgement reach the host as `error` signals; only a failure of the
    // start request itself rejects here.
    this.rt.start({ sdpOffer: offer, context: this.context }).catch(error => {
      const pending = this.pending;
      this.pending = null;
      pending?.reject(error instanceof RealtimeError ? error : new RealtimeError(classifyRealtimeError(error)));
    });
    return new Promise<void>((resolve, reject) => {
      this.pending = { resolve, reject };
    });
  }

  async stop(): Promise<void> {
    await this.rt.stop();
  }

  speak(text: string): Promise<void> { return this.rt.speak(text); }
  appendText(text: string): Promise<void> { return this.rt.appendText(text); }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.emit = null;
    const pending = this.pending;
    this.pending = null;
    pending?.reject(new RealtimeError('stopped'));
    this.rt.dispose();
  }

  handleNotification(method: string, params: { threadId?: unknown } & Record<string, unknown>): void {
    this.rt.handleNotification(method, params);
  }

  handleClosed(): void {
    this.rt.handleClosed();
  }
}
