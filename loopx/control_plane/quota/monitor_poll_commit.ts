import {decodeTaskLeaseProof, type TaskLeaseProof} from "../coordination/task_lease_proof.ts";
import { EffectiveAction, type QuotaEffectiveActionValue } from "./effective_action.generated.ts";
import { AgentScopeFrontierAction } from "../agents/agent_scope_frontier.generated.ts";
import { createHash } from "node:crypto";
import { access, readFile, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { monitorSuccessorIntent, monitorSuccessorRoute } from "../scheduler/monitor_successor.ts";
import { normalizeTodoCapabilities } from "../todos/work_requirements.ts";
import { parseProjectionDelivery } from "../todos/projection_delivery.ts";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  appendJsonLine,
  atomicWriteJson,
  atomicWriteText,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
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

export const QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA =
  "loopx_quota_monitor_poll_commit_request_v0";
export const QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA = "loopx_quota_monitor_poll_commit_request_v1";
export const QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA =
  "loopx_quota_monitor_poll_commit_result_v0";
export const QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA =
  "quota_monitor_poll_commit_receipt_v0";
const MONITOR_PENDING_ADMISSION_SCHEMA = "quota_monitor_poll_pending_admission_v1";
export const QUOTA_MONITOR_POLL_CLASSIFICATION = "quota_monitor_poll";

const MONITOR_TARGET_SCHEMA = "quota_monitor_target_v0";
const MONITOR_TODO_PROVIDER_PLAN_SCHEMA = "monitor_poll_todo_provider_plan_v0";
const LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA = "monitor_poll_todo_provider_plan_v1";
const MONITOR_TODO_WRITEBACK_SCHEMA = "monitor_poll_todo_writeback_v0";
const MONITOR_PHASES = ["event", "preflight", "commit"] as const;
const MONITOR_SOURCES = ["heartbeat", "controller", "adapter", "visible-goal"] as const;
const EXTERNAL_MONITOR_POLICIES = new Set([
  "material_transition_only",
  "read_only_observation_then_no_spend_if_unchanged",
]);
const EXTERNAL_MONITOR_REASON_CODES = new Set([
  "external_monitor_context",
  "external_evidence_poll_signal",
]);
const TURN_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const TODO_ID = /^todo_[a-z0-9_-]{3,64}$/;

type MonitorPhase = (typeof MONITOR_PHASES)[number];
type MonitorSource = (typeof MONITOR_SOURCES)[number];
type MonitorStatus =
  | "preview"
  | "provider_required"
  | "written"
  | "replayed"
  | "repaired"
  | "conflict";

interface MonitorDecision extends JsonObject {
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
  heartbeat_recommendation: JsonObject;
  work_lane_contract: JsonObject;
  external_evidence_observation: JsonObject;
  vision_wait_state: JsonObject;
  due_monitor_candidates: JsonObject[];
  registry_due_monitor: JsonObject;
}

interface MonitorObservation extends JsonObject {
  lease_proof?: TaskLeaseProof;
  actor_agent_id: string | null;
  settlement_todo_id: string | null;
  reason_summary: string | null;
  todo_id: string | null;
  target_key: string | null;
  result_hash: string | null;
  material_change: boolean;
  cadence: string | null;
  next_due_at: string | null;
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

interface MonitorRequest {
  schema_version: typeof QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA | typeof QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA;
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
}

interface MonitorProviderPlan extends JsonObject {
  schema_version: typeof MONITOR_TODO_PROVIDER_PLAN_SCHEMA | typeof LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA;
  lease_proof?: TaskLeaseProof;
  monitor_effect_id: string;
  goal_id: string;
  generated_at: string;
  execute: boolean;
  todo_id: string | null;
  target_key: string | null;
  result_hash: string;
  material_change: boolean;
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
  agent_id: string | null;
}

interface PendingMonitorReceiptFields extends JsonObject {
  effect_id: string;
  request_digest: string;
  status: "provider_pending";
  index_path: string;
  expected_index_digest: string | null;
  expected_index_bytes: number;
  provider_plan: JsonObject;
}

type PendingMonitorReceipt = PendingMonitorReceiptFields & (
  | {schema_version: typeof QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA}
  | {schema_version: typeof MONITOR_PENDING_ADMISSION_SCHEMA; admitted_decision: MonitorDecision}
);

interface DurableMonitorReceipt extends JsonObject {
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

type MonitorReceipt = PendingMonitorReceipt | DurableMonitorReceipt;

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

function safeGoalId(value: unknown): string {
  const goalId = requiredString(value, "goal_id").trim();
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError("goal_id must be a single path segment");
  }
  if (basename(goalId) !== goalId) {
    throw new EffectRuntimeRequestError("goal_id must not include path traversal");
  }
  return goalId;
}

function nullableObject(value: unknown, label: string): JsonObject {
  if (value === null || value === undefined) return {};
  return requiredObject(value, label);
}

function normalizedTodoId(value: unknown, label: string): string | null {
  const todoId = optionalString(value, label)?.trim() ?? null;
  if (todoId && !TODO_ID.test(todoId)) {
    throw new EffectRuntimeRequestError(`${label} must use the public todo_<token> shape`);
  }
  return todoId;
}

function decisionObject(value: unknown): MonitorDecision {
  const decision = requiredObject(value, "decision");
  const due = Array.isArray(decision.due_monitor_candidates)
    ? decision.due_monitor_candidates.map((item, index) =>
      requiredObject(item, `decision.due_monitor_candidates[${index}]`)
    )
    : [];
  return {
    ...decision,
    goal_id: safeGoalId(decision.goal_id),
    should_run: requireBoolean(decision.should_run, "decision.should_run"),
    normal_delivery_allowed: requireBoolean(
      decision.normal_delivery_allowed,
      "decision.normal_delivery_allowed",
    ),
    recovery_delivery_allowed: requireBoolean(
      decision.recovery_delivery_allowed,
      "decision.recovery_delivery_allowed",
    ),
    effective_action: optionalString(
      decision.effective_action,
      "decision.effective_action",
    ),
    self_repair_allowed: requireBoolean(
      decision.self_repair_allowed,
      "decision.self_repair_allowed",
    ),
    capability_repair_allowed: requireBoolean(
      decision.capability_repair_allowed,
      "decision.capability_repair_allowed",
    ),
    workspace_repair_allowed: requireBoolean(
      decision.workspace_repair_allowed,
      "decision.workspace_repair_allowed",
    ),
    state: typeof decision.state === "string" ? decision.state : "",
    safe_bypass_allowed: requireBoolean(
      decision.safe_bypass_allowed,
      "decision.safe_bypass_allowed",
    ),
    safe_bypass_kind: optionalString(
      decision.safe_bypass_kind,
      "decision.safe_bypass_kind",
    ),
    blocked_action_scope: optionalString(
      decision.blocked_action_scope,
      "decision.blocked_action_scope",
    ),
    agent_id: optionalString(decision.agent_id, "decision.agent_id")?.trim() ?? null,
    recommended_action: optionalString(
      decision.recommended_action,
      "decision.recommended_action",
    ),
    reason: optionalString(decision.reason, "decision.reason"),
    requires_user_action: requireBoolean(
      decision.requires_user_action,
      "decision.requires_user_action",
    ),
    heartbeat_recommendation: nullableObject(
      decision.heartbeat_recommendation,
      "decision.heartbeat_recommendation",
    ),
    work_lane_contract: nullableObject(
      decision.work_lane_contract,
      "decision.work_lane_contract",
    ),
    external_evidence_observation: nullableObject(
      decision.external_evidence_observation,
      "decision.external_evidence_observation",
    ),
    vision_wait_state: nullableObject(
      decision.vision_wait_state,
      "decision.vision_wait_state",
    ),
    due_monitor_candidates: due,
    registry_due_monitor: nullableObject(
      decision.registry_due_monitor,
      "decision.registry_due_monitor",
    ),
  };
}

function observationObject(value: unknown): MonitorObservation {
  const observation = requiredObject(value, "observation");
  const proof = decodeTaskLeaseProof(observation.lease_proof);
  const materialChange = requireBoolean(
    observation.material_change,
    "observation.material_change",
  );
  const result: MonitorObservation = {
    ...observation,
    actor_agent_id: optionalString(
      observation.actor_agent_id,
      "observation.actor_agent_id",
    )?.trim() ?? null,
    settlement_todo_id: normalizedTodoId(
      observation.settlement_todo_id,
      "observation.settlement_todo_id",
    ),
    reason_summary: optionalString(
      observation.reason_summary,
      "observation.reason_summary",
    ),
    todo_id: normalizedTodoId(observation.todo_id, "observation.todo_id"),
    target_key: optionalString(observation.target_key, "observation.target_key")?.trim() ?? null,
    result_hash: optionalString(observation.result_hash, "observation.result_hash")?.trim() ?? null,
    material_change: materialChange,
    cadence: optionalString(observation.cadence, "observation.cadence")?.trim() ?? null,
    next_due_at: optionalString(
      observation.next_due_at,
      "observation.next_due_at",
    )?.trim() ?? null,
    next_agent_todo: optionalString(
      observation.next_agent_todo,
      "observation.next_agent_todo",
    ),
    next_action_kind: optionalString(
      observation.next_action_kind,
      "observation.next_action_kind",
    )?.trim() ?? null,
    next_task_repository: optionalString(
      observation.next_task_repository,
      "observation.next_task_repository",
    )?.trim() ?? null,
    next_required_capabilities: requireStringArray(
      observation.next_required_capabilities ?? [],
      "observation.next_required_capabilities",
    ).map((item) => item.trim()).filter(Boolean),
    next_continuation_policy: optionalString(
      observation.next_continuation_policy,
      "observation.next_continuation_policy",
    )?.trim() ?? null,
    next_target_key: optionalString(
      observation.next_target_key,
      "observation.next_target_key",
    )?.trim() ?? null,
    next_user_todo: optionalString(
      observation.next_user_todo,
      "observation.next_user_todo",
    ),
    next_user_task_class: optionalString(
      observation.next_user_task_class,
      "observation.next_user_task_class",
    )?.trim() ?? null,
    next_claimed_by: optionalString(
      observation.next_claimed_by,
      "observation.next_claimed_by",
    )?.trim() ?? null,
  };
  if (materialChange && !result.todo_id && !result.target_key) {
    throw new EffectRuntimeRequestError(
      "`quota monitor-poll --material-change` requires --todo-id or --target-key",
    );
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

function requestObject(value: unknown): MonitorRequest {
  const request = requiredObject(value, "quota.monitor_poll.commit params");
  if (request.schema_version !== QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA &&
      request.schema_version !== QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Quota monitor-poll commit request schema mismatch");
  }
  const phase = requireStringLiteral(request.phase, MONITOR_PHASES, "phase");
  const execute = requireBoolean(request.execute, "execute");
  const runtimeRoot = optionalString(request.runtime_root, "runtime_root")?.trim() ?? null;
  if (execute && !runtimeRoot) {
    throw new EffectRuntimeRequestError("executing quota monitor-poll commit requires runtime_root");
  }
  const goalId = safeGoalId(request.goal_id);
  const decision = decisionObject(request.decision);
  if (decision.goal_id !== goalId) {
    throw new EffectRuntimeRequestError("decision.goal_id must match goal_id");
  }
  const turnId = optionalString(
    request.turn_instance_id,
    "turn_instance_id",
  )?.trim() ?? null;
  if (turnId && !TURN_ID.test(turnId)) {
    throw new EffectRuntimeRequestError(
      "turn_instance_id must be 1-128 public-safe letters, numbers, or ._:-",
    );
  }
  if (turnId && !decision.agent_id) {
    throw new EffectRuntimeRequestError(
      "turn-scoped monitor-poll requires a registered --agent-id",
    );
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
      request.source,
      MONITOR_SOURCES,
      "source",
      `quota monitor-poll source must be one of: ${MONITOR_SOURCES.join(", ")}`,
    ),
    generated_at: requiredString(request.generated_at, "generated_at").trim(),
    execute,
    expected_index_digest: optionalString(
      request.expected_index_digest,
      "expected_index_digest",
    ),
    turn_instance_id: turnId,
    decision,
    observation,
    provider_receipt: jsonObject(request.provider_receipt),
    status_reload_warning: jsonObject(request.status_reload_warning),
  };
}

function stringSet(value: unknown): Set<string> {
  return new Set(
    Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      : [],
  );
}

function exactBlockedWait(decision: MonitorDecision): JsonObject | null {
  const wait = decision.vision_wait_state;
  return wait.schema_version === "goal_vision_wait_state_v0" &&
      wait.state === "waiting" &&
      wait.reason_code === "exact_blocked_successor" &&
      wait.automatic_resume === true &&
      Boolean(normalizedTodoId(wait.selected_todo_id, "decision.vision_wait_state.selected_todo_id")) &&
      typeof wait.resume_when === "string" && Boolean(wait.resume_when.trim())
    ? wait
    : null;
}

function blockedSuccessorAllowed(decision: MonitorDecision): boolean {
  return ([
    AgentScopeFrontierAction.AGENT_SCOPE_WAIT, EffectiveAction.MONITOR_QUIET_SKIP,
  ] satisfies readonly QuotaEffectiveActionValue[] as readonly string[]).includes(
    decision.effective_action ?? "",
  ) && !decision.should_run &&
    !decision.requires_user_action && exactBlockedWait(decision) !== null;
}

function externalMonitorAllowed(decision: MonitorDecision): boolean {
  if (decision.requires_user_action || !decision.should_run) return false;
  const external = decision.external_evidence_observation;
  if (external.required === true && external.must_attempt_observation === true) return true;
  const lane = decision.work_lane_contract;
  const reasonCodes = stringSet(lane.reason_codes);
  if (lane.must_attempt_work !== true) return false;
  if (!EXTERNAL_MONITOR_POLICIES.has(String(lane.monitor_policy ?? ""))) return false;
  if ([...reasonCodes].some((code) => EXTERNAL_MONITOR_REASON_CODES.has(code))) return true;
  return Object.keys(external).length > 0;
}

function candidateMatches(candidate: JsonObject, observation: MonitorObservation): boolean {
  if (candidate.task_class !== "continuous_monitor") return false;
  const candidateTodo = normalizedTodoId(candidate.todo_id, "due monitor candidate.todo_id");
  const candidateTarget = typeof candidate.target_key === "string"
    ? candidate.target_key.trim()
    : "";
  if (observation.todo_id && candidateTodo !== observation.todo_id) return false;
  if (observation.target_key && candidateTarget !== observation.target_key) return false;
  return Boolean(candidateTodo || candidateTarget);
}

function dueMonitorAllowed(
  decision: MonitorDecision,
  observation: MonitorObservation,
): boolean {
  const lane = decision.work_lane_contract;
  if (lane.must_attempt_work !== true) return false;
  const reasonCodes = stringSet(lane.reason_codes);
  const candidate = decision.due_monitor_candidates.find((item) =>
    candidateMatches(item, observation)
  );
  if (lane.obligation === "attempt_due_monitor") return Boolean(candidate);
  if (reasonCodes.has("due_monitor_context") && candidate) return true;
  const registry = decision.registry_due_monitor;
  return reasonCodes.has("due_monitor_context") && registry.due === true &&
    registry.claimed_by === decision.agent_id && candidateMatches(registry, observation);
}

interface Admission {
  blocked: boolean;
  due: boolean;
  external: boolean;
}

function admission(request: MonitorRequest): Admission {
  const blocked = blockedSuccessorAllowed(request.decision);
  const external = externalMonitorAllowed(request.decision);
  const due = dueMonitorAllowed(request.decision, request.observation);
  if (
    request.decision.effective_action !== EffectiveAction.MONITOR_QUIET_SKIP &&
    !external && !due && !blocked
  ) {
    throw new EffectRuntimeRequestError(
      "monitor-poll requires monitor_quiet_skip, due monitor todo, external monitor observation, or exact blocked successor wait",
      "monitor_poll_admission_rejected",
    );
  }
  if (
    request.decision.heartbeat_recommendation.recommended_mode !==
      "monitor_quiet_until_material_transition" &&
    !external && !due && !blocked
  ) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll requires monitor_quiet_until_material_transition or exact blocked successor wait mode",
      "monitor_poll_admission_rejected",
    );
  }
  return { blocked, due, external };
}

function collapsedSummary(value: unknown, limit = 160): string {
  const text = String(value ?? "").trim().replace(/\s+/g, " ");
  return text.length <= limit ? text : `${text.slice(0, limit - 3).trimEnd()}...`;
}

function jsonString(value: string): string {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (character) =>
    `\\u${character.codePointAt(0)!.toString(16).padStart(4, "0")}`
  );
}

function pythonJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return jsonString(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(pythonJson).join(", ")}]`;
  const object = jsonObject(value);
  if (!object) throw new EffectRuntimeRequestError("monitor target contains a non-JSON value");
  return `{${Object.keys(object).sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right))).map((key) =>
    `${jsonString(key)}: ${pythonJson(object[key])}`
  ).join(", ")}}`;
}

function sha256Hex(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function blockedFrontierIdentity(decision: MonitorDecision): string | null {
  const wait = exactBlockedWait(decision);
  if (!wait) return null;
  return sha256Hex(pythonJson({
    agent_id: typeof wait.agent_id === "string" ? wait.agent_id.trim() : "",
    reason_code: "exact_blocked_successor",
    selected_todo_id: normalizedTodoId(
      wait.selected_todo_id,
      "decision.vision_wait_state.selected_todo_id",
    ),
    resume_when: String(wait.resume_when ?? "").trim(),
  })).slice(0, 16);
}

function monitorTarget(
  decision: MonitorDecision,
  monitorMode: string,
): JsonObject {
  const actionSummary = collapsedSummary(
    decision.recommended_action ?? decision.reason ?? "",
  );
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

function compactDecision(decision: MonitorDecision): JsonObject {
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

function compactProviderWriteback(receipt: JsonObject): JsonObject {
  const compact: JsonObject = {};
  for (const field of [
    "schema_version",
    "dry_run",
    "goal_id",
    "todo_id",
    "target_key",
    "result_hash",
    "material_change",
    "consecutive_no_change",
    "last_checked_at",
    "next_due_at",
    "cadence",
    "successor_receipts",
  ]) {
    compact[field] = receipt[field] ?? null;
  }
  if (receipt.lease_proof != null) compact.lease_proof = receipt.lease_proof;
  Object.assign(compact, monitorProjectionDelivery(receipt));
  return compact;
}

/** Display acknowledgement is diagnostic, never evidence for business commit.
 * Omitted on the legacy path to retain its exact v0 response shape. */
function monitorProjectionDelivery(receipt: JsonObject): JsonObject {
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
  for (const key of ["schema_version", "status", "reason_code", "retryable", "recommended_action", "retry_business_mutation"]) {
    if (outbox[key] != null) diagnostic[key] = outbox[key];
  }
  return {projection_delivery: status, projection_outbox: diagnostic};
}

function buildRecord(request: MonitorRequest, allowed: Admission): JsonObject {
  const material = request.observation.material_change;
  let kind = "monitor";
  let prefix = "monitor";
  if (allowed.blocked) {
    kind = "blocked successor wait";
    prefix = "blocked_successor_wait";
  } else if (allowed.external) {
    kind = "external monitor";
    prefix = "external_monitor";
  } else if (allowed.due) {
    kind = "due monitor";
    prefix = "due_monitor";
  }
  const recommendationReason = typeof request.decision.heartbeat_recommendation.reason === "string"
    ? request.decision.heartbeat_recommendation.reason
    : null;
  let monitorMode: string;
  let defaultReason: string;
  let healthCheck: string;
  if (material) {
    monitorMode = `${prefix}_material_transition`;
    defaultReason = `${kind} observation produced a material transition`;
    healthCheck = `${kind} material transition observed; follow-up state updated; no quota spend by monitor-poll`;
  } else if (allowed.blocked) {
    monitorMode = "blocked_successor_wait_without_material_transition";
    defaultReason = recommendationReason ?? request.decision.reason ??
      "exact blocked successor wait produced no material transition";
    healthCheck = "exact blocked successor wait unchanged; no quota spend; bounded replan after two identical frontier observations";
  } else if (allowed.external) {
    monitorMode = "external_monitor_observed_without_material_transition";
    defaultReason = "external monitor observation produced no material transition";
    healthCheck = "external monitor observation unchanged; no quota spend; no material transition";
  } else if (allowed.due) {
    monitorMode = "due_monitor_observed_without_material_transition";
    defaultReason = recommendationReason ?? request.decision.reason ??
      "due monitor poll had no material transition";
    healthCheck = "due monitor observation unchanged; no quota spend; next due updated";
  } else {
    monitorMode = "monitor_quiet_until_material_transition";
    defaultReason = recommendationReason ?? request.decision.reason ??
      "monitor-only poll had no material transition";
    healthCheck = "monitor-only poll unchanged; no quota spend; no material transition";
  }
  const target = monitorTarget(request.decision, monitorMode);
  const event: JsonObject = {
    event_type: QUOTA_MONITOR_POLL_CLASSIFICATION,
    source: request.source,
    monitor_mode: monitorMode,
    monitor_target: target,
    reason_summary: request.observation.reason_summary ?? defaultReason,
    material_change: material,
    settlement_todo_id: request.observation.settlement_todo_id,
    todo_id: request.observation.todo_id,
    target_key: request.observation.target_key,
    result_hash: request.observation.result_hash,
    before: compactDecision(request.decision),
  };
  const record: JsonObject = {
    generated_at: request.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_MONITOR_POLL_CLASSIFICATION,
    material_change: material,
    recommended_action: request.decision.recommended_action ?? recommendationReason ??
      request.decision.reason,
    health_check: healthCheck,
    delivery_outcome: material ? "outcome_progress" : "surface_only",
    settlement_todo_id: request.observation.settlement_todo_id,
    monitor_target: target,
    monitor_event: event,
  };
  if (request.decision.agent_id) {
    record.agent_id = request.decision.agent_id;
    event.agent_id = request.decision.agent_id;
  }
  if (request.turn_instance_id) {
    record.turn_instance_id = request.turn_instance_id;
    event.turn_instance_id = request.turn_instance_id;
  }
  if (request.provider_receipt) {
    event.todo_writeback = compactProviderWriteback(request.provider_receipt);
  }
  if (request.status_reload_warning) {
    event.status_reload_warning = request.status_reload_warning;
  }
  return record;
}

function sha256(value: string): string {
  return `sha256:${sha256Hex(value)}`;
}

function sha256Bytes(value: Uint8Array): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function requestDigest(request: MonitorRequest): string {
  // Admission evidence is intentionally absent: Todo writeback changes the
  // projected decision during a retry, while the logical observation remains
  // the same effect. Mutable phase/CAS/provider fields are fenced separately.
  const observation: JsonObject = { ...request.observation };
  if (!observation.settlement_todo_id) delete observation.settlement_todo_id;
  return sha256(pythonJson({
    schema_version: request.schema_version,
    effect_id: request.effect_id,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    source: request.source,
    turn_instance_id: request.turn_instance_id,
    observation,
  }));
}

function providerPlanFor(request: MonitorRequest): MonitorProviderPlan {
  const resultHash = request.observation.result_hash;
  if (!resultHash) {
    throw new EffectRuntimeRequestError("monitor todo writeback requires --result-hash");
  }
  return {
    schema_version: request.observation.lease_proof ? LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA : MONITOR_TODO_PROVIDER_PLAN_SCHEMA,
    ...(request.observation.lease_proof ? {lease_proof: request.observation.lease_proof} : {}),
    monitor_effect_id: request.effect_id,
    goal_id: request.goal_id,
    generated_at: request.generated_at,
    execute: request.execute,
    todo_id: request.observation.todo_id,
    target_key: request.observation.target_key,
    result_hash: resultHash,
    material_change: request.observation.material_change,
    cadence: request.observation.cadence,
    next_due_at: request.observation.next_due_at,
    reason_summary: request.observation.reason_summary,
    next_agent_todo: request.observation.next_agent_todo,
    next_action_kind: request.observation.next_action_kind,
    next_task_repository: request.observation.next_task_repository,
    next_required_capabilities: request.observation.next_required_capabilities,
    next_continuation_policy: request.observation.next_continuation_policy,
    next_target_key: request.observation.next_target_key,
    next_user_todo: request.observation.next_user_todo,
    next_user_task_class: request.observation.next_user_task_class,
    next_claimed_by: request.observation.next_claimed_by,
    agent_id: request.observation.actor_agent_id ?? request.decision.agent_id,
  };
}

function providerPlanObject(value: unknown): MonitorProviderPlan {
  const plan = requiredObject(value, "receipt.provider_plan");
  if (plan.schema_version !== MONITOR_TODO_PROVIDER_PLAN_SCHEMA &&
      plan.schema_version !== LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA) {
    throw new EffectRuntimeRequestError("Monitor Todo provider plan schema mismatch");
  }
  const proof = decodeTaskLeaseProof(plan.lease_proof);
  if ((plan.schema_version === LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA) !== (proof !== null)) {
    throw new EffectRuntimeRequestError("Monitor provider plan lease proof/schema mismatch");
  }
  return {
    schema_version: plan.schema_version,
    ...(proof ? {lease_proof: proof} : {}),
    monitor_effect_id: requiredString(
      plan.monitor_effect_id,
      "provider_plan.monitor_effect_id",
    ),
    goal_id: safeGoalId(plan.goal_id),
    generated_at: requiredString(plan.generated_at, "provider_plan.generated_at"),
    execute: requireBoolean(plan.execute, "provider_plan.execute"),
    todo_id: normalizedTodoId(plan.todo_id, "provider_plan.todo_id"),
    target_key: optionalString(plan.target_key, "provider_plan.target_key")?.trim() ?? null,
    result_hash: requiredString(plan.result_hash, "provider_plan.result_hash").trim(),
    material_change: requireBoolean(
      plan.material_change,
      "provider_plan.material_change",
    ),
    cadence: optionalString(plan.cadence, "provider_plan.cadence")?.trim() ?? null,
    next_due_at: optionalString(plan.next_due_at, "provider_plan.next_due_at")?.trim() ?? null,
    reason_summary: optionalString(plan.reason_summary, "provider_plan.reason_summary"),
    next_agent_todo: optionalString(plan.next_agent_todo, "provider_plan.next_agent_todo"),
    next_action_kind: optionalString(plan.next_action_kind, "provider_plan.next_action_kind")
      ?.trim() ?? null,
    next_task_repository: optionalString(
      plan.next_task_repository,
      "provider_plan.next_task_repository",
    )?.trim() ?? null,
    next_required_capabilities: requireStringArray(
      plan.next_required_capabilities,
      "provider_plan.next_required_capabilities",
    ),
    next_continuation_policy: optionalString(
      plan.next_continuation_policy,
      "provider_plan.next_continuation_policy",
    )?.trim() ?? null,
    next_target_key: optionalString(plan.next_target_key, "provider_plan.next_target_key")
      ?.trim() ?? null,
    next_user_todo: optionalString(plan.next_user_todo, "provider_plan.next_user_todo"),
    next_user_task_class: optionalString(
      plan.next_user_task_class,
      "provider_plan.next_user_task_class",
    )?.trim() ?? null,
    next_claimed_by: optionalString(plan.next_claimed_by, "provider_plan.next_claimed_by")
      ?.trim() ?? null,
    agent_id: optionalString(plan.agent_id, "provider_plan.agent_id")?.trim() ?? null,
  };
}

function objectArray(value: unknown, label: string): JsonObject[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an array`);
  }
  return value.map((item, index) => requiredObject(item, `${label}[${index}]`));
}

function requireProviderMatch(
  actual: unknown,
  expected: unknown,
  label: string,
): void {
  if (actual !== expected) {
    throw new EffectRuntimeRequestError(`${label} must match provider plan`);
  }
}

function requireProviderCapabilityMatch(
  actual: unknown,
  expected: readonly string[],
  label: string,
): void {
  const actualCapabilities = normalizeTodoCapabilities(actual, label);
  const expectedCapabilities = normalizeTodoCapabilities(expected, label);
  if (pythonJson(actualCapabilities) !== pythonJson(expectedCapabilities)) {
    throw new EffectRuntimeRequestError(`${label} must match provider plan`);
  }
}

function requiredProviderTodoId(value: unknown, label: string): string {
  const todoId = normalizedTodoId(value, label);
  if (!todoId) {
    throw new EffectRuntimeRequestError(`${label} is required`);
  }
  return todoId;
}

function requireProviderTodoText(
  value: unknown,
  expected: string,
  label: string,
): void {
  const actual = requiredString(value, label).trim().replace(/\s+/g, " ");
  const compactExpected = expected.trim().replace(/\s+/g, " ");
  requireProviderMatch(actual, compactExpected, label);
}

function requireCanonicalSuccessorRoute(
  value: JsonObject,
  expected: JsonObject,
  label: string,
): void {
  requireProviderMatch(
    optionalString(value.task_repository, `${label} task_repository`)?.trim() ?? null,
    expected.task_repository,
    `${label} task_repository`,
  );
  requireProviderCapabilityMatch(
    value.required_capabilities ?? [],
    requireStringArray(expected.required_capabilities, "expected capabilities"),
    `${label} required_capabilities`,
  );
  requireProviderMatch(
    optionalString(value.continuation_policy, `${label} continuation_policy`)?.trim() ?? null,
    expected.continuation_policy,
    `${label} continuation_policy`,
  );
  requireProviderMatch(
    optionalString(value.target_key, `${label} target_key`)?.trim() ?? null,
    expected.target_key,
    `${label} target_key`,
  );
  requireProviderMatch(
    optionalString(value.claimed_by, `${label} claimed_by`)?.trim() ?? null,
    expected.claimed_by,
    `${label} claimed_by`,
  );
}

function validateSuccessorReceipts(
  receipts: readonly JsonObject[],
  nextTodos: readonly JsonObject[],
  plan: MonitorProviderPlan,
  todoId: string,
): void {
  const expectedCount = plan.material_change
    ? Number(Boolean(plan.next_agent_todo)) + Number(Boolean(plan.next_user_todo))
    : 0;
  if (receipts.length !== expectedCount) {
    throw new EffectRuntimeRequestError(
      "provider_receipt.successor_receipts must match the requested successor routes",
    );
  }
  if (nextTodos.length !== receipts.length) {
    throw new EffectRuntimeRequestError(
      "provider_receipt.next_todos must match successor_receipts",
    );
  }
  let offset = 0;
  if (plan.material_change && plan.next_agent_todo) {
    const canonicalRoute = monitorSuccessorRoute(monitorSuccessorIntent(plan), todoId, plan.result_hash);
    const receipt = receipts[offset];
    const nextTodo = nextTodos[offset++];
    requireProviderMatch(receipt.role, "agent", "agent successor role");
    requireProviderMatch(nextTodo.role, "agent", "agent next_todo role");
    requireProviderMatch(
      receipt.task_class,
      "advancement_task",
      "agent successor task_class",
    );
    requireProviderMatch(
      nextTodo.task_class,
      "advancement_task",
      "agent next_todo task_class",
    );
    requireProviderMatch(
      receipt.action_kind,
      canonicalRoute.action_kind,
      "agent successor action_kind",
    );
    requireProviderMatch(
      nextTodo.action_kind,
      canonicalRoute.action_kind,
      "agent next_todo action_kind",
    );
    requireProviderMatch(
      receipt.unblocks_todo_id,
      todoId,
      "agent successor unblocks_todo_id",
    );
    requireProviderMatch(
      nextTodo.unblocks_todo_id,
      todoId,
      "agent next_todo unblocks_todo_id",
    );
    requireProviderTodoText(
      nextTodo.todo,
      plan.next_agent_todo,
      "agent next_todo text",
    );
    requireProviderMatch(
      requiredProviderTodoId(receipt.todo_id, "agent successor todo_id"),
      requiredProviderTodoId(nextTodo.todo_id, "agent next_todo todo_id"),
      "agent successor todo_id",
    );
    requireCanonicalSuccessorRoute(receipt, canonicalRoute, "agent successor");
    requireCanonicalSuccessorRoute(nextTodo, canonicalRoute, "agent next_todo");
  }
  if (plan.material_change && plan.next_user_todo) {
    const receipt = receipts[offset];
    const nextTodo = nextTodos[offset];
    requireProviderMatch(receipt.role, "user", "user successor role");
    requireProviderMatch(nextTodo.role, "user", "user next_todo role");
    requireProviderMatch(
      receipt.task_class,
      plan.next_user_task_class,
      "user successor task_class",
    );
    requireProviderMatch(
      nextTodo.task_class,
      plan.next_user_task_class,
      "user next_todo task_class",
    );
    requireProviderTodoText(
      nextTodo.todo,
      plan.next_user_todo,
      "user next_todo text",
    );
    requireProviderMatch(
      requiredProviderTodoId(receipt.todo_id, "user successor todo_id"),
      requiredProviderTodoId(nextTodo.todo_id, "user next_todo todo_id"),
      "user successor todo_id",
    );
    if (plan.next_user_task_class === "user_gate") {
      requireProviderMatch(
        receipt.action_kind,
        "gate",
        "user successor action_kind",
      );
      requireProviderMatch(
        receipt.unblocks_todo_id,
        todoId,
        "user successor unblocks_todo_id",
      );
    }
  }
}

function validatedProviderReceipt(
  value: unknown,
  plan: MonitorProviderPlan,
): JsonObject {
  const receipt = requiredObject(value, "provider_receipt");
  if (receipt.schema_version !== MONITOR_TODO_WRITEBACK_SCHEMA) {
    throw new EffectRuntimeRequestError("Monitor Todo provider receipt schema mismatch");
  }
  requireProviderMatch(
    receipt.monitor_effect_id,
    plan.monitor_effect_id,
    "provider_receipt.monitor_effect_id",
  );
  requireProviderMatch(receipt.goal_id, plan.goal_id, "provider_receipt.goal_id");
  const todoId = normalizedTodoId(receipt.todo_id, "provider_receipt.todo_id");
  if (!todoId) {
    throw new EffectRuntimeRequestError("provider_receipt.todo_id is required");
  }
  if (plan.todo_id) {
    requireProviderMatch(todoId, plan.todo_id, "provider_receipt.todo_id");
  }
  const targetKey = optionalString(
    receipt.target_key,
    "provider_receipt.target_key",
  )?.trim() ?? null;
  if (plan.target_key) {
    requireProviderMatch(targetKey, plan.target_key, "provider_receipt.target_key");
  }
  const resultHash = requiredString(
    receipt.result_hash,
    "provider_receipt.result_hash",
  ).trim();
  requireProviderMatch(resultHash, plan.result_hash, "provider_receipt.result_hash");
  const materialChange = requireBoolean(
    receipt.material_change,
    "provider_receipt.material_change",
  );
  requireProviderMatch(
    materialChange,
    plan.material_change,
    "provider_receipt.material_change",
  );
  const dryRun = requireBoolean(receipt.dry_run, "provider_receipt.dry_run");
  requireProviderMatch(dryRun, !plan.execute, "provider_receipt.dry_run");
  const generation = requireInteger(
    receipt.material_change_generation,
    "provider_receipt.material_change_generation",
  );
  const noChange = requireInteger(
    receipt.consecutive_no_change,
    "provider_receipt.consecutive_no_change",
  );
  if (generation < 0 || noChange < 0) {
    throw new EffectRuntimeRequestError("provider receipt monitor counters cannot be negative");
  }
  const lastCheckedAt = requiredString(
    receipt.last_checked_at,
    "provider_receipt.last_checked_at",
  ).trim();
  requireProviderMatch(
    lastCheckedAt,
    plan.generated_at,
    "provider_receipt.last_checked_at",
  );
  const nextDueAt = optionalString(
    receipt.next_due_at,
    "provider_receipt.next_due_at",
  )?.trim() ?? null;
  if (plan.next_due_at) {
    requireProviderMatch(nextDueAt, plan.next_due_at, "provider_receipt.next_due_at");
  }
  const cadence = optionalString(receipt.cadence, "provider_receipt.cadence")?.trim() ?? null;
  if (plan.cadence) {
    requireProviderMatch(cadence, plan.cadence, "provider_receipt.cadence");
  }
  const nextTodos = objectArray(receipt.next_todos, "provider_receipt.next_todos");
  const successors = objectArray(
    receipt.successor_receipts,
    "provider_receipt.successor_receipts",
  );
  validateSuccessorReceipts(successors, nextTodos, plan, todoId);
  const proof = decodeTaskLeaseProof(receipt.lease_proof);
  requireProviderMatch(proof?.idempotency_key ?? null, plan.lease_proof?.idempotency_key ?? null,
    "provider_receipt.lease_proof.idempotency_key");
  requireProviderMatch(proof?.expected_version ?? null, plan.lease_proof?.expected_version ?? null,
    "provider_receipt.lease_proof.expected_version");
  return {
    schema_version: MONITOR_TODO_WRITEBACK_SCHEMA,
    ...(proof ? {lease_proof: proof} : {}),
    dry_run: dryRun,
    goal_id: plan.goal_id,
    todo_id: todoId,
    target_key: targetKey,
    result_hash: resultHash,
    material_change: materialChange,
    material_change_generation: generation,
    consecutive_no_change: noChange,
    last_checked_at: lastCheckedAt,
    next_due_at: nextDueAt,
    cadence,
    todo_update: requiredObject(receipt.todo_update, "provider_receipt.todo_update"),
    next_todos: nextTodos,
    successor_receipts: successors,
    ...monitorProjectionDelivery(receipt),
  };
}

function requestWithProvider(
  request: MonitorRequest,
  plan: MonitorProviderPlan,
  providerReceipt: JsonObject,
): MonitorRequest {
  return {
    ...request,
    generated_at: plan.generated_at,
    observation: {
      ...request.observation,
      todo_id: String(providerReceipt.todo_id),
      target_key: optionalString(
        providerReceipt.target_key,
        "provider_receipt.target_key",
      )?.trim() ?? null,
      result_hash: String(providerReceipt.result_hash),
    },
    provider_receipt: providerReceipt,
  };
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return error instanceof Error && "code" in error && error.code === code;
}

async function readOptionalText(path: string): Promise<string | null> {
  try {
    return await readFile(path, "utf8");
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return null;
    throw error;
  }
}

async function readOptionalBytes(path: string): Promise<Buffer | null> {
  try {
    return await readFile(path);
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return null;
    throw error;
  }
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return false;
    throw error;
  }
}

function runStem(generatedAt: string): string {
  const normalized = generatedAt.replace(/[^0-9A-Za-z-]+/g, "-").replace(/^-+/, "");
  let trailingHyphenStart = normalized.length;
  while (trailingHyphenStart > 0 && normalized[trailingHyphenStart - 1] === "-") {
    trailingHyphenStart -= 1;
  }
  const stem = normalized.slice(0, trailingHyphenStart);
  if (!stem) {
    throw new EffectRuntimeRequestError("generated_at cannot form a run artifact name");
  }
  return stem;
}

async function nextArtifactPaths(
  runsDir: string,
  generatedAt: string,
  effectId: string,
): Promise<{ jsonPath: string; markdownPath: string }> {
  const effectDigest = sha256Hex(effectId).slice(0, 24);
  const base = `${runStem(generatedAt)}-quota-monitor-poll-${effectDigest}`;
  for (let index = 1; ; index += 1) {
    const stem = index === 1 ? base : `${base}-${index}`;
    const jsonPath = join(runsDir, `${stem}.json`);
    const markdownPath = join(runsDir, `${stem}.md`);
    if (!await pathExists(jsonPath) && !await pathExists(markdownPath)) {
      return { jsonPath, markdownPath };
    }
  }
}

function transactionPath(runsDir: string, effectId: string): string {
  return join(
    runsDir,
    ".transactions",
    "quota-monitor-poll",
    `${sha256Hex(effectId).slice(0, 24)}.json`,
  );
}

function committedReceiptStagePath(receiptPath: string): string {
  return `${receiptPath}.committed`;
}

export async function quotaMonitorPollIndexDigest(
  indexPath: string,
): Promise<string | null> {
  const content = await readOptionalBytes(indexPath);
  return content === null ? null : sha256Bytes(content);
}

function indexRecords(content: string | null): JsonObject[] {
  if (content === null) return [];
  const records: JsonObject[] = [];
  for (const [index, line] of content.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      throw new EffectRuntimeRequestError(
        `quota run index line ${index + 1} is malformed`,
        "malformed_run_index",
      );
    }
    records.push(requiredObject(value, `quota run index line ${index + 1}`));
  }
  return records;
}

function matchingIndexRecord(
  records: readonly JsonObject[],
  effectId: string,
): JsonObject | null {
  for (const record of [...records].reverse()) {
    const metadata = jsonObject(record.quota_monitor_poll_commit);
    if (metadata?.effect_id === effectId) return record;
  }
  return null;
}

function pendingIndexHistoryIntact(
  pending: PendingMonitorReceipt,
  current: Buffer | null,
): boolean {
  const expectedBytes = pending.expected_index_bytes;
  if (!Number.isSafeInteger(expectedBytes) || expectedBytes < 0 ||
      (current?.length ?? 0) < expectedBytes) return false;
  if (pending.expected_index_digest === null) {
    return expectedBytes === 0;
  }
  return sha256Bytes((current ?? Buffer.alloc(0)).subarray(0, expectedBytes)) ===
    pending.expected_index_digest;
}

function indexRecordFor(
  request: MonitorRequest,
  record: JsonObject,
  jsonPath: string,
  markdownPath: string,
  fingerprint: string,
): JsonObject {
  const event = requiredObject(record.monitor_event, "record.monitor_event");
  const indexRecord: JsonObject = {
    generated_at: record.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_MONITOR_POLL_CLASSIFICATION,
    recommended_action: record.recommended_action,
    health_check: record.health_check,
    delivery_outcome: record.delivery_outcome,
    monitor_target: record.monitor_target,
    json_path: jsonPath,
    markdown_path: markdownPath,
    quota_monitor_poll_commit: {
      schema_version: QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
      effect_id: request.effect_id,
      request_digest: fingerprint,
    },
  };
  for (const [field, value] of Object.entries({
    agent_id: record.agent_id,
    turn_instance_id: record.turn_instance_id,
    settlement_todo_id: event.settlement_todo_id,
    todo_id: event.todo_id,
    target_key: event.target_key,
    material_change: event.material_change === true ? true : null,
  })) {
    if (value !== null && value !== undefined && value !== "") indexRecord[field] = value;
  }
  const successorIds = successorReceipts(request.provider_receipt).map((receipt) =>
    String(receipt.todo_id ?? "").trim()
  ).filter(Boolean);
  if (successorIds.length) indexRecord.successor_todo_ids = successorIds;
  return indexRecord;
}

function pyValue(value: unknown): string {
  if (value === true) return "True";
  if (value === false) return "False";
  if (value === null || value === undefined) return "None";
  return String(value);
}

function monitorMarkdown(record: JsonObject): string {
  const event = requiredObject(record.monitor_event, "record.monitor_event");
  const before = requiredObject(event.before, "record.monitor_event.before");
  const target = requiredObject(event.monitor_target, "record.monitor_event.monitor_target");
  const writeback = jsonObject(event.todo_writeback) ?? {};
  const lines = [
    "# LoopX Quota Monitor Poll",
    "",
    `- goal_id: \`${pyValue(record.goal_id)}\``,
    `- classification: \`${pyValue(record.classification)}\``,
    `- agent_id: \`${pyValue(record.agent_id ?? event.agent_id ?? "")}\``,
    `- source: \`${pyValue(event.source)}\``,
    `- effective_action: \`${pyValue(before.effective_action)}\``,
    `- monitor_target: \`${pyValue(target.target_id)}\``,
    `- settlement_todo_id: \`${pyValue(event.settlement_todo_id ?? "")}\``,
    `- todo_id: \`${pyValue(event.todo_id ?? "")}\``,
    `- target_key: \`${pyValue(event.target_key ?? "")}\``,
    `- material_change: \`${pyValue(event.material_change)}\``,
    `- should_run: \`${pyValue(before.should_run)}\``,
    `- self_repair_allowed: \`${pyValue(before.self_repair_allowed)}\``,
    `- state: \`${pyValue(before.state)}\``,
    `- health_check: ${pyValue(record.health_check)}`,
    `- reason: ${pyValue(event.reason_summary)}`,
  ];
  if (Object.keys(writeback).length) {
    lines.push(
      "- todo_writeback: " +
        `dry_run=${pyValue(writeback.dry_run)} ` +
        `consecutive_no_change=${pyValue(writeback.consecutive_no_change)} ` +
        `last_checked_at=${pyValue(writeback.last_checked_at)} ` +
        `next_due_at=${pyValue(writeback.next_due_at)}`,
    );
  }
  return `${lines.join("\n")}\n`;
}

function successorReceipts(providerReceipt: JsonObject | null): JsonObject[] {
  return Array.isArray(providerReceipt?.successor_receipts)
    ? providerReceipt.successor_receipts.map((value, index) =>
      requiredObject(value, `provider_receipt.successor_receipts[${index}]`)
    )
    : [];
}

function payloadFor(
  request: MonitorRequest,
  record: JsonObject,
  jsonPath: string,
  markdownPath: string,
  indexPath: string,
  options: { appended: boolean; replayed: boolean; repaired: boolean },
): JsonObject {
  const event = requiredObject(record.monitor_event, "record.monitor_event");
  const receipts = successorReceipts(request.provider_receipt);
  const payload: JsonObject = {
    ok: true,
    mode: "monitor-poll",
    dry_run: !request.execute,
    goal_id: request.goal_id,
    appended: options.appended,
    registry_mutated: false,
    source: event.source,
    classification: QUOTA_MONITOR_POLL_CLASSIFICATION,
    generated_at: record.generated_at,
    agent_id: record.agent_id ?? null,
    settlement_todo_id: event.settlement_todo_id ?? null,
    todo_id: event.todo_id ?? null,
    target_key: event.target_key ?? null,
    material_change: event.material_change === true,
    monitor_event: event,
    todo_writeback: request.provider_receipt,
    successor_todo_ids: receipts.map((receipt) => receipt.todo_id).filter(Boolean),
    successor_receipts: receipts,
    health_check: record.health_check,
    delivery_outcome: record.delivery_outcome,
    json_path: jsonPath,
    markdown_path: markdownPath,
    index_path: indexPath,
    transaction_repaired: options.repaired,
    reason: options.replayed
      ? "replayed existing monitor poll event for the same effect identity"
      : options.repaired
      ? "quota monitor-poll commit repaired its durable transaction artifacts"
      : `${request.execute ? "appended" : "dry-run preview"} monitor poll event: ` +
        `${request.goal_id} effective_action=${request.decision.effective_action}`,
  };
  if (request.turn_instance_id) {
    payload.turn_instance_id = request.turn_instance_id;
    payload.replayed = options.replayed;
    if (request.execute) {
      const settlementTodoId = optionalString(
        event.settlement_todo_id,
        "monitor_event.settlement_todo_id",
      )?.trim() ?? null;
      const observedTodoId = optionalString(
        event.todo_id,
        "monitor_event.todo_id",
      )?.trim() ?? null;
      const exactSettlement = settlementTodoId !== null &&
        observedTodoId === settlementTodoId;
      const auxiliaryObservation = settlementTodoId !== null &&
        observedTodoId !== null && observedTodoId !== settlementTodoId;
      payload.turn_continuation = exactSettlement
        ? {
          schema_version: "quota_turn_continuation_v0",
          settlement_binding_matches_observation: true,
          current_turn_settled: true,
          same_turn_independent_settlement_allowed: false,
          next_turn_required: true,
          next_action:
            "rerun quota should-run with a fresh --turn-instance-id before independent work",
          reason: "the committed monitor-poll is this Turn's single settlement identity",
        }
        : {
          schema_version: "quota_turn_continuation_v0",
          settlement_binding_matches_observation: auxiliaryObservation
            ? false
            : null,
          current_turn_settled: false,
          same_turn_independent_settlement_allowed: false,
          next_turn_required: false,
          next_action: auxiliaryObservation
            ? "continue the original advancement settlement in this Turn"
            : "obtain a typed settlement binding before claiming this Turn settled",
          reason: auxiliaryObservation
            ? "the committed monitor-poll is an auxiliary observation and does not settle the advancement Turn"
            : "the committed monitor-poll has no exact Todo settlement binding",
        };
    }
  }
  if (request.status_reload_warning) {
    payload.status_reload_warning = request.status_reload_warning;
  }
  return payload;
}

function receiptObject(value: unknown): MonitorReceipt {
  const receipt = requiredObject(value, "quota monitor-poll transaction receipt");
  if (receipt.schema_version !== QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA &&
      receipt.schema_version !== MONITOR_PENDING_ADMISSION_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Quota monitor-poll transaction receipt schema mismatch",
    );
  }
  const expectedBytes = requireInteger(
    receipt.expected_index_bytes,
    "receipt.expected_index_bytes",
  );
  if (expectedBytes < 0) {
    throw new EffectRuntimeRequestError("receipt.expected_index_bytes cannot be negative");
  }
  const common = {
    schema_version: QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
    effect_id: requiredString(receipt.effect_id, "receipt.effect_id"),
    request_digest: requiredString(receipt.request_digest, "receipt.request_digest"),
    index_path: requiredString(receipt.index_path, "receipt.index_path"),
    expected_index_digest: optionalString(
      receipt.expected_index_digest,
      "receipt.expected_index_digest",
    ),
    expected_index_bytes: expectedBytes,
  } as const;
  const status = requireStringLiteral(
    receipt.status,
    ["provider_pending", "prepared", "committed"] as const,
    "receipt.status",
  );
  if (status === "provider_pending") {
    if (receipt.schema_version === MONITOR_PENDING_ADMISSION_SCHEMA) {
      return {...common, schema_version: MONITOR_PENDING_ADMISSION_SCHEMA, status,
        provider_plan: providerPlanObject(receipt.provider_plan),
        admitted_decision: decisionObject(receipt.admitted_decision)};
    }
    return {
      ...common,
      status,
      provider_plan: providerPlanObject(receipt.provider_plan),
    };
  }
  if (receipt.schema_version !== QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA) {
    throw new EffectRuntimeRequestError("admitted pending receipt cannot represent a completed settlement",
      "malformed_transaction_receipt");
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

async function readReceipt(path: string): Promise<MonitorReceipt | null> {
  const content = await readOptionalText(path);
  if (content === null) return null;
  try {
    return receiptObject(JSON.parse(content));
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new EffectRuntimeRequestError(
        "quota monitor-poll transaction receipt is malformed",
        "malformed_transaction_receipt",
      );
    }
    throw error;
  }
}

function validateReceiptScope(
  receipt: MonitorReceipt,
  runsDir: string,
  indexPath: string,
): void {
  if (resolve(receipt.index_path) !== resolve(indexPath)) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll receipt artifact paths do not match the transaction scope",
      "malformed_transaction_receipt",
    );
  }
  if (receipt.status === "provider_pending") return;
  const jsonPath = resolve(receipt.json_path);
  const markdownPath = resolve(receipt.markdown_path);
  const resolvedRunsDir = resolve(runsDir);
  const effectToken = sha256Hex(receipt.effect_id).slice(0, 24);
  const jsonName = basename(jsonPath);
  const markdownName = basename(markdownPath);
  const artifact = new RegExp(
    `-quota-monitor-poll-${effectToken}(?:-[2-9][0-9]*)?\\.(json|md)$`,
  );
  if (
    dirname(jsonPath) !== resolvedRunsDir ||
    dirname(markdownPath) !== resolvedRunsDir ||
    !artifact.test(jsonName) ||
    !artifact.test(markdownName) ||
    jsonName.slice(0, -5) !== markdownName.slice(0, -3)
  ) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll receipt artifact paths do not match the transaction scope",
      "malformed_transaction_receipt",
    );
  }
}

function repairedTruncatedTail(
  content: Buffer,
  receipt: DurableMonitorReceipt,
): string | null {
  if (content.length <= receipt.expected_index_bytes) return null;
  const prefix = content.subarray(0, receipt.expected_index_bytes);
  const tail = content.subarray(receipt.expected_index_bytes);
  const expectedLine = Buffer.from(`${JSON.stringify(receipt.index_record)}\n`, "utf8");
  if (tail.length >= expectedLine.length || !expectedLine.subarray(0, tail.length).equals(tail)) {
    return null;
  }
  if (
    receipt.expected_index_digest === null
      ? prefix.length !== 0
      : sha256Bytes(prefix) !== receipt.expected_index_digest
  ) return null;
  indexRecords(prefix.toString("utf8"));
  return `${prefix.toString("utf8")}${expectedLine.toString("utf8")}`;
}

async function ensureJsonArtifact(path: string, expected: JsonObject): Promise<boolean> {
  const content = await readOptionalText(path);
  if (content === null) {
    await atomicWriteJson(path, expected);
    return true;
  }
  let actual: unknown;
  try {
    actual = JSON.parse(content);
  } catch {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll JSON artifact is malformed",
      "artifact_conflict",
    );
  }
  if (pythonJson(actual) !== pythonJson(expected)) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll JSON artifact conflicts with its transaction receipt",
      "artifact_conflict",
    );
  }
  return false;
}

async function ensureMarkdownArtifact(
  path: string,
  expected: string,
): Promise<boolean> {
  const markdown = await readOptionalText(path);
  if (markdown === null) {
    await atomicWriteText(path, expected);
    return true;
  }
  if (markdown !== expected) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll Markdown artifact conflicts with its transaction receipt",
      "artifact_conflict",
    );
  }
  return false;
}

async function ensureContentArtifacts(
  receipt: DurableMonitorReceipt,
): Promise<boolean> {
  const [jsonRepaired, markdownRepaired] = await Promise.all([
    ensureJsonArtifact(receipt.json_path, receipt.record),
    ensureMarkdownArtifact(receipt.markdown_path, receipt.markdown),
  ]);
  return jsonRepaired || markdownRepaired;
}

async function ensureIndexArtifact(
  receipt: DurableMonitorReceipt,
  contentRepaired: boolean,
): Promise<boolean> {
  let repaired = contentRepaired;
  const bytes = await readOptionalBytes(receipt.index_path);
  let content = bytes?.toString("utf8") ?? null;
  let records: JsonObject[];
  try {
    records = indexRecords(content);
  } catch (error) {
    const recovered = bytes ? repairedTruncatedTail(bytes, receipt) : null;
    if (recovered === null) throw error;
    await atomicWriteText(receipt.index_path, recovered);
    content = recovered;
    records = indexRecords(content);
    repaired = true;
  }
  const match = matchingIndexRecord(records, receipt.effect_id);
  if (match) {
    const metadata = requiredObject(
      match.quota_monitor_poll_commit,
      "index quota_monitor_poll_commit",
    );
    if (metadata.request_digest !== receipt.request_digest) {
      throw new EffectRuntimeRequestError(
        "quota monitor-poll effect identity is already bound to a different request",
        "effect_id_conflict",
      );
    }
    if (pythonJson(match) !== pythonJson(receipt.index_record)) {
      throw new EffectRuntimeRequestError(
        "quota monitor-poll index record conflicts with its transaction receipt",
        "artifact_conflict",
      );
    }
  } else {
    const currentBytes = bytes ?? Buffer.alloc(0);
    const currentDigest = bytes === null ? null : sha256Bytes(bytes);
    if (
      currentBytes.length !== receipt.expected_index_bytes ||
      currentDigest !== receipt.expected_index_digest
    ) {
      throw new EffectRuntimeRequestError(
        "quota monitor-poll index history conflicts with its transaction receipt",
        "artifact_conflict",
      );
    }
    const prefix = content ?? "";
    if (prefix && !prefix.endsWith("\n")) {
      await atomicWriteText(
        receipt.index_path,
        `${prefix}\n${JSON.stringify(receipt.index_record)}\n`,
      );
    } else {
      await appendJsonLine(receipt.index_path, receipt.index_record);
    }
    repaired = true;
  }
  return repaired;
}

async function ensureArtifacts(receipt: DurableMonitorReceipt): Promise<boolean> {
  return await ensureIndexArtifact(
    receipt,
    await ensureContentArtifacts(receipt),
  );
}

function result(
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

async function replayDurableReceipt(
  request: MonitorRequest,
  fingerprint: string,
  receiptPath: string,
  receipt: DurableMonitorReceipt,
): Promise<QuotaMonitorPollCommitResult> {
  const repaired = await ensureArtifacts(receipt);
  if (receipt.status === "prepared") {
    await rm(committedReceiptStagePath(receiptPath), { force: true });
  }
  const replayPayload: JsonObject = {
    ...receipt.payload,
    appended: repaired,
    replayed: request.turn_instance_id ? true : receipt.payload.replayed,
    todo_writeback: null,
    transaction_repaired: repaired,
    reason: repaired
      ? "quota monitor-poll commit repaired its durable transaction artifacts"
      : "replayed existing monitor poll event for the same effect identity",
  };
  return result(
    request,
    fingerprint,
    repaired ? "repaired" : "replayed",
    receipt.record,
    replayPayload,
    String(replayPayload.reason),
    await quotaMonitorPollIndexDigest(receipt.index_path),
    null,
    receipt.index_record,
  );
}

function conflictFields(
  request: MonitorRequest,
  receipt: MonitorReceipt,
): string[] {
  const recorded = receipt.status === "provider_pending"
    ? receipt.provider_plan
    : requiredObject(receipt.record.monitor_event, "receipt.record.monitor_event");
  const requested = request.observation;
  const recordedTodo = optionalString(recorded.todo_id, "recorded todo_id")?.trim() ?? null;
  const recordedTarget = optionalString(recorded.target_key, "recorded target_key")?.trim() ?? null;
  const recordedHash = optionalString(recorded.result_hash, "recorded result_hash")?.trim() ?? null;
  const conflicts: string[] = [];
  if (requested.todo_id !== recordedTodo) conflicts.push("todo_id");
  if (requested.target_key && requested.target_key !== recordedTarget) {
    conflicts.push("target_key");
  }
  if (requested.result_hash !== recordedHash) conflicts.push("result_hash");
  if (requested.material_change !== (recorded.material_change === true)) {
    conflicts.push("material_change");
  }
  const recordedProof = decodeTaskLeaseProof(recorded.lease_proof ?? jsonObject(recorded.todo_writeback)?.lease_proof);
  if ((requested.lease_proof?.idempotency_key ?? null) !== (recordedProof?.idempotency_key ?? null) ||
      (requested.lease_proof?.expected_version ?? null) !== (recordedProof?.expected_version ?? null)) {
    conflicts.push("lease_proof");
  }
  return conflicts;
}

function effectConflict(
  request: MonitorRequest,
  fingerprint: string,
  indexDigest: string | null,
  receipt: MonitorReceipt,
): QuotaMonitorPollCommitResult {
  return result(
    request,
    fingerprint,
    "conflict",
    null,
    { ok: false, appended: false },
    "quota monitor-poll effect identity is already bound to a different request",
    indexDigest,
    null,
    null,
    {
      reason_code: "effect_id_conflict",
      conflict_fields: conflictFields(request, receipt),
    },
  );
}

export async function evaluateQuotaMonitorPollCommit(
  value: unknown,
): Promise<QuotaMonitorPollCommitResult> {
  const request = requestObject(value);
  const fingerprint = requestDigest(request);
  if (request.phase === "event") {
    const record = buildRecord(request, admission(request));
    return result(
      request,
      fingerprint,
      "preview",
      record,
      { record },
      "quota monitor-poll event preview evaluated by TypeScript",
    );
  }
  const providerNeeded = Boolean(
    request.observation.todo_id || request.observation.target_key,
  );
  if (request.phase === "preflight" && !providerNeeded) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll preflight requires a Todo provider target",
    );
  }
  if (!request.execute) {
    // A provider may mutate the Todo registry, so previews must pass admission
    // before returning a provider plan.
    const allowed = admission(request);
    if (request.phase === "preflight") {
      const providerPlan = providerPlanFor(request);
      return result(
        request,
        fingerprint,
        "provider_required",
        null,
        { ok: true, dry_run: true, provider_required: true },
        "quota monitor-poll Todo provider is required",
        request.runtime_root
          ? await quotaMonitorPollIndexDigest(
            join(request.runtime_root, "goals", request.goal_id, "runs", "index.jsonl"),
          )
          : null,
        providerPlan,
      );
    }
    let effectiveRequest = request;
    if (providerNeeded) {
      if (!request.provider_receipt) {
        throw new EffectRuntimeRequestError(
          "quota monitor-poll Todo commit requires a provider receipt",
        );
      }
      const plan = providerPlanFor(request);
      effectiveRequest = requestWithProvider(
        request,
        plan,
        validatedProviderReceipt(request.provider_receipt, plan),
      );
    }
    const record = buildRecord(effectiveRequest, allowed);
    const runsDir = request.runtime_root
      ? join(request.runtime_root, "goals", request.goal_id, "runs")
      : null;
    const indexPath = runsDir ? join(runsDir, "index.jsonl") : "";
    const paths = runsDir
      ? await nextArtifactPaths(runsDir, effectiveRequest.generated_at, request.effect_id)
      : { jsonPath: "", markdownPath: "" };
    return result(
      effectiveRequest,
      fingerprint,
      "preview",
      record,
      payloadFor(
        effectiveRequest,
        record,
        paths.jsonPath,
        paths.markdownPath,
        indexPath,
        {
        appended: false,
        replayed: false,
        repaired: false,
        },
      ),
      "quota monitor-poll transaction preview evaluated by TypeScript",
      indexPath ? await quotaMonitorPollIndexDigest(indexPath) : null,
    );
  }

  if (!request.runtime_root) {
    throw new EffectRuntimeRequestError(
      "executing quota monitor-poll commit requires runtime_root",
    );
  }
  const runsDir = join(request.runtime_root, "goals", request.goal_id, "runs");
  const indexPath = join(runsDir, "index.jsonl");
  return await withFileMutationLock(indexPath, async () => {
    const receiptPath = transactionPath(runsDir, request.effect_id);
    const existing = await readReceipt(receiptPath);
    if (existing) {
      validateReceiptScope(existing, runsDir, indexPath);
      if (
        existing.effect_id !== request.effect_id ||
        existing.request_digest !== fingerprint
      ) {
        return effectConflict(
          request,
          fingerprint,
          await quotaMonitorPollIndexDigest(indexPath),
          existing,
        );
      }
      if (existing.status !== "provider_pending") {
        return await replayDurableReceipt(
          request,
          fingerprint,
          receiptPath,
          existing,
        );
      }
    }

    // A pending v1 receipt preserves the decision that admitted this effect.
    // Validate that historical basis, never the post-business-commit projection.
    // It only authorizes settlement; the provider still fences any new mutation.
    let admittedRequest = request;
    if (existing?.schema_version === MONITOR_PENDING_ADMISSION_SCHEMA) {
      const decision = existing.admitted_decision;
      if (decision.goal_id !== request.goal_id || decision.agent_id !== request.decision.agent_id) {
        throw new EffectRuntimeRequestError("pending Monitor admission goal/agent does not match request",
          "malformed_transaction_receipt");
      }
      admittedRequest = {...request, decision};
    }
    let allowed: Admission;
    try {
      allowed = admission(admittedRequest);
    } catch (error) {
      if (existing?.schema_version === QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA &&
          error instanceof EffectRuntimeRequestError && error.code === "monitor_poll_admission_rejected") {
        throw new EffectRuntimeRequestError(
          "legacy pending Monitor receipt has no frozen admission; current admission is unavailable; preserve the receipt for reconciliation",
          "legacy_monitor_admission_unavailable");
      }
      throw error;
    }

    const indexBytes = await readOptionalBytes(indexPath);
    const indexContent = indexBytes?.toString("utf8") ?? null;
    const currentDigest = indexBytes ? sha256Bytes(indexBytes) : null;
    if (!existing && request.expected_index_digest !== currentDigest) {
      return result(
        request,
        fingerprint,
        "conflict",
        null,
        { ok: false, appended: false },
        "quota run index compare-and-swap precondition failed",
        currentDigest,
        null,
        null,
        { reason_code: "index_digest_conflict" },
      );
    }
    const records = indexRecords(indexContent);
    if (existing?.status === "provider_pending" && matchingIndexRecord(records, request.effect_id)) {
      return result(
        request, fingerprint, "conflict", null, {ok: false, appended: false},
        "quota monitor-poll effect identity exists while its provider receipt is pending",
        currentDigest, null, null, {reason_code: "effect_id_conflict"},
      );
    }
    if (!existing && matchingIndexRecord(records, request.effect_id)) {
      return result(
        request,
        fingerprint,
        "conflict",
        null,
        { ok: false, appended: false },
        "quota monitor-poll effect identity exists without a transaction receipt",
        currentDigest,
        null,
        null,
        { reason_code: "effect_id_conflict" },
      );
    }

    if (request.phase === "preflight") {
      if (existing && !pendingIndexHistoryIntact(existing, indexBytes)) {
        return result(
          request,
          fingerprint,
          "conflict",
          null,
          { ok: false, appended: false },
          "quota run index changed after monitor Todo provider preflight",
          currentDigest,
          null,
          null,
          { reason_code: "index_digest_conflict" },
        );
      }
      const plan = existing
        ? providerPlanObject(existing.provider_plan)
        : providerPlanFor(request);
      const expectedPlan = providerPlanFor({
        ...request,
        generated_at: plan.generated_at,
      });
      if (pythonJson(plan) !== pythonJson(expectedPlan)) {
        throw new EffectRuntimeRequestError(
          "quota monitor-poll provider plan conflicts with its transaction receipt",
          "malformed_transaction_receipt",
        );
      }
      if (!existing) {
        const pending = {
          schema_version: MONITOR_PENDING_ADMISSION_SCHEMA,
          effect_id: request.effect_id,
          request_digest: fingerprint,
          status: "provider_pending",
          index_path: indexPath,
          expected_index_digest: currentDigest,
          expected_index_bytes: indexBytes?.length ?? 0,
          provider_plan: plan,
          admitted_decision: request.decision,
        } satisfies PendingMonitorReceipt;
        await atomicWriteJson(receiptPath, pending);
      }
      return result(
        request,
        fingerprint,
        "provider_required",
        null,
        { ok: true, dry_run: false, provider_required: true },
        "quota monitor-poll Todo provider is required",
        currentDigest,
        plan,
      );
    }

    let effectiveRequest = admittedRequest;
    let expectedDigest = currentDigest;
    let expectedBytes = indexBytes?.length ?? 0;
    if (providerNeeded) {
      if (!existing || existing.status !== "provider_pending") {
        throw new EffectRuntimeRequestError(
          "quota monitor-poll Todo commit requires a durable provider preflight",
        );
      }
      if (!request.provider_receipt) {
        throw new EffectRuntimeRequestError(
          "quota monitor-poll Todo commit requires a provider receipt",
        );
      }
      const plan = providerPlanObject(existing.provider_plan);
      const expectedPlan = providerPlanFor({
        ...request,
        generated_at: plan.generated_at,
      });
      if (pythonJson(plan) !== pythonJson(expectedPlan)) {
        throw new EffectRuntimeRequestError(
          "quota monitor-poll provider plan conflicts with its transaction receipt",
          "malformed_transaction_receipt",
        );
      }
      if (!pendingIndexHistoryIntact(existing, indexBytes)) {
        return result(
          request,
          fingerprint,
          "conflict",
          null,
          { ok: false, appended: false },
          "quota run index changed after monitor Todo provider preflight",
          currentDigest,
          null,
          null,
          { reason_code: "index_digest_conflict" },
        );
      }
      effectiveRequest = requestWithProvider(
        admittedRequest,
        plan,
        validatedProviderReceipt(request.provider_receipt, plan),
      );
      // The pending receipt freezes the admitted observation, not the entire
      // append-only run index. An unrelated run may have committed while the
      // provider wrote this exact effect. The new prepared WAL fences the
      // current index under the same lock after its historical prefix passes.
      expectedDigest = currentDigest;
      expectedBytes = indexBytes?.length ?? 0;
    } else if (existing?.status === "provider_pending") {
      return effectConflict(request, fingerprint, currentDigest, existing);
    }

    const record = buildRecord(effectiveRequest, allowed);
    const { jsonPath, markdownPath } = await nextArtifactPaths(
      runsDir,
      effectiveRequest.generated_at,
      request.effect_id,
    );
    const payload = payloadFor(
      effectiveRequest,
      record,
      jsonPath,
      markdownPath,
      indexPath,
      { appended: true, replayed: false, repaired: false },
    );
    const indexRecord = indexRecordFor(
      effectiveRequest,
      record,
      jsonPath,
      markdownPath,
      fingerprint,
    );
    const prepared = {
      schema_version: QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
      effect_id: request.effect_id,
      request_digest: fingerprint,
      status: "prepared",
      json_path: jsonPath,
      markdown_path: markdownPath,
      index_path: indexPath,
      expected_index_digest: expectedDigest,
      expected_index_bytes: expectedBytes,
      record,
      index_record: indexRecord,
      markdown: monitorMarkdown(record),
      payload,
    } satisfies DurableMonitorReceipt;
    const [, contentRepaired] = await Promise.all([
      atomicWriteJson(receiptPath, prepared),
      ensureContentArtifacts(prepared),
    ]);
    // The conservative prepared receipt is the durable write-ahead recovery
    // authority; the public index remains the commit proof. Keeping the WAL in
    // that state avoids a second full-receipt fsync without ever publishing a
    // committed receipt before the index append succeeds.
    await ensureIndexArtifact(prepared, contentRepaired);
    return result(
      effectiveRequest,
      fingerprint,
      "written",
      record,
      payload,
      "quota monitor-poll transaction committed by TypeScript",
      await quotaMonitorPollIndexDigest(indexPath),
      null,
      indexRecord,
    );
  });
}
