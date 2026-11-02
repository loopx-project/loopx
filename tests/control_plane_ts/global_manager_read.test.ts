import assert from "node:assert/strict";
import test from "node:test";
import {projectGlobalSummary, projectGlobalGates} from "../../loopx/control_plane/presentation/global_manager_read.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const request = (): JsonObject => ({schema_version: "global_manager_read_request_v0", status: {ok: true}, samples: [],
  observed_at: "2026-11-02T12:00:00+00:00", generated_at: "2026-11-02T12:00:00+00:00",
  quota_read_at: "2026-11-02T12:00:00+00:00", boundary: {}, limit: 8, since: null, source_surfaces: [], time_range: "24h"});

for (const project of [projectGlobalSummary, projectGlobalGates]) {
  test(`${project.name} rejects malformed boundary facts`, () => {
    for (const patch of [{schema_version: "unknown"}, {status: []}, {samples: {}}, {samples: [null]},
      {samples: [{item: {}, quota: []}]}, {limit: "8"}, {observed_at: null}, {boundary: null}]) {
      assert.throws(() => project({...request(), ...patch}));
    }
  });
}

test("exact target wins over scope and unrelated visible work is removed", () => {
  const gate = {task_class: "user_gate", todo_id: "todo_gate", unblocks_todo_id: "todo_exact",
    decision_scope: {kind: "production", granularity: "project", scope_key: "deploy"}};
  const payload = projectGlobalGates({...request(), samples: [{gate_source_facts: {formal_gate: gate, eligible: true}, item: {goal_id: "synthetic"}, quota: {
    user_todo_summary: {gate_open_items: [gate]},
    agent_lane_next_action: {todo_id: "todo_other", required_decision_scopes: [gate.decision_scope]},
  }}]});
  assert.deepEqual((payload.gates as JsonObject[])[0].blocks, ["todo_exact"]);
  assert.equal(Object.hasOwn((payload.lanes as JsonObject[])[0], "top_todo_id"), false);
});

test("both transaction handlers dispatch and replay without state changes", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "synthetic", requestShutdown: () => undefined});
  for (const method of ["presentation.global_summary.project", "presentation.global_gates.project"]) {
    const input = request(), before = structuredClone(input);
    const first = await dispatchEffectRuntimeMethod(handlers, method, input);
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, method, input), first);
    assert.deepEqual(input, before);
    assert.equal((first.projection_envelope as JsonObject).complete, false);
  }
});

test("gates reject malformed source facts before filtering samples", () => {
  for (const facts of [undefined, null, [], {}, {eligible: "true", formal_gate: null},
    {eligible: true}, {eligible: true, formal_gate: []}]) {
    assert.throws(() => projectGlobalGates({...request(), samples: [
      {item: {}, quota: {}, gate_source_facts: facts},
    ]}));
  }
});

test("gate projection consumes source eligibility without reclassifying quota", () => {
  const quota = {interaction_contract: {user_channel: {action_required: true}}};
  const samples = [{item: {goal_id: "synthetic"}, quota,
    gate_source_facts: {eligible: false, formal_gate: null}}];
  assert.deepEqual(projectGlobalGates({...request(), samples}).gates, []);
  samples[0].quota = {};
  samples[0].gate_source_facts.eligible = true;
  assert.equal((projectGlobalGates({...request(), samples}).gates as JsonObject[])[0].owner, "controller");
});
