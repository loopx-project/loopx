import { HttpError } from './errors.js';
import type { ExtensionHost } from './extension-host.js';
import type { ProviderCapabilitySnapshot } from './extension-dependencies.js';
import { createExtensionVoice } from './extension-voice.js';
import type { VoiceSnapshot, VoiceTransport } from './voice-sessions.js';

export interface VoiceProviderAdapter {
  available(): Promise<boolean>;
  realtimeSupport?(): Promise<{ supported: boolean; reason?: string; login?: 'ready' | 'required' }>;
}

export interface VoiceThreads {
  voiceProvider(threadId: string, fallbackProviderId?: string): { providerId: string };
  voiceTransport(threadId: string, authorize?: () => void, providerId?: string): VoiceTransport;
}

export interface VoiceProvider {
  id: string;
  available: boolean;
  supported: boolean;
  login?: 'ready' | 'required';
  reason?: 'unavailable' | 'unsupported' | 'probe_failed';
}

/** Production wiring for verified providers and ephemeral voice leases. No audio is stored. */
export class VoiceService {
  readonly sessions;
  private readonly runtime;
  private providers: VoiceProvider[] = [];
  private checkedAt = 0;
  private pending?: Promise<VoiceProvider[]>;
  private disposed = false;
  private timer?: ReturnType<typeof setInterval>;
  private identities = new Map<string, VoiceProviderAdapter>();

  constructor(private readonly options: {
    adapters: ReadonlyMap<string, VoiceProviderAdapter>;
    extensions: ExtensionHost;
    manager: VoiceThreads;
    refreshMs?: number;
    probeMs?: number;
  }) {
    this.runtime = createExtensionVoice({
      extensions: options.extensions,
      authorizeTarget: binding => {
        const target = options.manager.voiceProvider(binding.threadId, binding.providerId);
        const provider = this.providers.find(p => p.id === binding.providerId);
        if (binding.hostId !== 'local' || target.providerId !== binding.providerId
          || this.identities.get(binding.providerId) !== options.adapters.get(binding.providerId)
          || !provider?.available || !provider.supported || provider.login === 'required') {
          throw new HttpError(409, 'voice_provider_unavailable');
        }
      },
      create: binding => options.manager.voiceTransport(binding.threadId, () => {
        options.extensions.assertUsable(binding.extensionId);
      }, binding.providerId),
    });
    this.sessions = this.runtime.sessions;
  }

  start(): void {
    if (this.timer || this.disposed) return;
    void this.refresh();
    this.timer = setInterval(() => { void this.refresh(true); }, this.options.refreshMs ?? 30_000);
    this.timer.unref();
  }

  /** Deduplicate concurrent probes; replacement/removal invalidates the cached identity. */
  async refresh(force = false): Promise<VoiceProvider[]> {
    if (this.disposed) return [];
    if (this.pending) return this.pending;
    const candidates = [...this.options.adapters].filter(([, a]) => !!a.realtimeSupport);
    const same = candidates.length === this.identities.size
      && candidates.every(([id, a]) => this.identities.get(id) === a);
    if (!force && same && this.checkedAt && Date.now() - this.checkedAt < (this.options.refreshMs ?? 30_000)) {
      return this.providers.map(p => ({ ...p }));
    }
    this.pending = (async () => {
      // Replacing an account under the same instance id must end its old media lease before
      // a successful probe can make the replacement look like the original provider again.
      await this.sessions.revalidate();
      const providers = await Promise.all(candidates.map(async ([id, adapter]): Promise<VoiceProvider> => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
          return await Promise.race([
            (async (): Promise<VoiceProvider> => {
              if (!await adapter.available()) return { id, available: false, supported: false, reason: 'unavailable' };
              const result = await adapter.realtimeSupport!();
              return { id, available: true, supported: result.supported,
                ...(result.login === 'ready' || result.login === 'required' ? { login: result.login } : {}),
                ...(result.supported ? {} : { reason: 'unsupported' as const }) };
            })(),
            new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), this.options.probeMs ?? 10_000); }),
          ]);
        } catch { return { id, available: false, supported: false, reason: 'probe_failed' }; }
        finally { clearTimeout(timer); }
      }));
      if (this.disposed) return [];
      // A removed/replaced adapter cannot become available through a late probe result.
      this.identities = new Map(candidates.filter(([id, a]) => this.options.adapters.get(id) === a));
      const next = providers.filter(p => this.identities.has(p.id));
      const changed = !this.checkedAt || JSON.stringify(next) !== JSON.stringify(this.providers);
      this.providers = next;
      this.checkedAt = Date.now();
      const snapshot: ProviderCapabilitySnapshot[] = this.providers.map(p => ({
        providerId: p.id, available: p.available && p.supported,
        // A failed probe is an error for this declared capability, not evidence that the CLI
        // is missing. Availability remains false, so this cannot satisfy installation.
        capabilities: p.supported || p.reason === 'probe_failed' ? ['realtimeVoice'] : [],
        ...(p.reason === 'probe_failed' ? { error: 'probe_failed' } : {}),
      }));
      if (changed) this.options.extensions.reconcile(snapshot);
      await this.sessions.revalidate();
      return this.providers.map(p => ({ ...p }));
    })();
    try { return await this.pending; }
    finally { this.pending = undefined; }
  }

  async open(ownerId: string, body: unknown): Promise<VoiceSnapshot> {
    const input = body as { extensionId?: unknown; threadId?: unknown; offer?: unknown } | null;
    if (input?.extensionId !== 'voice' || typeof input.threadId !== 'string' || typeof input.offer !== 'string') {
      throw new HttpError(400, 'invalid_voice_request');
    }
    // Fail before probing or spawning anything when the extension was not explicitly installed.
    const extension = this.options.extensions.list().find(e => e.manifest.id === 'voice');
    if (!extension?.installed || !extension.enabled || !extension.granted.includes('voice.control')) throw new HttpError(403, 'voice_permission_required');
    await this.refresh(true);
    const fallback = this.providers.find(p => p.available && p.supported && p.login === 'ready')
      ?? this.providers.find(p => p.available && p.supported);
    const { providerId } = this.options.manager.voiceProvider(input.threadId, fallback?.id);
    if (this.providers.find(provider => provider.id === providerId)?.login === 'required') {
      throw new HttpError(409, 'voice_login_required');
    }
    return this.sessions.open({ extensionId: 'voice', hostId: 'local', providerId, threadId: input.threadId, ownerId }, input.offer);
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    clearInterval(this.timer);
    await this.runtime.dispose();
  }
}
