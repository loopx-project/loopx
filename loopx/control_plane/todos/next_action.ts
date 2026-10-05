import {readTodoPriority, todoPriorityRank as priorityRank, type TodoPriority} from "./priority.ts";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString as optionalString,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringArray as stringArray,
} from "../runtime_decode.ts";

export const TODO_NEXT_ACTION_REQUEST_SCHEMA =
  "loopx_todo_next_action_transition_v1";
export const TODO_NEXT_ACTION_RESULT_SCHEMA =
  "loopx_todo_next_action_result_v0";
export const NEXT_ACTION_BINDING_SCHEMA = "loopx_next_action_binding_v0";

const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;
const NEXT_ACTION_BINDING_PATTERN =
  /^\s*<!--\s*loopx:next-action\s+schema=([A-Za-z0-9_-]+)\s+todo_id=(todo_[A-Za-z0-9_-]+)\s*-->\s*$/;
const TODO_STATUSES = new Set(["open", "done", "blocked", "deferred"]);
const CONTROL_TASK_CLASSES = new Set([
  "continuous_monitor",
  "user_gate",
  "blocker",
]);

export type {TodoPriority} from "./priority.ts";

export interface TodoNextActionSnapshot {
  todo_id: string;
  status: string;
  task_class: string | null;
  priority: TodoPriority | null;
  claimed_by: string | null;
  text: string;
  index: number;
  completion_continuation: string | null;
  successor_todo_ids: readonly string[];
}

export type TodoNextActionRequest =
  | {
      schema_version: typeof TODO_NEXT_ACTION_REQUEST_SCHEMA;
      operation: "bind";
      lines: readonly string[];
      todo_id: string;
    }
  | {
      schema_version: typeof TODO_NEXT_ACTION_REQUEST_SCHEMA;
      operation: "reconcile_added";
      lines: readonly string[];
      todo_id: string;
      agent_todos: readonly TodoNextActionSnapshot[];
    }
  | {
      schema_version: typeof TODO_NEXT_ACTION_REQUEST_SCHEMA;
      operation: "settle_completion";
      lines: readonly string[];
      todo_id: string;
      agent_todos: readonly TodoNextActionSnapshot[];
      materialized_todo_ids: readonly string[];
    };

export interface TodoNextActionResult extends JsonObject {
  schema_version: typeof TODO_NEXT_ACTION_RESULT_SCHEMA;
  operation: TodoNextActionRequest["operation"];
  outcome:
    | "bound"
    | "unchanged"
    | "not_terminal"
    | "awaiting_successor"
    | "route_unmatched"
    | "reprioritized"
    | "settled";
  changed: boolean;
  matched: boolean;
  lines: string[];
  completed_todo_id?: string;
  match_source?: "typed_todo_binding" | "legacy_exact_text";
  next_todo_id?: string | null;
  next_action?: string | null;
}

function nullableTodoPriority(
  value: unknown,
  label: string,
): TodoPriority | null {
  if (value === null) return null;
  // Snapshot input is a read compatibility boundary, unlike new priority intent.
  const priority = readTodoPriority({priority: requiredString(value, label)});
  if (priority === null) throw new EffectRuntimeRequestError(`${label} must be a supported Todo priority`);
  return priority;
}

function normalizedTodoId(value: unknown, label: string): string {
  const candidate = requiredString(value, label).trim().toLowerCase();
  if (!TODO_ID_PATTERN.test(candidate)) {
    throw new EffectRuntimeRequestError(`${label} must be a valid todo_id`);
  }
  return candidate;
}

function todoSnapshot(value: unknown, index: number): TodoNextActionSnapshot {
  const label = `agent_todos[${index}]`;
  const item = requiredObject(value, label);
  const status = requiredString(item.status, `${label}.status`).trim().toLowerCase();
  if (!TODO_STATUSES.has(status)) {
    throw new EffectRuntimeRequestError(`${label}.status is unsupported`);
  }
  if (!Number.isSafeInteger(item.index) || Number(item.index) < 1) {
    throw new EffectRuntimeRequestError(`${label}.index must be a positive integer`);
  }
  return {
    todo_id: normalizedTodoId(item.todo_id, `${label}.todo_id`),
    status,
    task_class: optionalString(item.task_class, `${label}.task_class`),
    priority: nullableTodoPriority(item.priority, `${label}.priority`),
    claimed_by: optionalString(item.claimed_by, `${label}.claimed_by`),
    text: normalizeText(requiredString(item.text, `${label}.text`)),
    index: Number(item.index),
    completion_continuation: optionalString(
      item.completion_continuation,
      `${label}.completion_continuation`,
    ),
    successor_todo_ids: stringArray(
      item.successor_todo_ids,
      `${label}.successor_todo_ids`,
    ).map((todoId, successorIndex) =>
      normalizedTodoId(todoId, `${label}.successor_todo_ids[${successorIndex}]`)
    ),
  };
}

export function decodeTodoNextActionRequest(
  value: unknown,
): TodoNextActionRequest {
  const request = requiredObject(value, "todo.next_action params");
  if (request.schema_version !== TODO_NEXT_ACTION_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo Next Action request schema mismatch");
  }
  const operation = requiredString(
    request.operation,
    "todo.next_action operation",
  );
  const lines = stringArray(request.lines, "todo.next_action lines");
  const todoId = normalizedTodoId(request.todo_id, "todo.next_action todo_id");
  if (operation === "bind") {
    return {
      schema_version: TODO_NEXT_ACTION_REQUEST_SCHEMA,
      operation,
      lines,
      todo_id: todoId,
    };
  }
  if (operation !== "reconcile_added" && operation !== "settle_completion") {
    throw new EffectRuntimeRequestError("Todo Next Action operation is unsupported");
  }
  if (!Array.isArray(request.agent_todos)) {
    throw new EffectRuntimeRequestError("todo.next_action agent_todos must be an array");
  }
  if (operation === "reconcile_added") {
    return {
      schema_version: TODO_NEXT_ACTION_REQUEST_SCHEMA,
      operation,
      lines,
      todo_id: todoId,
      agent_todos: request.agent_todos.map((value, index) =>
        todoSnapshot(value, index)
      ),
    };
  }
  const materializedTodoIds = stringArray(
    request.materialized_todo_ids,
    "todo.next_action materialized_todo_ids",
  ).map((todoId, index) =>
    normalizedTodoId(todoId, `materialized_todo_ids[${index}]`)
  );
  return {
    schema_version: TODO_NEXT_ACTION_REQUEST_SCHEMA,
    operation,
    lines,
    todo_id: todoId,
    agent_todos: request.agent_todos.map(todoSnapshot),
    materialized_todo_ids: materializedTodoIds,
  };
}

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function headingBounds(
  lines: readonly string[],
  heading: string,
): [number, number] | null {
  const needle = `## ${heading}`;
  const start = lines.findIndex((line) => line.trim() === needle);
  if (start < 0) return null;
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (lines[index].startsWith("## ")) {
      end = index;
      break;
    }
  }
  return [start, end];
}

function visibleNextActionEntries(lines: readonly string[]): string[] {
  const bounds = headingBounds(lines, "Next Action");
  if (!bounds) return [];
  const [start, end] = bounds;
  const entries: string[] = [];
  const current: string[] = [];
  const flush = (): void => {
    if (!current.length) return;
    const normalized = normalizeText(current.join(" "));
    if (normalized) entries.push(normalized);
    current.length = 0;
  };
  for (const line of lines.slice(start + 1, end)) {
    const stripped = line.trim();
    if (stripped.startsWith("<!--")) continue;
    if (!stripped) {
      flush();
      continue;
    }
    const bullet = /^(?:[-*]\s+|\d+[.)]\s+)(.*)$/.exec(stripped);
    if (bullet) {
      flush();
      current.push(bullet[1].replace(/^\[[ xX]\]\s+/, ""));
      continue;
    }
    if (current.length && /^(?: |\t)/.test(line)) {
      current.push(stripped);
      continue;
    }
    flush();
    const normalized = normalizeText(stripped);
    if (normalized) entries.push(normalized);
  }
  flush();
  return entries;
}

interface BindingMatch {
  schema: string;
  todoId: string;
  index: number;
}

function bindingMatches(lines: readonly string[]): BindingMatch[] {
  const bounds = headingBounds(lines, "Next Action");
  if (!bounds) return [];
  const [start, end] = bounds;
  const matches: BindingMatch[] = [];
  for (let index = start + 1; index < end; index += 1) {
    const match = NEXT_ACTION_BINDING_PATTERN.exec(lines[index]);
    if (match) {
      matches.push({
        schema: match[1],
        todoId: match[2].toLowerCase(),
        index,
      });
    }
  }
  return matches;
}

/** Decode the existing typed breadcrumb; arbitrary prose cannot select work. */
export function projectNextActionBinding(value: unknown): JsonObject {
  const request = requiredObject(value, "next action binding");
  const lines = stringArray(request.lines, "next action binding lines");
  const matches = bindingMatches(lines);
  return {todo_id: matches.length === 1 && matches[0].schema === NEXT_ACTION_BINDING_SCHEMA &&
    visibleNextActionEntries(lines).length === 1 ? matches[0].todoId : null};
}

function hasBindingDirective(lines: readonly string[]): boolean {
  const bounds = headingBounds(lines, "Next Action");
  if (!bounds) return false;
  const [start, end] = bounds;
  return lines.slice(start + 1, end).some((line) =>
    /^\s*<!--\s*loopx:next-action\b/.test(line)
  );
}

function bindingLine(todoId: string): string {
  return `<!-- loopx:next-action schema=${NEXT_ACTION_BINDING_SCHEMA} todo_id=${todoId} -->`;
}

function unchangedResult(
  operation: TodoNextActionRequest["operation"],
  outcome: TodoNextActionResult["outcome"],
  lines: readonly string[],
  matched = false,
): TodoNextActionResult {
  return {
    schema_version: TODO_NEXT_ACTION_RESULT_SCHEMA,
    operation,
    outcome,
    changed: false,
    matched,
    lines: [...lines],
  };
}

function bindNextAction(
  lines: readonly string[],
  todoId: string,
): TodoNextActionResult {
  const updated = [...lines];
  const bounds = headingBounds(updated, "Next Action");
  if (!bounds || visibleNextActionEntries(updated).length !== 1) {
    return unchangedResult("bind", "unchanged", updated);
  }
  const marker = bindingLine(todoId);
  const matches = bindingMatches(updated);
  if (matches.length === 1 && updated[matches[0].index] === marker) {
    return unchangedResult("bind", "unchanged", updated, true);
  }
  if (matches.length > 0 || hasBindingDirective(updated)) {
    return unchangedResult("bind", "unchanged", updated);
  }
  const refreshed = headingBounds(updated, "Next Action");
  if (!refreshed) return unchangedResult("bind", "unchanged", updated);
  const [start, end] = refreshed;
  let insertAt = end;
  while (insertAt > start + 1 && !updated[insertAt - 1].trim()) insertAt -= 1;
  updated.splice(insertAt, 0, marker);
  return {
    schema_version: TODO_NEXT_ACTION_RESULT_SCHEMA,
    operation: "bind",
    outcome: "bound",
    changed: true,
    matched: true,
    lines: updated,
  };
}

function nextOpenAgentTodo(
  todos: readonly TodoNextActionSnapshot[],
): TodoNextActionSnapshot | null {
  const candidates = todos.filter((todo) =>
    todo.status === "open" &&
    !CONTROL_TASK_CLASSES.has(todo.task_class ?? "")
  );
  candidates.sort((left, right) => {
    const leftClass = left.task_class === "advancement_task" ? 0 : 1;
    const rightClass = right.task_class === "advancement_task" ? 0 : 1;
    return leftClass - rightClass ||
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.index - right.index;
  });
  return candidates[0] ?? null;
}

function replaceNextAction(
  lines: readonly string[],
  todo: TodoNextActionSnapshot | null,
): string[] | null {
  const bounds = headingBounds(lines, "Next Action");
  if (!bounds) return null;
  const [start, end] = bounds;
  const replacement = ["## Next Action", ""];
  if (todo) {
    replacement.push(
      `- ${todo.text}`,
      bindingLine(todo.todo_id),
      "",
    );
  }
  return [
    ...lines.slice(0, start),
    ...replacement,
    ...lines.slice(end),
  ];
}

function reconcileAddedTodo(
  request: Extract<TodoNextActionRequest, { operation: "reconcile_added" }>,
): TodoNextActionResult {
  const added = request.agent_todos.find(
    (todo) => todo.todo_id === request.todo_id,
  );
  if (
    added?.status !== "open" ||
    added.task_class !== "advancement_task"
  ) {
    return unchangedResult("reconcile_added", "unchanged", request.lines);
  }
  const matches = bindingMatches(request.lines);
  if (
    matches.length !== 1 ||
    matches[0].schema !== NEXT_ACTION_BINDING_SCHEMA ||
    visibleNextActionEntries(request.lines).length !== 1
  ) {
    return unchangedResult("reconcile_added", "route_unmatched", request.lines);
  }
  const bound = request.agent_todos.find(
    (todo) => todo.todo_id === matches[0].todoId,
  );
  if (
    bound?.status !== "open" ||
    bound.task_class !== "advancement_task" ||
    priorityRank(added.priority) >= priorityRank(bound.priority)
  ) {
    return unchangedResult("reconcile_added", "unchanged", request.lines, true);
  }
  // An unclaimed bootstrap route may yield to concrete claimed work. A route
  // already owned by another peer remains authoritative for that peer's lane.
  if (bound.claimed_by && bound.claimed_by !== added.claimed_by) {
    return unchangedResult("reconcile_added", "unchanged", request.lines, true);
  }
  const updated = replaceNextAction(request.lines, added);
  if (!updated) {
    return unchangedResult("reconcile_added", "route_unmatched", request.lines, true);
  }
  return {
    schema_version: TODO_NEXT_ACTION_RESULT_SCHEMA,
    operation: "reconcile_added",
    outcome: "reprioritized",
    changed: true,
    matched: true,
    lines: updated,
    next_todo_id: added.todo_id,
    next_action: added.text,
  };
}

function settleCompletedTodo(
  request: Extract<TodoNextActionRequest, { operation: "settle_completion" }>,
): TodoNextActionResult {
  const completed = request.agent_todos.find(
    (todo) => todo.todo_id === request.todo_id,
  );
  if (!completed) {
    throw new EffectRuntimeRequestError("completed Todo is absent from the Agent Todo projection");
  }
  if (completed.status !== "done") {
    return unchangedResult(
      "settle_completion",
      "not_terminal",
      request.lines,
    );
  }
  if (
    completed.completion_continuation === "successor" &&
    (
      completed.successor_todo_ids.length === 0 ||
      completed.successor_todo_ids.some(
        (todoId) => !request.materialized_todo_ids.includes(todoId),
      )
    )
  ) {
    return unchangedResult(
      "settle_completion",
      "awaiting_successor",
      request.lines,
    );
  }

  const matches = bindingMatches(request.lines);
  const boundTodoId = matches.length === 1 &&
      matches[0].schema === NEXT_ACTION_BINDING_SCHEMA
    ? matches[0].todoId
    : null;
  let matchSource: TodoNextActionResult["match_source"];
  if (boundTodoId === completed.todo_id) {
    matchSource = "typed_todo_binding";
  } else if (
    !hasBindingDirective(request.lines) &&
    visibleNextActionEntries(request.lines).length === 1 &&
    visibleNextActionEntries(request.lines)[0] === completed.text
  ) {
    matchSource = "legacy_exact_text";
  } else {
    return {
      ...unchangedResult(
        "settle_completion",
        "route_unmatched",
        request.lines,
      ),
      completed_todo_id: completed.todo_id,
    };
  }

  const nextTodo = nextOpenAgentTodo(request.agent_todos);
  const updated = replaceNextAction(request.lines, nextTodo);
  if (!updated) {
    return {
      ...unchangedResult(
        "settle_completion",
        "route_unmatched",
        request.lines,
        true,
      ),
      completed_todo_id: completed.todo_id,
      match_source: matchSource,
    };
  }
  const changed = updated.some((line, index) => line !== request.lines[index]) ||
    updated.length !== request.lines.length;
  return {
    schema_version: TODO_NEXT_ACTION_RESULT_SCHEMA,
    operation: "settle_completion",
    outcome: changed ? "settled" : "unchanged",
    changed,
    matched: true,
    lines: updated,
    completed_todo_id: completed.todo_id,
    match_source: matchSource,
    next_todo_id: nextTodo?.todo_id ?? null,
    next_action: nextTodo?.text ?? null,
  };
}

export function transitionTodoNextAction(
  value: unknown,
): TodoNextActionResult {
  const request = decodeTodoNextActionRequest(value);
  if (request.operation === "bind") {
    return bindNextAction(request.lines, request.todo_id);
  }
  if (request.operation === "reconcile_added") {
    return reconcileAddedTodo(request);
  }
  return settleCompletedTodo(request);
}
