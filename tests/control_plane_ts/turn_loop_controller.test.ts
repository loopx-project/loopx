import assert from "node:assert/strict";
import test from "node:test";
import { controllerRequest } from "../../loopx/control_plane/turn_driver/loop_controller.ts";
import { TURN_CONTROLLER_CONTRACT } from "../../loopx/control_plane/turn_driver/turn_contract_generated.ts";
import type { JsonObject } from "../../loopx/control_plane/runtime_decode.ts";

const identity = {goal_id: "fixture-goal", agent_id: "fixture-agent", todo_id: "fixture-todo"};
function input(kind = "validated_progress", action = "normal_run"): JsonObject {
  return {operation: "disposition", predecessor_turn_key: "fixture-turn",
    receipt: {result_kind: kind, lineage: identity, turn_key: "fixture-turn",
      host_failure: kind === "host_failure" ? {schema_version: "loopx_turn_host_failure_v0",
        kind: "provider_capacity", attempt: 1, retryable: true,
        retry: {strategy: "same_configuration", max_attempts: 3, backoff_seconds: 30}} : undefined,
      task_failure: kind === "validation_failed" ? {recovery_kind: "replan_required"} : undefined},
    decision: {...identity, schema_version: "loopx_turn_envelope_v0", should_run: true, effective_action: action,
      action_signature: {matches: true, source_hash: "same", envelope_hash: "same"},
      action: {must_attempt: true, delivery_allowed: true, selected_todo: {todo_id: identity.todo_id}}},
    budget: {lineage: identity, max_turns: "1", completed_turns: "1"}};
}

// Mutate the real imported contract only within a synchronous test, then restore
// it. The production request does not accept a caller-provided program.
type MutableRule = {id: string; when: Record<string, unknown>; [key: string]: unknown};
const contract = TURN_CONTROLLER_CONTRACT as unknown as {rules: MutableRule[]};
test("conjunction key order cannot change the actual owner's decisions", () => {
  const original = contract.rules;
  const cases = [input(), input("host_failure"), input("host_failure", "agent_workspace_repair"),
    input("validation_failed"), input("iteration_failed"), {...input(), receipt: null}];
  const expected = cases.map(controllerRequest);
  try {
    contract.rules = original.map(rule => ({...rule, when: Object.fromEntries(Object.entries(rule.when).reverse())}));
    assert.deepEqual(cases.map(controllerRequest), expected);
  } finally { contract.rules = original; }
});

for (const [specific, general, request] of [
  ["progress_exhausted", "progress_route", input()],
  ["host_replan", "failed_receipt", input("host_failure", "autonomous_replan")],
  ["host_repair", "failed_receipt", input("host_failure", "agent_workspace_repair")],
  ["host_retry", "failed_receipt", input("host_failure")],
  ["host_exhausted", "failed_receipt", {...input("host_failure"), receipt: {
    result_kind: "host_failure", lineage: identity, turn_key: "fixture-turn",
    host_failure: {schema_version: "loopx_turn_host_failure_v0", kind: "provider_capacity", attempt: 3,
      retryable: true, retry: {strategy: "same_configuration", max_attempts: 3, backoff_seconds: 120}}}}],
  ["task_validation_replan", "failed_receipt", input("validation_failed")],
] as const) {
  test(`${specific} must precede ${general} in the production owner`, () => {
    const original = contract.rules;
    const expected = controllerRequest(request);
    assert.equal(expected.ok, true);
    const moved = original.find(rule => rule.id === specific)!;
    assert.ok(original.indexOf(moved) < original.findIndex(rule => rule.id === general));
    try {
      const reordered = original.filter(rule => rule !== moved);
      reordered.splice(reordered.findIndex(rule => rule.id === general) + 1, 0, moved);
      contract.rules = reordered;
      assert.notDeepEqual(controllerRequest(request), expected);
    } finally { contract.rules = original; }
  });
}

test("earlier user/capability returns do not demand later budget or retry facts", () => {
  const request = input();
  request.budget = {not_a_budget: true};
  const decision = request.decision as JsonObject;
  decision.user = {action_required: true};
  assert.equal((controllerRequest(request).value as JsonObject).disposition, "user_action_required");
  delete decision.user;
  decision.effective_action = "governed_capability_intent";
  (decision.action as JsonObject).capability_intent = {schema_version: "pending_capability_intent_projection_v0",
    goal_id: identity.goal_id, agent_id: identity.agent_id, command: "fixture"};
  assert.equal((controllerRequest(request).value as JsonObject).disposition, "capability_action_required");
  const failure = input("host_failure", "agent_workspace_repair");
  ((failure.receipt as JsonObject).host_failure as JsonObject).retry = {unreadable_later_policy: true};
  assert.equal((controllerRequest(failure).value as JsonObject).disposition, "repair");
});

test("budget comparison preserves integers beyond JSON's exact numeric range", () => {
  const request = input();
  request.budget = {lineage: identity, max_turns: "9007199254740993", completed_turns: "9007199254740992"};
  assert.equal((controllerRequest(request).value as JsonObject).disposition, "run_now");
});

test("one input with multiple errors keeps the earlier causal refusal", () => {
  const request = input();
  request.predecessor_turn_key = "foreign-turn";
  request.budget = null;
  const response = controllerRequest(request);
  assert.equal(response.ok, false);
  assert.match(String(response.message), /predecessor_turn_key/);
  assert.equal(response.error_type, "ValueError");
});

for (const defect of ["remove_stop", "terminal_becomes_repair", "unregistered_output", "disconnected_table"]) {
  test(`independent expectations detect the live TS contract defect: ${defect}`, () => {
    const original = contract.rules;
    const request = defect === "remove_stop" ? input("iteration_failed") : {...input(), receipt: null};
    if (defect === "terminal_becomes_repair") {
      (request.decision as JsonObject).effective_action = "terminal_no_followup";
      (request.decision as JsonObject).state = "terminal_no_followup";
    }
    const expected = controllerRequest(request);
    assert.equal(expected.ok, true);
    try {
      contract.rules = structuredClone(original);
      if (defect === "remove_stop") contract.rules = contract.rules.filter(rule => rule.id !== "receipt_iteration_failed");
      else if (defect === "disconnected_table") contract.rules = [];
      else contract.rules.find(rule => rule.id === (defect === "terminal_becomes_repair" ? "initial_terminal" : "initial_route"))!.disposition = defect === "unregistered_output" ? "unknown" : "repair";
      assert.notDeepEqual(controllerRequest(request), expected);
    } finally { contract.rules = original; }
  });
}

test("empty legacy JSON values do not grant capability or signature admission", () => {
  for (const empty of [[], {}]) {
    const request = input();
    request.receipt = null;
    const decision = request.decision as JsonObject;
    decision.effective_action = "governed_capability_intent";
    (decision.action as JsonObject).capability_intent = {schema_version: "pending_capability_intent_projection_v0",
      goal_id: identity.goal_id, agent_id: identity.agent_id, command: empty};
    assert.equal(controllerRequest(request).ok, false);
    decision.action_signature = {matches: true, source_hash: empty, envelope_hash: empty};
    assert.equal(controllerRequest(request).ok, false);
  }
});
