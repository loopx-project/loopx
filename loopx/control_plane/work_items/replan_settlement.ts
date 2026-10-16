import type { JsonObject } from "../effect_program.ts";
import {
  REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
  REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_RESULT_SCHEMA,
  REPLAN_SETTLEMENT_REQUEST_SCHEMA as GENERATED_REPLAN_SETTLEMENT_REQUEST_SCHEMA,
  REPLAN_SETTLEMENT_RESULT_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import {
  localTodoCompletionIdentity,
} from "../todos/completion_fence.ts";

export const REPLAN_SETTLEMENT_REQUEST_SCHEMA =
  GENERATED_REPLAN_SETTLEMENT_REQUEST_SCHEMA;
export const REPLAN_SETTLEMENT_CONTRACT_SCHEMA =
  REPLAN_SETTLEMENT_RESULT_SCHEMA;
export const TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA =
  REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_REQUEST_SCHEMA;
export const TODO_LIFECYCLE_REENTRY_SCHEMA =
  REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_RESULT_SCHEMA;

interface TodoSettlementBinding extends JsonObject {
  kind: "todo";
  id: string;
  cli_argument: "--todo-id";
}

interface DirectReplanSettlementBinding extends JsonObject {
  kind: "autonomous_replan";
  id: string;
  cli_argument: "--replan-obligation-id";
}

interface TodoBoundSemanticObligation extends JsonObject {
  kind: "autonomous_replan";
  id: string;
  settlement_bound: false;
  discharge: "todo_bound_writeback";
}

interface DirectSemanticObligation extends JsonObject {
  kind: "autonomous_replan";
  id: string;
  settlement_bound: true;
  discharge: "direct_settlement";
}

interface TodoBoundReplanSettlementContract extends JsonObject {
  schema_version: typeof REPLAN_SETTLEMENT_CONTRACT_SCHEMA;
  single_binding_required: true;
  settlement_binding: TodoSettlementBinding;
  semantic_obligation: TodoBoundSemanticObligation;
}

interface DirectReplanSettlementContract extends JsonObject {
  schema_version: typeof REPLAN_SETTLEMENT_CONTRACT_SCHEMA;
  single_binding_required: true;
  settlement_binding: DirectReplanSettlementBinding;
  semantic_obligation: DirectSemanticObligation;
}

export type ReplanSettlementContract =
  | TodoBoundReplanSettlementContract
  | DirectReplanSettlementContract;

function shellArgument(value: string): string {
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(value)) return value;
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function argumentSuffix(values: readonly string[]): string {
  return values.length > 0
    ? ` ${values.map(shellArgument).join(" ")}`
    : "";
}

function lifecycleTrigger(value: unknown, index: number): JsonObject {
  const trigger = requireJsonObject(value, `triggers[${index}]`);
  if (trigger.kind !== "completed_advancement_without_successor") {
    throw new EffectRuntimeRequestError(`triggers[${index}].kind is unsupported`);
  }
  return {
    kind: trigger.kind,
    todo_id: requireNonEmptyString(
      trigger.todo_id,
      `triggers[${index}].todo_id`,
    ),
    completion_turn_key: optionalNonEmptyString(
      trigger.completion_turn_key,
      `triggers[${index}].completion_turn_key`,
    ),
  };
}

export function projectTodoLifecycleSettlementReentry(value: unknown): JsonObject {
  const request = requireJsonObject(
    value,
    "work_item.replan_settlement.reentry params",
  );
  if (request.schema_version !== TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo lifecycle reentry request schema mismatch");
  }
  const goalId = requireNonEmptyString(request.goal_id, "goal_id");
  const runtimeRoot = optionalNonEmptyString(request.runtime_root, "runtime_root");
  const commandPrefix = runtimeRoot === null
    ? "loopx"
    : `loopx --runtime-root ${shellArgument(runtimeRoot)}`;
  if (!Array.isArray(request.triggers) || request.triggers.length === 0) {
    throw new EffectRuntimeRequestError("triggers must be a non-empty array");
  }
  const triggers = request.triggers.slice(0, 3).map(lifecycleTrigger).map(
    (trigger) => {
      const todoId = String(trigger.todo_id);
      const persistedKey = trigger.completion_turn_key;
      const localIdentityKey = localTodoCompletionIdentity(goalId, todoId);
      const completionIdentityKey = typeof persistedKey === "string"
        ? persistedKey
        : localIdentityKey;
      return {
        kind: trigger.kind,
        todo_id: todoId,
        completion_turn_key: completionIdentityKey,
        completion_identity_source: completionIdentityKey === localIdentityKey
          ? "unscoped_completion"
          : "turn_settlement",
      };
    },
  );
  const lifecycleActorArgs = Array.isArray(request.lifecycle_actor_args)
    ? request.lifecycle_actor_args.map((item, index) =>
      requireNonEmptyString(item, `lifecycle_actor_args[${index}]`)
    )
    : [];
  const quotaScopedArgs = Array.isArray(request.quota_scoped_args)
    ? request.quota_scoped_args.map((item, index) =>
      requireNonEmptyString(item, `quota_scoped_args[${index}]`)
    )
    : [];
  const nextCliActions = triggers.map((trigger) => {
    const todoId = String(trigger.todo_id);
    const completionIdentityKey = String(trigger.completion_turn_key);
    const completionIdentityArgs = [
      "--completion-identity-key",
      completionIdentityKey,
    ];
    // Reentry closes continuation only; it must not replace completion prose.
    return (
      `when no real successor remains for completed Todo ${todoId}: ` +
      `${commandPrefix} todo complete --goal-id ${shellArgument(goalId)} ` +
      `--todo-id ${shellArgument(todoId)}` +
      argumentSuffix(completionIdentityArgs) +
      argumentSuffix(lifecycleActorArgs) +
      " --no-follow-up"
    );
  });
  nextCliActions.push(
    "otherwise link or create one real runnable successor for the exact " +
      "completed Todo; do not create lifecycle-only filler",
    `${commandPrefix} --format json quota should-run --goal-id ${shellArgument(goalId)}` +
      argumentSuffix(quotaScopedArgs),
  );
  return {
    schema_version: TODO_LIFECYCLE_REENTRY_SCHEMA,
    resolution_mode: "todo_lifecycle_settlement",
    triggers,
    next_cli_actions: nextCliActions,
  };
}

export function projectReplanSettlementContract(
  value: unknown,
): ReplanSettlementContract {
  const request = requireJsonObject(value, "work_item.replan_settlement params");
  if (request.schema_version !== REPLAN_SETTLEMENT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Replan settlement request schema mismatch");
  }
  const selectedTodoId = optionalNonEmptyString(
    request.selected_todo_id,
    "selected_todo_id",
  );
  const semanticObligationId = requireNonEmptyString(
    request.semantic_replan_obligation_id,
    "semantic_replan_obligation_id",
  );
  if (selectedTodoId !== null) {
    return {
      schema_version: REPLAN_SETTLEMENT_CONTRACT_SCHEMA,
      single_binding_required: true,
      settlement_binding: {
        kind: "todo",
        id: selectedTodoId,
        cli_argument: "--todo-id",
      },
      semantic_obligation: {
        kind: "autonomous_replan",
        id: semanticObligationId,
        settlement_bound: false,
        discharge: "todo_bound_writeback",
      },
    };
  }

  return {
    schema_version: REPLAN_SETTLEMENT_CONTRACT_SCHEMA,
    single_binding_required: true,
    settlement_binding: {
      kind: "autonomous_replan",
      id: semanticObligationId,
      cli_argument: "--replan-obligation-id",
    },
    semantic_obligation: {
      kind: "autonomous_replan",
      id: semanticObligationId,
      settlement_bound: true,
      discharge: "direct_settlement",
    },
  };
}
