/** Shared zero-import L0 Session & Quota Fabric wire constants, closed vocabularies, and pure SSH observation codecs. */

export const SESSION_FABRIC_SSH_OBSERVATION_SCHEMA = "session_fabric_ssh_observation_v0";
export const SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA = "session_quota_window_observation_v0";
export const GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA = "governed_session_handover_proposal_v0";
export const GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA = "governed_session_handover_result_v0";

export const SESSION_MODES = ["managed_runtime", "attached_host"] as const;

export const SESSION_FABRIC_SSH_CONNECTION_STATES = [
  "connected",
  "degraded",
  "disconnected",
  "unknown",
] as const;

export const SESSION_FABRIC_SSH_FAILURE_CODES = [
  "no_ssh",
  "host_key",
  "auth",
  "unreachable",
  "ssh_failed",
  "daemon_missing",
  "forward_denied",
] as const;

export const SESSION_FABRIC_QUOTA_AVAILABILITIES = [
  "available",
  "near_limit",
  "exhausted",
  "unknown",
] as const;

export const SESSION_FABRIC_HANDOVER_DISPOSITIONS = [
  "no_handover_needed",
  "user_gate_required",
  "await_turn_fence",
  "ready_for_lease_reacquisition",
] as const;

export type SessionMode = (typeof SESSION_MODES)[number];
export type SshConnectionState = (typeof SESSION_FABRIC_SSH_CONNECTION_STATES)[number];
export type SshFailureCode = (typeof SESSION_FABRIC_SSH_FAILURE_CODES)[number];
export type QuotaWindowAvailability = (typeof SESSION_FABRIC_QUOTA_AVAILABILITIES)[number];
export type HandoverDisposition = (typeof SESSION_FABRIC_HANDOVER_DISPOSITIONS)[number];

/** Canonical TypeScript owner for safe explicit OpenSSH Host aliases (`ssh_host_catalog_v0`). */
export const SAFE_HOST_ALIAS = /^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$/;

const FORBIDDEN_CREDENTIAL_OR_PATH_PATTERNS: readonly RegExp[] = [
  /(?:\/(?:Users|home|private|tmp|var)\/|~[\\/]|[A-Za-z]:[\\/]|\\\\[A-Za-z0-9_.-]+\\|\.ssh[\\/])/i,
  /(?<![A-Za-z0-9])sk-(?:proj-|ant-|live-|test-|svcacct-|admin-)?[A-Za-z0-9_-]{8,}/i,
  /(?<![A-Za-z0-9])(?:sk|rk)_(?:live|test)_[A-Za-z0-9_-]{8,}/i,
  /(?<![A-Za-z0-9_])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{4,}/i,
  /\bbearer(?:\s+[^\s]+|\s*[:=]\s*[^\s]+)/i,
];

export interface SshHostObservation extends Record<string, unknown> {
  schema_version: typeof SESSION_FABRIC_SSH_OBSERVATION_SCHEMA;
  alias: string;
  connection_state: SshConnectionState;
  failure_code: SshFailureCode | null;
  checked_at: string | null;
  tunnel_latency_ms: number | null;
}

export interface SessionQuotaWindowObservation extends Record<string, unknown> {
  schema_version: typeof SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA;
  profile_id: string;
  availability: QuotaWindowAvailability;
  used_ratio: number | null;
  resets_at: string | null;
  eligible_models: string[];
  handover_eligible: boolean;
}

export interface GovernedSessionHandoverResult extends Record<string, unknown> {
  schema_version: typeof GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA;
  goal_id: string;
  todo_id: string;
  agent_id: string;
  session_mode: SessionMode;
  from_profile_id: string;
  to_profile_id: string;
  availability: QuotaWindowAvailability;
  used_ratio: number | null;
  threshold_ratio: number;
  disposition: HandoverDisposition;
  gate_required: boolean;
  turn_fence_satisfied: boolean;
  ready_for_reacquisition: boolean;
  reason: string;
}

export function isSafeHostAlias(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  return Boolean(trimmed) && !trimmed.includes("..") && SAFE_HOST_ALIAS.test(trimmed);
}

export function assertPublicSafeFabricText(
  value: string,
  label: string,
  fail: (message: string) => never,
): string {
  const trimmed = value.trim();
  if (FORBIDDEN_CREDENTIAL_OR_PATH_PATTERNS.some((pattern) => pattern.test(trimmed))) {
    throw fail(`${label} must not contain private paths or credentials`);
  }
  return trimmed;
}

export function requireSafeHostAlias(
  value: unknown,
  label: string,
  fail: (message: string) => never,
): string {
  if (typeof value !== "string" || !value.trim()) {
    throw fail(`${label} must be a non-empty string`);
  }
  const alias = assertPublicSafeFabricText(value, label, fail);
  if (!isSafeHostAlias(alias)) {
    throw fail(`${label} must be an explicit safe OpenSSH Host alias`);
  }
  return alias;
}

export function decodeSshHostObservationWith(
  value: unknown,
  options: {
    fail: (message: string) => never;
    parseTimestamp: (raw: string) => Date | null;
  },
): SshHostObservation {
  const { fail, parseTimestamp } = options;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw fail("ssh_host_observation must be an object");
  }
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== SESSION_FABRIC_SSH_OBSERVATION_SCHEMA) {
    throw fail("ssh_host_observation schema_version mismatch");
  }
  const alias = requireSafeHostAlias(raw.alias, "ssh_host_observation.alias", fail);
  const rawConnectionState = raw.connection_state;
  if (typeof rawConnectionState !== "string" || !rawConnectionState.trim()) {
    throw fail("ssh_host_observation.connection_state must be a non-empty string");
  }
  if (!(SESSION_FABRIC_SSH_CONNECTION_STATES as readonly string[]).includes(rawConnectionState)) {
    throw fail("ssh_host_observation.connection_state is unsupported");
  }
  const connectionState = rawConnectionState as SshConnectionState;

  let rawFailure: string | null = null;
  const rawFailureCode = raw.failure_code;
  if (rawFailureCode !== null && rawFailureCode !== undefined && rawFailureCode !== "") {
    if (typeof rawFailureCode !== "string" || !rawFailureCode.trim()) {
      throw fail("ssh_host_observation.failure_code must be a non-empty string");
    }
    rawFailure = rawFailureCode.trim();
  }

  let failureCode: SshFailureCode | null = null;
  if (connectionState === "degraded" || connectionState === "disconnected") {
    if (rawFailure === null || !(SESSION_FABRIC_SSH_FAILURE_CODES as readonly string[]).includes(rawFailure)) {
      throw fail("degraded or disconnected SSH host observations require a valid failure_code");
    }
    failureCode = rawFailure as SshFailureCode;
  } else if (rawFailure !== null) {
    throw fail("connected or unknown SSH host observations must not carry a failure_code");
  }

  const rawLatency = raw.tunnel_latency_ms ?? null;
  let latency: number | null = null;
  if (rawLatency !== null) {
    if (typeof rawLatency !== "number" || !Number.isSafeInteger(rawLatency) || rawLatency < 0) {
      throw fail("ssh_host_observation.tunnel_latency_ms must be a non-negative integer");
    }
    latency = rawLatency;
  }

  let checkedAt: string | null = null;
  const rawCheckedAt = raw.checked_at;
  if (rawCheckedAt !== null && rawCheckedAt !== undefined && rawCheckedAt !== "") {
    if (typeof rawCheckedAt !== "string" || !rawCheckedAt.trim()) {
      throw fail("ssh_host_observation.checked_at must be a non-empty string");
    }
    const cleanCheckedAt = assertPublicSafeFabricText(rawCheckedAt, "ssh_host_observation.checked_at", fail);
    if (parseTimestamp(cleanCheckedAt) === null) {
      throw fail("ssh_host_observation.checked_at must be a valid ISO timestamp");
    }
    checkedAt = cleanCheckedAt;
  }

  return {
    schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
    alias,
    connection_state: connectionState,
    failure_code: failureCode,
    checked_at: checkedAt,
    tunnel_latency_ms: latency,
  };
}
