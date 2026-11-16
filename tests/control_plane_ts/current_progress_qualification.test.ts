import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256 as sha} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {checkpointBasisSnapshot, evaluateCheckpointReadContext as read, evaluateResultWorkBinding as work}
  from "../../loopx/control_plane/goals/checkpoint_read_context.ts";

const identity = settlementIdentityPayload({goal_id: "progress-goal", agent_id: "agent",
  todo_id: "todo_original", turn_instance_id: "original-turn"});
function prepare(change: (facts: JsonObject) => void = () => {}, validation = "none") {
  const original: JsonObject = {todos: [{todo_id: "todo_original", role: "agent", claimed_by: "agent", status: "open",
    completion_validation_required: true, completion_validation_sha256: "1".repeat(64), completion_validation_revision: 1}],
    frontmatter: {objective: "Keep exact progress"}, goal_prose: "Final acceptance is separate.", acceptance: null, agent_vision: null,
    source: {registry_path: "/original/registry.json", store_identity: "original-store",
      registry_goal: {id: "progress-goal", display_name: "Original label"}},
    execution_lease: {schema_version: "task_lease_v0", goal_id: "progress-goal", todo_id: "todo_original", owner: "agent",
      idempotency_key: "original-key", lease_epoch: 1, version: 1, status: "active", active: true,
      acquire_ttl_seconds: 1800, acquired_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
      expires_at: "2099-01-01T00:00:00Z", write_scopes: []}};
  const captured = read({phase: "read", purpose: "delivery_result", identity, facts: original,
    admitted_turn: true, read_context_id: "original-work", checkpoint_continuation: true, decision_scope: "agent_lane"});
  assert.equal(captured.ok, true);
  const receipt = captured.receipt as JsonObject;
  if (validation !== "none") receipt.terminal_validation_effect = {
    schema_version: "terminal_validation_effect_v1", status: "passed", updates: {}, updates_sha256: sha({}),
    ...(validation === "passed" ? {binding_sha256: "a".repeat(64)} : {})};
  const retained = {settlement_identity: identity, result_work_receipt_sha256: "b".repeat(64), work_receipt_sha256: sha(receipt)};
  const artifacts = {json_path: "/original/R.json", markdown_path: "/original/R.md"};
  const current = structuredClone(original); change(current);
  const prior = {vision_checkpoint: {decision: "missing_required", satisfied: false}};
  const direction = read({phase: "read", purpose: "supplement_checkpoint", identity, facts: current,
    prior, read_context_id: "current-direction", checkpoint_continuation: true, full_frontier: true, decision_scope: "agent_lane"});
  assert.equal(direction.ok, true);
  const directionReceipt = {...direction.receipt as JsonObject, retained_result_binding: retained, retained_result_artifacts: artifacts};
  const request = {phase: "progress_qualification", identity, receipt, retained_result_binding: retained,
    retained_result_artifacts: artifacts, direction_receipt: directionReceipt,
    current_work: checkpointBasisSnapshot({phase: "read", purpose: "delivery_result", identity, facts: current})};
  return {original, current, receipt, directionReceipt, retained, prior, request,
    qualify: () => work(request).qualification as JsonObject};
}

test("current progress names its validation owner without treating a missing or malformed result as passed", () => {
  for (const validation of ["none", "malformed", "passed"]) {
    const p = prepare(undefined, validation).qualify();
    assert.deepEqual(p.changed_work_components, []);
    assert.equal((p.validation_contract as JsonObject).todo_completion instanceof Object, true);
    assert.equal(((p.validation_contract as JsonObject).progress_validation as JsonObject).required, false);
    assert.equal(p.original_validation_reusable, validation === "passed");
  }
  const changed = prepare(f => { f.goal_prose = "New final acceptance"; }, "passed").qualify();
  assert.deepEqual(changed.changed_work_components, ["goal"]);
  assert.equal(changed.original_validation_reusable, false);
});

test("unrelated peers, display and same-owner renewal preserve related work; final validation changes do not", () => {
  const changes = [
    (f: JsonObject) => (f.todos as JsonObject[]).push({todo_id: "peer", role: "agent", claimed_by: "other", status: "open"}),
    (f: JsonObject) => { ((f.source as JsonObject).registry_goal as JsonObject).display_name = "New label"; },
    (f: JsonObject) => { Object.assign(f.execution_lease as JsonObject, {version: 2,
      updated_at: "2026-01-01T00:01:00Z", expires_at: "2099-01-02T00:00:00Z"}); },
  ];
  for (const change of changes) assert.deepEqual(prepare(change).qualify().changed_work_components, []);
  const original = prepare().qualify();
  const changed = prepare(f => { (f.todos as JsonObject[])[0].completion_validation_sha256 = "2".repeat(64); }).qualify();
  assert.deepEqual(changed.changed_work_components, ["todo"]);
  assert.notEqual(changed.validation_contract_sha256, original.validation_contract_sha256);
});

test("unavailable work source, owner, lease or epoch never becomes a current progress grant", () => {
  for (const change of [
    (f: JsonObject) => { (f.source as JsonObject).store_identity = "replacement"; },
    (f: JsonObject) => { (f.todos as JsonObject[])[0].claimed_by = "another-agent"; },
    (f: JsonObject) => { (f.execution_lease as JsonObject).active = false; },
    (f: JsonObject) => { (f.execution_lease as JsonObject).lease_epoch = 2; },
  ]) assert.throws(prepare(change).qualify, (error: unknown) =>
    (error as {code?: string}).code === "checkpoint_direction_source_changed");
});

test("a known legacy direction return is retained as old outcome evidence without adopting its judgment", () => {
  const f = prepare();
  const receipt: JsonObject = {...f.directionReceipt, direction_host_attempt_count: 1};
  delete receipt.retained_result_artifacts;
  const decision = {read_context_id: receipt.read_context_id, agent_vision: {state: "vision_patch_proposed"}};
  receipt.current_agent_decision = decision;
  receipt.current_agent_binding = {read_context_id: receipt.read_context_id, identity_sha256: sha(identity),
    decision_sha256: sha(decision), retained_result_binding_sha256: sha(f.retained)};
  const renewed = read({phase: "read", purpose: "supplement_checkpoint", identity,
    facts: {...f.current, goal_prose: "Changed current requirements"}, prior: f.prior, receipt,
    read_context_id: "new-direction", checkpoint_continuation: true, full_frontier: true, decision_scope: "agent_lane"});
  assert.equal(renewed.ok, true);
  const next = renewed.receipt as JsonObject;
  assert.equal(next.current_agent_decision, undefined);
  assert.equal((next.prior_direction_response as JsonObject).read_context_id, receipt.read_context_id);
  assert.equal(work({phase: "direction_dispatch", receipt: next}).attempt, 2);
});
