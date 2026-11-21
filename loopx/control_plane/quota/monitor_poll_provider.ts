/** Todo provider dispatch plans, exact receipt validation, no-effect proofs, and conflicts. */
import { decodeTaskLeaseProof } from "../coordination/task_lease_proof.ts";
import { monitorPollRequestHash } from "../coordination/todo_monitor_poll.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import type { JsonObject } from "../effect_program.ts";
import {
  jsonObject,
  requireBoolean,
  requireInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringArray,
} from "../runtime_decode.ts";
import {
  monitorSuccessorIntent,
  monitorSuccessorPriorities,
  monitorSuccessorRoute,
} from "../scheduler/monitor_successor.ts";
import { planTodoCreationPriority, readTodoPriority } from "../todos/priority.ts";
import { normalizeTodoCapabilities } from "../todos/work_requirements.ts";
import {
  candidateMatches,
  LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA,
  MONITOR_TODO_PROVIDER_PLAN_SCHEMA,
  MONITOR_TODO_WRITEBACK_SCHEMA,
  monitorProjectionDelivery,
  normalizedTodoId,
  objectArray,
  optionalTrimmed,
  pythonJson,
  sharedObservationValues,
  type MonitorProviderPlan,
  type MonitorReceipt,
  type MonitorRequest,
} from "./monitor_poll_codec.ts";

export function providerPlanFor(
  request: MonitorRequest,
  gateGuard = true,
  legacyBatchVersion: 1 | null = null,
): MonitorProviderPlan {
  const resultHash = request.observation.result_hash;
  if (!resultHash) throw new EffectRuntimeRequestError("monitor todo writeback requires --result-hash");
  return {
    schema_version: request.observation.lease_proof
      ? LEASED_MONITOR_TODO_PROVIDER_PLAN_SCHEMA
      : MONITOR_TODO_PROVIDER_PLAN_SCHEMA,
    ...(request.observation.lease_proof ? { lease_proof: request.observation.lease_proof } : {}),
    ...(gateGuard ? { gate_scope_guard: true } : {}),
    ...(legacyBatchVersion === 1 ? { legacy_batch_version: 1 as const } : {}),
    monitor_effect_id: request.effect_id,
    goal_id: request.goal_id,
    generated_at: request.generated_at,
    execute: request.execute,
    result_hash: resultHash,
    ...(request.observation.material_change_scope == null ? {} : {
      material_change_scope: request.observation.material_change_scope,
    }),
    ...monitorSuccessorPriorities(request.observation, true),
    ...sharedObservationValues(request.observation),
    agent_id: request.observation.actor_agent_id ?? request.decision.agent_id,
  } as MonitorProviderPlan;
}

/** Upgrade uncommitted historical Monitor dispatch without rewriting its WAL.
 * Existing business receipts replay the same intended-effect identity first. */
export function providerDispatchPlan(
  plan: MonitorProviderPlan,
  request: MonitorRequest,
): MonitorProviderPlan {
  const candidate = request.decision.due_monitor_candidates.find((item) =>
    candidateMatches(item, request.observation),
  );
  return {
    ...plan,
    gate_scope_guard: true,
    ...(typeof candidate?.resume_when === "string" ? { admitted_resume_when: candidate.resume_when } : {}),
  };
}

function requireProviderMatch(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) throw new EffectRuntimeRequestError(`${label} must match provider plan`);
}

function matchedOptional(
  receipt: JsonObject,
  plan: MonitorProviderPlan,
  key: "target_key" | "next_due_at" | "cadence",
): string | null {
  const label = `provider_receipt.${key}`;
  const value = optionalTrimmed(receipt[key], label);
  if (plan[key]) requireProviderMatch(value, plan[key], label);
  return value;
}

function requirePairMatch(
  receipt: JsonObject,
  nextTodo: JsonObject,
  field: string,
  expected: unknown,
  roleLabel: string,
): void {
  requireProviderMatch(receipt[field], expected, `${roleLabel} successor ${field}`);
  requireProviderMatch(nextTodo[field], expected, `${roleLabel} next_todo ${field}`);
}

function requireProviderCapabilityMatch(
  actual: unknown,
  expected: readonly string[],
  label: string,
): void {
  if (pythonJson(normalizeTodoCapabilities(actual, label)) !== pythonJson(normalizeTodoCapabilities(expected, label))) {
    throw new EffectRuntimeRequestError(`${label} must match provider plan`);
  }
}

function requiredProviderTodoId(value: unknown, label: string): string {
  const todoId = normalizedTodoId(value, label);
  if (!todoId) throw new EffectRuntimeRequestError(`${label} is required`);
  return todoId;
}

function requireProviderTodoText(value: unknown, expected: string, label: string): void {
  requireProviderMatch(requiredString(value, label).trim().replace(/\s+/g, " "), expected.trim().replace(/\s+/g, " "), label);
}

function requireProviderSuccessorPriority(
  receipt: JsonObject,
  nextTodo: JsonObject,
  plan: MonitorProviderPlan,
  role: "agent" | "user",
): void {
  const text = String(plan[`next_${role}_todo`]);
  const key = `next_${role}_priority`;
  const explicit = Object.hasOwn(plan, key);
  const taskClass = role === "agent" ? "advancement_task" : plan.next_user_task_class;
  const expected = planTodoCreationPriority({ text, task_class: taskClass, ...(explicit ? { priority: plan[key] } : {}) });
  if (!explicit && nextTodo.todo === text) return; // Historical provider plan and original receipt.
  requireProviderTodoText(nextTodo.todo, String(expected.text), `${role} next_todo text`);
  requireProviderMatch(readTodoPriority(receipt), expected.priority, `${role} successor priority`);
  requireProviderMatch(readTodoPriority(nextTodo), expected.priority, `${role} next_todo priority`);
}

function requireCanonicalSuccessorRoute(value: JsonObject, expected: JsonObject, label: string): void {
  for (const field of ["task_repository", "continuation_policy", "target_key", "claimed_by"] as const) {
    requireProviderMatch(optionalTrimmed(value[field], `${label} ${field}`), expected[field], `${label} ${field}`);
  }
  requireProviderCapabilityMatch(
    value.required_capabilities ?? [],
    requireStringArray(expected.required_capabilities, "expected capabilities"),
    `${label} required_capabilities`,
  );
}

function validateSuccessorReceipts(
  receipts: readonly JsonObject[],
  nextTodos: readonly JsonObject[],
  plan: MonitorProviderPlan,
  todoId: string,
): void {
  const expectedCount = plan.material_change ? Number(Boolean(plan.next_agent_todo)) + Number(Boolean(plan.next_user_todo)) : 0;
  if (receipts.length !== expectedCount) {
    throw new EffectRuntimeRequestError("provider_receipt.successor_receipts must match the requested successor routes");
  }
  if (nextTodos.length !== receipts.length) {
    throw new EffectRuntimeRequestError("provider_receipt.next_todos must match successor_receipts");
  }
  let offset = 0;
  if (plan.material_change && plan.next_agent_todo) {
    const canonicalRoute = monitorSuccessorRoute(monitorSuccessorIntent(plan), todoId, plan.result_hash);
    const receipt = receipts[offset];
    const nextTodo = nextTodos[offset++];
    requirePairMatch(receipt, nextTodo, "role", "agent", "agent");
    requirePairMatch(receipt, nextTodo, "task_class", "advancement_task", "agent");
    requirePairMatch(receipt, nextTodo, "action_kind", canonicalRoute.action_kind, "agent");
    requirePairMatch(receipt, nextTodo, "unblocks_todo_id", todoId, "agent");
    requireProviderSuccessorPriority(receipt, nextTodo, plan, "agent");
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
    requirePairMatch(receipt, nextTodo, "role", "user", "user");
    requirePairMatch(receipt, nextTodo, "task_class", plan.next_user_task_class, "user");
    requireProviderSuccessorPriority(receipt, nextTodo, plan, "user");
    requireProviderMatch(
      requiredProviderTodoId(receipt.todo_id, "user successor todo_id"),
      requiredProviderTodoId(nextTodo.todo_id, "user next_todo todo_id"),
      "user successor todo_id",
    );
    if (plan.next_user_task_class === "user_gate") {
      requireProviderMatch(receipt.action_kind, "gate", "user successor action_kind");
      requireProviderMatch(receipt.unblocks_todo_id, todoId, "user successor unblocks_todo_id");
    }
  }
}

export function validatedProviderReceipt(value: unknown, plan: MonitorProviderPlan): JsonObject {
  const receipt = requiredObject(value, "provider_receipt");
  if (receipt.schema_version !== MONITOR_TODO_WRITEBACK_SCHEMA) {
    throw new EffectRuntimeRequestError("Monitor Todo provider receipt schema mismatch");
  }
  requireProviderMatch(receipt.monitor_effect_id, plan.monitor_effect_id, "provider_receipt.monitor_effect_id");
  requireProviderMatch(receipt.goal_id, plan.goal_id, "provider_receipt.goal_id");
  const todoId = normalizedTodoId(receipt.todo_id, "provider_receipt.todo_id");
  if (!todoId) throw new EffectRuntimeRequestError("provider_receipt.todo_id is required");
  if (plan.todo_id) requireProviderMatch(todoId, plan.todo_id, "provider_receipt.todo_id");
  const targetKey = matchedOptional(receipt, plan, "target_key");
  const resultHash = requiredString(receipt.result_hash, "provider_receipt.result_hash").trim();
  requireProviderMatch(resultHash, plan.result_hash, "provider_receipt.result_hash");
  const materialChange = requireBoolean(receipt.material_change, "provider_receipt.material_change");
  requireProviderMatch(materialChange, plan.material_change, "provider_receipt.material_change");
  const materialChangeScope = optionalTrimmed(receipt.material_change_scope, "provider_receipt.material_change_scope");
  requireProviderMatch(materialChangeScope, plan.material_change_scope ?? null, "provider_receipt.material_change_scope");
  const dryRun = requireBoolean(receipt.dry_run, "provider_receipt.dry_run");
  requireProviderMatch(dryRun, !plan.execute, "provider_receipt.dry_run");
  const generation = requireInteger(receipt.material_change_generation, "provider_receipt.material_change_generation");
  const noChange = requireInteger(receipt.consecutive_no_change, "provider_receipt.consecutive_no_change");
  if (generation < 0 || noChange < 0) {
    throw new EffectRuntimeRequestError("provider receipt monitor counters cannot be negative");
  }
  const lastCheckedAt = requiredString(receipt.last_checked_at, "provider_receipt.last_checked_at").trim();
  requireProviderMatch(lastCheckedAt, plan.generated_at, "provider_receipt.last_checked_at");
  const nextDueAt = matchedOptional(receipt, plan, "next_due_at");
  const cadence = matchedOptional(receipt, plan, "cadence");
  const nextTodos = objectArray(receipt.next_todos, "provider_receipt.next_todos");
  const successors = objectArray(receipt.successor_receipts, "provider_receipt.successor_receipts");
  validateSuccessorReceipts(successors, nextTodos, plan, todoId);
  const proof = decodeTaskLeaseProof(receipt.lease_proof);
  requireProviderMatch(proof?.idempotency_key ?? null, plan.lease_proof?.idempotency_key ?? null, "provider_receipt.lease_proof.idempotency_key");
  requireProviderMatch(proof?.expected_version ?? null, plan.lease_proof?.expected_version ?? null, "provider_receipt.lease_proof.expected_version");
  return {
    schema_version: MONITOR_TODO_WRITEBACK_SCHEMA,
    ...(proof ? { lease_proof: proof } : {}),
    dry_run: dryRun,
    goal_id: plan.goal_id,
    todo_id: todoId,
    target_key: targetKey,
    result_hash: resultHash,
    material_change: materialChange,
    ...(materialChangeScope === null ? {} : { material_change_scope: materialChangeScope }),
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

export function requestWithProvider(
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
      target_key: optionalTrimmed(providerReceipt.target_key, "provider_receipt.target_key"),
      result_hash: String(providerReceipt.result_hash),
    },
    provider_receipt: providerReceipt,
  };
}

export function conflictFields(request: MonitorRequest, receipt: MonitorReceipt): string[] {
  const recorded = receipt.status === "provider_pending"
    ? receipt.provider_plan
    : requiredObject(receipt.record.monitor_event, "receipt.record.monitor_event");
  const requested = request.observation;
  const conflicts: string[] = [];
  if (requested.todo_id !== optionalTrimmed(recorded.todo_id, "recorded todo_id")) conflicts.push("todo_id");
  const recordedTarget = optionalTrimmed(recorded.target_key, "recorded target_key");
  if (requested.target_key && requested.target_key !== recordedTarget) conflicts.push("target_key");
  if (requested.result_hash !== optionalTrimmed(recorded.result_hash, "recorded result_hash")) conflicts.push("result_hash");
  if (requested.material_change !== (recorded.material_change === true)) conflicts.push("material_change");
  if ((requested.material_change_scope ?? null) !== optionalTrimmed(recorded.material_change_scope, "recorded material_change_scope")) {
    conflicts.push("material_change_scope");
  }
  const recordedProof = decodeTaskLeaseProof(recorded.lease_proof ?? jsonObject(recorded.todo_writeback)?.lease_proof);
  if (
    (requested.lease_proof?.idempotency_key ?? null) !== (recordedProof?.idempotency_key ?? null) ||
    (requested.lease_proof?.expected_version ?? null) !== (recordedProof?.expected_version ?? null)
  ) conflicts.push("lease_proof");
  return conflicts;
}

export function validateNoEffect(receipt: JsonObject | null, plan: MonitorProviderPlan): void {
  const proof = receipt?.no_effect as JsonObject | undefined;
  const observation = Object.fromEntries([
    "todo_id", "target_key", "result_hash", "material_change", "generated_at", "cadence", "next_due_at", "reason_summary",
  ].map((key) => [key, plan[key]]));
  if (Object.hasOwn(plan, "material_change_scope")) observation.material_change_scope = plan.material_change_scope ?? null;
  const intent = Object.fromEntries([
    "next_agent_todo", "next_action_kind", "next_task_repository", "next_required_capabilities",
    "next_continuation_policy", "next_target_key", "next_claimed_by", "next_user_todo", "next_user_task_class",
  ].map((key) => [key, plan[key]]));
  Object.assign(intent, monitorSuccessorPriorities(plan));
  const hash = monitorPollRequestHash({
    goal_id: plan.goal_id,
    actor_agent_id: plan.agent_id,
    dry_run: !plan.execute,
    observation,
    intent,
    lease_proof: plan.lease_proof,
  });
  if (
    receipt?.schema_version !== "loopx_coordination_monitor_poll_result_v0" ||
    receipt.status !== "failed" ||
    receipt.changed !== false ||
    receipt.reason_code !== "monitor_poll_rejected" ||
    !["file_v0", "sqlite_v0", "postgresql_v0"].includes(String(receipt.source_authority)) ||
    receipt.decision_read_from_provider !== true ||
    receipt.legacy_fallback_used !== false ||
    proof?.schema_version !== "monitor_poll_no_effect_v0" ||
    proof.goal_id !== plan.goal_id ||
    proof.operation_id !== plan.monitor_effect_id ||
    proof.request_sha256 !== hash
  ) {
    throw new EffectRuntimeRequestError(
      "provider rejection does not prove this pending Monitor request had no effect",
      "monitor_poll_no_effect_unproven",
    );
  }
}
