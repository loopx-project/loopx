import type {
  ExtensionManifest,
  ExtensionRequirements,
  ExtensionRequirementStatus,
  KnownAgentCapability,
} from '@calli/extension-api';

/** All currently known agent capabilities that can be declared in `requires.agentCapabilities`. */
export const KNOWN_AGENT_CAPABILITIES: ReadonlySet<KnownAgentCapability> = new Set<KnownAgentCapability>([
  'realtimeVoice',
]);

/**
 * Validates the raw `requires` object in an extension manifest.
 * Throws an Error if the declaration is invalid, contains duplicates, or contains unknown capabilities.
 */
export function checkExtensionRequirements(value: unknown): ExtensionRequirements | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('requires must be an object');
  }
  if (Object.keys(value).some(key => key !== 'agentCapabilities')) throw new Error('unknown requirement field');
  const req = value as Partial<ExtensionRequirements>;
  const caps = req.agentCapabilities;
  if (caps === undefined) return {};
  if (!Array.isArray(caps)) {
    throw new Error('requires.agentCapabilities must be a list');
  }
  const seen = new Set<string>();
  for (const c of caps) {
    if (typeof c !== 'string') {
      throw new Error('requires.agentCapabilities entries must be strings');
    }
    if (!KNOWN_AGENT_CAPABILITIES.has(c as KnownAgentCapability)) {
      throw new Error(`unknown required agent capability: ${c}`);
    }
    if (seen.has(c)) {
      throw new Error(`duplicate required agent capability: ${c}`);
    }
    seen.add(c);
  }
  return { agentCapabilities: [...caps] };
}

/** Verified runtime capability snapshot for a single provider on this machine. */
export interface ProviderCapabilitySnapshot {
  providerId: string;
  /** Whether the provider instance is operational and available for sessions */
  available: boolean;
  /** Capabilities verified on this provider instance */
  capabilities: readonly string[];
  /** Error detail if the provider failed verification */
  error?: string;
}

/** Synchronous resolver returning the current machine's provider capability snapshot. */
export type CapabilitySnapshotResolver = () => readonly ProviderCapabilitySnapshot[] | null | undefined;

/**
 * Evaluates whether an extension manifest's requirements are satisfied by the provider capability snapshot.
 *
 * Rules:
 * - Extensions without `requires` or with empty `agentCapabilities` are always `satisfied`.
 * - If no snapshot is provided (null or undefined), status is `unknown` (default unknown, never default satisfied).
 * - A single available provider must satisfy ALL required capabilities of the extension (no mixing across providers).
 * - Provider must have `available === true`.
 * - If an unavailable provider declares the capabilities and has an `error`, reports `error`.
 * - Otherwise reports `missing`.
 */
export function resolveRequirements(
  manifest: ExtensionManifest,
  snapshot: readonly ProviderCapabilitySnapshot[] | null | undefined,
): ExtensionRequirementStatus {
  const reqs = manifest.requires?.agentCapabilities;
  if (!reqs || reqs.length === 0) {
    return { state: 'satisfied' };
  }

  // Capability snapshot has not been provided or probed yet.
  if (snapshot === undefined || snapshot === null) {
    return {
      state: 'unknown',
      missing: [...reqs],
      reason: 'Agent provider capabilities have not been probed yet',
    };
  }

  // 1. Look for a single available provider that satisfies all required capabilities.
  for (const provider of snapshot) {
    if (!provider.available) continue;
    const provided = new Set(provider.capabilities);
    if (reqs.every((c) => provided.has(c))) {
      return {
        state: 'satisfied',
        providerId: provider.providerId,
      };
    }
  }

  // 2. Check if an unavailable provider matched the capabilities but failed verification.
  for (const provider of snapshot) {
    const provided = new Set(provider.capabilities);
    if (reqs.every((c) => provided.has(c))) {
      if (provider.error) {
        return {
          state: 'error',
          providerId: provider.providerId,
          missing: [...reqs],
          reason: 'Provider capability verification failed',
        };
      }
      return {
        state: 'missing',
        providerId: provider.providerId,
        missing: [...reqs],
        reason: `Provider ${provider.providerId} is not available`,
      };
    }
  }

  // 3. No provider has all required capabilities.
  return {
    state: 'missing',
    missing: [...reqs],
    reason: `No available provider satisfies required capabilities: ${reqs.join(', ')}`,
  };
}

/**
 * Manages capability resolver state and requirement resolution for ExtensionHost.
 */
export class ExtensionDependencyResolver {
  private manualSnapshot: readonly ProviderCapabilitySnapshot[] | null | undefined = undefined;

  constructor(private readonly resolver?: CapabilitySnapshotResolver) {}

  setSnapshot(snapshot: readonly ProviderCapabilitySnapshot[] | null | undefined): void {
    this.manualSnapshot = snapshot?.map(provider => ({ ...provider, capabilities: [...provider.capabilities] })) ?? snapshot;
  }

  currentSnapshot(): readonly ProviderCapabilitySnapshot[] | null | undefined {
    if (this.manualSnapshot !== undefined) {
      return this.manualSnapshot;
    }
    if (this.resolver) {
      return this.resolver();
    }
    return undefined;
  }

  resolve(manifest: ExtensionManifest): ExtensionRequirementStatus {
    // Unrelated extensions must work even if the capability probe is unavailable or broken.
    if (!manifest.requires?.agentCapabilities?.length) return { state: 'satisfied' };
    try { return resolveRequirements(manifest, this.currentSnapshot()); }
    catch {
      // Native probe errors may contain credentials or private paths. Never publish them.
      return { state: 'error', missing: [...manifest.requires.agentCapabilities], reason: 'Provider capability verification failed' };
    }
  }
}
