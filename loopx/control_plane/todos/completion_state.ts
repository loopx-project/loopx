import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean as requiredBoolean,
  requireJsonObject as requiredObject,
  requireStringArray as stringArray,
} from "../runtime_decode.ts";

export const TODO_COMPLETION_STATE_REQUEST_SCHEMA =
  "loopx_todo_completion_state_request_v0";
export const TODO_COMPLETION_STATE_RESULT_SCHEMA =
  "loopx_todo_completion_state_result_v0";

export const TODO_COMPLETION_CONTINUATIONS = [
  "active_goal",
  "successor",
  "no_followup",
] as const;
export const TODO_COMPLETION_RECOVERIES = [
  "same_turn_terminal_closeout",
  "lifecycle_reentry_terminal_closeout",
] as const;

export type TodoCompletionContinuation =
  (typeof TODO_COMPLETION_CONTINUATIONS)[number];
export type TodoCompletionRecovery =
  (typeof TODO_COMPLETION_RECOVERIES)[number];
export type TodoCompletionNormalizationKind =
  | "no_followup"
  | "continuation"
  | "recovery";

export interface TodoCompletionStateResult extends JsonObject {
  schema_version: typeof TODO_COMPLETION_STATE_RESULT_SCHEMA;
  continuation: TodoCompletionContinuation;
  recovery: TodoCompletionRecovery | null;
}

function optionalBoolean(value: unknown, label: string): boolean | null {
  if (value === null || value === undefined) return null;
  return requiredBoolean(value, label);
}

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function requestObject(value: unknown, operation: string): JsonObject {
  const request = requiredObject(value, `todo.completion_state.${operation} params`);
  if (request.schema_version !== TODO_COMPLETION_STATE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo completion state request schema mismatch");
  }
  return request;
}

export function normalizeTodoNoFollowup(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError("no_followup must be a boolean, string, or null");
  }
  const candidate = value.trim().toLowerCase();
  if (["1", "true", "yes", "y", "no_followup", "no-followup"].includes(candidate)) {
    return true;
  }
  if (["0", "false", "no", "n"].includes(candidate)) return false;
  return null;
}

export function normalizeTodoCompletionContinuation(
  value: unknown,
): TodoCompletionContinuation | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError("completion_continuation must be a string or null");
  }
  const candidate = value.trim().toLowerCase();
  return TODO_COMPLETION_CONTINUATIONS.some((item) => item === candidate)
    ? candidate as TodoCompletionContinuation
    : null;
}

export function requireTodoCompletionContinuation(
  value: unknown,
): TodoCompletionContinuation {
  const normalized = normalizeTodoCompletionContinuation(value);
  if (normalized !== null) return normalized;
  throw new EffectRuntimeRequestError(
    "completion_continuation must be one of: active_goal, successor, no_followup",
  );
}

export function normalizeTodoCompletionRecovery(
  value: unknown,
): TodoCompletionRecovery | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError("completion_recovery must be a string or null");
  }
  const candidate = value.trim().toLowerCase();
  return TODO_COMPLETION_RECOVERIES.some((item) => item === candidate)
    ? candidate as TodoCompletionRecovery
    : null;
}

export function requireTodoCompletionMetadata(
  key: string,
  value: unknown,
): TodoCompletionContinuation | TodoCompletionRecovery | null {
  if (key === "completion_continuation") {
    return requireTodoCompletionContinuation(value);
  }
  const normalized = normalizeTodoCompletionRecovery(value);
  if (normalized !== null || value === null || value === undefined || value === "") {
    return normalized;
  }
  throw new EffectRuntimeRequestError(
    "completion_recovery must be same_turn_terminal_closeout or " +
      "lifecycle_reentry_terminal_closeout",
  );
}

export function completionContinuationForWrite(
  noFollowup: boolean,
  hasSuccessor: boolean,
): TodoCompletionContinuation {
  if (noFollowup && hasSuccessor) {
    throw new EffectRuntimeRequestError("todo completion cannot record both no_followup and a successor");
  }
  if (noFollowup) return "no_followup";
  if (hasSuccessor) return "successor";
  return "active_goal";
}

export function normalizeTodoCompletionValue(value: unknown): JsonObject {
  const request = requestObject(value, "normalize");
  const kind = request.kind;
  if (kind !== "no_followup" && kind !== "continuation" && kind !== "recovery") {
    throw new EffectRuntimeRequestError("completion normalization kind is unsupported");
  }
  const raw = request.value;
  const normalized = kind === "no_followup"
    ? normalizeTodoNoFollowup(raw)
    : kind === "continuation"
    ? normalizeTodoCompletionContinuation(raw)
    : normalizeTodoCompletionRecovery(raw);
  return {
    schema_version: TODO_COMPLETION_STATE_RESULT_SCHEMA,
    kind,
    value: normalized,
  };
}

export function requireTodoCompletionMetadataValue(value: unknown): JsonObject {
  const request = requestObject(value, "require_metadata");
  const key = optionalString(request.key, "key");
  if (key === null) throw new EffectRuntimeRequestError("key must be a string");
  return {
    schema_version: TODO_COMPLETION_STATE_RESULT_SCHEMA,
    value: requireTodoCompletionMetadata(key, request.value),
  };
}

export function selectTodoCompletionState(value: unknown): TodoCompletionStateResult {
  const request = requestObject(value, "evaluate");
  const todo = requiredObject(request.todo, "todo");
  const requestedNoFollowup = requiredBoolean(
    request.requested_no_followup,
    "requested_no_followup",
  );
  const effectiveNoFollowup = requestedNoFollowup ||
    normalizeTodoNoFollowup(todo.no_followup) === true;
  const continuation = completionContinuationForWrite(
    effectiveNoFollowup,
    requiredBoolean(request.has_successor, "has_successor"),
  );
  const completionIdentitySource = optionalString(
    request.completion_identity_source,
    "completion_identity_source",
  );
  if (
    completionIdentitySource !== null &&
    completionIdentitySource !== "turn_settlement" &&
    completionIdentitySource !== "unscoped_completion" &&
    completionIdentitySource !== "lifecycle_reentry"
  ) {
    throw new EffectRuntimeRequestError("completion_identity_source is unsupported");
  }
  const recovery = String(todo.status ?? "") === "done" &&
      requestedNoFollowup &&
      normalizeTodoCompletionContinuation(todo.completion_continuation) ===
        "active_goal"
    ? completionIdentitySource === "lifecycle_reentry"
      ? "lifecycle_reentry_terminal_closeout"
      : "same_turn_terminal_closeout"
    : null;
  return {
    schema_version: TODO_COMPLETION_STATE_RESULT_SCHEMA,
    continuation,
    recovery,
  };
}

export function buildTodoCompletionMetadataUpdates(value: unknown): JsonObject {
  const request = requestObject(value, "metadata_updates");
  const block = requiredObject(request.block, "block");
  const completionContinuation = optionalString(
    request.completion_continuation,
    "completion_continuation",
  );
  const completionRecovery = optionalString(
    request.completion_recovery,
    "completion_recovery",
  );
  const updates: JsonObject = {};
  if (completionContinuation !== null) {
    updates.completion_continuation = completionContinuation;
  }
  if (completionRecovery !== null) updates.completion_recovery = completionRecovery;

  const targetStatus = optionalString(request.target_status, "target_status");
  const normalizedStatus = optionalString(
    request.normalized_status,
    "normalized_status",
  );
  if (
    targetStatus !== "done" || normalizedStatus !== "done" ||
    completionContinuation !== null ||
    normalizeTodoCompletionContinuation(block.completion_continuation) !== null
  ) {
    return {
      schema_version: TODO_COMPLETION_STATE_RESULT_SCHEMA,
      updates,
    };
  }

  const requestedNoFollowup = optionalBoolean(request.no_followup, "no_followup");
  const effectiveNoFollowup = requestedNoFollowup === null
    ? normalizeTodoNoFollowup(block.no_followup) === true
    : requestedNoFollowup;
  const successorTodoIds = request.successor_todo_ids === null
    ? stringArray(block.successor_todo_ids, "block.successor_todo_ids")
    : stringArray(request.successor_todo_ids, "successor_todo_ids");
  updates.completion_continuation = completionContinuationForWrite(
    effectiveNoFollowup,
    successorTodoIds.length > 0,
  );
  return {
    schema_version: TODO_COMPLETION_STATE_RESULT_SCHEMA,
    updates,
  };
}
