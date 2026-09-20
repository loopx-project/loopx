/** Monitor authoring and observation rules. A plan is not a commit receipt:
 * the caller must hold the Todo writer lock and retain its authority fence. */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { evaluateSchedulerStateTransition, SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA } from "../scheduler/state_transition_rules.ts";
import { parseTodoTimestampMicros } from "../runtime_timestamp.ts";

export const TODO_MONITOR_METADATA_REQUEST_SCHEMA = "loopx_todo_monitor_metadata_request_v0";
export const TODO_MONITOR_METADATA_RESULT_SCHEMA = "loopx_todo_monitor_metadata_result_v0";
export const MONITOR_CONFIGURATION_FIELDS = ["target_key", "cadence", "next_due_at", "expires_at", "watch_only"] as const;
export const MONITOR_METADATA_FIELDS = ["target_key", "monitor_effect_id", "cadence", "next_due_at",
  "expires_at", "last_checked_at", "result_hash", "consecutive_no_change", "material_change",
  "material_change_generation", "max_no_change_before_replan", "watch_only"] as const;

/** An observation is input evidence, never a patch to persisted counters. */
export interface MonitorPollObservation extends JsonObject {
  generated_at: string;
  result_hash: string;
  material_change: boolean;
}

export function decodeMonitorPollObservation(value: unknown): MonitorPollObservation {
  const raw = requireJsonObject(value, "Monitor observation");
  const strings = ["monitor_effect_id", "target_key", "cadence", "next_due_at"];
  for (const key of Object.keys(raw)) {
    if (!["generated_at", "result_hash", "material_change", ...strings].includes(key)) {
      throw new EffectRuntimeRequestError(`Monitor observation does not own ${key}`);
    }
  }
  for (const key of strings) if (raw[key] != null && typeof raw[key] !== "string") {
    throw new EffectRuntimeRequestError(`Monitor observation ${key} must be a string or null`);
  }
  return {...raw, generated_at: requireNonEmptyString(raw.generated_at, "generated_at"),
    result_hash: requireNonEmptyString(raw.result_hash, "result_hash"),
    material_change: requireBoolean(raw.material_change, "material_change")};
}

/** Public configuration is not an observation/import codec. Keep historical
 * fields available to their existing lower-level owners, never to this intent. */
export function normalizeMonitorConfiguration(value: unknown): JsonObject {
  const raw = requireJsonObject(value, "Monitor configuration");
  const input: JsonObject = {};
  for (const [field, value] of Object.entries(raw)) {
    if (!(MONITOR_CONFIGURATION_FIELDS as readonly string[]).includes(field)) {
      throw new EffectRuntimeRequestError(`Monitor configuration does not own ${field}; use the observation lifecycle`);
    }
    if (value !== null && typeof value !== "string" && !(field === "watch_only" && typeof value === "boolean")) {
      throw new EffectRuntimeRequestError(`Monitor configuration ${field} must be a string or null`);
    }
    input[field] = field === "watch_only" && value !== null ? String(value).toLowerCase() : value;
  }
  return normalizeMetadata(input);
}

export function validateMonitorConfigurationTarget(existing: JsonObject, metadata: JsonObject): void {
  if (!Object.hasOwn(metadata, "target_key") || text(metadata.target_key) === text(existing.target_key)) return;
  // Observations and dependent generation fences name this target's history.
  // Changing its identity cannot reuse those receipts as evidence for a new target.
  if (["monitor_effect_id", "result_hash", "last_checked_at"].some(field => text(existing[field])) ||
      counter(existing.material_change_generation) > 0) {
    throw new EffectRuntimeRequestError("an observed Monitor cannot change target_key; create an independent Monitor for the new target");
  }
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === false || value === 0) return "";
  return stripPythonWhitespace(value === true ? "True" : String(value));
}

function timestampOrNull(value: unknown): bigint | null {
  return parseTodoTimestampMicros(text(value));
}

function timestamp(value: unknown, label: string): bigint {
  const result = timestampOrNull(value);
  if (result === null) throw new EffectRuntimeRequestError(`${label} must be an ISO timestamp`);
  return result;
}

function schedule(generatedAt: string, cadence: unknown, explicit: unknown = null) {
  const result = evaluateSchedulerStateTransition({
    schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA, operation: "monitor_schedule",
    generated_at: generatedAt, cadence: cadence ?? null, explicit_next_due_at: explicit ?? null,
  });
  if (result.operation !== "monitor_schedule") throw new Error("monitor schedule result mismatch");
  return result;
}

function counter(value: unknown): number {
  const raw = text(value);
  if (!/^[+-]?\d+$/.test(raw)) return 0;
  const result = Number(raw);
  if (!Number.isSafeInteger(result)) throw new EffectRuntimeRequestError("monitor counter exceeds the safe integer range");
  return Math.max(0, result);
}

function normalizeMetadata(value: unknown): JsonObject {
  const raw = value == null ? {} : requireJsonObject(value, "monitor metadata");
  const normalized: JsonObject = {};
  for (const field of MONITOR_METADATA_FIELDS) {
    if (!Object.hasOwn(raw, field)) continue;
    if (raw[field] === null) normalized[field] = null;
    else if (text(raw[field])) normalized[field] = text(raw[field]);
  }
  if (normalized.cadence != null && schedule("1970-01-01T00:00:00Z", normalized.cadence).cadence_seconds === null) {
    throw new EffectRuntimeRequestError("--cadence must look like 30m, 2h, or 1d");
  }
  for (const field of ["next_due_at", "expires_at", "last_checked_at"] as const) {
    if (normalized[field] != null) timestamp(normalized[field], `--${field.replaceAll("_", "-")}`);
  }
  for (const field of ["consecutive_no_change", "material_change_generation"] as const) {
    if (normalized[field] == null) continue;
    const raw = String(normalized[field]);
    const label = `--${field.replaceAll("_", "-")}`;
    if (!/^[+-]?\d+$/.test(raw)) throw new EffectRuntimeRequestError(`${label} must be an integer`);
    if (!Number.isSafeInteger(Number(raw)) || Number(raw) < 0) {
      throw new EffectRuntimeRequestError(`${label} must be a non-negative safe integer`);
    }
  }
  if (normalized.material_change != null && !["true", "false"].includes(String(normalized.material_change))) {
    throw new EffectRuntimeRequestError("--material-change metadata must be true or false");
  }
  if (normalized.watch_only != null && !["true", "false"].includes(String(normalized.watch_only).toLowerCase())) {
    throw new EffectRuntimeRequestError("--watch-only metadata must be true or false");
  }
  return normalized;
}

function poll(existing: JsonObject, observation: JsonObject, reactivate: boolean): {metadata: JsonObject; transition: JsonObject} {
  if (existing.task_class !== "continuous_monitor") {
    throw new EffectRuntimeRequestError("monitor poll observation requires task_class=continuous_monitor");
  }
  const material = requireBoolean(observation.material_change, "material_change");
  const resultHash = text(observation.result_hash);
  if (!resultHash) throw new EffectRuntimeRequestError("monitor todo writeback requires --result-hash");
  const generatedAt = text(observation.generated_at);
  const observedAt = timestamp(generatedAt, "generated_at");
  if (reactivate) {
    if (existing.status !== "done" || existing.role !== "agent" || existing.archive_state === "archive" || existing.superseded_by) {
      throw new EffectRuntimeRequestError("Monitor reactivation requires an unarchived completed Agent Monitor");
    }
    if (!material || observedAt <= timestamp(existing.completed_at, "Monitor completed_at")) {
      throw new EffectRuntimeRequestError("Monitor reactivation requires a material observation newer than completion");
    }
  }
  const existingTarget = text(existing.target_key);
  const requestedTarget = text(observation.target_key);
  if (requestedTarget && existingTarget && requestedTarget !== existingTarget) {
    throw new EffectRuntimeRequestError(`monitor poll target_key resolves to '${existingTarget}', not '${requestedTarget}'`);
  }
  const target = requestedTarget || existingTarget;
  const cadence = text(observation.cadence || existing.cadence);
  const due = schedule(generatedAt, cadence, observation.next_due_at).next_due_at;
  if (!material && !due) throw new EffectRuntimeRequestError(
    "unchanged monitor todo writeback requires --next-due-at or a parseable cadence such as 30m/2h/1d");
  const effectId = text(observation.monitor_effect_id);
  const previousEffect = text(existing.monitor_effect_id);
  const previousGeneration = counter(existing.material_change_generation);
  const previousNoChange = counter(existing.consecutive_no_change);
  const replay = Boolean(effectId && effectId === previousEffect);
  if (replay) {
    const facts = {result_hash: resultHash, material_change: String(material), last_checked_at: generatedAt,
      target_key: target, cadence, next_due_at: due || ""};
    const conflicts = Object.entries(facts).filter(([key, expected]) => text(existing[key]) !== expected).map(([key]) => key);
    if (conflicts.length) throw new EffectRuntimeRequestError(
      `monitor effect identity is already bound to different observation fields: ${conflicts.join(", ")}`);
  } else {
    const persistedAt = timestampOrNull(existing.last_checked_at);
    // Ordering is about observations, not whether a caller supplied a retry ID.
    // Preserve same-second unkeyed polls; distinct keyed effects retain strict ordering.
    if (persistedAt !== null && (observedAt < persistedAt ||
        (effectId && previousEffect && observedAt === persistedAt))) {
      throw new EffectRuntimeRequestError("monitor observation is older than the persisted monitor effect");
    }
  }
  const previousHash = text(existing.result_hash);
  if (reactivate && replay) throw new EffectRuntimeRequestError("a historical Monitor observation cannot reactivate completed work");
  const advances = !replay && material && (reactivate || resultHash !== previousHash);
  const generation = previousGeneration + Number(advances);
  const noChange = replay ? previousNoChange : material || (previousHash && previousHash !== resultHash)
    ? 0 : previousNoChange + 1;
  if (!Number.isSafeInteger(generation) || !Number.isSafeInteger(noChange)) {
    throw new EffectRuntimeRequestError("monitor counter exceeds the safe integer range");
  }
  const metadata: JsonObject = replay
    ? Object.fromEntries(MONITOR_METADATA_FIELDS.filter(key => existing[key] != null).map(key => [key, existing[key]]))
    : {last_checked_at: generatedAt, result_hash: resultHash, consecutive_no_change: String(noChange),
      material_change: String(material), material_change_generation: String(generation)};
  if (!replay) {
    if (effectId) metadata.monitor_effect_id = effectId;
    if (target) metadata.target_key = target;
    if (cadence) metadata.cadence = cadence;
    if (due) metadata.next_due_at = due;
  }
  return {metadata, transition: {monitor_effect_id: effectId || null, provider_replayed: replay,
    result_hash: resultHash, material_change: material, material_change_applied: advances,
    material_change_generation: generation, consecutive_no_change: noChange, last_checked_at: generatedAt,
    target_key: target || null, cadence: cadence || null, next_due_at: due}};
}

export interface MonitorMetadataPlan extends JsonObject {
  schema_version: typeof TODO_MONITOR_METADATA_RESULT_SCHEMA;
  metadata: JsonObject;
  transition: JsonObject | null;
}

/** Composed in-process by the field planner, or used by the create adapter. */
export function planMonitorMetadata(value: unknown): MonitorMetadataPlan {
  const request = requireJsonObject(value, "monitor metadata request");
  if (request.schema_version !== TODO_MONITOR_METADATA_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("monitor metadata request schema mismatch");
  }
  const existing = requireJsonObject(request.existing ?? {}, "monitor source");
  const planned = request.observation == null ? null : poll(existing,
    requireJsonObject(request.observation, "monitor observation"), request.reactivate === true);
  if (planned && request.metadata != null) throw new EffectRuntimeRequestError("monitor observation cannot be combined with raw metadata");
  let metadata = normalizeMetadata(planned?.metadata ?? request.metadata);
  const nonTarget = Object.entries(metadata).some(([key, v]) => key !== "target_key" && v !== null);
  if (nonTarget && (request.role !== "agent" || request.task_class !== "continuous_monitor")) {
    throw new EffectRuntimeRequestError("monitor schedule metadata requires --role agent --task-class continuous_monitor");
  }
  if (metadata.target_key != null && request.role !== "agent") throw new EffectRuntimeRequestError("target_key requires --role agent");
  if (request.generated_at != null && request.task_class === "continuous_monitor" && metadata.next_due_at == null && metadata.cadence != null) {
    const due = schedule(text(request.generated_at), metadata.cadence).next_due_at;
    if (due) metadata = {...metadata, next_due_at: due};
  }
  if (requireBoolean(request.enforce_boundedness, "enforce_boundedness") && request.task_class === "continuous_monitor") {
    const effective = {...existing, ...metadata};
    if (!text(effective.expires_at) && !text(request.resume_when) && text(effective.watch_only).toLowerCase() !== "true") {
      throw new EffectRuntimeRequestError("continuous_monitor requires one of: --expires-at, --resume-when, or --watch-only");
    }
  }
  return {schema_version: TODO_MONITOR_METADATA_RESULT_SCHEMA, metadata, transition: planned?.transition ?? null};
}
