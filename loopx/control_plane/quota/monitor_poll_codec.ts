/** Codecs, schemas, canonical hashes, and target projections for monitor-poll commits. */
import { createHash } from "node:crypto";
import { basename } from "node:path";

import { monitorHeartbeatDecisionFields } from "./turn_envelope.ts";
import {
  parseQuotaAccountingOwner,
  quotaGoalRef,
  type QuotaAccountingOwner,
} from "./source_admission.ts";
import {
  decodeTaskLeaseProof,
  type TaskLeaseProof,
} from "../coordination/task_lease_proof.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import type { JsonObject } from "../effect_program.ts";
import {
  jsonObject,
  optionalNonEmptyString as optionalString,
  requireBoolean,
  requireInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  MONITOR_TARGET_SCHEMA,
  monitorSuccessorIntent,
  monitorSuccessorPriorities,
} from "../scheduler/monitor_successor.ts";
import { normalizeMonitorChangeScope } from "../todos/monitor_scope.ts";
import { parseProjectionDelivery } from "../todos/projection_delivery.ts";
import { normalizeTodoRepository } from "../todos/work_requirements.ts";

export const QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA =
  "loopx_quota_monitor_poll_commit_request_v0";
export const QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA =
  "loopx_quota_monitor_poll_commit_request_v1";
export const QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA =
  "loopx_quota_monitor_poll_commit_result_v0";
export const QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA =
  "quota_monitor_poll_commit_receipt_v0";
export const MONITOR_PENDING_ADMISSION_SCHEMA =
  "quota_monitor_poll_pending_admission_v1";
export const QUOTA_MONITOR_POLL_CLASSIFICATION = "quota_monitor_poll";

export const MONITOR_TODO_PROVIDER_PLAN_SCHEMA = "monitor_poll_todo_provider_plan_v0";
export const LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA =
  "monitor_poll_todo_provider_plan_v1";
export const MONITOR_TODO_WRITEBACK_SCHEMA = "monitor_poll_todo_writeback_v0";
export const MONITOR_PHASES = ["event", "preflight", "commit", "provider_rejected"] as const;
export const MONITOR_SOURCES = ["heartbeat", "controller", "adapter", "visible-goal"] as const;

const TURN_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const TODO_ID = /^todo_[a-z0-9_-]{3,64}$/;
const SHARED_OBSERVATION_KEYS = [
  "todo_id",
  "target_key",
  "material_change",
  "cadence",
  "next_due_at",
  "reason_summary",
  "next_agent_todo",
  "next_action_kind",
  "next_task_repository",
  "next_required_capabilities",
  "next_continuation_policy",
  "next_target_key",
  "next_user_todo",
  "next_user_task_class",
  "next_claimed_by",
] as const;

export type MonitorPhase = (typeof MONITOR_PHASES)[number];
export type MonitorSource = (typeof MONITOR_SOURCES)[number];
export type MonitorStatus =
  | "preview"
  | "provider_required"
  | "aborted"
  | "written"
  | "replayed"
  | "repaired"
  | "conflict";

export interface MonitorDecision extends JsonObject {
  goal_id: string;
  should_run: boolean;
  normal_delivery_allowed: boolean;
  recovery_delivery_allowed: boolean;
  effective_action: string | null;
  self_repair_allowed: boolean;
  capability_repair_allowed: boolean;
  workspace_repair_allowed: boolean;
  state: string;
  safe_bypass_allowed: boolean;
  safe_bypass_kind: string | null;
  blocked_action_scope: string | null;
  agent_id: string | null;
  recommended_action: string | null;
  reason: string | null;
  requires_user_action: boolean;
  work_lane_contract: JsonObject;
  external_evidence_observation: JsonObject;
  vision_wait_state: JsonObject;
  due_monitor_candidates: JsonObject[];
  registry_due_monitor: JsonObject;
  auxiliary_settlement_todo: JsonObject | null;
  auxiliary_gate_scope: JsonObject | null;
}

export interface MonitorSharedObservationFields extends JsonObject {
  lease_proof?: TaskLeaseProof;
  todo_id: string | null;
  target_key: string | null;
  observed_task_repository?: string;
  material_change: boolean;
  material_change_scope?: string | null;
  cadence: string | null;
  next_due_at: string | null;
  reason_summary: string | null;
  next_agent_todo: string | null;
  next_action_kind: string | null;
  next_task_repository: string | null;
  next_required_capabilities: string[];
  next_continuation_policy: string | null;
  next_target_key: string | null;
  next_user_todo: string | null;
  next_user_task_class: string | null;
  next_claimed_by: string | null;
}

export interface MonitorObservation extends MonitorSharedObservationFields {
  actor_agent_id: string | null;
  settlement_todo_id: string | null;
  result_hash: string | null;
}

export interface MonitorRequest {
  schema_version:
    | typeof QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA
    | typeof QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA;
  phase: MonitorPhase;
  effect_id: string;
  runtime_root: string | null;
  goal_id: string;
  source: MonitorSource;
  generated_at: string;
  execute: boolean;
  expected_index_digest: string | null;
  turn_instance_id: string | null;
  decision: MonitorDecision;
  observation: MonitorObservation;
  provider_receipt: JsonObject | null;
  status_reload_warning: JsonObject | null;
  owner: QuotaAccountingOwner;
}

export interface MonitorProviderPlan extends MonitorSharedObservationFields {
  legacy_batch_version?: 1;
  schema_version:
    | typeof MONITOR_TODO_PROVIDER_PLAN_SCHEMA
    | typeof LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA;
  gate_scope_guard?: boolean;
  admitted_resume_when?: string;
  monitor_effect_id: string;
  goal_id: string;
  generated_at: string;
  execute: boolean;
  result_hash: string;
  agent_id: string | null;
}

export interface PendingMonitorReceiptFields extends JsonObject {
  effect_id: string;
  request_digest: string;
  status: "provider_pending";
  index_path: string;
  expected_index_digest: string | null;
  expected_index_bytes: number;
  provider_plan: JsonObject;
}

export type PendingMonitorReceipt = PendingMonitorReceiptFields & (
  | { schema_version: typeof QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA }
  | { schema_version: typeof MONITOR_PENDING_ADMISSION_SCHEMA; admitted_decision: MonitorDecision }
);

export interface DurableMonitorReceipt extends JsonObject {
  schema_version: typeof QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA;
  effect_id: string;
  request_digest: string;
  status: "prepared" | "committed";
  json_path: string;
  markdown_path: string;
  index_path: string;
  expected_index_digest: string | null;
  expected_index_bytes: number;
  record: JsonObject;
  index_record: JsonObject;
  markdown: string;
  payload: JsonObject;
}

export type MonitorReceipt = PendingMonitorReceipt | DurableMonitorReceipt;

export interface QuotaMonitorPollCommitResult extends JsonObject {
  schema_version: typeof QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA;
  effect_id: string;
  status: MonitorStatus;
  written: boolean;
  replayed: boolean;
  repaired: boolean;
  conflict: boolean;
  request_digest: string;
  index_digest: string | null;
  reason: string;
  record: JsonObject | null;
  payload: JsonObject;
  provider_plan: JsonObject | null;
  index_record: JsonObject | null;
  reason_code?: string;
  conflict_fields?: string[];
}

export function optionalTrimmed(value: unknown, label: string): string | null {
  const text = optionalString(value, label);
  return text === null ? null : text.trim();
}

export function safeGoalId(value: unknown): string {
  const goalId = requiredString(value, "goal_id").trim();
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError("goal_id must be a single path segment");
  }
  if (basename(goalId) !== goalId) {
    throw new EffectRuntimeRequestError("goal_id must not include path traversal");
  }
  return goalId;
}

export function nullableObject(value: unknown, label: string): JsonObject {
  return value == null ? {} : requiredObject(value, label);
}

export function normalizedTodoId(value: unknown, label: string): string | null {
  const todoId = optionalTrimmed(value, label);
  if (todoId && !TODO_ID.test(todoId)) {
    throw new EffectRuntimeRequestError(`${label} must use the public todo_<token> shape`);
  }
  return todoId;
}

export function objectArray(value: unknown, label: string): JsonObject[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value.map((item, index) => requiredObject(item, `${label}[${index}]`));
}

export function successorReceipts(providerReceipt: JsonObject | null): JsonObject[] {
  return Array.isArray(providerReceipt?.successor_receipts)
    ? providerReceipt.successor_receipts.map((v, i) => requiredObject(v, `provider_receipt.successor_receipts[${i}]`))
    : [];
}

function decodeSuccessorRouteFields(
  source: JsonObject,
  prefix: string,
  trimCapabilities: boolean,
): Record<string, unknown> {
  const trim = (key: string): string | null => optionalTrimmed(source[key], `${prefix}.${key}`);
  const raw = (key: string): string | null => optionalString(source[key], `${prefix}.${key}`);
  const nextAgentTodo = raw("next_agent_todo");
  const nextActionKind = trim("next_action_kind");
  const nextTaskRepository = trim("next_task_repository");
  const rawCapabilities = requireStringArray(
    trimCapabilities ? (source.next_required_capabilities ?? []) : source.next_required_capabilities,
    `${prefix}.next_required_capabilities`,
  );
  return {
    next_agent_todo: nextAgentTodo,
    next_action_kind: nextActionKind,
    next_task_repository: nextTaskRepository,
    next_required_capabilities: trimCapabilities
      ? rawCapabilities.map((item) => item.trim()).filter(Boolean)
      : rawCapabilities,
    next_continuation_policy: trim("next_continuation_policy"),
    next_target_key: trim("next_target_key"),
    next_user_todo: raw("next_user_todo"),
    next_user_task_class: trim("next_user_task_class"),
    next_claimed_by: trim("next_claimed_by"),
  };
}

export function sharedObservationValues(source: MonitorSharedObservationFields): Record<string, unknown> {
  return {
    ...Object.fromEntries(SHARED_OBSERVATION_KEYS.map((key) => [key, source[key]])),
    ...observedRepositoryField(source),
  };
}

function observedRepositoryField(source: JsonObject): { observed_task_repository?: string } {
  return Object.hasOwn(source, "observed_task_repository")
    ? { observed_task_repository: normalizeTodoRepository(source.observed_task_repository, "observed_task_repository") ?? "" }
    : {};
}

export function collapsedSummary(value: unknown, limit = 160): string {
  const text = String(value ?? "").trim().replace(/\s+/g, " ");
  return text.length <= limit ? text : `${text.slice(0, limit - 3).trimEnd()}...`;
}

function jsonString(value: string): string {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (ch) =>
    `\\u${ch.codePointAt(0)!.toString(16).padStart(4, "0")}`,
  );
}

/** Canonical Python `json.dumps(..., sort_keys=True, ensure_ascii=True)` serializer
 * so hashes match historical pending receipts and Python producers byte-for-byte. */
export function pythonJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return jsonString(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(pythonJson).join(", ")}]`;
  const object = jsonObject(value);
  if (!object) throw new EffectRuntimeRequestError("monitor target contains a non-JSON value");
  return `{${Object.keys(object).sort((l, r) => Buffer.compare(Buffer.from(l), Buffer.from(r)))
    .map((key) => `${jsonString(key)}: ${pythonJson(object[key])}`)
    .join(", ")}}`;
}

export function sha256Hex(value: string | Uint8Array): string {
  return typeof value === "string"
    ? createHash("sha256").update(value, "utf8").digest("hex")
    : createHash("sha256").update(value).digest("hex");
}

export function sha256(value: string | Uint8Array): string {
  return `sha256:${sha256Hex(value)}`;
}

export function monitorResult(
  request: MonitorRequest,
  fingerprint: string,
  status: MonitorStatus,
  record: JsonObject | null,
  payload: JsonObject,
  reason: string,
  indexDigest: string | null = null,
  providerPlan: JsonObject | null = null,
  indexRecord: JsonObject | null = null,
  extra: Partial<QuotaMonitorPollCommitResult> = {},
): QuotaMonitorPollCommitResult {
  return {
    schema_version: QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA,
    effect_id: request.effect_id,
    status,
    written: status === "written",
    replayed: status === "replayed",
    repaired: status === "repaired",
    conflict: status === "conflict",
    request_digest: fingerprint,
    index_digest: indexDigest,
    reason,
    record,
    payload,
    provider_plan: providerPlan,
    index_record: indexRecord,
    ...extra,
  };
}

export function conflictResult(
  request: MonitorRequest,
  fingerprint: string,
  reason: string,
  indexDigest: string | null,
  reasonCode: string,
  extra: Partial<QuotaMonitorPollCommitResult> = {},
): QuotaMonitorPollCommitResult {
  return monitorResult(
    request, fingerprint, "conflict", null, { ok: false, appended: false },
    reason, indexDigest, null, null, { reason_code: reasonCode, ...extra },
  );
}

export function decisionObject(value: unknown): MonitorDecision {
  const decision = requiredObject(value, "decision");
  const due = Array.isArray(decision.due_monitor_candidates)
    ? decision.due_monitor_candidates.map((item, i) => requiredObject(item, `decision.due_monitor_candidates[${i}]`))
    : [];
  const opt = (key: string): string | null => optionalString(decision[key], `decision.${key}`);
  const bool = (key: string): boolean => requireBoolean(decision[key], `decision.${key}`);
  return {
    ...decision,
    goal_id: safeGoalId(decision.goal_id),
    should_run: bool("should_run"),
    normal_delivery_allowed: bool("normal_delivery_allowed"),
    recovery_delivery_allowed: bool("recovery_delivery_allowed"),
    effective_action: opt("effective_action"),
    self_repair_allowed: bool("self_repair_allowed"),
    capability_repair_allowed: bool("capability_repair_allowed"),
    workspace_repair_allowed: bool("workspace_repair_allowed"),
    state: typeof decision.state === "string" ? decision.state : "",
    safe_bypass_allowed: bool("safe_bypass_allowed"),
    safe_bypass_kind: opt("safe_bypass_kind"),
    blocked_action_scope: opt("blocked_action_scope"),
    agent_id: optionalTrimmed(decision.agent_id, "decision.agent_id"),
    recommended_action: opt("recommended_action"),
    reason: opt("reason"),
    requires_user_action: bool("requires_user_action"),
    ...monitorHeartbeatDecisionFields(decision),
    work_lane_contract: nullableObject(decision.work_lane_contract, "decision.work_lane_contract"),
    external_evidence_observation: nullableObject(decision.external_evidence_observation, "decision.external_evidence_observation"),
    vision_wait_state: nullableObject(decision.vision_wait_state, "decision.vision_wait_state"),
    due_monitor_candidates: due,
    registry_due_monitor: nullableObject(decision.registry_due_monitor, "decision.registry_due_monitor"),
    auxiliary_gate_scope: decision.auxiliary_gate_scope == null
      ? null : requiredObject(decision.auxiliary_gate_scope, "decision.auxiliary_gate_scope"),
    auxiliary_settlement_todo: decision.auxiliary_settlement_todo == null
      ? null : requiredObject(decision.auxiliary_settlement_todo, "decision.auxiliary_settlement_todo"),
  };
}

export interface MonitorLaneFacts {
  must_attempt_work: boolean;
  obligation: string | null;
  monitor_policy: string;
  reason_codes: ReadonlySet<string>;
  external_observation_required: boolean;
  has_external_observation: boolean;
}

export function monitorLaneFacts(decision: MonitorDecision): MonitorLaneFacts {
  const lane = decision.work_lane_contract;
  const external = decision.external_evidence_observation;
  const reasonCodes = new Set(
    Array.isArray(lane.reason_codes)
      ? lane.reason_codes.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      : [],
  );
  return {
    must_attempt_work: lane.must_attempt_work === true,
    obligation: typeof lane.obligation === "string" ? lane.obligation : null,
    monitor_policy: String(lane.monitor_policy ?? ""),
    reason_codes: reasonCodes,
    external_observation_required: external.required === true && external.must_attempt_observation === true,
    has_external_observation: Object.keys(external).length > 0,
  };
}

export function observationObject(value: unknown): MonitorObservation {
  const observation = requiredObject(value, "observation");
  const proof = decodeTaskLeaseProof(observation.lease_proof);
  const materialChange = requireBoolean(observation.material_change, "observation.material_change");
  const rawScope = optionalTrimmed(observation.material_change_scope, "observation.material_change_scope");
  const materialChangeScope = rawScope === null ? null : normalizeMonitorChangeScope(rawScope);
  if (rawScope !== null && materialChangeScope === null) {
    throw new EffectRuntimeRequestError("observation.material_change_scope must be a public-safe token of at most 96 characters");
  }
  if (materialChangeScope !== null && !materialChange) {
    throw new EffectRuntimeRequestError("material_change_scope requires material_change=true");
  }
  const trim = (key: string): string | null => optionalTrimmed(observation[key], `observation.${key}`);
  const result = {
    ...observation,
    ...observedRepositoryField(observation),
    actor_agent_id: trim("actor_agent_id"),
    settlement_todo_id: normalizedTodoId(observation.settlement_todo_id, "observation.settlement_todo_id"),
    reason_summary: optionalString(observation.reason_summary, "observation.reason_summary"),
    todo_id: normalizedTodoId(observation.todo_id, "observation.todo_id"),
    target_key: trim("target_key"),
    result_hash: trim("result_hash"),
    material_change: materialChange,
    ...(materialChangeScope === null ? {} : { material_change_scope: materialChangeScope }),
    cadence: trim("cadence"),
    next_due_at: trim("next_due_at"),
    ...monitorSuccessorPriorities(observation),
    ...decodeSuccessorRouteFields(observation, "observation", true),
  } as MonitorObservation;
  if (materialChange && !result.todo_id && !result.target_key) {
    throw new EffectRuntimeRequestError("`quota monitor-poll --material-change` requires --todo-id or --target-key");
  }
  if (proof) {
    if (!result.todo_id && !result.target_key) throw new EffectRuntimeRequestError("lease proof requires a Monitor target");
    result.lease_proof = proof;
  } else {
    delete result.lease_proof;
  }
  // Validate the route without rewriting the persisted observation fingerprint.
  // Pending receipts from earlier versions must remain replayable.
  monitorSuccessorIntent(result);
  return result;
}

export function requestObject(value: unknown): MonitorRequest {
  const request = requiredObject(value, "quota.monitor_poll.commit params");
  if (
    request.schema_version !== QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA &&
    request.schema_version !== QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA
  ) {
    throw new EffectRuntimeRequestError("Quota monitor-poll commit request schema mismatch");
  }
  const phase = requireStringLiteral(request.phase, MONITOR_PHASES, "phase");
  const execute = requireBoolean(request.execute, "execute");
  const runtimeRoot = optionalTrimmed(request.runtime_root, "runtime_root");
  if (execute && !runtimeRoot) {
    throw new EffectRuntimeRequestError("executing quota monitor-poll commit requires runtime_root");
  }
  const goalId = safeGoalId(request.goal_id);
  const decision = decisionObject(request.decision);
  if (decision.goal_id !== goalId) throw new EffectRuntimeRequestError("decision.goal_id must match goal_id");
  const turnId = optionalTrimmed(request.turn_instance_id, "turn_instance_id");
  if (turnId && !TURN_ID.test(turnId)) {
    throw new EffectRuntimeRequestError("turn_instance_id must be 1-128 public-safe letters, numbers, or ._:-");
  }
  if (turnId && !decision.agent_id) {
    throw new EffectRuntimeRequestError("turn-scoped monitor-poll requires a registered --agent-id");
  }
  const observation = observationObject(request.observation);
  if (observation.lease_proof && request.schema_version !== QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("lease-backed monitor-poll requires request v1");
  }
  if (request.schema_version === QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA && !observation.lease_proof) {
    throw new EffectRuntimeRequestError("monitor-poll request v1 requires lease_proof");
  }
  return {
    schema_version: request.schema_version,
    phase,
    effect_id: requiredString(request.effect_id, "effect_id").trim(),
    runtime_root: runtimeRoot,
    goal_id: goalId,
    source: requireStringLiteral(
      request.source, MONITOR_SOURCES, "source",
      `quota monitor-poll source must be one of: ${MONITOR_SOURCES.join(", ")}`,
    ),
    generated_at: requiredString(request.generated_at, "generated_at").trim(),
    execute,
    expected_index_digest: optionalString(request.expected_index_digest, "expected_index_digest"),
    turn_instance_id: turnId,
    decision,
    observation,
    provider_receipt: jsonObject(request.provider_receipt),
    status_reload_warning: jsonObject(request.status_reload_warning),
    owner: parseQuotaAccountingOwner({
      goalRefValue: request.goal_ref, sourceAdmissionValue: request.source_admission,
      runtimeRoot: runtimeRoot ?? "", goalId,
    }),
  };
}

export function providerPlanObject(value: unknown): MonitorProviderPlan {
  const plan = requiredObject(value, "receipt.provider_plan");
  if (
    plan.schema_version !== MONITOR_TODO_PROVIDER_PLAN_SCHEMA &&
    plan.schema_version !== LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA
  ) {
    throw new EffectRuntimeRequestError("Monitor Todo provider plan schema mismatch");
  }
  if (plan.legacy_batch_version !== undefined && plan.legacy_batch_version !== 1) {
    throw new EffectRuntimeRequestError("unsupported legacy Monitor batch version");
  }
  const proof = decodeTaskLeaseProof(plan.lease_proof);
  if ((plan.schema_version === LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA) !== (proof !== null)) {
    throw new EffectRuntimeRequestError("Monitor provider plan lease proof/schema mismatch");
  }
  const trim = (key: string): string | null => optionalTrimmed(plan[key], `provider_plan.${key}`);
  return {
    schema_version: plan.schema_version,
    ...(plan.legacy_batch_version === 1 ? { legacy_batch_version: 1 } : {}),
    ...(proof ? { lease_proof: proof } : {}),
    ...(plan.gate_scope_guard == null ? {} : {
      gate_scope_guard: requireBoolean(plan.gate_scope_guard, "provider_plan.gate_scope_guard"),
    }),
    monitor_effect_id: requiredString(plan.monitor_effect_id, "provider_plan.monitor_effect_id"),
    goal_id: safeGoalId(plan.goal_id),
    generated_at: requiredString(plan.generated_at, "provider_plan.generated_at"),
    execute: requireBoolean(plan.execute, "provider_plan.execute"),
    todo_id: normalizedTodoId(plan.todo_id, "provider_plan.todo_id"),
    target_key: trim("target_key"),
    ...observedRepositoryField(plan),
    result_hash: requiredString(plan.result_hash, "provider_plan.result_hash").trim(),
    material_change: requireBoolean(plan.material_change, "provider_plan.material_change"),
    ...(Object.hasOwn(plan, "material_change_scope") ? {
      material_change_scope: trim("material_change_scope"),
    } : {}),
    cadence: trim("cadence"),
    next_due_at: trim("next_due_at"),
    reason_summary: optionalString(plan.reason_summary, "provider_plan.reason_summary"),
    ...monitorSuccessorPriorities(plan),
    ...decodeSuccessorRouteFields(plan, "provider_plan", false),
    agent_id: trim("agent_id"),
  } as MonitorProviderPlan;
}

export function receiptObject(value: unknown): MonitorReceipt {
  const receipt = requiredObject(value, "quota monitor-poll transaction receipt");
  if (
    receipt.schema_version !== QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA &&
    receipt.schema_version !== MONITOR_PENDING_ADMISSION_SCHEMA
  ) {
    throw new EffectRuntimeRequestError("Quota monitor-poll transaction receipt schema mismatch");
  }
  const expectedBytes = requireInteger(receipt.expected_index_bytes, "receipt.expected_index_bytes");
  if (expectedBytes < 0) throw new EffectRuntimeRequestError("receipt.expected_index_bytes cannot be negative");
  const common = {
    schema_version: QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
    effect_id: requiredString(receipt.effect_id, "receipt.effect_id"),
    request_digest: requiredString(receipt.request_digest, "receipt.request_digest"),
    index_path: requiredString(receipt.index_path, "receipt.index_path"),
    expected_index_digest: optionalString(receipt.expected_index_digest, "receipt.expected_index_digest"),
    expected_index_bytes: expectedBytes,
    ...(receipt.goal_ref === undefined ? {} : { goal_ref: requiredObject(receipt.goal_ref, "receipt.goal_ref") }),
  } as const;
  const status = requireStringLiteral(receipt.status, ["provider_pending", "prepared", "committed"] as const, "receipt.status");
  if (status === "provider_pending") {
    if (receipt.schema_version === MONITOR_PENDING_ADMISSION_SCHEMA) {
      return {
        ...common,
        schema_version: MONITOR_PENDING_ADMISSION_SCHEMA,
        status,
        provider_plan: providerPlanObject(receipt.provider_plan),
        admitted_decision: decisionObject(receipt.admitted_decision),
      };
    }
    return { ...common, status, provider_plan: providerPlanObject(receipt.provider_plan) };
  }
  if (receipt.schema_version !== QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "admitted pending receipt cannot represent a completed settlement", "malformed_transaction_receipt",
    );
  }
  return {
    ...common,
    status,
    json_path: requiredString(receipt.json_path, "receipt.json_path"),
    markdown_path: requiredString(receipt.markdown_path, "receipt.markdown_path"),
    record: requiredObject(receipt.record, "receipt.record"),
    index_record: requiredObject(receipt.index_record, "receipt.index_record"),
    markdown: requiredString(receipt.markdown, "receipt.markdown"),
    payload: requiredObject(receipt.payload, "receipt.payload"),
  };
}

export function exactBlockedWait(decision: MonitorDecision): JsonObject | null {
  const wait = decision.vision_wait_state;
  return wait.schema_version === "goal_vision_wait_state_v0" &&
      wait.state === "waiting" &&
      wait.reason_code === "exact_blocked_successor" &&
      wait.automatic_resume === true &&
      Boolean(normalizedTodoId(wait.selected_todo_id, "decision.vision_wait_state.selected_todo_id")) &&
      typeof wait.resume_when === "string" &&
      Boolean(wait.resume_when.trim())
    ? wait
    : null;
}

export function candidateMatches(candidate: JsonObject, observation: MonitorObservation): boolean {
  if (candidate.task_class !== "continuous_monitor") return false;
  const candidateTodo = normalizedTodoId(candidate.todo_id, "due monitor candidate.todo_id");
  const candidateTarget = typeof candidate.target_key === "string" ? candidate.target_key.trim() : "";
  if (observation.todo_id && candidateTodo !== observation.todo_id) return false;
  if (observation.target_key && candidateTarget !== observation.target_key) return false;
  return Boolean(candidateTodo || candidateTarget);
}

export function requestDigest(request: MonitorRequest): string {
  // Admission evidence is intentionally absent: Todo writeback changes the
  // projected decision during a retry, while the logical observation remains
  // the same effect. Mutable phase/CAS/provider fields are fenced separately.
  const observation: JsonObject = { ...request.observation };
  const goalRef = quotaGoalRef(request.owner);
  if (!observation.settlement_todo_id) delete observation.settlement_todo_id;
  return sha256(pythonJson({
    schema_version: request.schema_version,
    effect_id: request.effect_id,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    source: request.source,
    turn_instance_id: request.turn_instance_id,
    ...(goalRef === null ? {} : { goal_ref: goalRef }),
    observation,
  }));
}

export function blockedFrontierIdentity(decision: MonitorDecision): string | null {
  const wait = exactBlockedWait(decision);
  if (!wait) return null;
  return sha256Hex(pythonJson({
    agent_id: typeof wait.agent_id === "string" ? wait.agent_id.trim() : "",
    reason_code: "exact_blocked_successor",
    selected_todo_id: normalizedTodoId(wait.selected_todo_id, "decision.vision_wait_state.selected_todo_id"),
    resume_when: String(wait.resume_when ?? "").trim(),
  })).slice(0, 16);
}

export function monitorTarget(decision: MonitorDecision, monitorMode: string): JsonObject {
  const actionSummary = collapsedSummary(decision.recommended_action ?? decision.reason ?? "");
  const frontierIdentity = blockedFrontierIdentity(decision);
  const parts: JsonObject = {
    goal_id: decision.goal_id,
    agent_id: decision.agent_id ?? "",
    monitor_mode: monitorMode,
    effective_action: decision.effective_action ?? "",
    frontier_identity: frontierIdentity ?? "",
  };
  if (!frontierIdentity) parts.action_summary = actionSummary;
  const target: JsonObject = {
    schema_version: MONITOR_TARGET_SCHEMA,
    target_id: sha256Hex(pythonJson(parts)).slice(0, 16),
    monitor_mode: monitorMode,
    effective_action: decision.effective_action ?? "",
    action_summary: actionSummary,
  };
  if (decision.agent_id) target.agent_id = decision.agent_id;
  if (frontierIdentity) target.frontier_identity = frontierIdentity;
  return target;
}

export function compactDecision(decision: MonitorDecision): JsonObject {
  return {
    should_run: decision.should_run,
    normal_delivery_allowed: decision.normal_delivery_allowed,
    recovery_delivery_allowed: decision.recovery_delivery_allowed,
    effective_action: decision.effective_action,
    self_repair_allowed: decision.self_repair_allowed,
    capability_repair_allowed: decision.capability_repair_allowed,
    workspace_repair_allowed: decision.workspace_repair_allowed,
    state: decision.state,
    safe_bypass_allowed: decision.safe_bypass_allowed,
    safe_bypass_kind: decision.safe_bypass_kind,
    blocked_action_scope: decision.blocked_action_scope,
    compute: decision.compute ?? null,
    window_hours: decision.window_hours ?? null,
    slot_minutes: decision.slot_minutes ?? null,
    spent_slots: decision.spent_slots ?? null,
    allowed_slots: decision.allowed_slots ?? null,
  };
}

/** Display acknowledgement is diagnostic, never evidence for business commit.
 * Omitted on the legacy path to retain its exact v0 response shape. */
export function monitorProjectionDelivery(receipt: JsonObject): JsonObject {
  if (receipt.projection_delivery == null) return {};
  let status: ReturnType<typeof parseProjectionDelivery>;
  try {
    status = parseProjectionDelivery(receipt.projection_delivery);
  } catch (error) {
    throw new EffectRuntimeRequestError(
      error instanceof Error ? error.message : "invalid Monitor projection delivery status",
    );
  }
  const outbox = requiredObject(receipt.projection_outbox, "projection_outbox");
  const diagnostic: JsonObject = {};
  for (const key of [
    "schema_version", "status", "reason_code", "retryable", "recommended_action", "retry_business_mutation",
  ]) {
    if (outbox[key] != null) diagnostic[key] = outbox[key];
  }
  return { projection_delivery: status, projection_outbox: diagnostic };
}

export function compactProviderWriteback(receipt: JsonObject): JsonObject {
  const compact: JsonObject = {};
  for (const field of [
    "schema_version", "dry_run", "goal_id", "todo_id", "target_key", "result_hash",
    "material_change", "consecutive_no_change", "last_checked_at", "next_due_at", "cadence", "successor_receipts",
  ]) {
    compact[field] = receipt[field] ?? null;
  }
  if (receipt.material_change_scope != null) compact.material_change_scope = receipt.material_change_scope;
  if (receipt.lease_proof != null) compact.lease_proof = receipt.lease_proof;
  Object.assign(compact, monitorProjectionDelivery(receipt));
  return compact;
}
