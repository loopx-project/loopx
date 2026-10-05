import assert from "node:assert/strict";
import test from "node:test";
import {settlementPlanPayload} from "../../loopx/control_plane/effect_program.ts";
import {turnScopedCliSettlementPlan} from "../../loopx/control_plane/quota/settlement_plan.ts";

const input = {
  identity: {goal_id: "goal", agent_id: "agent", todo_id: "todo", turn_instance_id: "turn"},
  command_templates: {
    todo_completion: "ordinary completion", durable_writeback: "writeback",
    quota_spend: "spend", terminal_closeout: "terminal completion",
  },
};

test("ordinary completion is conditional on deliverable acceptance, not Turn accounting", () => {
  const plan = turnScopedCliSettlementPlan(input);
  assert.deepEqual(plan.steps.map(step => step.kind),
    ["validation", "durable_writeback", "quota_spend", "terminal_closeout"]);
  assert.equal(plan.steps[0].command_template, "ordinary completion");
  assert.equal(plan.steps[0].command_condition, "todo_deliverable_complete");
  assert.equal(plan.steps[0].conditional, undefined); // Validation itself is never optional.
  assert.match(plan.steps[0].precondition, /original declaration and current lease/);
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
  assert.match(plan.steps[1].precondition, /outcome_gap: blocked.*blocker\/evidence IDs.*continuation checks/);
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
  assert.match(plan.steps[1].precondition, /^validation succeeded/);
  assert.match(plan.steps[1].precondition, /Route elimination needs evidence/);
  assert.match(plan.steps[1].precondition, /failure alone is not progress/);
  assert.match(plan.steps[1].precondition, /outcome_gap: blocked.*blocker\/evidence IDs.*continuation checks/);
  assert.equal(plan.steps[1].command_template, "writeback");
  assert.equal(plan.steps[2].command_template, "spend");
  assert.equal(plan.identity.turn_instance_id, "turn");
});

test("incomplete command facts or ambiguous identity fail closed", () => {
  for (const key of ["todo_completion", "durable_writeback", "quota_spend", "terminal_closeout"]) {
    assert.throws(() => turnScopedCliSettlementPlan({...input,
      command_templates: {...input.command_templates, [key]: ""},
    }), /must be a non-empty string/);
  }
  assert.throws(() => turnScopedCliSettlementPlan({...input,
    identity: {...input.identity, replan_obligation_id: "replan"},
  }), /cannot bind both/);
});
