/** Archive is a retention transaction, separate from completion and its lease
 * and validation effects. Both compose the same durable receipt owner. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel,
  type CoordinationProjectionMutation} from "./coordination_projection.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "./command_receipt.ts";
import {selectCoordinationTodoArchive} from "./todo_archive_selection.ts";

export const COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA =
  "loopx_coordination_todo_archive_result_v0";
export const COORDINATION_TODO_ARCHIVE_RECEIPT_SCHEMA =
  "loopx_coordination_todo_archive_receipt_v0";

export interface CoordinationTodoArchiveInput {
  readonly goal_id: string;
  readonly role: "agent" | "user";
  readonly max_active_done: number;
  readonly operation_id: string;
  readonly expected_provider_revision?: string;
  readonly dry_run: boolean;
  readonly now: Date;
}

export type CoordinationTodoArchiveResult = JsonObject & {
  readonly schema_version: typeof COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA;
};

function archiveFailure(code: string, reason: string): CoordinationTodoArchiveResult {
  return {
    schema_version: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA,
    status: "failed",
    changed: false,
    reason_code: code,
    reason,
  };
}

function normalizeArchiveInput(raw: CoordinationTodoArchiveInput): CoordinationTodoArchiveInput {
  if (!Number.isSafeInteger(raw.max_active_done) || raw.max_active_done < 0) {
    throw new AuthorityStoreProtocolError("max_active_done must be a non-negative safe integer");
  }
  if (raw.role !== "agent" && raw.role !== "user") {
    throw new AuthorityStoreProtocolError("role must be one of: agent, user");
  }
  if (typeof raw.dry_run !== "boolean") throw new AuthorityStoreProtocolError("dry_run must be a boolean");
  if (!(raw.now instanceof Date) || Number.isNaN(raw.now.valueOf())) {
    throw new AuthorityStoreProtocolError("now must be a valid Date");
  }
  return {
    ...raw,
    goal_id: requireAuthorityStoreId(raw.goal_id, "goal id"),
    role: raw.role,
    operation_id: requireAuthorityStoreId(raw.operation_id, "operation id"),
    ...(raw.expected_provider_revision === undefined ? {} : {
      expected_provider_revision: requireAuthorityStoreId(
        raw.expected_provider_revision, "expected provider revision",
      ),
    }),
  };
}

function archiveReceipt(input: CoordinationTodoArchiveInput, requestSha: string) {
  return new CoordinationCommandReceipt({result_schema: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA,
    identity: {schema_version: COORDINATION_TODO_ARCHIVE_RECEIPT_SCHEMA,
      operation_id: input.operation_id, goal_id: input.goal_id, request_sha256: requestSha},
    failure: archiveFailure,
    decode(original) {
      const result = commandReceiptResult(original);
      return {...result, fields: {...result.fields, operation_id: input.operation_id}};
    }});
}

/** Archive the oldest canonical completed records while preserving standing decisions. */
export async function executeCoordinationTodoArchiveCompleted(
  store: AuthorityStore,
  rawInput: CoordinationTodoArchiveInput,
): Promise<CoordinationTodoArchiveResult> {
  let input: CoordinationTodoArchiveInput;
  try {
    input = normalizeArchiveInput(rawInput);
  } catch (error) {
    return archiveFailure(
      "invalid_coordination_todo_archive",
      error instanceof Error ? error.message : "invalid Todo archive request",
    );
  }
  const requestSha = canonicalAuthoritySha256({
    goal_id: input.goal_id,
    role: input.role,
    max_active_done: input.max_active_done,
    dry_run: input.dry_run,
    ...(input.expected_provider_revision === undefined ? {} : {
      expected_provider_revision: input.expected_provider_revision,
    }),
  });
  // Preview observes the current snapshot without consuming or replaying a
  // durable operation identity. Historical receipts precede current-head CAS.
  const receipt = archiveReceipt(input, requestSha);
  if (!input.dry_run) {
    const replay = await receipt.read(store);
    if (replay !== null) return replay;
  }
  const observation = input.dry_run
    ? {kind: "authority" as const, authority: await store.loadAuthority()}
    : await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") {
    return {schema_version: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA, ...head, changed: false};
  }
  if (input.expected_provider_revision !== undefined &&
      head.provider_revision !== input.expected_provider_revision) {
    return {
      schema_version: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA,
      status: "conflict", changed: false,
      conflict_kind: "provider_revision_mismatch",
      current_provider_revision: head.provider_revision,
      current_cursor: head.cursor,
    };
  }
  let projection: ReturnType<typeof indexCoordinationProjection>;
  try {
    projection = indexCoordinationProjection(head.head, input.goal_id);
    validateCoordinationTodoReadModel(head.head, input.goal_id);
  } catch (error) {
    return archiveFailure(
      "invalid_coordination_projection",
      error instanceof Error ? error.message : "invalid coordination projection",
    );
  }
  const selection = selectCoordinationTodoArchive({
    role: input.role,
    max_active_done: input.max_active_done,
    todos: projection.todo_ids.map((todoId) => projection.todos.get(todoId)!),
  });
  const moved = selection.moved_todo_ids.map((todoId) => projection.todos.get(todoId)!);
  const updatedAt = input.now.toISOString().replace(/\.\d{3}Z$/u, "Z");
  const result: JsonObject = {
    role: selection.role,
    operation_id: input.operation_id,
    changed: moved.length > 0,
    active_done_before: selection.active_done_before,
    active_done_after: selection.active_done_after,
    max_active_done: selection.max_active_done,
    moved_count: selection.moved_count,
    moved_todo_ids: selection.moved_todo_ids,
    retained_standing_decision_count: selection.retained_standing_decision_count,
  };
  if (input.dry_run) {
    return {
      ...result,
      schema_version: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA,
      status: moved.length > 0 ? "planned" : "no_change",
      dry_run: true,
      provider_revision: head.provider_revision,
      cursor: head.cursor,
    };
  }
  if (moved.length === 0) {
    return {
      ...result,
      schema_version: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA,
      status: "no_change",
      dry_run: false,
      provider_revision: head.provider_revision,
      cursor: head.cursor,
    };
  }
  const mutations: CoordinationProjectionMutation[] = moved.map((todo) => ({
    kind: "todo_upsert",
    todo: {...todo, archive_state: "archive", updated_at: updatedAt},
  }));
  const commit: AuthorityStoreCommit = prepareCoordinationProjectionCommit({
    goal_id: input.goal_id,
    operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision,
    projection: head.head,
    mutations,
  });
  commit.receipts = [{
    schema_version: COORDINATION_TODO_ARCHIVE_RECEIPT_SCHEMA,
    operation_id: input.operation_id,
    goal_id: input.goal_id,
    request_sha256: requestSha,
    result,
  }];
  return archiveReceipt(input, requestSha).commit(store, commit);
}
