import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolveQuotaRunDecision} from "../../loopx/control_plane/quota/run_decision.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/quota_run_decision.json", import.meta.url), "utf8"));
for (const scenario of fixture.cases) {
  test(scenario.name, () => {
    assert.deepEqual(resolveQuotaRunDecision({...fixture.base_facts, ...scenario.facts}),
      {...fixture.base_result, ...scenario.expected});
  });
}

test("malformed admission facts cannot manufacture a runnable decision", () => {
  for (const patch of [{normal_delivery_allowed: "true"}, {replan_applies: null},
    {workspace_guard: "not a guard"}, {quota_focus_wait: 1}, {reason: null},
    {task_orchestration_lane_count: -1}, {task_orchestration_lane_count: 1.5},
    {quota_allowed_slots: "-1"}, {quota_spent_slots: "1.5"}, {quota_spent_slots: 1}]) {
    assert.throws(() => resolveQuotaRunDecision({...fixture.base_facts, ...patch} as JsonObject));
  }
});

for (const state of ["throttled", "focus_wait", "waiting", "eligible"]) {
  test(`fresh replan cannot exceed its compute window under ${state}`, () => {
    const result = resolveQuotaRunDecision({...fixture.base_facts, state,
      replan_required: true, replan_applies: true,
      quota_allowed_slots: "1", quota_spent_slots: "1"});
    assert.equal(result.should_run, false);
    assert.equal(result.replan_decision_allowed, false);
    assert.equal(result.normal_delivery_allowed, false);
    assert.equal(result.effective_action, "throttled_skip");
  });
}

test("quota boundaries retain integer precision and admit only remaining allowance", () => {
  const facts = {...fixture.base_facts, replan_required: true, replan_applies: true,
    quota_allowed_slots: "9007199254740993", quota_spent_slots: "9007199254740992"};
  assert.equal(resolveQuotaRunDecision(facts).replan_decision_allowed, true);
  assert.equal(resolveQuotaRunDecision({...facts,
    quota_spent_slots: "9007199254740993"}).should_run, false);
});

for (const scenario of [
  {
    name: "adaptive counts child lanes",
    laneCount: 2,
    contract: {execution_state: "ready", mode: "adaptive"},
    expected: {omit_current_agent_next_action: true, kind: "task_orchestration",
      route_decision: "coordinate_task_bundle", reason: "task-scoped coordinator may spawn admitted child lanes",
      peer_lane_count: 2},
  },
  {
    name: "peer coordinator counts peer lanes",
    laneCount: 1,
    contract: {execution_state: "", mode: "task_scoped_peer"},
    expected: {omit_current_agent_next_action: true, kind: "task_orchestration",
      route_decision: "coordinate_task_bundle",
      reason: "task-scoped coordinator must activate/resume eligible peer lanes", peer_lane_count: 1},
  },
  {
    name: "waiting coordinator has no override",
    laneCount: 0,
    contract: {execution_state: "waiting", mode: "adaptive"},
    expected: null,
  },
]) {
  test(`task route hint: ${scenario.name}`, () => {
    const decision = resolveQuotaRunDecision({...fixture.base_facts,
      task_orchestration_lane_count: scenario.laneCount,
      task_orchestration_contract: scenario.contract});
    assert.deepEqual(decision.goal_route_hint_override, scenario.expected);
  });
}
