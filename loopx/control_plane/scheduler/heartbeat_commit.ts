import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import {
  atomicWriteJson,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
import {
  goalRolloutEventLogPath,
  heartbeatReceiptStatus,
} from "../rollout_receipt_log.ts";
import {
  requireBoolean as requiredBoolean,
  requireInteger as requiredInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  buildSchedulerState,
  CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
  CODEX_APP_SURFACE,
  loadSchedulerState,
  mergeSchedulerHostUpdateFailure,
  migrateLegacySchedulerStateUnderLock,
  normalizeSchedulerHostUpdateFailures,
  normalizeSchedulerHostUpdateFailure,
  normalizeSchedulerRrule,
  normalizeSchedulerState,
  retainedSchedulerHostUpdateFailures,
  rruleForMinutes,
  schedulerRruleIntervalMinutes,
  schedulerStatePath,
  SCHEDULER_STATE_STORE_REQUEST_SCHEMA,
  type SchedulerScope,
} from "./state_store.ts";
import { evaluateSchedulerStateTransition } from "./state_transition_rules.ts";

export const SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA =
  "loopx_scheduler_heartbeat_commit_request_v0";
export const SCHEDULER_HEARTBEAT_COMMIT_RESULT_SCHEMA =
  "loopx_scheduler_heartbeat_commit_result_v0";
export const SCHEDULER_HEARTBEAT_COMMIT_RECEIPT_SCHEMA =
  "scheduler_heartbeat_commit_receipt_v0";
export const SCHEDULER_HEARTBEAT_HOST_FACTS_SCHEMA =
  "loopx_scheduler_heartbeat_host_facts_v0";
export const SCHEDULER_HEARTBEAT_COMMIT_ERROR_SCHEMA =
  "loopx_scheduler_heartbeat_commit_error_v0";
export const SCHEDULER_HEARTBEAT_COMMIT_OPERATIONS = [
  "ack",
  "host_failure",
] as const;
export type SchedulerHeartbeatCommitOperation =
  (typeof SCHEDULER_HEARTBEAT_COMMIT_OPERATIONS)[number];

export const SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES = 2;

export type SchedulerHeartbeatCommitStatus =
  "written" | "replayed" | "conflict" | "skipped" | "preview";

export interface SchedulerHeartbeatCommitRequest {
  schema_version: typeof SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA;
  operation: SchedulerHeartbeatCommitOperation;
  effect_id: string;
  runtime_root: string;
  scope: SchedulerScope;
  reset_token: string;
  identity_signature: string;
  progression_index: number;
  progression_minutes: number[];
  expected_rrule: string;
  applied_rrule: string;
  cadence_class: string;
  stale_tolerance_minutes: number;
  generated_at: string;
  expected_state_digest: string | null;
  execute: boolean;
  ack_needed: boolean | null;
  apply_needed: boolean | null;
  source: string;
  host_match_observed: boolean;
  failure_kind: string | null;
  observed_host_rrule: string;
  prior_host_update_failures: JsonObject[];
}

/**
 * Compact boundary payload used by non-TypeScript callers. State, CAS, and
 * operation identity are intentionally absent: the native transaction owns
 * those values and derives them from the scoped store and normalized facts.
 */
export interface SchedulerHeartbeatHostFactsRequest extends JsonObject {
  schema_version: typeof SCHEDULER_HEARTBEAT_HOST_FACTS_SCHEMA;
  operation?: SchedulerHeartbeatCommitOperation | "failure";
  outcome?: "ack" | "failure" | "host_failure";
  runtime_root: string;
  goal_id: string;
  agent_id: string;
  surface?: string;
  state_key?: string;
  reset_token: string;
  identity_signature: string;
  progression_index: number;
  progression_minutes: number[];
  expected_rrule?: string | null;
  target_rrule?: string | null;
  applied_rrule?: string | null;
  acknowledged_rrule?: string | null;
  cadence_class?: string;
  stale_tolerance_minutes?: number;
  generated_at: string;
  execute?: boolean;
  ack_needed?: boolean | null;
  apply_needed?: boolean | null;
  source?: string;
  host_match_observed?: boolean;
  failure_kind?: string | null;
  observed_host_rrule?: string | null;
  observed_rrule?: string | null;
  prior_host_update_failures?: JsonObject[];
  /** Optional caller-owned id for events that must be distinct by contract. */
  operation_id?: string;
  /** Explicitly named alias for callers that distinguish stable event ids. */
  stable_operation_id?: string;
  /** Accepted as a compatibility alias for operation_id. */
  effect_id?: string;
}

export interface SchedulerHeartbeatCommitError {
  schema_version: typeof SCHEDULER_HEARTBEAT_COMMIT_ERROR_SCHEMA;
  status: "error";
  error: {
    kind: string;
    code: string;
    message: string;
  };
}
interface CommitMetadata {
  schema_version: typeof SCHEDULER_HEARTBEAT_COMMIT_RECEIPT_SCHEMA;
  effect_id: string;
  request_digest: string;
  operation: SchedulerHeartbeatCommitOperation;
  goal_instance_id: string | null;
}

export interface SchedulerHeartbeatCommitResult extends JsonObject {
  schema_version: typeof SCHEDULER_HEARTBEAT_COMMIT_RESULT_SCHEMA;
  operation: SchedulerHeartbeatCommitOperation;
  effect_id: string;
  status: SchedulerHeartbeatCommitStatus;
  written: boolean;
  replayed: boolean;
  conflict: boolean;
  already_applied: boolean;
  path: string;
  state: JsonObject | null;
  expected_state_digest: string | null;
  actual_state_digest: string | null;
  state_digest: string | null;
  reason: string;
  reason_code?: string;
  expected_rrule?: string;
  applied_rrule?: string;
  target_rrule?: string;
  observed_host_rrule?: string;
  failure_count?: number;
  stale_hint_accepted?: boolean;
  stale_hint_tolerance_minutes?: number;
}

export interface SchedulerHeartbeatCommitOptions {
  readonly heartbeatReceiptTurnInstanceId?: string | null;
  readonly heartbeatReceiptGoalRef?: JsonObject | null;
  /** Project registry; supplies the current authoritative Goal lifetime. */
  readonly registryPath?: string | null;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, child]) => [key, stableValue(child)]),
  );
}

function sha256(value: string): string {
  return `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`;
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(stableValue(value));
}

export function schedulerHeartbeatCommitStateDigest(
  state: JsonObject | null,
): string | null {
  if (state === null) return null;
  const domainState = Object.fromEntries(
    Object.entries(state).filter(([key]) => key !== "heartbeat_commit"),
  );
  return sha256(canonicalJson(domainState));
}

// Aliases keep the digest useful to callers that name it after the state store.
export const schedulerStateDigest = schedulerHeartbeatCommitStateDigest;

function requestDigest(request: SchedulerHeartbeatCommitRequest): string {
  const semanticFacts: JsonObject = {
    schema_version: request.schema_version,
    operation: request.operation,
    runtime_root: request.runtime_root,
    goal_id: request.scope.goalId,
    agent_id: request.scope.agentId,
    surface: request.scope.surface,
    state_key: request.scope.stateKey,
    reset_token: request.reset_token,
    identity_signature: request.identity_signature,
    progression_index: request.progression_index,
    progression_minutes: request.progression_minutes,
    expected_rrule: request.expected_rrule,
    applied_rrule: request.applied_rrule,
    cadence_class: request.cadence_class,
    stale_tolerance_minutes: request.stale_tolerance_minutes,
    ack_needed: request.ack_needed,
    apply_needed: request.apply_needed,
    source: request.source,
    host_match_observed: request.host_match_observed,
    failure_kind: request.failure_kind,
    observed_host_rrule: request.observed_host_rrule,
  };
  if (request.operation === "host_failure") {
    // A host-failure event is distinct once the caller has observed the
    // previous failure cache. Keeping this compact observation in the digest
    // lets retries with the same facts replay while allowing the next failure
    // to increment the retained pair count.
    semanticFacts.prior_host_update_failures = request.prior_host_update_failures;
  }
  return sha256(canonicalJson(semanticFacts));
}

/**
 * Derive the default idempotency key from semantic scheduler facts. Clock,
 * CAS, and execute-mode observations are deliberately excluded so a retry
 * after a lost response replays the original commit instead of conflicting.
 */
export function schedulerHeartbeatOperationId(
  request: SchedulerHeartbeatCommitRequest,
): string {
  return `scheduler-heartbeat:${requestDigest(request).slice("sha256:".length)}`;
}

function textOrDefault(
  value: unknown,
  label: string,
  defaultValue: string,
): string {
  if (value === undefined || value === null || value === "") return defaultValue;
  return requiredString(value, label).trim();
}

function optionalBoolean(value: unknown, label: string): boolean | null {
  if (value === undefined || value === null) return null;
  return requiredBoolean(value, label);
}

function optionalDigest(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  return requiredString(value, "expected_state_digest").trim();
}

function positiveIntegerList(value: unknown, label: string): number[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty array of positive integers`);
  }
  const result = value.map((item, index) => {
    const number = requiredInteger(item, `${label}[${index}]`);
    if (number <= 0) {
      throw new EffectRuntimeRequestError(`${label} must be a non-empty array of positive integers`);
    }
    return number;
  });
  return result;
}

function optionalFailureList(value: unknown): JsonObject[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("prior_host_update_failures must be an array");
  }
  const normalized = value.map((candidate, index) => {
    const failure = normalizeSchedulerHostUpdateFailure(candidate);
    if (!failure) {
      throw new EffectRuntimeRequestError(
        `prior_host_update_failures[${index}] is malformed`,
      );
    }
    return failure;
  });
  return normalizeSchedulerHostUpdateFailures(normalized);
}

function requestObject(value: unknown): SchedulerHeartbeatCommitRequest {
  const request = requiredObject(value, "scheduler.heartbeat.commit params");
  if (request.schema_version !== SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Scheduler heartbeat commit request schema mismatch");
  }
  const operation = requireStringLiteral(
    request.operation,
    SCHEDULER_HEARTBEAT_COMMIT_OPERATIONS,
    "scheduler heartbeat commit operation",
    "Scheduler heartbeat commit operation is unsupported",
  );
  const effectId = requiredString(request.effect_id, "effect_id").trim();
  const runtimeRoot = requiredString(request.runtime_root, "runtime_root").trim();
  const goalId = requiredString(request.goal_id, "goal_id").trim();
  const agentId = requiredString(request.agent_id, "agent_id").trim();
  const surface = textOrDefault(request.surface, "surface", CODEX_APP_SURFACE);
  const stateKey = textOrDefault(
    request.state_key,
    "state_key",
    CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
  );
  const resetToken = requiredString(request.reset_token, "reset_token").trim();
  const identitySignature = requiredString(
    request.identity_signature,
    "identity_signature",
  ).trim();
  const progressionIndex = requiredInteger(
    request.progression_index,
    "progression_index",
  );
  const progressionMinutes = positiveIntegerList(
    request.progression_minutes,
    "progression_minutes",
  );
  if (progressionIndex < 0 || progressionIndex >= progressionMinutes.length) {
    throw new EffectRuntimeRequestError("progression_index must refer to progression_minutes");
  }
  const generatedAt = requiredString(request.generated_at, "generated_at").trim();
  const execute = request.execute === undefined
    ? true
    : requiredBoolean(request.execute, "execute");
  const ackNeeded = optionalBoolean(request.ack_needed, "ack_needed");
  const hostMatchObserved = request.host_match_observed === undefined
    ? false
    : requiredBoolean(request.host_match_observed, "host_match_observed");
  const source = textOrDefault(
    request.source,
    "source",
    operation === "host_failure"
      ? "quota_scheduler_host_update_failure"
      : "quota_scheduler_ack",
  );
  const staleToleranceMinutes = request.stale_tolerance_minutes === undefined
    ? SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES
    : requiredInteger(request.stale_tolerance_minutes, "stale_tolerance_minutes");
  if (staleToleranceMinutes < 0) {
    throw new EffectRuntimeRequestError("stale_tolerance_minutes must not be negative");
  }

  const derivedRrule = rruleForMinutes(progressionMinutes[progressionIndex]);
  const expectedRrule = normalizeSchedulerRrule(
    request.expected_rrule ?? request.target_rrule ?? derivedRrule,
  );
  if (!expectedRrule) throw new EffectRuntimeRequestError("expected_rrule must not be empty");
  const appliedRrule = normalizeSchedulerRrule(
    request.applied_rrule ?? request.acknowledged_rrule ?? "",
  );
  const observedHostRrule = normalizeSchedulerRrule(
    request.observed_host_rrule ?? request.observed_rrule ?? "",
  );
  const cadenceClass = textOrDefault(
    request.cadence_class,
    "cadence_class",
    "default",
  );
  const failureKind = request.failure_kind === undefined ||
      request.failure_kind === null || request.failure_kind === ""
    ? null
    : requiredString(request.failure_kind, "failure_kind").trim();
  const priorHostUpdateFailures = optionalFailureList(
    request.prior_host_update_failures,
  );
  return {
    schema_version: SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA,
    operation,
    effect_id: effectId,
    runtime_root: runtimeRoot,
    scope: { goalId, agentId, surface, stateKey },
    reset_token: resetToken,
    identity_signature: identitySignature,
    progression_index: progressionIndex,
    progression_minutes: progressionMinutes,
    expected_rrule: expectedRrule,
    applied_rrule: appliedRrule,
    cadence_class: cadenceClass,
    stale_tolerance_minutes: staleToleranceMinutes,
    generated_at: generatedAt,
    expected_state_digest: optionalDigest(request.expected_state_digest),
    execute,
    ack_needed: ackNeeded,
    apply_needed: optionalBoolean(request.apply_needed, "apply_needed"),
    source,
    host_match_observed: hostMatchObserved,
    failure_kind: failureKind,
    observed_host_rrule: observedHostRrule,
    prior_host_update_failures: priorHostUpdateFailures,
  };
}

function optionalExternalOperationId(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  return requiredString(value, "operation_id").trim();
}

function hostFactsRequest(value: unknown): {
  request: SchedulerHeartbeatCommitRequest;
  operation_id: string | null;
} {
  const input = requiredObject(value, "scheduler heartbeat host facts");
  if (input.schema_version !== SCHEDULER_HEARTBEAT_HOST_FACTS_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Scheduler heartbeat host facts schema mismatch",
    );
  }
  const nestedScope = input.scope && typeof input.scope === "object" &&
      !Array.isArray(input.scope)
    ? input.scope as Record<string, unknown>
    : {};
  const nestedAck = input.ack && typeof input.ack === "object" &&
      !Array.isArray(input.ack)
    ? input.ack as Record<string, unknown>
    : {};
  const nestedFailure = input.failure && typeof input.failure === "object" &&
      !Array.isArray(input.failure)
    ? input.failure as Record<string, unknown>
    : {};
  const goalId = input.goal_id ?? nestedScope.goal_id;
  const agentId = input.agent_id ?? nestedScope.agent_id;
  const operationValue = input.operation ?? input.outcome;
  const operation = operationValue === "failure" ||
      operationValue === "host_failure"
    ? "host_failure"
    : operationValue === "ack"
    ? "ack"
    : operationValue;
  const operationId = optionalExternalOperationId(
    input.operation_id ?? input.stable_operation_id ?? input.effect_id,
  );
  const operationPayload = operation === "host_failure"
    ? nestedFailure
    : nestedAck;
  const staleToleranceMinutes = input.stale_tolerance_minutes ??
    operationPayload.stale_tolerance_minutes ??
    operationPayload.stale_hint_tolerance_minutes;
  // requestObject remains the single decoder for the full transaction shape.
  // Host facts only fill state-derived CAS and effect identity below.
  const request = requestObject({
    ...input,
    ...operationPayload,
    schema_version: SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA,
    operation,
    effect_id: operationId ?? "scheduler-heartbeat-pending",
    runtime_root: input.runtime_root,
    goal_id: goalId,
    agent_id: agentId,
    surface: input.surface ?? nestedScope.surface,
    state_key: input.state_key ?? nestedScope.state_key,
    stale_tolerance_minutes: staleToleranceMinutes,
    expected_state_digest: null,
  });
  return { request, operation_id: operationId };
}

interface CurrentGoalInstance {
  readonly kind: "legacy" | "absent" | "instance" | "ambiguous" | "unverifiable";
  readonly goalInstanceId?: string;
  readonly active?: boolean;
  readonly issue?: string;
}

/**
 * The registered Goal lifetime that owns scheduler writes right now.
 *
 * Only the registry declares a current instance; a legacy registry keeps the
 * established goal-scoped path.  A registry that cannot be read is reported as
 * legacy so an unreadable local file does not invent an instance.
 */
async function readCurrentGoalInstance(
  registryPath: string,
  goalId: string,
): Promise<CurrentGoalInstance> {
  let text: string;
  try {
    text = await readFile(registryPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { kind: "absent" };
    }
    return {
      kind: "unverifiable",
      issue: "the project registry could not be read at commit time",
    };
  }
  let root: unknown;
  try {
    root = JSON.parse(text);
  } catch {
    return {
      kind: "unverifiable",
      issue: "the project registry is not valid JSON at commit time",
    };
  }
  const envelope = Array.isArray(root) && root.length === 2 ? root : null;
  const header = envelope === null ? null : envelope[0];
  const payload = envelope === null ? root : envelope[1];
  if (header !== null && (
    typeof header !== "object" || header === null ||
    typeof (header as JsonObject).schema_version !== "string" ||
    !String((header as JsonObject).schema_version)
      .startsWith("loopx_project_registry_envelope_")
  )) return { kind: "legacy" };
  if (typeof payload !== "object" || payload === null) return { kind: "legacy" };
  const goals = (payload as JsonObject).goals;
  if (!Array.isArray(goals)) return { kind: "legacy" };
  const matching = goals.filter((item) => (
    typeof item === "object" && item !== null &&
    (item as JsonObject).id === goalId
  ));
  if (matching.length === 0) return { kind: "absent" };
  // One registry entry names one current lifetime.  Duplicate entries are not
  // resolved by position: an arbitrary pick could authorize a superseded
  // lifetime or silently switch the owner mid-commit.
  if (matching.length > 1) {
    return {
      kind: "ambiguous",
      issue: "the project registry lists the Goal more than once",
    };
  }
  const goal = matching[0] as JsonObject;
  const instanceId = goal.goal_instance_id;
  if (typeof instanceId !== "string" || !/^ginst_[0-9a-f]{32}$/.test(instanceId)) {
    return { kind: "legacy" };
  }
  return {
    kind: "instance",
    goalInstanceId: instanceId,
    active: (goal as JsonObject).status === "active",
  };
}

function commitMetadata(state: JsonObject | null): CommitMetadata | null {
  if (!state) return null;
  const value = state.heartbeat_commit;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const metadata = value as Record<string, unknown>;
  if (
    metadata.schema_version !== SCHEDULER_HEARTBEAT_COMMIT_RECEIPT_SCHEMA ||
    typeof metadata.effect_id !== "string" ||
    typeof metadata.request_digest !== "string" ||
    (metadata.operation !== "ack" && metadata.operation !== "host_failure")
  ) return null;
  return {
    schema_version: SCHEDULER_HEARTBEAT_COMMIT_RECEIPT_SCHEMA,
    effect_id: metadata.effect_id,
    request_digest: metadata.request_digest,
    operation: metadata.operation,
    goal_instance_id: typeof metadata.goal_instance_id === "string"
      ? metadata.goal_instance_id
      : null,
  };
}

function withCommitMetadata(
  state: JsonObject,
  request: SchedulerHeartbeatCommitRequest,
  digest: string,
  goalInstanceId: string | null,
): JsonObject {
  return {
    ...state,
    heartbeat_commit: {
      schema_version: SCHEDULER_HEARTBEAT_COMMIT_RECEIPT_SCHEMA,
      effect_id: request.effect_id,
      request_digest: digest,
      operation: request.operation,
      ...(goalInstanceId === null ? {} : { goal_instance_id: goalInstanceId }),
    },
  };
}

function result(
  request: SchedulerHeartbeatCommitRequest,
  path: string,
  status: SchedulerHeartbeatCommitStatus,
  state: JsonObject | null,
  reason: string,
  extra: Partial<SchedulerHeartbeatCommitResult> = {},
): SchedulerHeartbeatCommitResult {
  const actualDigest = schedulerHeartbeatCommitStateDigest(state);
  return {
    schema_version: SCHEDULER_HEARTBEAT_COMMIT_RESULT_SCHEMA,
    operation: request.operation,
    effect_id: request.effect_id,
    status,
    written: status === "written",
    replayed: status === "replayed",
    conflict: status === "conflict",
    already_applied: status === "skipped",
    path,
    state,
    expected_state_digest: request.expected_state_digest,
    actual_state_digest: actualDigest,
    state_digest: actualDigest,
    reason,
    ...extra,
  };
}

function stateForScope(
  value: unknown,
  scope: SchedulerScope,
): JsonObject | null {
  return normalizeSchedulerState(value, scope);
}

function priorFailuresForRequest(
  request: SchedulerHeartbeatCommitRequest,
  existing: JsonObject | null,
): JsonObject[] {
  if (existing !== null) {
    return normalizeSchedulerHostUpdateFailures(
      existing.host_update_failures,
      existing.host_update_failure,
    );
  }
  return request.prior_host_update_failures;
}

async function readStateUnderLock(
  path: string,
  scope: SchedulerScope,
): Promise<JsonObject | null> {
  try {
    const parsed: unknown = JSON.parse(await readFile(path, "utf8"));
    const state = stateForScope(parsed, scope);
    if (!state) throw new Error("persisted scheduler state is invalid");
    return state;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    if (error instanceof SyntaxError) {
      throw new Error("persisted scheduler state is not valid JSON");
    }
    throw error;
  }
}

function transitionAllowsStaleAck(
  request: SchedulerHeartbeatCommitRequest,
  existing: JsonObject | null,
): boolean {
  if (request.cadence_class !== "monitor_wait") return false;
  const appliedMinutes = schedulerRruleIntervalMinutes(request.applied_rrule);
  const expectedMinutes = schedulerRruleIntervalMinutes(request.expected_rrule);
  if (
    appliedMinutes === null ||
    expectedMinutes === null ||
    appliedMinutes < expectedMinutes ||
    appliedMinutes - expectedMinutes > request.stale_tolerance_minutes
  ) return false;
  // The transition kernel owns the monitor tolerance calculation. The caller's
  // identity proof remains explicit below and is never inferred from prose.
  const syntheticState: JsonObject = {
    ...(existing ?? {}),
    progression_index: request.progression_index,
    progression_minutes: request.progression_minutes,
    last_applied_rrule: request.applied_rrule,
    reset_token: request.reset_token,
    identity_signature: request.identity_signature,
    updated_at: request.generated_at,
  };
  const transition = evaluateSchedulerStateTransition({
    schema_version: "loopx_scheduler_state_transition_request_v0",
    operation: "backoff",
    progression_minutes: request.progression_minutes,
    scheduler_state: syntheticState,
    reset_token: request.reset_token,
    identity_signature: request.identity_signature,
    advance_same_identity: true,
    current_time: request.generated_at,
    observed_host_rrule: "",
    cadence_class: request.cadence_class,
    stale_tolerance_minutes: request.stale_tolerance_minutes,
  });
  if (
    transition.current_rrule !== request.expected_rrule ||
    !transition.current_rrule_already_applied
  ) return false;
  const proofMatchesExisting = existing !== null &&
    existing.reset_token === request.reset_token &&
    existing.identity_signature === request.identity_signature;
  return proofMatchesExisting || existing === null;
}

function isStaleMonitorAck(
  request: SchedulerHeartbeatCommitRequest,
): boolean {
  return request.operation === "ack" &&
    request.cadence_class === "monitor_wait" &&
    request.applied_rrule !== request.expected_rrule;
}

function buildAckState(
  request: SchedulerHeartbeatCommitRequest,
  existing: JsonObject | null,
  path: string,
  fingerprint: string,
  goalInstanceId: string | null,
): { state: JsonObject; extra: Partial<SchedulerHeartbeatCommitResult> } |
  SchedulerHeartbeatCommitResult {
  if (request.ack_needed === false && request.apply_needed === false) {
    return result(
      request,
      path,
      "skipped",
      existing,
      "scheduler RRULE already applied; no ack write needed",
    );
  }
  const derivedRrule = rruleForMinutes(
    request.progression_minutes[request.progression_index],
  );
  if (request.expected_rrule !== derivedRrule) {
    return result(
      request,
      path,
      "conflict",
      existing,
      "expected_rrule does not match the progression target",
      {
        reason_code: "target_rrule_conflict",
        expected_rrule: derivedRrule,
        applied_rrule: request.applied_rrule,
      },
    );
  }
  const exact = request.applied_rrule === request.expected_rrule;
  if (
    !exact &&
    request.cadence_class === "monitor_wait" &&
    existing !== null &&
    (existing.reset_token !== request.reset_token ||
      existing.identity_signature !== request.identity_signature)
  ) {
    return result(
      request,
      path,
      "conflict",
      existing,
      "stale monitor ACK identity does not match persisted scheduler state",
      { reason_code: "identity_conflict" },
    );
  }
  if (!exact && !transitionAllowsStaleAck(request, existing)) {
    return result(
      request,
      path,
      "conflict",
      existing,
      "applied_rrule does not match the scheduler target",
      {
        reason_code: "rrule_mismatch",
        expected_rrule: request.expected_rrule,
        applied_rrule: request.applied_rrule,
      },
    );
  }
  if (!request.applied_rrule) {
    return result(
      request,
      path,
      "conflict",
      existing,
      "ack requires applied_rrule",
      { reason_code: "missing_applied_rrule" },
    );
  }
  const priorFailures = retainedSchedulerHostUpdateFailures(
    priorFailuresForRequest(request, existing),
    request.generated_at,
    request.applied_rrule,
  ).filter((failure) =>
    normalizeSchedulerRrule(failure.target_rrule) !== request.applied_rrule
  );
  let state = buildSchedulerState({
    goal_id: request.scope.goalId,
    agent_id: request.scope.agentId,
    surface: request.scope.surface,
    state_key: request.scope.stateKey,
    reset_token: request.reset_token,
    identity_signature: request.identity_signature,
    progression_index: request.progression_index,
    progression_minutes: request.progression_minutes,
    last_applied_rrule: request.applied_rrule,
    updated_at: request.generated_at,
    source: request.source,
    host_update_failures: priorFailures.length ? priorFailures : null,
  });
  state = withCommitMetadata(state, request, fingerprint, goalInstanceId);
  return {
    state,
    extra: {
      expected_rrule: request.expected_rrule,
      applied_rrule: request.applied_rrule,
      stale_hint_accepted: !exact,
      stale_hint_tolerance_minutes: request.stale_tolerance_minutes,
    },
  };
}

function buildHostFailureState(
  request: SchedulerHeartbeatCommitRequest,
  existing: JsonObject | null,
  path: string,
  fingerprint: string,
  goalInstanceId: string | null,
): { state: JsonObject; extra: Partial<SchedulerHeartbeatCommitResult> } |
  SchedulerHeartbeatCommitResult {
  if (request.apply_needed !== true) {
    return result(
      request,
      path,
      "conflict",
      existing,
      "scheduler host update failure is not recordable because no host update is needed",
      { reason_code: "host_update_not_needed" },
    );
  }
  const derivedRrule = rruleForMinutes(
    request.progression_minutes[request.progression_index],
  );
  if (!request.expected_rrule || request.expected_rrule !== derivedRrule) {
    return result(
      request,
      path,
      "conflict",
      existing,
      "target_rrule does not match the progression target",
      { reason_code: "target_rrule_conflict", expected_rrule: derivedRrule },
    );
  }
  const targetRrule = request.expected_rrule;
  const observedHostRrule = request.observed_host_rrule;
  const priorFailures = retainedSchedulerHostUpdateFailures(
    priorFailuresForRequest(request, existing),
    request.generated_at,
    observedHostRrule,
  );
  const priorFailure = [...priorFailures].reverse().find((failure) =>
    normalizeSchedulerRrule(failure.target_rrule) === targetRrule
  );
  const priorCount = typeof priorFailure?.failure_count === "number"
    ? priorFailure.failure_count
    : 0;
  const failure = {
    schema_version: "scheduler_host_update_failure_v0",
    target_rrule: targetRrule,
    observed_host_rrule: observedHostRrule,
    failure_kind: request.failure_kind || "host_tool_failure",
    failure_count: priorCount + 1,
    failed_at: request.generated_at,
  } satisfies JsonObject;
  const failures = mergeSchedulerHostUpdateFailure(
    priorFailures,
    failure,
    request.generated_at,
  );
  let state = buildSchedulerState({
    goal_id: request.scope.goalId,
    agent_id: request.scope.agentId,
    surface: request.scope.surface,
    state_key: request.scope.stateKey,
    reset_token: request.reset_token,
    identity_signature: request.identity_signature,
    progression_index: request.progression_index,
    progression_minutes: request.progression_minutes,
    last_applied_rrule: observedHostRrule,
    updated_at: request.generated_at,
    source: request.source,
    host_update_failure: failure,
    host_update_failures: failures,
  });
  state = withCommitMetadata(state, request, fingerprint, goalInstanceId);
  return {
    state,
    extra: {
      target_rrule: targetRrule,
      observed_host_rrule: observedHostRrule,
      failure_count: priorCount + 1,
    },
  };
}

interface GoalLifetimeRejection {
  readonly kind: "reject";
  readonly message: string;
  readonly reasonCode: string;
}

type GoalLifetimeDecision =
  | { readonly kind: "accept"; readonly goalInstanceId: string | null }
  | GoalLifetimeRejection;

/**
 * Which Goal lifetime the project registry lets this commit write.
 *
 * A Goal that declares a lifetime cannot be written by a receipt that names
 * none: an older host's report would otherwise land on whichever instance is
 * current now.  A registry without instance identity keeps the established
 * goal-scoped path.
 */
function decideGoalLifetime(
  current: CurrentGoalInstance,
  receiptGoalRef: JsonObject | null,
): GoalLifetimeDecision {
  if (receiptGoalRef === null) {
    return current.kind === "instance"
      ? {
        kind: "reject",
        message:
          "scheduler writeback from this Goal lifetime must name the current Goal reference",
        reasonCode: "heartbeat_receipt_goal_ref_missing",
      }
      : { kind: "accept", goalInstanceId: null };
  }
  switch (current.kind) {
    case "absent":
      return {
        kind: "reject",
        message:
          "scheduler writeback requires the registered Goal; it is not in the project registry",
        reasonCode: "goal_instance_absent",
      };
    case "unverifiable":
      return {
        kind: "reject",
        message: `the current Goal lifetime is not verifiable from the project registry: ${
          current.issue ?? "unknown registry state"
        }`,
        reasonCode: "goal_instance_unverifiable",
      };
    case "ambiguous":
      return {
        kind: "reject",
        message: `the current Goal lifetime is not verifiable from the project registry: ${
          current.issue ?? "duplicate entries"
        }`,
        reasonCode: "goal_instance_ambiguous",
      };
    case "instance":
      if (current.active !== true) {
        return {
          kind: "reject",
          message:
            "the Goal is not active in the project registry; refusing scheduler writeback from its previous lifetime",
          reasonCode: "goal_instance_inactive",
        };
      }
      if (receiptGoalRef.goal_instance_id !== current.goalInstanceId) {
        return {
          kind: "reject",
          message:
            "heartbeat receipt belongs to a superseded Goal instance; refusing scheduler writeback",
          reasonCode: "heartbeat_receipt_goal_instance_superseded",
        };
      }
      // An `instance` row without an id cannot be matched against the receipt's
      // Goal reference; the null keeps the state field absent rather than
      // writing a lifetime the registry did not name.
      return { kind: "accept", goalInstanceId: current.goalInstanceId ?? null };
    case "legacy":
      return { kind: "accept", goalInstanceId: null };
  }
}

/** Registry transactions hold this marker across their whole read-modify-write. */
const GOAL_REGISTRY_FENCE_TIMEOUT_MS = 60_000;

/**
 * Rename scheduler state only while the registry still names its lifetime.
 *
 * The read above is a fast rejection; this fence is the authoritative one.
 * Python registry transactions take the same cross-runtime mutation marker
 * (`<registry>.ts-effect.lock`), so a Goal recreation either publishes its new
 * lifetime before this read or waits for the state rename to finish.  Without
 * the marker a recreation could land between the two and let a superseded
 * lifetime overwrite the state its replacement already wrote.
 */
async function writeStateUnderRegistryFence(params: {
  registryPath: string | null;
  goalId: string;
  receiptGoalRef: JsonObject | null;
  goalInstanceId: string | null;
  write: () => Promise<void>;
}): Promise<GoalLifetimeRejection | null> {
  const fenced = async (): Promise<GoalLifetimeRejection | null> => {
    if (params.registryPath !== null) {
      const current = await readCurrentGoalInstance(
        params.registryPath, params.goalId,
      );
      const lifetime = decideGoalLifetime(current, params.receiptGoalRef);
      if (lifetime.kind === "reject") return lifetime;
      if (lifetime.goalInstanceId !== params.goalInstanceId) {
        return {
          kind: "reject",
          message:
            "the Goal lifetime changed before the scheduler commit was written; refusing scheduler writeback",
          reasonCode: "heartbeat_receipt_goal_instance_superseded",
        };
      }
    }
    await params.write();
    return null;
  };
  if (params.registryPath === null) return await fenced();
  return await withFileMutationLock(
    params.registryPath, fenced, GOAL_REGISTRY_FENCE_TIMEOUT_MS,
  );
}

export async function evaluateSchedulerHeartbeatCommit(
  value: unknown,
  options: SchedulerHeartbeatCommitOptions = {},
): Promise<SchedulerHeartbeatCommitResult> {
  const request = requestObject(value);
  const receiptTurnInstanceId = options.heartbeatReceiptTurnInstanceId === null ||
      options.heartbeatReceiptTurnInstanceId === undefined
    ? null
    : requiredString(
      options.heartbeatReceiptTurnInstanceId,
      "heartbeatReceiptTurnInstanceId",
    ).trim();
  let receiptGoalRef: JsonObject | null = null;
  if (options.heartbeatReceiptGoalRef !== undefined &&
      options.heartbeatReceiptGoalRef !== null) {
    const parsed = parseExactGoalRef(options.heartbeatReceiptGoalRef);
    if (parsed.kind === "invalid") {
      throw new EffectRuntimeRequestError(
        `heartbeat receipt goal_ref is invalid: ${parsed.issue}`,
        "heartbeat_receipt_goal_ref_invalid",
      );
    }
    receiptGoalRef = {
      goal_id: parsed.value.goalId.value,
      goal_instance_id: parsed.value.goalInstanceId.value,
    };
    if (receiptGoalRef.goal_id !== request.scope.goalId) {
      throw new EffectRuntimeRequestError(
        "heartbeat receipt goal_ref does not match the scheduler scope",
        "heartbeat_receipt_goal_ref_mismatch",
      );
    }
  }
  const registryPath = options.registryPath === null || options.registryPath === undefined
    ? null
    : requiredString(options.registryPath, "registryPath").trim();
  const path = schedulerStatePath(request.runtime_root, request.scope);
  const fingerprint = requestDigest(request);
  // Legacy state stays read-only until the commit holds every applicable
  // authority fence.
  const loaded = await loadSchedulerState({
    schema_version: SCHEDULER_STATE_STORE_REQUEST_SCHEMA,
    runtime_root: request.runtime_root,
    goal_id: request.scope.goalId,
    agent_id: request.scope.agentId,
    surface: request.scope.surface,
    state_key: request.scope.stateKey,
    migrate_legacy: false,
  });
  const commit = async (): Promise<SchedulerHeartbeatCommitResult> => {
    const canonical = await readStateUnderLock(path, request.scope);
    let existing = canonical ?? loaded.state;
    // One owner decides which Goal lifetime may still write scheduler state.
    // The registry is read under the state lock, before any receipt or replay
    // read, so an replaced or cancelled lifetime cannot overwrite or replay a
    // newer instance's commit with the same Goal/Agent/surface/state key.
    let goalInstanceId: string | null = null;
    if (registryPath !== null) {
      // One owner decides which Goal lifetime may still write scheduler state.
      // The registry is read under the state lock, before any receipt or
      // replay read, so an replaced or cancelled lifetime cannot overwrite or
      // replay a newer instance's commit with the same Goal/Agent/surface/state
      // key.  The rename repeats the same decision under the registry marker
      // lock, because a Goal recreation can publish a new lifetime while this
      // commit is still preparing.
      const current = await readCurrentGoalInstance(
        registryPath, request.scope.goalId,
      );
      const lifetime = decideGoalLifetime(current, receiptGoalRef);
      if (lifetime.kind === "reject") {
        return result(
          request, path, "conflict", existing, lifetime.message,
          { reason_code: lifetime.reasonCode },
        );
      }
      goalInstanceId = lifetime.goalInstanceId;
    }
    if (receiptTurnInstanceId !== null) {
      const status = await heartbeatReceiptStatus({
        runtimeRoot: request.runtime_root,
        goalId: request.scope.goalId,
        agentId: request.scope.agentId,
        turnInstanceId: receiptTurnInstanceId,
        goalRef: receiptGoalRef,
      });
      if (status !== "fresh") {
        return result(
          request,
          path,
          "conflict",
          existing,
          status === "missing"
            ? "originating heartbeat receipt is missing at scheduler commit"
            : "originating heartbeat receipt was superseded before scheduler commit",
          { reason_code: `heartbeat_receipt_${status}` },
        );
      }
    }
    if (
      request.operation === "ack" && request.host_match_observed &&
      (!request.observed_host_rrule || request.observed_host_rrule !== request.applied_rrule)
    ) {
      return result(
        request, path, "conflict", existing,
        "host-match ACK requires a current observation matching applied_rrule",
        {
          reason_code: "rrule_mismatch",
          applied_rrule: request.applied_rrule,
          observed_host_rrule: request.observed_host_rrule,
        },
      );
    }
    if (request.execute && canonical === null) {
      existing = await migrateLegacySchedulerStateUnderLock(
        request.runtime_root,
        request.scope,
      );
    }
    const existingDigest = schedulerHeartbeatCommitStateDigest(existing);
    const metadata = commitMetadata(existing);
    if (metadata?.effect_id === request.effect_id) {
      const committedInstance = typeof metadata.goal_instance_id === "string"
        ? metadata.goal_instance_id
        : null;
      if (committedInstance !== goalInstanceId) {
        return result(
          request, path, "conflict", existing,
          "effect_id is already bound to a scheduler commit from another Goal instance",
          { reason_code: "goal_instance_replay_conflict" },
        );
      }
      if (
        metadata.operation === request.operation &&
        metadata.request_digest === fingerprint
      ) {
        return result(
          request,
          path,
          "replayed",
          existing,
          "scheduler heartbeat commit replayed",
        );
      }
      return result(
        request,
        path,
        "conflict",
        existing,
        "effect_id is already bound to a different scheduler heartbeat commit",
        { reason_code: "effect_id_conflict" },
      );
    }
    if (request.expected_state_digest !== existingDigest) {
      return result(
        request,
        path,
        "conflict",
        existing,
        "scheduler state compare-and-swap precondition failed",
        { reason_code: "state_digest_conflict" },
      );
    }
    const identityMatches = existing === null || (
      existing.reset_token === request.reset_token &&
      existing.identity_signature === request.identity_signature
    );
    // A changed scheduler identity starts a fresh progression. An observed
    // host match grants that reset authority only to an exact ACK: a host
    // failure cannot also claim that the target RRULE is already applied.
    // Stale monitor ACKs remain bound to the persisted identity. Initial and
    // identity-reset commits must begin at index zero.
    const exactHostMatchAck = request.operation === "ack" &&
      request.host_match_observed &&
      request.applied_rrule === request.expected_rrule;
    const identityReset = existing !== null && !identityMatches &&
      !isStaleMonitorAck(request) &&
      (!request.host_match_observed || exactHostMatchAck);
    if (existing !== null && !identityMatches && !identityReset) {
      return result(
        request,
        path,
        "conflict",
        existing,
        "scheduler heartbeat identity does not match persisted state",
        { reason_code: "identity_conflict" },
      );
    }
    if (
      (existing === null || identityReset) &&
      request.progression_index !== 0
    ) {
      return result(
        request,
        path,
        "conflict",
        existing,
        "scheduler heartbeat initial or identity-reset progression must start at index zero",
        { reason_code: "initial_progression_index_conflict" },
      );
    }
    if (
      existing !== null &&
      identityMatches &&
      typeof existing.progression_index === "number" &&
      request.progression_index < existing.progression_index
    ) {
      return result(
        request,
        path,
        "conflict",
        existing,
        "scheduler heartbeat progression would move backwards",
        { reason_code: "progression_regression" },
      );
    }
    if (
      existing !== null &&
      identityMatches &&
      typeof existing.progression_index === "number" &&
      request.progression_index > existing.progression_index + 1
    ) {
      return result(
        request,
        path,
        "conflict",
        existing,
        "scheduler heartbeat progression cannot skip a cadence stage",
        { reason_code: "progression_skip_conflict" },
      );
    }
    const built = request.operation === "ack"
      ? buildAckState(request, existing, path, fingerprint, goalInstanceId)
      : buildHostFailureState(request, existing, path, fingerprint, goalInstanceId);
    if ("status" in built) return built;
    if (!request.execute) {
      return result(
        request,
        path,
        "preview",
        built.state,
        "scheduler heartbeat commit preview",
        built.extra,
      );
    }
    const fence = await writeStateUnderRegistryFence({
      registryPath,
      goalId: request.scope.goalId,
      receiptGoalRef,
      goalInstanceId,
      write: async () => {
        await atomicWriteJson(path, stableValue(built.state) as JsonObject);
      },
    });
    if (fence !== null) {
      return result(
        request,
        path,
        "conflict",
        existing,
        fence.message,
        { reason_code: fence.reasonCode },
      );
    }
    return result(
      request,
      path,
      "written",
      built.state,
      "scheduler heartbeat commit written",
      built.extra,
    );
  };
  return await withFileMutationLock(path, async () => {
    if (receiptTurnInstanceId === null) return await commit();
    // Keep the established state-first order: a follow-up waiting for state
    // does not block a newer receipt from landing. Once both locks are held,
    // freshness and the final state rename are one cross-runtime operation.
    return await withFileMutationLock(
      goalRolloutEventLogPath(request.runtime_root, request.scope.goalId),
      commit,
    );
  });
}

/**
 * Native transaction boundary for the scheduler facade exit. Callers provide
 * only normalized scheduler/host observations; the transaction owns state
 * loading, legacy migration, CAS calculation, operation identity, and the
 * locked commit kernel.
 */
export async function evaluateSchedulerHeartbeatHostFacts(
  value: unknown,
  options: SchedulerHeartbeatCommitOptions = {},
): Promise<SchedulerHeartbeatCommitResult> {
  const { request: facts, operation_id: externalOperationId } =
    hostFactsRequest(value);
  const loaded = await loadSchedulerState({
    schema_version: SCHEDULER_STATE_STORE_REQUEST_SCHEMA,
    runtime_root: facts.runtime_root,
    goal_id: facts.scope.goalId,
    agent_id: facts.scope.agentId,
    surface: facts.scope.surface,
    state_key: facts.scope.stateKey,
    migrate_legacy: false,
  });
  const request: SchedulerHeartbeatCommitRequest = {
    ...facts,
    effect_id: externalOperationId ?? schedulerHeartbeatOperationId(facts),
    expected_state_digest: schedulerHeartbeatCommitStateDigest(loaded.state),
  };
  return await evaluateSchedulerHeartbeatCommit(
    {
      ...request,
      goal_id: request.scope.goalId,
      agent_id: request.scope.agentId,
      surface: request.scope.surface,
      state_key: request.scope.stateKey,
    },
    options,
  );
}
