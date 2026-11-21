import type { ExtensionHost } from './extension-host.js';
import { exactGrant } from './extensions.js';
import { HttpError } from './errors.js';
import { VoiceSessions, type VoiceSessionsOptions } from './voice-sessions.js';

export interface ExtensionVoiceOptions extends Omit<VoiceSessionsOptions, 'authorize'> {
  extensions: Pick<ExtensionHost, 'list' | 'onChange'>;
  /** Check the selected machine, provider, target and current native account, independently of install permission. */
  authorizeTarget: VoiceSessionsOptions['authorize'];
}

/**
 * Connect host-owned media leases to extension permissions, including view-only extensions
 * with no daemon subscriber. Nothing depends on delivery of extension.deactivating.
 * The daemon must await dispose() during shutdown, before releasing extension storage.
 */
export function createExtensionVoice(options: ExtensionVoiceOptions): { sessions: VoiceSessions; dispose(): Promise<void> } {
  const { extensions, authorizeTarget, ...limitsAndTransport } = options;
  const sessions = new VoiceSessions({
    ...limitsAndTransport,
    authorize(binding) {
      const extension = extensions.list().find(state => state.manifest.id === binding.extensionId);
      if (!extension || extension.source !== 'builtin' || !extension.installed || !extension.enabled
        || !extension.manifest.permissions.includes('voice.control')
        || !exactGrant(extension.manifest, extension.granted)) {
        throw new HttpError(403, 'Voice permission is not available');
      }
      authorizeTarget(binding);
    },
  });
  // revalidate transitions to stopping synchronously, before any later native payload can escape.
  const unsubscribe = extensions.onChange(() => { void sessions.revalidate(); });
  let disposal: Promise<void> | undefined;
  return {
    sessions,
    dispose() {
      if (disposal) return disposal;
      disposal = sessions.dispose();
      unsubscribe();
      return disposal;
    },
  };
}
