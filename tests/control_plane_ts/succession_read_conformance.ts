import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleSuccessionFixture} from "./production_scale_coordination_fixture.ts";
import {validateCoordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {runPythonReadConsumer} from "./python_read_consumer.ts";

// Exercise the shipped Python consumer → typed policy, not a second test reducer.
const CONSUMER = `
import json, sys
from loopx.control_plane.coordination.local_authority import canonical_todo_summary_fields
from loopx.control_plane.todos.goal_todo_projection import filtered_todo_summary
source=json.load(sys.stdin)
summary=canonical_todo_summary_fields(source['todos'])['agent_todos']
result={}
for key, todo_id in source['cases'].items():
    selected=filtered_todo_summary(summary,role='agent',todo_id=todo_id)
    result[key]={'gap':selected.get('completed_without_successor_count',0),
      'gates':[g['gate_state'] for g in selected.get('handoff_gates',[])],
      'closure':'terminal_closure_proof' in selected}
print(json.dumps(result))
`;
export function registerSuccessionReadConformance(name: string, factory: AuthorityStoreConformanceFactory): void {
  for (const schema of ["native", "legacy"] as const) test(`${name}: full-graph succession readback (${schema})`, async context => {
    const {store} = await factory(context), goal = "succession-goal";
    const {projection, cases} = productionScaleSuccessionFixture(goal, schema);
    validateCoordinationTodoReadModel(projection, goal);
    assert.equal((await store.commitAuthority({operation_id: "succession-source", expected_provider_revision: null,
      next_projection: projection, events: [], receipts: []})).status, "applied");
    const before = await store.loadAuthority();
    assert.equal(before.status, "loaded");
    const child = runPythonReadConsumer(CONSUMER, {todos: before.head.todos, cases});
    assert.equal(child.status, 0, child.stderr);
    const actual = JSON.parse(child.stdout);
    assert.deepEqual(actual.inferred_source, {gap: 0, gates: [], closure: false});
    assert.deepEqual(actual.missing_source, {gap: 1, gates: [], closure: false});
    assert.deepEqual(actual.self_source, {gap: 1, gates: [], closure: false});
    assert.deepEqual(actual.handoff_source, {gap: 0, gates: ["cleared_with_successor"], closure: false});
    assert.deepEqual(actual.closed_source, {gap: 0, gates: [], closure: false});
    assert.deepEqual(await store.loadAuthority(), before);
  });
}
