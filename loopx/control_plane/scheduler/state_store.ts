import { createHash } from "node:crypto";
import { readFile, unlink } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  atomicWriteJson,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
import {
  assertNever,
  jsonObject,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringLiteral,
} from "../runtime_decode.ts";

export const SCHEDULER_STATE_SCHEMA_VERSION = "loopx_scheduler_state_v0";
export const SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION =
  "scheduler_host_update_failure_v0";
export const SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA =
  "loopx_scheduler_state_operation_request_v0";
export const SCHEDULER_STATE_OPERATION_RESULT_SCHEMA =
  "loopx_scheduler_state_operation_result_v0";
export const SCHEDULER_STATE_STORE_REQUEST_SCHEMA =
  "loopx_scheduler_state_store_request_v0";
export const SCHEDULER_STATE_STORE_RESULT_SCHEMA =
  "loopx_scheduler_state_store_result_v0";

export const APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY =
  "scheduler_hint.app_automation.stateful_backoff";
export const LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY =
  "scheduler_hint.codex_app.stateful_backoff";
export const CODEX_APP_STATEFUL_BACKOFF_STATE_KEY =
  LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY;
export const CODEX_APP_SURFACE = "codex_app";

const HOST_UPDATE_FAILURE_CACHE_LIMIT = 4;
const HOST_UPDATE_FAILURE_TTL_MS = 24 * 60 * 60 * 1_000;
const SCOPE_SEGMENT_LABEL_LIMIT = 47;
const SCOPE_SEGMENT_HASH_LENGTH = 16;

export const SCHEDULER_STATE_OPERATIONS = [
  "rrule_for_minutes",
  "normalize_rrule",
  "rrule_interval_minutes",
  "normalize_failure",
  "normalize_failures",
  "retain_failures",
  "merge_failure",
  "normalize_state",
  "build_state",
  "state_path",
] as const;
export type SchedulerStateOperation =
  (typeof SCHEDULER_STATE_OPERATIONS)[number];

export interface SchedulerScope {
  goalId: string;
  agentId: string;
  surface: string;
  stateKey: string;
}

export interface SchedulerStateOperationResult {
  schema_version: typeof SCHEDULER_STATE_OPERATION_RESULT_SCHEMA;
  operation: SchedulerStateOperation;
  value: unknown;
}

export interface SchedulerStateLoadResult {
  schema_version: typeof SCHEDULER_STATE_STORE_RESULT_SCHEMA;
  operation: "load";
  path: string;
  state: JsonObject | null;
}

interface SchedulerStateWriteResultBase {
  schema_version: typeof SCHEDULER_STATE_STORE_RESULT_SCHEMA;
  operation: "write";
  path: string;
  state: JsonObject;
}

export type SchedulerStateWriteResult = SchedulerStateWriteResultBase & (
  | { written: true; replayed: false }
  | { written: false; replayed: true }
);

function pythonTruthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === "number") return value !== 0 && !Number.isNaN(value);
  if (typeof value === "string" || Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function pythonString(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return String(value);
}

function stringOrEmpty(value: unknown): string {
  return pythonTruthy(value) ? pythonString(value) : "";
}

function trimmed(value: unknown): string {
  return stringOrEmpty(value).trim();
}

function pythonInteger(value: unknown): number | null {
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "number") {
    return Number.isFinite(value) ? Math.trunc(value) : null;
  }
  if (typeof value === "string" && /^[+-]?\d+$/.test(value.trim())) {
    const result = Number(value.trim());
    return Number.isSafeInteger(result) ? result : null;
  }
  return null;
}

export function rruleForMinutes(value: unknown): string {
  const minutes = pythonInteger(value);
  if (minutes === null) throw new EffectRuntimeRequestError("scheduler minutes must be an integer");
  return `FREQ=MINUTELY;INTERVAL=${Math.max(1, minutes)}`;
}

export function normalizeSchedulerRrule(value: unknown): string {
  let text = stringOrEmpty(value).trim().replace(/\s+/g, " ");
  if (text.toUpperCase().startsWith("RRULE:")) text = text.slice(6).trim();
  return text;
}

export function schedulerRruleIntervalMinutes(value: unknown): number | null {
  const parts = new Map<string, string>();
  for (const part of normalizeSchedulerRrule(value).split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    parts.set(
      part.slice(0, separator).trim().toUpperCase(),
      part.slice(separator + 1).trim(),
    );
  }
  if ((parts.get("FREQ") ?? "").toUpperCase() !== "MINUTELY") return null;
  const interval = pythonInteger(parts.get("INTERVAL") ?? "");
  return interval !== null && interval > 0 ? interval : null;
}

export function normalizeSchedulerHostUpdateFailure(
  value: unknown,
): JsonObject | null {
  const input = jsonObject(value);
  if (
    !input ||
    stringOrEmpty(input.schema_version) !==
      SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION
  ) return null;
  const targetRrule = normalizeSchedulerRrule(input.target_rrule);
  const observedHostRrule = normalizeSchedulerRrule(input.observed_host_rrule);
  const failureKind = trimmed(input.failure_kind);
  const failedAt = trimmed(input.failed_at);
  const failureCount = pythonInteger(input.failure_count);
  if (
    !targetRrule ||
    !failureKind ||
    !failedAt ||
    failureCount === null ||
    failureCount < 1
  ) return null;
  return {
    schema_version: SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
    target_rrule: targetRrule,
    observed_host_rrule: observedHostRrule,
    failure_kind: failureKind,
    failure_count: failureCount,
    failed_at: failedAt,
  };
}

function failurePair(value: JsonObject): string {
  return `${normalizeSchedulerRrule(value.target_rrule)}\u0000${
    normalizeSchedulerRrule(value.observed_host_rrule)
  }`;
}

export function normalizeSchedulerHostUpdateFailures(
  value: unknown,
  legacyFailure: unknown = null,
): JsonObject[] {
  const candidates = Array.isArray(value) ? value : [];
  const normalized: JsonObject[] = [];
  for (const candidate of [...candidates, legacyFailure]) {
    const failure = normalizeSchedulerHostUpdateFailure(candidate);
    if (!failure) continue;
    const pair = failurePair(failure);
    const duplicate = normalized.findIndex((item) => failurePair(item) === pair);
    if (duplicate >= 0) normalized.splice(duplicate, 1);
    normalized.push(failure);
  }
  return normalized.slice(-HOST_UPDATE_FAILURE_CACHE_LIMIT);
}

export function schedulerTimestampMilliseconds(value: unknown): number | null {
  const text = trimmed(value);
  if (!text) return null;
  const timezoneAware = /(?:[zZ]|[+-]\d{2}(?::?\d{2})?)$/.test(text)
    ? text
    : `${text}Z`;
  const parsed = Date.parse(timezoneAware);
  return Number.isNaN(parsed) ? null : parsed;
}

export function retainedSchedulerHostUpdateFailures(
  value: unknown,
  referenceTime: unknown = null,
  observedHostRrule: unknown = null,
): JsonObject[] {
  const failures = normalizeSchedulerHostUpdateFailures(value);
  const now = schedulerTimestampMilliseconds(referenceTime) ?? Date.now();
  const cutoff = now - HOST_UPDATE_FAILURE_TTL_MS;
  const expectedHostRrule = normalizeSchedulerRrule(observedHostRrule);
  return failures.filter((failure) => {
    const failedAt = schedulerTimestampMilliseconds(failure.failed_at);
    if (failedAt === null || failedAt < cutoff) return false;
    return !expectedHostRrule ||
      normalizeSchedulerRrule(failure.observed_host_rrule) === expectedHostRrule;
  });
}

export function mergeSchedulerHostUpdateFailure(
  value: unknown,
  failure: unknown,
  referenceTime: unknown = null,
): JsonObject[] {
  const normalized = normalizeSchedulerHostUpdateFailure(failure);
  if (!normalized) throw new EffectRuntimeRequestError("scheduler host update failure is invalid");
  const retained = retainedSchedulerHostUpdateFailures(
    value,
    referenceTime,
    normalized.observed_host_rrule,
  );
  return normalizeSchedulerHostUpdateFailures([...retained, normalized]);
}

function positiveIntegerList(value: unknown): number[] | null {
  if (!Array.isArray(value)) return null;
  const result: number[] = [];
  for (const item of value) {
    const number = pythonInteger(item);
    if (number === null || number <= 0) return null;
    result.push(number);
  }
  return result.length ? result : null;
}

function schedulerScope(params: JsonObject): SchedulerScope {
  const surface = trimmed(params.surface) || CODEX_APP_SURFACE;
  const stateKey = trimmed(params.state_key) || CODEX_APP_STATEFUL_BACKOFF_STATE_KEY;
  return {
    goalId: trimmed(params.goal_id),
    agentId: trimmed(params.agent_id),
    surface,
    stateKey,
  };
}

export function normalizeSchedulerState(
  value: unknown,
  scope: SchedulerScope,
): JsonObject | null {
  const state = jsonObject(value);
  if (!state) return null;
  const expected: JsonObject = {
    schema_version: SCHEDULER_STATE_SCHEMA_VERSION,
    goal_id: scope.goalId,
    agent_id: scope.agentId,
    surface: scope.surface,
    state_key: scope.stateKey,
  };
  for (const [key, expectedValue] of Object.entries(expected)) {
    if (trimmed(state[key]) !== expectedValue) return null;
  }
  const resetToken = trimmed(state.reset_token);
  const identitySignature = trimmed(state.identity_signature);
  const lastAppliedRrule = trimmed(state.last_applied_rrule);
  const failures = normalizeSchedulerHostUpdateFailures(
    state.host_update_failures,
    state.host_update_failure,
  );
  if (!resetToken || !identitySignature) return null;
  if (!lastAppliedRrule && !failures.length) return null;
  const progressionIndex = pythonInteger(state.progression_index);
  if (progressionIndex === null || progressionIndex < 0) return null;
  const progressionMinutes = positiveIntegerList(state.progression_minutes);
  if (!progressionMinutes) return null;
  const normalized: JsonObject = {
    ...state,
    ...expected,
    reset_token: resetToken,
    identity_signature: identitySignature,
    progression_index: progressionIndex,
    progression_minutes: progressionMinutes,
    last_applied_rrule: lastAppliedRrule,
  };
  if (failures.length) {
    normalized.host_update_failures = failures;
    normalized.host_update_failure = failures.at(-1)!;
  } else {
    delete normalized.host_update_failures;
    delete normalized.host_update_failure;
  }
  return normalized;
}

export function buildSchedulerState(params: JsonObject): JsonObject {
  const scope = schedulerScope(params);
  const state: JsonObject = {
    schema_version: SCHEDULER_STATE_SCHEMA_VERSION,
    goal_id: scope.goalId,
    agent_id: scope.agentId,
    surface: scope.surface,
    state_key: scope.stateKey,
    reset_token: params.reset_token ?? null,
    identity_signature: params.identity_signature ?? null,
    progression_index: params.progression_index ?? null,
    progression_minutes: params.progression_minutes ?? null,
    last_applied_rrule: params.last_applied_rrule ?? null,
    updated_at: stringOrEmpty(params.updated_at),
  };
  if (params.host_update_failure !== undefined && params.host_update_failure !== null) {
    state.host_update_failure = params.host_update_failure;
  }
  if (params.host_update_failures !== undefined && params.host_update_failures !== null) {
    state.host_update_failures = params.host_update_failures;
  }
  if (pythonTruthy(params.source)) state.source = pythonString(params.source);
  const normalized = normalizeSchedulerState(state, scope);
  if (!normalized) {
    throw new EffectRuntimeRequestError("scheduler state is missing required persisted-state fields");
  }
  return normalized;
}

function safeSegment(value: unknown): string {
  const safe = trimmed(value)
    .replace(/[^0-9A-Za-z_.-]+/g, "-")
    .replace(/^[-._]+|[-._]+$/g, "");
  return safe || "default";
}

function stableHash(value: string, length: number): string {
  return createHash("sha256")
    .update(value, "utf8")
    .digest("hex")
    .slice(0, length);
}

function scopedSegment(value: unknown): string {
  const raw = trimmed(value);
  const label = safeSegment(raw).slice(0, SCOPE_SEGMENT_LABEL_LIMIT);
  return `${label}-${stableHash(raw, SCOPE_SEGMENT_HASH_LENGTH)}`;
}

function expandUser(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/") || path.startsWith("~\\")) {
    return join(homedir(), path.slice(2));
  }
  return path;
}

export function schedulerStatePath(
  runtimeRoot: string,
  scope: SchedulerScope,
): string {
  const stateHash = stableHash(scope.stateKey, 16);
  return join(
    expandUser(runtimeRoot),
    "goals",
    scopedSegment(scope.goalId),
    "scheduler-state",
    scopedSegment(scope.agentId),
    scopedSegment(scope.surface),
    `${stateHash}.json`,
  );
}

function legacySchedulerStatePath(
  runtimeRoot: string,
  scope: SchedulerScope,
): string {
  return join(
    expandUser(runtimeRoot),
    "goals",
    safeSegment(scope.goalId),
    "scheduler-state",
    safeSegment(scope.agentId),
    safeSegment(scope.surface),
    `${stableHash(scope.stateKey, 16)}.json`,
  );
}

function operationRequest(value: unknown): JsonObject {
  const request = requiredObject(value, "scheduler.state params");
  if (request.schema_version !== SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Scheduler state operation request schema mismatch");
  }
  return request;
}

export function evaluateSchedulerStateOperation(
  value: unknown,
): SchedulerStateOperationResult {
  const request = operationRequest(value);
  const operation = requireStringLiteral(
    request.operation,
    SCHEDULER_STATE_OPERATIONS,
    "scheduler.state operation",
    "Scheduler state operation is unsupported",
  );
  let result: unknown;
  switch (operation) {
    case "rrule_for_minutes":
      result = rruleForMinutes(request.value);
      break;
    case "normalize_rrule":
      result = normalizeSchedulerRrule(request.value);
      break;
    case "rrule_interval_minutes":
      result = schedulerRruleIntervalMinutes(request.value);
      break;
    case "normalize_failure":
      result = normalizeSchedulerHostUpdateFailure(request.value);
      break;
    case "normalize_failures":
      result = normalizeSchedulerHostUpdateFailures(
        request.value,
        request.legacy_failure,
      );
      break;
    case "retain_failures":
      result = retainedSchedulerHostUpdateFailures(
        request.value,
        request.reference_time,
        request.observed_host_rrule,
      );
      break;
    case "merge_failure":
      result = mergeSchedulerHostUpdateFailure(
        request.value,
        request.failure,
        request.reference_time,
      );
      break;
    case "normalize_state":
      result = normalizeSchedulerState(request.state, schedulerScope(request));
      break;
    case "build_state":
      result = buildSchedulerState(request);
      break;
    case "state_path":
      result = schedulerStatePath(
        requiredString(request.runtime_root, "runtime_root"),
        schedulerScope(request),
      );
      break;
    default:
      return assertNever(operation, "Scheduler state operation is unsupported");
  }
  return {
    schema_version: SCHEDULER_STATE_OPERATION_RESULT_SCHEMA,
    operation,
    value: result,
  };
}

function storeRequest(value: unknown, operation: "load" | "write"): {
  request: JsonObject;
  scope: SchedulerScope;
  path: string;
  legacyPath: string;
} {
  const request = requiredObject(value, `scheduler.state.${operation} params`);
  if (request.schema_version !== SCHEDULER_STATE_STORE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Scheduler state store request schema mismatch");
  }
  const scope = schedulerScope(request);
  const runtimeRoot = requiredString(request.runtime_root, "runtime_root");
  const path = schedulerStatePath(runtimeRoot, scope);
  const legacyPath = legacySchedulerStatePath(runtimeRoot, scope);
  return { request, scope, path, legacyPath };
}

async function migrateLegacySchedulerStateAtPathsUnderLock(
  scope: SchedulerScope,
  path: string,
  legacyPath: string,
): Promise<JsonObject | null> {
  // Probe the legacy file before creating any directory or taking the legacy
  // lock. The legacy layout uses unbounded safeSegment labels, so an overlong
  // scope component can make the legacy path unaddressable (ENAMETOOLONG) or
  // simply absent (ENOENT); either way there is nothing to migrate and the
  // canonical path must be reported as missing rather than raising an effect
  // rejection from mkdir on the unbounded legacy directory.
  try {
    await readFile(legacyPath, "utf8");
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT" || code === "ENAMETOOLONG") return null;
    throw error;
  }
  return await withFileMutationLock(legacyPath, async () => {
    try {
      const current = normalizeSchedulerState(
        JSON.parse(await readFile(path, "utf8")),
        scope,
      );
      if (current) return current;
    } catch {
      // The canonical path is still absent or unusable; inspect legacy state.
    }
    let legacyState: JsonObject | null = null;
    try {
      legacyState = normalizeSchedulerState(
        JSON.parse(await readFile(legacyPath, "utf8")),
        scope,
      );
    } catch {
      return null;
    }
    if (!legacyState) return null;
    await atomicWriteJson(path, stableValue(legacyState) as JsonObject);
    try {
      await unlink(legacyPath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    return legacyState;
  });
}

async function migrateLegacySchedulerState(
  scope: SchedulerScope,
  path: string,
  legacyPath: string,
): Promise<JsonObject | null> {
  return await withFileMutationLock(
    path,
    async () =>
      await migrateLegacySchedulerStateAtPathsUnderLock(
        scope,
        path,
        legacyPath,
      ),
  );
}

export async function migrateLegacySchedulerStateUnderLock(
  runtimeRoot: string,
  scope: SchedulerScope,
): Promise<JsonObject | null> {
  return await migrateLegacySchedulerStateAtPathsUnderLock(
    scope,
    schedulerStatePath(runtimeRoot, scope),
    legacySchedulerStatePath(runtimeRoot, scope),
  );
}

async function readLegacySchedulerState(
  scope: SchedulerScope,
  legacyPath: string,
): Promise<JsonObject | null> {
  try {
    return normalizeSchedulerState(
      JSON.parse(await readFile(legacyPath, "utf8")),
      scope,
    );
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT" || code === "ENAMETOOLONG" || error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

export async function loadSchedulerState(
  value: unknown,
): Promise<SchedulerStateLoadResult> {
  const { request, scope, path, legacyPath } = storeRequest(value, "load");
  let state: JsonObject | null = null;
  try {
    state = normalizeSchedulerState(JSON.parse(await readFile(path, "utf8")), scope);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      state = request.migrate_legacy === false
        ? await readLegacySchedulerState(scope, legacyPath)
        : await migrateLegacySchedulerState(scope, path, legacyPath);
    }
  }
  return {
    schema_version: SCHEDULER_STATE_STORE_RESULT_SCHEMA,
    operation: "load",
    path,
    state,
  };
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, stableValue(child)]),
  );
}

function statesMatch(left: JsonObject, right: JsonObject): boolean {
  return JSON.stringify(stableValue(left)) === JSON.stringify(stableValue(right));
}

export async function writeSchedulerState(
  value: unknown,
): Promise<SchedulerStateWriteResult> {
  const { request, scope, path } = storeRequest(value, "write");
  const state = normalizeSchedulerState(request.state, scope);
  if (!state) throw new EffectRuntimeRequestError("scheduler state does not match target scope or schema");
  return await withFileMutationLock(path, async () => {
    try {
      const existing = normalizeSchedulerState(
        JSON.parse(await readFile(path, "utf8")),
        scope,
      );
      if (existing && statesMatch(existing, state)) {
        return {
          schema_version: SCHEDULER_STATE_STORE_RESULT_SCHEMA,
          operation: "write",
          path,
          state,
          written: false,
          replayed: true,
        };
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT" &&
        !(error instanceof SyntaxError)) throw error;
    }
    await atomicWriteJson(path, stableValue(state) as JsonObject);
    return {
      schema_version: SCHEDULER_STATE_STORE_RESULT_SCHEMA,
      operation: "write",
      path,
      state,
      written: true,
      replayed: false,
    };
  });
}
