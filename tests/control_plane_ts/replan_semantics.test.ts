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

test("stale Goal Acceptance cannot be discharged by unrelated progress or a vision patch", () => {
  const stale = {triggers: [{kind: "goal_acceptance_stale", vision_todo_ids: ["todo_stale"]}],
    satisfying_semantic_outcomes: ["new_runnable_successor", "new_concrete_blocker"]};
  assert.deepEqual(requiredSemanticOutcomes(stale), ["new_runnable_successor", "new_concrete_blocker"]);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: stale,
    observation_delta: {delta_kinds: ["new_surface"]}}).accepted, false);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: stale, agent_vision: vision}).accepted, false);
  for (const outcome of ["new_runnable_successor", "new_concrete_blocker"]) {
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: stale,
      observation_delta: {delta_kinds: [outcome]}}).accepted, true);
  }
});

test("no-followup cannot hide an inconsistent vision behind another accepted outcome", () => {
  const request = {operation: "qualify", obligation,
    observation_delta: {delta_kinds: ["coverage_backed_no_followup", "new_concrete_blocker"]}};
  assert.equal(projectReplanSemantics({...request, agent_vision: vision}).reason_code, "no_followup_vision_path_inconsistent");
  const valid = projectReplanSemantics({...request, agent_vision: {state: "no_followup", path_delta: {outcome: "stop"}}});
  assert.equal(valid.accepted, true);
});

test("external progress review: renamed identifiers discharge only behind new evidence; the plan may stand on evidence", () => {
  const review = {triggers: [{kind: "external_progress_review_drift", model_authority: "none"}]};
  const projection = projectReplanSemantics({operation: "requirements", obligation: review});
  const required = projection.required_any_of as string[];
  assert.equal(required.includes("fresh_vision_path_outcome"), true);
  for (const outcome of ["new_surface", "new_hypothesis", "new_probe_family", "new_concrete_blocker", "coverage_backed_no_followup"]) {
    assert.equal(required.includes(outcome), true, outcome);
  }
  assert.match(String(projection.cli_semantic_args), /--progress-hypothesis-id/);
  const contract = projection.writeback_contract as Record<string, unknown>;
  assert.equal(contract.identity_outcomes_require_new_evidence, true);
  assert.match(String(contract.alternative_cli_semantic_args), /--agent-vision-json/);
  assert.deepEqual(contract.vision_authoring, visionAuthoringContract());

  const qualify = (observation_delta?: Record<string, unknown>, agent_vision?: Record<string, unknown>) =>
    projectReplanSemantics({operation: "qualify", obligation: review, observation_delta, agent_vision});
  for (const identity of ["new_surface", "new_hypothesis", "new_probe_family"]) {
    const refused = qualify({delta_kinds: [identity], evidence_novel: false});
    assert.equal(refused.accepted, false, identity);
    assert.equal(refused.reason_code, "progress_identity_without_new_evidence");
    assert.deepEqual(refused.outcomes, []);
    assert.equal(qualify({delta_kinds: [identity]}).accepted, false, `${identity} without the novelty fact`);
    const accepted = qualify({delta_kinds: [identity], evidence_novel: true});
    assert.equal(accepted.accepted, true, identity);
    assert.deepEqual(accepted.satisfying_outcomes, [identity]);
  }
  // Substantive outcomes do not depend on the novelty fact, and a refused
  // rename beside an accepted blocker leaves no reason code behind.
  assert.equal(qualify({delta_kinds: ["new_concrete_blocker"], evidence_novel: false}).accepted, true);
  const mixed = qualify({delta_kinds: ["new_hypothesis", "new_concrete_blocker"], evidence_novel: false});
  assert.equal(mixed.accepted, true);
  assert.deepEqual(mixed.satisfying_outcomes, ["new_concrete_blocker"]);
  assert.equal("reason_code" in mixed, false);
  // A claim already made while the obligation formed cannot supply any
  // progress outcome, including blocker and coverage-backed terminal exits.
  for (const outcome of ["new_surface", "new_hypothesis", "new_probe_family", "new_runnable_successor",
    "new_concrete_blocker", "coverage_backed_exploration_exhausted", "coverage_backed_no_followup"]) {
    const replayed = qualify({delta_kinds: [outcome], evidence_novel: true, observation_repeated: true});
    assert.equal(replayed.accepted, false, outcome);
    assert.equal(replayed.reason_code, "progress_observation_replayed", outcome);
    assert.deepEqual(replayed.outcomes, [], outcome);
  }
  const replayedWithBlocker = qualify({delta_kinds: ["new_hypothesis", "new_concrete_blocker"], evidence_novel: true, observation_repeated: true});
  assert.equal(replayedWithBlocker.accepted, false);
  assert.deepEqual(replayedWithBlocker.satisfying_outcomes, []);
  assert.equal(replayedWithBlocker.reason_code, "progress_observation_replayed");
  assert.equal(qualify({delta_kinds: ["new_hypothesis"], evidence_novel: true, observation_repeated: false}).accepted, true);
  assert.equal(qualify({delta_kinds: ["new_concrete_blocker"], evidence_novel: false, observation_repeated: false}).accepted, true);
  // Keeping the plan on evidence is a legal exit for this source.
  const kept = qualify(undefined, vision);
  assert.equal(kept.accepted, true);
  assert.deepEqual(kept.satisfying_outcomes, ["fresh_vision_path_outcome"]);
  const replayedWithVision = qualify({delta_kinds: ["coverage_backed_no_followup"], observation_repeated: true}, vision);
  assert.equal(replayedWithVision.accepted, true);
  assert.deepEqual(replayedWithVision.satisfying_outcomes, ["fresh_vision_path_outcome"]);
  assert.equal(qualify(undefined, {...vision, path_delta: {outcome: "wait", evidence_refs: ["e"]}}).accepted, false);
  assert.equal(qualify({delta_kinds: ["new_hypothesis"], evidence_novel: false}, vision).accepted, true);
});

test("the identity rule is scoped to the external review source", () => {
  const fuse = {triggers: [{kind: "typed_progress_repeat"}]};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: fuse,
    observation_delta: {delta_kinds: ["new_hypothesis"], evidence_novel: false}}).accepted, true);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: fuse,
    observation_delta: {delta_kinds: ["new_hypothesis"], observation_repeated: true}}).accepted, true);
  assert.equal((projectReplanSemantics({operation: "requirements", obligation: fuse}).required_any_of as string[])
    .includes("fresh_vision_path_outcome"), false);
  // A vision duty on the same obligation keeps the stricter vision policy.
  const withVision = {triggers: [{kind: "external_progress_review_drift"}, {kind: "required_agent_vision_missing"}]};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: withVision,
    observation_delta: {delta_kinds: ["new_hypothesis"], evidence_novel: true}}).accepted, false);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: withVision, agent_vision: vision}).accepted, true);
  // An explicit outcome restriction stays authoritative over the source default.
  const restricted = {triggers: [{kind: "external_progress_review_drift"}], satisfying_semantic_outcomes: ["new_concrete_blocker"]};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: restricted,
    observation_delta: {delta_kinds: ["new_hypothesis"], evidence_novel: true}}).accepted, false);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: restricted, agent_vision: vision}).accepted, false);
  // Trigger prose cannot opt into the external review policy.
  const prose = {triggers: [{kind: "typed_progress_repeat", text: "external_progress_review_drift"}]};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: prose,
    observation_delta: {delta_kinds: ["new_hypothesis"], evidence_novel: false}}).accepted, true);
});


test("acceptance holds use one typed recovery policy even without a projected outcome list", () => {
  for (const kind of ["goal_acceptance_unbound", "goal_acceptance_stale"]) {
    const held = {triggers: [{kind}]};
    assert.deepEqual(requiredSemanticOutcomes(held), ["new_runnable_successor", "new_concrete_blocker"]);
    assert.throws(() => requiredSemanticOutcomes({...held, satisfying_semantic_outcomes: ["fresh_vision_path_outcome"]}), /cannot widen/);
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: held, agent_vision: vision}).accepted, false);
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: held,
      observation_delta: {delta_kinds: ["new_surface"]}}).accepted, false);
  }
});


test("planning advice cannot discharge a replan or widen source-specific exits", () => {
  for (const kind of ["typed_progress_repeat", "vision_acceptance_gap", "long_todo_chain",
    "external_progress_review_drift", "goal_acceptance_stale"]) {
    const source = {triggers: [{kind}]};
    const projection = projectReplanSemantics({operation: "requirements", obligation: source});
    assert.equal((projection.planning_guidance as string[]).length, 2);
    const refusal = projectReplanSemantics({operation: "qualify", obligation: source,
      planning_guidance: projection.planning_guidance});
    assert.equal(refusal.accepted, false);
    assert.deepEqual(refusal.required_any_of, requiredSemanticOutcomes(source));
    assert.equal(refusal.planning_guidance, undefined);
  }
});
