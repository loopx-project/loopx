import assert from "node:assert/strict";
import test from "node:test";
import {settlementPlanPayload} from "../../loopx/control_plane/effect_program.ts";
import {visionAuthoringContract} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {nativeCliSettlementProjection, turnScopedCliSettlementPlan}
  from "../../loopx/control_plane/quota/settlement_plan.ts";
import {EXECUTION_PREPARATION_SCHEMA_VERSION} from "../../loopx/control_plane/work_items/execution_preparation.ts";
import {DELIVERY_OUTCOMES, decodeOptionalDeliveryOutcome, isTurnScopedSettlementOutcome}
  from "../../loopx/control_plane/work_items/delivery_outcome.ts";

const input = {
  identity: {goal_id: "goal", agent_id: "agent", todo_id: "todo", turn_instance_id: "turn"},
  delivery_batch_scale_choices: ["test_only", "single_surface", "multi_surface", "implementation"],
  command_templates: {
    todo_claim: "ordinary claim",
    todo_completion: "ordinary completion", durable_writeback: "writeback",
    quota_spend: "spend", terminal_closeout: "terminal completion",
  },
};

function assertCombinedWriteback(result: ReturnType<typeof nativeCliSettlementProjection>, writebackIndex: number) {
  assert.ok(Array.isArray(result.changes));
  assert.ok(result.changes.some(change =>
    change.path.join("/") === "settlement_plan/schema_version" && change.value === "quota_settlement_plan_v2"));
  assert.ok(result.changes.some(change =>
    change.path.join("/") === `next_cli_actions/${writebackIndex}` &&
    change.value === "writeback --settle-turn --settlement-receipt"));
  assert.ok(result.changes.some(change =>
    change.path.join("/") === `next_cli_actions/${writebackIndex + 1}` && change.remove === true));
  assert.ok(result.changes.every(change => !change.path.includes("identity")));
}

test("native settlement only replaces matching executable steps on the original ready Turn", () => {
  const plan = settlementPlanPayload(turnScopedCliSettlementPlan(input));
  const preparation = {schema_version: EXECUTION_PREPARATION_SCHEMA_VERSION, state: "ready",
    todo_id: "todo", agent_id: "agent", delivery_allowed: true};
  const cli = {settlement_plan: plan, execution_preparation: preparation, spend_after_validation: true,
    next_cli_actions: ["supporting action", "writeback", "spend"]};
  const receipt = {status: "upgraded", closeout_required: true, turn_instance_id: "turn",
    settlement_identity: plan.identity};
  const request = {cli, receipt, todo_id: "todo"};
  for (const status of ["committed", "upgraded", "replayed"])
    assertCombinedWriteback(nativeCliSettlementProjection({...request, receipt: {...receipt, status}}), 1);
  for (const changed of [
    {todo_id: "other"},
    {receipt: {...receipt, turn_instance_id: "other"}},
    {receipt: {...receipt, settlement_identity: {...plan.identity as object, effect_id: "changed"}}},
    {receipt: {...receipt, settlement_binding_owed: true}},
    {receipt: {...receipt, closeout_required: false}},
    {receipt: {...receipt, status: "pending"}},
    {cli: {...cli, execution_preparation: {...preparation, state: "claim_required"}}},
    {cli: {...cli, spend_after_validation: false}},
    {cli: {...cli, next_cli_actions: ["spend", "writeback"]}},
    {cli: {...cli, next_cli_actions: ["writeback", "spend", "spend"]}},
  ]) assert.deepEqual(nativeCliSettlementProjection({...request, ...changed}), {});
  assert.deepEqual(cli.next_cli_actions, ["supporting action", "writeback", "spend"]);
});

test("complete consumers compose the original admitted CLI plan without inventing readiness", () => {
  const plan = settlementPlanPayload(turnScopedCliSettlementPlan(input));
  const cli = {settlement_plan: plan, spend_after_validation: true,
    next_cli_actions: ["writeback", "spend"]};
  const receipt = {status: "committed", closeout_required: true, turn_instance_id: "turn",
    settlement_identity: plan.identity};
  const scope = {goal_id: "goal", agent_id: "agent", should_run: true,
    effective_action: "normal_run", normal_delivery_allowed: true, delivery_allowed: true};
  const request = {cli, receipt, todo_id: "todo", complete_scope: scope};
  const before = structuredClone(request);
  assertCombinedWriteback(nativeCliSettlementProjection(request), 0);
  assert.deepEqual(nativeCliSettlementProjection({cli, receipt, todo_id: "todo"}), {});
  for (const changed of [{complete_scope: null}, {complete_scope: {}},
    {cli: {...cli, spend_after_validation: false}}, {todo_id: "other"},
    {receipt: {...receipt, status: "pending"}}, {receipt: {...receipt, settlement_binding_owed: true}},
    {receipt: {...receipt, settlement_identity: {...plan.identity as object, turn_instance_id: "other"}}}])
    assert.deepEqual(nativeCliSettlementProjection({...request, ...changed}), {});
  for (const changed of [{goal_id: "other"}, {agent_id: "other"}, {delivery_allowed: false},
    {normal_delivery_allowed: false}, {should_run: false}, {effective_action: "replan_required"}])
    assert.deepEqual(nativeCliSettlementProjection({...request, complete_scope: {...scope, ...changed}}), {});
  const prepared = {schema_version: EXECUTION_PREPARATION_SCHEMA_VERSION, state: "ready",
    todo_id: "todo", agent_id: "agent", delivery_allowed: true};
  assertCombinedWriteback(nativeCliSettlementProjection({...request, cli: {...cli, execution_preparation: prepared}}), 0);
  for (const execution_preparation of [null, "ready", {}, {...prepared, state: "claim_required"},
    {...prepared, todo_id: "other"}, {...prepared, delivery_allowed: false}])
    assert.deepEqual(nativeCliSettlementProjection({...request, cli: {...cli, execution_preparation}}), {});
  assert.deepEqual(request, before);
});

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
  assert.match(plan.steps[0].precondition, /unfinished Todo open with its original declaration/);
  assert.doesNotMatch(plan.steps[0].precondition, /Prior-feedback|semantic_closeout/);
  assert.match(plan.steps[1].precondition, /new evidence produced or obtained by this admitted work.*artifact\/input/);
  assert.match(plan.steps[1].precondition, /Prior-feedback readback.*deterministic repetition without new information are context/);
  assert.match(plan.steps[1].precondition, /not this Turn's progress; use them to advance the task first/);
  assert.match(plan.steps[1].precondition, /Follow the remaining plan.*yield for freshly guarded continuation/);
  assert.doesNotMatch(plan.steps[0].precondition, /spend before the next decision/);
  assert.match(plan.steps[1].precondition, /--agent-vision-json/);
  assert.match(plan.steps[1].precondition, /path_delta.*evidence_refs/);
  assert.match(plan.steps[1].precondition, /acceptance_summary/);
  assert.match(plan.steps[1].precondition, /typed progress flags only when this Turn's evidence supports/);
  assert.match(plan.steps[1].precondition, /--progress-coverage-scope-id from verified scope coverage/);
  assert.match(plan.steps[1].precondition, /terminal closeout follows spend/);
  assert.doesNotMatch(plan.steps[1].command_template!, /--progress-coverage-scope-id/);
  assert.match(plan.steps[2].precondition, /declared completion validation/);
  assert.match(plan.steps[3].precondition, /matching writeback and quota spend receipts/);
  assert.equal(plan.steps[3].conditional, true);
  const wire = settlementPlanPayload(plan);
  assert.equal((wire.ordered_steps as Record<string, unknown>[])[0].command_condition,
    "todo_deliverable_complete");
  assert.deepEqual(input.command_templates, {
    todo_claim: "ordinary claim",
    todo_completion: "ordinary completion", durable_writeback: "writeback",
    quota_spend: "spend", terminal_closeout: "terminal completion",
  });
});

test("canonical hard-lease guidance carries execution preparation without changing other modes", () => {
  const original = turnScopedCliSettlementPlan(input);
  for (const handoff_mode of [undefined, null, "soft_claim", "legacy"]) {
    assert.deepEqual(turnScopedCliSettlementPlan({...input, handoff_mode}), original);
  }
  const hard = turnScopedCliSettlementPlan({...input, handoff_mode: "hard_lease"});
  assert.match(hard.steps[0].precondition, /ordinary Todo claim: `ordinary claim`/);
  assert.match(hard.steps[0].command_template!, /--claim-operation-id '<original claim_operation_id>'/);
  assert.match(hard.steps[3].command_template!, /--claim-operation-id '<original claim_operation_id>'/);
  assert.deepEqual(hard.identity, original.identity);
  assert.deepEqual(hard.steps.slice(1, 3), original.steps.slice(1, 3));
  const explicit = turnScopedCliSettlementPlan({...input, handoff_mode: "hard_lease",
    completion_proof_supplied: true});
  assert.equal(explicit.steps[0].command_template, original.steps[0].command_template);
  assert.equal(explicit.steps[3].command_template, original.steps[3].command_template);
  assert.throws(() => turnScopedCliSettlementPlan({...input, handoff_mode: "hard_lease",
    command_templates: {...input.command_templates, todo_claim: ""}}), /must be a non-empty string/);
});

test("in-flight validation offers completion only after original deliverable acceptance", () => {
  const plan = turnScopedCliSettlementPlan({...input, delivery_boundary: "in_flight_continuation"});
  assert.equal(plan.steps[0].command_template, "ordinary completion");
  assert.equal(plan.steps[0].command_condition, "todo_deliverable_complete");
  assert.equal(plan.steps[0].conditional, undefined);
  assert.match(plan.steps[0].precondition, /unfinished Todo open with its original declaration/);
  assert.match(plan.steps[1].precondition, /same Turn; no new guard or binding/);
  assert.match(plan.steps[1].precondition, /^validation succeeded/);
  assert.match(plan.steps[1].precondition, /Route elimination needs evidence/);
  assert.match(plan.steps[1].precondition, /failure alone is not progress/);
  assert.match(plan.steps[1].precondition, /semantic_closeout.*new decision evidence.*fresh Vision\/path_delta/i);
  assert.match(plan.steps[1].precondition, /including continue\/no_change when retaining the route/);
  assert.match(
    plan.steps[1].precondition,
    /outcome_gap requires semantic_closeout.*blocker\/evidence IDs.*continuation checks/,
  );
  assert.equal(plan.steps[1].command_template, "writeback");
  assert.equal(plan.steps[2].command_template, "spend");
  assert.equal(plan.identity.turn_instance_id, "turn");
  assert.equal(plan.steps[3].conditional, true);
});

test("semantic feedback checkpoint is an exact variant of the current writeback, not another plan", () => {
  for (const delivery_boundary of [undefined, "in_flight_continuation"]) {
    const before = turnScopedCliSettlementPlan({...input, delivery_boundary});
    const checkpoint = "same bound writeback --delivery-boundary semantic_closeout --agent-vision-json <file>";
    const plan = turnScopedCliSettlementPlan({...input, delivery_boundary,
      command_templates: {...input.command_templates, semantic_closeout: checkpoint}});
    assert.deepEqual(plan.identity, before.identity);
    assert.equal(plan.steps.length, before.steps.length);
    for (const i of [0, 2, 3]) assert.deepEqual(plan.steps[i], before.steps[i]);
    assert.equal(plan.steps[1].command_template, before.steps[1].command_template);
    assert.equal(plan.steps[1].semantic_closeout_command_template, checkpoint);
    if (delivery_boundary === "in_flight_continuation") {
      assert.match(plan.steps[1].precondition,
        /Use command_template for ordinary in_flight_continuation without new decision evidence/);
    }
    assert.match(plan.steps[1].precondition,
      /Use semantic_closeout_command_template with --agent-vision-json for new decision evidence, route change or Todo completion/);
    assert.deepEqual(plan.steps[1].vision_authoring, visionAuthoringContract());
    const serialized = settlementPlanPayload(plan).ordered_steps as Record<string, unknown>[];
    assert.equal(serialized[1].semantic_closeout_command_template, checkpoint);
    assert.match(String(serialized[2].precondition), /exact original-identity durable writeback receipt/);
    assert.match(String(serialized[2].precondition), /that receipt qualifies.*fresh open-Todo semantic checkpoint/);
    assert.match(String(serialized[2].precondition), /does not waive terminal completion validation/);
    assert.match(String(serialized[2].precondition), /skip this step after typed_blocked_writeback_no_spend/);
    assert.match(visionAuthoringContract().continuation_review as string,
      /candidate usability alone is not completion.*full marginal cost/);
    assert.match(visionAuthoringContract().continuation_review as string,
      /useful authorized work.*preserve Stop, budget\/permission limits and genuine waits/);
  }
  assert.throws(() => turnScopedCliSettlementPlan({...input,
    command_templates: {...input.command_templates, semantic_closeout: ""}}), /must be a non-empty string/);
});

test("Todo-bound semantic planning shares its exact autonomous authoring contract", () => {
  const ordinary = turnScopedCliSettlementPlan(input);
  assert.deepEqual(turnScopedCliSettlementPlan({...input, autonomous_replan: false}), ordinary);
  const replan = turnScopedCliSettlementPlan({...input, autonomous_replan: true});
  assert.deepEqual(replan.steps[1].vision_authoring, visionAuthoringContract(true));
  assert.match(replan.steps[1].precondition, /Autonomous replan uses --delivery-boundary semantic_closeout/);
  assert.doesNotMatch(replan.steps[1].precondition, /outcome_progress may use in_flight_continuation/);
  assert.deepEqual(replan.identity, ordinary.identity);
  for (const i of [0, 2, 3]) assert.deepEqual(replan.steps[i], ordinary.steps[i]);
  for (const autonomous_replan of [null, "true", 1]) {
    assert.throws(() => turnScopedCliSettlementPlan({...input, autonomous_replan}), /must be a boolean/);
  }
});

test("autonomous replan never manufactures a Todo completion step", () => {
  const plan = turnScopedCliSettlementPlan({...input,
    identity: {goal_id: "goal", agent_id: "agent", replan_obligation_id: "replan", turn_instance_id: "turn"},
    command_templates: {durable_writeback: "writeback", quota_spend: "spend"},
  });
  assert.deepEqual(plan.steps.map(step => step.kind), ["validation", "durable_writeback", "quota_spend"]);
  assert.equal(plan.steps[0].command_template, undefined);
  assert.equal(plan.identity.binding_kind, "autonomous_replan");
  assert.match(plan.steps[1].precondition, /Autonomous replan uses --delivery-boundary semantic_closeout/);
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


test("material CLI retains authoring while in-flight CLI omits material closeout guidance", () => {
  for (const delivery_boundary of [undefined, "in_flight_continuation"]) {
    const wire = settlementPlanPayload(turnScopedCliSettlementPlan({...input, delivery_boundary}));
    const steps = wire.ordered_steps as Record<string, unknown>[];
    const material = delivery_boundary === undefined;
    assert.equal(steps.filter(step => step.vision_authoring).length, material ? 1 : 0);
    assert.deepEqual(steps[1].vision_authoring, material ? visionAuthoringContract() : undefined);
  }
});
