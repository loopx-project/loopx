import type { JsonObject } from "../effect_program.ts";
import type {
  AuthorityStoreCommit,
} from "./authority_store.ts";
import {
  AuthorityStoreProtocolError,
  authorityUnicodeCompare,
  canonicalAuthorityBytes,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {
  TODO_DOMAIN_READ_RECORD_SCHEMA,
  TODO_DOMAIN_RECORD_CONTRACT,
  TODO_CANONICAL_READ_RECORD_FIELDS,
  TODO_CANONICAL_READ_RECORD_SCHEMA,
} from "./coordination_state_contract.ts";
import {canonicalTodoRecord} from "./todo_presentation.ts";

export const COORDINATION_PROJECTION_MUTATION_EVENT_SCHEMA =
  "loopx_coordination_projection_mutation_event_v0";
export const COORDINATION_PROJECTION_MUTATION_RECEIPT_SCHEMA =
  "loopx_coordination_projection_mutation_receipt_v0";
export { TODO_CANONICAL_READ_RECORD_FIELDS, TODO_CANONICAL_READ_RECORD_SCHEMA };

/**
 * Build the revision-bound Todo read model carried by a canonical projection.
 *
 * The read model is projection metadata, not another source of Todo meaning.
 * Keeping its construction beside validation prevents shadow capture, native
 * transactions, and conformance fixtures from drifting on schema fields or
 * digest inputs.  Callers still choose the legacy/native schema explicitly;
 * this helper never performs a compatibility conversion.
 */
export function coordinationTodoReadModel(
  records: readonly JsonObject[],
  schemaVersion: unknown,
): JsonObject {
  const isNative = schemaVersion === TODO_DOMAIN_READ_RECORD_SCHEMA;
  if (!isNative && schemaVersion !== TODO_CANONICAL_READ_RECORD_SCHEMA) {
    throw new AuthorityStoreProtocolError("coordination Todo read-model schema mismatch");
  }
  return {
    schema_version: schemaVersion,
    todo_count: records.length,
    records_sha256: canonicalAuthoritySha256(records),
    contract_fields: [...(isNative
      ? TODO_DOMAIN_RECORD_CONTRACT.fields
      : TODO_CANONICAL_READ_RECORD_FIELDS)],
  };
}

export interface CoordinationTodoProjectionIndex {
  readonly todos: ReadonlyMap<string, JsonObject>;
  readonly todo_ids: readonly string[];
}

export interface CoordinationProjectionIndex extends CoordinationTodoProjectionIndex {
  readonly leases: ReadonlyMap<string, JsonObject>;
  readonly lease_todo_ids: readonly string[];
}

export type CoordinationProjectionMutation =
  | { readonly kind: "todo_upsert"; readonly todo: JsonObject; readonly clear_fields?: readonly string[] }
  | { readonly kind: "todo_remove"; readonly todo_id: string }
  | { readonly kind: "lease_upsert"; readonly lease: JsonObject }
  | { readonly kind: "lease_remove"; readonly todo_id: string };

export interface CoordinationProjectionCommitInput {
  readonly goal_id: string;
  readonly operation_id: string;
  readonly expected_provider_revision: string;
  readonly projection: JsonObject;
  readonly mutations: readonly CoordinationProjectionMutation[];
}

/**
 * Field groups appended to the v0 Todo manifest by released contract
 * revisions, oldest first.
 *
 * The v0 manifest keeps one schema version while revisions add fields, so the
 * `contract_fields` a persisted head declares identifies the release that
 * wrote it. Naming one group per released revision keeps every previously
 * written head exactly reproducible. Deriving the historical shape from the
 * current field list instead would silently drop heads written by the release
 * that is current today, which is the upgrade path this validation exists to
 * protect.
 */
const TODO_CONTRACT_REVISION_FIELDS: readonly (readonly string[])[] = [
  ["completion_validation_revision", "completion_validation_revision_history"],
  ["completion_result"],
];

interface HistoricalTodoContract {
  /** The exact field list a head written before the later revisions declares. */
  readonly fields: readonly string[];
  /** Fields that release had not added yet, so its records must not carry them. */
  readonly absentFields: ReadonlySet<string>;
}

/**
 * Enumerate the released pre-extension shapes of one Todo manifest, newest
 * first. This is not a general subset rule: a declaration must equal one
 * released field list exactly, so partial revision groups, reordered,
 * duplicated and unknown fields all fail.
 */
function historicalTodoContracts(
  fields: readonly string[],
): readonly HistoricalTodoContract[] {
  const contracts: HistoricalTodoContract[] = [];
  const absentFields = new Set<string>();
  for (let index = TODO_CONTRACT_REVISION_FIELDS.length - 1; index >= 0; index -= 1) {
    for (const field of TODO_CONTRACT_REVISION_FIELDS[index]!) absentFields.add(field);
    contracts.push({
      fields: fields.filter((field) => !absentFields.has(field)),
      absentFields: new Set(absentFields),
    });
  }
  return contracts;
}

function sortedIds(values: Iterable<string>): string[] {
  return [...values].sort(authorityUnicodeCompare);
}

function indexRecords(
  value: unknown,
  field: "todos" | "leases",
): Map<string, JsonObject> {
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError(`coordination projection ${field} must be an array`);
  }
  const records = new Map<string, JsonObject>();
  for (const [index, candidate] of value.entries()) {
    const record = canonicalAuthorityObject(candidate, `projection.${field}[${index}]`);
    const todoId = requireAuthorityStoreId(
      record.todo_id,
      `projection.${field}[${index}].todo_id`,
    );
    if (records.has(todoId)) {
      throw new AuthorityStoreProtocolError(
        `coordination projection contains duplicate ${field === "todos" ? "todo" : "lease"} ids`,
      );
    }
    records.set(todoId, record);
  }
  return records;
}

/**
 * Validate and index the Todo portion of one canonical coordination
 * projection. Keeping this invariant outside a provider adapter lets the
 * current shadow read and the future canonical transition path share exactly
 * one identity boundary.
 */
export function indexCoordinationProjectionTodos(
  value: JsonObject,
  expectedGoalId: string,
): CoordinationTodoProjectionIndex {
  if (value.goal_id !== expectedGoalId) {
    throw new AuthorityStoreProtocolError("coordination projection goal mismatch");
  }
  const todos = indexRecords(value.todos, "todos");

  return {
    todos,
    todo_ids: sortedIds(todos.keys()),
  };
}

/**
 * Validate the revision-bound Todo consumer contract carried by a promotable
 * projection. This is intentionally stricter than identity indexing: a head
 * that can coordinate claims but cannot reproduce the existing Todo list is
 * not eligible to become the read authority.
 */
export function validateCoordinationTodoReadModel(
  value: JsonObject,
  expectedGoalId: string,
): JsonObject {
  const index = indexCoordinationProjectionTodos(value, expectedGoalId);
  const records = index.todo_ids.map((todoId) => index.todos.get(todoId)!);
  // Identity indexing already validates/copies every record and rejects duplicate
  // IDs. The insertion order of those same records proves order; serializing
  // both full arrays again adds no content validation. The digest below still
  // covers every field, including nested metadata.
  if ([...index.todos.keys()].some((todoId, position) => todoId !== index.todo_ids[position])) {
    throw new AuthorityStoreProtocolError(
      "coordination Todo read records must use deterministic todo_id order",
    );
  }
  const readModel = canonicalAuthorityObject(
    value.todo_read_model,
    "projection.todo_read_model",
  );
  const domain = readModel.schema_version === TODO_DOMAIN_READ_RECORD_SCHEMA;
  if (!domain && readModel.schema_version !== TODO_CANONICAL_READ_RECORD_SCHEMA) {
    throw new AuthorityStoreProtocolError("coordination Todo read-model schema mismatch");
  }
  if (readModel.todo_count !== records.length) {
    throw new AuthorityStoreProtocolError("coordination Todo read-model count mismatch");
  }
  if (readModel.records_sha256 !== canonicalAuthoritySha256(records)) {
    throw new AuthorityStoreProtocolError("coordination Todo read-model digest mismatch");
  }
  const fields = domain ? TODO_DOMAIN_RECORD_CONTRACT.fields : TODO_CANONICAL_READ_RECORD_FIELDS;
  // Contract revisions extended the v0 manifest without changing its schema.
  // Retain every released pre-extension shape for persisted heads.
  const declaredFields = canonicalAuthorityBytes(readModel.contract_fields);
  const currentContract = declaredFields.equals(canonicalAuthorityBytes(fields));
  const historicalContract = currentContract ? undefined : historicalTodoContracts(fields)
    .find((contract) => declaredFields.equals(canonicalAuthorityBytes(contract.fields)));
  if (!currentContract && historicalContract === undefined) {
    throw new AuthorityStoreProtocolError("coordination Todo read-model field contract mismatch");
  }
  if (historicalContract !== undefined &&
      records.some((record) => [...historicalContract.absentFields].some((field) => field in record))) {
    throw new AuthorityStoreProtocolError("coordination Todo record exceeds its historical field contract");
  }
  for (const [recordIndex, record] of records.entries()) {
    canonicalTodoRecord(record, `coordination Todo read record ${recordIndex}`);
  }
  return readModel;
}

function requireCompleteTodoReplacement(
  previous: JsonObject | undefined,
  replacement: JsonObject,
  index: number,
  clearFields: readonly string[] = [],
): void {
  if (previous === undefined) return;
  const explicitClears = new Set(clearFields);
  const omittedFields = Object.keys(previous)
    .filter((field) => !(field in replacement) && !explicitClears.has(field))
    .sort(authorityUnicodeCompare);
  if (omittedFields.length > 0) {
    throw new AuthorityStoreProtocolError(
      `coordination Todo replacement ${index} omits existing fields: ${omittedFields.join(", ")}`,
    );
  }
}

function applyCoordinationMutation(
  mutation: CoordinationProjectionMutation,
  index: number,
  todos: Map<string, JsonObject>,
  leases: Map<string, JsonObject>,
  claimMutationTarget: (kind: "todo" | "lease", todoId: string) => void,
  requireCompleteReplacement: boolean,
): void {
  switch (mutation.kind) {
    case "todo_upsert": {
      const todo = canonicalAuthorityObject(mutation.todo, `mutations[${index}].todo`);
      const todoId = requireAuthorityStoreId(todo.todo_id, `mutations[${index}].todo_id`);
      claimMutationTarget("todo", todoId);
      if (requireCompleteReplacement) {
        requireCompleteTodoReplacement(todos.get(todoId), todo, index, mutation.clear_fields);
      }
      todos.set(todoId, todo);
      return;
    }
    case "todo_remove": {
      const todoId = requireAuthorityStoreId(
        mutation.todo_id,
        `mutations[${index}].todo_id`,
      );
      claimMutationTarget("todo", todoId);
      if (!todos.delete(todoId)) {
        throw new AuthorityStoreProtocolError("coordination projection todo remove target missing");
      }
      return;
    }
    case "lease_upsert": {
      const lease = canonicalAuthorityObject(mutation.lease, `mutations[${index}].lease`);
      const todoId = requireAuthorityStoreId(lease.todo_id, `mutations[${index}].todo_id`);
      claimMutationTarget("lease", todoId);
      leases.set(todoId, lease);
      return;
    }
    case "lease_remove": {
      const todoId = requireAuthorityStoreId(
        mutation.todo_id,
        `mutations[${index}].todo_id`,
      );
      claimMutationTarget("lease", todoId);
      if (!leases.delete(todoId)) {
        throw new AuthorityStoreProtocolError("coordination projection lease remove target missing");
      }
      return;
    }
    default: {
      const unreachable: never = mutation;
      throw new AuthorityStoreProtocolError(
        `unsupported coordination projection mutation: ${String(unreachable)}`,
      );
    }
  }
}

/** Validate the complete Todo/lease identity graph of one coordination head. */
export function indexCoordinationProjection(
  value: JsonObject,
  expectedGoalId: string,
): CoordinationProjectionIndex {
  const todoIndex = indexCoordinationProjectionTodos(value, expectedGoalId);
  const leases = indexRecords(value.leases, "leases");
  for (const todoId of leases.keys()) {
    if (!todoIndex.todos.has(todoId)) {
      throw new AuthorityStoreProtocolError(
        "coordination projection lease references an unknown todo",
      );
    }
  }
  return {
    ...todoIndex,
    leases,
    lease_todo_ids: sortedIds(leases.keys()),
  };
}

/**
 * Apply one already-authorized coordination transaction as a pure projection
 * reduction. Domain transition policy stays with the LoopX kernel; this
 * reducer owns only exact identity replacement/removal, deterministic order,
 * and the final Todo/lease referential-integrity fence shared by every store.
 */
export function reduceCoordinationProjection(
  value: JsonObject,
  expectedGoalId: string,
  mutations: readonly CoordinationProjectionMutation[],
): JsonObject {
  if (mutations.length === 0) {
    throw new AuthorityStoreProtocolError("coordination projection mutation batch is empty");
  }
  const current = indexCoordinationProjection(value, expectedGoalId);
  const readModel = value.todo_read_model === undefined
    ? undefined : validateCoordinationTodoReadModel(value, expectedGoalId);
  const todos = new Map(current.todos);
  const leases = new Map(current.leases);
  const mutatedRecords = new Set<string>();

  const claimMutationTarget = (kind: "todo" | "lease", todoId: string): void => {
    const target = `${kind}:${todoId}`;
    if (mutatedRecords.has(target)) {
      throw new AuthorityStoreProtocolError(
        "coordination projection mutation targets one record more than once",
      );
    }
    mutatedRecords.add(target);
  };

  for (const [index, mutation] of mutations.entries()) {
    applyCoordinationMutation(
      mutation,
      index,
      todos,
      leases,
      claimMutationTarget,
      value.todo_read_model !== undefined,
    );
  }

  for (const todoId of leases.keys()) {
    if (!todos.has(todoId)) {
      throw new AuthorityStoreProtocolError(
        "coordination projection mutation leaves an orphan lease",
      );
    }
  }
  const nextTodos = sortedIds(todos.keys()).map((todoId) => todos.get(todoId)!);
  const reduced = canonicalAuthorityObject({
    ...value,
    todos: nextTodos,
    leases: sortedIds(leases.keys()).map((todoId) => leases.get(todoId)!),
    ...(readModel === undefined
      ? {}
      : { todo_read_model: coordinationTodoReadModel(nextTodos, readModel.schema_version) }),
  }, "coordination projection");
  if (value.todo_read_model !== undefined) {
    validateCoordinationTodoReadModel(reduced, expectedGoalId);
  }
  return reduced;
}

function mutationTarget(mutation: CoordinationProjectionMutation, index: number): string {
  const record = mutation.kind === "todo_upsert"
    ? canonicalAuthorityObject(mutation.todo, `mutations[${index}].todo`)
    : mutation.kind === "lease_upsert"
    ? canonicalAuthorityObject(mutation.lease, `mutations[${index}].lease`)
    : mutation;
  const todoId = requireAuthorityStoreId(record.todo_id, `mutations[${index}].todo_id`);
  return `${mutation.kind.startsWith("todo_") ? "todo" : "lease"}:${todoId}`;
}

/**
 * Prepare the one AuthorityStore transaction used by the canonical cutover.
 * Event, next projection, and receipt are derived from the same validated
 * reduction so a provider adapter cannot accidentally persist three
 * disagreeing descriptions of one effect.
 */
export function prepareCoordinationProjectionCommit(
  input: CoordinationProjectionCommitInput,
): AuthorityStoreCommit {
  const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
  const operationId = requireAuthorityStoreId(input.operation_id, "operation id");
  const expectedRevision = requireAuthorityStoreId(
    input.expected_provider_revision,
    "expected provider revision",
  );
  const currentProjection = canonicalAuthorityObject(input.projection, "projection");
  const nextProjection = reduceCoordinationProjection(
    currentProjection,
    goalId,
    input.mutations,
  );
  if (currentProjection.todo_read_model !== undefined) {
    validateCoordinationTodoReadModel(nextProjection, goalId);
  }
  const targets = input.mutations.map(mutationTarget).sort(authorityUnicodeCompare);
  const mutationKinds = input.mutations.map((mutation) => mutation.kind).sort(
    authorityUnicodeCompare,
  );
  const previousProjectionSha256 = canonicalAuthoritySha256(currentProjection);
  const nextProjectionSha256 = canonicalAuthoritySha256(nextProjection);
  const mutationSha256 = canonicalAuthoritySha256(input.mutations);
  const common = {
    operation_id: operationId,
    goal_id: goalId,
    mutation_kinds: mutationKinds,
    targets,
    mutation_sha256: mutationSha256,
    previous_projection_sha256: previousProjectionSha256,
    next_projection_sha256: nextProjectionSha256,
  };
  return {
    expected_provider_revision: expectedRevision,
    operation_id: operationId,
    events: [{
      schema_version: COORDINATION_PROJECTION_MUTATION_EVENT_SCHEMA,
      ...common,
    }],
    next_projection: nextProjection,
    receipts: [{
      schema_version: COORDINATION_PROJECTION_MUTATION_RECEIPT_SCHEMA,
      ...common,
    }],
  };
}
