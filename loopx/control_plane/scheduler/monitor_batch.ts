/** Shared Monitor observation and successor planner. The caller owns persistence and admission. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256, requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {normalizeRegisteredTodoAgents, normalizeTodoAgent, stripPythonWhitespace} from "../coordination/todo_agents.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA} from "../coordination/coordination_state_contract.ts";
import {planCoordinationTodoCreate} from "../coordination/todo_create.ts";
import {projectTodoGateScopes} from "../todos/decision_scope.ts";
import {planMonitorMetadata, TODO_MONITOR_METADATA_REQUEST_SCHEMA} from "../todos/monitor_metadata.ts";
import {planMonitorSuccessor, selectMonitorTodo, MONITOR_SUCCESSOR_REQUEST_SCHEMA} from "./monitor_successor.ts";
import {optionalNonEmptyString, requireBoolean, requireJsonObject} from "../runtime_decode.ts";
import {planTodoAuthoringScope, TODO_AUTHORING_SCOPE_REQUEST_SCHEMA} from "../todos/authoring_scope.ts";
import type {TaskLeaseProof} from "../coordination/task_lease_proof.ts";

export const MONITOR_BATCH_PLAN_REQUEST_SCHEMA = "loopx_monitor_batch_plan_request_v0";
export const MONITOR_BATCH_PLAN_RESULT_SCHEMA = "loopx_monitor_batch_plan_result_v0";
export const MONITOR_BATCH_RECEIPT_SCHEMA = "loopx_monitor_batch_receipt_v0";

export interface MonitorBatchInput {
  goal_id: string;
  operation_id: string;
  actor_agent_id: string | null;
  registered_agents: readonly string[];
  dry_run: boolean;
  observation: JsonObject;
  intent: JsonObject;
  gate_scope_guard?: boolean;
  todos: readonly JsonObject[];
  read_model_schema?: string;
  lease_proof?: TaskLeaseProof | null;
  /** Canonical authority supplies its existing lease and mutation admission. */
  admit_monitor?: (monitor: JsonObject) => void;
}

export function monitorPollRequestHash(input: Pick<MonitorBatchInput,
  "goal_id" | "observation" | "intent" | "actor_agent_id" | "dry_run" | "lease_proof">): string {
  return canonicalAuthoritySha256({goal_id: input.goal_id, observation: input.observation,
    intent: input.intent, actor_agent_id: input.actor_agent_id, dry_run: input.dry_run,
    ...(input.lease_proof ? {lease_proof: input.lease_proof} : {})});
}

/** Wire field vocabulary shared by canonical and legacy Monitor requests. */
export function normalizeMonitorPollFields(observation: unknown, intent: unknown):
    Pick<MonitorBatchInput, "observation" | "intent"> {
  const fields = {observation: canonicalAuthorityObject(observation, "Monitor observation"),
    intent: canonicalAuthorityObject(intent, "Monitor successor intent")};
  const observationFields = new Set(["todo_id", "target_key", "generated_at", "result_hash", "material_change", "cadence", "next_due_at", "reason_summary"]);
  for (const key of Object.keys(fields.observation)) if (!observationFields.has(key)) throw new EffectRuntimeRequestError(`unsupported Monitor observation field: ${key}`);
  const intentFields = new Set(["next_agent_todo", "next_action_kind", "next_task_repository", "next_required_capabilities",
    "next_continuation_policy", "next_target_key", "next_claimed_by", "next_user_todo", "next_user_task_class"]);
  for (const key of Object.keys(fields.intent)) if (!intentFields.has(key)) throw new EffectRuntimeRequestError(`unsupported Monitor successor field: ${key}`);
  return fields;
}

function planWriteback(input: MonitorBatchInput,
  claimPolicy: "actor_owned" | "registered_peer_handoff" = "actor_owned") {
  const todos = input.todos;
  const observation = input.observation;
  const monitor = selectMonitorTodo(todos,
    optionalNonEmptyString(observation.todo_id, "todo_id"), optionalNonEmptyString(observation.target_key, "target_key"));
  const actor = input.actor_agent_id;
  if (input.gate_scope_guard) {
    // The caller fences this exact snapshot together with its state write.
    const scopes = projectTodoGateScopes({agent_id: actor, items: [monitor],
      gates: todos.filter(todo => todo.role === "user")
        .map(todo => ({...todo, is_gate: todo.task_class === "user_gate"}))});
    if ((scopes.items as JsonObject[])[0].state === "blocked") {
      throw new EffectRuntimeRequestError("Monitor observation is blocked by current User gate dependencies");
    }
  }
  input.admit_monitor?.(monitor);
  const successorPlan = planMonitorSuccessor({schema_version: MONITOR_SUCCESSOR_REQUEST_SCHEMA,
    todo_id: monitor.todo_id, result_hash: observation.result_hash, source_task_repository: monitor.task_repository ?? null,
    intent: {...input.intent, material_change: observation.material_change}});
  const intent = canonicalAuthorityObject(successorPlan.intent, "Monitor successor intent");
  const route = canonicalAuthorityObject(successorPlan.agent_route, "Monitor successor route");
  const monitorPlan = planMonitorMetadata({schema_version: TODO_MONITOR_METADATA_REQUEST_SCHEMA,
    existing: monitor, role: "agent", task_class: "continuous_monitor", enforce_boundedness: false,
    observation: {...observation, monitor_effect_id: input.operation_id}});
  const transition = canonicalAuthorityObject(monitorPlan.transition, "Monitor transition");
  if ((intent.next_agent_todo || intent.next_user_todo) && transition.material_change_applied !== true) {
    throw new EffectRuntimeRequestError("successor authoring requires a new material-change generation; poll without successor options for unchanged evidence");
  }
  const metadata = canonicalAuthorityObject(monitorPlan.metadata, "Monitor metadata");
  // Generation is an integer in the persisted Todo contract. The older
  // observation tokens remain strings (including "0" and "false"); retain
  // their existing wire types so permanent Markdown projection is lossless.
  if (metadata.material_change_generation != null) metadata.material_change_generation = Number(metadata.material_change_generation);
  const updated: JsonObject = {...monitor, last_actor_agent_id: actor, updated_at: observation.generated_at};
  for (const [key, value] of Object.entries(metadata)) {
    if (value === null) delete updated[key]; else updated[key] = value;
  }
  const reason = optionalNonEmptyString(observation.reason_summary, "reason_summary");
  if (reason) updated.reason = reason;
  const nextTodos: JsonObject[] = [];
  const plannedTodos = new Map(todos.map(todo => [String(todo.todo_id), todo]));
  const mutations: {kind: "todo_upsert"; todo: JsonObject}[] = [{kind: "todo_upsert", todo: updated}];
  for (const role of ["agent", "user"] as const) {
    const text = intent[role === "agent" ? "next_agent_todo" : "next_user_todo"];
    if (!text) continue;
    const id = `todo_${canonicalAuthoritySha256({monitor: monitor.todo_id,
      generation: transition.material_change_generation, role}).slice(0, 24)}`;
    const todo: JsonObject = {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: id, role, text,
      status: "open", done: false, archive_state: "active",
      task_class: role === "agent" ? "advancement_task" : intent.next_user_task_class};
    if (role === "agent") {
      Object.assign(todo, Object.fromEntries(Object.entries(route).filter(([, value]) => value !== null)),
        {unblocks_todo_id: monitor.todo_id});
    } else {
      // Reuse public authoring scope: an actor-bound gate, never an inferred
      // all-agent/global gate. User actions retain their actor binding too.
      const scope = planTodoAuthoringScope({schema_version: TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
        command: "create", role, todo: {}, goal_id: input.goal_id, registered_agents: input.registered_agents,
        intent: {task_class: todo.task_class, actor_agent_id: actor}});
      for (const key of ["bound_agent", "blocks_agent", "goal_bound", "global_gate"]) {
        if (scope[key] != null && scope[key] !== false) todo[key] = scope[key];
      }
      if (todo.task_class === "user_gate") Object.assign(todo, {action_kind: "gate", unblocks_todo_id: monitor.todo_id});
    }
    const created = planCoordinationTodoCreate({goal_id: input.goal_id, operation_id: input.operation_id,
      actor_agent_id: actor, registered_agents: input.registered_agents, dry_run: input.dry_run,
      now: new Date(String(observation.generated_at)), todo}, plannedTodos,
      input.read_model_schema ?? TODO_DOMAIN_READ_RECORD_SCHEMA, "role_text", claimPolicy);
    if (created.status !== "planned" && created.status !== "no_change") throw new EffectRuntimeRequestError(String(created.reason));
    const record = canonicalAuthorityObject(created.todo, "successor Todo");
    nextTodos.push({...record, todo: record.text, ok: true, dry_run: input.dry_run});
    if (created.status === "planned") mutations.push({kind: "todo_upsert", todo: record});
    plannedTodos.set(String(record.todo_id), record);
  }
  const receiptFields = ["todo_id", "role", "task_class", "action_kind", "task_repository",
    "continuation_policy", "required_capabilities", "claimed_by", "unblocks_todo_id", "target_key"];
  const writeback: JsonObject = {schema_version: "monitor_poll_todo_writeback_v0", dry_run: input.dry_run,
    goal_id: input.goal_id, todo_id: monitor.todo_id, monitor_effect_id: input.operation_id,
    target_key: transition.target_key || null, result_hash: observation.result_hash,
    material_change: observation.material_change, material_change_generation: transition.material_change_generation,
    consecutive_no_change: transition.consecutive_no_change, last_checked_at: observation.generated_at,
    next_due_at: transition.next_due_at ?? null, cadence: transition.cadence || null,
    todo_update: {ok: true, todo_id: monitor.todo_id, monitor_poll_transition: transition},
    ...(input.lease_proof ? {lease_proof: {...input.lease_proof}} : {}),
    next_todos: nextTodos, successor_receipts: nextTodos.map(todo => Object.fromEntries(
      receiptFields.filter(key => todo[key] != null).map(key => [key, todo[key]]))), provider_replayed: false};
  return {mutations, writeback};
}

function normalizeBatchInput(raw: MonitorBatchInput): MonitorBatchInput {
  return {
    ...raw,
    goal_id: requireAuthorityStoreId(raw.goal_id, "goal id"),
    operation_id: requireAuthorityStoreId(raw.operation_id, "operation id"),
    actor_agent_id: raw.actor_agent_id == null ? null : normalizeTodoAgent(raw.actor_agent_id, "actor_agent_id"),
    registered_agents: normalizeRegisteredTodoAgents(raw.registered_agents),
    dry_run: requireBoolean(raw.dry_run, "dry_run"),
    gate_scope_guard: raw.gate_scope_guard == null ? false : requireBoolean(raw.gate_scope_guard, "gate_scope_guard"),
    ...normalizeMonitorPollFields(raw.observation, raw.intent),
    todos: raw.todos,
  };
}

/** Pure fresh batch planning. Canonical and legacy callers own their receipts. */
export function planMonitorBatch(raw: MonitorBatchInput) {
  const input = normalizeBatchInput(raw);
  const todos = input.todos.map(todo => canonicalAuthorityObject(todo, "Monitor Todo snapshot item"));
  return planWriteback({...input, todos});
}

/** Public legacy adapter endpoint; the caller saves receipt and mutations in
 * the same atomic state-file replacement. */
export function planLegacyMonitorBatch(value: unknown): JsonObject {
  try {
    return planLegacyMonitorBatchRequest(value);
  } catch (error) {
    if (error instanceof AuthorityStoreProtocolError) {
      throw new EffectRuntimeRequestError(error.message);
    }
    throw error;
  }
}

function planLegacyMonitorBatchRequest(value: unknown): JsonObject {
  const request = requireJsonObject(value, "legacy Monitor batch request");
  if (request.schema_version !== MONITOR_BATCH_PLAN_REQUEST_SCHEMA || !Array.isArray(request.todos)) {
    throw new EffectRuntimeRequestError("legacy Monitor batch request schema mismatch");
  }
  const input = normalizeBatchInput(request as unknown as MonitorBatchInput);
  const hash = monitorPollRequestHash(input);
  if (request.previous_receipt != null) {
    const previous = canonicalAuthorityObject(request.previous_receipt, "Monitor batch receipt");
    if (previous.schema_version !== MONITOR_BATCH_RECEIPT_SCHEMA || previous.goal_id !== input.goal_id ||
        previous.operation_id !== input.operation_id || previous.request_sha256 !== hash) {
      throw new EffectRuntimeRequestError("monitor effect identity is already bound: Monitor batch receipt identity or original request mismatch");
    }
    const writeback = canonicalAuthorityObject(previous.writeback, "Monitor batch receipt writeback");
    const expectedRoles = (["agent", "user"] as const).filter(role => {
      const successor = input.intent[role === "agent" ? "next_agent_todo" : "next_user_todo"];
      return typeof successor === "string" && stripPythonWhitespace(successor).length > 0;
    });
    if (writeback.schema_version !== "monitor_poll_todo_writeback_v0" ||
        writeback.goal_id !== input.goal_id || writeback.monitor_effect_id !== input.operation_id ||
        (input.observation.todo_id != null && writeback.todo_id !== input.observation.todo_id) ||
        writeback.result_hash !== input.observation.result_hash ||
        writeback.material_change !== input.observation.material_change ||
        writeback.last_checked_at !== input.observation.generated_at ||
        writeback.dry_run !== input.dry_run ||
        !Array.isArray(writeback.next_todos) || !Array.isArray(writeback.successor_receipts) ||
        writeback.next_todos.length !== writeback.successor_receipts.length ||
        writeback.next_todos.length !== expectedRoles.length) {
      throw new EffectRuntimeRequestError("Monitor batch receipt writeback is malformed");
    }
    const successorIds = new Set<string>();
    for (const [index, todo] of writeback.next_todos.entries()) {
      const item = canonicalAuthorityObject(todo, "Monitor successor receipt");
      const summary = canonicalAuthorityObject(writeback.successor_receipts[index], "Monitor successor summary");
      if (typeof item.todo_id !== "string" || !item.todo_id.startsWith("todo_") ||
          successorIds.has(item.todo_id) || item.todo_id !== summary.todo_id ||
          item.role !== expectedRoles[index] || summary.role !== expectedRoles[index]) {
        throw new EffectRuntimeRequestError("Monitor batch receipt successor identity is malformed");
      }
      successorIds.add(item.todo_id);
    }
    return {schema_version: MONITOR_BATCH_PLAN_RESULT_SCHEMA, mutations: [],
      writeback: {...writeback, provider_replayed: true}, receipt: previous, replayed: true};
  }
  if (request.legacy_batch_version !== 1) {
    throw new EffectRuntimeRequestError("legacy Monitor recovery needs reconciliation: preserve the pending receipt and inspect the original Monitor and successor Todos before retrying; frozen plan predates atomic batch receipts", "legacy_monitor_recovery_required");
  }
  const todos = input.todos.map(todo => canonicalAuthorityObject(todo, "Monitor Todo snapshot item"));
  if (todos.some(todo => todo.monitor_effect_id === input.operation_id)) {
    throw new EffectRuntimeRequestError("Monitor effect already applied without its batch receipt; preserve the pending receipt and reconcile the original successor Todos", "legacy_monitor_recovery_required");
  }
  // Legacy Monitor explicitly permits routing a successor to a registered peer.
  // Canonical Monitor and ordinary Todo create keep their actor-owned claim rule.
  const planned = planWriteback({...input, todos}, "registered_peer_handoff");
  const receipt = {schema_version: MONITOR_BATCH_RECEIPT_SCHEMA, goal_id: input.goal_id,
    operation_id: input.operation_id, request_sha256: hash, writeback: planned.writeback};
  return {schema_version: MONITOR_BATCH_PLAN_RESULT_SCHEMA, mutations: planned.mutations,
    writeback: planned.writeback, receipt, replayed: false};
}
