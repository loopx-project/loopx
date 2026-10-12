/** Canonical Todo reads and projection confirmation share one provider snapshot. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {acceptanceWorkGuard, projectGoalAcceptance,
  projectGoalAcceptanceWorkGuards} from "../goals/acceptance_contract.ts";
import {authorityStoreSourceAuthority as sourceAuthorityFor} from "./authority_store.ts";
import {canonicalAuthoritySha256, hasExactAuthorityKeys, parseAuthorityCursor,
  requireAuthorityStoreId} from "./authority_store_codec.ts";
import {openRuntimeAuthorityStore as openRuntimeStore, requireLocalAuthorityRuntimeRoot as runtimeRoot,
  localAuthorityOpenFailure, type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";
import {CoordinationProjectionRead, indexCoordinationProjectionTodos, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA, LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA, LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA} from "./coordination_state_contract.generated.ts";
import {decodeProjectionReadback, confirmProjectionReadback} from "../todos/projection_delivery.ts";
import {completionTurnOperationId,
  COORDINATION_TODO_TERMINAL_LIFECYCLE_RECEIPT_SCHEMA} from "./todo_terminal_lifecycle.ts";

/** Shared admission for full and paged collection reads. Pagination changes
 * transport only; retained records, read-model validation and acceptance keep
 * this same owner. */
export function canonicalTodoCollection(head: JsonObject, goalId: string, includeLeases: boolean) {
  const read = new CoordinationProjectionRead(head, goalId);
  return {
    projection: read.todoIndex,
    todoReadModel: read.validateTodoReadModel(),
    leaseIndex: includeLeases ? read.coordinationIndex : null,
    acceptance: projectGoalAcceptance(head, goalId),
  };
}

const LOCAL_COORDINATION_OPERATION_RECEIPT_REQUEST_SCHEMA = "loopx_local_coordination_operation_receipt_request_v0";
const LOCAL_COORDINATION_OPERATION_RECEIPT_RESULT_SCHEMA = "loopx_local_coordination_operation_receipt_result_v0";
const LOCAL_COORDINATION_TODO_SOURCE_REQUEST_SCHEMA = "loopx_local_coordination_todo_source_request_v0";
const LOCAL_COORDINATION_TODO_SOURCE_RESULT_SCHEMA = "loopx_local_coordination_todo_source_result_v0";

/** Read the original canonical Todo projection, never a later provider head. */
export async function readLocalCoordinationTodoSource(
  value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "canonical_unavailable";
  const failure = (code: string): JsonObject => ({
    schema_version: LOCAL_COORDINATION_TODO_SOURCE_RESULT_SCHEMA,
    status: "failed", reason_code: code, source_authority: sourceAuthority,
    decision_read_from_provider: true, legacy_fallback_used: false,
  });
  const missing = (): JsonObject => ({
    schema_version: LOCAL_COORDINATION_TODO_SOURCE_RESULT_SCHEMA,
    status: "missing", reason_code: "todo_source_terminal_receipt_missing",
    source_authority: sourceAuthority, decision_read_from_provider: true,
    legacy_fallback_used: false,
  });
  try {
    const input = requireJsonObject(value, "historical Todo source request");
    if (!hasExactAuthorityKeys(input, ["schema_version", "runtime_root", "goal_id", "source"]) &&
        !hasExactAuthorityKeys(input, ["schema_version", "runtime_root", "goal_id", "terminal"])) {
      return failure("todo_source_request_invalid");
    }
    if (input.schema_version !== LOCAL_COORDINATION_TODO_SOURCE_REQUEST_SCHEMA ||
        (Object.hasOwn(input, "source") === Object.hasOwn(input, "terminal"))) {
      return failure("todo_source_request_invalid");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const store = await openRuntimeStore(root, goalId, dependencies);
    sourceAuthority = sourceAuthorityFor(store);
    const identity = await store.storeIdentity();
    if (identity.status !== "available") return failure("todo_source_store_unavailable");

    let cursor = "";
    let revision = "";
    let operationId: string | null = null;
    let terminalReceipt: JsonObject | null = null;
    let terminalTodoId: string | null = null;
    let terminalAgentId: string | null = null;
    let terminalTurnKey: string | null = null;
    if (Object.hasOwn(input, "source")) {
      const anchor = requireJsonObject(input.source, "Todo source anchor");
      if (!hasExactAuthorityKeys(anchor,
        ["source_authority", "store_identity", "provider_revision", "cursor"])) {
        return failure("todo_source_anchor_invalid");
      }
      if (requireAuthorityStoreId(anchor.source_authority, "source authority") !== sourceAuthority ||
          requireAuthorityStoreId(anchor.store_identity, "store identity") !== identity.store_identity) {
        return failure("todo_source_lineage_mismatch");
      }
      revision = requireAuthorityStoreId(anchor.provider_revision, "provider revision");
      cursor = requireAuthorityStoreId(anchor.cursor, "cursor");
    } else {
      const terminal = requireJsonObject(input.terminal, "terminal Todo source");
      if (!hasExactAuthorityKeys(terminal,
        ["source_authority", "store_identity", "todo_id", "agent_id", "completion_turn_key"])) {
        return failure("todo_source_terminal_invalid");
      }
      if (requireAuthorityStoreId(terminal.source_authority, "source authority") !== sourceAuthority ||
          requireAuthorityStoreId(terminal.store_identity, "store identity") !== identity.store_identity) {
        return failure("todo_source_lineage_mismatch");
      }
      terminalTodoId = requireAuthorityStoreId(terminal.todo_id, "todo id");
      terminalAgentId = requireAuthorityStoreId(terminal.agent_id, "agent id");
      terminalTurnKey = requireAuthorityStoreId(terminal.completion_turn_key, "completion turn key");
      for (const closeout of [true, false]) {
        const candidate = completionTurnOperationId({
          goal_id: goalId, todo_id: terminalTodoId, requested_completion_turn_key: terminalTurnKey,
        }, closeout);
        const observed = await store.readReceipt(candidate);
        if (observed.status === "missing") continue;
        if (observed.status !== "found" || observed.receipts.length !== 1) {
          return failure("todo_source_terminal_receipt_invalid");
        }
        const row = observed.receipts[0]!;
        if (row.schema_version !== COORDINATION_TODO_TERMINAL_LIFECYCLE_RECEIPT_SCHEMA ||
            row.operation_id !== candidate || row.goal_id !== goalId ||
            row.todo_id !== terminalTodoId || row.command !== "complete") {
          return failure("todo_source_terminal_receipt_invalid");
        }
        operationId = candidate;
        terminalReceipt = row;
        revision = observed.provider_revision;
        cursor = observed.cursor;
        break;
      }
      if (terminalReceipt === null) return missing();
    }
    let offset: bigint;
    try {
      offset = parseAuthorityCursor(cursor) - 1n;
    } catch {
      return failure("todo_source_anchor_invalid");
    }
    const page = await store.scanCommitted(offset === 0n ? null : offset.toString(), 1);
    if (page.status !== "page" || page.transactions.length !== 1) {
      return failure("todo_source_history_unavailable");
    }
    const transaction = page.transactions[0]!;
    if (transaction.cursor !== cursor || transaction.provider_revision !== revision ||
        (operationId !== null && transaction.operation_id !== operationId) ||
        (terminalReceipt !== null &&
          (transaction.receipts.length !== 1 ||
           canonicalAuthoritySha256(transaction.receipts[0]) !==
             canonicalAuthoritySha256(terminalReceipt)))) {
      return failure("todo_source_history_mismatch");
    }
    const after = await store.storeIdentity();
    if (after.status !== "available" || after.store_identity !== identity.store_identity) {
      return failure("todo_source_lineage_mismatch");
    }
    const {projection, todoReadModel, acceptance} = canonicalTodoCollection(
      transaction.projection, goalId, false,
    );
    let completion: JsonObject | null = null;
    if (terminalReceipt !== null) {
      const todo = projection.todos.get(terminalTodoId!);
      const result = terminalReceipt.result;
      if (todo?.status !== "done" || todo.no_followup !== true ||
          todo.completion_turn_key !== terminalTurnKey ||
          todo.last_actor_agent_id !== terminalAgentId ||
          typeof result !== "object" || result === null || Array.isArray(result) ||
          (result as JsonObject).completion_identity_key !== terminalTurnKey ||
          typeof (result as JsonObject).completion_receipt_id !== "string" ||
          (result as JsonObject).completion_receipt_id !== todo.completion_receipt_id ||
          typeof (result as JsonObject).completed_at !== "string" ||
          (result as JsonObject).completed_at !== todo.completed_at) {
        return failure("todo_source_terminal_identity_mismatch");
      }
      completion = {
        todo_id: terminalTodoId, agent_id: terminalAgentId,
        completion_turn_key: terminalTurnKey,
        completion_receipt_id: todo.completion_receipt_id,
        completed_at: todo.completed_at,
      };
    }
    const source = {
      source_authority: sourceAuthority, store_identity: identity.store_identity,
      provider_revision: revision, cursor, ...(operationId === null ? {} : {operation_id: operationId}),
    };
    return {
      schema_version: LOCAL_COORDINATION_TODO_SOURCE_RESULT_SCHEMA,
      status: "loaded", goal_id: goalId, source,
      ...(completion === null ? {} : {completion}),
      provider_revision: revision, cursor, store_identity: identity.store_identity,
      todos: projection.todo_ids.map((todoId) => projection.todos.get(todoId)!),
      todo_ids: projection.todo_ids, todo_read_model: todoReadModel,
      ...(acceptance.enabled !== true ? {} : {
        goal_acceptance_contract: acceptance,
        goal_acceptance_work_guards: projectGoalAcceptanceWorkGuards(
          transaction.projection, goalId, projection.todo_ids),
      }),
      source_authority: sourceAuthority, decision_read_from_provider: true,
      legacy_fallback_used: false,
    };
  } catch (error) {
    return {...failure("todo_source_read_failed"), ...localAuthorityOpenFailure(error)};
  }
}

/** Exact historical operation readback. It grants no current lease or retry authority. */
export async function readLocalCoordinationOperationReceipt(
  value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "canonical_unavailable";
  try {
    const input = requireJsonObject(value, "local coordination operation receipt request");
    if (input.schema_version !== LOCAL_COORDINATION_OPERATION_RECEIPT_REQUEST_SCHEMA) {
      throw new Error("local coordination operation receipt request schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const operationId = requireAuthorityStoreId(input.operation_id, "operation id");
    const store = await openRuntimeStore(root, goalId, dependencies);
    sourceAuthority = sourceAuthorityFor(store);
    const receipt = await store.readReceipt(operationId);
    return {schema_version: LOCAL_COORDINATION_OPERATION_RECEIPT_RESULT_SCHEMA,
      goal_id: goalId, operation_id: operationId, ...receipt,
      source_authority: sourceAuthority, decision_read_from_provider: true,
      legacy_fallback_used: false};
  } catch (error) {
    return {schema_version: LOCAL_COORDINATION_OPERATION_RECEIPT_RESULT_SCHEMA,
      status: "failed", reason_code: "invalid_local_coordination_operation_receipt_request",
      reason: error instanceof Error ? error.message : "invalid operation receipt request",
      source_authority: sourceAuthority, decision_read_from_provider: false,
      legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
  }
}

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
    const {projection, todoReadModel, leaseIndex, acceptance} = canonicalTodoCollection(
      head.head, goalId, input.include_leases === true,
    );
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
