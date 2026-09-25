import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireJsonObject,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  evaluateTodoCompletionFence,
  TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
  type TodoCompletionProjectionSource,
} from "./completion_fence.ts";
import {normalizeTodoCompletionValidationDeclaration} from "./completion_validation_declaration.ts";

export const TODO_COMPLETION_VALIDATION_PLAN_REQUEST_SCHEMA =
  "loopx_todo_completion_validation_plan_request_v0";
export const TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA =
  "loopx_todo_completion_validation_plan_result_v0";

export type TodoCompletionValidationPlanResult =
  | (JsonObject & {
      schema_version: typeof TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA;
      effect: "skip";
      reason: "dry_run" | "no_declaration" | "terminal_replay";
    })
  | (JsonObject & {
      schema_version: typeof TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA;
      effect: "run";
      reason: "declared_validation";
      validation_command: string | null;
      validation_argv: readonly string[] | null;
      validation_label: string | null;
      validation_timeout_seconds: number | null;
    })
  | (JsonObject & {
      schema_version: typeof TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA;
      effect: "reject";
      reason: "invalid_declaration";
      status: "declaration_invalid";
      validation_label: string | null;
      summary: string;
    });

function projectionSource(value: unknown): TodoCompletionProjectionSource {
  return requireStringLiteral(
    value,
    ["materialized"] as const,
    "projection_source",
  );
}

function optionalOpaqueString(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function invalidDeclaration(
  summary: string,
  validationLabel: string | null,
): TodoCompletionValidationPlanResult {
  return {
    schema_version: TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA,
    effect: "reject",
    reason: "invalid_declaration",
    status: "declaration_invalid",
    validation_label: validationLabel,
    summary,
  };
}

export function evaluateTodoCompletionValidationPlan(
  value: unknown,
): TodoCompletionValidationPlanResult {
  const request = requireJsonObject(
    value,
    "todo.completion_validation_plan params",
  );
  if (request.schema_version !== TODO_COMPLETION_VALIDATION_PLAN_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo completion validation plan request schema mismatch");
  }
  const todo = requireJsonObject(
    request.todo,
    "todo.completion_validation_plan todo",
  );
  const dryRun = requireBoolean(request.dry_run, "dry_run");
  if (dryRun) {
    return {
      schema_version: TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA,
      effect: "skip",
      reason: "dry_run",
    };
  }

  const source = projectionSource(request.projection_source);
  const fence = evaluateTodoCompletionFence({
    schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: source,
    todo: {
      status: todo.status,
      no_followup: todo.no_followup,
      completion_continuation: todo.completion_continuation,
      completion_turn_key: todo.completion_turn_key,
      successor_todo_ids: todo.successor_todo_ids,
    },
    requested_no_followup: requireBoolean(
      request.requested_no_followup,
      "requested_no_followup",
    ),
    requested_completion_turn_key: optionalOpaqueString(
      request.requested_completion_turn_key,
      "requested_completion_turn_key",
    ),
    requested_completion_identity_source:
      request.requested_completion_identity_source,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
  });
  if (fence.outcome === "replay") {
    return {
      schema_version: TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA,
      effect: "skip",
      reason: "terminal_replay",
    };
  }

  const declaration = normalizeTodoCompletionValidationDeclaration(todo);
  const validationLabel = typeof todo.validation_label === "string" &&
      todo.validation_label !== ""
    ? todo.validation_label
    : null;
  if (!declaration.ok) {
    return invalidDeclaration(declaration.summary, validationLabel);
  }
  if (
    declaration.value.validation_command === null &&
    declaration.value.validation_command_argv === null
  ) {
    return {
      schema_version: TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA,
      effect: "skip",
      reason: "no_declaration",
    };
  }

  return {
    schema_version: TODO_COMPLETION_VALIDATION_PLAN_RESULT_SCHEMA,
    effect: "run",
    reason: "declared_validation",
    validation_command: declaration.value.validation_command,
    validation_argv: declaration.value.validation_command_argv,
    validation_label: declaration.value.validation_label,
    validation_timeout_seconds: declaration.value.validation_timeout_seconds,
  };
}
