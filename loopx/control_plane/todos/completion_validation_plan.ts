import type { JsonObject } from "../effect_program.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
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
  // The typed fence admits only a bound upgrade of an already committed
  // RESULT. Closeout reuses that result's validation; it is not new work.
  if (fence.outcome === "replay" ||
      fence.reason === "same_turn_terminal_upgrade" ||
      fence.reason === "lifecycle_reentry_terminal_upgrade") {
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


/** Recover an admitted validation effect, never a new completion authority. */
export function terminalValidationRecovery(request: JsonObject, receipt: JsonObject): JsonObject {
  const input = requireJsonObject(request.terminal_input, "terminal validation request");
  const issued = requireJsonObject(request.execution_result, "issued validation effects");
  if (issued.status !== "execute_validation") throw new EffectRuntimeRequestError("Validation recovery requires native effect admission");
  const transient = new Set(["observed_at", "delivery_context", "validation_source_provider_revision",
    "validation_receipt", "goal_acceptance_validation_receipts", "result_validation_binding"]);
  const binding = canonicalAuthoritySha256({read_context_id: receipt.read_context_id,
    request: Object.fromEntries(Object.entries(input).filter(([key]) => !transient.has(key))),
    validation_effect: issued.validation_effect ?? null,
    acceptance_effects: issued.goal_acceptance_validation_effects ?? null,
    acceptance_source: issued.goal_acceptance_source_binding ?? null});
  const previous = receipt.terminal_validation_effect == null ? null
    : requireJsonObject(receipt.terminal_validation_effect, "terminal validation recovery record");
  const reject = (code: string): JsonObject => ({ok: false, error_code: code,
    error: "Retain the original validation effect and candidate; reconcile its outcome before retrying."});
  if (previous !== null && (previous.schema_version !== "terminal_validation_effect_v1" || previous.binding_sha256 !== binding)) {
    return reject("delivery_validation_binding_mismatch");
  }
  if (previous !== null && previous.status !== "pending" &&
      (!["passed", "failed"].includes(String(previous.status)) ||
       previous.updates_sha256 !== canonicalAuthoritySha256(previous.updates))) {
    return reject("delivery_validation_receipt_corrupt");
  }
  if (request.phase === "begin_terminal_validation") {
    if (previous?.status === "pending") return reject("delivery_validation_outcome_unknown");
    if (previous?.status === "passed") return {ok: true, effect: "replay", updates: previous.updates};
    return {ok: true, effect: "run", receipt: {...receipt, terminal_validation_effect: {
      schema_version: "terminal_validation_effect_v1", binding_sha256: binding, status: "pending"}}};
  }
  if (request.phase !== "finish_terminal_validation" || previous?.status !== "pending") {
    return reject("delivery_validation_outcome_unknown");
  }
  const updates = requireJsonObject(request.updates, "validation outcome updates");
  if (Object.keys(updates).some(key => !["validation_receipt", "goal_acceptance_source_binding", "goal_acceptance_validation_receipts"].includes(key))) {
    throw new EffectRuntimeRequestError("Validation outcome contains non-effect fields");
  }
  const outcomes: JsonObject[] = [];
  if (issued.validation_effect != null) outcomes.push(requireJsonObject(updates.validation_receipt, "caller validation outcome"));
  if (issued.goal_acceptance_validation_effects != null) {
    if (!Array.isArray(updates.goal_acceptance_validation_receipts)) throw new EffectRuntimeRequestError("Acceptance outcomes missing");
    for (const row of updates.goal_acceptance_validation_receipts) {
      outcomes.push(requireJsonObject(requireJsonObject(row, "criterion outcome").receipt, "criterion validation outcome"));
    }
  }
  if (!outcomes.length) throw new EffectRuntimeRequestError("Validation outcome missing");
  // A known failed check remains retryable under the existing completion rule.
  // Only an issued, fully successful effect group is recovered without running.
  const passed = outcomes.every(outcome => outcome.passed === true);
  return {ok: true, receipt: {...receipt, terminal_validation_effect: {
    schema_version: "terminal_validation_effect_v1", binding_sha256: binding,
    status: passed ? "passed" : "failed", updates, updates_sha256: canonicalAuthoritySha256(updates)}}};
}
