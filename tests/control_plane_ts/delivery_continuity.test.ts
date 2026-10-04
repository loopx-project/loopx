import assert from "node:assert/strict";
import test from "node:test";

import {
  DELIVERY_ROUTING_REQUEST_SCHEMA,
  evaluateDeliveryRoute,
} from "../../loopx/control_plane/turn_driver/delivery_continuity.ts";

function todo(overrides: Record<string, unknown> = {}) {
  return {
    todo_id: "todo_current001",
    status: "open",
    task_class: "advancement_task",
    claimed_by: "codex-main",
    actionable: true,
    capability_ready: true,
    ...overrides,
  };
}

function route(overrides: Record<string, unknown> = {}) {
  return evaluateDeliveryRoute({
    schema_version: DELIVERY_ROUTING_REQUEST_SCHEMA,
    agent_id: "codex-main",
    previous_todo_id: "todo_current001",
    previous_delivery_outcome: "outcome_progress",
    continuity_todo: todo(),
    fallback_todo: todo({ todo_id: "todo_queuehead001" }),
    preemptions: [],
    ...overrides,
  });
}

test("one routing transaction keeps continuity ahead of the fallback", () => {
  assert.deepEqual(route(), {
    schema_version: "loopx_delivery_routing_result_v0",
    selection: "continuity",
    continuity: {
      schema_version: "loopx_delivery_continuity_result_v0",
      decision: "resume_in_flight",
      reason: "same_open_todo_after_progress",
      todo_id: "todo_current001",
      delivery_boundary: "in_flight_continuation",
    },
    boundary: {
      schema_version: "loopx_delivery_boundary_result_v0",
      delivery_boundary: "in_flight_continuation",
      reason: "open_advancement_todo",
      todo_id: "todo_current001",
    },
  });
});

test("one routing transaction releases to fallback or no selection", () => {
  const fallback = route({ previous_delivery_outcome: "outcome_gap" });
  assert.equal(fallback.selection, "fallback");
  assert.equal(fallback.boundary?.todo_id, "todo_queuehead001");

  assert.deepEqual(route({
    previous_todo_id: null,
    previous_delivery_outcome: null,
    continuity_todo: null,
    fallback_todo: null,
  }), {
    schema_version: "loopx_delivery_routing_result_v0",
    selection: "none",
    continuity: null,
    boundary: null,
  });
});

test("an initially selected open advancement Todo settles as in flight", () => {
  const result = route({
    previous_todo_id: null,
    previous_delivery_outcome: null,
    continuity_todo: null,
    fallback_todo: todo(),
  });
  assert.equal(result.selection, "fallback");
  assert.deepEqual(result.boundary, {
    schema_version: "loopx_delivery_boundary_result_v0",
    delivery_boundary: "in_flight_continuation",
    reason: "open_advancement_todo",
    todo_id: "todo_current001",
  });
});

test("non-advancement and preempted selections settle strictly", () => {
  const nonAdvancement = route({
    previous_todo_id: null,
    previous_delivery_outcome: null,
    continuity_todo: null,
    fallback_todo: todo({ task_class: "monitor_task" }),
  });
  assert.equal(nonAdvancement.boundary?.delivery_boundary, "semantic_closeout");

  const preempted = route({
    previous_todo_id: null,
    previous_delivery_outcome: null,
    continuity_todo: null,
    fallback_todo: todo(),
    preemptions: ["autonomous_replan"],
  });
  assert.equal(preempted.boundary?.reason, "autonomous_replan");
});

test("terminal and outcome-gap anchors release normal reselection", () => {
  const terminal = route({
    continuity_todo: todo({ status: "done" }),
  });
  assert.equal(terminal.selection, "fallback");
  assert.equal(terminal.continuity?.reason, "todo_not_open");

  const outcomeGap = route({ previous_delivery_outcome: "outcome_gap" });
  assert.equal(outcomeGap.selection, "fallback");
  assert.equal(outcomeGap.continuity?.reason, "previous_delivery_not_progress");
});

test("typed control obligations preempt sticky delivery", () => {
  const result = route({
    fallback_todo: null,
    preemptions: ["autonomous_replan", "delivery_not_allowed"],
  });
  assert.equal(result.selection, "none");
  assert.equal(result.continuity?.decision, "preempt");
  assert.equal(result.continuity?.reason, "autonomous_replan");
  assert.equal(result.continuity?.delivery_boundary, "semantic_closeout");
});

function replanFacts(overrides: Record<string, unknown> = {}) {
  const obligation = "replan-0123456789abcdef";
  return {
    replan_todo: todo({todo_id: "todo_new_plan", replan_obligation_id: obligation}),
    latest_replan_ack: {
      schema_version: "autonomous_replan_ack_v0", recorded: true, agent_id: "codex-main",
      semantic_delta: {schema_version: "replan_semantic_delta_v0", accepted: true,
        outcomes: ["new_runnable_successor"], successor_todo_id: "todo_new_plan",
        obligation_id: obligation, successor_origin_obligation_id: obligation},
    },
    ...overrides,
  };
}

test("the accepted replan successor becomes the default ahead of the old route", () => {
  const preferred = route(replanFacts());
  assert.equal(preferred.selection, "replan");
  assert.equal(preferred.boundary?.todo_id, "todo_new_plan");
  assert.equal(preferred.continuity?.decision, "resume_in_flight");
  // Recommendation cannot change an already committed identity or open a gate.
  for (const preemption of ["heartbeat_receipt", "autonomous_replan", "blocking_work_lane", "delivery_not_allowed"]) {
    assert.notEqual(route({...replanFacts(), preemptions: [preemption]}).selection, "replan");
  }
});

test("replan preference rechecks current execution and exact causal ownership", () => {
  for (const patch of [{status: "done"}, {status: "blocked"}, {actionable: false},
    {capability_ready: false}, {claimed_by: "another-agent"}, {claimed_by: null},
    {task_class: "continuous_monitor"}, {replan_obligation_id: "replan-fedcba9876543210"}]) {
    assert.equal(route(replanFacts({replan_todo: todo({
      todo_id: "todo_new_plan", replan_obligation_id: "replan-0123456789abcdef", ...patch,
    })})).selection, "continuity");
  }
  const facts = replanFacts();
  const ack = facts.latest_replan_ack;
  for (const patch of [{recorded: false}, {agent_id: "another-agent"},
    {semantic_delta: {...ack.semantic_delta, accepted: false}},
    {semantic_delta: {...ack.semantic_delta, successor_todo_id: "todo_other_plan"}},
    {semantic_delta: {...ack.semantic_delta, outcomes: ["fresh_vision_path_outcome"]}}]) {
    assert.deepEqual(route({...facts, latest_replan_ack: {...ack, ...patch}}), route());
  }
  assert.deepEqual(route({latest_replan_ack: null, replan_todo: null}), route());
});

test("runtime decoder rejects malformed projection facts", () => {
  assert.throws(
    () => route({ preemptions: ["urgent prose"] }),
    /unsupported reason/,
  );
  assert.throws(
    () => route({ continuity_todo: todo({ actionable: "true" }) }),
    /continuity_todo.actionable must be a boolean/,
  );
  assert.throws(
    () => route({
      previous_todo_id: null,
      previous_delivery_outcome: null,
      continuity_todo: null,
      fallback_todo: todo({ capability_ready: "yes" }),
    }),
    /fallback_todo.capability_ready must be a boolean/,
  );
});
