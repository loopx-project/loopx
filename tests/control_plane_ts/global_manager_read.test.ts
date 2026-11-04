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

test("one goal retains its runnable peer, late gate and unique source counts in either order", () => {
  const samples = Array.from({length: 9}, (_, index) => {
    const agent = `agent-${index + 1}`, ready = index === 0, gated = index === 8;
    const gate = {todo_id: "todo_gate", task_class: "user_gate", text: "Approve synthetic output?",
      unblocks_todo_id: "todo_held", _display: {text: "Approve synthetic output?"}};
    const quota: JsonObject = {ok: true, goal_id: "synthetic", state: gated ? "operator_gate" : "eligible",
      normal_delivery_allowed: ready, agent_identity: {agent_id: agent},
      ...(ready ? {selected_todo: {todo_id: "todo_ready"}, agent_lane_next_action: {todo_id: "todo_ready", agent_id: agent}} : {}),
      ...(gated ? {gate_id: "todo_gate", selected_todo: {todo_id: "todo_held"},
        agent_lane_next_action: {todo_id: "todo_held", agent_id: agent},
        user_todo_summary: {open_count: 1, first_open_items: [gate], gate_open_items: [gate]}} : {})};
    return {item: {goal_id: "synthetic"}, quota, gate_source_facts: {formal_gate: gated ? gate : null, eligible: gated}};
  });
  for (const ordered of [samples, [...samples].reverse()]) {
    const input = {...request(), samples: [...ordered, samples[8]]}, before = structuredClone(input);
    const summary = projectGlobalSummary(input), gates = projectGlobalGates(input);
    assert.deepEqual((summary.lanes as JsonObject[]).map(row => [row.agent_id, row.top_todo_id]), [["agent-1", "todo_ready"]]);
    assert.equal((summary.summary as JsonObject).open_gate_count, 1);
    assert.equal((gates.summary as JsonObject).open_gate_count, 1);
    assert.equal((summary.todos as JsonObject[]).length, 2);
    assert.deepEqual((gates.gates as JsonObject[])[0].blocks, ["todo_held"]);
    for (const payload of [summary, gates]) {
      const sources = (payload.projection_envelope as JsonObject).sources as JsonObject[];
      assert.equal(sources.find(row => row.source_id === "goal_quota")!.item_count, 1);
    }
    assert.deepEqual(input, before);
  }
});

test("an unavailable peer stays visible as source degradation without hiding healthy work", () => {
  const input = {...request(), samples: [
    {item: {goal_id: "synthetic"}, quota: {ok: false, state: "quota_unavailable"}},
    {item: {goal_id: "synthetic"}, quota: {ok: true, state: "normal_run", normal_delivery_allowed: true,
      agent_identity: {agent_id: "healthy-peer"}, agent_lane_next_action: {todo_id: "todo_ready"}}},
  ]};
  const payload = projectGlobalSummary(input);
  assert.equal((payload.lanes as JsonObject[])[0].agent_id, "healthy-peer");
  const envelope = payload.projection_envelope as JsonObject;
  assert.equal(envelope.alert, true);
  assert.equal((envelope.sources as JsonObject[]).find(row => row.source_id === "goal_quota")!.unreadable_count, 1);
});

test("foreign-only Todo notifications do not duplicate gates; applicable and unknown scope survive", () => {
  for (const [scope, expected] of [
    [{agent_id: "peer-a", current_agent_blocking_open_count: 0, other_agent_scoped_open_count: 1}, 0],
    [{agent_id: "peer-a", current_agent_blocking_open_count: 1, other_agent_scoped_open_count: 1}, 1],
    [{agent_id: "peer-a", other_agent_scoped_open_count: 1}, 1],
  ] as const) {
    const quota = {source: "active_state", status: "active_state_user_gate",
      interaction_contract: {user_channel: {action_required: true}},
      user_todo_summary: {agent_scope_filter: scope}};
    const sample = {item: {goal_id: "synthetic"}, quota, gate_source_facts: {formal_gate: null, eligible: true}};
    const input = {...request(), samples: [sample]};
    assert.equal((projectGlobalSummary(input).gates as JsonObject[]).length, expected);
    assert.equal((projectGlobalGates(input).gates as JsonObject[]).length, expected);
  }
});

for (const [name, origin] of [
  ["unknown source", {}],
  ["ordinary user Todo", {source: "active_state", status: "active_state_user_todo"}],
  ["another source", {source: "latest_run", status: "active_state_user_gate"}],
  ["explicit decision", {source: "active_state", status: "active_state_user_gate", gate_id: "distinct-decision"}],
] as const) {
  test(`foreign Todo counts preserve a required decision from ${name}`, () => {
    const question = "Confirm independent recovery?";
    const quota = {...origin, interaction_contract: {user_channel: {
      action_required: true, question, _display: {question},
    }}, user_todo_summary: {agent_scope_filter: {
      agent_id: "peer-a", current_agent_blocking_open_count: 0, other_agent_scoped_open_count: 1,
    }}};
    const sample = {item: {goal_id: "synthetic"}, quota}, before = structuredClone(sample);
    const input = {...request(), samples: [{...sample, gate_source_facts: {formal_gate: null, eligible: true}}]};
    for (const result of [projectGlobalSummary(input), projectGlobalGates(input),
      projectGlobalSummary({...request(), samples: [sample]})]) {
      const gates = result.gates as JsonObject[];
      assert.equal(gates.length, 1);
      assert.equal(gates[0].question, question);
    }
    assert.deepEqual(sample, before);
  });
}

test("a formal gate survives foreign Todo scope counts", () => {
  const gate = {todo_id: "todo_current_gate", task_class: "user_gate", unblocks_todo_id: "todo_current_work"};
  const sample = {item: {goal_id: "synthetic"}, gate_source_facts: {formal_gate: gate, eligible: true},
    quota: {source: "active_state", status: "active_state_user_gate", user_todo_summary: {agent_scope_filter: {
      agent_id: "peer-a", current_agent_blocking_open_count: 0, other_agent_scoped_open_count: 1,
    }}}};
  for (const project of [projectGlobalSummary, projectGlobalGates]) {
    const gates = project({...request(), samples: [sample]}).gates as JsonObject[];
    assert.equal(gates.length, 1);
    assert.deepEqual(gates[0].blocks, ["todo_current_work"]);
  }
});
