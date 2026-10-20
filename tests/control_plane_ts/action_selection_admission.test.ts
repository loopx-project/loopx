import assert from "node:assert/strict";
import test from "node:test";
import {currentActionSelectionAdmission, actionSelectionNeedsRecovery,
  assertActionSelectionConsistency} from "../../loopx/control_plane/work_items/action_selection_admission.ts";
import {normalizeTodoId} from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const agent = {must_attempt: true, delivery_allowed: true};
function source(state = "deferred"): JsonObject {
  return {ok: true, should_run: true, effective_action: "autonomous_replan",
    execution_obligation: {must_attempt_work: true},
    action_selection_qualification: {state, requested_todo_id: "todo_target",
      recovery_action: "reenter_guard_without_selection"}};
}

test("current work and pending delivery use canonical channel permission", () => {
  const absent = source();
  assert.deepEqual(currentActionSelectionAdmission(absent, "todo_target", agent), {selected_todo_id: null, admitted: false});
  assert.equal(actionSelectionNeedsRecovery(absent, agent), true);
  for (const selected of [{todo_id: "todo_target"}, {todo_id: "todo_target", selection_binding: "pending_action_selection"}]) {
    const packet = {...source(), selected_todo: selected, normal_delivery_allowed: true};
    assert.equal(currentActionSelectionAdmission(packet, "todo_target", agent).admitted, true);
    assert.equal(actionSelectionNeedsRecovery(packet, agent), false);
    assert.equal(currentActionSelectionAdmission(packet, "todo_other", agent).admitted, false);
    assert.equal(currentActionSelectionAdmission({...packet, ok: false}, "todo_target", agent).admitted, false);
  }
});

test("workspace repair needs its repair route and explicit delivery refusal", () => {
  const packet = {...source(), workspace_repair_allowed: true, effective_action: "agent_workspace_repair",
    execution_obligation: {kind: "agent_workspace_repair", must_attempt_work: true},
    selected_todo: {todo_id: "todo_target", selection_binding: "pending_action_selection"}};
  assert.equal(currentActionSelectionAdmission(packet, "todo_target", {...agent, delivery_allowed: false}).admitted, true);
  assert.equal(currentActionSelectionAdmission(packet, "todo_target", agent).admitted, false);
  assert.equal(currentActionSelectionAdmission(packet, "todo_target", {must_attempt: false, delivery_allowed: false}).admitted, false);
  assert.equal(currentActionSelectionAdmission({...packet, workspace_repair_allowed: false}, "todo_target",
    {...agent, delivery_allowed: false}).admitted, false);
});

test("qualified scoped delivery retains its exact identity and current channel permission", () => {
  const packet: JsonObject = {...source("qualified"), normal_delivery_allowed: false,
    safe_bypass_allowed: true, safe_bypass_kind: "scoped_user_gate_fallback",
    selected_todo: {todo_id: "todo_target", selection_binding: "pending_action_selection"},
    scoped_user_gate_fallback: {selected_executable: {todo_id: "todo_target"}}};
  assert.equal(currentActionSelectionAdmission(packet, "todo_target", agent).admitted, true);
  assert.equal(packet.normal_delivery_allowed, false);
  for (const patch of [
    {safe_bypass_allowed: false}, {safe_bypass_kind: "unrelated_bypass"},
    {scoped_user_gate_fallback: null},
    {scoped_user_gate_fallback: {selected_executable: {todo_id: "todo_other"}}},
    {action_selection_qualification: {state: "deferred", requested_todo_id: "todo_target"}},
    {action_selection_qualification: {state: "qualified", requested_todo_id: "todo_other"}},
    {ok: false}, {should_run: false},
  ]) {
    assert.equal(currentActionSelectionAdmission({...packet, ...patch}, "todo_target", agent).admitted,
      false, JSON.stringify(patch));
  }
  for (const facts of [{must_attempt: false, delivery_allowed: true},
    {must_attempt: true, delivery_allowed: false}, {must_attempt: true, delivery_allowed: null}]) {
    assert.equal(currentActionSelectionAdmission(packet, "todo_target", facts).admitted, false);
  }
  assert.equal(currentActionSelectionAdmission(packet, "todo_other", agent).admitted, false);
});

test("pending delivery cannot borrow root permission from a denied or missing channel grant", () => {
  const packet = {...source(), normal_delivery_allowed: true,
    selected_todo: {todo_id: "todo_target", selection_binding: "pending_action_selection"}};
  for (const facts of [{must_attempt: false, delivery_allowed: true},
    {must_attempt: true, delivery_allowed: false}, {must_attempt: true, delivery_allowed: null}]) {
    assert.equal(currentActionSelectionAdmission(packet, "todo_target", facts).admitted, false);
    assert.equal(actionSelectionNeedsRecovery(packet, facts), true);
  }
});

test("compatibility annotations neither grant nor veto canonical current-work admission", () => {
  for (const legacy of [undefined, {}, {must_attempt_work: false}, {must_attempt_work: true}]) {
    const packet = {...source(), selected_todo: {todo_id: "todo_target"}};
    if (legacy === undefined) delete packet.execution_obligation;
    else packet.execution_obligation = legacy;
    assert.equal(currentActionSelectionAdmission(packet, "todo_target", agent).admitted, true);
    assert.equal(currentActionSelectionAdmission(packet, "todo_target", {...agent, must_attempt: false}).admitted, false);
  }
});

test("receipt bindings are left to reconciliation and qualified nested work is retained", () => {
  for (const field of ["selected_todo", "autonomous_replan_obligation"]) {
    assert.equal(actionSelectionNeedsRecovery({...source(), [field]: {selection_binding: "heartbeat_receipt"}}, agent), false);
  }
  const packet = source("qualified");
  (packet.action_selection_qualification as JsonObject).selected_todo = {todo_id: "todo_target"};
  assert.equal(currentActionSelectionAdmission(packet, "todo_target", agent).admitted, true);
  assert.equal(actionSelectionNeedsRecovery(packet, agent), false);
});

test("a missing recovery action is an error only on the recovery path", () => {
  const packet = source("rejected");
  delete (packet.action_selection_qualification as JsonObject).recovery_action;
  assert.equal(currentActionSelectionAdmission(packet, "todo_target", agent).admitted, false);
  assert.throws(() => actionSelectionNeedsRecovery(packet, agent), /omitted its typed recovery action/);
});

test("Todo grammar is shared without changing the callers' whitespace policies", () => {
  for (const [raw, expected] of [[" TODO_TARGET ", "todo_target"], ["\u001ctodo_target\u001c", "todo_target"],
    ["\ufefftodo_target\ufeff", null], ["todo_x", null], ["../todo_target", null], [["todo_target"], null]] as const) {
    assert.equal(currentActionSelectionAdmission({...source(), selected_todo: {todo_id: raw}}, "todo_target", agent).selected_todo_id, expected);
  }
  assert.equal(normalizeTodoId(" TODO_TARGET "), "todo_target");
  assert.equal(normalizeTodoId("\ufefftodo_target\ufeff"), "todo_target");
  assert.throws(() => normalizeTodoId("\u001ctodo_target\u001c"), /todo_<token>/);
  assert.throws(() => normalizeTodoId("../todo_target"), /todo_<token>/);
});

test("refusal keeps re-entry guidance but rejects stale work and settlement grants", () => {
  const packet = {...source("rejected"), ok: false, should_run: false, execution_obligation: {must_attempt_work: false}};
  const interaction = {agent_channel: {must_attempt: false, delivery_allowed: false},
    cli_channel: {spend_allowed_now: false, spend_after_validation: false, next_cli_actions: ["rerun the current guard"]}};
  assert.doesNotThrow(() => assertActionSelectionConsistency(packet, interaction));
  assert.throws(() => assertActionSelectionConsistency({...packet, safe_bypass_allowed: true}, interaction), /unadmitted/);
  assert.throws(() => assertActionSelectionConsistency(packet, {...interaction,
    cli_channel: {...interaction.cli_channel, selection_command: {}}}), /unadmitted/);
});
