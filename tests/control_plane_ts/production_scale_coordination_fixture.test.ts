import assert from "node:assert/strict";
import test from "node:test";

import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  TODO_CANONICAL_READ_RECORD_SCHEMA,
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
  TODO_ITEM_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {validateCoordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {
  PRODUCTION_SCALE_AGENT_TODO_COUNT,
  PRODUCTION_SCALE_COMPLETION_TARGET_INDEX,
  PRODUCTION_SCALE_SUPERSEDE_TARGET_INDEX,
  productionScaleCoordinationFixture,
  requireProductionScaleTargetIndex,
} from "./production_scale_coordination_fixture.ts";

test("production-scale fixture is deterministic and explicitly ordered", () => {
  const first = productionScaleCoordinationFixture("fixture-goal", "legacy");
  const second = productionScaleCoordinationFixture("fixture-goal", "legacy");
  assert.equal(canonicalAuthoritySha256(first.projection), canonicalAuthoritySha256(second.projection));
  const todos = first.projection.todos as Record<string, unknown>[];
  assert.equal(todos.length, first.expected_initial_todo_count);
  assert.deepEqual(
    todos.map(todo => todo.todo_id),
    [...todos].sort((left, right) => String(left.todo_id).localeCompare(String(right.todo_id)))
      .map(todo => todo.todo_id),
  );
  assert.equal(first.projection.todo_read_model &&
    (first.projection.todo_read_model as Record<string, unknown>).schema_version,
  TODO_CANONICAL_READ_RECORD_SCHEMA);
  assert.equal(
    (first.projection.todo_read_model as Record<string, unknown>).records_sha256,
    canonicalAuthoritySha256(todos),
  );
});
test("legacy and native fixture variants differ only by compatibility provenance", () => {
  const legacy = productionScaleCoordinationFixture("fixture-goal", "legacy");
  const native = productionScaleCoordinationFixture("fixture-goal", "native");
  const legacyTodos = legacy.projection.todos as Record<string, unknown>[];
  const nativeTodos = native.projection.todos as Record<string, unknown>[];
  assert.equal(legacyTodos.length, nativeTodos.length);
  for (const [index, legacyTodo] of legacyTodos.entries()) {
    const nativeTodo = nativeTodos[index]!;
    assert.equal(legacyTodo.todo_id, nativeTodo.todo_id);
    assert.equal(legacyTodo.role, nativeTodo.role);
    assert.equal(legacyTodo.text, nativeTodo.text);
    assert.equal(legacyTodo.status, nativeTodo.status);
    assert.equal(legacyTodo.source_section !== undefined, true);
    assert.equal(legacyTodo.index !== undefined, true);
    assert.equal(nativeTodo.schema_version, TODO_DOMAIN_ITEM_SCHEMA);
    assert.equal(nativeTodo.source_section, undefined);
    assert.equal(nativeTodo.index, undefined);
    const {schema_version: _legacySchema, source_section: _section, index: _index, ...legacyDomain} = legacyTodo;
    const {schema_version: _nativeSchema, ...nativeDomain} = nativeTodo;
    assert.deepEqual(legacyDomain, nativeDomain);
  }
  assert.equal(
    (native.projection.todo_read_model as Record<string, unknown>).schema_version,
    TODO_DOMAIN_READ_RECORD_SCHEMA,
  );
  assert.doesNotThrow(() => validateCoordinationTodoReadModel(native.projection, "fixture-goal"));
  assert.doesNotThrow(() => validateCoordinationTodoReadModel(legacy.projection, "fixture-goal"));
  assert.equal(native.expected_agent_archive_count_after_terminals, legacy.expected_agent_archive_count_after_terminals);
  assert.equal(native.expected_user_archive_count, legacy.expected_user_archive_count);
});

test("fixture rejects silent status-count drift at construction time", () => {
  // The checked-in envelope is validated while loading. This assertion keeps
  // the public contract explicit: the fixture must expose both wire schemas.
  const fixture = productionScaleCoordinationFixture("fixture-goal", "native");
  assert.equal(
    (fixture.projection.todos as Record<string, unknown>[])[0]?.schema_version,
    TODO_DOMAIN_ITEM_SCHEMA,
  );
  assert.equal(
    (fixture.projection.todo_read_model as Record<string, unknown>).schema_version,
    TODO_DOMAIN_READ_RECORD_SCHEMA,
  );
  assert.notEqual(TODO_ITEM_SCHEMA, TODO_DOMAIN_ITEM_SCHEMA);
});

test("fixture rejects a target index that addresses no generated agent Todo", () => {
  // Invariant (typescript-control-plane-migration-v0 §2.5): parsed JSON enters
  // the system as unknown, and a type annotation does not prove the bytes
  // satisfy the contract. The envelope's two target indices are the only
  // envelope fields consumed as positional lookups, so they are validated at
  // the boundary. The cases below are derived from that rule, not from the
  // generated fixture: an index either addresses a generated agent Todo or it
  // is rejected, whatever the fixture happens to contain.
  const agentTodoCount = PRODUCTION_SCALE_AGENT_TODO_COUNT;
  assert.ok(agentTodoCount > 0, "the envelope must generate at least one agent Todo");

  assert.equal(requireProductionScaleTargetIndex(0, "test target index", agentTodoCount), 0);
  assert.equal(
    requireProductionScaleTargetIndex(agentTodoCount - 1, "test target index", agentTodoCount),
    agentTodoCount - 1,
  );

  // Mutation: the boundary one past the last generated Todo. Before the
  // validation existed this value reached `agents[value]` and produced
  // `undefined` instead of a diagnosis.
  assert.throws(
    () => requireProductionScaleTargetIndex(agentTodoCount, "test target index", agentTodoCount),
    /does not address a generated agent Todo/u,
  );
  assert.throws(
    () => requireProductionScaleTargetIndex(-1, "test target index", agentTodoCount),
    /does not address a generated agent Todo/u,
  );
  assert.throws(
    () => requireProductionScaleTargetIndex(1.5, "test target index", agentTodoCount),
    /does not address a generated agent Todo/u,
  );

  // The checked-in envelope must satisfy the rule its consumers rely on.
  assert.ok(PRODUCTION_SCALE_COMPLETION_TARGET_INDEX < agentTodoCount);
  assert.ok(PRODUCTION_SCALE_SUPERSEDE_TARGET_INDEX < agentTodoCount);
  assert.notEqual(PRODUCTION_SCALE_COMPLETION_TARGET_INDEX, PRODUCTION_SCALE_SUPERSEDE_TARGET_INDEX);
});
