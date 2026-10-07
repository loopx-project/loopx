/** Current execution admission for the existing Todo completion wait. The
 * full canonical head, including retained archived rows, is the source. */
import type {JsonObject} from "../effect_program.ts";
import {diagnoseTodoResumeCondition, evaluateTodoResumeConditions, normalizeTodoResumeWhen,
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION, TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION} from "../todos/resume_condition.ts";

export interface TodoExecutionDependencyRejection {
  code: "todo_dependency_pending" | "todo_dependency_invalid";
  reason: string;
  condition: JsonObject;
}

function completionTarget(todo: JsonObject | undefined): string | null {
  if (typeof todo?.resume_when !== "string" || !todo.resume_when.trim().toLowerCase().startsWith("todo_done:")) return null;
  const normalized = normalizeTodoResumeWhen({schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
    resume_when: todo.resume_when});
  return normalized?.startsWith("todo_done:") ? normalized.slice("todo_done:".length) : null;
}

export function todoExecutionDependencyRejection(
  todos: ReadonlyMap<string, JsonObject>, todoId: string,
): TodoExecutionDependencyRejection | null {
  const todo = todos.get(todoId);
  if (typeof todo?.resume_when !== "string" || !todo.resume_when.trim().toLowerCase().startsWith("todo_done:")) return null;
  const immediateTarget = completionTarget(todo);
  if (immediateTarget === null) return {code: "todo_dependency_invalid",
    reason: "Completion dependency target is invalid", condition: {resume_when: todo.resume_when}};
  // The resume evaluator owns satisfaction and diagnosis. We only supply its
  // exact source records and reject a structural cycle in this one-edge chain.
  const result = evaluateTodoResumeConditions({schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [todo], source_items: todos.has(immediateTarget) ? [todos.get(immediateTarget)!] : [],
    kinds: ["todo_done"]});
  const entry = (result.conditions as JsonObject[])[0];
  const condition = entry?.condition as JsonObject | undefined;
  if (!condition) return {code: "todo_dependency_invalid", reason: "Completion dependency could not be evaluated", condition: {}};
  const seen = new Set([todoId]);
  let target: string | null = immediateTarget;
  while (target !== null) {
    if (seen.has(target)) return {code: "todo_dependency_invalid", reason: "Completion dependency contains a cycle", condition};
    seen.add(target);
    const prerequisite = todos.get(target);
    // A retained completion is the exact condition's terminal fact. Its old
    // planning wait is history, not a live edge in the waiting chain.
    target = prerequisite?.status === "done" ? null : completionTarget(prerequisite);
  }
  const diagnosis = diagnoseTodoResumeCondition(condition, todoId);
  if (diagnosis.state === "satisfied") return null;
  return {code: diagnosis.state === "invalid" ? "todo_dependency_invalid" : "todo_dependency_pending",
    reason: diagnosis.state === "invalid"
      ? `Completion dependency is invalid: ${diagnosis.reason}`
      : "Prerequisite Todo must be done before this Todo can execute", condition};
}
