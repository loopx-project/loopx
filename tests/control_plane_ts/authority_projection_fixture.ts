import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {
  authorityUnicodeCompare,
  canonicalAuthorityObject,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  coordinationTodoReadModel,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {
  canonicalTodoDomainRecord,
  TODO_CANONICAL_READ_RECORD_SCHEMA,
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {
  materializeTodoRecordForSchema,
} from "../../loopx/control_plane/coordination/todo_presentation.ts";

export type AuthorityProjectionSchema = "legacy" | "native";

function readModelSchema(schema: AuthorityProjectionSchema): string {
  return schema === "native"
    ? TODO_DOMAIN_READ_RECORD_SCHEMA
    : TODO_CANONICAL_READ_RECORD_SCHEMA;
}

/**
 * Convert one synthetic Todo to the requested wire shape without changing
 * domain fields. Compatibility metadata is deliberately the only difference.
 */
export function todoFixtureRecord(
  value: JsonObject,
  schema: AuthorityProjectionSchema,
): JsonObject {
  const domainInput = structuredClone(value);
  delete domainInput.schema_version;
  delete domainInput.source_section;
  delete domainInput.index;
  // Content revision is read-model selection metadata, not a persisted Todo field.
  delete domainInput.content_revision;
  const domain = canonicalTodoDomainRecord({
    ...domainInput,
    schema_version: TODO_DOMAIN_ITEM_SCHEMA,
  }, "fixture Todo domain record");
  const materialized = materializeTodoRecordForSchema(
    domain,
    readModelSchema(schema),
    "fixture Todo",
  );
  if (schema === "legacy") {
    if (typeof value.source_section === "string") {
      materialized.source_section = value.source_section;
    }
    if (Number.isSafeInteger(value.index)) materialized.index = value.index;
  }
  return canonicalAuthorityObject(materialized, "fixture Todo");
}

/**
 * Construct a complete canonical projection from domain records. Every test
 * provider receives identical ordering and read-model metadata, so a parity
 * failure points at a provider rather than at fixture assembly.
 */
export function authorityProjectionFixture(
  goalId: string,
  todos: readonly JsonObject[],
  leases: readonly JsonObject[] = [],
  schema: AuthorityProjectionSchema = "native",
  extras: JsonObject = {},
): JsonObject {
  const normalizedTodos = todos
    .map(todo => todoFixtureRecord(todo, schema))
    .sort((left, right) => authorityUnicodeCompare(
      String(left.todo_id), String(right.todo_id),
    ));
  const normalizedLeases = leases.map(lease => structuredClone(lease)).sort((left, right) =>
    authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)));
  return canonicalAuthorityObject({
    ...structuredClone(extras),
    goal_id: goalId,
    todos: normalizedTodos,
    leases: normalizedLeases,
    todo_read_model: coordinationTodoReadModel(normalizedTodos, readModelSchema(schema)),
  }, "fixture coordination projection");
}

/** Preserve logical Todo fields while changing only compatibility provenance. */
export function projectionFixtureAsSchema(
  projection: JsonObject,
  schema: AuthorityProjectionSchema,
): JsonObject {
  const todos = Array.isArray(projection.todos)
    ? projection.todos as JsonObject[]
    : [];
  const leases = Array.isArray(projection.leases)
    ? projection.leases as JsonObject[]
    : [];
  const extras = structuredClone(projection);
  delete extras.goal_id;
  delete extras.todos;
  delete extras.leases;
  delete extras.todo_read_model;
  return authorityProjectionFixture(String(projection.goal_id), todos, leases, schema, extras);
}
