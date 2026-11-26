/** L0 Session & Quota Fabric additive codecs, read-only projections, and fence-bound handover checks. */
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
  type JsonObject,
} from "../runtime_decode.ts";
import { parseIsoTimestamp } from "../runtime_timestamp.ts";
import {
  assertPublicSafeFabricText,
  decodeSshHostObservationWith,
  GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
  GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA,
  isSafeHostAlias,
  requireSafeHostAlias,
  SAFE_HOST_ALIAS,
  SESSION_FABRIC_HANDOVER_DISPOSITIONS,
  SESSION_FABRIC_QUOTA_AVAILABILITIES,
  SESSION_FABRIC_SSH_CONNECTION_STATES,
  SESSION_FABRIC_SSH_FAILURE_CODES,
  SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
  SESSION_MODES,
  SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
  type GovernedSessionHandoverResult,
  type HandoverDisposition,
  type QuotaWindowAvailability,
  type SessionMode,
  type SessionQuotaWindowObservation,
  type SshConnectionState,
  type SshFailureCode,
  type SshHostObservation,
} from "./session_quota_fabric_contract.ts";

export {
  GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
  GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA,
  isSafeHostAlias,
  SAFE_HOST_ALIAS,
  SESSION_FABRIC_HANDOVER_DISPOSITIONS,
  SESSION_FABRIC_QUOTA_AVAILABILITIES,
  SESSION_FABRIC_SSH_CONNECTION_STATES,
  SESSION_FABRIC_SSH_FAILURE_CODES,
  SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
  SESSION_MODES,
  SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
  type GovernedSessionHandoverResult,
  type HandoverDisposition,
  type QuotaWindowAvailability,
  type SessionMode,
  type SessionQuotaWindowObservation,
  type SshConnectionState,
  type SshFailureCode,
  type SshHostObservation,
};

const OPAQUE_PROFILE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;

function failFabricRequest(message: string): never {
  throw new EffectRuntimeRequestError(message);
}

function assertPublicSafeText(value: string, label: string): string {
  return assertPublicSafeFabricText(value, label, failFabricRequest);
}

function requireHostAlias(value: unknown, label: string): string {
  return requireSafeHostAlias(value, label, failFabricRequest);
}

function requireOpaqueProfileId(value: unknown, label: string): string {
  const profileId = requireNonEmptyString(value, label).trim();
  if (profileId.includes("@") || profileId.includes("/") || !OPAQUE_PROFILE_ID.test(profileId)) {
    throw new EffectRuntimeRequestError(`${label} must be an opaque profile identifier`);
  }
  return assertPublicSafeText(profileId, label);
}

function optionalIsoTimestamp(value: unknown, label: string): string | null {
  const raw = optionalNonEmptyString(value, label);
  if (raw === null) return null;
  const trimmed = assertPublicSafeText(raw, label);
  if (parseIsoTimestamp(trimmed) === null) {
    throw new EffectRuntimeRequestError(`${label} must be a valid ISO timestamp`);
  }
  return trimmed;
}

export function decodeSshHostObservation(value: unknown): SshHostObservation {
  return decodeSshHostObservationWith(value, {
    fail: failFabricRequest,
    parseTimestamp: parseIsoTimestamp,
  });
}

/** Merge optional L0 readiness observations into `ssh_host_catalog_v0`.
 * When no observations are supplied, host entries stay `{ alias }` only for feature-off parity. */
export function projectSshHostCatalogPayload(
  configuredAliases: readonly string[],
  observations: readonly unknown[] = [],
): JsonObject {
  const aliases: string[] = [];
  const seenAliases = new Set<string>();
  for (let index = 0; index < configuredAliases.length; index += 1) {
    const alias = requireHostAlias(configuredAliases[index], `configured_aliases[${index}]`);
    if (seenAliases.has(alias)) continue;
    seenAliases.add(alias);
    aliases.push(alias);
  }
  const byAlias = new Map<string, SshHostObservation>();
  for (const item of observations) {
    const observation = decodeSshHostObservation(item);
    if (!seenAliases.has(observation.alias)) {
      throw new EffectRuntimeRequestError(
        `SSH observation target '${observation.alias}' is absent from ssh_host_catalog_v0`,
      );
    }
    if (byAlias.has(observation.alias)) {
      throw new EffectRuntimeRequestError(
        `duplicate SSH host observation for alias '${observation.alias}'`,
      );
    }
    byAlias.set(observation.alias, observation);
  }
  return {
    ok: true,
    schema_version: "ssh_host_catalog_v0",
    hosts: aliases.map((alias) => {
      const observation = byAlias.get(alias);
      return observation ? { alias, observation } : { alias };
    }),
  };
}

export function projectSshHostCatalogRequest(value: unknown): JsonObject {
  const raw = requireJsonObject(value, "ssh_host_catalog_projection_request");
  const configuredAliases = requireStringArray(
    raw.configured_aliases,
    "ssh_host_catalog_projection_request.configured_aliases",
  );
  const observations = raw.observations === undefined || raw.observations === null
    ? []
    : raw.observations;
  if (!Array.isArray(observations)) {
    throw new EffectRuntimeRequestError(
      "ssh_host_catalog_projection_request.observations must be an array",
    );
  }
  return projectSshHostCatalogPayload(configuredAliases, observations);
}

export function decodeSessionQuotaWindowObservation(
  value: unknown,
): SessionQuotaWindowObservation {
  const raw = requireJsonObject(value, "quota_window_observation");
  if (raw.schema_version !== SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA) {
    throw new EffectRuntimeRequestError("quota_window_observation schema_version mismatch");
  }
  const profileId = requireOpaqueProfileId(raw.profile_id, "quota_window_observation.profile_id");
  const availability = requireStringLiteral(
    raw.availability,
    SESSION_FABRIC_QUOTA_AVAILABILITIES,
    "quota_window_observation.availability",
  );
  let usedRatio: number | null = null;
  if (availability === "unknown") {
    if (raw.used_ratio !== null && raw.used_ratio !== undefined) {
      throw new EffectRuntimeRequestError("unknown quota window must have used_ratio=null");
    }
  } else {
    if (typeof raw.used_ratio !== "number" || !Number.isFinite(raw.used_ratio) ||
        raw.used_ratio < 0 || raw.used_ratio > 1) {
      throw new EffectRuntimeRequestError("quota_window_observation.used_ratio must be within [0, 1]");
    }
    usedRatio = raw.used_ratio;
  }
  const eligibleModels: string[] = [];
  const seenModels = new Set<string>();
  for (const model of requireStringArray(raw.eligible_models ?? [], "quota_window_observation.eligible_models")) {
    const clean = assertPublicSafeText(model, "quota_window_observation.eligible_models[]");
    if (!clean || seenModels.has(clean)) continue;
    seenModels.add(clean);
    eligibleModels.push(clean);
  }
  return {
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: profileId,
    availability,
    used_ratio: usedRatio,
    resets_at: optionalIsoTimestamp(raw.resets_at, "quota_window_observation.resets_at"),
    eligible_models: eligibleModels,
    handover_eligible: availability === "near_limit" || availability === "exhausted",
  };
}

/** Evaluate whether an observed profile quota window warrants a fenced handover proposal
 * without executing any switch, acquiring leases, or implicitly changing execution mode. */
export function evaluateGovernedSessionHandover(
  value: unknown,
): GovernedSessionHandoverResult {
  const raw = requireJsonObject(value, "governed_session_handover");
  if (raw.schema_version !== GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA) {
    throw new EffectRuntimeRequestError("governed_session_handover schema_version mismatch");
  }
  const goalId = assertPublicSafeText(requireNonEmptyString(raw.goal_id, "governed_session_handover.goal_id"), "goal_id");
  const todoId = assertPublicSafeText(requireNonEmptyString(raw.todo_id, "governed_session_handover.todo_id"), "todo_id");
  const agentId = assertPublicSafeText(requireNonEmptyString(raw.agent_id, "governed_session_handover.agent_id"), "agent_id");
  const fromProfile = requireOpaqueProfileId(raw.from_profile_id, "governed_session_handover.from_profile_id");
  const toProfile = requireOpaqueProfileId(raw.to_profile_id, "governed_session_handover.to_profile_id");
  if (fromProfile === toProfile) {
    throw new EffectRuntimeRequestError("from_profile_id and to_profile_id must differ");
  }
  const fromMode = requireStringLiteral(
    raw.from_session_mode,
    SESSION_MODES,
    "governed_session_handover.from_session_mode",
  );
  const toMode = requireStringLiteral(
    raw.to_session_mode,
    SESSION_MODES,
    "governed_session_handover.to_session_mode",
  );
  if (fromMode !== toMode) {
    throw new EffectRuntimeRequestError("session handover cannot implicitly change execution mode");
  }
  const observation = decodeSessionQuotaWindowObservation(raw.quota_observation);
  if (observation.profile_id !== fromProfile) {
    throw new EffectRuntimeRequestError("quota_observation.profile_id must match from_profile_id");
  }
  const threshold = raw.threshold_ratio;
  if (typeof threshold !== "number" || !Number.isFinite(threshold) || threshold <= 0 || threshold > 1) {
    throw new EffectRuntimeRequestError("governed_session_handover.threshold_ratio must be in (0, 1]");
  }
  const currentTurnFenced = requireBoolean(raw.current_turn_fenced, "governed_session_handover.current_turn_fenced");
  const previousLeaseReleased = requireBoolean(raw.previous_lease_released, "governed_session_handover.previous_lease_released");
  const askBeforeSwitch = requireBoolean(raw.ask_before_switch, "governed_session_handover.ask_before_switch");

  const fenceSatisfied = currentTurnFenced && previousLeaseReleased;
  let disposition: HandoverDisposition;
  let reason: string;
  if (!observation.handover_eligible) {
    disposition = "no_handover_needed";
    reason = observation.availability === "unknown"
      ? "quota window is unknown; fail closed with no switch"
      : "observed quota window is available; no handover needed";
  } else if (observation.availability === "near_limit" && (observation.used_ratio ?? 0) < threshold) {
    disposition = "no_handover_needed";
    reason = "observed quota usage is below handover threshold";
  } else if (askBeforeSwitch) {
    disposition = "user_gate_required";
    reason = "handover requires confirmation on the shared Goal Inbox/Gate projection";
  } else if (!fenceSatisfied) {
    disposition = "await_turn_fence";
    reason = "current turn must reach its fence and release the prior lease before reacquisition";
  } else {
    disposition = "ready_for_lease_reacquisition";
    reason = "prior turn fenced and lease released; target profile may reacquire the Todo lease";
  }

  return {
    schema_version: GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA,
    goal_id: goalId,
    todo_id: todoId,
    agent_id: agentId,
    session_mode: fromMode,
    from_profile_id: fromProfile,
    to_profile_id: toProfile,
    availability: observation.availability,
    used_ratio: observation.used_ratio,
    threshold_ratio: threshold,
    disposition,
    gate_required: disposition === "user_gate_required",
    turn_fence_satisfied: fenceSatisfied,
    ready_for_reacquisition: disposition === "ready_for_lease_reacquisition",
    reason,
  };
}
