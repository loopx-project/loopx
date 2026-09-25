import type { JsonObject } from "../effect_program.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {
  effectRuntimeErrorPayload,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  decodeTodoCompletionFenceRequest,
  evaluateTodoCompletionFence,
  localTodoCompletionIdentity,
  type TodoCompletionFenceResult,
  type TodoCompletionIdentitySource,
  type TodoCompletionProjectionSource,
  TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
} from "./completion_fence.ts";
import {
  buildTodoCompletionMetadataUpdates,
  selectTodoCompletionState,
  type TodoCompletionRecovery,
  type TodoCompletionContinuation,
  TODO_COMPLETION_STATE_REQUEST_SCHEMA,
} from "./completion_state.ts";
import {
  evaluateTodoCompletionValidationPlan,
  type TodoCompletionValidationPlanResult,
  TODO_COMPLETION_VALIDATION_PLAN_REQUEST_SCHEMA,
} from "./completion_validation_plan.ts";
import {
  resolveTodoCompletionPolicy,
  type TodoCompletionPolicyResult,
} from "./completion_policy.ts";

export const TODO_COMPLETION_TRANSACTION_REQUEST_SCHEMA =
  "loopx_todo_completion_transaction_v0";
export const TODO_COMPLETION_TRANSACTION_RESULT_SCHEMA =
  "loopx_todo_completion_transaction_result_v0";
export const TODO_COMPLETION_POLICY_FAILURE_SCHEMA =
  "loopx_todo_completion_policy_failure_v0";
const CALLER_VALIDATION_RECEIPT_SCHEMA = "issue_fix_validation_command_v0";

const PROJECTION_SOURCES = ["materialized"] as const;
const COMPLETION_IDENTITY_SOURCES = [
  "turn_settlement",
  "unscoped_completion",
  "lifecycle_reentry",
] as const;

interface CallerValidationReceipt extends JsonObject {
  schema_version: typeof CALLER_VALIDATION_RECEIPT_SCHEMA;
  command_label: string;
  exit_code: number | null;
  passed: boolean;
  status: string | null;
  summary: string | null;
  stdout_captured: false;
  stderr_captured: false;
  local_path_captured: false;
  validation_declaration_sha256?: string;
}

interface CompletionStateProjection extends JsonObject {
  continuation: TodoCompletionContinuation;
  recovery: TodoCompletionRecovery | null;
}

interface CompletionTransactionBase extends JsonObject {
  schema_version: typeof TODO_COMPLETION_TRANSACTION_RESULT_SCHEMA;
  completion_identity_key: string | null;
  completion_identity_source: TodoCompletionIdentitySource | null;
  fence: TodoCompletionFenceResult;
}

export interface TodoCompletionValidationEffect extends JsonObject {
  kind: "caller_validation";
  validation_command: string | null;
  validation_argv: readonly string[] | null;
  validation_label: string | null;
  validation_timeout_seconds: number | null;
  task_repository: string | null;
  validation_declaration_sha256?: string;
}

export interface TodoCompletionExecuteValidation
  extends CompletionTransactionBase {
  decision: "execute_validation";
  validation_effect: TodoCompletionValidationEffect;
}

interface TodoCompletionSettlement extends CompletionTransactionBase {
  completion_state: CompletionStateProjection;
  metadata_updates: JsonObject;
  validation_receipt: CallerValidationReceipt | null;
}

export interface TodoCompletionCommit extends TodoCompletionSettlement {
  decision: "commit";
  completion_policy?: TodoCompletionPolicyResult;
}

export interface TodoCompletionPolicyFailure extends JsonObject {
  schema_version: typeof TODO_COMPLETION_POLICY_FAILURE_SCHEMA;
  kind: "completion_policy_rejected";
  diagnostic_code: string;
  summary: string;
}

export interface TodoCompletionPolicyReject extends TodoCompletionSettlement {
  decision: "policy_reject";
  completion_policy_failure: TodoCompletionPolicyFailure;
}

export interface TodoCompletionReplay extends CompletionTransactionBase {
  decision: "replay";
}

export interface TodoCompletionReject extends CompletionTransactionBase {
  decision: "reject";
  failure: JsonObject & {
    kind: "validation_declaration_invalid" | "validation_failed";
    summary: string;
    validation_receipt: CallerValidationReceipt;
  };
}

export type TodoCompletionTransactionResult =
  | TodoCompletionExecuteValidation
  | TodoCompletionCommit
  | TodoCompletionPolicyReject
  | TodoCompletionReplay
  | TodoCompletionReject;

interface DecodedTransactionRequest {
  goal_id: string;
  todo_id: string;
  projection_source: TodoCompletionProjectionSource;
  todo: JsonObject;
  requested_no_followup: boolean;
  requested_completion_turn_key: string | null;
  requested_completion_identity_source: TodoCompletionIdentitySource | null;
  requested_has_successor: boolean;
  dry_run: boolean;
  validation_receipt: CallerValidationReceipt | null;
  completion_policy_request: JsonObject | null;
}

function optionalIdentitySource(
  value: unknown,
): TodoCompletionIdentitySource | null {
  if (value === null || value === undefined || value === "") return null;
  return requireStringLiteral(
    value,
    COMPLETION_IDENTITY_SOURCES,
    "requested_completion_identity_source",
  );
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  return requireInteger(value, label);
}

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function optionalDigest(value: unknown, label: string): string | null {
  const normalized = optionalString(value, label);
  if (normalized !== null && !/^[a-f0-9]{64}$/u.test(normalized)) {
    throw new EffectRuntimeRequestError(`${label} must be a SHA-256 digest or null`);
  }
  return normalized;
}

function requireFalse(value: unknown, label: string): false {
  if (requireBoolean(value, label) !== false) {
    throw new EffectRuntimeRequestError(`${label} must be false`);
  }
  return false;
}

function decodeValidationReceipt(
  value: unknown,
): CallerValidationReceipt | null {
  if (value === null || value === undefined) return null;
  const receipt = requireJsonObject(value, "validation_receipt");
  const schemaVersion = requireStringLiteral(
    receipt.schema_version,
    [CALLER_VALIDATION_RECEIPT_SCHEMA] as const,
    "validation_receipt.schema_version",
  );
  const exitCode = optionalInteger(
    receipt.exit_code,
    "validation_receipt.exit_code",
  );
  const passed = requireBoolean(
    receipt.passed,
    "validation_receipt.passed",
  );
  if ((passed && exitCode !== 0) || (!passed && exitCode === 0)) {
    throw new EffectRuntimeRequestError(
      "validation_receipt exit_code and passed must describe the same outcome",
    );
  }
  return {
    schema_version: schemaVersion,
    command_label: requireNonEmptyString(
      receipt.command_label,
      "validation_receipt.command_label",
    ),
    exit_code: exitCode,
    passed,
    status: optionalString(receipt.status, "validation_receipt.status"),
    summary: optionalString(receipt.summary, "validation_receipt.summary"),
    stdout_captured: requireFalse(
      receipt.stdout_captured,
      "validation_receipt.stdout_captured",
    ),
    stderr_captured: requireFalse(
      receipt.stderr_captured,
      "validation_receipt.stderr_captured",
    ),
    local_path_captured: requireFalse(
      receipt.local_path_captured,
      "validation_receipt.local_path_captured",
    ),
    ...(optionalDigest(
      receipt.validation_declaration_sha256,
      "validation_receipt.validation_declaration_sha256",
    ) === null ? {} : {validation_declaration_sha256: receipt.validation_declaration_sha256 as string}),
  };
}

function decodeRequest(value: unknown): DecodedTransactionRequest {
  const request = requireJsonObject(value, "todo.completion transaction");
  requireStringLiteral(
    request.schema_version,
    [TODO_COMPLETION_TRANSACTION_REQUEST_SCHEMA] as const,
    "schema_version",
  );
  return {
    goal_id: requireNonEmptyString(request.goal_id, "goal_id"),
    todo_id: requireNonEmptyString(request.todo_id, "todo_id"),
    projection_source: requireStringLiteral(
      request.projection_source,
      PROJECTION_SOURCES,
      "projection_source",
    ),
    todo: requireJsonObject(request.todo, "todo"),
    requested_no_followup: requireBoolean(
      request.requested_no_followup,
      "requested_no_followup",
    ),
    requested_completion_turn_key: optionalNonEmptyString(
      request.requested_completion_turn_key,
      "requested_completion_turn_key",
    ),
    requested_completion_identity_source: optionalIdentitySource(
      request.requested_completion_identity_source,
    ),
    requested_has_successor: requireBoolean(
      request.requested_has_successor,
      "requested_has_successor",
    ),
    dry_run: requireBoolean(request.dry_run, "dry_run"),
    validation_receipt: decodeValidationReceipt(request.validation_receipt),
    completion_policy_request:
      request.completion_policy_request === null ||
        request.completion_policy_request === undefined
        ? null
        : requireJsonObject(
          request.completion_policy_request,
          "completion_policy_request",
        ),
  };
}

function completionIdentity(request: DecodedTransactionRequest): {
  key: string | null;
  source: TodoCompletionIdentitySource | null;
} {
  const status = String(request.todo.status ?? "").trim().toLowerCase();
  if (request.requested_completion_turn_key !== null || status === "done") {
    return {
      key: request.requested_completion_turn_key,
      source: request.requested_completion_identity_source,
    };
  }
  return {
    key: localTodoCompletionIdentity(request.goal_id, request.todo_id),
    source: "unscoped_completion",
  };
}

function declarationReceipt(
  plan: Extract<TodoCompletionValidationPlanResult, { effect: "reject" }>,
): CallerValidationReceipt {
  return {
    schema_version: CALLER_VALIDATION_RECEIPT_SCHEMA,
    command_label: plan.validation_label ?? "todo completion validation",
    exit_code: null,
    passed: false,
    status: plan.status,
    summary: plan.summary,
    stdout_captured: false,
    stderr_captured: false,
    local_path_captured: false,
  };
}

function baseResult(
  identity: ReturnType<typeof completionIdentity>,
  fence: TodoCompletionFenceResult,
): CompletionTransactionBase {
  return {
    schema_version: TODO_COMPLETION_TRANSACTION_RESULT_SCHEMA,
    completion_identity_key: identity.key,
    completion_identity_source: identity.source,
    fence,
  };
}

function evaluateCompletionPolicy(
  request: JsonObject | null,
):
  | { outcome: "not_requested" }
  | { outcome: "accepted"; policy: TodoCompletionPolicyResult }
  | { outcome: "rejected"; failure: TodoCompletionPolicyFailure } {
  if (request === null) return { outcome: "not_requested" };
  try {
    return {
      outcome: "accepted",
      policy: resolveTodoCompletionPolicy(request),
    };
  } catch (error) {
    if (!(error instanceof EffectRuntimeRequestError)) throw error;
    const failure = effectRuntimeErrorPayload(error);
    return {
      outcome: "rejected",
      failure: {
        schema_version: TODO_COMPLETION_POLICY_FAILURE_SCHEMA,
        kind: "completion_policy_rejected",
        diagnostic_code: failure.code,
        summary: failure.message,
      },
    };
  }
}

/**
 * Reduce one Todo completion admission/settlement transaction.
 *
 * A completion without a declared external validation reaches `commit` or
 * `replay` in one reduction. A declared command yields one typed validation
 * effect; the caller returns its privacy-safe receipt for the final reduction.
 */
export function reduceTodoCompletionTransaction(
  value: unknown,
): TodoCompletionTransactionResult {
  const request = decodeRequest(value);
  const identity = completionIdentity(request);
  const fenceRequest = decodeTodoCompletionFenceRequest({
    schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: request.projection_source,
    todo: request.todo,
    requested_no_followup: request.requested_no_followup,
    requested_completion_turn_key: identity.key,
    requested_completion_identity_source: identity.source,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
  });
  const validationPlan = evaluateTodoCompletionValidationPlan({
    schema_version: TODO_COMPLETION_VALIDATION_PLAN_REQUEST_SCHEMA,
    projection_source: request.projection_source,
    todo: request.todo,
    requested_no_followup: request.requested_no_followup,
    requested_completion_turn_key: identity.key,
    requested_completion_identity_source: identity.source,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    dry_run: request.dry_run,
  });
  const fence = evaluateTodoCompletionFence(fenceRequest);
  const base = baseResult(identity, fence);

  if (fence.outcome === "replay") {
    if (request.validation_receipt !== null) {
      throw new EffectRuntimeRequestError("terminal replay must not carry a validation_receipt");
    }
    return { ...base, decision: "replay" };
  }

  if (validationPlan.effect === "reject") {
    const receipt = declarationReceipt(validationPlan);
    return {
      ...base,
      decision: "reject",
      failure: {
        kind: "validation_declaration_invalid",
        summary: validationPlan.summary,
        validation_receipt: receipt,
      },
    };
  }

  if (validationPlan.effect === "run") {
    const validationDeclarationSha256 = canonicalAuthoritySha256({
      validation_command: validationPlan.validation_command,
      validation_command_argv: validationPlan.validation_argv,
      validation_label: validationPlan.validation_label,
      validation_timeout_seconds: validationPlan.validation_timeout_seconds,
    });
    const validationRevision = request.todo.completion_validation_revision;
    const requiresDigest = Number.isSafeInteger(validationRevision) &&
      Number(validationRevision) > 0;
    if (request.validation_receipt === null) {
      return {
        ...base,
        decision: "execute_validation",
        validation_effect: {
          kind: "caller_validation",
          validation_command: validationPlan.validation_command,
          validation_argv: validationPlan.validation_argv,
          validation_label: validationPlan.validation_label,
          validation_timeout_seconds:
            validationPlan.validation_timeout_seconds,
          task_repository: optionalNonEmptyString(
            request.todo.task_repository,
            "todo.task_repository",
          ),
          ...(requiresDigest
            ? {validation_declaration_sha256: validationDeclarationSha256}
            : {}),
        },
      };
    }
    const expectedLabel = validationPlan.validation_label ??
      "todo completion validation";
    if (request.validation_receipt.command_label !== expectedLabel) {
      throw new EffectRuntimeRequestError(
        "validation_receipt.command_label does not match the authorized effect",
      );
    }
    if (requiresDigest &&
        request.validation_receipt.validation_declaration_sha256 !==
          validationDeclarationSha256) {
      throw new EffectRuntimeRequestError(
        "validation_receipt does not match the current validation declaration",
      );
    }
    if (!request.validation_receipt.passed) {
      return {
        ...base,
        decision: "reject",
        failure: {
          kind: "validation_failed",
          summary:
            request.validation_receipt.summary ??
            `${request.validation_receipt.command_label} did not pass`,
          validation_receipt: request.validation_receipt,
        },
      };
    }
  } else if (request.validation_receipt !== null) {
    throw new EffectRuntimeRequestError(
      "validation_receipt requires a declared completion validation effect",
    );
  }

  const completionStateResult = selectTodoCompletionState({
    schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
    todo: request.todo,
    requested_no_followup: request.requested_no_followup,
    has_successor:
      request.requested_has_successor ||
      fenceRequest.todo.successor_todo_ids.length > 0,
    completion_identity_source: identity.source,
  });
  const metadataResult = buildTodoCompletionMetadataUpdates({
    schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
    block: request.todo,
    target_status: "done",
    normalized_status: "done",
    completion_continuation: completionStateResult.continuation,
    completion_recovery: completionStateResult.recovery,
    no_followup: request.requested_no_followup,
    successor_todo_ids: null,
  });
  const updates = requireJsonObject(
    metadataResult.updates,
    "completion metadata updates",
  );
  const completionPolicy = evaluateCompletionPolicy(
    request.completion_policy_request,
  );
  const settlement = {
    ...base,
    completion_state: {
      continuation: completionStateResult.continuation,
      recovery: completionStateResult.recovery,
    },
    metadata_updates: updates,
    validation_receipt: request.validation_receipt,
  };
  if (completionPolicy.outcome === "rejected") {
    return {
      ...settlement,
      decision: "policy_reject",
      completion_policy_failure: completionPolicy.failure,
    };
  }
  return {
    ...settlement,
    decision: "commit",
    ...(completionPolicy.outcome === "not_requested"
      ? {}
      : { completion_policy: completionPolicy.policy }),
  };
}
