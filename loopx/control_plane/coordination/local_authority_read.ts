/** Canonical Todo reads and projection confirmation share one provider snapshot. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {acceptanceWorkGuard, projectGoalAcceptance,
  projectGoalAcceptanceWorkGuards} from "../goals/acceptance_contract.ts";
import {authorityStoreSourceAuthority as sourceAuthorityFor} from "./authority_store.ts";
import {requireAuthorityStoreId} from "./authority_store_codec.ts";
import {openRuntimeAuthorityStore as openRuntimeStore, requireLocalAuthorityRuntimeRoot as runtimeRoot,
  localAuthorityOpenFailure, type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";
import {indexCoordinationProjection, indexCoordinationProjectionTodos, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA, LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA, LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA} from "./coordination_state_contract.generated.ts";
import {decodeProjectionReadback, confirmProjectionReadback} from "../todos/projection_delivery.ts";

/** Provider-first exact Todo read. Missing/unavailable state never falls back. */
export async function readLocalCoordinationTodo(
  value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  try {
    const input = requireJsonObject(value, "local coordination Todo read request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA) {
      throw new Error("local coordination Todo read request schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const todoId = requireAuthorityStoreId(input.todo_id, "todo id");
    const store = await openRuntimeStore(root, goalId, dependencies);
    sourceAuthority = sourceAuthorityFor(store);
    const head = await store.loadAuthority();
    if (head.status !== "loaded") {
      return {
        schema_version: LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
        ...head,
        source_authority: sourceAuthority,
        decision_read_from_provider: true,
        legacy_fallback_used: false,
      };
    }
    const projection = indexCoordinationProjectionTodos(head.head, goalId);
    validateCoordinationTodoReadModel(head.head, goalId);
    const todo = projection.todos.get(todoId);
    const acceptance = todo === undefined ? null : acceptanceWorkGuard(head.head, goalId, todoId);
    return {
      schema_version: LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
      status: todo === undefined ? "missing" : "found",
      todo_id: todoId,
      ...(todo === undefined ? {} : { todo }),
      ...(acceptance === null ? {} : {goal_acceptance_guard: acceptance}),
      todo_ids: projection.todo_ids,
      provider_revision: head.provider_revision,
      cursor: head.cursor,
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
    };
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_local_coordination_todo_read_request",
      reason: error instanceof Error ? error.message : "invalid Todo read request",
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Provider-first Todo collection read. Missing/unavailable state never falls back. */
export async function listLocalCoordinationTodos(
  value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  try {
    const input = requireJsonObject(value, "local coordination Todo list request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA) {
      throw new Error("local coordination Todo list request schema mismatch");
    }
    const readback = input.projection_readback === undefined ? null : decodeProjectionReadback(input.projection_readback);
    if (input.include_leases !== undefined && typeof input.include_leases !== "boolean") {
      throw new Error("include_leases must be a boolean");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const store = await openRuntimeStore(root, goalId, dependencies);
    sourceAuthority = sourceAuthorityFor(store);
    const head = await store.loadAuthority();
    if (head.status !== "loaded") {
      return {
        schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
        ...head,
        source_authority: sourceAuthority,
        decision_read_from_provider: true,
        legacy_fallback_used: false,
      };
    }
    const projection = indexCoordinationProjectionTodos(head.head, goalId);
    const todoReadModel = validateCoordinationTodoReadModel(head.head, goalId);
    const leaseIndex = input.include_leases === true
      ? indexCoordinationProjection(head.head, goalId) : null;
    const acceptance = projectGoalAcceptance(head.head, goalId);
    return {
      schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
      status: "loaded",
      ...(readback === null ? {} : {projection_readback: confirmProjectionReadback(readback, head.provider_revision)}),
      todos: projection.todo_ids.map((todoId) => projection.todos.get(todoId)!),
      todo_ids: projection.todo_ids,
      todo_read_model: todoReadModel,
      ...(acceptance.enabled !== true ? {} : {goal_acceptance_contract: acceptance,
        goal_acceptance_work_guards: projectGoalAcceptanceWorkGuards(head.head, goalId, projection.todo_ids)}),
      ...(leaseIndex === null ? {} : {
        leases: leaseIndex.lease_todo_ids.map((id) => leaseIndex.leases.get(id)!),
        handoff_mode: head.head.handoff_mode ?? "legacy",
      }),
      provider_revision: head.provider_revision,
      cursor: head.cursor,
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
    };
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_local_coordination_todo_list_request",
      reason: error instanceof Error ? error.message : "invalid Todo list request",
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}
