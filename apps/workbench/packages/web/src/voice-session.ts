import { useEffect, useState, useMemo } from 'react';
import { Api, type VoiceSnapshot, type VoiceEvent, type OutgoingImage } from './api';
import { VoiceMediaController, type VoiceMediaOptions, type VoiceMediaSnapshot, VoiceMediaException } from './voice-media';
import { t } from './i18n';
import type { VoiceTranscript } from './voice-timeline';

export interface VoiceSessionState {
  readonly phase: 'listening' | 'thinking' | 'speaking';
  readonly startedAt: number | null;
  readonly active: boolean;
  readonly status: 'idle' | 'requesting' | 'connecting' | 'connected' | 'error';
  readonly sessionId: string | null;
  readonly threadId: string | null;
  readonly providerId?: string | null;
  readonly isMuted: boolean;
  readonly transcript: string;
  readonly transcriptFinal: boolean;
  readonly error: string | null;
  readonly messages: readonly VoiceTranscript[];
  readonly messagesThreadId: string | null;
}

export type VoiceSessionListener = (state: VoiceSessionState) => void;

export interface VoiceSessionControllerOptions {
  api: Api;
  mediaOptions?: VoiceMediaOptions;
  createMediaController?: (options?: VoiceMediaOptions) => VoiceMediaController;
}

export class VoiceSessionController {
  private readonly api: Api;
  private readonly mediaOptions?: VoiceMediaOptions;
  private readonly createMediaController: (options?: VoiceMediaOptions) => VoiceMediaController;
  private readonly listeners = new Set<VoiceSessionListener>();

  private _state: VoiceSessionState = {
    phase: 'listening',
    startedAt: null,
    active: false,
    status: 'idle',
    sessionId: null,
    threadId: null,
    isMuted: false,
    transcript: '',
    transcriptFinal: false,
    error: null,
    messages: [],
    messagesThreadId: null,
  };

  private mediaController: VoiceMediaController | null = null;
  private generation = 0;
  private currentItemId: string | null = null;
  private currentTranscript = '';
  private sseAbort: AbortController | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;

  constructor(options: VoiceSessionControllerOptions) {
    this.api = options.api;
    this.mediaOptions = options.mediaOptions;
    this.createMediaController = options.createMediaController ?? ((opts) => new VoiceMediaController(opts));
  }

  get state(): VoiceSessionState {
    return this._state;
  }

  subscribe(listener: VoiceSessionListener): () => void {
    this.listeners.add(listener);
    listener(this._state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private updateState(partial: Partial<VoiceSessionState>): void {
    this._state = { ...this._state, ...partial };
    for (const listener of this.listeners) {
      try {
        listener(this._state);
      } catch {}
    }
  }

  private resetState(): void {
    this.updateState({
      active: false,
      status: 'idle',
      sessionId: null,
      threadId: null,
      providerId: null,
      isMuted: false,
      transcript: '',
      transcriptFinal: false,
    });
  }

  private mapErrorMessage(err: unknown): string {
    const code = (err as any)?.code ?? (err as any)?.status ?? (err as any)?.name ?? '';
    const text = String((err as any)?.message ?? '');
    if (/voice_target_busy/.test(text)) return t('voice.targetBusy');
    if (code === 'permission-denied' || code === 'NotAllowedError' || /permission/i.test(text)) {
      return t('voice.permissionDenied');
    }
    if (code === 'insecure-context' || /secure context/i.test(text)) {
      return t('voice.insecureContext');
    }
    if (code === 'not-supported' || /not supported/i.test(text)) {
      return t('voice.notSupported');
    }
    if (/voice_login_required|chatgpt_login_required|login required|unauthorized/i.test(text) || code === 401) {
      return t('voice.chatgptLoginRequired');
    }
    if (code === 'ice-timeout' || /timeout/i.test(text)) {
      return t('voice.iceTimeout');
    }
    if (code === 'playback-blocked' || /autoplay/i.test(text)) {
      return t('voice.playbackBlocked');
    }
    if (code === 'connection-failed') {
      return t('voice.connectionFailed');
    }
    return t('voice.error');
  }

  private mapReasonMessage(reason?: string): string {
    if (reason === 'startup_timeout') return t('voice.iceTimeout');
    if (reason === 'revoked') return t('voice.requirementMissing');
    if (reason === 'provider_error' || reason === 'provider_closed') return t('voice.connectionFailed');
    if (reason === 'expired') return t('voice.connectionFailed');
    return t('voice.error');
  }

  private cleanupMedia(target?: VoiceMediaController | null): void {
    const toDispose = target ?? this.mediaController;
    if (toDispose) {
      try {
        toDispose.dispose();
      } catch {}
      if (this.mediaController === toDispose) {
        this.mediaController = null;
      }
    }
  }

  /**
   * Starts a real-time voice session.
   * NOTE: Must be called directly within a user gesture (e.g. click handler)
   * so media.prepare() can acquire the microphone without browser policy blocking.
   */
  async start(threadId: string): Promise<void> {
    if (this._state.active) {
      if (this._state.threadId === threadId && this._state.status !== 'error') {
        return;
      }
      void this.stop();
    }

    const gen = ++this.generation;
    this.currentItemId = null;
    this.currentTranscript = '';

    const media = this.createMediaController(this.mediaOptions);
    this.mediaController = media;

    media.onStateChange((snap) => {
      this.handleMediaStateChange(snap, gen);
    });

    this.updateState({
      active: true,
      status: 'requesting',
      startedAt: Date.now(),
      phase: 'listening',
      threadId,
      providerId: null,
      sessionId: null,
      isMuted: false,
      transcript: '',
      transcriptFinal: false,
      error: null,
      messages: this._state.messagesThreadId === threadId ? this._state.messages : [],
      messagesThreadId: threadId,
    });

    // 1. Prepare media synchronously in user gesture callstack
    let offer: string;
    try {
      offer = await media.prepare();
    } catch (err: any) {
      if (this.generation !== gen) {
        media.dispose();
        return;
      }
      const msg = this.mapErrorMessage(err);
      this.cleanupMedia(media);
      this.updateState({ active: false, status: 'error', error: msg });
      return;
    }

    if (this.generation !== gen) {
      media.dispose();
      return;
    }

    this.updateState({ status: 'connecting' });

    // 2. Start session on daemon
    let snapshot: VoiceSnapshot;
    try {
      snapshot = await this.api.startVoiceSession(threadId, offer, 'voice');
    } catch (err: any) {
      if (this.generation !== gen) {
        media.dispose();
        return;
      }
      const msg = this.mapErrorMessage(err);
      this.cleanupMedia(media);
      this.updateState({ active: false, status: 'error', error: msg });
      return;
    }

    if (this.generation !== gen) {
      // Stopped or superseded while HTTP request was pending; delete delayed session
      void this.api.stopVoiceSession(snapshot.id).catch(() => {});
      media.dispose();
      return;
    }

    const sessionId = snapshot.id;
    this.updateState({ sessionId, providerId: snapshot.providerId });

    // 3. Connect SSE event stream
    this.sseAbort = new AbortController();
    this.listenToEvents(sessionId, this.sseAbort.signal, gen, media);

    // 4. Start heartbeat (every 10 seconds)
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (this.generation === gen && this._state.sessionId === sessionId) {
        this.api.voiceHeartbeat(sessionId).catch((err) => {
          if (this.generation === gen) {
            this.updateState({ active: false, status: 'error', error: this.mapErrorMessage(err) });
            void this.stop(true);
          }
        });
      }
    }, 10_000);
  }

  private listenToEvents(
    sessionId: string,
    signal: AbortSignal,
    gen: number,
    media: VoiceMediaController
  ): void {
    const streamPromise = this.api.streamVoiceEvents(
      sessionId,
      (event: VoiceEvent) => {
        if (this.generation !== gen || this._state.sessionId !== sessionId) return;

        if (event.type === 'sdp') {
          media.acceptAnswer(event.sdp).catch((err) => {
            if (this.generation === gen) {
              this.updateState({ active: false, status: 'error', error: this.mapErrorMessage(err) });
              void this.stop(true);
            }
          });
        } else if (event.type === 'transcript') {
          this.updateState({ phase: event.role === 'user' ? event.final ? 'thinking' : 'listening' : event.final ? 'listening' : 'speaking' });
          if (event.role === 'user' || event.role === 'assistant') {
            const messages = [...this._state.messages];
            const index = messages.findIndex(m => m.itemId === event.itemId);
            const previous = messages[index];
            if (!previous || previous.role === event.role && (!previous.final || event.final)) {
              const message: VoiceTranscript = {
                itemId: event.itemId, role: event.role,
                text: (event.final ? event.text : (previous?.text ?? '') + event.text).slice(0, 32768),
                final: event.final, streaming: !event.final,
                at: previous?.at ?? event.at ?? new Date().toISOString(),
                afterSeq: previous?.afterSeq ?? event.afterSeq ?? 0,
                order: previous?.order ?? event.order ?? messages.length,
              };
              if (index < 0) messages.push(message);
              else messages[index] = message;
              this.updateState({ messages: messages.slice(-256) });
            }
          }
          if (event.itemId !== this.currentItemId || this._state.transcriptFinal && !event.final) {
            this.currentItemId = event.itemId;
            this.currentTranscript = event.text;
          } else {
            if (event.final) {
              this.currentTranscript = event.text;
            } else {
              this.currentTranscript = (this.currentTranscript || '') + event.text;
            }
          }
          this.currentTranscript = this.currentTranscript.slice(-32768);
          this.updateState({
            transcript: this.currentTranscript,
            transcriptFinal: Boolean(event.final),
          });
        } else if (event.type === 'state') {
          const isErrorState =
            event.session.state === 'failed' ||
            (Boolean(event.session.reason) && event.session.reason !== 'ended');

          if (isErrorState) {
            const err = this.mapReasonMessage(event.session.reason);
            this.updateState({ active: false, status: 'error', error: err });
            void this.stop(true);
            return;
          }

          if (event.session.state === 'connected') {
            this.updateState({ status: 'connected' });
          } else if (event.session.state === 'closed' || event.session.state === 'stopping') {
            void this.stop(false);
          }
        }
      },
      signal
    );

    Promise.resolve(streamPromise)
      .then(() => {
        if (this.generation === gen && this._state.active) {
          void this.stop();
        }
      })
      .catch((err) => {
        if (this.generation === gen && !signal.aborted) {
          this.updateState({ active: false, status: 'error', error: this.mapErrorMessage(err) });
          void this.stop(true);
        }
      });
  }

  private handleMediaStateChange(snap: VoiceMediaSnapshot, gen: number): void {
    if (this.generation !== gen) return;
    if (snap.category === 'connected') {
      if (this._state.sessionId) {
        this.api.voiceConnected(this._state.sessionId).catch((err) => {
          if (this.generation === gen) {
            this.updateState({ active: false, status: 'error', error: this.mapErrorMessage(err) });
            void this.stop(true);
          }
        });
      }
    } else if (snap.category === 'error') {
      const msg = this.mapErrorMessage(snap.error);
      this.updateState({ active: false, status: 'error', error: msg });
      void this.stop(true);
    }
  }

  levels = (): { input: number; output: number } => this.mediaController?.levels?.() ?? { input: 0, output: 0 };

  async appendText(text: string, images?: OutgoingImage[], videos?: string[]): Promise<void> {
    if (!this._state.sessionId || this._state.status !== 'connected') throw new Error(t('voice.connecting'));
    await this.api.appendVoiceText(this._state.sessionId, text, images, videos);
    this.updateState({ phase: 'thinking' });
  }

  /** Mutes or unmutes the local microphone input track. */
  setMuted(muted: boolean): void {
    if (!this._state.active) return;
    this.mediaController?.setMuted(muted);
    this.updateState({ isMuted: muted });
  }

  toggleMute(): void {
    this.setMuted(!this._state.isMuted);
  }

  /**
   * Stops the active voice session: shuts down local tracks and peer connection,
   * cancels SSE, stops heartbeat, and notifies the daemon via DELETE /api/voice/:id.
   */
  async stop(preserveError = false): Promise<void> {
    this.generation++;
    this.currentItemId = null;
    this.currentTranscript = '';

    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }

    if (this.sseAbort) {
      this.sseAbort.abort();
      this.sseAbort = null;
    }

    this.cleanupMedia();

    const sessionId = this._state.sessionId;
    const errorToKeep = preserveError ? this._state.error : null;
    const statusToSet = preserveError && errorToKeep ? 'error' : 'idle';

    this.updateState({
      active: false,
      status: statusToSet,
      sessionId: null,
      threadId: null,
      isMuted: false,
      transcript: '',
      transcriptFinal: false,
      error: errorToKeep,
      messages: this._state.messages.map(m => ({ ...m, streaming: false })),
    });

    if (sessionId) {
      try {
        await this.api.stopVoiceSession(sessionId);
      } catch {}
    }
  }

  dismissError(): void {
    this.updateState({ error: null, status: 'idle' });
  }
}

/**
 * React hook to bind VoiceSessionController to the current thread and component lifecycle.
 */
export function useVoiceSession(
  api: Api,
  threadId: string | null,
  host: string | null
): {
  state: VoiceSessionState;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  setMuted: (muted: boolean) => void;
  toggleMute: () => void;
  dismissError: () => void;
  appendText: (text: string, images?: OutgoingImage[], videos?: string[]) => Promise<void>;
  levels: () => { input: number; output: number };
} {
  const controller = useMemo(() => new VoiceSessionController({ api }), [api, host]);
  const [observed, setObserved] = useState({ controller, state: controller.state });

  useEffect(() => {
    const unsubscribe = controller.subscribe(state => setObserved({ controller, state }));
    return () => { unsubscribe(); void controller.stop(); };
  }, [controller]);

  useEffect(() => () => { void controller.stop(); }, [controller, threadId]);

  useEffect(() => {
    const onExit = () => { void controller.stop(); };
    window.addEventListener('pagehide', onExit);
    window.addEventListener('beforeunload', onExit);
    return () => {
      window.removeEventListener('pagehide', onExit);
      window.removeEventListener('beforeunload', onExit);
    };
  }, [controller]);

  return {
    state: observed.controller === controller ? observed.state : controller.state,
    start: () => threadId ? controller.start(threadId) : Promise.resolve(),
    stop: () => controller.stop(),
    setMuted: muted => controller.setMuted(muted),
    toggleMute: () => controller.toggleMute(),
    dismissError: () => controller.dismissError(),
    appendText: (text, images, videos) => controller.appendText(text, images, videos),
    levels: controller.levels,
  };
}
