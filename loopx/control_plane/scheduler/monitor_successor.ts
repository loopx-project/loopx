/** Monitor follow-up intent, shared by preflight, legacy writeback and receipt
 * verification. This read-only plan is not authorization or a commit receipt. */
import { createHash } from "node:crypto";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { optionalNonEmptyString, requireBoolean, requireJsonObject } from "../runtime_decode.ts";
import { normalizeTodoAgent, stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { normalizeTodoRepository, normalizeTodoCapabilities } from "../todos/work_requirements.ts";
import {planTodoCreationPriority} from "../todos/priority.ts";

export const MONITOR_SUCCESSOR_REQUEST_SCHEMA = "loopx_monitor_successor_plan_request_v0";
export const MONITOR_SUCCESSOR_RESULT_SCHEMA = "loopx_monitor_successor_plan_result_v0";

/** Select from the caller's complete snapshot, never from a compact lane. */
export function selectMonitorTodo(items: readonly JsonObject[], todoId: string | null,
  targetKey: string | null): JsonObject {
  if (!todoId && !targetKey) throw new EffectRuntimeRequestError("monitor todo writeback requires --todo-id or --target-key");
  // A completed historical watch must not shadow its active replacement.
  // Explicit IDs still resolve first so an inactive target gets a rejection.
  const matches = items.filter(item => todoId ? item.todo_id === todoId :
    item.target_key === targetKey && item.status !== "done" && item.archive_state !== "archive");
  if (matches.length !== 1) throw new EffectRuntimeRequestError(
    matches.length ? "monitor target matched multiple todos; pass --todo-id" : "monitor todo target was not found");
  const item = matches[0]!;
  if (targetKey && item.target_key && item.target_key !== targetKey) {
    throw new EffectRuntimeRequestError(`monitor todo target_key resolves to '${item.target_key}', not '${targetKey}'`);
  }
  if (item.role !== "agent" || item.task_class !== "continuous_monitor") {
    throw new EffectRuntimeRequestError("monitor-poll todo writeback target must be task_class=continuous_monitor");
  }
  if (item.status === "done" || item.archive_state === "archive") {
    throw new EffectRuntimeRequestError("monitor-poll requires an active, unfinished Monitor");
  }
  return item;
}

export function selectMonitorTodoRequest(value: unknown): JsonObject {
  const request = requireJsonObject(value, "monitor target selection");
  if (request.schema_version !== "loopx_monitor_target_request_v0" || !Array.isArray(request.items)) {
    throw new EffectRuntimeRequestError("monitor target selection schema mismatch");
  }
  return {schema_version: "loopx_monitor_target_result_v0", todo: selectMonitorTodo(
    request.items.map(item => requireJsonObject(item, "monitor target item")),
    text(request.todo_id, "todo_id"), text(request.target_key, "target_key"))};
}

function text(value: unknown, field: string): string | null {
  const raw = optionalNonEmptyString(value, field);
  return raw === null ? null : stripPythonWhitespace(raw) || null;
}

// Shared with public Todo metadata updates; no second route codec here.

export interface MonitorSuccessorIntent extends JsonObject {
  next_agent_todo: string | null;
  next_action_kind: string | null;
  next_task_repository: string | null;
  next_required_capabilities: string[];
  next_continuation_policy: string | null;
  next_target_key: string | null;
  next_claimed_by: string | null;
  next_user_todo: string | null;
  next_user_task_class: "user_action" | "user_gate" | null;
}

/** Optional authoring fields stay absent in historical observation identities. */
export function monitorSuccessorPriorities(input: JsonObject, defaultOmitted = false): JsonObject {
  const priorities: JsonObject = {};
  for (const [role, todo] of [["agent", text(input.next_agent_todo, "next_agent_todo")], ["user", text(input.next_user_todo, "next_user_todo")]] as const) {
    const key = `next_${role}_priority`;
    const supplied = Object.hasOwn(input, key);
    if (!supplied && (!defaultOmitted || !todo)) continue;
    if (!todo) throw new EffectRuntimeRequestError(`--next-${role}-priority requires --next-${role}-todo`);
    // The Todo owner validates values and conflicting legacy prefixes.
    const taskClass = role === "agent" ? "advancement_task" : text(input.next_user_task_class, "next_user_task_class");
    priorities[key] = planTodoCreationPriority({text: todo, task_class: taskClass,
      ...(supplied ? {priority: input[key]} : {})}).priority;
  }
  return priorities;
}

export function monitorSuccessorIntent(value: unknown): MonitorSuccessorIntent {
  const input = requireJsonObject(value, "monitor successor intent");
  const material = requireBoolean(input.material_change, "material_change");
  const agentTodo = text(input.next_agent_todo, "next_agent_todo");
  const userTodo = text(input.next_user_todo, "next_user_todo");
  if ((agentTodo || userTodo) && !material) {
    throw new EffectRuntimeRequestError("`--next-agent-todo` and `--next-user-todo` require --material-change");
  }
  const action = text(input.next_action_kind, "next_action_kind")?.toLowerCase() ?? null;
  const policy = text(input.next_continuation_policy, "next_continuation_policy")?.toLowerCase() ?? null;
  const target = text(input.next_target_key, "next_target_key");
  const claim = text(input.next_claimed_by, "next_claimed_by");
  const repo = normalizeTodoRepository(input.next_task_repository, "--next-task-repository");
  const capabilities = normalizeTodoCapabilities(input.next_required_capabilities, "--next-required-capability");
  if (!agentTodo && (action || policy || target || claim || repo || capabilities.length)) {
    throw new EffectRuntimeRequestError("monitor successor routing options require --next-agent-todo");
  }
  if (agentTodo && !action) {
    throw new EffectRuntimeRequestError("`quota monitor-poll --next-agent-todo` requires explicit successor action semantics via --next-action-kind");
  }
  if (action && !/^[a-z][a-z0-9_-]{0,63}$/.test(action)) {
    throw new EffectRuntimeRequestError("--next-action-kind must be a public-safe token: lowercase letters, digits, '_' or '-'");
  }
  if (policy && !["independent_handoff", "same_agent_non_delivery"].includes(policy)) {
    throw new EffectRuntimeRequestError("--next-continuation-policy must be a supported todo continuation policy");
  }
  const userClass = text(input.next_user_task_class, "next_user_task_class");
  if (userTodo && !userClass) throw new EffectRuntimeRequestError("--next-user-todo requires explicit --next-user-task-class user_action|user_gate");
  if (!userTodo && userClass) throw new EffectRuntimeRequestError("--next-user-task-class requires --next-user-todo");
  if (userClass !== null && userClass !== "user_action" && userClass !== "user_gate") {
    throw new EffectRuntimeRequestError("--next-user-task-class must be user_action or user_gate");
  }
  let owner: string | null = null;
  if (claim) {
    try { owner = normalizeTodoAgent(claim, "next_claimed_by"); }
    catch { throw new EffectRuntimeRequestError("--next-claimed-by must be a public-safe agent id"); }
  }
  const priorities = monitorSuccessorPriorities(input);
  return {...priorities, next_agent_todo: agentTodo, next_action_kind: action,
    next_task_repository: repo, next_required_capabilities: capabilities,
    next_continuation_policy: policy, next_target_key: target, next_claimed_by: owner,
    next_user_todo: userTodo, next_user_task_class: userClass};
}

export function monitorSuccessorRoute(intent: MonitorSuccessorIntent, todoId: string, resultHash: string): JsonObject {
  if (!intent.next_agent_todo) return {};
  return {action_kind: intent.next_action_kind, task_repository: intent.next_task_repository,
    required_capabilities: intent.next_required_capabilities,
    continuation_policy: intent.next_continuation_policy ?? "independent_handoff",
    target_key: intent.next_target_key ?? `monitor-successor:${todoId}:${createHash("sha256").update(resultHash).digest("hex").slice(0, 16)}`,
    claimed_by: intent.next_claimed_by};
}

export function planMonitorSuccessor(value: unknown): JsonObject {
  const request = requireJsonObject(value, "monitor successor plan");
  if (request.schema_version !== MONITOR_SUCCESSOR_REQUEST_SCHEMA) throw new EffectRuntimeRequestError("monitor successor plan schema mismatch");
  const intent = monitorSuccessorIntent(request.intent);
  const todoId = text(request.todo_id, "todo_id");
  const resultHash = text(request.result_hash, "result_hash");
  if (!todoId || !/^todo_[a-z0-9_-]{3,64}$/.test(todoId) || !resultHash) {
    throw new EffectRuntimeRequestError("monitor successor plan requires a stable todo_id and result_hash");
  }
  const sourceRepository = text(request.source_task_repository, "source_task_repository");
  if (intent.next_agent_todo && sourceRepository && !intent.next_task_repository) {
    throw new EffectRuntimeRequestError("repository-bound monitor successors require explicit --next-task-repository so same-repository and cross-repository routing cannot be confused");
  }
  return {schema_version: MONITOR_SUCCESSOR_RESULT_SCHEMA, intent,
    agent_route: monitorSuccessorRoute(intent, todoId, resultHash)};
}
