import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentity} from "../../loopx/control_plane/effect_program.ts";
import {projectCurrentActionSelectionAdmission} from "../../loopx/control_plane/work_items/action_selection_admission.ts";
import {projectReplanSettlementContract, REPLAN_SETTLEMENT_REQUEST_SCHEMA} from "../../loopx/control_plane/work_items/replan_settlement.ts";

function request(): JsonObject {
  const identity = settlementIdentity({goal_id: "source-fixture", agent_id: "fixture-agent",
    todo_id: "todo_selected", turn_instance_id: "source-turn"});
  return {packet: {ok: true, should_run: true, normal_delivery_allowed: false,
    selected_todo: {todo_id: identity.todo_id, selection_binding: "pending_action_selection"},
    action_selection_qualification: {state: "qualified", requested_todo_id: identity.todo_id}},
  requested_todo_id: identity.todo_id, agent_must_attempt: true, agent_delivery_allowed: false,
  replan_source: {identity, goal_id: identity.goal_id, agent_id: identity.agent_id,
    plan_identity: {...identity}, obligation_id: "replan-1111111111111111",
    contract: projectReplanSettlementContract({schema_version: REPLAN_SETTLEMENT_REQUEST_SCHEMA,
      selected_todo_id: identity.todo_id, semantic_replan_obligation_id: "replan-1111111111111111"})}};
}

test("selected Todo replan freezes its source without granting ordinary delivery", () => {
  for (const binding of ["pending_action_selection", "heartbeat_receipt"]) {
    const value = request();
    (value.packet as JsonObject).selected_todo = {todo_id: "todo_selected", selection_binding: binding};
    const before = structuredClone(value);
    const projection = projectCurrentActionSelectionAdmission(value);
    assert.equal(projection.replan_source_admitted, true);
    if (binding === "pending_action_selection") assert.equal(projection.admitted, false);
    assert.deepEqual(value, before);
    assert.equal(value.agent_delivery_allowed, false);
  }
});

test("visible replan cannot freeze a source for unselected or differently bound work", () => {
  const changes: ((value: JsonObject) => void)[] = [
    v => { (v.packet as JsonObject).should_run = false; },
    v => { (v.packet as JsonObject).ok = false; },
    v => { v.agent_must_attempt = false; },
    v => { (v.packet as JsonObject).selected_todo = {todo_id: "todo_other"}; },
    v => { (v.packet as JsonObject).action_selection_qualification = {state: "deferred"}; },
    v => { (v.packet as JsonObject).action_selection_qualification = {state: "rejected"}; },
    v => { delete (v.packet as JsonObject).action_selection_qualification; },
    v => { (v.replan_source as JsonObject).contract = null; },
    v => { (v.replan_source as JsonObject).obligation_id = "replan-2222222222222222"; },
    v => { (v.replan_source as JsonObject).goal_id = "other-goal"; },
    v => { (v.replan_source as JsonObject).agent_id = "other-agent"; },
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"].map(field =>
      (v: JsonObject) => { ((v.replan_source as JsonObject).plan_identity as JsonObject)[field] = "different"; }),
    v => { ((v.replan_source as JsonObject).plan_identity as JsonObject).replan_obligation_id = "replan-1111111111111111"; },
    v => { ((v.replan_source as JsonObject).contract as JsonObject).single_binding_required = false; },
    v => { ((v.replan_source as JsonObject).contract as JsonObject).settlement_binding = {kind: "todo", id: "todo_other", cli_argument: "--todo-id"}; },
    v => { ((v.replan_source as JsonObject).contract as JsonObject).semantic_obligation = {kind: "autonomous_replan", id: "replan-1111111111111111", settlement_bound: true, discharge: "direct_settlement"}; },
  ];
  for (const change of changes) {
    const value = request();
    change(value);
    assert.equal(projectCurrentActionSelectionAdmission(value).replan_source_admitted, false,
      JSON.stringify(value));
  }
});
