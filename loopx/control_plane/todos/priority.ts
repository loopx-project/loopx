import {COORDINATION_STATE_CONTRACT} from "../coordination/coordination_state_contract.generated.ts";
/** Todo priority is intent, never an eligibility grant. Legacy text markers
 * are decoded only at compatibility boundaries; ordinary prose is opaque. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {compactPythonWhitespace} from "../coordination/todo_agents.ts";

const contract = COORDINATION_STATE_CONTRACT.todo_priority;
export const TODO_PRIORITIES = contract.values;
export type TodoPriority = typeof TODO_PRIORITIES[number];
export const DEFAULT_TODO_PRIORITY: TodoPriority = "P1";
export const BLOCKING_TODO_PRIORITY: TodoPriority = "P0";
const LEGACY_PREFIX = new RegExp(contract.legacy_prefix_pattern, "iu");
const LEGACY_LABEL = new RegExp(contract.legacy_label_pattern, "iu");

export function normalizeTodoPriority(value: unknown): TodoPriority | null {
  if (value === null) return null;
  if (typeof value !== "string" || !TODO_PRIORITIES.includes(value.trim().toUpperCase() as TodoPriority)) {
    throw new EffectRuntimeRequestError("priority must be P0, P1, P2, P3, P4 or null");
  }
  return value.trim().toUpperCase() as TodoPriority;
}

export function legacyTodoPriority(text: string): {priority: TodoPriority | null; title: string} {
  const match = LEGACY_PREFIX.exec(text);
  return match ? {priority: LEGACY_LABEL.exec(match[1]!)?.[1]?.toUpperCase() as TodoPriority ?? null, title: match[2]!.trim()} :
    {priority: null, title: text};
}

/** Historical decorated labels remain readable without rewriting receipts. */
export function readTodoPriority(todo: JsonObject): TodoPriority | null {
  if (Object.hasOwn(todo, "priority")) {
    return typeof todo.priority === "string"
      ? LEGACY_LABEL.exec(todo.priority.trim())?.[1]?.toUpperCase() as TodoPriority ?? null : null;
  }
  return legacyTodoPriority(String(todo.text ?? todo.title ?? "")).priority;
}
export function todoPriorityRank(priority: unknown): number {
  const value = readTodoPriority({priority: priority ?? null});
  return value === null ? contract.missing_rank : TODO_PRIORITIES.indexOf(value);
}

/** Produces the compatibility display together with canonical priority/title.
 * A text-only unprefixed edit retains priority. An explicit legacy prefix is
 * still an edit for old callers. Conflicting explicit declarations fail. */
export function planTodoPriority(todo: JsonObject, intent: JsonObject): JsonObject {
  const hasPriority = Object.hasOwn(intent, "priority");
  if (intent.clear_priority != null && typeof intent.clear_priority !== "boolean") {
    throw new EffectRuntimeRequestError("clear_priority must be a boolean");
  }
  if (hasPriority && intent.clear_priority === true) {
    throw new EffectRuntimeRequestError("provide either priority or clear_priority, not both");
  }
  const hasText = Object.hasOwn(intent, "text");
  if (!hasText && !hasPriority && intent.clear_priority !== true) return {};
  const rawText = hasText ? intent.text : todo.text;
  if (typeof rawText !== "string" || !compactPythonWhitespace(rawText)) {
    throw new EffectRuntimeRequestError("todo text must not be empty");
  }
  const parsed = legacyTodoPriority(compactPythonWhitespace(rawText));
  const declared = hasPriority ? normalizeTodoPriority(intent.priority) :
    intent.clear_priority === true ? null : undefined;
  if (hasText && declared !== undefined && parsed.priority !== null && parsed.priority !== declared) {
    throw new EffectRuntimeRequestError("priority conflicts with the legacy text prefix; remove the prefix");
  }
  const priority = declared !== undefined ? declared :
    hasText && parsed.priority !== null ? parsed.priority : readTodoPriority(todo);
  return {text: priority === null ? parsed.title : `[${priority}] ${parsed.title}`,
    title: parsed.title, priority};
}

/** Default only a fresh creation, after receipt lookup. Machine-declared
 * blocking work is urgent; ordinary work remains P1. Reads, edits and
 * deliberate predecessor inheritance retain their original priority. */
export function planTodoCreationPriority(intent: JsonObject): JsonObject {
  const defaultPriority = intent.task_class === "user_gate" || intent.task_class === "blocker" ||
    intent.status === "blocked" ? BLOCKING_TODO_PRIORITY : DEFAULT_TODO_PRIORITY;
  return planTodoPriority({priority: defaultPriority}, intent);
}

export function evaluateTodoPriority(value: unknown): JsonObject {
  const request = requireJsonObject(value, "Todo priority request");
  if (request.schema_version !== "todo_priority_request_v0") throw new EffectRuntimeRequestError("Todo priority schema mismatch");
  if (request.create !== undefined && typeof request.create !== "boolean") {
    throw new EffectRuntimeRequestError("create must be a boolean");
  }
  const todo = requireJsonObject(request.todo, "Todo");
  const intent = requireJsonObject(request.intent, "priority intent");
  return request.create === true ? planTodoCreationPriority(intent) : planTodoPriority(todo, intent);
}
