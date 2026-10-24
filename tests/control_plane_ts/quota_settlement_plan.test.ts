import assert from "node:assert/strict";
import test from "node:test";
import {settlementPlanPayload} from "../../loopx/control_plane/effect_program.ts";
import {visionAuthoringContract} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {projectMcpInteraction} from "../../loopx/control_plane/turn_driver/host_interaction.ts";
import {turnScopedCliSettlementPlan} from "../../loopx/control_plane/quota/settlement_plan.ts";
import {DELIVERY_OUTCOMES, decodeOptionalDeliveryOutcome, isTurnScopedSettlementOutcome}
  from "../../loopx/control_plane/work_items/delivery_outcome.ts";

const input = {
  identity: {goal_id: "goal", agent_id: "agent", todo_id: "todo", turn_instance_id: "turn"},
  delivery_batch_scale_choices: ["test_only", "single_surface", "multi_surface", "implementation"],
  command_templates: {
    todo_completion: "ordinary completion", durable_writeback: "writeback",
    quota_spend: "spend", terminal_closeout: "terminal completion",
  },
};

for (const lane of ["ordinary", "in_flight", "autonomous_replan"]) {
  test(`${lane} settlement offers only outcomes that its writer can qualify`, () => {
    const identity = lane === "autonomous_replan"
      ? {goal_id: "goal", agent_id: "agent", replan_obligation_id: "replan", turn_instance_id: "turn"}
      : input.identity;
    const plan = turnScopedCliSettlementPlan({...input, identity,
      ...(lane === "in_flight" ? {delivery_boundary: "in_flight_continuation"} : {}),
    });
    const guidance = plan.steps[1].precondition;
    const offered = guidance.match(/; outcome=([a-z_|]+)\./)?.[1].split("|");
    const binding = lane === "autonomous_replan" ? "replan" : "todo";
    const blocked = {schema_version: "typed_progress_observation_v0", result_class: "blocked",
      work_item_id: binding, blocker_id: "blocker", evidence_ids: ["evidence:failure"]};
    // The writer's qualification rule, rather than the projection's source list,
    // is the independent oracle for this Turn's advertised choices.
    assert.deepEqual(offered, DELIVERY_OUTCOMES.filter(outcome =>
      isTurnScopedSettlementOutcome(outcome, blocked, binding)));
    assert.equal(isTurnScopedSettlementOutcome("outcome_gap", undefined, binding), false);
    assert.equal(decodeOptionalDeliveryOutcome("surface_only"), "surface_only");
    assert.equal(isTurnScopedSettlementOutcome("surface_only", blocked, binding), false);
    assert.match(guidance, /do not relabel preparation-only work as progress/i);
  });
}

test("ordinary completion is conditional on deliverable acceptance, not Turn accounting", () => {
  const plan = turnScopedCliSettlementPlan(input);
  assert.deepEqual(plan.steps.map(step => step.kind),
    ["validation", "durable_writeback", "quota_spend", "terminal_closeout"]);
  assert.equal(plan.steps[0].command_template, "ordinary completion");
  assert.equal(plan.steps[0].command_condition, "todo_deliverable_complete");
  assert.equal(plan.steps[0].conditional, undefined); // Validation itself is never optional.
  assert.match(plan.steps[0].precondition, /original declaration and current lease/);
  assert.match(plan.steps[0].precondition, /Use --evidence for an artifact pointer/);
  assert.match(plan.steps[0].precondition,
    /--result-file requires approved Goal acceptance criteria bound to this Todo, not merely a Todo validator/);
  assert.match(plan.steps[0].precondition, /Todo done does not settle the Turn/);
  assert.match(plan.steps[1].precondition, /--agent-vision-json/);
  assert.match(plan.steps[1].precondition, /path_delta.*evidence_refs/);
  assert.match(plan.steps[1].precondition, /acceptance_summary/);
  assert.match(plan.steps[2].precondition, /declared completion validation/);
  assert.match(plan.steps[3].precondition, /matching writeback and quota spend receipts/);
  assert.equal(plan.steps[3].conditional, true);
  const wire = settlementPlanPayload(plan);
  assert.equal((wire.ordered_steps as Record<string, unknown>[])[0].command_condition,
    "todo_deliverable_complete");
  assert.deepEqual(input.command_templates, {
    todo_completion: "ordinary completion", durable_writeback: "writeback",
    quota_spend: "spend", terminal_closeout: "terminal completion",
  });
});

test("in-flight progress neither executes completion nor rewrites the declaration", () => {
  const plan = turnScopedCliSettlementPlan({...input, delivery_boundary: "in_flight_continuation"});
  assert.equal(plan.steps[0].command_template, undefined);
  assert.equal(plan.steps[0].command_condition, undefined);
  assert.match(plan.steps[0].precondition, /keep the Todo open/);
  assert.match(plan.steps[1].precondition, /^validation succeeded/);
  assert.match(plan.steps[1].precondition, /Route elimination needs evidence/);
  assert.match(plan.steps[1].precondition, /failure alone is not progress/);
  assert.match(
    plan.steps[1].precondition,
    /outcome_gap requires semantic_closeout.*blocker\/evidence IDs.*continuation checks/,
  );
  assert.equal(plan.steps[1].command_template, "writeback");
  assert.equal(plan.steps[2].command_template, "spend");
  assert.equal(plan.identity.turn_instance_id, "turn");
  assert.equal(plan.steps[3].conditional, true);
});

test("autonomous replan never manufactures a Todo completion step", () => {
  const plan = turnScopedCliSettlementPlan({...input,
    identity: {goal_id: "goal", agent_id: "agent", replan_obligation_id: "replan", turn_instance_id: "turn"},
    command_templates: {durable_writeback: "writeback", quota_spend: "spend"},
  });
  assert.deepEqual(plan.steps.map(step => step.kind), ["validation", "durable_writeback", "quota_spend"]);
  assert.equal(plan.steps[0].command_template, undefined);
  assert.equal(plan.identity.binding_kind, "autonomous_replan");
  assert.ok(plan.steps.every(step => step.vision_authoring === undefined));
  assert.match(plan.steps[1].precondition, /^validation succeeded/);
  assert.match(plan.steps[1].precondition, /Route elimination needs evidence/);
  assert.match(plan.steps[1].precondition, /failure alone is not progress/);
  assert.match(
    plan.steps[1].precondition,
    /outcome_gap requires semantic_closeout.*blocker\/evidence IDs.*continuation checks/,
  );
  assert.equal(plan.steps[1].command_template, "writeback");
  assert.equal(plan.steps[2].command_template, "spend");
  assert.equal(plan.identity.turn_instance_id, "turn");
});

test("incomplete command facts or ambiguous identity fail closed", () => {
  for (const choices of [undefined, [], [""], [1]]) {
    assert.throws(() => turnScopedCliSettlementPlan({...input, delivery_batch_scale_choices: choices}),
      /must be an array of strings|must be a non-empty string/);
  }
  for (const key of ["todo_completion", "durable_writeback", "quota_spend", "terminal_closeout"]) {
    assert.throws(() => turnScopedCliSettlementPlan({...input,
      command_templates: {...input.command_templates, [key]: ""},
    }), /must be a non-empty string/);
  }
  assert.throws(() => turnScopedCliSettlementPlan({...input,
    identity: {...input.identity, replan_obligation_id: "replan"},
  }), /cannot bind both/);
});


test("material CLI and MCP retain authoring while in-flight CLI omits material closeout guidance", () => {
  for (const delivery_boundary of [undefined, "in_flight_continuation"]) {
    const wire = settlementPlanPayload(turnScopedCliSettlementPlan({...input, delivery_boundary}));
    const steps = wire.ordered_steps as Record<string, unknown>[];
    const material = delivery_boundary === undefined;
    assert.equal(steps.filter(step => step.vision_authoring).length, material ? 1 : 0);
    assert.deepEqual(steps[1].vision_authoring, material ? visionAuthoringContract() : undefined);
    const mcp = projectMcpInteraction({ok: true, normal_delivery_allowed: true,
      selected_todo: {todo_id: "todo"}, interaction_contract: {cli_channel: {settlement_plan: wire}}});
    const contract = mcp.interaction_contract as Record<string, Record<string, unknown>>;
    assert.equal(contract.cli_channel.settlement_plan, undefined);
    // MCP complete_task is material completion even when the CLI can continue in-flight.
    assert.deepEqual(contract.mcp_channel.vision_authoring, visionAuthoringContract());
    assert.match(visionAuthoringContract().authoring_hint as string, /original committed Turn writeback/);
  }
});
