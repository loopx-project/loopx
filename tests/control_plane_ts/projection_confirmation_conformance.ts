import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import {listLocalCoordinationTodos, readLocalCoordinationTodo} from "../../loopx/control_plane/coordination/local_authority_read.ts";
import {LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA, LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import {canonicalReadRoot} from "./local_promotion_fixture.ts";

export function registerProjectionConfirmationConformance(name: string, factory: AuthorityStoreConformanceFactory): void {
  for (const shape of ["native", "legacy"] as const) test(`${name}: projection confirmation observes current full source (${shape})`, async context => {
    const {store, contender} = await factory(context);
    const goal = "projection-confirmation", fixture = productionScaleCoordinationFixture(goal, shape);
    // Retained archived Monitor generations previously stranded Markdown recovery.
    const monitor = (fixture.projection.todos as Record<string, unknown>[]).find(row => row.task_class === "continuous_monitor")!;
    Object.assign(monitor, {archive_state: "archive", status: "done", done: true, material_change_generation: 12,
      ...(shape === "legacy" ? {source_section: "Completed Work Archive"} : {})});
    fixture.projection.todo_read_model = coordinationTodoReadModel(fixture.projection.todos as Record<string, unknown>[],
      (fixture.projection.todo_read_model as Record<string, unknown>).schema_version as string);
    const seeded = await store.commitAuthority({operation_id: "projection-source", expected_provider_revision: null,
      next_projection: fixture.projection, events: [], receipts: []});
    assert.equal(seeded.status, "applied");
    const dependencies = {createStore: () => store};
    const request = {schema_version: LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA, goal_id: goal,
      runtime_root: await canonicalReadRoot(context, goal)};
    const plain = await listLocalCoordinationTodos(request, dependencies);
    assert.equal(plain.status, "loaded");
    assert.equal(Object.hasOwn(plain, "projection_readback"), false);
    for (const changed of [false, true]) {
      const confirmed = await listLocalCoordinationTodos({...request,
        projection_readback: {provider_revision: seeded.provider_revision, changed, attempt: 1, target: "latest"}}, dependencies);
      const {projection_readback, ...unchanged} = confirmed;
      assert.deepEqual(unchanged, plain, "confirmation cannot change full-source semantics");
      assert.deepEqual(projection_readback, {status: changed ? "delivered" : "current",
        provider_revision: seeded.provider_revision, observed_provider_revision: seeded.provider_revision, next_action: "finish"});
    }
    const before = await contender.loadAuthority(); assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    const committed = await contender.commitAuthority({operation_id: "overlapping-revision", expected_provider_revision: before.provider_revision,
      next_projection: before.head, events: [], receipts: []});
    assert.equal(committed.status, "applied");
    const after = await store.loadAuthority();
    const stale = await listLocalCoordinationTodos({...request, include_leases: true,
      projection_readback: {provider_revision: seeded.provider_revision, changed: true, attempt: 1, target: "latest"}}, dependencies);
    const confirmation = stale.projection_readback as Record<string, unknown>;
    assert.equal(confirmation.status, "pending");
    assert.equal(confirmation.provider_revision, seeded.provider_revision);
    assert.equal(confirmation.observed_provider_revision, committed.provider_revision);
    assert.equal(confirmation.next_action, "retry");
    assert.equal(confirmation.retry_business_mutation, false);
    for (const target of ["latest", "pinned"]) for (const attempt of [1, 3]) {
      const result = await listLocalCoordinationTodos({...request,
        projection_readback: {provider_revision: seeded.provider_revision, changed: true, attempt, target}}, dependencies);
      const decision = result.projection_readback as Record<string, unknown>;
      assert.equal(decision.next_action, target === "latest" && attempt === 1 ? "retry" : "finish");
      assert.equal(decision.status, "pending");
    }
    assert.equal((stale.todos as unknown[]).length, fixture.expected_initial_todo_count);
    assert.equal((stale.leases as unknown[]).length, fixture.expected_current_lease_count);
    assert.equal(stale.provider_revision, committed.provider_revision);
    const exact = await readLocalCoordinationTodo({schema_version: LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
      runtime_root: request.runtime_root, goal_id: goal, todo_id: fixture.completion_todo_id}, dependencies);
    assert.equal(exact.status, "found"); assert.equal(exact.provider_revision, stale.provider_revision);
    const invalid = await listLocalCoordinationTodos({...request, projection_readback: {changed: true}}, dependencies);
    assert.equal(invalid.status, "failed");
    assert.deepEqual(await store.loadAuthority(), after, "confirmation and rejected reads never write authority");
  });
}
