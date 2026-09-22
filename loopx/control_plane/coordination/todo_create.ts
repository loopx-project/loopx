import {planTodoPriority} from "../todos/priority.ts";
import {AUTHORITY_SOURCE_CHANGED, uncheckedAuthoritySource, type AuthoritySourceCheck} from "./authority_source.ts";
import type { JsonObject } from "../effect_program.ts";
import {CoordinationCommandReceipt} from "./command_receipt.ts";
import type { AuthorityStore } from "./authority_store.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {normalizeRegisteredTodoAgents, normalizeTodoAgent} from "./todo_agents.ts";
import {
  TODO_DOMAIN_ITEM_SCHEMA,
  canonicalTodoDomainRecord,
} from "./coordination_state_contract.ts";
import {materializeTodoRecordForSchema} from "./todo_presentation.ts";
import {
  indexCoordinationProjection,
  prepareCoordinationProjectionCommit,
  validateCoordinationTodoReadModel,
} from "./coordination_projection.ts";

export const COORDINATION_TODO_CREATE_RESULT_SCHEMA =
  "loopx_coordination_todo_create_result_v0";
export const COORDINATION_TODO_CREATE_RECEIPT_SCHEMA =
  "loopx_coordination_todo_create_receipt_v0";

export interface CoordinationTodoCreateInput {
  readonly goal_id: string;
  readonly todo: JsonObject;
  readonly actor_agent_id: string | null;
  readonly registered_agents: readonly string[];
  readonly operation_id: string;
  readonly dry_run: boolean;
  readonly now: Date;
}

export type CoordinationTodoCreateResult = JsonObject & {
  readonly schema_version: typeof COORDINATION_TODO_CREATE_RESULT_SCHEMA;
};

function failure(code: string, reason: string, detail: JsonObject = {}): CoordinationTodoCreateResult {
  return {
    ...detail,
    schema_version: COORDINATION_TODO_CREATE_RESULT_SCHEMA,
    status: "failed",
    reason_code: code,
    reason,
  };
}

function createReceipt(input: CoordinationTodoCreateInput, requestSha: string) {
  return new CoordinationCommandReceipt({result_schema: COORDINATION_TODO_CREATE_RESULT_SCHEMA,
    identity: {schema_version: COORDINATION_TODO_CREATE_RECEIPT_SCHEMA,
      operation_id: input.operation_id, goal_id: input.goal_id, todo_id: String(input.todo.todo_id),
      request_sha256: requestSha}, failure,
    decode(original) {
      const todo = canonicalAuthorityObject(original.todo, "created Todo receipt");
      if (todo.todo_id !== input.todo.todo_id) throw new AuthorityStoreProtocolError("created Todo receipt identity mismatch");
      return {fields: {todo_id: original.todo_id, todo, original_receipt: original}, changed: true};
    }});
}

function normalizeCreateInput(rawInput: CoordinationTodoCreateInput): CoordinationTodoCreateInput {
  const todo = canonicalTodoDomainRecord(rawInput.todo, "Todo create record");
  const input = {
    ...rawInput,
    goal_id: requireAuthorityStoreId(rawInput.goal_id, "goal id"),
    operation_id: requireAuthorityStoreId(rawInput.operation_id, "operation id"),
    todo,
    actor_agent_id: rawInput.actor_agent_id === null
      ? null : normalizeTodoAgent(rawInput.actor_agent_id, "actor_agent_id"),
    registered_agents: normalizeRegisteredTodoAgents(rawInput.registered_agents),
  };
  if (typeof input.dry_run !== "boolean") {
    throw new AuthorityStoreProtocolError("dry_run must be a boolean");
  }
  if (!(input.now instanceof Date) || Number.isNaN(input.now.valueOf())) {
    throw new AuthorityStoreProtocolError("now must be a valid Date");
  }
  if (todo.status === "done" || todo.archive_state !== "active") {
    throw new AuthorityStoreProtocolError(
      "Todo create requires an active record that is not already done",
    );
  }
  if (todo.claimed_by !== undefined) {
    const owner = normalizeTodoAgent(todo.claimed_by, "todo.claimed_by");
    if (!input.registered_agents.includes(owner)) {
      throw new AuthorityStoreProtocolError("Todo claim owner is not registered");
    }
    if (input.actor_agent_id !== null && input.actor_agent_id !== owner) {
      throw new AuthorityStoreProtocolError("claimed Todo create requires actor to match owner");
    }
  }
  if (input.actor_agent_id !== null && !input.registered_agents.includes(input.actor_agent_id)) {
    throw new AuthorityStoreProtocolError("actor_agent_id is not registered for this goal");
  }
  return input;
}

function semanticDuplicateResult(
  todo: JsonObject,
  duplicate: JsonObject,
  providerRevision: unknown,
  cursor: unknown,
): CoordinationTodoCreateResult {
  const ignored = new Set(["schema_version", "todo_id", "created_by", "last_actor_agent_id", "updated_at"]);
  const mismatch = Object.entries(todo).find(([field, value]) =>
    !ignored.has(field) && canonicalAuthoritySha256(value) !== canonicalAuthoritySha256(duplicate[field])
  );
  if (mismatch !== undefined) {
    return failure(
      "todo_semantic_duplicate_conflict",
      `an active Todo with the same role/text has different ${mismatch[0]}; use Todo update`,
      {todo_id: duplicate.todo_id},
    );
  }
  return {
    schema_version: COORDINATION_TODO_CREATE_RESULT_SCHEMA,
    status: "no_change",
    changed: false,
    todo_id: duplicate.todo_id,
    todo: duplicate,
    provider_revision: providerRevision,
    cursor,
  };
}

function createCandidate(
  input: CoordinationTodoCreateInput,
  readModelSchema: unknown,
): JsonObject {
  const priority = Object.hasOwn(input.todo, "priority")
    ? planTodoPriority({}, {text: input.todo.text, priority: input.todo.priority}) : {};
  const candidate: JsonObject = {
    ...input.todo,
    ...priority,
    schema_version: TODO_DOMAIN_ITEM_SCHEMA,
    created_by: input.actor_agent_id,
    last_actor_agent_id: input.actor_agent_id,
    updated_at: input.now.toISOString().replace(/\.\d{3}Z$/u, "Z"),
  };
  if (priority.priority === null) { delete candidate.priority; delete candidate.title; }
  const domainCreated = canonicalTodoDomainRecord(candidate, "created Todo");
  return materializeTodoRecordForSchema(domainCreated, readModelSchema, "created Todo");
}

/** In-process create planning for a caller-owned canonical transaction. Never
 * commits or authorizes the enclosing operation; single create and Monitor
 * batches share record admission, attribution and semantic duplicate rules. */
export function planCoordinationTodoCreate(
  rawInput: CoordinationTodoCreateInput,
  todos: ReadonlyMap<string, JsonObject>,
  readModelSchema: unknown,
  identity: "role_text" | "operation_lane" = "role_text",
): CoordinationTodoCreateResult {
  const input = normalizeCreateInput(rawInput);
  const candidate = createCandidate(input, readModelSchema);
  const duplicate = identity === "role_text" ? [...todos.values()].find((todo) =>
    todo.role === input.todo.role && todo.archive_state === "active" &&
    todo.status !== "done" && todo.status !== "deferred" && todo.text === candidate.text) : undefined;
  if (identity === "role_text" && duplicate !== undefined) return semanticDuplicateResult(candidate, duplicate, null, null);
  if (todos.has(String(input.todo.todo_id))) {
    return failure("todo_already_exists", "Todo id already exists in canonical authority", {todo_id: input.todo.todo_id});
  }
  return {schema_version: COORDINATION_TODO_CREATE_RESULT_SCHEMA, status: "planned",
    changed: true, todo_id: input.todo.todo_id, todo: candidate};
}

async function commitCreate(
  store: AuthorityStore,
  input: CoordinationTodoCreateInput,
  requestSha: string,
  head: Extract<Awaited<ReturnType<AuthorityStore["loadAuthority"]>>, {status: "loaded"}>,
  created: JsonObject,
  todoId: string,
): Promise<CoordinationTodoCreateResult> {
  const commit = prepareCoordinationProjectionCommit({
    goal_id: input.goal_id,
    operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision,
    projection: head.head,
    mutations: [{kind: "todo_upsert", todo: created}],
  });
  commit.receipts = [{
    schema_version: COORDINATION_TODO_CREATE_RECEIPT_SCHEMA,
    operation_id: input.operation_id,
    goal_id: input.goal_id,
    request_sha256: requestSha,
    todo_id: todoId,
    todo: created,
  }];
  return createReceipt(input, requestSha).commit(store, commit);
}

/** Create one canonical work item through the provider transaction and outbox. */
export async function executeCoordinationTodoCreate(
  store: AuthorityStore,
  rawInput: CoordinationTodoCreateInput,
  authoritySourcesCurrent: AuthoritySourceCheck = uncheckedAuthoritySource,
): Promise<CoordinationTodoCreateResult> {
  let input: CoordinationTodoCreateInput;
  try {
    input = normalizeCreateInput(rawInput);
  } catch (error) {
    return failure(
      "invalid_coordination_todo_create",
      error instanceof Error ? error.message : "invalid Todo create",
    );
  }

  const requestSha = canonicalAuthoritySha256({
    goal_id: input.goal_id,
    todo: input.todo,
    actor_agent_id: input.actor_agent_id,
    dry_run: input.dry_run,
  });
  const receipt = createReceipt(input, requestSha);
  const existing = await receipt.read(store);
  if (existing !== null) return existing;

  if (!await authoritySourcesCurrent()) return failure(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  const observation = await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") {
    return {schema_version: COORDINATION_TODO_CREATE_RESULT_SCHEMA, ...head};
  }
  let projection: ReturnType<typeof indexCoordinationProjection>;
  try {
    projection = indexCoordinationProjection(head.head, input.goal_id);
    validateCoordinationTodoReadModel(head.head, input.goal_id);
  } catch (error) {
    return failure(
      "invalid_coordination_projection",
      error instanceof Error ? error.message : "invalid coordination projection",
    );
  }
  const todoId = requireAuthorityStoreId(input.todo.todo_id, "todo id");
  const readModel = canonicalAuthorityObject(head.head.todo_read_model, "Todo read model");
  const plan = planCoordinationTodoCreate(input, projection.todos, readModel.schema_version);
  if (!await authoritySourcesCurrent()) return failure(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  if (plan.status !== "planned") return {...plan, provider_revision: head.provider_revision, cursor: head.cursor};
  const created = canonicalAuthorityObject(plan.todo, "created Todo");
  if (input.dry_run) {
    return {
      schema_version: COORDINATION_TODO_CREATE_RESULT_SCHEMA,
      status: "planned",
      changed: true,
      dry_run: true,
      todo_id: todoId,
      todo: created,
      provider_revision: head.provider_revision,
      cursor: head.cursor,
    };
  }
  return commitCreate(store, input, requestSha, head, created, todoId);
}
