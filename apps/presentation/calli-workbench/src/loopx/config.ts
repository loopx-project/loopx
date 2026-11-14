/**
 * Adapter endpoints and polling cadence.
 *
 * The two LoopX local services are fixed by the same convention the LoopX
 * dashboard's vite proxy uses: /status.json -> :8766, /api/chat -> :8767.
 * The bases default to the page's own origin: the dev proxy forwards the paths.
 */

export interface LoopXAdapterConfig {
  /** Main read-only projection (status.json, /healthz, /ssh-hosts). */
  statusBase: string;
  /** Chat surface (/api/chat/sessions, capabilities, endpoints, ...). */
  chatBase: string;
  /** Stream polling interval. Dashboard conversation cadence is 5s. */
  pollIntervalMs: number;
  /** Backoff ceiling while a poll target keeps failing. */
  pollBackoffMaxMs: number;
  /** Per-request timeout. */
  requestTimeoutMs: number;
}

export function loadConfig(overrides: Partial<LoopXAdapterConfig> = {}): LoopXAdapterConfig {
  return {
    statusBase: '',
    chatBase: '',
    // Matches the dashboard conversation-queue read cadence (5s, backoff to 30s).
    pollIntervalMs: 5_000,
    pollBackoffMaxMs: 30_000,
    requestTimeoutMs: 30_000,
    ...overrides,
  };
}
