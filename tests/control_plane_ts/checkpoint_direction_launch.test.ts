import assert from "node:assert/strict";
import test from "node:test";
import {evaluateCheckpointReadContext as read, evaluateResultWorkBinding as work} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";
import {settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";

const identity = settlementIdentityPayload({goal_id: "launch-goal", agent_id: "agent", todo_id: "task", turn_instance_id: "original-turn"});
const facts = {todos: [{todo_id: "task", role: "agent", claimed_by: "agent", status: "open", text: "Validate output"}],
  frontmatter: {objective: "Validated output"}, goal_prose: "Preserve the original output.", acceptance: null,
  agent_vision: {summary: "Validate"}, source: {registry_path: "/original/registry.json", store_identity: "original-store",
    registry_goal: {id: "launch-goal", display_name: "Original label", coordination: {registered_agents: ["agent"]}}}};
function receipt(purpose: string, current: JsonObject = facts): JsonObject {
  const result = read({phase: "read", purpose, identity,
    facts: purpose === "first_delivery" ? {...current, todos: [{...facts.todos[0], status: "done", completion_turn_key: identity.turn_instance_id}]} : current,
    read_context_id: purpose, admitted_turn: true,
    checkpoint_continuation: purpose !== "delivery_result", full_frontier: true, decision_scope: "agent_lane",
    ...(purpose === "supplement_checkpoint" ? {prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}}} : {})});
  assert.equal(result.ok, true, JSON.stringify(result));
  return result.receipt as JsonObject;
}
const binding = (direction: JsonObject, original: unknown = receipt("delivery_result")) => work({
  phase: "direction_launch_context", identity, receipt: direction, work_receipt: original});

test("new direction may refresh a launch witness only within its verified original semantic source", () => {
  const original = receipt("delivery_result");
  const displayed = structuredClone(facts);
  displayed.source.registry_goal.display_name = "A new label";
  assert.deepEqual(binding(receipt("first_delivery", displayed), original), {ok: true, refresh_launch_witness: true});
  assert.deepEqual(binding(receipt("supplement_checkpoint", displayed), original), {ok: true, refresh_launch_witness: true});
  for (const source of [{...facts.source, registry_path: "/replacement/registry.json"},
    {...facts.source, store_identity: "replacement"},
    {...facts.source, registry_goal: {...facts.source.registry_goal, source_registry: "/other/source.json"}},
    {...facts.source, registry_goal: {...facts.source.registry_goal, future_permission: true}}]) {
    const changed = binding(receipt("first_delivery", {...facts, source}), original);
    assert.equal(changed.error_code, "checkpoint_direction_source_changed");
    assert.equal(changed.reread_required, false);
  }
  const direction = receipt("first_delivery");
  assert.equal(binding({...direction, decision_basis: {}}).error_code, "checkpoint_direction_basis_corrupt");
  assert.equal(binding(direction, {...original, identity: {...identity, agent_id: "foreign"}}).error_code, "delivery_work_identity_mismatch");
  assert.equal(binding(direction, {...original, work_basis: {}}).error_code, "delivery_work_basis_corrupt");
});

test("historical and missing work receipts cannot refresh a raw launch witness", () => {
  const direction = receipt("first_delivery");
  for (const original of [null, {...receipt("delivery_result"), schema_version: "checkpoint_read_context_v2"}]) {
    assert.deepEqual(binding(direction, original), {ok: true, refresh_launch_witness: false});
  }
});

test("known pre-launch refusal is recoverable only explicitly; unknown attempts and real stops stay closed", () => {
  const direction = {...receipt("first_delivery"), direction_host_attempt_count: 1};
  const dispatch = (r: JsonObject, explicit_resume = true) => work({phase: "direction_dispatch", receipt: r, explicit_resume});
  assert.equal(dispatch(direction).error_code, "checkpoint_direction_launch_outcome_unknown");
  assert.equal(dispatch(direction).reread_required, false);
  assert.equal(work({phase: "direction_launch_failure", receipt: direction,
    error_code: "stale_goal_authority", not_started: false}).error_code, "checkpoint_direction_launch_outcome_unknown");
  for (const code of ["stale_goal_authority", "checkpoint_read_context_stale", "goal_stopped", "unrecognized_failure"]) {
    const refused = work({phase: "direction_launch_failure", receipt: direction, error_code: code, not_started: true}).receipt as JsonObject;
    assert.equal(dispatch(refused, false).error_code, "checkpoint_direction_stopped");
    assert.equal(dispatch(refused).ok, ["stale_goal_authority", "checkpoint_read_context_stale"].includes(code));
    assert.equal(direction.direction_host_attempt_count, 1);
  }
});

test("a new read retains proof of the previous returned direction without adopting its old judgment", () => {
  const original = {...receipt("first_delivery"), direction_host_attempt_count: 1};
  const decision = {read_context_id: original.read_context_id, vision_unchanged_reason: "The original route is justified."};
  const returned = work({phase: "direction_return", identity, receipt: original, decision}).receipt as JsonObject;
  const renewed = read({phase: "read", purpose: "first_delivery", identity,
    facts: {...facts, goal_prose: "Acceptance changed.", todos: [{...facts.todos[0], status: "done", completion_turn_key: identity.turn_instance_id}]},
    read_context_id: "new-direction-read", receipt: returned, admitted_turn: true,
    checkpoint_continuation: true, full_frontier: true, decision_scope: "agent_lane"}).receipt as JsonObject;
  assert.equal(renewed.current_agent_decision, undefined);
  assert.equal((renewed.prior_direction_response as JsonObject).attempt, 1);
  assert.equal(work({phase: "direction_dispatch", receipt: renewed}).attempt, 2);
  const proof = renewed.prior_direction_response as JsonObject;
  assert.equal(work({phase: "direction_dispatch", receipt: {...renewed, prior_direction_response: {
    ...proof, binding: {...proof.binding as JsonObject, decision_sha256: "damaged"}}}}).error_code,
    "checkpoint_direction_launch_outcome_unknown");
  assert.equal(work({phase: "direction_dispatch", receipt: {...renewed, direction_host_attempt_count: 2}}).error_code,
    "checkpoint_direction_launch_outcome_unknown");
  const unknown = read({phase: "read", purpose: "first_delivery", identity,
    facts: {...facts, todos: [{...facts.todos[0], status: "done", completion_turn_key: identity.turn_instance_id}]},
    read_context_id: "replacement-read", receipt: {...returned, schema_version: "checkpoint_read_context_v99",
      prior_direction_response: proof}, admitted_turn: true,
    checkpoint_continuation: true, full_frontier: true, decision_scope: "agent_lane"}).receipt as JsonObject;
  assert.equal(unknown.prior_direction_response, undefined);
  assert.equal(work({phase: "direction_dispatch", receipt: unknown}).error_code, "checkpoint_direction_launch_outcome_unknown");
});
