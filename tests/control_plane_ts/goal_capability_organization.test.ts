import assert from "node:assert/strict";
import test from "node:test";
import { inspectImprovementPolicy, normalizeImprovementPolicy, planImprovementConfiguration, planCapabilityImprovement } from "../../loopx/control_plane/capabilities/goal_capability_organization.ts";
import { evaluateGoalAgentContext } from "../../loopx/control_plane/goal_agent_context.ts";
import { evaluateSubagentContext } from "../../loopx/control_plane/subagent_context.ts";

const scope = { goal_id: "example", agent_id: "coordinator", todo_id: "todo-example" };
const policy = { mode: "bounded", discovery_budget_minutes: 5, max_trials: 1 };
const input = { phase: "before_plan", scope, capability_improvement: policy };
const trial = { capability_id: "example-source", applicable: true, enabled: false,
  configuration_ref: "owner/config-v1", effect_ref: "experiment/baseline", rollback_ref: "owner/rollback" };

test("configuration is default-off, bounded and never a capability enablement switch", () => {
  assert.deepEqual(normalizeImprovementPolicy(null), { ...policy, mode: "off" });
  assert.deepEqual(planImprovementConfiguration({ current: policy, patch: { max_trials: 0 } }),
    { configuration: { ...policy, max_trials: 0 } });
  assert.deepEqual(planImprovementConfiguration({ current: policy, clear: true }), { configuration: null });
  for (const patch of [{ mode: "auto_install" }, { mode: ["bounded"] }, { enabled: true }, { max_trials: true },
    { max_trials: 3 }, { discovery_budget_minutes: 31 }, { discovery_budget_minutes: 0 }]) {
    assert.throws(() => planImprovementConfiguration({ patch }));
  }
  assert.throws(() => planImprovementConfiguration({ clear: true, patch: policy }));
  assert.deepEqual(inspectImprovementPolicy({ ...policy, max_trials: 500 }), {
    mode: "off", discovery_budget_minutes: 5, max_trials: 1, configuration_status: "invalid",
  });
  assert.deepEqual(planImprovementConfiguration({ current: { mode: "invalid" }, clear: true }),
    { configuration: null });
});

test("routine wake without a Goal gap produces no discovery, no task and no state", () => {
  assert.equal(planCapabilityImprovement(policy, {}).reason_code, "no_goal_gap");
  assert.equal(planCapabilityImprovement(null, { gap_ref: "example/gap" }).reason_code, "improvement_off");
  const gap = planCapabilityImprovement(policy, { gap_ref: "example/gap" });
  assert.equal(gap.recommendation, "bounded_discovery");
  assert.equal(gap.execution_authorized, false);
  assert.equal(gap.discovery_budget_minutes, 5);
});

test("direct-first: already-enabled applicable capability wins over a proposed trial", () => {
  const direct = { capability_id: "example-direct", configuration_ref: "original-owner/v2", enabled: true, applicable: true };
  const before = structuredClone([trial, direct]);
  const plan = planCapabilityImprovement(policy, { gap_ref: "example/gap", candidates: before });
  assert.equal(plan.recommendation, "inspect_direct_capability");
  assert.equal(plan.capability_id, direct.capability_id);
  assert.deepEqual(before, [trial, direct]);
  assert.equal(plan.execution_authorized, false);
});

test("trial needs applicability, explicit off state, owner, effect and rollback refs", () => {
  const plan = (candidate: object) => planCapabilityImprovement(policy, { gap_ref: "example/gap", candidates: [candidate] });
  assert.equal(plan(trial).recommendation, "propose_reversible_trial");
  for (const patch of [{ applicable: false }, { enabled: undefined }, { configuration_ref: null },
    { effect_ref: null }, { rollback_ref: null }, { rollback_ref: "/private/secret" }]) {
    assert.equal(plan({ ...trial, ...patch }).recommendation, "continue_current_work");
  }
  assert.equal(planCapabilityImprovement({ ...policy, max_trials: 0 }, {
    gap_ref: "example/gap", candidates: [trial],
  }).reason_code, "trial_budget_zero");
});

test("off preserves existing coordinator output byte for byte at every phase", () => {
  const orchestration = { mode: "multi_subagent", spawn_allowed: true, max_children: 2 };
  for (const phase of ["before_plan", "before_delegate", "after_delegate_result"]) {
    assert.deepEqual(evaluateGoalAgentContext({ phase, scope, orchestration }),
      evaluateSubagentContext({ phase, scope, orchestration }));
    assert.deepEqual(evaluateGoalAgentContext({ phase, scope, orchestration,
      capability_improvement: { ...policy, mode: "off" } }),
    evaluateSubagentContext({ phase, scope, orchestration }));
  }
  assert.equal(evaluateGoalAgentContext({ ...input, capability_improvement: null }), null);
});

test("shared before-plan projection discloses replan and ignores unrelated private content", () => {
  const packet = evaluateGoalAgentContext({ ...input, observations: { capability_improvement: {
    trigger: "replan", gap_ref: "example/gap", candidates: [trial], raw_result: "private result",
  } } })!;
  const contribution = (packet.contributions as any[])[0];
  assert.equal(contribution.facts.planning_trigger, "replan");
  assert.equal(contribution.facts.recommendation, "propose_reversible_trial");
  assert.equal(packet.authority, "guidance_only");
  assert.ok(!JSON.stringify(packet).includes("private result"));
  assert.deepEqual(packet.failures, []);
  assert.equal(evaluateGoalAgentContext({ ...input, phase: "before_delegate" }), null);
});

test("bad policy/candidate advice fails open without changing other capability context", () => {
  const orchestration = { mode: "multi_subagent", spawn_allowed: true, max_children: 2 };
  for (const patch of [{ capability_improvement: { ...policy, max_trials: 50 } },
    { observations: { capability_improvement: { gap_ref: "example/gap", candidates: Array(9).fill(trial) } } }]) {
    const packet = evaluateGoalAgentContext({ ...input, orchestration, ...patch })!;
    assert.equal((packet.contributions as any[]).length, 1);
    assert.equal((packet.contributions as any[])[0].capability_id, "multi_subagent");
    assert.equal((packet.failures as any[])[0].code, "context_provider_failed");
  }
});

test("ordinary improvement and multi-subagent hints fit the unchanged shared budget", () => {
  const packet = evaluateGoalAgentContext({ ...input, orchestration: {
    mode: "multi_subagent", spawn_allowed: true, max_children: 6,
  } })!;
  assert.equal((packet.contributions as any[]).length, 2);
  assert.deepEqual(packet.failures, []);
  assert.ok(Buffer.byteLength(JSON.stringify(packet)) <= 3072);
});
