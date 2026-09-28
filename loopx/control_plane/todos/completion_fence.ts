import { createHash } from "node:crypto";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean as requiredBoolean,
  requireJsonObject as requiredObject,
} from "../runtime_decode.ts";
import {
  normalizeTodoCompletionContinuation,
  normalizeTodoNoFollowup,
  type TodoCompletionContinuation,
} from "./completion_state.ts";

export const TODO_COMPLETION_FENCE_REQUEST_SCHEMA =
  "loopx_todo_completion_fence_request_v0";
export const TODO_COMPLETION_FENCE_RESULT_SCHEMA =
  "loopx_todo_completion_fence_result_v0";

const TODO_STATUSES = ["open", "done", "blocked", "deferred"] as const;
const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;

export type TodoStatus = (typeof TODO_STATUSES)[number];
export type TodoCompletionProjectionSource = "materialized";
export type TodoCompletionIdentitySource =
  | "turn_settlement"
  | "unscoped_completion"
  | "lifecycle_reentry";

export interface TodoCompletionFenceRequest {
  schema_version: typeof TODO_COMPLETION_FENCE_REQUEST_SCHEMA;
  projection_source: TodoCompletionProjectionSource;
  todo: {
    status: TodoStatus;
    no_followup: boolean | string | null;
    completion_continuation: string | null;
    completion_turn_key: string | null;
    successor_todo_ids: readonly string[];
  };
  requested_no_followup: boolean;
  requested_completion_turn_key: string | null;
  requested_completion_identity_source: TodoCompletionIdentitySource | null;
  goal_id: string | null;
  todo_id: string | null;
}

export type TodoCompletionFenceContinueReason =
  | "not_terminal"
  | "same_turn_terminal_upgrade"
  | "lifecycle_reentry_terminal_upgrade"
  | "unscoped_completion_identity_repair"
  | "untyped_completion_repair";

export type TodoCompletionFenceResult =
  | (JsonObject & {
      schema_version: typeof TODO_COMPLETION_FENCE_RESULT_SCHEMA;
      outcome: "continue";
      reason: TodoCompletionFenceContinueReason;
      status: TodoStatus;
      terminal_before_request: boolean;
      completion_continuation: TodoCompletionContinuation | null;
    })
  | (JsonObject & {
      schema_version: typeof TODO_COMPLETION_FENCE_RESULT_SCHEMA;
      outcome: "replay";
      reason: "already_terminal";
      status: "done" | "deferred";
      terminal_before_request: true;
      completion_continuation: TodoCompletionContinuation | null;
    });

function optionalOpaqueString(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function completionIdentitySource(
  value: unknown,
): TodoCompletionIdentitySource | null {
  if (value === null || value === undefined || value === "") return null;
  if (
    value === "turn_settlement" || value === "unscoped_completion" ||
    value === "lifecycle_reentry"
  ) {
    return value;
  }
  throw new EffectRuntimeRequestError("requested_completion_identity_source is unsupported");
}

export function localTodoCompletionIdentity(
  goalId: string,
  todoId: string,
): string {
  const digest = createHash("sha256")
    .update(`loopx-local-todo-completion-v0\0${goalId}\0${todoId}`, "utf8")
    .digest("hex");
  return `local_completion_${digest.slice(0, 32)}`;
}

function todoNoFollowup(value: unknown): boolean | null {
  if (
    value !== null && value !== undefined &&
    typeof value !== "boolean" && typeof value !== "string"
  ) {
    throw new EffectRuntimeRequestError("todo.no_followup must be a boolean, string, or null");
  }
  return normalizeTodoNoFollowup(value);
}

function todoCompletionContinuation(
  value: unknown,
): TodoCompletionContinuation | null {
  if (
    value !== null && value !== undefined && value !== "" &&
    typeof value !== "string"
  ) {
    throw new EffectRuntimeRequestError("todo.completion_continuation must be a string or null");
  }
  return normalizeTodoCompletionContinuation(value);
}

function todoStatus(value: unknown): TodoStatus {
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError("todo.status must be a supported string");
  }
  const normalized = value.trim().toLowerCase();
  if (!TODO_STATUSES.some((status) => status === normalized)) {
    throw new EffectRuntimeRequestError("todo.status is unsupported");
  }
  return normalized as TodoStatus;
}

function projectionSource(value: unknown): TodoCompletionProjectionSource {
  if (value === "materialized") return value;
  throw new EffectRuntimeRequestError("projection_source is unsupported");
}

function successorTodoIds(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  const values = Array.isArray(value) ? value : [value];
  if (values.some((item) => typeof item !== "string")) {
    throw new EffectRuntimeRequestError("todo.successor_todo_ids must contain only strings");
  }
  const result: string[] = [];
  for (const raw of values as string[]) {
    for (const match of raw.matchAll(/\btodo_[A-Za-z0-9_-]+\b/g)) {
      const todoId = match[0].toLowerCase();
      if (TODO_ID_PATTERN.test(todoId) && !result.includes(todoId)) {
        result.push(todoId);
      }
    }
    const todoId = raw.trim().toLowerCase();
    if (TODO_ID_PATTERN.test(todoId) && !result.includes(todoId)) {
      result.push(todoId);
    }
  }
  return result;
}

export function decodeTodoCompletionFenceRequest(
  value: unknown,
): TodoCompletionFenceRequest {
  const request = requiredObject(value, "todo.completion_fence params");
  if (request.schema_version !== TODO_COMPLETION_FENCE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo completion fence request schema mismatch");
  }
  const todo = requiredObject(request.todo, "todo.completion_fence todo");
  return {
    schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: projectionSource(request.projection_source),
    todo: {
      status: todoStatus(todo.status),
      no_followup: todoNoFollowup(todo.no_followup),
      completion_continuation: optionalOpaqueString(
        todo.completion_continuation,
        "todo.completion_continuation",
      ),
      completion_turn_key: optionalOpaqueString(
        todo.completion_turn_key,
        "todo.completion_turn_key",
      ),
      successor_todo_ids: successorTodoIds(todo.successor_todo_ids),
    },
    requested_no_followup: requiredBoolean(
      request.requested_no_followup,
      "requested_no_followup",
    ),
    requested_completion_turn_key: optionalOpaqueString(
      request.requested_completion_turn_key,
      "requested_completion_turn_key",
    ),
    requested_completion_identity_source: completionIdentitySource(
      request.requested_completion_identity_source,
    ),
    goal_id: optionalOpaqueString(request.goal_id, "goal_id"),
    todo_id: optionalOpaqueString(request.todo_id, "todo_id"),
  };
}

export function evaluateTodoCompletionFence(
  value: unknown,
): TodoCompletionFenceResult {
  const request = decodeTodoCompletionFenceRequest(value);
  const status = request.todo.status;
  const continuation = todoCompletionContinuation(
    request.todo.completion_continuation,
  );
  const done = status === "done";
  const terminalBeforeRequest = done;
  const requestedKey = request.requested_completion_turn_key;
  const storedKey = request.todo.completion_turn_key;
  const expectedLocalKey = request.goal_id !== null && request.todo_id !== null
    ? localTodoCompletionIdentity(request.goal_id, request.todo_id)
    : null;
  const unscopedIdentityRepair = done && storedKey === null &&
    request.requested_completion_identity_source === "lifecycle_reentry" &&
    requestedKey !== null && requestedKey === expectedLocalKey;

  if (
    request.requested_completion_identity_source === "lifecycle_reentry" &&
    !done
  ) {
    throw new EffectRuntimeRequestError("Todo lifecycle reentry requires an already completed Todo");
  }

  if (
    done && requestedKey !== null && requestedKey !== storedKey &&
    !unscopedIdentityRepair
  ) {
    throw new EffectRuntimeRequestError(
      "todo is already completed under a different completion_turn_key",
    );
  }

  const terminalUpgrade = done && request.requested_no_followup &&
    normalizeTodoNoFollowup(request.todo.no_followup) !== true;
  if (terminalUpgrade) {
    if (request.todo.successor_todo_ids.length > 0) {
      throw new EffectRuntimeRequestError(
        "todo terminal closeout cannot replace an existing successor",
      );
    }
    if (continuation === null) {
      throw new EffectRuntimeRequestError(
        "completed todo is missing completion_continuation; repair the " +
          "Todo with `loopx todo complete` without --no-follow-up before terminal " +
          "closeout",
      );
    }
    if (continuation !== "active_goal") {
      throw new EffectRuntimeRequestError(
        "todo terminal closeout recovery requires " +
          "completion_continuation=active_goal",
      );
    }
    if (
      requestedKey === null ||
      (requestedKey !== storedKey && !unscopedIdentityRepair)
    ) {
      throw new EffectRuntimeRequestError(
        "todo terminal closeout requires the original completion_turn_key",
      );
    }
    return {
      schema_version: TODO_COMPLETION_FENCE_RESULT_SCHEMA,
      outcome: "continue",
      reason: unscopedIdentityRepair
        ? "unscoped_completion_identity_repair"
        : request.requested_completion_identity_source === "lifecycle_reentry"
        ? "lifecycle_reentry_terminal_upgrade"
        : "same_turn_terminal_upgrade",
      status,
      terminal_before_request: true,
      completion_continuation: continuation,
    };
  }

  if (done && continuation === null) {
    return {
      schema_version: TODO_COMPLETION_FENCE_RESULT_SCHEMA,
      outcome: "continue",
      reason: "untyped_completion_repair",
      status,
      terminal_before_request: true,
      completion_continuation: null,
    };
  }

  if (terminalBeforeRequest) {
    return {
      schema_version: TODO_COMPLETION_FENCE_RESULT_SCHEMA,
      outcome: "replay",
      reason: "already_terminal",
      status,
      terminal_before_request: true,
      completion_continuation: continuation,
    };
  }

  return {
    schema_version: TODO_COMPLETION_FENCE_RESULT_SCHEMA,
    outcome: "continue",
    reason: "not_terminal",
    status,
    terminal_before_request: false,
    completion_continuation: continuation,
  };
}
