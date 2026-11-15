import { randomUUID } from 'node:crypto';
import { HttpError } from './errors.js';

/** One machine, provider and target, selected by the host rather than by native model output. */
export interface VoiceBinding {
  extensionId: string;
  hostId: string;
  providerId: string;
  threadId: string;
  ownerId: string;
}

/** Supplied from the authenticated host context, never trusted directly from a request body. */
export interface VoiceAccess {
  id: string;
  extensionId: string;
  ownerId: string;
}

export interface VoiceSnapshot {
  id: string;
  hostId: string;
  providerId: string;
  threadId: string;
  state: 'connecting' | 'connected' | 'stopping' | 'closed' | 'failed';
  startAccepted: boolean;
  upstreamStarted: boolean;
  /** Reported by the owning browser; this is not proof of audible speech or billing. */
  mediaConnected: boolean;
  reason?: 'ended' | 'expired' | 'startup_timeout' | 'revoked' | 'provider_error' | 'provider_closed' | 'shutdown';
}

export type VoiceSignal =
  | { type: 'started' }
  | { type: 'sdp'; sdp: string }
  | { type: 'transcript'; itemId: string; text: string; final: boolean;
      role?: 'user' | 'assistant'; at?: string; afterSeq?: number; order?: number }
  | { type: 'error' }
  | { type: 'closed' };

export type VoiceEvent =
  | { type: 'state'; session: VoiceSnapshot }
  | Extract<VoiceSignal, { type: 'sdp' | 'transcript' }>;

/** Only resources owned by this voice connection. No turn cancellation or approval API. */
export interface VoiceTransport {
  start(offer: string, emit: (event: VoiceSignal) => void): Promise<void>;
  /** Speak a host-selected task summary without submitting another agent turn. */
  speak?(text: string): Promise<void>;
  appendText?(text: string, attachments?: { images?: unknown; videos?: unknown }): Promise<void>;
  stop(reason?: VoiceSnapshot['reason']): Promise<void>;
  /** Synchronous, unconditional release, including when the native stop request never settles. */
  dispose(): void;
}

/** Host-owned instructions for native delegation; never supplied by an extension or the browser. */
export interface VoiceContext {
  instructions: string;
}

export interface VoiceSessionsOptions {
  /** Recheck installation, grant, provider availability and the target binding on every use. */
  authorize(binding: Readonly<VoiceBinding>): void;
  create(binding: Readonly<VoiceBinding>): VoiceTransport;
  leaseMs?: number;
  startupMs?: number;
  stopMs?: number;
  maxSessions?: number;
}

interface Session {
  binding: Readonly<VoiceBinding>;
  snapshot: VoiceSnapshot;
  transport: VoiceTransport;
  listeners: Set<(event: VoiceEvent) => void>;
  answer?: string;
  lease?: ReturnType<typeof setTimeout>;
  startup?: ReturnType<typeof setTimeout>;
  stopping?: Promise<void>;
}

const MAX_SDP = 64 * 1024;
const MAX_TEXT = 32 * 1024;
const active = (s: Session) => s.snapshot.state === 'connecting' || s.snapshot.state === 'connected';

/**
 * Volatile voice leases, separate from ThreadManager's task queue and persisted event stream.
 * Callers wire authenticated control routes and adapter transports; this class opens no network.
 */
export class VoiceSessions {
  private readonly sessions = new Map<string, Session>();
  private readonly leaseMs: number;
  private readonly startupMs: number;
  private readonly stopMs: number;
  private readonly maxSessions: number;
  private disposed = false;

  constructor(private readonly options: VoiceSessionsOptions) {
    this.leaseMs = options.leaseMs ?? 30_000;
    this.startupMs = options.startupMs ?? 20_000;
    this.stopMs = options.stopMs ?? 2_500;
    this.maxSessions = options.maxSessions ?? 16;
    if ([this.leaseMs, this.startupMs, this.stopMs, this.maxSessions].some(n => !Number.isSafeInteger(n) || n < 1)) {
      throw new Error('Invalid voice session limits');
    }
  }

  /** Reserve synchronously, so concurrent starts cannot both acquire the target. */
  open(binding: VoiceBinding, offer: string): VoiceSnapshot {
    if (this.disposed) throw new HttpError(503, 'Voice service is stopping');
    if (Object.values(binding).some(v => typeof v !== 'string' || !v || v.length > 256)
      || ['extensionId', 'hostId', 'providerId', 'threadId', 'ownerId'].some(k => !binding[k as keyof VoiceBinding])) {
      throw new HttpError(400, 'Invalid voice binding');
    }
    if (typeof offer !== 'string' || !offer.trim() || offer.length > MAX_SDP) throw new HttpError(400, 'Invalid voice offer');
    const bound = Object.freeze({ ...binding });
    this.options.authorize(bound);
    if (this.sessions.size >= this.maxSessions) throw new HttpError(429, 'Too many voice sessions');
    for (const s of this.sessions.values()) {
      if ((s.binding.hostId === bound.hostId && s.binding.threadId === bound.threadId)
        || s.binding.ownerId === bound.ownerId) throw new HttpError(409, 'Voice session is already open');
    }
    const transport = this.options.create(bound);
    const s: Session = {
      binding: bound, transport, listeners: new Set(),
      snapshot: { id: randomUUID(), hostId: bound.hostId, providerId: bound.providerId, threadId: bound.threadId,
        state: 'connecting', startAccepted: false, upstreamStarted: false, mediaConnected: false },
    };
    this.sessions.set(s.snapshot.id, s);
    this.renew(s);
    s.startup = setTimeout(() => { void this.end(s, 'startup_timeout'); }, this.startupMs);
    s.startup.unref();
    // The owner can subscribe before even synchronous adapter notifications arrive.
    queueMicrotask(() => { void this.launch(s, offer); });
    return { ...s.snapshot };
  }

  get(access: VoiceAccess): VoiceSnapshot {
    return { ...this.usable(access).snapshot };
  }

  subscribe(access: VoiceAccess, listener: (event: VoiceEvent) => void): () => void {
    const s = this.usable(access);
    s.listeners.add(listener);
    this.deliver(listener, { type: 'state', session: { ...s.snapshot } });
    // Retain only the current answer in memory, allowing a late control-stream subscription.
    if (s.answer && active(s) && s.listeners.has(listener)) this.deliver(listener, { type: 'sdp', sdp: s.answer });
    return () => { s.listeners.delete(listener); };
  }

  heartbeat(access: VoiceAccess): VoiceSnapshot {
    const s = this.usable(access);
    this.renew(s);
    return { ...s.snapshot };
  }

  mediaConnected(access: VoiceAccess): VoiceSnapshot {
    const s = this.usable(access);
    if (!s.answer) throw new HttpError(409, 'Voice negotiation has not produced an answer');
    s.snapshot.mediaConnected = true;
    this.publishConnection(s);
    return { ...s.snapshot };
  }

  async appendText(access: VoiceAccess, text: unknown, attachments?: { images?: unknown; videos?: unknown }): Promise<void> {
    const s = this.usable(access);
    if (typeof text !== 'string' || (!text.trim() && !(Array.isArray(attachments?.images) && attachments.images.length) && !(Array.isArray(attachments?.videos) && attachments.videos.length)) || text.length > MAX_TEXT) throw new HttpError(400, 'invalid_voice_text');
    if (s.snapshot.state !== 'connected') throw new HttpError(409, 'voice_not_connected');
    if (!s.transport.appendText) throw new HttpError(501, 'voice_text_unavailable');
    await s.transport.appendText(text.trim(), attachments);
  }

  /** End only voice, including after grants were revoked. A missing session is already stopped. */
  async stop(access: VoiceAccess): Promise<void> {
    const s = this.sessions.get(access.id);
    if (!s) return;
    this.checkOwner(s, access);
    await this.end(s, 'ended');
  }

  /** Called on extension/provider changes; fail closed without waiting for the next heartbeat. */
  async revalidate(): Promise<void> {
    const stops: Promise<void>[] = [];
    for (const s of this.sessions.values()) {
      if (s.stopping) { stops.push(s.stopping); continue; }
      if (!active(s)) continue;
      try { this.options.authorize(s.binding); }
      catch { stops.push(this.end(s, 'revoked')); }
    }
    await Promise.all(stops);
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    await Promise.all([...this.sessions.values()].map(s => this.end(s, 'shutdown')));
  }

  private checkOwner(s: Session, access: VoiceAccess): void {
    if (s.binding.ownerId !== access.ownerId || s.binding.extensionId !== access.extensionId) {
      throw new HttpError(404, 'Voice session not found');
    }
  }

  private usable(access: VoiceAccess): Session {
    const s = this.sessions.get(access.id);
    if (!s) throw new HttpError(404, 'Voice session not found');
    this.checkOwner(s, access);
    try { this.options.authorize(s.binding); }
    catch { void this.end(s, 'revoked'); throw new HttpError(403, 'Voice access revoked'); }
    if (!active(s)) throw new HttpError(409, 'Voice session is stopping');
    return s;
  }

  private renew(s: Session): void {
    clearTimeout(s.lease);
    s.lease = setTimeout(() => { void this.end(s, 'expired'); }, this.leaseMs);
    s.lease.unref();
  }

  private async launch(s: Session, offer: string): Promise<void> {
    if (!active(s)) return;
    try {
      // Authorization may have changed since open() reserved the target.
      try { this.options.authorize(s.binding); }
      catch { await this.end(s, 'revoked'); return; }
      await s.transport.start(offer, event => this.signal(s, event));
      if (!active(s)) return;
      s.snapshot.startAccepted = true;
      this.publishConnection(s);
    } catch { await this.end(s, 'provider_error'); }
  }

  private signal(s: Session, event: VoiceSignal): void {
    if (!active(s) || this.sessions.get(s.snapshot.id) !== s) return;
    try { this.options.authorize(s.binding); }
    catch { void this.end(s, 'revoked'); return; }
    switch (event.type) {
      case 'started':
        s.snapshot.upstreamStarted = true;
        this.publishConnection(s);
        break;
      case 'sdp':
        if (typeof event.sdp !== 'string' || !event.sdp.trim() || event.sdp.length > MAX_SDP) {
          void this.end(s, 'provider_error'); return;
        }
        s.answer = event.sdp;
        this.publish(s, { type: 'sdp', sdp: event.sdp });
        break;
      case 'transcript':
        if (typeof event.itemId !== 'string' || !event.itemId || event.itemId.length > 256
          || typeof event.text !== 'string' || event.text.length > MAX_TEXT || typeof event.final !== 'boolean') {
          void this.end(s, 'provider_error'); return;
        }
        this.publish(s, { type: 'transcript', itemId: event.itemId, text: event.text, final: event.final,
          ...(event.role === 'user' || event.role === 'assistant' ? { role: event.role } : {}),
          ...(typeof event.at === 'string' ? { at: event.at } : {}),
          ...(Number.isSafeInteger(event.afterSeq) && event.afterSeq! >= 0 ? { afterSeq: event.afterSeq } : {}),
          ...(Number.isSafeInteger(event.order) && event.order! >= 0 ? { order: event.order } : {}),
        });
        break;
      case 'error': void this.end(s, 'provider_error'); break;
      case 'closed': void this.end(s, 'provider_closed'); break;
    }
  }

  private publishConnection(s: Session): void {
    // Native acceptance and browser media can arrive in either order. An early browser
    // connection must not hide a failed start RPC or cancel the startup deadline.
    if (s.snapshot.startAccepted && s.snapshot.upstreamStarted && s.snapshot.mediaConnected) {
      s.snapshot.state = 'connected';
      clearTimeout(s.startup);
    }
    this.publish(s, { type: 'state', session: { ...s.snapshot } });
  }

  private publish(s: Session, event: VoiceEvent): void {
    for (const listener of [...s.listeners]) {
      // Each subscriber gets its own copy; it cannot corrupt another subscriber or host state.
      const copy = event.type === 'state' ? { ...event, session: { ...event.session } } : { ...event };
      this.deliver(listener, copy);
    }
  }

  private deliver(listener: (event: VoiceEvent) => void, event: VoiceEvent): void {
    try { listener(event); } catch { /* A disconnected control stream must not break cleanup. */ }
  }

  private end(s: Session, reason: NonNullable<VoiceSnapshot['reason']>): Promise<void> {
    if (s.stopping) return s.stopping;
    // Assign the cleanup promise before invoking listeners or provider code (both can reenter).
    s.stopping = Promise.resolve().then(async () => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          Promise.resolve().then(() => s.transport.stop(reason)),
          new Promise<void>(resolve => { timer = setTimeout(resolve, this.stopMs); }),
        ]);
      } catch { /* dispose is unconditional, even when native stop fails. */ }
      finally {
        clearTimeout(timer);
        try { s.transport.dispose(); } catch { /* No native errors or private data reach consumers. */ }
        s.snapshot.state = ['startup_timeout', 'provider_error', 'revoked'].includes(reason) ? 'failed' : 'closed';
        this.publish(s, { type: 'state', session: { ...s.snapshot } });
        s.listeners.clear();
        this.sessions.delete(s.snapshot.id);
      }
    });
    s.snapshot.state = 'stopping';
    s.snapshot.reason = reason;
    s.answer = undefined;
    clearTimeout(s.lease);
    clearTimeout(s.startup);
    this.publish(s, { type: 'state', session: { ...s.snapshot } });
    return s.stopping;
  }
}
