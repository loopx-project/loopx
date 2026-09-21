import assert from "node:assert/strict";
import test from "node:test";
import { projectReplanSemantics, requiredSemanticOutcomes } from "../../loopx/control_plane/work_items/replan_semantics.ts";
import { visionAuthoringContract } from "../../loopx/control_plane/goals/vision_checkpoint.ts";

const obligation = {triggers: [{kind: "required_agent_vision_missing"}]};
const vision = {vision_patch: {acceptance_summary: "Observed permission boundary"},
  path_delta: {outcome: "continue", evidence_refs: ["evidence-permission"]}};

test("obligation source governs both authoring projection and semantic discharge", () => {
  const projection = projectReplanSemantics({operation: "requirements", obligation});
  assert.match(String(projection.cli_semantic_args), /--agent-vision-json/);
  assert.deepEqual((projection.writeback_contract as Record<string, unknown>).vision_authoring, visionAuthoringContract());
  assert.equal(projectReplanSemantics({operation: "qualify", obligation,
    observation_delta: {delta_kinds: ["new_surface"]}}).accepted, false);
  const delta = projectReplanSemantics({operation: "qualify", obligation, agent_vision: vision});
  assert.equal(delta.accepted, true);
  assert.deepEqual(delta.required_any_of, projection.required_any_of);
  assert.deepEqual(delta.satisfying_outcomes, ["fresh_vision_path_outcome"]);
});

test("missing evidence or acceptance cannot manufacture a fresh path outcome", () => {
  for (const agentVision of [
    {...vision, vision_patch: {}}, {...vision, path_delta: {outcome: "continue"}},
    {...vision, path_delta: {outcome: "wait", evidence_refs: ["evidence-permission"]}},
  ]) {
    assert.equal(projectReplanSemantics({operation: "qualify", obligation, agent_vision: agentVision}).accepted, false);
  }
});

test("long-chain review accepts its evidence-linked vision route and retains progress exits", () => {
  const chain = {triggers: [{kind: "long_todo_chain"}]};
  const projection = projectReplanSemantics({operation: "requirements", obligation: chain});
  assert.match(String(projection.cli_semantic_args), /--agent-vision-json/);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: chain,
    agent_vision: vision}).accepted, true);
  for (const outcome of ["new_surface", "new_hypothesis", "new_probe_family", "new_runnable_successor"]) {
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: chain,
      observation_delta: {delta_kinds: [outcome]}}).accepted, true);
  }
  for (const incomplete of [{vision_patch: vision.vision_patch},
    {...vision, path_delta: {outcome: "replan", evidence_refs: []}},
    {...vision, vision_patch: {}}, {...vision, path_delta: {outcome: "wait", evidence_refs: ["evidence"]}}]) {
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: chain,
      agent_vision: incomplete}).accepted, false);
  }
  // A stricter source, mixed vision duty or unrelated trigger keeps its policy.
  for (const restricted of [
    {...chain, satisfying_semantic_outcomes: ["new_runnable_successor"]},
    {triggers: [{kind: "typed_progress_repeat", text: "long_todo_chain"}]},
  ]) {
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: restricted,
      agent_vision: vision}).accepted, false);
  }
  assert.equal(projectReplanSemantics({operation: "qualify",
    obligation: {triggers: [...chain.triggers, ...obligation.triggers]},
    observation_delta: {delta_kinds: ["new_surface"]}}).accepted, false);
});

test("explicit outcome restriction remains authoritative; trigger prose is not", () => {
  assert.deepEqual(requiredSemanticOutcomes({satisfying_semantic_outcomes: ["new_runnable_successor", "new_runnable_successor"]}), ["new_runnable_successor"]);
  assert.throws(() => requiredSemanticOutcomes({satisfying_semantic_outcomes: ["unrecognized"]}), /unknown typed outcome/);
  const prose = {triggers: [{kind: "typed_progress_repeat", text: "required_agent_vision_missing"}]};
  assert.equal(requiredSemanticOutcomes(prose).includes("new_surface"), true);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: prose,
    observation_delta: {delta_kinds: ["new_surface"]}}).accepted, true);
});

test("no-followup cannot hide an inconsistent vision behind another accepted outcome", () => {
  const request = {operation: "qualify", obligation,
    observation_delta: {delta_kinds: ["coverage_backed_no_followup", "new_concrete_blocker"]}};
  assert.equal(projectReplanSemantics({...request, agent_vision: vision}).reason_code, "no_followup_vision_path_inconsistent");
  const valid = projectReplanSemantics({...request, agent_vision: {state: "no_followup", path_delta: {outcome: "stop"}}});
  assert.equal(valid.accepted, true);
});
