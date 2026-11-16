import assert from "node:assert/strict";
import test from "node:test";
import {settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256 as hash} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {checkpointReadReceiptPurpose, evaluateCheckpointReadContext as evaluate,
  evaluateResultWorkBinding as work, type CheckpointReadPurpose} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";

const identity = settlementIdentityPayload({goal_id: "semantic-goal", agent_id: "agent",
  todo_id: "task", turn_instance_id: "original-turn"});
const facts = {
  todos: [{todo_id: "task", role: "agent", claimed_by: "agent", status: "open", text: "Validate the output."}],
  frontmatter: {objective: "A validated output"}, goal_prose: "Acceptance: the output is correct.",
  acceptance: {revision: 1}, agent_vision: {summary: "Validate the output"},
  source: {authority: "file_v0", store_identity: "original-store", registry_goal: {
    id: "semantic-goal", display_name: "Original label", coordination: {registered_agents: ["agent"]}}},
  execution_lease: {owner: "agent", idempotency_key: "original-execution", lease_epoch: 3,
    version: 7, status: "active", active: true, write_scopes: ["src/**"],
    write_repository: "git:example.test/team/project", expires_at: "2030-01-01T01:00:00Z", updated_at: "old"},
};

function read(use: CheckpointReadPurpose, current: JsonObject = facts, extra: JsonObject = {}) {
  return evaluate({phase: "read", purpose: use, identity, facts: current,
    read_context_id: "original-read", admitted_turn: true, full_frontier: true, decision_scope: "agent_lane",
    prior: use === "supplement_checkpoint" ? {vision_checkpoint: {decision: "missing_required", satisfied: false}} : null,
    ...extra});
}
function check(receipt: JsonObject, current: JsonObject = facts) {
  return evaluate({phase: "check", purpose: checkpointReadReceiptPurpose(receipt) ?? "delivery_result",
    identity, facts: current, read_context_id: "original-read", receipt});
}

// Historical encoding is independent of the new producer/projection. These
// receipts must keep the original full source/lease basis, not be upgraded.
function historical(use: CheckpointReadPurpose): JsonObject {
  const basis: JsonObject = {todo: facts.todos[0], goal: {frontmatter: facts.frontmatter,
    prose: facts.goal_prose, acceptance: facts.acceptance, user_todos: []},
    dependencies: [], agent_vision: facts.agent_vision, source: facts.source,
    frontier: {scope: "agent_lane", todos: facts.todos}};
  if (use !== "supplement_checkpoint") basis.execution_lease = facts.execution_lease;
  return {schema_version: use === "supplement_checkpoint" ? "checkpoint_read_context_v1" : "checkpoint_read_context_v2",
    ...(use === "supplement_checkpoint" ? {} : {purpose: use}), identity,
    read_context_id: "original-read", dependency_todo_ids: [], full_frontier: true, decision_scope: "agent_lane",
    versions: Object.fromEntries(Object.entries(basis).map(([key, value]) => [key, hash(value)])),
    ...(use === "delivery_result" ? {work_basis: basis} : {decision_basis: basis})};
}

for (const use of ["supplement_checkpoint", "first_delivery", "delivery_result"] as const) {
  test(`${use}: new semantic receipt survives pure display changes and normal renewal`, () => {
    const before = structuredClone(facts);
    const result = read(use), receipt = result.receipt as JsonObject;
    assert.equal(receipt.schema_version, "checkpoint_read_context_v3");
    assert.equal(receipt.purpose, use);
    const renewed = structuredClone(facts);
    Object.assign(renewed.execution_lease, {version: 8, expires_at: "2030-01-01T02:00:00Z", updated_at: "renewed"});
    assert.equal(check(receipt, renewed).ok, true);
    const displayed = structuredClone(facts);
    displayed.source.registry_goal.display_name = "Another label";
    assert.equal(check(receipt, displayed).ok, true);
    assert.equal(check(receipt).ok, true);
    assert.deepEqual(facts, before);
  });

  test(`${use}: historical receipt preserves its original interpretation and bytes`, () => {
    const receipt = historical(use), before = JSON.stringify(receipt);
    assert.equal(check(receipt).ok, true);
    const changed = structuredClone(facts);
    changed.source.registry_goal.display_name = "Another label";
    assert.deepEqual(check(receipt, changed).changed_components, ["source"]);
    if (use !== "supplement_checkpoint") {
      changed.source.registry_goal.display_name = facts.source.registry_goal.display_name;
      changed.execution_lease.version++;
      assert.deepEqual(check(receipt, changed).changed_components, ["execution_lease"]);
    }
    assert.equal(JSON.stringify(receipt), before);
  });
}

test("new work basis keeps execution lineage, current active status and unknown authority facts", () => {
  const receipt = read("delivery_result").receipt as JsonObject;
  for (const change of [{owner: "other"}, {idempotency_key: "new-execution"}, {lease_epoch: 4},
    {write_repository: "git:example.test/team/other"}, {write_scopes: ["other/**"]},
    {status: "released", active: false}, {active: false}, {future_authority: "changed"}]) {
    const changed = {...facts, execution_lease: {...facts.execution_lease, ...change}};
    assert.deepEqual(check(receipt, changed).changed_components, ["execution_lease"]);
  }
  for (const source of [{...facts.source, store_identity: "replacement"},
    {...facts.source, registry_goal: {...facts.source.registry_goal, status: "stopped"}},
    {...facts.source, registry_goal: {...facts.source.registry_goal, coordination: {registered_agents: []}}},
    {...facts.source, registry_goal: {...facts.source.registry_goal, future_permission: true}}]) {
    assert.deepEqual(check(receipt, {...facts, source}).changed_components, ["source"]);
  }
  assert.deepEqual(check(receipt, {...facts, goal_prose: "Acceptance changed."}).changed_components, ["goal"]);
});

test("new work and historical work preserve validation binding and detect damaged basis", () => {
  for (const receipt of [read("delivery_result").receipt as JsonObject, historical("delivery_result")]) {
    assert.equal(work({phase: "restore", identity, receipt}).ok, true);
    const candidate = {evidence: "validated-output"}, validation = {ok: true};
    const bound = work({phase: "bind_validation", identity, receipt, candidate, validation});
    assert.equal(bound.ok, true);
    assert.equal(work({phase: "check_validation", identity, receipt, candidate, validation, binding: bound.binding}).ok, true);
    assert.equal(work({phase: "check_validation", identity, receipt, candidate: {evidence: "replacement"},
      validation, binding: bound.binding}).error_code, "delivery_validation_binding_mismatch");
    const damaged = {...receipt, work_basis: {...receipt.work_basis as JsonObject, todo: {text: "replacement"}}};
    assert.equal(work({phase: "restore", identity, receipt: damaged}).error_code, "delivery_work_basis_corrupt");
  }
});

test("new and historical direction responses retain basis integrity", () => {
  const current = {...facts, todos: [{...facts.todos[0], status: "done", completion_turn_key: identity.turn_instance_id}]};
  const receipt = read("first_delivery", current, {checkpoint_continuation: true}).receipt as JsonObject;
  for (const original of [receipt, historical("supplement_checkpoint")]) {
    const decision = {read_context_id: "original-read", vision_unchanged_reason: "Current obligations preserve this route."};
    assert.equal(work({phase: "direction_response", identity, receipt: original, decision}).ok, true);
    assert.equal(work({phase: "direction_response", identity, receipt: {...original, decision_basis: {}}, decision}).error_code,
      "checkpoint_direction_basis_corrupt");
  }
});

test("unknown versions and mismatched purpose cannot acquire work or direction acceptance", () => {
  const receipt = read("delivery_result").receipt as JsonObject;
  for (const changed of [{...receipt, schema_version: "checkpoint_read_context_v99"},
    {...receipt, purpose: null}, {...receipt, schema_version: "checkpoint_read_context_v1"}]) {
    assert.equal(checkpointReadReceiptPurpose(changed), null);
    assert.equal(check(changed).error_code, "checkpoint_read_context_unknown_or_replaced");
    assert.equal(work({phase: "restore", identity, receipt: changed}).error_code, "delivery_work_identity_mismatch");
    assert.equal(work({phase: "direction_response", identity, receipt: changed, decision: {}}).error_code,
      "checkpoint_direction_context_invalid");
  }
});
