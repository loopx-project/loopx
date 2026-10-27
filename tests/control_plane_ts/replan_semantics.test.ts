import assert from "node:assert/strict";
import test from "node:test";
import { projectReplanSemantics, requiredSemanticOutcomes } from "../../loopx/control_plane/work_items/replan_semantics.ts";

test("capability evidence is not a default exit or a generic progress claim", () => {
  for (const outcome of ["capability_evidence_observed", "capability_duty_retired"]) {
  assert.equal(requiredSemanticOutcomes({triggers: [{kind: "no_progress_streak"}]}).includes("capability_evidence_observed"), false);
  assert.throws(() => requiredSemanticOutcomes({satisfying_semantic_outcomes: ["capability_evidence_observed"]}), /bound owning capability/);
  const obligation = {satisfying_semantic_outcomes: [outcome],
    capability_guard: {schema_version: "semantic_replan_capability_guard_v0", capability_id: "explore"}};
  assert.deepEqual(requiredSemanticOutcomes(obligation), [outcome]);
  assert.throws(() => projectReplanSemantics({operation: "qualify", obligation,
    observation_delta: {delta_kinds: [outcome]}}), /owning capability, not generic progress/);
  }
});
import { visionAuthoringContract } from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const obligation = {triggers: [{kind: "required_agent_vision_missing"}]};
const vision = {vision_patch: {acceptance_summary: "Observed permission boundary"},
  path_delta: {outcome: "continue", evidence_refs: ["evidence-permission"]}};

test("open-work review guidance does not require configured checks or outcome declarations", () => {
  for (const kind of ["typed_progress_repeat", "periodic_review_due", "goal_outcome_uncovered"]) {
    for (const stride_strategy of ["off", "evidence_conditioned_v1"]) {
      const source = {obligation_id: "replan-1111111111111111", triggers: [{kind}],
        replan_context: {stride_strategy}};
      const project = (current: boolean | null) => projectReplanSemantics({operation: "requirements", obligation: {
        ...source, replan_context: {...source.replan_context, core_goal: {acceptance_contract: current === null ? null : {
          outcome_requirements: {current_lifetime: current, outcomes: [], acceptance_gaps: []}}}}}});
      const ordinary = project(null), retired = project(false), current = project(true);
      assert.deepEqual(retired, ordinary);
      assert.deepEqual(current, ordinary, "default review and its authority do not depend on an outcome declaration");
      const guidance = (ordinary.planning_guidance as string[]).join(" ");
      for (const clause of ["even with open Todos", "revisable planning choices", "never forces a pivot"]) {
        assert.ok(guidance.includes(clause));
      }
    }
  }
});

test("a revalidated successor receipt settles only its exact original Turn guard", () => {
  const id = "replan-1111111111111111";
  const delta = {schema_version: "replan_semantic_delta_v0", accepted: true,
    obligation_id: id, successor_todo_id: "todo_independent_successor",
    outcomes: ["new_runnable_successor"], satisfying_outcomes: ["new_runnable_successor"]};
  const ack = {schema_version: "autonomous_replan_ack_v0", recorded: true,
    source: "todo_replan_successor_transition", semantic_delta: delta};
  const source = {obligation_id: id};
  const request = {operation: "turn_transition", guard_scoped: true,
    selected_obligation_id: id, transition_candidates: [{obligation: source, ack: null},
      {obligation: source, ack}]};
  assert.deepEqual(projectReplanSemantics(request).semantic_delta, delta);
  for (const guard of [{guard_scoped: false}, {selected_obligation_id: null},
    {selected_obligation_id: "replan-2222222222222222"}]) {
    assert.equal(projectReplanSemantics({...request, ...guard}).semantic_delta, null);
  }
  for (const invalid of [{recorded: false}, {source: "prose_claim"},
    {schema_version: "unknown"}, {semantic_delta: {...delta, accepted: false}},
    {semantic_delta: {...delta, satisfying_outcomes: []}},
    {semantic_delta: {...delta, successor_todo_id: null}}]) {
    assert.equal(projectReplanSemantics({...request, transition_candidates: [
      {obligation: source, ack: {...ack, ...invalid}}]}).semantic_delta, null);
  }
  const next = {...ack, semantic_delta: {...delta, obligation_id: "replan-2222222222222222"}};
  assert.deepEqual(projectReplanSemantics({...request, transition_candidates: [
    {obligation: source, ack: next}, {obligation: source, ack}]}).semantic_delta, delta);
  assert.throws(() => projectReplanSemantics({...request, transition_candidates: undefined}), /source-bound/);
  assert.throws(() => projectReplanSemantics({...request, selected_obligation_id: "malformed"}), /malformed/);
});

test("receipt-bound reentry keeps original canonical proof separate from the next frontier", () => {
  const id = "replan-1111111111111111";
  const original = {required: true, obligation_id: id, triggers: [{kind: "periodic_review_due"}]};
  const current = {...original, obligation_id: "replan-2222222222222222", triggers: [{kind: "vision_acceptance_gap"}]};
  const delta = {schema_version: "replan_semantic_delta_v0", accepted: true,
    obligation_id: id, successor_todo_id: "todo_independent_successor",
    outcomes: ["new_runnable_successor"], satisfying_outcomes: ["new_runnable_successor"]};
  const ack = {schema_version: "autonomous_replan_ack_v0", recorded: true,
    source: "todo_replan_successor_transition", semantic_delta: delta};
  const request = {operation: "receipt_bound_obligation", guard_scoped: true,
    replay_phase: "open", selected_obligation_id: id, current_obligation: current,
    transition_candidates: [{obligation: original, ack}]};
  const projected = projectReplanSemantics(request).obligation as JsonObject;
  assert.equal(projected.obligation_id, id);
  assert.deepEqual(projected.triggers, original.triggers);
  assert.equal(projected.selection_binding, "heartbeat_receipt");
  assert.equal(projected.resolution_mode, "receipt_bound_replan_settlement");
  assert.deepEqual(projected.todo_actions, []);
  const packet = projected.settlement_action_packet as JsonObject;
  assert.equal(packet.settlement_only, true);
  assert.equal(packet.successor_todo_id, delta.successor_todo_id);
  assert.equal((packet.writeback_contract as JsonObject).preferred_input, "canonical_successor_transition");
  assert.equal(current.obligation_id, "replan-2222222222222222");
  for (const invalid of [{guard_scoped: false}, {transition_candidates: []},
    {selected_obligation_id: "replan-3333333333333333"},
    {transition_candidates: [{obligation: current, ack}]},
    {transition_candidates: [{obligation: original, ack: {...ack, recorded: false}}]},
    {transition_candidates: [{obligation: {...original, satisfying_semantic_outcomes: ["fresh_vision_path_outcome"]}, ack}]}]) {
    assert.throws(() => projectReplanSemantics({...request, ...invalid}), /identity conflicts/);
  }
  const unchanged = projectReplanSemantics({...request, current_obligation: original,
    transition_candidates: []}).obligation as JsonObject;
  assert.equal(unchanged.selection_binding, "heartbeat_receipt");
  assert.equal(unchanged.resolution_mode, undefined);
  // A receipt admitted without replan work cannot adopt a later frontier.
  assert.equal(projectReplanSemantics({...request, selected_obligation_id: null}).obligation, null);
  // A fresh, unguarded read still exposes that current frontier for admission.
  assert.equal(projectReplanSemantics({...request, guard_scoped: false,
    selected_obligation_id: null}).obligation, current);
});

test("verified original writeback projects only its outstanding debit even after frontier drift", () => {
  const result = projectReplanSemantics({operation: "receipt_bound_obligation", guard_scoped: true,
    replay_phase: "settlement_pending", selected_obligation_id: "replan-1111111111111111",
    current_obligation: null, transition_candidates: []}).obligation as JsonObject;
  assert.equal(result.obligation_id, "replan-1111111111111111");
  const packet = result.settlement_action_packet as JsonObject;
  assert.equal(packet.decision, "settlement_pending");
  assert.equal(packet.semantic_delta, null);
  assert.equal((packet.writeback_contract as JsonObject).preferred_input, "verified_replan_writeback");
  assert.throws(() => projectReplanSemantics({operation: "receipt_bound_obligation", guard_scoped: true,
    replay_phase: "settled", selected_obligation_id: "replan-1111111111111111",
    current_obligation: null, transition_candidates: []}), /malformed/);
});

test("only a plain outcome review has a scalar receipt source", () => {
  const source = {schema_version: "autonomous_replan_obligation_v0", required: true,
    obligation_id: "replan-1111111111111111", agent_id: "reviewer",
    stop_condition: "Stop at owner-only decisions.",
    triggers: [{kind: "goal_outcome_uncovered", text: "Private Goal prose stays outside telemetry"}],
    replan_context: {context_id: "old-acceptance-basis"}};
  const request = {operation: "receipt_review_source", agent_id: "reviewer", obligation: source};
  assert.deepEqual(projectReplanSemantics(request), {review_source: {
    kind: "goal_outcome_uncovered", stop_condition: source.stop_condition}});
  for (const patch of [{required: false}, {schema_version: "unknown"}, {agent_id: "another-agent"},
    {triggers: []}, {triggers: [{kind: "periodic_review_due"}]},
    {triggers: [{kind: "goal_outcome_uncovered"}, {kind: "external_progress_review_drift"}]},
    {satisfying_semantic_outcomes: ["new_runnable_successor"]},
    {progress_baseline: {}}, {progress_window: []},
    {triggers: [{kind: "goal_outcome_uncovered", progress_baseline: {}}]},
    {triggers: [{kind: "goal_outcome_uncovered", progress_window: []}]},
    {stop_condition: null}, {stop_condition: "s".repeat(241)},
    {resolution_mode: "todo_lifecycle_settlement"},
    {capability_guard: {schema_version: "semantic_replan_capability_guard_v0"}}]) {
    assert.deepEqual(projectReplanSemantics({...request, obligation: {...source, ...patch}}), {review_source: null});
  }
});

test("a recorded outcome review survives work-link refinement without cached context or discharge", () => {
  const id = "replan-1111111111111111";
  const request = {operation: "receipt_bound_obligation", guard_scoped: true,
    replay_phase: "open", selected_obligation_id: id, current_obligation: null,
    transition_candidates: [], agent_id: "reviewer", receipt_review: {
      kind: "goal_outcome_uncovered", stop_condition: "Stop at owner-only decisions."}};
  const recovered = projectReplanSemantics(request).obligation as JsonObject;
  assert.equal(recovered.obligation_id, id);
  assert.equal(recovered.agent_id, "reviewer");
  assert.equal(recovered.stop_condition, request.receipt_review.stop_condition);
  assert.deepEqual(recovered.triggers, [{kind: "goal_outcome_uncovered"}]);
  assert.equal(recovered.selection_binding, "heartbeat_receipt");
  assert.equal(recovered.replan_context, undefined);
  assert.equal(recovered.settlement_action_packet, undefined);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: recovered}).accepted, false);
  for (const invalid of [{guard_scoped: false}, {agent_id: null},
    {receipt_review: null}, {receipt_review: {kind: "periodic_review_due"}},
    {receipt_review: {kind: "goal_outcome_uncovered"}}]) {
    assert.throws(() => projectReplanSemantics({...request, ...invalid}), /identity conflicts/);
  }
  assert.equal(projectReplanSemantics({...request, selected_obligation_id: null}).obligation, null);
});

test("obligation source governs both authoring projection and semantic discharge", () => {
  const projection = projectReplanSemantics({operation: "requirements", obligation});
  assert.match(String(projection.cli_semantic_args), /--agent-vision-json/);
  assert.deepEqual((projection.writeback_contract as Record<string, unknown>).vision_authoring, visionAuthoringContract(true));
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

for (const reviewKind of ["long_todo_chain", "periodic_review_due"]) {
  test(`${reviewKind} accepts its evidence-linked vision route and retains progress exits`, () => {
    const chain = {triggers: [{kind: reviewKind}]};
    const projection = projectReplanSemantics({operation: "requirements", obligation: chain});
    assert.match(String(projection.cli_semantic_args), /--agent-vision-json/);
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: chain,
      agent_vision: vision}).accepted, true);
    // Reusing applicable work evidence can keep the route without inventing
    // another probe or Todo; a current evidence-linked decision is still needed.
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: chain,
      agent_vision: {...vision, path_delta: {...vision.path_delta, outcome: "no_change"}},
      observation_delta: {delta_kinds: [], evidence_novel: false}}).accepted, true);
    assert.match(String((projection.writeback_contract as JsonObject).rule), /First reuse observed evidence valid for current source\/acceptance/);
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
      {triggers: [...chain.triggers, {kind: "goal_acceptance_unbound"}]},
      {triggers: [{kind: "typed_progress_repeat", text: reviewKind}]},
    ]) {
      assert.equal(projectReplanSemantics({operation: "qualify", obligation: restricted,
        agent_vision: vision}).accepted, false);
    }
    assert.equal(projectReplanSemantics({operation: "qualify",
      obligation: {triggers: [...chain.triggers, ...obligation.triggers]},
      observation_delta: {delta_kinds: ["new_surface"]}}).accepted, false);
  });

}

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
  assert.deepEqual(contract.vision_authoring, visionAuthoringContract(true));

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

test("known blockers cannot discharge any replan source; independent vision evidence can", () => {
  for (const kind of ["vision_acceptance_gap", "typed_progress_repeat", "external_progress_review_drift"]) {
    const request = {operation: "qualify", obligation: {triggers: [{kind}]},
      observation_delta: {delta_kinds: ["new_concrete_blocker"], blocker_repeated: true,
        evidence_novel: true, observation_repeated: false}};
    const rejected = projectReplanSemantics(request);
    assert.equal(rejected.accepted, false, kind);
    assert.equal(rejected.reason_code, "progress_observation_replayed", kind);
    assert.deepEqual(rejected.outcomes, []);
    assert.equal(projectReplanSemantics({...request,
      observation_delta: {...request.observation_delta, blocker_repeated: false}}).accepted, true);
    if (kind !== "typed_progress_repeat") {
      const kept = projectReplanSemantics({...request, agent_vision: vision});
      assert.equal(kept.accepted, true);
      assert.deepEqual(kept.satisfying_outcomes, ["fresh_vision_path_outcome"]);
    }
  }
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
    "external_progress_review_drift", "goal_acceptance_stale", "vision_successor_required"]) {
    const source = {triggers: [{kind}]};
    const projection = projectReplanSemantics({operation: "requirements", obligation: source});
    const guidance = projection.planning_guidance as string[];
    assert.ok(guidance.length > 0 && guidance.length <= 3);
    assert.ok(guidance.join(" ").length < 3000, "boundaries replace the long method checklist");
    const refusal = projectReplanSemantics({operation: "qualify", obligation: source,
      planning_guidance: projection.planning_guidance});
    assert.equal(refusal.accepted, false);
    assert.deepEqual(refusal.required_any_of, requiredSemanticOutcomes(source));
    assert.equal(refusal.planning_guidance, undefined);
  }
});


test("stride format replaces duplicate strategy advice and never discharges a semantic review", () => {
  const plain = {triggers: [{kind: "typed_progress_repeat"}]};
  const base = projectReplanSemantics({operation: "requirements", obligation: plain});
  const off = projectReplanSemantics({operation: "requirements", obligation: {
    ...plain, replan_context: {stride_strategy: "off"}}});
  assert.deepEqual(off, base);
  const enabled = {...plain, replan_context: {stride_strategy: "evidence_conditioned_v1"}};
  const withStrategy = projectReplanSemantics({operation: "requirements", obligation: enabled});
  assert.deepEqual(withStrategy.planning_guidance, base.planning_guidance);
  assert.ok(withStrategy.stride_control);
  assert.equal(base.stride_control, undefined);
  assert.deepEqual(withStrategy.required_any_of,
    ["fresh_vision_path_outcome", ...(base.required_any_of as string[])]);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: enabled,
    planning_guidance: withStrategy.planning_guidance}).accepted, false);
  for (const version of ["future_v2", null, 1]) {
    assert.throws(() => projectReplanSemantics({operation: "requirements", obligation: {
      ...plain, replan_context: {stride_strategy: version}}}), /stride_strategy/);
  }
});

test("vision ACK freshness covers its durable run, never later gaps or unknown timestamps", () => {
  const at = "2026-08-13T09:00:00+08:00";
  const ack = {recorded: true, generated_at: at,
    semantic_delta: {accepted: true, outcomes: ["fresh_vision_path_outcome"]},
    delta_contract: {delta_kinds: ["goal_vision_patch"]}};
  const request = {operation: "vision_ack", ack, acceptance_gaps: [{generated_at: at}]};
  assert.equal(projectReplanSemantics(request).acknowledged, true);
  assert.equal(projectReplanSemantics({...request, acceptance_gaps: [{generated_at: "2026-08-13T01:00:00Z"}]}).acknowledged, true);
  for (const generated_at of ["2026-08-13T09:01:00+08:00", null, "invalid", "2026-08-13T09:00:00"]) {
    assert.equal(projectReplanSemantics({...request, acceptance_gaps: [{generated_at: at}, {generated_at}]}).acknowledged, false);
  }
  for (const invalid of [{recorded: false}, {generated_at: null},
    {semantic_delta: {accepted: false, outcomes: ["fresh_vision_path_outcome"]}},
    {semantic_delta: {accepted: true, outcomes: ["new_surface"]}},
    {semantic_delta: {accepted: true, outcomes: []}}]) {
    assert.equal(projectReplanSemantics({...request, ack: {...ack, ...invalid}}).acknowledged, false);
  }
  assert.equal(projectReplanSemantics({...request, acceptance_gaps: []}).acknowledged, false);
  const completion = {kind: "vision_outcome_checkpoint_required",
    source: "recent_completed_advancement_todo", completed_at: at};
  assert.equal(projectReplanSemantics({...request, acceptance_gaps: [completion]}).acknowledged, true);
  assert.equal(projectReplanSemantics({...request, acceptance_gaps: [{...completion,
    completed_at: "2026-08-13T09:01:00+08:00"}]}).acknowledged, false);
  assert.equal(projectReplanSemantics({...request, acceptance_gaps: [{completed_at: at}]}).acknowledged, false);
});

test("vision ACK compares strict calendar timestamps at microsecond precision", () => {
  const at = "2026-08-01T00:30:00.000100Z";
  const ack = {recorded: true, generated_at: at,
    semantic_delta: {accepted: true, outcomes: ["fresh_vision_path_outcome"]}};
  for (const completed of [false, true]) {
    const gap = (timestamp: string) => completed
      ? {kind: "vision_outcome_checkpoint_required", source: "recent_completed_advancement_todo",
        completed_at: timestamp}
      : {generated_at: timestamp};
    for (const [timestamp, covered] of [
      [at, true], ["2026-08-01T08:30:00.000100+08:00", true],
      ["2026-08-01T00:30:00.000900Z", false], ["2026-02-30T00:00:00Z", false],
    ] as const) {
      assert.equal(projectReplanSemantics({operation: "vision_ack", ack,
        acceptance_gaps: [gap(timestamp)]}).acknowledged, covered);
    }
    assert.equal(projectReplanSemantics({operation: "vision_ack",
      ack: {...ack, generated_at: "2026-02-30T00:00:00Z"},
      acceptance_gaps: [gap("2026-02-28T00:00:00Z")]}).acknowledged, false);
  }
});

test("outcome blocker ACK covers only its exact current agent checkpoint", () => {
  const gap = {kind: "goal_outcome_uncovered", source: "goal_acceptance_contract",
    agent_id: "author", frontier_revision: "a".repeat(64)};
  const delta = {accepted: true, obligation_id: "replan-1111111111111111",
    trigger_kinds: [gap.kind], trigger_checkpoints: [{kind: gap.kind, frontier_revision: gap.frontier_revision}],
    outcomes: ["new_concrete_blocker"], satisfying_outcomes: ["new_concrete_blocker"]};
  const ack = {recorded: true, agent_id: "author", generated_at: "2026-08-01T00:00:00Z", semantic_delta: delta};
  const accepts = (changes: JsonObject = {}, gaps: JsonObject[] = [gap]) => projectReplanSemantics({
    operation: "vision_ack", ack: {...ack, ...changes}, acceptance_gaps: gaps}).acknowledged;
  assert.equal(accepts(), true, "an accepted blocker needs no read-time timestamp to settle the same source");
  for (const changes of [{agent_id: "other"}, {agent_id: null}, {recorded: false}, {generated_at: null},
    {semantic_delta: {...delta, accepted: false}}, {semantic_delta: {...delta, obligation_id: "unknown"}},
    {semantic_delta: {...delta, trigger_kinds: []}}, {semantic_delta: {...delta, trigger_checkpoints: []}},
    {semantic_delta: {...delta, satisfying_outcomes: []}}, {semantic_delta: {...delta, outcomes: ["new_surface"]}}]) {
    assert.equal(accepts(changes), false);
  }
  for (const change of [{source: "agent_claim"}, {agent_id: "other"}, {frontier_revision: "b".repeat(64)},
    {frontier_revision: "unknown"}, {kind: "goal_acceptance_unbound"}]) {
    assert.equal(accepts({}, [{...gap, ...change}]), false);
  }
  assert.equal(accepts({}, [gap, {generated_at: "2026-08-01T00:00:01Z"}]), false,
    "an outcome checkpoint cannot acknowledge later ordinary Vision evidence");
  assert.equal(accepts({}, [gap, {generated_at: ack.generated_at}]), true);
});

test("a formal acceptance-hold ACK covers its exact owner basis after display updates", () => {
  for (const kind of ["goal_acceptance_stale", "goal_acceptance_unbound"]) {
    const revision = "a".repeat(64);
    const gap = {kind, source: "goal_acceptance_contract", frontier_revision: revision,
      generated_at: "2026-09-23T08:04:00Z"};
    const delta = {accepted: true, obligation_id: "replan-1111111111111111",
      outcomes: ["new_concrete_blocker"], satisfying_outcomes: ["new_concrete_blocker"],
      trigger_kinds: [kind], trigger_checkpoints: [{kind, frontier_revision: revision}]};
    const ack = {recorded: true, generated_at: "2026-09-23T08:03:00Z", semantic_delta: delta};
    const request = {operation: "vision_ack", acceptance_gaps: [gap], ack};
    assert.equal(projectReplanSemantics(request).acknowledged, true);
    for (const changed of [
      {...gap, frontier_revision: "b".repeat(64)},
      {...gap, kind: "vision_acceptance_gap"},
      {...gap, source: "unconfirmed_display"},
    ]) assert.equal(projectReplanSemantics({...request, acceptance_gaps: [changed]}).acknowledged, false);
    for (const changed of [
      {recorded: false}, {generated_at: "2026-02-30T08:03:00Z"}, {generated_at: "2026-09-23T08:03:00"},
      {semantic_delta: {...delta, accepted: false}}, {semantic_delta: {...delta, outcomes: []}},
      {semantic_delta: {...delta, satisfying_outcomes: []}},
      {semantic_delta: {...delta, trigger_kinds: ["vision_acceptance_gap"]}},
      {semantic_delta: {...delta, trigger_checkpoints: [{kind, frontier_revision: "other"}]}},
      {semantic_delta: {...delta, obligation_id: null}},
    ]) assert.equal(projectReplanSemantics({...request, ack: {...ack, ...changed}}).acknowledged, false);
    // A newly observed independent gap still requires its own current evidence.
    assert.equal(projectReplanSemantics({...request,
      acceptance_gaps: [gap, {kind: "vision_acceptance_gap", generated_at: gap.generated_at}]}).acknowledged, false);
    assert.equal(projectReplanSemantics({...request,
      acceptance_gaps: [gap, {kind: "vision_acceptance_gap", generated_at: ack.generated_at}]}).acknowledged, true);
    // Historical ACKs without an obligation identity retain their old freshness rule.
    assert.equal(projectReplanSemantics({...request, acceptance_gaps: [{...gap, generated_at: ack.generated_at}],
      ack: {...ack, semantic_delta: {...delta, obligation_id: null}}}).acknowledged, true);
  }
});

test("old hold checkpoints require exact source digest and original timestamp coverage", () => {
  const kind = "goal_acceptance_unbound", old = "b".repeat(64);
  const gap = {kind, source: "goal_acceptance_contract", frontier_revision: "a".repeat(64),
    legacy_frontier_revision: old, generated_at: "2026-09-23T08:02:00Z"};
  const delta = {accepted: true, obligation_id: "replan-1111111111111111",
    outcomes: ["new_concrete_blocker"], satisfying_outcomes: ["new_concrete_blocker"],
    trigger_kinds: [kind], trigger_checkpoints: [{kind, frontier_revision: old}]};
  const ack = {recorded: true, generated_at: "2026-09-23T08:03:00Z", semantic_delta: delta};
  const request = {operation: "vision_ack", acceptance_gaps: [gap], ack};
  assert.equal(projectReplanSemantics(request).acknowledged, true);
  for (const change of [{legacy_frontier_revision: "c".repeat(64)}, {source: "other"},
    {generated_at: "2026-09-23T08:04:00Z"}, {generated_at: null}]) {
    assert.equal(projectReplanSemantics({...request, acceptance_gaps: [{...gap, ...change}]}).acknowledged, false);
  }
});

test("only a bound original receipt can select the source-derived legacy obligation", () => {
  const original = "replan-1111111111111111";
  const current = {obligation_id: "replan-2222222222222222", legacy_obligation_id: original,
    triggers: [{kind: "goal_acceptance_unbound", frontier_revision: "a".repeat(64)}]};
  const request = {operation: "receipt_bound_obligation", guard_scoped: true,
    selected_obligation_id: original, current_obligation: current, replay_phase: "open", transition_candidates: []};
  const bound = projectReplanSemantics(request).obligation as Record<string, unknown>;
  assert.equal(bound.obligation_id, original);
  assert.equal(bound.selection_binding, "heartbeat_receipt");
  assert.equal(Object.hasOwn(bound, "settlement_action_packet"), false);
  for (const changed of [{...request, guard_scoped: false},
    {...request, current_obligation: {...current, legacy_obligation_id: "replan-3333333333333333"}},
    {...request, current_obligation: {...current, triggers: [{kind: "periodic_review"}]}}]) {
    assert.throws(() => projectReplanSemantics(changed), /identity conflicts/);
  }
  assert.deepEqual(projectReplanSemantics({...request, selected_obligation_id: null}).obligation, null);
});


test("canonical no-follow-up restores only its exact admitted review and still owes Vision", () => {
  const id = "replan-1111111111111111", agent = "synthetic-agent";
  const row = {todo_id: "todo_original", text: "Verify independent source", status: "done", done: true,
    task_class: "advancement_task", claimed_by: agent, last_actor_agent_id: agent,
    completed_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:01Z",
    completion_turn_key: "original-completion", no_followup: true, completion_continuation: "no_followup",
    completion_recovery: "lifecycle_reentry_terminal_closeout"};
  const original = {required: true, obligation_id: id, resolution_mode: "todo_lifecycle_settlement",
    triggers: [{kind: "completed_advancement_without_successor", todo_id: row.todo_id,
      completion_turn_key: row.completion_turn_key, agent_id: agent}]};
  const candidate = {obligation: original, source_obligation: original, lifecycle_settled: true,
    guard_recorded_at: "2026-08-01T00:00:01.123456Z", items: [row]};
  const request = {operation: "receipt_bound_obligation", guard_scoped: true, replay_phase: "open",
    selected_obligation_id: id, agent_id: agent, current_obligation: null, transition_candidates: [candidate]};
  const result = projectReplanSemantics(request).obligation as JsonObject;
  assert.equal(result.obligation_id, id);
  assert.equal(result.resolution_mode, "receipt_bound_replan_settlement");
  assert.deepEqual(result.todo_actions, []);
  const packet = result.settlement_action_packet as JsonObject;
  assert.equal(packet.settlement_only, true);
  assert.equal(packet.semantic_delta, null);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: result}).accepted, false);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: result, agent_vision: vision}).accepted, true);
  const historical = {...row, todo_id: "todo_historical", completion_turn_key: "historical-completion",
    completed_at: "2026-07-31T23:59:00Z", updated_at: "2026-07-31T23:59:01Z"};
  for (const items of [[historical, row], [row, historical]]) {
    assert.equal((projectReplanSemantics({...request,
      transition_candidates: [{...candidate, items}]}).obligation as JsonObject).obligation_id, id);
  }
  // A whole-second timestamp cannot prove which side of admission this row
  // closed on. The exact trigger/digest check still rejects the ambiguous source.
  assert.throws(() => projectReplanSemantics({...request, transition_candidates: [{...candidate,
    items: [row, {...historical, updated_at: row.updated_at}]}]}), /identity conflicts/);
  for (const change of [{status: "open"}, {done: false}, {claimed_by: "peer"}, {last_actor_agent_id: "peer"},
    {no_followup: false}, {completion_continuation: "active_goal"}, {completion_recovery: null},
    {completion_turn_key: "changed"}, {completed_at: "2026-08-01T00:00:02Z"},
    {successor_todo_ids: ["todo_new"]}, {archive_state: "archived"},
    {updated_at: "2026-08-01T00:00:00Z"}, {updated_at: "2026-02-30T00:00:02Z"}, {updated_at: null}]) {
    assert.throws(() => projectReplanSemantics({...request,
      transition_candidates: [{...candidate, items: [{...row, ...change}]}]}), /identity conflicts/);
  }
  assert.throws(() => projectReplanSemantics({...request, guard_scoped: false}), /identity conflicts/);
  assert.throws(() => projectReplanSemantics({...request,
    selected_obligation_id: "replan-2222222222222222"}), /identity conflicts/);
});

test("lifecycle reconstruction restores completion order before its trigger bound", () => {
  const agent = "synthetic-agent";
  const rows = [1, 2, 3, 4].map(index => ({todo_id: `todo_${index}`, index,
    status: "done", done: true, task_class: "advancement_task", claimed_by: agent,
    last_actor_agent_id: agent, completed_at: `2026-08-01T00:00:0${index}Z`,
    updated_at: "2026-08-01T00:00:10Z", completion_turn_key: `completion-${index}`,
    no_followup: true, completion_continuation: "no_followup",
    completion_recovery: "lifecycle_reentry_terminal_closeout"}));
  const facts = projectReplanSemantics({operation: "lifecycle_source", agent_id: agent,
    guard_recorded_at: "2026-08-01T00:00:05Z", items: rows});
  assert.deepEqual((facts.items as JsonObject[]).map(item => item.todo_id),
    ["todo_4", "todo_3", "todo_2", "todo_1"]);
  assert.equal(facts.settled, true);
  const ties = rows.map(item => ({...item, completed_at: "2026-08-01T00:00:01Z"}));
  assert.deepEqual((projectReplanSemantics({operation: "lifecycle_source", agent_id: agent,
    guard_recorded_at: "2026-08-01T00:00:05Z", items: ties}).items as JsonObject[])
    .map(item => item.todo_id), ["todo_4", "todo_3", "todo_2", "todo_1"]);
});


test("pending lifecycle survives a new frontier without granting settlement or successor work", () => {
  const agent = "synthetic-agent", id = "replan-1111111111111111";
  const row = {todo_id: "todo_pending", status: "done", done: true,
    task_class: "advancement_task", claimed_by: agent, last_actor_agent_id: agent,
    completed_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z",
    completion_turn_key: "original-completion", completion_continuation: "active_goal"};
  const original = {required: true, obligation_id: id, resolution_mode: "todo_lifecycle_settlement",
    todo_actions: ["Settle the original completed Todo"],
    triggers: [{kind: "completed_advancement_without_successor", todo_id: row.todo_id,
      completion_turn_key: row.completion_turn_key, agent_id: agent}]};
  const candidate = {obligation: original, source_obligation: original, lifecycle_settled: false,
    guard_recorded_at: "2026-08-01T00:00:01Z", items: [row]};
  const request = {operation: "receipt_bound_obligation", guard_scoped: true, replay_phase: "open",
    selected_obligation_id: id, agent_id: agent,
    current_obligation: {obligation_id: "replan-2222222222222222"}, transition_candidates: [candidate]};
  const recovered = projectReplanSemantics(request).obligation as JsonObject;
  assert.deepEqual(recovered, {...original, selection_binding: "heartbeat_receipt"});
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: recovered,
    agent_vision: vision}).accepted, false);
  for (const change of [{status: "open"}, {done: false}, {claimed_by: "peer"},
    {last_actor_agent_id: "peer"}, {completion_turn_key: "changed"},
    {completed_at: "2026-08-01T00:00:02Z"}, {successor_todo_ids: ["todo_new"]},
    {superseded_by: "todo_new"}, {archive_state: "archived"}]) {
    assert.throws(() => projectReplanSemantics({...request,
      transition_candidates: [{...candidate, items: [{...row, ...change}]}]}), /identity conflicts/);
  }
  assert.throws(() => projectReplanSemantics({...request, guard_scoped: false}), /identity conflicts/);
  assert.throws(() => projectReplanSemantics({...request,
    transition_candidates: [{...candidate, lifecycle_settled: true}]}), /identity conflicts/);
});
